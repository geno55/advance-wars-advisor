"""Replay every recorded measurement against the engine.

The project had 75 emulator observations and not one test that read them, so a
regression in the damage model was silent: `tests/calibrate.py` is a __main__
script and pytest never collected it. This is that missing test.

Two sources, and they go through different formulas on purpose:

  * `harness/observations.csv` -- first strikes through `resolve()`, and the
    four counterattack rows through `counter_damage()`. Routing the counters
    through the strike formula is exactly the mistake that produced a refuted
    display rule and kept it for months, so this file keeps them apart and says
    why.

  * `tests/fixtures/*.json` -- seeded sweeps, 64 luck seeds each. These are
    stronger than the CSV rows: because the seed is written rather than
    sampled, a sweep pins the whole SET of damages a board can produce, and
    therefore its multiplicities, not just a value that was seen once.
"""
import csv
import json
import pathlib
import sys
import unittest
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.damage import (Attack, counter_damage, damage_for_luck,  # noqa: E402
                           LUCK_MAX, LUCK_MIN, resolve, select_weapon)

# Sweeps written before the harness recorded the tile the attacker FOUGHT from.
# A fixture sits at target-select and the unit record still holds the PRE-move
# tile -- settled, see A10 and test_the_fixture_tile_is_the_PRE_MOVE_tile below.
# These two record the Tank on Road while it fired from Plains, so their
# counters cannot be scored without assuming the answer. Their openings are
# unaffected and are still tested. city81b.json is the same board re-swept with
# both tiles recorded.
UNRESOLVED_ATTACKER_TERRAIN = ("city100.json", "city81.json")

WIDE_LUCK = ("nell_wood_luck.json", "sonja_wood_luck.json", "kanbei_att_wood.json",
          "kanbei_def_wood.json")

SWEEPS = ("counter.json", "att57.json", "def81.json", "def85.json",
          "def65.json", "wood100.json", "wood81.json", "city100.json",
          "city81.json", "max_wood_co.json", "nell_wood_luck.json",
          "sonja_wood_luck.json")

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _sweep_cos(sweep):
    """(P1 co id, P2 co id) as the GAME saw them for this sweep.

    co_in_fixture records the CO of the player that was WRITTEN, not P1's, so
    it cannot fill in the other side. The fixture's own P1 is Andy, id 1, which
    every sweep that wrote P1 records directly.
    """
    if not sweep.get("co_abilities"):
        return 1, 1                     # gate shut: record 1 for both sides
    written = sweep.get("co_player", 1)
    return (sweep["co_written"] if written == 1 else 1,
            sweep["co_written"] if written == 2 else 1)


def _effective_co_defense(sweep, unit_type):
    """The defender-side multiplier: per-unit defence folded with universal."""
    from engine import co as co_mod
    _, p2 = _sweep_cos(sweep)
    return co_mod.modifiers(p2, unit_type)[1] * co_mod.universal(p2)[1] // 100


def _effective_co_attack(sweep, unit_type):
    """The attack modifier the GAME actually used for this sweep.

    Not simply the CO that was written. The damage path only consults
    army +0x1D when [0x03004318] is set; with it clear it substitutes record 1,
    Andy, for both sides (DERIVATION 24). So a sweep that wrote Max without
    also forcing that flag measured Andy, and replaying it as Max would fail
    for the right reason in the wrong place.
    """
    from engine import co as co_mod
    p1, _ = _sweep_cos(sweep)
    return co_mod.modifiers(p1, unit_type)[0] * co_mod.universal(p1)[0] // 100


def _effective_luck(sweep):
    """The luck range the GAME rolled for this sweep. Same gate as the
    modifiers: no flag, no CO, so the standard 0..9."""
    from engine import co as co_mod
    if not sweep.get("co_abilities"):
        return (LUCK_MIN, LUCK_MAX)
    return co_mod.luck(_sweep_cos(sweep)[0])


def _stars():
    t = json.loads((ROOT / "data" / "aw1_terrain.json").read_text(encoding="utf-8"))
    s = {v["name"].lower(): v["stars"] for v in t["terrain"].values()}
    s["plains"] = s["plain"]          # the CSV spells it the other way
    return s


def _units():
    u = json.loads((ROOT / "data" / "aw1_unit_stats.json").read_text(encoding="utf-8"))
    return {v["id"]: k for k, v in u["units"].items()}


def _observations():
    with open(ROOT / "harness" / "observations.csv", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("mode") == "exact"]


def _is_counter(row):
    return "counter" in (row.get("notes") or "").lower()


class TestObservationCorpus(unittest.TestCase):
    def setUp(self):
        self.stars = _stars()
        self.rows = _observations()

    def test_the_corpus_is_still_there(self):
        """If this number drops, observations were deleted rather than explained
        -- which is what a calibration tool recommends when the model is wrong."""
        self.assertGreaterEqual(len(self.rows), 75)

    def test_every_first_strike_is_reproduced(self):
        misses = []
        for r in self.rows:
            if _is_counter(r):
                continue
            st = self.stars[r["terrain"].strip().lower()]
            out = resolve(Attack(r["attacker"], r["defender"], int(r["att_hp"]),
                                 int(r["def_hp"]), st, 99,
                                 int(r["co_attack"]), int(r["co_defense"])),
                          verified=True)
            obs = int(r["observed"])
            if out is None or not (out.min_damage <= obs <= out.max_damage):
                rng = "illegal" if out is None else f"{out.min_damage}-{out.max_damage}"
                misses.append(f"{r['attacker']}@{r['att_hp']} -> {r['defender']}"
                              f"@{r['def_hp']} on {r['terrain']}: saw {obs}, "
                              f"model says {rng}")
        self.assertEqual(misses, [], "\n  " + "\n  ".join(misses))

    def test_every_counterattack_is_reproduced_exactly(self):
        """Exactly, not within a range: the counter carries no luck (A9b)."""
        seen = 0
        for r in self.rows:
            if not _is_counter(r):
                continue
            seen += 1
            st = self.stars[r["terrain"].strip().lower()]
            w = select_weapon(r["attacker"], r["defender"])
            self.assertIsNotNone(w)
            self.assertEqual(
                counter_damage(w.base, int(r["att_hp"]), int(r["co_defense"]),
                               st, int(r["def_hp"])),
                int(r["observed"]),
                f"counter {r['attacker']}@{r['att_hp']} -> {r['defender']} "
                f"on {r['terrain']}")
        self.assertEqual(seen, 4, "the four recorded counters")


class TestSeededSweeps(unittest.TestCase):
    """Each sweep is a whole board's damage distribution, measured.

    A sweep constrains far more than a single observation: with the luck seed
    written rather than sampled, the SET of damages the board can produce is
    fully determined, so both the values and how often each occurs are testable.
    """

    def _load(self, name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def _replay(self, sweep):
        """Rebuild the board the sweep ran on and return the model's damage
        multiset over the ten luck rolls, plus what was actually observed."""
        by_row = _units()
        stars = _stars()
        terr = json.loads((ROOT / "data" / "aw1_terrain.json").read_text(encoding="utf-8"))
        att = by_row[sweep["attacker_type"] - 1]
        dfn = by_row[sweep["defender_type"] - 1]
        def_stars = terr["terrain"][str(sweep["defender_terrain"])]["stars"]
        lo, hi = _effective_luck(sweep)
        a = Attack(att, dfn, sweep["attacker_hp"], sweep["defender_hp"],
                   def_stars, co_attack=_effective_co_attack(sweep, att),
                   co_defense=_effective_co_defense(sweep, dfn),
                   luck_min=lo, luck_max=hi)
        model = {damage_for_luck(a, lk) for lk in range(lo, hi + 1)}
        observed = Counter(c["damage"] for c in sweep["cases"]
                           if not c["destroyed"])
        return model, observed

    def _check(self, name, exact=True):
        """`exact` asserts the model's damage set and the game's are equal.

        That is the right test at 64 seeds over a 10-wide roll: every roll is
        near-certain to come up, so a value the model predicts and the game
        never produced is a real disagreement. It is the WRONG test once a CO
        widens the roll. Nell rolls 20 values and Sonja 25, so a given roll is
        missed with probability (19/20)^64 = 3.8% and (24/25)^64 = 7.3%, and
        with several singletons in the band a gap is likelier than not.

        What holds either way, and is asserted either way, is that the game
        never produced a damage the model cannot: an EXTRA is always a failure,
        a missing singleton in a wide band is arithmetic about sampling.
        """
        sweep = self._load(name)
        self.assertTrue(sweep["controls"]["passed"], f"{name}: control failed")
        self.assertEqual(sweep["controls"]["seed"], sweep["controls"]["identity"],
                         f"{name}: the identity write changed the result, so "
                         "nothing measured with it means anything")
        model, observed = self._replay(sweep)
        extra = set(observed) - model
        self.assertEqual(extra, set(),
                         f"{name}: the game produced {sorted(extra)}, which the "
                         f"model cannot")
        if exact:
            self.assertEqual(set(observed), model,
                             f"{name}: the model's damage set and the game's differ")
        else:
            missing = model - set(observed)
            self.assertLessEqual(
                len(missing), 2,
                f"{name}: {len(missing)} model values unrolled ({sorted(missing)}), "
                f"too many to be sampling")
        return sweep, observed

    def test_attacker_at_57_says_ceil(self):
        """Writing the attacker to 57 internal HP. ceil scales it as 6 and
        floor_min1 as 5, which are 27-32 against 22-27 -- and the game said
        27-32. This is the sweep that refuted floor_min1."""
        sweep, observed = self._check("att57.json")
        self.assertEqual(sweep["hp_written"]["attacker"], 57)
        self.assertEqual(min(observed), 27)
        self.assertEqual(max(observed), 32)

    def test_defender_at_81_says_ceil_and_excludes_round(self):
        """81 is the value that separates ceil from round: ceil displays 9,
        every other candidate displays 8."""
        sweep, observed = self._check("def81.json")
        self.assertEqual(sweep["hp_written"]["defender"], 81)
        self.assertEqual((min(observed), max(observed)), (48, 53))

    def test_a_non_neutral_co_truncates_before_everything_else(self):
        """The first measurement taken with a CO that is not Andy, and it
        caught a bug the whole corpus was blind to.

        Max is 150/100 on Tank. Exact arithmetic gives 75 * 150/100 = 112.5 and
        predicts 90..97; the game said 89..96, which is floor(112.5) = 112. The
        engine had carried that term as a Fraction since the beginning and
        nothing noticed, because 100/100 divides exactly and all 75 corpus rows
        and every earlier sweep were neutral.

        The shape settles it as firmly as the range. Truncated, ten rolls
        collapse onto eight damages with 92 and 96 doubled; exact would double
        90 and 94 instead. The sweep saw 92 eleven times and 96 fourteen,
        against 5-8 for the rest."""
        sweep, observed = self._check("max_wood_co.json")
        self.assertEqual(sweep["co_written"], 2)
        self.assertEqual(sweep["co_abilities"], 1)
        self.assertEqual((min(observed), max(observed)), (89, 96))
        # The doubled values, which only the truncated model produces.
        doubled = {d for d, n in observed.items() if n >= 10}
        self.assertEqual(doubled, {92, 96})

    def test_a_written_co_without_the_flag_measured_andy(self):
        """The four sweeps that wasted an afternoon. Kept as the reason
        co_abilities is recorded: without it a reader cannot tell a sweep where
        the CO took effect from one where it was silently replaced."""
        sweep = self._load("max_wood_co.json")
        no_flag = dict(sweep, co_abilities=None)
        self.assertEqual(_effective_co_attack(no_flag, "Tank"), 100)
        self.assertEqual(_effective_co_attack(sweep, "Tank"), 150)

    def test_nells_roll_reaches_19(self):
        """The measurement that turned A11 from a reading of two bytes into a
        fact. Nell carries +06=10, which the rule reads as 0..19; on this board
        that is damage 60..75 where a standard CO caps at 67. The sweep saw 75,
        which requires a roll of 19 and no standard CO can produce."""
        sweep, observed = self._check("nell_wood_luck.json", exact=False)
        self.assertEqual((sweep["co_written"], sweep["co_abilities"]), (0, 1))
        self.assertEqual(max(observed), 75)
        above = {d for d in observed if d > 67}
        self.assertTrue(above, "no damage above the standard band's 67")

    def test_sonjas_roll_goes_negative(self):
        """The half that actually mattered. Sonja's symmetric +06=15/+07=15
        reads as -15..9 -- a window the same width as everyone else's, slid
        down -- and a negative roll lowers the MINIMUM, which is what made
        quoting her as a standard CO report kills that will not land. Damage
        below 60 requires it, and the sweep went to 48."""
        sweep, observed = self._check("sonja_wood_luck.json", exact=False)
        self.assertEqual((sweep["co_written"], sweep["co_abilities"]), (7, 1))
        self.assertEqual(min(observed), 48)
        below = {d for d in observed if d < 60}
        self.assertTrue(below, "no damage below the standard band's 60")

    def test_the_three_cos_separate_on_one_board(self):
        """Same fixture, same seeds, three COs: the bands barely overlap and
        each is exactly what its record predicts. Nothing but +0x1D and the
        gate differed."""
        bands = {}
        for name in ("wood100.json", "nell_wood_luck.json",
                     "sonja_wood_luck.json"):
            _, observed = self._replay(self._load(name))
            bands[name] = (min(observed), max(observed))
        self.assertEqual(bands["wood100.json"], (60, 67))          # Andy
        self.assertEqual(bands["nell_wood_luck.json"], (60, 75))   # Nell
        self.assertEqual(bands["sonja_wood_luck.json"], (48, 67))  # Sonja

    def test_kanbei_attacking_applies_the_universal_pair(self):
        """Kanbei's per-unit entries are all 100/100; everything he has is in
        header +11/+12. A model reading only the pool quotes him as Andy at
        60-67, which is why he was refused rather than answered. He lands
        72-79: the value multiplied by 120/100 before luck."""
        sweep, observed = self._check("kanbei_att_wood.json")
        self.assertEqual((sweep["co_written"], sweep["co_player"]), (6, 1))
        self.assertEqual((min(observed), max(observed)), (72, 79))
        self.assertEqual(_effective_co_attack(sweep, "Tank"), 120)

    def test_kanbei_defending_multiplies_the_value_not_the_bracket(self):
        """The one that changed the formula's shape.

        His +12 reads 80. Three candidates: ignore it (60-67), add it inside
        the terrain bracket as the engine used to (45-50), or multiply the
        value before luck (48-55). The game said 48-55.

        That matters beyond Kanbei. `co_def` had sat inside the bracket since
        the beginning and no measurement could object, because at 100 the two
        forms are identical and every observation before this one was
        neutral."""
        sweep, observed = self._check("kanbei_def_wood.json")
        self.assertEqual((sweep["co_written"], sweep["co_player"]), (6, 2))
        self.assertEqual((min(observed), max(observed)), (48, 55))
        self.assertEqual(_effective_co_defense(sweep, "Infantry"), 80)

    def test_defender_at_85_agrees(self):
        sweep, observed = self._check("def85.json")
        self.assertEqual(sweep["hp_written"]["defender"], 85)
        self.assertEqual((min(observed), max(observed)), (48, 53))

    def test_defender_at_65_tests_the_product_form(self):
        """The defender term is `terrain_stars * display_hp(defender)` -- a
        PRODUCT, and two points can only fit a line rather than test one. This
        is the third point on the display axis at 4 stars: display 10, 9 and
        now 7, giving 45-50, 48-53 and 54-60. All three match the linear form.

        It also re-refutes floor_min1 independently of att57: that rule reads 65
        as display 6 and predicts 57-63, and the game said 54-60."""
        sweep, observed = self._check("def65.json")
        self.assertEqual(sweep["hp_written"]["defender"], 65)
        self.assertEqual((min(observed), max(observed)), (54, 60))


    def test_wood_and_city_confirm_ceil_off_the_mountain(self):
        """Every earlier sweep was on a mountain. If `ceil` were an artifact of
        4-star terrain, these would miss."""
        for name, hp, rng in (("wood100.json", 100, (60, 67)),
                              ("wood81.json", 81, (61, 68)),
                              ("city100.json", 100, (52, 58)),
                              ("city81.json", 81, (54, 61))):
            sweep, observed = self._check(name)
            self.assertEqual(sweep["defender_hp"], hp, name)
            self.assertEqual((min(observed), max(observed)), rng, name)

    def test_the_defender_term_is_a_product_across_the_STARS_axis(self):
        """`terrain_stars * display_hp(defender)` is a product, and until Wood
        and City existed every partial-defender observation was on a mountain --
        so the stars factor had one value and could not be tested at all. At a
        fixed display of 9, three terrains now pin it."""
        for name, stars, rng in (("wood81.json", 2, (61, 68)),
                                 ("city81.json", 3, (54, 61)),
                                 ("def81.json", 4, (48, 53))):
            sweep = self._load(name)
            terr = json.loads((ROOT / "data" / "aw1_terrain.json")
                              .read_text(encoding="utf-8"))
            self.assertEqual(
                terr["terrain"][str(sweep["defender_terrain"])]["stars"], stars,
                name)
            observed = {c["damage"] for c in sweep["cases"]
                        if not c["destroyed"]}
            self.assertEqual((min(observed), max(observed)), rng, name)


    def test_the_fixture_tile_is_the_PRE_MOVE_tile(self):
        """A fixture sits at target-select -- after the move is chosen, before
        it is confirmed -- and the attacker's record still holds the tile it
        started on. Measured directly rather than inferred: the City fixture
        reads terrain 5 (Road, 0 stars) before confirming and terrain 1 (Plain,
        1 star) after, on every seed.

        This is why the City counters missed on both hypotheses. The formula
        was right; the tile was wrong. See A10.
        """
        sweep = self._load("city81b.json")
        self.assertEqual(sweep["attacker_terrain"], 5)          # Road, 0 stars
        self.assertEqual(sweep["attacker_terrain_after"], 1)    # Plain, 1 star
        self.assertTrue(sweep["attacker_moved_on_confirm"])
        self.assertTrue(all(c["attacker_moved_on_confirm"]
                            for c in sweep["cases"]))

        # And the counter reproduces ONLY with the tile it fought from. This is
        # the assertion that makes the finding load-bearing rather than a note.
        w = select_weapon("Infantry", "Tank")
        fought, recorded = 0, 0
        for c in sweep["cases"]:
            survivor = sweep["defender_hp"] - c["damage"]
            fought += counter_damage(w.base, survivor, 100, 1, 100) == c["counter"]
            recorded += counter_damage(w.base, survivor, 100, 0, 100) == c["counter"]
        n = len(sweep["cases"])
        self.assertEqual(fought, n, "the tile it fought from must reproduce")
        self.assertEqual(recorded, 0, "the recorded tile must not")

    def test_the_short_sweep_is_a_subset_of_the_model(self):
        """city81b is 8 seeds, not 64, so it cannot hit every damage value --
        it was run to read one line of diagnostics. Assert containment, not
        equality, or this passes for the wrong reason."""
        sweep = self._load("city81b.json")
        model, observed = self._replay(sweep)
        self.assertTrue(set(observed) <= model,
                        f"observed {sorted(observed)} not within {sorted(model)}")
        self.assertTrue(sweep["controls"]["passed"])

    def test_the_counter_is_a_function_of_the_survivor(self):
        """The no-luck claim, stated correctly.

        The first version of this asserted the counter was CONSTANT across a
        sweep, which held for every board that existed at the time and then
        failed on wood100 -- where the survivor crosses a threshold and the
        counter legitimately moves between 0 and 1. Constant was never the
        claim. The claim is that the counter carries no roll of its OWN: equal
        survivors must counter equally, however much the opening varies.
        """
        for name in SWEEPS:
            sweep = self._load(name)
            by_survivor = {}
            for c in sweep["cases"]:
                if c["attacker_destroyed"]:
                    continue
                survivor = sweep["defender_hp"] - c["damage"]
                by_survivor.setdefault(survivor, set()).add(c["counter"])
            self.assertGreater(len({c["damage"] for c in sweep["cases"]}), 1,
                               f"{name}: opening did not vary, so this proves "
                               "nothing")
            for survivor, counters in sorted(by_survivor.items()):
                self.assertEqual(
                    len(counters), 1,
                    f"{name}: survivor at {survivor} HP countered for "
                    f"{sorted(counters)} on different seeds -- that is a luck "
                    "roll in the counter")

    def test_the_model_reproduces_each_sweeps_counter(self):
        skipped = []
        by_row = _units()
        terr = json.loads((ROOT / "data" / "aw1_terrain.json").read_text(encoding="utf-8"))
        for name in SWEEPS:
            sweep = self._load(name)
            att = by_row[sweep["attacker_type"] - 1]
            dfn = by_row[sweep["defender_type"] - 1]
            # The tile it FOUGHT from, not the one the fixture recorded. A
            # fixture sits at target-select; if the unit record still holds the
            # pre-move tile, `attacker_terrain` names somewhere the unit never
            # fired from. Sweeps predating that distinction are skipped rather
            # than scored against a guess -- see A9b.
            after = sweep.get("attacker_terrain_after",
                              sweep["attacker_terrain"])
            if name in UNRESOLVED_ATTACKER_TERRAIN and \
                    "attacker_terrain_after" not in sweep:
                skipped.append(name)
                continue
            att_stars = terr["terrain"][str(after)]["stars"]
            w = select_weapon(dfn, att)
            for c in sweep["cases"]:
                if c["attacker_destroyed"]:
                    continue
                survivor = sweep["defender_hp"] - c["damage"]
                if survivor <= 0:
                    continue
                self.assertEqual(
                    counter_damage(w.base, survivor, 100, att_stars,
                                   c["attacker_hp_before"]),
                    c["counter"],
                    f"{name} seed {c['seed']}: survivor {survivor}")
        self.assertEqual(sorted(skipped),
                         sorted(UNRESOLVED_ATTACKER_TERRAIN),
                         "the set of sweeps with a disputed attacker tile "
                         "changed -- if a re-sweep resolved one, drop it from "
                         "UNRESOLVED_ATTACKER_TERRAIN")


if __name__ == "__main__":
    unittest.main(verbosity=2)

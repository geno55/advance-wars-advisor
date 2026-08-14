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

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


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
        a = Attack(att, dfn, sweep["attacker_hp"], sweep["defender_hp"], def_stars)
        model = {damage_for_luck(a, lk) for lk in range(LUCK_MIN, LUCK_MAX + 1)}
        observed = Counter(c["damage"] for c in sweep["cases"]
                           if not c["destroyed"])
        return model, observed

    def _check(self, name):
        sweep = self._load(name)
        self.assertTrue(sweep["controls"]["passed"], f"{name}: control failed")
        self.assertEqual(sweep["controls"]["seed"], sweep["controls"]["identity"],
                         f"{name}: the identity write changed the result, so "
                         "nothing measured with it means anything")
        model, observed = self._replay(sweep)
        self.assertEqual(set(observed), model,
                         f"{name}: the model's damage set and the game's differ")
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

    def test_the_counter_is_constant_while_the_opening_varies(self):
        """The shape of A9b's evidence, kept as a regression: if a future model
        change reintroduces a luck term in the counter, this fails."""
        for name in ("counter.json", "att57.json", "def81.json",
                     "def85.json", "def65.json"):
            sweep = self._load(name)
            openings = {c["damage"] for c in sweep["cases"]}
            counters = {c["counter"] for c in sweep["cases"]
                        if not c["attacker_destroyed"]}
            self.assertGreater(len(openings), 1, f"{name}: opening did not vary")
            self.assertEqual(len(counters), 1,
                             f"{name}: the counter varied across seeds, which "
                             "means it carries luck after all")

    def test_the_model_reproduces_each_sweeps_counter(self):
        by_row = _units()
        terr = json.loads((ROOT / "data" / "aw1_terrain.json").read_text(encoding="utf-8"))
        for name in ("counter.json", "att57.json", "def81.json",
                     "def85.json", "def65.json"):
            sweep = self._load(name)
            att = by_row[sweep["attacker_type"] - 1]
            dfn = by_row[sweep["defender_type"] - 1]
            att_stars = terr["terrain"][str(sweep["attacker_terrain"])]["stars"]
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Eliminate wrong damage-formula hypotheses using observations from the game.

We cannot read the formula out of the ROM as easily as the tables, and we must
not guess it. So: enumerate every (formula variant x terrain-star assignment)
hypothesis, then delete the ones that contradict what the emulator actually did.

Two things make this harder than a plain equality check, and both are handled
as *constraints* rather than assumed away:

  1. Luck. Each attack rolls 0..9 and you cannot see the roll. An observation
     is consistent with a hypothesis if SOME luck value reproduces it.
  2. Display HP. A human watching the screen sees 1..10, not the internal
     1..100. So an observed post-attack HP of 5 only tells us the internal
     value landed in 41..50.

Usage:
    python calibrate.py observations.csv          # narrow the hypothesis set
    python calibrate.py observations.csv --suggest  # what to measure next
    python calibrate.py --selftest                # prove the machinery works
"""
from __future__ import annotations

import csv
import dataclasses
import functools
import itertools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.damage import (VARIANTS, DISPLAY_VARIANTS, LUCK_MIN,  # noqa: E402
                           LUCK_MAX, display_hp, screen_bars,
                           select_weapon, tables)

STAR_RANGE = range(0, 5)          # AW terrain defence is 0..4 stars

# The game shows terrain defence as "Def" when you highlight a tile, so it is
# observable, not something to infer. Known values are pinned; anything absent
# stays a free parameter. This collapses the search enormously -- with four
# terrains it is 5^4 = 625 star maps versus 1.
_TERRAIN_FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "aw1_terrain.json"
try:
    KNOWN_STARS = json.loads(_TERRAIN_FILE.read_text(encoding="utf-8"))["stars"]
except (OSError, ValueError, KeyError):
    KNOWN_STARS = {}


def star_options(terrain):
    """Candidate star values for one terrain: pinned if we know it."""
    if terrain in KNOWN_STARS:
        return (KNOWN_STARS[terrain],)
    return tuple(STAR_RANGE)


class Obs:
    __slots__ = ("attacker", "defender", "att_hp", "def_hp", "terrain",
                 "mode", "observed", "co_atk", "co_def", "line")

    def __init__(self, row, line):
        self.attacker = row["attacker"].strip()
        self.defender = row["defender"].strip()
        self.att_hp = int(row["att_hp"])
        self.def_hp = int(row["def_hp"])
        self.terrain = row["terrain"].strip().lower()
        self.mode = row["mode"].strip()
        self.observed = int(row["observed"])
        self.co_atk = int(row.get("co_attack") or 100)
        self.co_def = int(row.get("co_defense") or 100)
        self.line = line

    def __repr__(self):
        return (f"{self.attacker}({self.att_hp}) -> {self.defender}({self.def_hp}) "
                f"on {self.terrain}: {self.mode}={self.observed}")


@dataclasses.dataclass
class Hypothesis:
    """One candidate model. Named fields, not a tuple.

    It was a tuple, with shared-luck mode distinguished by `len(h) > 3` and the
    display rule read as `h[2]` at eight separate sites. Adding the fourth field
    below to a positional tuple would have silently shifted every one of them,
    and the failure would have looked like a calibration result rather than a
    bug. So: attributes.
    """
    variant: str
    stars: dict                       # terrain name -> stars
    att_display: str
    def_display: str
    luck: int = None                  # set only in shared-luck mode

    def rules(self):
        return self.att_display, self.def_display

    def __repr__(self):
        d = (f"{self.att_display}" if self.att_display == self.def_display
             else f"att={self.att_display}/def={self.def_display}")
        return (f"<{self.variant} {d} "
                + " ".join(f"{k}={v}" for k, v in sorted(self.stars.items()))
                + (f" luck={self.luck}" if self.luck is not None else "") + ">")


@functools.lru_cache(maxsize=None)
def _predict(attacker, defender, att_hp, def_hp, mode, co_atk, co_def,
             variant, stars, att_display=None, def_display=None):
    """All outcomes this hypothesis considers possible, over the luck range.

    Memoised on primitives: the recording protocol repeats each matchup ~10x,
    so this collapses the hypothesis sweep from millions of Fraction operations
    to a few thousand, which is what makes live feedback while recording
    feasible.
    """
    w = select_weapon(attacker, defender)
    if w is None:
        return frozenset()
    fn = VARIANTS[variant]
    out = set()
    for luck in range(LUCK_MIN, LUCK_MAX + 1):
        raw = fn(w.base, display_hp(att_hp, att_display), co_atk, co_def,
                 stars, display_hp(def_hp, def_display), luck)
        dmg = max(0, min(raw, def_hp))
        if mode == "exact":
            out.add(dmg)
        elif mode == "display_after":
            # What the player reports is the on-screen bar count, which rounds
            # up even though the combat maths does not.
            out.add(screen_bars(max(0, def_hp - dmg)))
        else:
            raise ValueError(f"unknown mode {mode!r}")
    return frozenset(out)


def predict(obs, variant, stars, att_display=None, def_display=None):
    return _predict(obs.attacker, obs.defender, obs.att_hp, obs.def_hp,
                    obs.mode, obs.co_atk, obs.co_def, variant, stars,
                    att_display, def_display)


@functools.lru_cache(maxsize=None)
def _outcome(attacker, defender, att_hp, def_hp, mode, co_atk, co_def,
             variant, stars, luck, att_display=None, def_display=None):
    """The single outcome for one specific luck roll."""
    w = select_weapon(attacker, defender)
    if w is None:
        return None
    raw = VARIANTS[variant](w.base, display_hp(att_hp, att_display),
                            co_atk, co_def, stars,
                            display_hp(def_hp, def_display), luck)
    dmg = max(0, min(raw, def_hp))
    if mode == "exact":
        return dmg
    # What the PLAYER reports is the on-screen bar count, which rounds up even
    # though the combat maths does not.
    return screen_bars(max(0, def_hp - dmg))


def outcome(obs, variant, stars, luck, att_display=None, def_display=None):
    return _outcome(obs.attacker, obs.defender, obs.att_hp, obs.def_hp,
                    obs.mode, obs.co_atk, obs.co_def, variant, stars, luck,
                    att_display, def_display)


def consistent(obs, h):
    return obs.observed in predict(obs, h.variant, h.stars[obs.terrain],
                                   h.att_display, h.def_display)


def enumerate_hypotheses(terrains):
    """A hypothesis is (formula variant, star map, ATTACKER display rule,
    DEFENDER display rule).

    The two display rules are separate parameters, and that is the whole point
    of this function. They used to be one, applied to both operands -- which
    meant the hypothesis "the attacker rounds one way and the defender another"
    did not exist in the search space and could not be eliminated OR confirmed.
    Worse, a single rule made the corpus look decisive when it was not: the
    defender was at 100 internal HP in every row, where all four rules agree, so
    the defender slot carried no information at all and the attacker slot got
    all the credit for a fit that was really about neither.

    Nothing about the arithmetic is asserted here; the data eliminates. If both
    slots come back with the same survivor set, that is a result rather than an
    assumption.
    """
    terrains = sorted(terrains)
    options = [star_options(t) for t in terrains]
    for variant in VARIANTS:
        for att_disp, def_disp in itertools.product(DISPLAY_VARIANTS, repeat=2):
            for combo in itertools.product(*options):
                yield Hypothesis(variant, dict(zip(terrains, combo)),
                                 att_disp, def_disp)


def survivors(observations, shared_luck=False):
    """Surviving hypotheses, as Hypothesis objects.

    shared_luck=False treats each observation independently -- it only asserts
    that SOME roll in 0..9 explains it. Correct when the roll actually varies.

    shared_luck=True additionally requires ONE roll to explain every observation
    in the batch. Use this when a save state restores the RNG and every battle is
    launched from the same state, so the roll is frozen. It is a far stronger
    constraint: without it, a constant hidden roll cannot be disentangled from
    the formula and calibration stalls with dozens of survivors.
    """
    terrains = {o.terrain for o in observations}
    alive = []
    for h in enumerate_hypotheses(terrains):
        if shared_luck:
            for luck in range(LUCK_MIN, LUCK_MAX + 1):
                if all(outcome(o, h.variant, h.stars[o.terrain], luck,
                               h.att_display, h.def_display) == o.observed
                       for o in observations):
                    alive.append(dataclasses.replace(h, luck=luck))
        elif all(consistent(o, h) for o in observations):
            alive.append(h)
    return alive


AGREEMENT_SWEEP = [("Tank", "Tank"), ("MdTank", "Infantry"), ("Rockets", "Tank"),
                   ("Artillery", "Infantry"), ("BCopter", "Tank"), ("Mech", "Tank"),
                   ("Infantry", "Infantry"), ("AntiAir", "Recon")]


def agreement(alive):
    """Do the surviving hypotheses actually PREDICT differently?

    Converging to a single hypothesis is the wrong success criterion. Several
    (variant, stars, luck) triples can be different labels for identical
    behaviour -- notably floor_end and floor_attack_then_end are mathematically
    the same whenever the CO modifier is 100, since base*100/100 is exact. What
    matters is whether anything we would ever TELL the player differs.

    Returns (scenarios, damage_disagreements, kill_disagreements, max_spread).
    """
    scen = dmg_dis = kill_dis = spread = 0
    for att, dfn in AGREEMENT_SWEEP:
        w = select_weapon(att, dfn)
        if w is None:
            continue
        for ahp in (100, 70, 50, 30):
            for dhp in (100, 65, 35, 15):
                for stars in range(5):
                    sets = []
                    for h in alive:
                        lucks = ([h.luck] if h.luck is not None
                                 else range(LUCK_MIN, LUCK_MAX + 1))
                        vals = {max(0, min(VARIANTS[h.variant](
                            w.base, display_hp(ahp, h.att_display), 100, 100,
                            stars, display_hp(dhp, h.def_display), lk),
                            dhp)) for lk in lucks}
                        sets.append(frozenset(vals))
                    scen += 1
                    if len(set(sets)) > 1:
                        dmg_dis += 1
                        allv = [v for s in sets for v in s]
                        spread = max(spread, max(allv) - min(allv))
                    if len({min(s) >= dhp for s in sets}) > 1:
                        kill_dis += 1
    return scen, dmg_dis, kill_dis, spread


def describe_agreement(alive):
    scen, dmg, kill, spread = agreement(alive)
    if kill == 0 and dmg == 0:
        return ("all survivors predict IDENTICALLY across "
                f"{scen} unseen scenarios -- they are the same model under "
                "different labels. This is as converged as it needs to be.")
    if kill == 0:
        return (f"survivors differ on exact damage in {dmg}/{scen} scenarios "
                f"(max {spread} internal HP) but NEVER on kill/no-kill. "
                "Safe for tactical advice.")
    return (f"survivors disagree on kill/no-kill in {kill}/{scen} scenarios "
            f"(max damage spread {spread}). NOT yet safe for advice -- "
            "record more battles.")


def diagnose(observations, shared_luck=False):
    """When nothing survives, find which rows are responsible.

    Leave-one-out, then leave-two-out. Reporting 'a row is wrong somewhere' is
    useless; naming the row (and what it would need to be true) is actionable.
    """
    n = len(observations)
    print(f"nothing survives with all {n} observations. isolating the conflict.\n")

    singles = [i for i in range(n)
               if survivors(observations[:i] + observations[i + 1:], shared_luck)]
    if singles:
        print("removing any ONE of these restores consistency:")
        for i in singles:
            print(f"  line {observations[i].line}: {observations[i]}")
        print("\nOne of them is mis-recorded. The most likely culprit is whichever")
        print("involves a terrain you are least sure of.")
        return singles

    print("no single row explains it; trying pairs...")
    for i in range(n):
        for j in range(i + 1, n):
            rest = [o for k, o in enumerate(observations) if k not in (i, j)]
            if survivors(rest, shared_luck):
                print(f"removing BOTH of these restores consistency:")
                print(f"  line {observations[i].line}: {observations[i]}")
                print(f"  line {observations[j].line}: {observations[j]}")
                return [i, j]

    print("no pair explains it either. Either three or more rows are wrong, or")
    print("the real formula is not among the variants in engine/damage.py.")
    return []


def explain(obs):
    """What would each terrain-star value require for this one observation?"""
    print(f"{obs}")
    for stars in range(5):
        vals = sorted(predict(obs, v, stars) for v in VARIANTS)
        allv = sorted({x for s in vals for x in s})
        ok = "possible" if obs.observed in allv else "IMPOSSIBLE"
        rng = f"{min(allv)}-{max(allv)}" if allv else "-"
        print(f"  {stars} stars: {rng:>9s}  {ok}")


def report(observations, alive):
    terrains = sorted({o.terrain for o in observations})
    pinned = [t for t in terrains if t in KNOWN_STARS]
    print(f"{len(observations)} observations, {len(terrains)} terrain(s): "
          + ", ".join(terrains))
    if pinned:
        print("terrain Def read from the game (not inferred): "
              + ", ".join(f"{t}={KNOWN_STARS[t]}" for t in pinned))
    total = len(VARIANTS) * len(DISPLAY_VARIANTS)
    for t in terrains:
        total *= len(star_options(t))
    print(f"{len(alive)} of {total} hypotheses survive\n")

    if not alive:
        print("NOTHING survives. Either an observation is mis-recorded, or the")
        print("real formula is not among the variants in engine/damage.py.")
        print("Both are worth knowing; do not 'fix' this by widening the luck range.")
        return

    vs = sorted({h.variant for h in alive})
    att_ds = sorted({h.att_display for h in alive})
    def_ds = sorted({h.def_display for h in alive})
    print(f"formula variants still possible ({len(vs)}): {', '.join(vs)}")
    if len(vs) > 1:
        # This tool asks only "does SOME luck value reproduce each observation".
        # That can never separate variants whose ranges nest, and luck_after_hp's
        # range sits inside luck_last's whenever the defence multiplier is <= 1,
        # which with a neutral CO is always. Saying so beats leaving a reader to
        # conclude the question is open when it has been settled elsewhere.
        print("  NOTE: this is set-based elimination -- it cannot separate "
              "variants whose ranges nest, which these do.")
        print("  The formula was settled by a seeded sweep instead "
              "(tools/rng_fit.py, DERIVATION.md 17): luck_after_hp.")
    # Report the two slots SEPARATELY. Collapsing them is what made a
    # corpus carrying no defender-side information look like it had
    # settled both.
    for label, ds, blind in (("attacker", att_ds, "attacker at 100 or 9"),
                             ("defender", def_ds, "defender at 100")):
        print(f"{label} display rule ({len(ds)}): {', '.join(ds)}"
              + ("  (determined)" if len(ds) == 1 else ""))
        if len(ds) > 1:
            # Do not let this read as "unsettled". This corpus cannot
            # settle it at any sample size, because every row sits on
            # values where the candidates agree. It was settled elsewhere,
            # by WRITING HP to a value where they differ.
            print(f"  This corpus CANNOT narrow this: every row has the "
                  f"{blind} internal HP,")
            print("  where these rules return the same number. No sample "
                  "size fixes that. Settled")
            print("  instead by seeded sweeps at written HP -- "
                  "ASSUMPTIONS A9a, fixtures in")
            print("  tests/fixtures/, replayed by tests/test_corpus.py: "
                  "ceil.")
    if att_ds != def_ds:
        print("  NOTE: the two slots have different survivor sets, so "
              "this corpus does")
        print("  constrain them asymmetrically. That is a result, not a "
              "bug -- but check it")
        print("  against A9a before believing it.")

    for t in terrains:
        vals = sorted({h.stars[t] for h in alive})
        star = "determined" if len(vals) == 1 else "ambiguous"
        print(f"  {t:10s} stars: {vals}  ({star})")
    if alive[0].luck is not None:
        lucks = sorted({h.luck for h in alive})
        print(f"  shared luck roll: {lucks}"
              + ("  (determined)" if len(lucks) == 1 else ""))

    print("\n" + describe_agreement(alive))

    stars_pinned = len({tuple(sorted(h.stars.items())) for h in alive}) == 1
    print(f"terrain stars fully determined: {stars_pinned}")

    _, dmg, kill, _ = agreement(alive)
    if stars_pinned and kill == 0 and dmg == 0:
        h = alive[0]
        print(f"\nDONE. Set DEFAULT_VARIANT='{h.variant}' in engine/damage.py,")
        print("then run  python tools/verify_corpus.py --write  to recompute")
        print("provenance.verified_against_emulator from the replay. Do NOT")
        print("set that flag by hand: it is derived, and a hand-set one is")
        print("rejected for having no record of how it was arrived at.")
        if len(att_ds) == 1 and len(def_ds) == 1 and att_ds == def_ds:
            print(f"DEFAULT_DISPLAY='{h.att_display}' -- both operands are "
                  "pinned, and to the same rule")
        elif len(att_ds) == 1 and len(def_ds) == 1:
            # Both pinned, to DIFFERENT rules. A real result, and one the
            # engine cannot currently express: _terms() applies a single
            # display_hp to both operands. Say that rather than quietly
            # reporting the pair as though it could be configured.
            print(f"  attacker rule: {att_ds[0]}   defender rule: {def_ds[0]}")
            print("  BOTH PINNED, AND THEY DIFFER. engine/damage.py cannot "
                  "express this today --")
            print("  _terms() applies one display_hp to both operands. Split "
                  "it before setting")
            print("  anything, or the model cannot represent what was measured.")
        else:
            # This is the message that once said "any survivor will do -- they
            # are equivalent" and named a single display rule. They are
            # equivalent ON THE OBSERVATIONS, which is not the same as being
            # equivalent, and picking one arbitrarily is how a rule the corpus
            # never constrained ended up hardcoded for months.
            print(f"  attacker rule survivors: {', '.join(att_ds)}")
            print(f"  defender rule survivors: {', '.join(def_ds)}")
            print("  DO NOT pick one. The survivors agree on every observation "
                  "you have, which")
            print("  is not the same as agreeing. Measure a board where they "
                  "differ -- write the")
            print("  HP and sweep the seed -- or carry the ambiguity.")
    else:
        print("\nNot done. Run with --suggest for the most informative next test.")


# --------------------------------------------------------------------------
# active learning: what should we measure next?
# --------------------------------------------------------------------------

CANDIDATE_ATTACKS = [
    ("Infantry", "Infantry"), ("Infantry", "Mech"), ("Tank", "Infantry"),
    ("Tank", "Tank"), ("Tank", "Recon"), ("Artillery", "Tank"),
    ("MdTank", "Tank"), ("Rockets", "Infantry"), ("AntiAir", "Infantry"),
    ("BCopter", "Tank"), ("Mech", "Tank"),
]
CANDIDATE_HP = [100, 70, 50, 30]


def suggest(alive, terrains, mode="display_after", top=8):
    """Pick experiments that split the surviving hypotheses most evenly.

    An experiment is informative in proportion to how many distinct predicted
    outcomes it produces across live hypotheses -- if every hypothesis predicts
    the same thing, running it teaches you nothing.
    """
    ideas = []
    for (att, dfn), ahp, dhp, terr in itertools.product(
            CANDIDATE_ATTACKS, CANDIDATE_HP, [100], sorted(terrains)):
        if select_weapon(att, dfn) is None:
            continue
        probe = Obs({"attacker": att, "defender": dfn, "att_hp": str(ahp),
                     "def_hp": str(dhp), "terrain": terr,
                     "mode": mode, "observed": "0"}, 0)
        buckets = {}
        for h in alive:
            key = frozenset(predict(probe, h.variant, h.stars[terr],
                                    h.att_display, h.def_display))
            buckets.setdefault(key, []).append(h)
        if len(buckets) < 2:
            continue
        # Even splits are best; score by the size of the largest remaining group.
        worst_case = max(len(v) for v in buckets.values())
        ideas.append((worst_case, len(buckets), att, dfn, ahp, terr))

    ideas.sort(key=lambda x: (x[0], -x[1]))
    print("\nmost informative experiments (worst-case survivors after each):")
    seen = set()
    shown = 0
    for worst, nb, att, dfn, ahp, terr in ideas:
        sig = (att, dfn, ahp, terr)
        if sig in seen:
            continue
        seen.add(sig)
        print(f"  {att:10s} at {ahp:3d} HP  ->  full-health {dfn:10s} on {terr:9s}"
              f"   splits {len(alive)} into {nb} groups, worst case {worst} left")
        shown += 1
        if shown >= top:
            break


# --------------------------------------------------------------------------
# selftest: validate the calibration machinery without an emulator
# --------------------------------------------------------------------------

def selftest():
    """Generate observations from a KNOWN variant + star map, then check that
    calibration recovers it. This validates the harness, not the game model --
    it proves that when real data arrives, the logic will do the right thing."""
    import random
    rng = random.Random(1234)
    truth_variant = "floor_each_step"
    # DELIBERATELY DIFFERENT rules for the two operands. The point of this
    # selftest is now to prove the machinery can recover an asymmetric truth --
    # a hypothesis that did not exist in the search space until the display rule
    # was split, and therefore one the tool would previously have missed while
    # reporting a confident answer.
    truth_att_display = "floor"
    truth_def_display = "ceil"
    truth_stars = {"plains": 1, "woods": 2, "mountain": 4, "road": 0}

    obs = []
    line = 0
    for (att, dfn) in CANDIDATE_ATTACKS:
        if select_weapon(att, dfn) is None:
            continue
        for terr in truth_stars:
            # 100 is degenerate on the attacker side too -- and 70 and 50, and
            # every other multiple of ten. The first draft of this selftest used
            # (100, 70, 50) and left the attacker rule four-way ambiguous while
            # reporting success, which is the same blind spot that put a refuted
            # rule in DEFAULT_DISPLAY. 57 splits floor/floor_min1 from
            # ceil/round; 7 splits floor from floor_min1.
            for ahp in (100, 57, 7):
                # The defender HP must VARY, or the defender slot carries no
                # information and the selftest would pass while proving nothing
                # about the half it exists to check. 100 is the degenerate value
                # where every rule agrees; 81 and 65 are where they part.
                for dhp, mode in ((100, "display_after"), (81, "exact"),
                                  (65, "exact")):
                    line += 1
                    probe = Obs({"attacker": att, "defender": dfn,
                                 "att_hp": str(ahp), "def_hp": str(dhp),
                                 "terrain": terr, "mode": mode,
                                 "observed": "0"}, line)
                    outcomes = sorted(predict(probe, truth_variant,
                                              truth_stars[terr],
                                              truth_att_display,
                                              truth_def_display))
                    if not outcomes:
                        continue
                    probe.observed = rng.choice(outcomes)
                    obs.append(probe)

    print(f"selftest: truth variant={truth_variant} "
          f"att_display={truth_att_display} def_display={truth_def_display} "
          f"stars={truth_stars}")
    print(f"generated {len(obs)} synthetic observations")
    alive = survivors(obs)
    report(obs, alive)
    ok = any(h.variant == truth_variant and h.stars == truth_stars
             and h.att_display == truth_att_display
             and h.def_display == truth_def_display for h in alive)
    asym = any(h.att_display != h.def_display for h in alive)
    print(f"asymmetric hypotheses reachable at all: {asym}")
    if not asym:
        sys.exit("SELFTEST FAILED: the search space has no asymmetric "
                 "hypothesis, so splitting the display rule did nothing")
    print(f"\ntruth retained in survivor set: {ok}")
    if not ok:
        sys.exit("SELFTEST FAILED: calibration eliminated the true hypothesis")
    if len(alive) == 1:
        print("SELFTEST PASSED: converged exactly on the truth")
    else:
        print(f"SELFTEST PASSED: truth retained; {len(alive)} hypotheses still tied "
              "(more/other observations would separate them)")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return
    if len(argv) < 2:
        sys.exit(__doc__)
    path = pathlib.Path(argv[1])
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh)
                if r.get("attacker") and not r["attacker"].lstrip().startswith("#")]
    if not rows:
        sys.exit(f"{path} has no observations yet -- go record some battles.")

    # COUNTERATTACKS DO NOT BELONG IN THIS ELIMINATION.
    #
    # Everything below fits the FIRST-STRIKE formula. The game computes a
    # counter differently -- base * raw internal hp / 100, no display term and
    # no luck (ASSUMPTIONS A9b) -- so a counter row scored here is a row scored
    # with arithmetic that does not apply to it.
    #
    # This is not hypothetical tidiness. Four counter rows were in this corpus,
    # and under the strike formula two of them refuted `ceil` and installed
    # `floor_min1` as "determined" for months. The other 71 rows could not
    # object: every one of them has the attacker at 100 or 9 internal HP, where
    # all four candidate display rules return the same number. So this tool
    # confidently reported a display rule that the corpus had never constrained
    # and that direct measurement later refuted. Dropping them here is the fix.
    counters = [r for r in rows if "counter" in (r.get("notes") or "").lower()]
    rows = [r for r in rows if "counter" not in (r.get("notes") or "").lower()]
    if counters:
        print(f"excluding {len(counters)} counterattack row(s): the game "
              "computes counters\nwith a different formula (A9b), so they "
              "cannot eliminate strike hypotheses.\n")
    observations = [Obs(r, i + 2) for i, r in enumerate(rows)]
    shared = "--shared-luck" in argv
    if shared:
        print("shared-luck mode: requiring ONE roll to explain every observation\n")
    alive = survivors(observations, shared_luck=shared)
    if not alive:
        bad = diagnose(observations, shared)
        if "--explain" in argv:
            print()
            for i in bad:
                explain(observations[i])
                print()
        return
    report(observations, alive)
    if "--suggest" in argv and alive:
        # Rank experiments in whatever mode is actually being recorded --
        # an exact-HP reading is far more informative than a bar count, so
        # ranking in the wrong mode recommends the wrong battles.
        modes = {o.mode for o in observations}
        mode = "exact" if modes == {"exact"} else "display_after"
        suggest(alive, {o.terrain for o in observations}, mode=mode)


if __name__ == "__main__":
    main(sys.argv)

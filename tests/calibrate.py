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
import functools
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.damage import (VARIANTS, LUCK_MIN, LUCK_MAX, display_hp,  # noqa: E402
                           select_weapon, tables)

STAR_RANGE = range(0, 5)          # AW terrain defence is 0..4 stars


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


@functools.lru_cache(maxsize=None)
def _predict(attacker, defender, att_hp, def_hp, mode, co_atk, co_def,
             variant, stars):
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
        raw = fn(w.base, display_hp(att_hp), co_atk, co_def,
                 stars, display_hp(def_hp), luck)
        dmg = max(0, min(raw, def_hp))
        if mode == "exact":
            out.add(dmg)
        elif mode == "display_after":
            out.add(display_hp(max(0, def_hp - dmg)))
        else:
            raise ValueError(f"unknown mode {mode!r}")
    return frozenset(out)


def predict(obs, variant, stars):
    return _predict(obs.attacker, obs.defender, obs.att_hp, obs.def_hp,
                    obs.mode, obs.co_atk, obs.co_def, variant, stars)


def consistent(obs, variant, star_map):
    return obs.observed in predict(obs, variant, star_map[obs.terrain])


def enumerate_hypotheses(terrains):
    terrains = sorted(terrains)
    for variant in VARIANTS:
        for combo in itertools.product(STAR_RANGE, repeat=len(terrains)):
            yield variant, dict(zip(terrains, combo))


def survivors(observations):
    terrains = {o.terrain for o in observations}
    alive = []
    for variant, star_map in enumerate_hypotheses(terrains):
        if all(consistent(o, variant, star_map) for o in observations):
            alive.append((variant, star_map))
    return alive


def report(observations, alive):
    terrains = sorted({o.terrain for o in observations})
    print(f"{len(observations)} observations, {len(terrains)} terrain(s): "
          + ", ".join(terrains))
    total = len(VARIANTS) * 5 ** len(terrains)
    print(f"{len(alive)} of {total} hypotheses survive\n")

    if not alive:
        print("NOTHING survives. Either an observation is mis-recorded, or the")
        print("real formula is not among the variants in engine/damage.py.")
        print("Both are worth knowing; do not 'fix' this by widening the luck range.")
        return

    vs = sorted({v for v, _ in alive})
    print(f"formula variants still possible ({len(vs)}): {', '.join(vs)}")
    for t in terrains:
        vals = sorted({sm[t] for _, sm in alive})
        star = "determined" if len(vals) == 1 else "ambiguous"
        print(f"  {t:10s} stars: {vals}  ({star})")

    if len(alive) == 1:
        v, sm = alive[0]
        print(f"\nCONVERGED: variant={v}  stars={sm}")
        print("Set provenance.verified_against_emulator=true in data/aw1_damage.json")
        print(f"and DEFAULT_VARIANT='{v}' in engine/damage.py.")
    else:
        print("\nNot converged. Run with --suggest for the most informative next test.")


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


def suggest(alive, terrains, top=8):
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
                     "mode": "display_after", "observed": "0"}, 0)
        buckets = {}
        for variant, sm in alive:
            key = frozenset(predict(probe, variant, sm[terr]))
            buckets.setdefault(key, []).append((variant, sm))
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
    truth_stars = {"plains": 1, "woods": 2, "mountain": 4, "road": 0}

    obs = []
    line = 0
    for (att, dfn) in CANDIDATE_ATTACKS:
        if select_weapon(att, dfn) is None:
            continue
        for terr in truth_stars:
            for ahp in (100, 70, 50):
                line += 1
                probe = Obs({"attacker": att, "defender": dfn, "att_hp": str(ahp),
                             "def_hp": "100", "terrain": terr,
                             "mode": "display_after", "observed": "0"}, line)
                outcomes = sorted(predict(probe, truth_variant, truth_stars[terr]))
                probe.observed = rng.choice(outcomes)
                obs.append(probe)

    print(f"selftest: truth variant={truth_variant} stars={truth_stars}")
    print(f"generated {len(obs)} synthetic observations")
    alive = survivors(obs)
    report(obs, alive)
    ok = any(v == truth_variant and sm == truth_stars for v, sm in alive)
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
    observations = [Obs(r, i + 2) for i, r in enumerate(rows)]
    alive = survivors(observations)
    report(observations, alive)
    if "--suggest" in argv and alive:
        suggest(alive, {o.terrain for o in observations})


if __name__ == "__main__":
    main(sys.argv)

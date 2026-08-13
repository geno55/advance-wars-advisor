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


@functools.lru_cache(maxsize=None)
def _predict(attacker, defender, att_hp, def_hp, mode, co_atk, co_def,
             variant, stars, display=None):
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
        raw = fn(w.base, display_hp(att_hp, display), co_atk, co_def,
                 stars, display_hp(def_hp, display), luck)
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


def predict(obs, variant, stars, display=None):
    return _predict(obs.attacker, obs.defender, obs.att_hp, obs.def_hp,
                    obs.mode, obs.co_atk, obs.co_def, variant, stars, display)


@functools.lru_cache(maxsize=None)
def _outcome(attacker, defender, att_hp, def_hp, mode, co_atk, co_def,
             variant, stars, luck, display=None):
    """The single outcome for one specific luck roll."""
    w = select_weapon(attacker, defender)
    if w is None:
        return None
    raw = VARIANTS[variant](w.base, display_hp(att_hp, display), co_atk, co_def,
                            stars, display_hp(def_hp, display), luck)
    dmg = max(0, min(raw, def_hp))
    if mode == "exact":
        return dmg
    # What the PLAYER reports is the on-screen bar count, which rounds up even
    # though the combat maths does not.
    return screen_bars(max(0, def_hp - dmg))


def outcome(obs, variant, stars, luck, display=None):
    return _outcome(obs.attacker, obs.defender, obs.att_hp, obs.def_hp,
                    obs.mode, obs.co_atk, obs.co_def, variant, stars, luck,
                    display)


def consistent(obs, variant, star_map, display=None):
    return obs.observed in predict(obs, variant, star_map[obs.terrain], display)


def enumerate_hypotheses(terrains):
    """A hypothesis is (formula variant, star map, display rule).

    The display rule is a free parameter because assuming it was ceil() produced
    a model the emulator refuted. Nothing about the arithmetic is asserted here;
    the data eliminates.
    """
    terrains = sorted(terrains)
    options = [star_options(t) for t in terrains]
    for variant in VARIANTS:
        for disp in DISPLAY_VARIANTS:
            for combo in itertools.product(*options):
                yield variant, dict(zip(terrains, combo)), disp


def survivors(observations, shared_luck=False):
    """Surviving hypotheses as tuples: (variant, star_map[, luck]).

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
    for variant, star_map, disp in enumerate_hypotheses(terrains):
        if shared_luck:
            for luck in range(LUCK_MIN, LUCK_MAX + 1):
                if all(outcome(o, variant, star_map[o.terrain], luck, disp)
                       == o.observed for o in observations):
                    alive.append((variant, star_map, disp, luck))
        elif all(consistent(o, variant, star_map, disp) for o in observations):
            alive.append((variant, star_map, disp))
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
                        disp = h[2]
                        lucks = ([h[3]] if len(h) > 3
                                 else range(LUCK_MIN, LUCK_MAX + 1))
                        vals = {max(0, min(VARIANTS[h[0]](
                            w.base, display_hp(ahp, disp), 100, 100, stars,
                            display_hp(dhp, disp), lk), dhp)) for lk in lucks}
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

    vs = sorted({h[0] for h in alive})
    ds = sorted({h[2] for h in alive})
    print(f"formula variants still possible ({len(vs)}): {', '.join(vs)}")
    print(f"display rule ({len(ds)}): {', '.join(ds)}"
          + ("  (determined)" if len(ds) == 1 else ""))
    for t in terrains:
        vals = sorted({h[1][t] for h in alive})
        star = "determined" if len(vals) == 1 else "ambiguous"
        print(f"  {t:10s} stars: {vals}  ({star})")
    if len(alive[0]) > 3:
        lucks = sorted({h[3] for h in alive})
        print(f"  shared luck roll: {lucks}"
              + ("  (determined)" if len(lucks) == 1 else ""))

    print("\n" + describe_agreement(alive))

    stars_pinned = len({tuple(sorted(h[1].items())) for h in alive}) == 1
    print(f"terrain stars fully determined: {stars_pinned}")

    _, dmg, kill, _ = agreement(alive)
    if stars_pinned and kill == 0 and dmg == 0:
        v, dp = alive[0][0], alive[0][2]
        print("\nDONE. Set provenance.verified_against_emulator=true in "
              "data/aw1_damage.json,")
        print(f"DEFAULT_VARIANT='{v}' and DEFAULT_DISPLAY='{dp}' in "
              "engine/damage.py")
        print("(any survivor will do -- they are equivalent).")
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
            key = frozenset(predict(probe, h[0], h[1][terr], h[2]))
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
    truth_display = "floor"
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
                outcomes = sorted(predict(probe, truth_variant,
                                          truth_stars[terr], truth_display))
                probe.observed = rng.choice(outcomes)
                obs.append(probe)

    print(f"selftest: truth variant={truth_variant} display={truth_display} "
          f"stars={truth_stars}")
    print(f"generated {len(obs)} synthetic observations")
    alive = survivors(obs)
    report(obs, alive)
    ok = any(h[0] == truth_variant and h[1] == truth_stars
             and h[2] == truth_display for h in alive)
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

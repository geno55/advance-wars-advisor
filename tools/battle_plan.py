"""Produce an ordered battle list to pin down the damage formula, and simulate
whether it actually converges.

Two parts:

1. RANK. Score every candidate battle by expected surviving hypotheses,
      E[survivors] = sum_v |S(v)|^2 / sum_v |S(v)|,   S(v) = {h : v possible under h}
   Minimax was tried first and is useless here: outcome sets overlap enough that
   some value is consistent with every hypothesis, so the worst case never
   improves and the search stalls. Expected-case reflects what actually happens.

2. SIMULATE. Pick a truth, freeze the luck roll (the pessimistic case if save
   states restore the RNG), run the ranked list, and count survivors. This is the
   honest feasibility check: it says how many battles are really needed, rather
   than asserting a number.

Full-health attackers only. A damaged unit sits at an internal HP the player
cannot read (7 bars is anywhere in 61..70), so planning around one means
planning around data that cannot be recorded.
"""
import itertools
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tests"))

from calibrate import Obs, predict  # noqa: E402
from engine.damage import (VARIANTS, display_hp, select_weapon)  # noqa: E402

TERRAINS = ["road", "plains", "woods", "mountain"]

MATCHUPS = [
    ("Infantry", "Infantry"), ("Infantry", "Mech"), ("Mech", "Infantry"),
    ("Tank", "Infantry"), ("Tank", "Mech"), ("Tank", "Recon"), ("Tank", "Tank"),
    ("Recon", "Infantry"), ("MdTank", "Tank"), ("MdTank", "Infantry"),
    ("Artillery", "Tank"), ("Rockets", "Infantry"), ("AntiAir", "Infantry"),
]


def probe(att, dfn, terr):
    return Obs({"attacker": att, "defender": dfn, "att_hp": "100",
                "def_hp": "100", "terrain": terr, "mode": "display_after",
                "observed": "0"}, 0)


def partition(cand, hyps):
    att, dfn, terr = cand
    p = probe(att, dfn, terr)
    by = {}
    for h in hyps:
        variant, stars = h
        for v in predict(p, variant, stars[terr]):
            by.setdefault(v, []).append(h)
    return by


def expected_survivors(by):
    tot = sum(len(x) for x in by.values())
    if not tot:
        return float("inf")
    return sum(len(x) ** 2 for x in by.values()) / tot


def outcome_under(cand, truth, luck):
    """What the player would actually see, for a given truth and luck roll."""
    att, dfn, terr = cand
    variant, stars = truth
    w = select_weapon(att, dfn)
    raw = VARIANTS[variant](w.base, 10, 100, 100, stars[terr], 10, luck)
    return display_hp(max(0, 100 - max(0, min(raw, 100))))


def main():
    hyps = [(v, dict(zip(TERRAINS, combo)))
            for v in VARIANTS
            for combo in itertools.product(range(5), repeat=len(TERRAINS))]
    cands = [(a, d, t) for (a, d) in MATCHUPS for t in TERRAINS
             if select_weapon(a, d) is not None]

    ranked = sorted(cands, key=lambda c: expected_survivors(partition(c, hyps)))

    # Keep the list diverse: at most two battles per matchup, three per terrain,
    # so a single lucky matchup does not dominate and terrain stars all get hit.
    plan, per_match, per_terr = [], {}, {}
    for c in ranked:
        a, d, t = c
        if per_match.get((a, d), 0) >= 2 or per_terr.get(t, 0) >= 4:
            continue
        plan.append(c)
        per_match[(a, d)] = per_match.get((a, d), 0) + 1
        per_terr[t] = per_terr.get(t, 0) + 1
        if len(plan) >= 16:
            break

    print(f"start: {len(hyps)} hypotheses "
          f"({len(VARIANTS)} formulas x 5^{len(TERRAINS)} star maps)\n")
    print("THE BATTLE LIST (full health both sides, neutral CO both sides)\n")
    for i, (a, d, t) in enumerate(plan, 1):
        by = partition((a, d, t), hyps)
        o = sorted(by)
        print(f"{i:2d}. {a:9s} -> {d:9s} on {t:8s}   expect {min(o)}-{max(o)} bars left")

    # Feasibility, luck FROZEN. Compare treating each observation independently
    # ("some roll explains this") against solving for one shared roll.
    from calibrate import outcome as outcome_at  # noqa: E402

    shared_hyps = [(v, sm, L) for (v, sm) in hyps for L in range(10)]
    std = {"road": 0, "plains": 1, "woods": 2, "mountain": 4}

    print("\nsimulated convergence with the luck roll FROZEN")
    print("(the case where a save state restores the RNG)\n")
    print(f"  {'truth formula':24s} {'independent':>14s} {'shared-luck':>14s}")
    worst_ind = worst_shared = 0
    for variant in VARIANTS:
        for luck in (0, 5, 9):
            truth = (variant, std)
            ind, shared = hyps, shared_hyps
            n_ind = n_shared = None
            for i, c in enumerate(plan, 1):
                v = outcome_under(c, truth, luck)
                p = probe(*c)
                ind = [h for h in ind if v in predict(p, h[0], h[1][c[2]])]
                shared = [h for h in shared
                          if outcome_at(p, h[0], h[1][c[2]], h[2]) == v]
                if n_ind is None and len(ind) == 1:
                    n_ind = i
                if n_shared is None and len(shared) == 1:
                    n_shared = i
            a = f"{n_ind}" if n_ind else f">{len(plan)} ({len(ind)} left)"
            b = f"{n_shared}" if n_shared else f">{len(plan)} ({len(shared)} left)"
            worst_ind = max(worst_ind, n_ind or 99)
            worst_shared = max(worst_shared, n_shared or 99)
            if luck == 5:
                print(f"  {variant:24s} {a:>14s} {b:>14s}")
    print(f"\n  worst case over all formulas and frozen rolls:")
    print(f"    independent : {'never' if worst_ind >= 99 else worst_ind} battles")
    print(f"    shared-luck : {'never' if worst_shared >= 99 else worst_shared} battles")
    print("\nrecord.py prints CONVERGED the moment it happens -- stop early if so.")


if __name__ == "__main__":
    main()

"""Recover the luck distribution from repeated trials of one fixed matchup.

Everything so far assumed the roll is uniform over 0..9. That assumption has
never been tested, and it is load-bearing: the 357:1 evidence favouring
luck_after_hp rests entirely on it.

Give this the per-trial damage values for a single repeated matchup and it
inverts each one into the roll(s) that could have produced it, then reports
whether the resulting distribution looks uniform -- and which variants can
explain the observed set at all.

    python tools/luck_fit.py Tank Infantry mountain 100 100 47 48 46 49 ...
"""
import collections
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from engine.damage import (DEFAULT_DISPLAY, DISPLAY_VARIANTS,  # noqa: E402
                           VARIANTS, select_weapon)
import json  # noqa: E402

STARS = json.loads((HERE.parent / "data" / "aw1_terrain.json")
                   .read_text(encoding="utf-8"))["stars"]


def damage_by_roll(variant, display, att, dfn, ahp, dhp, stars):
    w = select_weapon(att, dfn)
    disp = DISPLAY_VARIANTS[display]
    return [max(0, min(VARIANTS[variant](w.base, disp(ahp), 100, 100, stars,
                                         disp(dhp), lk), dhp)) for lk in range(10)]


def main(argv):
    att, dfn, terr = argv[1], argv[2], argv[3].lower()
    ahp, dhp = int(argv[4]), int(argv[5])
    obs = [int(x) for x in argv[6:]]
    if terr not in STARS:
        sys.exit(f"terrain {terr!r} not in data/aw1_terrain.json -- read its Def")
    stars = STARS[terr]

    print(f"{att}@{ahp} -> {dfn}@{dhp} on {terr} ({stars} stars), "
          f"{len(obs)} trials, display={DEFAULT_DISPLAY}\n")
    seen = collections.Counter(obs)
    print("observed damage: " + ", ".join(f"{d}x{n}" for d, n in sorted(seen.items())))

    for variant in VARIANTS:
        table = damage_by_roll(variant, DEFAULT_DISPLAY, att, dfn, ahp, dhp, stars)
        reachable = set(table)
        impossible = [d for d in seen if d not in reachable]
        if impossible:
            print(f"\n{variant:15s} REFUTED -- cannot produce {sorted(set(impossible))}")
            continue
        # Invert: which rolls could each observation have come from?
        rolls = collections.Counter()
        for d, n in seen.items():
            cands = [lk for lk, x in enumerate(table) if x == d]
            for lk in cands:                       # split evenly when ambiguous
                rolls[lk] += n / len(cands)
        never = [lk for lk in range(10) if rolls[lk] == 0]
        exp = len(obs) / 10
        print(f"\n{variant:15s} damage by roll: {table}")
        print(f"{'':15s} implied roll counts (expected {exp:.1f} each):")
        print(f"{'':15s} " + "  ".join(f"{lk}:{rolls[lk]:.1f}" for lk in range(10)))
        if never:
            # Probability of missing exactly these rolls under a uniform model.
            p = ((10 - len(never)) / 10) ** len(obs)
            seen_rolls = sorted(lk for lk in range(10) if rolls[lk] > 0)
            contiguous = (seen_rolls ==
                          list(range(seen_rolls[0], seen_rolls[-1] + 1)))
            print(f"{'':15s} rolls NEVER seen: {never}   "
                  f"P(that under a uniform roll) = {p:.4f}")
            if contiguous and len(seen_rolls) < 10:
                print(f"{'':15s} -> observed rolls {seen_rolls[0]}-{seen_rolls[-1]} "
                      "form a CONTIGUOUS BAND.")
            elif p < 0.01:
                print(f"{'':15s} -> scattered gaps: suspect the MODEL, not sampling")
        else:
            print(f"{'':15s} every roll 0-9 observed; consistent with uniform")


EPILOGUE = """
Reading the result
------------------
CONTIGUOUS BAND under every variant means the model is fine and the SAMPLING is
clustered. The GBA advances its RNG as frames pass, so if you reload a save
state and confirm the attack after roughly the same delay each time, you sample
a narrow window of the sequence rather than the whole range. Twenty trials at a
consistent tempo can easily be twenty draws from five adjacent rolls.

This matters: any inference of the form "we never saw a high roll, therefore the
high-roll variant is wrong" is INVALID under clustered sampling. It assumes
every roll had a fair chance of appearing.

To sample properly, vary the delay ON PURPOSE between loading and confirming:
wait a different count each time, or move the cursor a varying number of tiles
first. You want the observed band to widen, ideally covering 0-9.

SCATTERED gaps are the opposite diagnosis: rolls missing from the middle of an
otherwise-covered range point at the model, not the sampling.
"""

if __name__ == "__main__":
    if len(sys.argv) < 7:
        sys.exit(__doc__)
    main(sys.argv)
    print(EPILOGUE)

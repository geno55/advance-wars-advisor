"""Which battles are actually worth recording?

A matchup only teaches us something if the surviving hypotheses disagree about
its outcome. Here a hypothesis is (formula variant, terrain stars) -- 6 x 5 = 30
of them for a single terrain. Rank candidate battles by how finely they split
that set, and prefer ones that do not one-shot the defender (an observed 0 tells
you almost nothing).
"""
import itertools
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.damage import VARIANTS, display_hp, select_weapon  # noqa: E402

PAIRS = [
    ("Infantry", "Infantry"), ("Infantry", "Mech"), ("Mech", "Infantry"),
    ("Tank", "Infantry"), ("Tank", "Tank"), ("Tank", "Recon"),
    ("Recon", "Infantry"), ("Artillery", "Tank"), ("MdTank", "Tank"),
    ("Rockets", "Infantry"), ("AntiAir", "Infantry"), ("Mech", "Tank"),
    ("BCopter", "Tank"), ("APC", "Infantry"),
]


def outcomes(att, dfn, att_hp, stars, variant, def_hp=100):
    w = select_weapon(att, dfn)
    if w is None:
        return None
    fn = VARIANTS[variant]
    out = set()
    for luck in range(10):
        raw = fn(w.base, display_hp(att_hp), 100, 100, stars, display_hp(def_hp), luck)
        out.add(display_hp(max(0, def_hp - max(0, min(raw, def_hp)))))
    return frozenset(out)


rows = []
for (att, dfn), att_hp in itertools.product(PAIRS, (100, 70)):
    if select_weapon(att, dfn) is None:
        continue
    classes = {}
    overkill = False
    for variant, stars in itertools.product(VARIANTS, range(5)):
        o = outcomes(att, dfn, att_hp, stars, variant)
        classes.setdefault(o, []).append((variant, stars))
        if o == frozenset({0}):
            overkill = True
    worst = max(len(v) for v in classes.values())
    rows.append((worst, -len(classes), overkill, att, dfn, att_hp, len(classes)))

rows.sort(key=lambda r: (r[2], r[0], r[1]))
print("Best battles to record (defender at full health, 30 hypotheses to split)\n")
print(f"{'attacker':10s} {'defender':10s} {'att HP':>7s} {'groups':>7s} {'worst case':>11s}")
for worst, _, overkill, att, dfn, hp, ngroups in rows[:14]:
    flag = "  <- always kills, low value" if overkill else ""
    print(f"{att:10s} {dfn:10s} {display_hp(hp):>6d}  {ngroups:>7d} {worst:>11d}{flag}")

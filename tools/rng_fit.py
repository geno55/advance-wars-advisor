"""Recover how the luck roll is drawn, then settle the damage formula.

    python tools/rng_fit.py C:/tmp/seedsweep.json

Given a seed sweep -- each case is the same attack replayed with the RNG state
written to a known value -- this searches for the (consumption depth, modulus)
that makes the observed damage a consistent function of the roll, and then
reports which formula variants survive.

Nothing about the draw is assumed. We know the generator exactly, from the
disassembly at 0x083D69D4:

    state = (state * 20077 + 12345) mod 2^32
    output = (state >> 16) & 0x7FFF

but NOT how many times it is called between our write and the luck roll, nor
what the roll is reduced by. Both are searched over, and a fit only counts if
it is a well-defined function: the same roll must never yield two different
damages across the sweep.

WHY THIS BEATS THE FRAME-DELAY SWEEP. That one could only argue from absence --
"we never saw 51, so luck_last is out" -- which is precisely the inference
ASSUMPTIONS.md A4 records being withdrawn once already, and the structural gap
at damage 47 showed the sampling was still not uniform. With the seed written,
the roll is an input. A variant is refuted because it predicts the wrong damage
for a KNOWN roll, which needs no distributional assumption at all.
"""
import argparse
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "engine"))

import damage as dmg                                        # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
MASK = 0xFFFFFFFF


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def outputs(seed, mul, add, depth):
    """The generator's outputs for the first `depth` calls after `seed`."""
    s, out = seed, []
    for _ in range(depth):
        s = (s * mul + add) & MASK
        out.append((s >> 16) & 0x7FFF)
    return out


def distribution_test(live, w, a_disp, d_disp, stars, co_atk, co_def):
    """Compare the observed damage histogram against each variant's shape.

    This works without knowing the generator, and -- crucially -- it is not an
    argument from absence. Each variant predicts a specific SHAPE: under
    luck_after_hp several rolls collapse onto the same damage, so some values
    occur twice as often as others, while luck_last maps ten rolls onto ten
    distinct damages and is therefore flat. Observing the 2:1 pattern is
    positive evidence for one and against the other.

    The uniformity this rests on is a property of the SEEDS WE CHOSE, spread
    across the 32-bit space, not an assumption about how the game samples. That
    is exactly what the withdrawn 357:1 inference lacked.
    """
    from collections import Counter
    obs = Counter(c["damage"] for c in live)
    n = sum(obs.values())
    print(f"  observed over {n} seeds: {dict(sorted(obs.items()))}")
    print(f"\n  {'variant':<24}{'chi2/df':>9}   verdict")
    scored = []
    for name, fn in dmg.VARIANTS.items():
        m = Counter(fn(w.base, a_disp, co_atk, co_def, stars, d_disp, L)
                    for L in range(10))
        exp = {d: n * c / 10 for d, c in m.items()}
        impossible = sorted(set(obs) - set(exp))
        if impossible:
            print(f"  {name:<24}{'--':>9}   REFUTED: cannot produce {impossible}")
            continue
        chi = sum((obs.get(d, 0) - e) ** 2 / e for d, e in exp.items())
        df = max(1, len(exp) - 1)
        never = sorted(d for d in exp if obs.get(d, 0) == 0)
        note = f"predicts {never}, never seen in {n} seeds" if never else "fits"
        print(f"  {name:<24}{chi / df:9.2f}   {note}")
        scored.append((chi / df, name, never))
    if not scored:
        print("\n  every variant refuted -- the model is wrong somewhere.")
        return 1
    scored.sort()
    best = scored[0][0]
    winners = [nm for s, nm, _ in scored if s < best * 3 + 1]
    print(f"\n  best shape: {', '.join(winners)}  (chi2/df {best:.2f})")
    losers = [(nm, nv, s) for s, nm, nv in scored if nm not in winners]
    for nm, nv, s in losers:
        extra = f", and predicts {nv} which never appeared" if nv else ""
        print(f"  {nm} is refuted on shape (chi2/df {s:.2f}){extra}")
    print("\n  Variants that coincide on THIS matchup cannot be separated here;")
    print("  cross this against tests/calibrate.py over the full corpus.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sweep_json")
    ap.add_argument("--max-depth", type=int, default=12)
    ap.add_argument("--co-attack", type=int, default=100)
    ap.add_argument("--co-defense", type=int, default=100)
    args = ap.parse_args()

    s = json.loads(pathlib.Path(args.sweep_json).read_text(encoding="utf-8"))
    if s.get("mode") != "seed":
        sys.exit("this is not a seed sweep -- run dmg_seedsweep, not dmg_sweep")
    mul, add = s["rng_mul"], s["rng_add"]

    names = {u["id"] + 1: n for n, u in load("aw1_unit_stats.json")["units"].items()}
    att, dfn = names.get(s["attacker_type"]), names.get(s["defender_type"])
    terr = load("aw1_terrain.json")["terrain"][str(s["defender_terrain"])]
    stars = load("aw1_terrain.json")["stars"][terr["name"].lower()]

    live = [c for c in s["cases"] if not c["destroyed"]]
    print(f"{att} (hp {s['attacker_hp']}) -> {dfn} (hp {s['defender_hp']}) "
          f"on {terr['name']} ({stars} stars)")
    print(f"  {len(live)} usable case(s) of {len(s['cases'])}, "
          f"{len(set(c['damage'] for c in live))} distinct damage value(s)")
    if len(live) < 10:
        sys.exit("too few usable cases to fit anything")

    w = dmg.select_weapon(att, dfn, s["attacker_ammo"])
    if w is None:
        sys.exit(f"{att} cannot attack {dfn} -- check the slots")
    a_disp = dmg.display_hp(s["attacker_hp"])
    d_disp = dmg.display_hp(s["defender_hp"])

    # -- search for (depth, modulus) making damage a function of the roll ---
    fits = []
    for depth in range(1, args.max_depth + 1):
        for mod in (10, 16, 100):
            table = defaultdict(set)
            for c in live:
                roll = outputs(c["seed"], mul, add, depth)[-1] % mod
                table[roll].add(c["damage"])
            if all(len(v) == 1 for v in table.values()):
                fits.append((depth, mod, {k: v.pop() for k, v in table.items()}))

    if not fits:
        print("\n  no (depth, modulus) makes damage a function of the roll --")
        print("  expected when the generator's algorithm is unknown, as it is for")
        print("  the combat state at 0x03001D30. Falling back to the DISTRIBUTION")
        print("  test, which needs no model of the generator at all.\n")
        return distribution_test(live, w, a_disp, d_disp, stars,
                                 args.co_attack, args.co_defense)

    print(f"\n  {len(fits)} consistent (depth, modulus) fit(s):")
    for depth, mod, table in fits:
        print(f"    depth {depth}, mod {mod}: {len(table)} distinct roll(s) seen, "
              f"damage {min(table.values())}..{max(table.values())}")

    depth, mod, table = fits[0]
    print(f"\n  using depth {depth}, mod {mod}")
    print("  roll -> damage: " + "  ".join(f"{k}:{v}" for k, v in sorted(table.items())))

    # -- now every variant is testable at a KNOWN roll --------------------
    print(f"\n  base {w.base} ({w.slot}), attacker display HP {a_disp}, "
          f"defender display HP {d_disp}")
    print("\n  variant                 verdict")
    survivors = []
    for name, fn in dmg.VARIANTS.items():
        bad = [(roll, obs, fn(w.base, a_disp, args.co_attack, args.co_defense,
                              stars, d_disp, roll))
               for roll, obs in sorted(table.items())
               if fn(w.base, a_disp, args.co_attack, args.co_defense,
                     stars, d_disp, roll) != obs]
        if bad:
            r, obs, pred = bad[0]
            print(f"  {name:22s}  REFUTED at roll {r}: observed {obs}, "
                  f"predicts {pred} ({len(bad)} mismatch(es))")
        else:
            survivors.append(name)
            print(f"  {name:22s}  consistent at every observed roll")

    print(f"\n  {len(survivors)} variant(s) survive: {', '.join(survivors) or 'NONE'}")
    if len(survivors) == 1:
        print(f"  The damage formula is {survivors[0]}. This rests on predicted-"
              "vs-observed at known rolls, not on any value failing to appear.")
    elif not survivors:
        print("  NONE surviving means the fit is wrong or the model is. Check the "
              "CO was neutral and the terrain is what the sweep recorded.")
        return 1
    else:
        unseen = sorted(set(range(mod)) - set(table))
        print(f"  Rolls never drawn: {unseen}. Seeds hitting those would "
              "discriminate further; widen the sweep.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

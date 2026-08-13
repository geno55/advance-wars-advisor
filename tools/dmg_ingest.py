"""Turn a damage sweep into observations, and say which variants survive.

    python tools/dmg_ingest.py C:/tmp/dmg.json
    python tools/dmg_ingest.py C:/tmp/dmg.json --append

The sweep varies only how many frames pass before the attack is confirmed, so
every case is the same matchup sampled at a different point in the RNG
sequence. That is the experiment ASSUMPTIONS.md A4 asks for, and the thing 20
hand-recorded trials could not deliver.

This deliberately does NOT introduce a second analysis path. It emits rows in
harness/observations.csv format and reports what the existing hypothesis set
says; `--append` writes them so tests/calibrate.py can do the real elimination
over the full corpus.

TWO THINGS THAT INVALIDATE A SWEEP, both the operator's responsibility:

  * A non-neutral CO. Modifiers are real and large -- the ROM holds records at
    150/100 and 170/100 -- and they silently scale everything. Every existing
    observation was recorded with a plain CO and no power active.
  * A defender that dies. Damage is then only a lower bound, so those cases are
    excluded here rather than recorded as exact.
"""
import argparse
import json
import pathlib
import sys
from fractions import Fraction

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "engine"))

import damage as dmg                                        # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
OBS = pathlib.Path(__file__).resolve().parent.parent / "harness" / "observations.csv"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def type_names():
    units = load("aw1_unit_stats.json")["units"]
    return {u["id"] + 1: n for n, u in units.items()}


def terrain_name(tid):
    t = load("aw1_terrain.json")["terrain"].get(str(tid))
    if t is None:
        sys.exit(f"unknown terrain id {tid}")
    return t["name"].lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sweep_json")
    ap.add_argument("--append", action="store_true",
                    help="append the rows to harness/observations.csv")
    ap.add_argument("--co-attack", type=int, default=100)
    ap.add_argument("--co-defense", type=int, default=100)
    args = ap.parse_args()

    s = json.loads(pathlib.Path(args.sweep_json).read_text(encoding="utf-8"))
    names = type_names()
    att = names.get(s["attacker_type"])
    dfn = names.get(s["defender_type"])
    if att is None or dfn is None:
        sys.exit(f"unrecognised unit type id in the sweep "
                 f"({s['attacker_type']}, {s['defender_type']})")
    terr = terrain_name(s["defender_terrain"])
    att_hp, def_hp = s["attacker_hp"], s["defender_hp"]

    live = [c for c in s["cases"] if not c["destroyed"]]
    dead = len(s["cases"]) - len(live)
    observed = sorted({c["damage"] for c in live})
    print(f"{att} (hp {att_hp}) -> {dfn} (hp {def_hp}) on {terr}")
    print(f"  {len(s['cases'])} case(s), {dead} excluded for destroying the "
          f"defender, {len(observed)} distinct damage value(s): {observed}")
    if not observed:
        sys.exit("nothing usable -- every case destroyed the defender; "
                 "repeat with a healthier defender or a weaker attacker")

    spread = observed[-1] - observed[0]
    print(f"  spread {spread}  (a wider spread discriminates harder; "
          f"a spread of 0 means the sweep did not move the roll at all)")

    # -- what the surviving variants predict -----------------------------
    w = dmg.select_weapon(att, dfn, s["attacker_ammo"])
    if w is None:
        sys.exit(f"{att} cannot attack {dfn} -- check the slots")
    stars = load("aw1_terrain.json")["stars"][terr]
    a_disp = dmg.display_hp(att_hp)
    d_disp = dmg.display_hp(def_hp)
    print(f"  base {w.base} ({w.slot}), attacker display HP {a_disp}, "
          f"defender display HP {d_disp}, {stars} star(s)")

    print("\n  variant                 predicted range   consistent?")
    survivors = []
    for name, fn in dmg.VARIANTS.items():
        vals = [fn(w.base, a_disp, args.co_attack, args.co_defense,
                   stars, d_disp, luck) for luck in range(10)]
        lo, hi = min(vals), max(vals)
        ok = all(v in vals for v in observed)
        if ok:
            survivors.append(name)
        print(f"  {name:22s}  {lo:3d}..{hi:<3d}          "
              f"{'yes' if ok else 'NO  (cannot produce ' + str(sorted(set(observed) - set(vals))) + ')'}")

    print(f"\n  {len(survivors)} of {len(dmg.VARIANTS)} variant(s) consistent "
          f"with this sweep alone: {', '.join(survivors) or 'NONE'}")
    if not survivors:
        print("  NONE surviving means the model is wrong somewhere, or the CO "
              "was not neutral, or the terrain was misread. Do not append this.")
        return 1
    if len(survivors) > 1:
        unreached = {}
        for name in survivors:
            fn = dmg.VARIANTS[name]
            vals = {fn(w.base, a_disp, args.co_attack, args.co_defense,
                       stars, d_disp, luck) for luck in range(10)}
            unreached[name] = sorted(vals - set(observed))
        print("  values each survivor predicts but the sweep never produced:")
        for name, miss in unreached.items():
            print(f"    {name:22s} {miss}")
        print("  Seeing any of those would eliminate the others. If a wide "
              "sweep keeps missing them, that is evidence too -- but only "
              "because the delay was varied deliberately, which is exactly "
              "what A4 says the old 20 trials lacked.")

    rows = [f"{att},{dfn},{att_hp},{def_hp},{terr},exact,{d},"
            f"{args.co_attack},{args.co_defense},"
            f"automated sweep k={c['k']}"
            for c, d in ((c, c["damage"]) for c in live)]
    print(f"\n  {len(rows)} observation row(s) ready")
    if args.append:
        with OBS.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(r + "\n")
        print(f"  appended to {OBS}")
        print("  now run: python tests/calibrate.py harness/observations.csv")
    else:
        print("  re-run with --append to add them, then run calibrate.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())

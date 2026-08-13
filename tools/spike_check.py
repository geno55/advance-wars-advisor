"""Check the written-state spike against engine/pathing.py.

This answers the question the spike exists to answer: does a game state produced
by WRITING RAM behave like one produced by playing to it? If it does, test cases
stop costing a human an emulator session each and the automated tier becomes
possible. If it does not, every number the harness generates is measuring the
harness.

    python tools/spike_check.py C:/tmp/spike.json

A pass means: for every unit type written into the same tile, the game's own
movement flood fill matches what pathing.py computes, tile for tile.

WHAT A PASS DOES NOT PROVE. That written state equals played state -- only that
written state is SELF-consistent with our model. The two can agree and both be
wrong about what the game does when you actually play there. To close that, run
one case both ways: play to a real position with unit X, dump it, then write
unit X into a fixture at the same tile and compare. `--baseline` does that
comparison when you have a hand-made state.json of the same situation.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "engine"))

import pathing                                              # noqa: E402
from state import Board, Unit                               # noqa: E402

UNREACHABLE = 255
DATA = pathlib.Path(__file__).resolve().parent.parent / "data"


def type_names():
    """RAM type id (1-based) -> name, from the extracted unit table."""
    units = json.loads((DATA / "aw1_unit_stats.json").read_text(encoding="utf-8"))["units"]
    return {u["id"] + 1: n for n, u in units.items()}


def build_board(blob, case, names):
    """The fixture's board, with the swept unit substituted into its slot."""
    rows = sorted(blob["terrain"], key=lambda r: r["y"])
    units = []
    for u in blob["fixture_units"]:
        name = names.get(u["type_id"])
        if name is None:
            continue
        if u["slot"] == blob["slot"]:
            units.append(Unit(u["slot"], u["player"], case["type"], case["x"],
                              case["y"], case["hp"], case["ammo"], 0,
                              case["fuel"], False, False, False, 0, 0))
        else:
            units.append(Unit(u["slot"], u["player"], name, u["x"], u["y"],
                              u["hp"], u["ammo"], 0, u["fuel"],
                              False, False, False, 0, 0))
    return Board(width=blob["width"], height=blob["height"], units=units,
                 armies=[], terrain=[r["t"] for r in rows],
                 owner=[r["owner"] for r in rows], weather_index=0)


def game_set(grid):
    return {(x, y) for y, row in enumerate(grid)
            for x, v in enumerate(row) if v != UNREACHABLE}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spike_json")
    ap.add_argument("--baseline", help="a hand-played state.json to compare against")
    args = ap.parse_args()

    blob = json.loads(pathlib.Path(args.spike_json).read_text(encoding="utf-8"))
    names = type_names()
    print(f"{blob['width']}x{blob['height']} fixture, slot {blob['slot']}, "
          f"{len(blob['cases'])} case(s)")

    # The control case is the fixture with NOTHING written, so its grid is what
    # simply playing produces. Comparing it to the written case of the same type
    # is the only thing here that tests write-vs-play; everything else tests
    # written-vs-our-model, which could agree while both are wrong.
    control = next((c for c in blob["cases"] if c.get("control")), None)
    if control is not None:
        twin = next((c for c in blob["cases"]
                     if not c.get("control") and c["type_id"] == control["type_id"]),
                    None)
        name = names.get(control["type_id"], f"type {control['type_id']}")
        if twin is None:
            print(f"control holds {name}, which the sweep does not cover -- "
                  "no write-vs-play comparison possible")
        else:
            same_set = game_set(control["grid"]) == game_set(twin["grid"])
            same_grid = control["grid"] == twin["grid"]
            print(f"write-vs-play [{name}]: "
                  f"reachable sets {'MATCH' if same_set else 'DIFFER'}, "
                  f"full cost grids {'match' if same_grid else 'differ'}")
            if not same_set:
                print("  Writing RAM does NOT reproduce the played state. "
                      "Do not build the automated tier on this.")
                return 1
            if not same_grid:
                print("  Sets agree but costs differ -- fine if the control's "
                      "fuel differs from what the sweep wrote, suspicious "
                      "otherwise.")
        print()

    failures, checked = [], 0
    for case in blob["cases"]:
        if case.get("control"):
            continue
        board = build_board(blob, case, names)
        unit = next(u for u in board.units if u.slot == blob["slot"])
        theirs = game_set(case["grid"])
        ours = set(pathing.reachable(board, unit))
        checked += 1
        ok = theirs == ours
        flag = "ok " if ok else "FAIL"
        print(f"  {flag} {case['type']:<11} game {len(theirs):3d}  "
              f"ours {len(ours):3d}  move {pathing.allowance(unit)}")
        if not ok:
            failures.append((case["type"], sorted(theirs - ours),
                             sorted(ours - theirs)))

    for name, missing, extra in failures:
        print(f"\n  {name}: {len(missing)} game-only, {len(extra)} ours-only")
        for t in missing[:8]:
            print(f"    game-only {t}")
        for t in extra[:8]:
            print(f"    ours-only {t}")

    print(f"\n{checked - len(failures)}/{checked} cases match")

    if args.baseline:
        base = json.loads(pathlib.Path(args.baseline).read_text(encoding="utf-8"))
        bgrid = {r["y"]: r["v"] for r in base.get("move_grid", [])}
        if not bgrid:
            print("baseline has no move_grid; dump it with a unit selected")
            return 1
        btiles = game_set([bgrid[y] for y in sorted(bgrid)])
        match = [c for c in blob["cases"] if game_set(c["grid"]) == btiles]
        print(f"\nbaseline (hand-played) marks {len(btiles)} tile(s); "
              f"{len(match)} written case(s) reproduce it exactly"
              + (f": {', '.join(c['type'] for c in match)}" if match else ""))
        if not match:
            print("  NO written case reproduces the played state. Writing RAM "
                  "does not stand in for playing -- do not build on this.")
            return 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

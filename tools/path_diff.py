"""Diff our movement search against the game's own flood fill.

This is milestone 3's version of the damage calibration harness: the emulator
is the oracle, and our model has to match it rather than merely look reasonable.

The game paints a unit's reachable set into the rows reached through the table
at 0x03003600, and mgba_state.lua now dumps it as `move_grid`. That grid is
only meaningful WHILE A UNIT IS SELECTED and its blue range is on screen.

WHAT THE GRID ACTUALLY IS, measured rather than assumed. It is the set of tiles
the unit can move THROUGH -- `reachable()` -- and NOT the set it may stop on.
The game paints tiles occupied by friendly units and then refuses to let you
end there, which is confirmed both by the grid matching `reachable()` exactly
on a live board (19/19) and by the game rejecting those moves when tried.

So the primary comparison below is against `reachable()`. `destinations()` is
strictly narrower and this oracle CANNOT validate it -- the game does not write
that set anywhere we have found. It is reported for information, never scored.

Grid values are the movement cost SPENT to reach the tile, not the movement
remaining. 255 is unreachable.

    (in mGBA, select a unit so its movement range is showing)
    state("C:/tmp/state.json")

    python tools/path_diff.py C:/tmp/state.json

With no --unit, it tries every non-acted unit of the active player and reports
which one the grid matches -- because the dump does not record where the cursor
was, and the matching unit identifies itself.

What the non-255 values MEAN is deliberately not assumed here. The tool reports
the value distribution alongside our own costs, so the relationship (remaining
movement? cost spent? something else?) can be read off real data rather than
guessed and then defended.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "engine"))

import pathing                                              # noqa: E402
import state as state_mod                                   # noqa: E402

UNREACHABLE = 255


def grid_of(raw):
    rows = raw.get("move_grid")
    if not rows:
        sys.exit("this dump has no move_grid -- re-dump with the current "
                 "mgba_state.lua, with a unit selected")
    return [r["v"] for r in sorted(rows, key=lambda r: r["y"])]


def game_reachable(grid):
    return {(x, y) for y, row in enumerate(grid)
            for x, v in enumerate(row) if v != UNREACHABLE}


def compare(board, unit, grid):
    ours_through = set(pathing.reachable(board, unit))
    ours_end = set(pathing.destinations(board, unit))
    theirs = game_reachable(grid)
    return {
        "unit": unit,
        "theirs": theirs,
        "through": ours_through,
        "end": ours_end,
        "missing_vs_through": theirs - ours_through,
        "extra_vs_through": ours_through - theirs,
        "missing_vs_end": theirs - ours_end,
        "extra_vs_end": ours_end - theirs,
    }


def score(c):
    """Scored against the pass-through set, which is what the grid holds."""
    return len(c["missing_vs_through"]) + len(c["extra_vs_through"])


def render(board, unit, grid, c):
    out = []
    for y in range(board.height):
        row = []
        for x in range(board.width):
            t = (x, y)
            g, thru, end = t in c["theirs"], t in c["through"], t in c["end"]
            row.append("##" if t == (unit.x, unit.y) else
                       ".." if not g and not thru else
                       "GG" if g and not thru else
                       "oo" if thru and not g else
                       "++" if end else "xx")
        out.append("  " + " ".join(row))
    out.append("  ##=unit  ++=agree, can stop  xx=agree, pass-through only")
    out.append("  GG=game only (WE ARE WRONG)  oo=ours only (WE ARE WRONG)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("state_json")
    ap.add_argument("--unit", help="x,y of the selected unit; inferred if absent")
    ap.add_argument("--map", action="store_true", help="print a comparison grid")
    args = ap.parse_args()

    path = pathlib.Path(args.state_json)
    raw = json.loads(path.read_text(encoding="utf-8"))
    board = state_mod.load(path)
    grid = grid_of(raw)
    theirs = game_reachable(grid)
    print(f"{board.width}x{board.height} board, weather {board.weather}, "
          f"game marks {len(theirs)} reachable tile(s)")
    if board.warnings:
        for w in board.warnings:
            print(f"  !! {w}")
        print("  Any mismatch below is untrustworthy until these are resolved: "
              "a wrong board size makes both sets wrong in different ways.")

    if args.unit:
        x, y = (int(v) for v in args.unit.split(","))
        u = board.unit_at(x, y)
        if u is None:
            sys.exit(f"no unit at ({x},{y})")
        candidates = [u]
    else:
        candidates = [u for u in board.units if not u.loaded]

    results = sorted((compare(board, u, grid) for u in candidates), key=score)
    if not results:
        sys.exit("no units to compare against")

    best = results[0]
    u = best["unit"]
    print(f"best match: {u.type} P{u.player} at ({u.x},{u.y}), "
          f"move {pathing.allowance(u)}")
    print(f"  game {len(best['theirs'])} | ours(through) {len(best['through'])} "
          f"| ours(end) {len(best['end'])}")

    if score(best) == 0:
        print("  EXACT MATCH against reachable() -- the pass-through set")
    else:
        print(f"  MISMATCH vs reachable(): {len(best['missing_vs_through'])} "
              f"tile(s) the game reaches and we do not, "
              f"{len(best['extra_vs_through'])} the other way")
        for t in sorted(best["missing_vs_through"])[:12]:
            print(f"    game-only {t} terrain={board.terrain_name(*t)}")
        for t in sorted(best["extra_vs_through"])[:12]:
            print(f"    ours-only {t} terrain={board.terrain_name(*t)}")

    # destinations() is narrower than anything the game writes down, so it is
    # reported and never scored. Naming the blocker makes it checkable by eye.
    occupied = {(o.x, o.y): o for o in board.units if not o.loaded}
    excluded = sorted(set(best["through"]) - set(best["end"]))
    print(f"  of those, {len(excluded)} cannot be STOPPED on "
          f"(the game paints them but rejects the move):")
    for t in excluded[:12]:
        o = occupied.get(t)
        who = f"{o.type} P{o.player}" if o else "?"
        print(f"    {t} occupied by {who}")

    # What do the non-255 values mean? Report against the set the grid holds.
    pairs = {}
    for (x, y), cost in sorted(pathing.reachable(board, u).items()):
        pairs.setdefault(grid[y][x], set()).add(cost)
    print("  grid value -> our cost(s) on those tiles:")
    for gv in sorted(pairs):
        print(f"    {gv:3d} -> {sorted(pairs[gv])}")
    if all(len(v) == 1 and gv in v for gv, v in pairs.items()):
        print("    (grid value == movement cost spent, on every tile)")

    if args.map:
        print(render(board, u, grid, best))
    return 0 if score(best) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

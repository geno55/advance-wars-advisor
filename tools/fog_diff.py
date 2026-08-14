"""Our predicted visibility against the game's own fog mask.

    python tools/fog_diff.py fogged.json

`engine/fog.py` currently rests on four assumed rules with no measurement
behind any of them. The fog hunt turned up a run at 0x03007910 that is all zero
with fog off and bitmask-shaped with it on, which is what a per-tile hidden
mask looks like. If that is what it is, it is an ORACLE -- the same role the
game's own movement flood fill plays for pathing in tools/path_diff.py -- and
the rules stop being assumptions.

Two steps, and the order matters.

1. PIN THE LAYOUT, WITHOUT USING THE RULES. Sweep base, row stride, bit order
   and polarity, and keep only layouts where every one of YOUR OWN units stands
   on a tile the mask calls visible. That anchor is true in any fog
   implementation and owes nothing to engine/fog.py, so it cannot launder our
   assumptions into the thing that is supposed to test them.

2. ONLY THEN COMPARE. With the layout pinned independently, a disagreement
   between the mask and our predicted visibility is OUR rules being wrong. The
   tool scores every combination of the optional rules and reports which one
   the game actually behaves like.

If step 1 pins nothing, that is reported as a failure to identify the mask
rather than being forced -- a mis-pinned layout would make step 2 produce
confident nonsense about the rules.

Needs a FOGGED dump taken with the probe on:

    state("C:/tmp/fogged.json", true)
"""
import argparse
import itertools
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import fog                                        # noqa: E402
from engine.state import load                                 # noqa: E402

# Where the hunt saw the candidate run. Swept, not trusted.
SEARCH_LO, SEARCH_HI = 0x03007880, 0x030079C0
STRIDES = (1, 2, 4, 8, 16, 32)


def decode(blob, off, w, h, stride, msb_first, one_means_hidden):
    """Read a w*h bitmap out of `blob` at `off`, as a set of VISIBLE tiles."""
    vis = set()
    need = off + (h - 1) * stride + (w + 7) // 8
    if off < 0 or need > len(blob):
        return None
    for y in range(h):
        row = off + y * stride
        for x in range(w):
            bit = (7 - (x % 8)) if msb_first else (x % 8)
            one = (blob[row + x // 8] >> bit) & 1
            if one != one_means_hidden:
                vis.add((x, y))
    return vis


def layouts(blob, base_addr, w, h):
    for off in range(max(0, SEARCH_LO - base_addr), SEARCH_HI - base_addr):
        for stride in STRIDES:
            if stride < (w + 7) // 8:
                continue
            for msb in (True, False):
                for hidden in (True, False):
                    yield off, stride, msb, hidden


def main():
    p = argparse.ArgumentParser()
    p.add_argument("state", help="a FOGGED dump taken with state(path, true)")
    p.add_argument("--player", type=int)
    p.add_argument("--max", type=int, default=6)
    a = p.parse_args()

    raw = json.loads(pathlib.Path(a.state).read_text(encoding="utf-8"))
    probe = raw.get("probe", {}).get("iwram")
    if not probe:
        sys.exit("no iwram probe in this dump -- re-dump with state(path, true)")
    board = load(a.state)
    player = a.player or board.active_player
    if not player:
        sys.exit("no active player in this dump; pass --player")
    if board.fog is False:
        sys.exit("this dump has fog OFF -- the mask will be empty. Capture a "
                 "fogged match.")

    blob = bytes.fromhex(probe["hex"])
    base_addr = int(probe["base"], 16)
    w, h = board.width, board.height
    own = {(u.x, u.y) for u in board.units
           if u.player == player and not u.loaded}
    print(f"{w}x{h} board, P{player}, {len(own)} own unit tile(s) as the anchor")

    # ---- step 1: pin the layout using only the own-units anchor -------------
    survivors = []
    for off, stride, msb, hidden in layouts(blob, base_addr, w, h):
        vis = decode(blob, off, w, h, stride, msb, hidden)
        if vis is None or not own <= vis:
            continue
        if not (0 < len(vis) < w * h):          # all-lit or all-dark is not a mask
            continue
        survivors.append((off, stride, msb, hidden, vis))

    print(f"\nstep 1 -- layouts consistent with every own unit being visible: "
          f"{len(survivors)}")
    if not survivors:
        print("\nNone. Either the run at 0x03007910 is not a visibility mask, or\n"
              "its layout is outside the swept space (base "
              f"0x{SEARCH_LO:08X}..0x{SEARCH_HI:08X}, strides {STRIDES}).\n"
              "Not forcing a fit: a mis-pinned layout would produce confident\n"
              "nonsense about the visibility rules in step 2.")
        return 1

    # Prefer the layout that lights the least, since any superset also passes
    # the anchor and the tightest one is the one carrying real information.
    survivors.sort(key=lambda s: (len(s[4]), s[0]))
    for off, stride, msb, hidden, vis in survivors[:a.max]:
        print(f"  0x{base_addr + off:08X}  stride {stride:2d}  "
              f"{'msb' if msb else 'lsb'}-first  "
              f"1={'hidden' if hidden else 'visible'}  "
              f"{len(vis)}/{w * h} lit")
    if len(survivors) > a.max:
        print(f"  ... and {len(survivors) - a.max} more")

    # ---- step 2: score our rules against the pinned mask -------------------
    off, stride, msb, hidden, game_vis = survivors[0]
    print(f"\nstep 2 -- scoring engine/fog.py against the tightest layout, "
          f"0x{base_addr + off:08X}")
    optional = ["hiding_terrain", "property_vision", "mountain_bonus"]
    rows = []
    for combo in itertools.product((False, True), repeat=len(optional)):
        rs = fog.rules(**dict(zip(optional, combo)))
        ours = fog.visible_tiles(board, player, rs)
        miss = game_vis - ours          # game lights it, we do not: we are blind
        extra = ours - game_vis         # we light it, game does not: we cheat
        rows.append((len(miss) + len(extra), len(miss), len(extra), combo))
    rows.sort()

    print(f"  {'blind':>6s} {'cheat':>6s}  rules on")
    for total, miss, extra, combo in rows:
        on = ", ".join(n for n, v in zip(optional, combo) if v) or "(none)"
        star = "  <-- best" if total == rows[0][0] else ""
        print(f"  {miss:6d} {extra:6d}  {on}{star}")

    best = rows[0]
    tied = [r for r in rows if r[0] == best[0]]
    print()
    if best[0] == 0:
        on = ", ".join(n for n, v in zip(optional, best[3]) if v) or "(none)"
        print(f"EXACT match with rules: {on}.")
        if len(tied) > 1:
            # Rules that never fire cannot be scored. Saying "confirmed" for
            # all of them would turn one measurement into four.
            varies = {n for i, n in enumerate(optional)
                      if len({t[3][i] for t in tied}) > 1}
            settled = [n for n in optional if n not in varies]
            print(f"  {len(tied)} combinations tie, so this board only settles "
                  f"{', '.join(settled) or 'nothing'}.")
            print(f"  UNEXERCISED here, still assumptions: {', '.join(sorted(varies))}.")
            print(f"  To settle them, capture a board that makes them bite -- a "
                  f"unit sitting\n  in woods for hiding_terrain, one on a "
                  f"mountain for mountain_bonus.")
        else:
            print("  No other combination matches, so this board settles all "
                  "three.")
    else:
        print(f"No rule combination reproduces the mask exactly -- best is "
              f"{best[1]} tile(s) we miss and {best[2]} we wrongly light.\n"
              f"'cheat' is the dangerous column: those are tiles the advisor "
              f"would treat as seen.\nA residue this size usually means one "
              f"more rule exists (a CO vision trait, or\nterrain that blocks "
              f"sight) rather than that the radius model is wrong.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

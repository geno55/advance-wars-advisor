"""Our predicted visibility against the game's own fog mask.

    python tools/fog_diff.py fogged.json

`engine/fog.py` currently rests on four assumed rules with no measurement
behind any of them. If the game keeps a per-tile visibility mask, that mask is
an ORACLE -- the same role the game's own movement flood fill plays for pathing
in tools/path_diff.py -- and the rules stop being assumptions.

A first attempt pointed this at 0x03007910, which is all zero with fog off and
looked bitmask-shaped with it on. It is not a mask: read as words it is
0x030053C0, 0x0823066C, 0x080780FD -- IWRAM and ROM pointers, one of them an
odd address and therefore a THUMB function pointer. It is a handler table the
game populates when fog switches on. Bytes that look like a bitmask and bytes
that are the low halves of pointers are the same bytes.

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

Give it a CLEAR capture of the same map as a control. Without one the anchor is
the only constraint and it is far too weak: the first real run survived 984
layouts and its top-ranked pick was the pointer table. With a control, a
candidate must both be among the bytes fog changed and read zero when there is
nothing to hide.

    state("C:/tmp/fog_on.json",  true)   -- fogged
    state("C:/tmp/fog_off.json", true)   -- same map, fog off
    python tools/fog_diff.py C:/tmp/fog_on.json --off C:/tmp/fog_off.json

Both shapes are swept: a bit-per-tile bitmap, and a byte-per-tile array, which
is what sitting next to the byte-per-tile terrain map at 0x02016C2A would
suggest.
"""
import argparse
import itertools
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import fog, pathing                               # noqa: E402
from engine.state import load                                 # noqa: E402

STRIDES = (1, 2, 4, 8, 15, 16, 20, 24, 30, 32, 40, 48, 60, 64)


def row_bytes(w, bits):
    return (w + 7) // 8 if bits == 1 else w


def decode(blob, off, w, h, stride, msb_first, one_means_hidden, bits):
    """Read a w*h visibility map out of `blob` at `off`, as VISIBLE tiles.

    Two shapes. `bits=1` is a packed bitmap; `bits=8` is a byte per tile, where
    "set" means nonzero -- the shape the terrain map next door uses.
    """
    if off < 0 or off + (h - 1) * stride + row_bytes(w, bits) > len(blob):
        return None
    vis = set()
    for y in range(h):
        row = off + y * stride
        for x in range(w):
            if bits == 1:
                bit = (7 - (x % 8)) if msb_first else (x % 8)
                one = (blob[row + x // 8] >> bit) & 1
            else:
                one = 1 if blob[row + x] else 0
            if one != one_means_hidden:
                vis.add((x, y))
    return vis


def shapes(w, h):
    """(bits, stride, msb, hidden, span) combinations worth trying.

    Strides are capped at twice the natural row width. A mask padded to eight
    times its row length is not impossible, but sweeping that far costs more
    than it is worth and would be a silent claim to have ruled it out.
    """
    for bits in (1, 8):
        nat = row_bytes(w, bits)
        for stride in sorted({s for s in STRIDES if nat <= s <= 2 * nat} | {nat}):
            span = nat + (h - 1) * stride
            # A byte-per-tile array has no bit order to get wrong.
            for msb in ((True, False) if bits == 1 else (False,)):
                for hidden in (True, False):
                    yield bits, stride, msb, hidden, span


def changed_offsets(on_blob, off_blob):
    """Byte positions that differ between the fogged and clear captures.

    This is the constraint that makes the search tractable and honest. A
    visibility mask must be one of the things fog turned on, so its bytes have
    to be among these -- and requiring them to read ZERO in the clear capture
    is what a mask looks like when there is nothing to hide.
    """
    n = min(len(on_blob), len(off_blob))
    return {i for i in range(n) if on_blob[i] != off_blob[i]}


def unstable(blobs):
    """Byte positions that differ between captures that should be identical.

    Two fogged dumps of the same untouched board must agree wherever the game
    stores visibility, so anything that moved between them is a counter, a
    cursor or RNG -- and cannot be part of the mask. This is what the second
    capture buys, and it removes roughly half the candidates.
    """
    if len(blobs) < 2:
        return set()
    n = min(len(b) for b in blobs)
    first = blobs[0]
    return {i for i in range(n) if any(b[i] != first[i] for b in blobs[1:])}


def visible_at(blob, off, x, y, stride, msb_first, one_means_hidden, bits):
    """Decode a single tile. Checking the handful of tiles that MUST be visible
    before decoding all 150 is what keeps the sweep to minutes."""
    if bits == 1:
        bit = (7 - (x % 8)) if msb_first else (x % 8)
        one = (blob[off + y * stride + x // 8] >> bit) & 1
    else:
        one = 1 if blob[off + y * stride + x] else 0
    return one != one_means_hidden


def nonzero_prefix(blob):
    """prefix[i] = how many of blob[0:i] are nonzero, so an all-zero span test
    is a subtraction instead of a scan. EWRAM is 256K and the start set runs to
    hundreds of thousands; scanning each span turns minutes into hours."""
    pre = [0] * (len(blob) + 1)
    run = 0
    for i, b in enumerate(blob):
        if b:
            run += 1
        pre[i + 1] = run
    return pre


def candidate_starts(changed, span, blob_len):
    """Offsets a mask of exactly `span` bytes could BEGIN at.

    Not simply the changed bytes: a mask whose top row is entirely dark starts
    on a byte that never moved. What must hold is that the span CONTAINS a
    changed byte, so the start may sit up to span-1 bytes earlier. The span is
    per-layout -- using the largest possible one as a single bound demands an
    all-zero window no real capture has, and finds nothing.
    """
    starts = set()
    for c in changed:
        starts.update(range(max(0, c - span + 1), min(c, blob_len - span) + 1))
    return sorted(starts)


def counting_prefix(flags):
    """prefix[i] = how many of flags[0:i] are true, so a span test is a
    subtraction rather than a scan."""
    pre = [0] * (len(flags) + 1)
    run = 0
    for i, f in enumerate(flags):
        if f:
            run += 1
        pre[i + 1] = run
    return pre


def local_enough(vis, own, properties, reach):
    """Whether every lit tile is plausibly lit BY something.

    Fog means sight is local. A layout that lights half the map from five units
    is not a visibility mask however well it covers those five tiles, and the
    own-units anchor alone cannot say so -- it passes any layout that lights
    almost everything. This is deliberately rule-light: it asks only that lit
    tiles be near a unit or on a property, never how near, so engine/fog.py's
    actual radius rule stays out of its own test.
    """
    for tile in vis:
        if tile in properties:
            continue
        if not any(abs(tile[0] - ox) + abs(tile[1] - oy) <= reach
                   for ox, oy in own):
            return False
    return True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("state", nargs="+",
                   help="FOGGED dumps of one untouched board, taken with "
                        "state(path, true). Two or more lets the search drop "
                        "everything that moved between them, which cannot be "
                        "the mask.")
    p.add_argument("--off", nargs="+",
                   help="CLEAR dump(s) of the SAME map, as a control. Strongly "
                        "recommended: without one the search cannot rule out "
                        "regions fog never touched, and the first run without "
                        "one produced 984 candidate layouts.")
    p.add_argument("--clear", choices=("any", "zero", "constant"),
                   default="any",
                   help="what the span must look like with fog OFF. 'zero' "
                        "assumes the game blanks the array; 'constant' allows "
                        "an all-visible fill too. Default 'any' assumes "
                        "nothing -- requiring zero found nothing on the first "
                        "real capture, and that may have been the assumption "
                        "rather than the absence of a mask.")
    p.add_argument("--player", type=int)
    p.add_argument("--max", type=int, default=6)
    a = p.parse_args()

    def read(path, want_fog):
        r = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if r.get("fog") is not want_fog:
            sys.exit(f"{path} has fog={r.get('fog')}, expected {want_fog}")
        if not r.get("probe"):
            sys.exit(f"{path} has no probe -- re-dump with state(path, true)")
        return r

    ons = [read(f, True) for f in a.state]
    offs = [read(f, False) for f in (a.off or [])]
    probes = ons[0]["probe"]
    board = load(a.state[0])
    for other in ons[1:] + offs:
        if (other["width"], other["height"]) != (board.width, board.height):
            sys.exit("captures are of different-sized maps -- the control must "
                     "be the SAME map with only the fog toggle changed")
    player = a.player or board.active_player
    if not player:
        sys.exit("no active player in this dump; pass --player")
    if board.fog is False:
        sys.exit("this dump has fog OFF -- the mask will be empty. Capture a "
                 "fogged match.")

    w, h = board.width, board.height
    own = {(u.x, u.y) for u in board.units
           if u.player == player and not u.loaded}
    print(f"{w}x{h} board, P{player}, {len(own)} own unit tile(s) as the anchor")
    print(f"a bit-per-tile mask would be {((w + 7) // 8) * h} bytes "
          f"(stride {(w + 7) // 8}); a byte-per-tile one {w * h} (stride {w})")
    print(f"{len(ons)} fogged capture(s), {len(offs)} clear, "
          f"clear-span rule '{a.clear}'")
    if not offs:
        print("!! no --off control given: every region is in scope and the "
              "anchor alone\n!! is far too weak to pin a layout. Expect "
              "hundreds of survivors.")

    # Sight is local. Max vision on the roster plus slack for bonuses nobody
    # has measured -- generous on purpose, since this is a plausibility filter
    # and not the radius rule under test.
    reach = max(s["vision"] for s in pathing._stats().values()) + 3
    props = {(x, y) for x, y, _ in board.properties_of(player)}

    # ---- step 1: pin the layout, using no rule from engine/fog.py -----------
    survivors = []
    for region in sorted(probes):
        on_blobs = [bytes.fromhex(o["probe"][region]["hex"]) for o in ons
                    if region in o["probe"]]
        off_blobs = [bytes.fromhex(o["probe"][region]["hex"]) for o in offs
                     if region in o["probe"]]
        blob = on_blobs[0]
        base_addr = int(probes[region]["base"], 16)

        # Anything that moved between captures that should agree is not the
        # mask, whichever side it moved on.
        noise = unstable(on_blobs) | unstable(off_blobs)
        noise_pre = counting_prefix([i in noise for i in range(len(blob))])

        if off_blobs:
            changed = changed_offsets(blob, off_blobs[0]) - noise
            zero_pre = counting_prefix([b != 0 for b in off_blobs[0]])
            step_pre = counting_prefix(
                [i > 0 and off_blobs[0][i] != off_blobs[0][i - 1]
                 for i in range(len(off_blobs[0]))])
            print(f"  {region}: {len(changed)} stable change(s) off->on, "
                  f"{len(noise)} byte(s) dropped as noise")
        else:
            changed, zero_pre, step_pre = None, None, None

        for bits, stride, msb, hidden, span in shapes(w, h):
            starts = (candidate_starts(changed, span, len(blob))
                      if changed is not None
                      else range(max(0, len(blob) - span)))
            for off in starts:
                if off + span > len(blob):
                    continue
                # Cheapest reject first: the tiles that MUST read visible.
                if not all(visible_at(blob, off, x, y, stride, msb, hidden, bits)
                           for x, y in own):
                    continue
                # The mask must be identical across the fogged captures.
                if noise_pre[off + span] - noise_pre[off]:
                    continue
                if a.clear == "zero" and zero_pre is not None:
                    if zero_pre[off + span] - zero_pre[off]:
                        continue
                if a.clear == "constant" and step_pre is not None:
                    if step_pre[off + span] - step_pre[off + 1]:
                        continue
                vis = decode(blob, off, w, h, stride, msb, hidden, bits)
                if vis is None or not (0 < len(vis) < w * h):
                    continue
                if not local_enough(vis, own, props, reach):
                    continue
                # How much of what fog actually wrote this layout accounts for.
                # Neighbouring offsets decode almost the same picture, so the
                # discriminator is which span covers the writes -- one that
                # leaves changed bytes outside itself is straddling the real
                # structure rather than being it.
                covered = (sum(1 for c in changed if off <= c < off + span)
                           if changed is not None else 0)
                survivors.append((region, base_addr, off, stride, msb, hidden,
                                  bits, vis, covered))

    print(f"\nstep 1 -- layouts consistent with every own unit being visible: "
          f"{len(survivors)}")
    if not survivors:
        print("\nNone, across every probed region. Either no per-tile mask "
              "exists in the\nprobed RAM, or its shape is outside the swept "
              f"space (strides {STRIDES},\nbit-per-tile only -- a byte-per-tile "
              "array would not be found by this).\n"
              "Not forcing a fit: a mis-pinned layout would produce confident\n"
              "nonsense about the visibility rules in step 2.")
        return 1

    # Rank by structural fit, not by how little it lights. Preferring the
    # sparsest layout was wrong: any layout that happens to cover the handful
    # of own-unit tiles passes the anchor, so "tightest" selected for
    # coincidence and picked a pointer table.
    survivors.sort(key=lambda s: (s[3] != row_bytes(w, s[6]), -s[8], s[2]))
    for (region, base_addr, off, stride, msb, hidden, bits, vis,
         covered) in survivors[:a.max]:
        star = "  <-- natural stride" if stride == row_bytes(w, bits) else ""
        shape = "bit/tile" if bits == 1 else "byte/tile"
        order = f"{'msb' if msb else 'lsb'}-first  " if bits == 1 else ""
        print(f"  {region} 0x{base_addr + off:08X}  {shape:9s} stride {stride:2d}  "
              f"{order}set={'hidden' if hidden else 'visible'}  "
              f"{len(vis)}/{w * h} lit  covers {covered}{star}")
    if len(survivors) > a.max:
        print(f"  ... and {len(survivors) - a.max} more")
    if len(survivors) > 1:
        print("  More than one layout survives, so step 2 scores the "
              "best-ranked and is\n  only as good as that ranking.")

    # ---- step 2: score our rules against the pinned mask -------------------
    (region, base_addr, off, stride, msb, hidden, bits, game_vis,
     _cov) = survivors[0]
    print(f"\nstep 2 -- scoring engine/fog.py against "
          f"{region} 0x{base_addr + off:08X}")
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

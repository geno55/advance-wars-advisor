"""Dump ASCII string clusters, and hunt for terrain / mission name tables.

The unit names turned out to be plain ASCII in a packed blob at 0x282CC8, so
the terrain names and mission names are probably nearby and in the same style.
Finding the terrain blob also pins the terrain ID order, which we need for the
defence-star table.
"""
import pathlib
import re
import sys
from collections import defaultdict

TERRAIN = ["Plain", "Wood", "Mountain", "River", "Road", "Bridge", "Shoal",
           "Reef", "Sea", "City", "Base", "Airport", "Port", "HQ", "Silo",
           "Pipe"]


def ascii_strings(rom, lo, hi, minlen=3):
    out, cur, start = [], [], None
    for i in range(lo, min(hi, len(rom))):
        b = rom[i]
        if 0x20 <= b < 0x7F:
            if start is None:
                start = i
            cur.append(chr(b))
        else:
            if start is not None and len(cur) >= minlen:
                out.append((start, "".join(cur)))
            cur, start = [], None
    if start is not None and len(cur) >= minlen:
        out.append((start, "".join(cur)))
    return out


def main(path, mode="clusters", lo=0, hi=None):
    rom = pathlib.Path(path).read_bytes()
    hi = hi or len(rom)

    if mode == "dump":
        for off, s in ascii_strings(rom, lo, hi, 3):
            print(f"0x{off:06X}  {s!r}")
        return

    if mode == "terrain":
        print("=== terrain name hits ===")
        for name in TERRAIN:
            hits = [m.start() for m in re.finditer(re.escape(name.encode()), rom)]
            if hits:
                print(f"  {name:10s} " + ", ".join(f"0x{h:06X}" for h in hits[:8]))
        return

    # cluster mode: which 1KB regions are dense with plausible names?
    buckets = defaultdict(list)
    for off, s in ascii_strings(rom, 0, len(rom), 4):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9 '!?.,\-]{3,23}", s):
            buckets[off >> 10].append(s)
    ranked = sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    print("=== densest name-like string regions (1KB buckets) ===")
    for bucket, strings in ranked[:22]:
        sample = ", ".join(repr(s) for s in strings[:7])
        print(f"0x{bucket << 10:06X}  n={len(strings):3d}  {sample}")


if __name__ == "__main__":
    m = sys.argv[2] if len(sys.argv) > 2 else "clusters"
    main(sys.argv[1], m,
         int(sys.argv[3], 0) if len(sys.argv) > 3 else 0,
         int(sys.argv[4], 0) if len(sys.argv) > 4 else None)

"""Locate a pointer table by finding references to known string addresses.

Mission names live at known offsets. Whatever table indexes the missions almost
certainly stores pointers to those names, so: find where each name's mapped
address appears as a little-endian word, sort the hits, and look for a constant
stride. A constant stride means we have found the table and know its record size
-- and the other fields in each record are the map data we actually want.
"""
import pathlib
import struct
import sys
from collections import Counter

ROM_BASE = 0x08000000


def find_word(rom, value):
    needle = struct.pack("<I", value)
    out, start = [], 0
    while True:
        i = rom.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


def main(path, targets):
    rom = pathlib.Path(path).read_bytes()
    hits = {}
    for t in targets:
        hits[t] = find_word(rom, ROM_BASE + t)

    print("references to each name string:")
    for t, hs in hits.items():
        s = rom[t:rom.index(b"\0", t)].decode("ascii", "replace")
        loc = ", ".join(f"0x{h:06X}" for h in hs[:6]) or "(none)"
        print(f"  0x{t:06X} {s!r:22s} -> {loc}")

    # Look for a consistent stride between consecutive missions' pointer slots.
    flat = sorted((h, t) for t, hs in hits.items() for h in hs)
    deltas = Counter()
    for i in range(len(flat) - 1):
        d = flat[i + 1][0] - flat[i][0]
        if 0 < d <= 0x80:
            deltas[d] += 1
    print("\nmost common gaps between reference sites:")
    for d, n in deltas.most_common(6):
        print(f"  stride 0x{d:02X} ({d:3d} bytes)  x{n}")


if __name__ == "__main__":
    main(sys.argv[1], [int(a, 0) for a in sys.argv[2:]])

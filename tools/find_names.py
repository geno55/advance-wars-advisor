"""Find the unit name strings and any pointer table indexing them.

The unit ID -> name mapping is the one thing that makes the damage matrices
meaningful. Deriving it from the ROM (rather than from memory) is the whole
point, so: locate the ASCII names, then look for a table of GBA pointers
(0x08xxxxxx, little-endian) that references them in ID order.
"""
import sys, re, pathlib, struct

ROM_BASE = 0x08000000

WANTED = ["Infantry", "Mech", "Recon", "Tank", "MdTank", "Md Tank", "APC",
          "Artillery", "Rocket", "Missile", "Anti-Air", "AntiAir", "Fighter",
          "Bomber", "Copter", "Battleship", "Cruiser", "Lander", "Sub",
          "Neotank"]


def ascii_strings(rom, lo, hi, minlen=3):
    out = []
    cur, start = [], None
    for i in range(lo, hi):
        b = rom[i]
        if 0x20 <= b < 0x7F:
            if start is None:
                start = i
            cur.append(chr(b))
        else:
            if start is not None and len(cur) >= minlen:
                out.append((start, "".join(cur)))
            cur, start = [], None
    return out


def main(path):
    rom = pathlib.Path(path).read_bytes()

    print("=== unit-name strings anywhere in ROM ===")
    found = {}
    for name in WANTED:
        for m in re.finditer(re.escape(name.encode()), rom):
            found.setdefault(name, []).append(m.start())
    for name, offs in sorted(found.items()):
        print(f"  {name:12s} x{len(offs):<3d} " +
              ", ".join(f"0x{o:06X}" for o in offs[:6]))

    # Cluster: where do the most distinct names live close together?
    all_offs = sorted((o, n) for n, os in found.items() for o in os)
    print("\n=== densest cluster of distinct names ===")
    best = None
    for i, (o, _) in enumerate(all_offs):
        window = [x for x in all_offs if o <= x[0] < o + 0x400]
        distinct = len(set(n for _, n in window))
        if best is None or distinct > best[0]:
            best = (distinct, o)
    if best:
        distinct, o = best
        print(f"  {distinct} distinct names near 0x{o:06X}")
        for off, s in ascii_strings(rom, max(0, o - 0x80), o + 0x400):
            print(f"    0x{off:06X}  {s!r}")


if __name__ == "__main__":
    main(sys.argv[1])

"""Find code references to the damage tables.

ARM/THUMB loads a 32-bit constant from a literal pool, so the table's mapped
address (0x08000000 + file offset) appears verbatim as 4 little-endian bytes
somewhere near the routine that uses it. Find those literals and we have found
the damage code.

This also settles the fighter-secondary question for free: if only one of the
two ROM copies is referenced by code, that copy is the live one.
"""
import pathlib
import struct
import sys

ROM_BASE = 0x08000000

TARGETS = {
    "primary_main":   0x283B48,
    "secondary_main": 0x283D88,
    "primary_alt":    0x3F4BBC,
    "secondary_alt":  0x3F4DFC,
}


def find_literals(rom, file_off):
    """Every place the mapped address appears as a 4-byte LE word."""
    needle = struct.pack("<I", ROM_BASE + file_off)
    out, start = [], 0
    while True:
        i = rom.find(needle, start)
        if i < 0:
            return out
        out.append(i)
        start = i + 1


def main(path):
    rom = pathlib.Path(path).read_bytes()
    print(f"{'table':16s} {'mapped addr':>12s}  literal-pool references")
    for name, off in TARGETS.items():
        hits = find_literals(rom, off)
        loc = ", ".join(f"0x{h:06X}" for h in hits) if hits else "(none)"
        print(f"{name:16s} 0x{ROM_BASE + off:08X}  {loc}")

    # Also look for near-miss references: code often loads table_base - k and
    # indexes with a biased register, so scan a small window around each table.
    print("\nnear-miss references (table_base +/- 0x40):")
    for name, off in TARGETS.items():
        found = []
        for delta in range(-0x40, 0x41, 4):
            for h in find_literals(rom, off + delta):
                found.append((delta, h))
        if found:
            for delta, h in found[:12]:
                print(f"  {name:16s} base{delta:+#06x} referenced at 0x{h:06X}")
        else:
            print(f"  {name:16s} (none)")


if __name__ == "__main__":
    main(sys.argv[1])

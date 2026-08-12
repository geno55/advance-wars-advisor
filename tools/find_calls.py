"""Find THUMB BL callers of a given address -- walk the call graph upward.

The base-damage routine only does the table lookup and CO modifiers. HP scaling,
terrain and luck live in whatever calls it, so we need callers.

ARMv4T BL is a 32-bit instruction pair:
    hi = 0xF000 | imm11_high     (offset bits 22:12, signed)
    lo = 0xF800 | imm11_low      (offset bits 11:1)
    target = pc_of_hi + 4 + (sext(imm11_high) << 12) + (imm11_low << 1)
"""
import pathlib
import struct
import sys

ROM_BASE = 0x08000000


def bl_targets(rom):
    """Yield (call_site_file_offset, target_mapped_address) for every BL."""
    for i in range(0, len(rom) - 3, 2):
        hi = struct.unpack_from("<H", rom, i)[0]
        if (hi & 0xF800) != 0xF000:
            continue
        lo = struct.unpack_from("<H", rom, i + 2)[0]
        if (lo & 0xF800) != 0xF800:
            continue
        off_hi = hi & 0x7FF
        if off_hi & 0x400:                    # sign extend 11 bits
            off_hi -= 0x800
        delta = (off_hi << 12) | ((lo & 0x7FF) << 1)
        target = ROM_BASE + i + 4 + delta
        yield i, target


def main(path, want, window):
    rom = pathlib.Path(path).read_bytes()
    lo, hi = want - window, want + window
    hits = [(site, t) for site, t in bl_targets(rom) if lo <= t <= hi]
    print(f"BL sites targeting 0x{want:08X} +/-0x{window:X}: {len(hits)}")
    for site, t in sorted(hits, key=lambda x: x[1]):
        print(f"  call at 0x{site:06X} (mapped 0x{ROM_BASE + site:08X}) -> 0x{t:08X}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2], 0),
         int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x20)

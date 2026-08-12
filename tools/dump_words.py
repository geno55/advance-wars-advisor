"""Dump 32-bit little-endian words with light annotation, for decoding structs."""
import pathlib
import struct
import sys

ROM_BASE = 0x08000000


def tag(rom, v):
    if 0x08000000 <= v < 0x08000000 + len(rom):
        off = v - ROM_BASE
        # If it points at printable ASCII, show it -- that identifies name fields.
        s = bytearray()
        for i in range(off, min(off + 24, len(rom))):
            if rom[i] == 0:
                break
            if not (0x20 <= rom[i] < 0x7F):
                s = bytearray()
                break
            s.append(rom[i])
        if len(s) >= 3:
            return f"-> rom 0x{off:06X} {bytes(s).decode('ascii')!r}"
        return f"-> rom 0x{off:06X}"
    if 0x02000000 <= v < 0x02040000:
        return "-> ewram"
    if 0x03000000 <= v < 0x03008000:
        return "-> iwram"
    if v < 0x10000:
        return f"({v})"
    return ""


def main(path, lo, hi, stride=4):
    rom = pathlib.Path(path).read_bytes()
    for off in range(lo, hi, 4):
        v = struct.unpack_from("<I", rom, off)[0]
        rel = (off - lo) % stride
        mark = "|" if rel == 0 else " "
        print(f"{mark}0x{off:06X} +{rel:02X}  0x{v:08X}  {tag(rom, v)}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2], 0), int(sys.argv[3], 0),
         int(sys.argv[4], 0) if len(sys.argv) > 4 else 4)

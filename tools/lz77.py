"""GBA BIOS LZ77 (type 0x10) decompression, and a map dumper built on it.

Header: byte 0x10, then a 24-bit little-endian decompressed size.
Body: a flag byte, then 8 units, MSB first.
  flag bit 0 -> copy one literal byte
  flag bit 1 -> two bytes b1,b2: length = (b1 >> 4) + 3,
                disp = ((b1 & 0xF) << 8) | b2, copy from out[-disp-1]
"""
import pathlib
import struct
import sys


def decompress(rom, off):
    hdr = struct.unpack_from("<I", rom, off)[0]
    if (hdr & 0xFF) != 0x10:
        raise ValueError(f"0x{off:06X}: not LZ77 (header byte 0x{hdr & 0xFF:02X})")
    size = hdr >> 8
    out = bytearray()
    p = off + 4
    while len(out) < size:
        flags = rom[p]
        p += 1
        for bit in range(7, -1, -1):
            if len(out) >= size:
                break
            if flags & (1 << bit):
                b1, b2 = rom[p], rom[p + 1]
                p += 2
                length = (b1 >> 4) + 3
                disp = ((b1 & 0xF) << 8) | b2
                start = len(out) - disp - 1
                if start < 0:
                    raise ValueError("bad back-reference")
                for i in range(length):
                    out.append(out[start + i])
            else:
                out.append(rom[p])
                p += 1
    return bytes(out), p - off


def main(path, off):
    rom = pathlib.Path(path).read_bytes()
    data, consumed = decompress(rom, off)
    print(f"0x{off:06X}: {len(data)} bytes decompressed from {consumed} "
          f"({100*consumed/len(data):.0f}%)")
    print(f"first 32 bytes: {' '.join(f'{b:3d}' for b in data[:32])}")
    print(f"distinct values: {sorted(set(data))}")

    # Guess the grid: AW maps are at most 30x20, and the payload is usually
    # width*height plus a small header.
    n = len(data)
    print("\nplausible width x height factorisations:")
    for w in range(10, 31):
        for extra in (0, 1, 2, 3, 4):
            if (n - extra) % w == 0:
                h = (n - extra) // w
                if 10 <= h <= 30:
                    print(f"  {w:2d} x {h:2d}  (+{extra} byte header)")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2], 0))

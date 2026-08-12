"""Render a mission map from the compressed blob.

Format (confirmed on It's War!): u16 width, u16 height, then width*height u16
tile ids. Tile ids run 0..~236, far more than the ~14 terrain types, so the id
space evidently encodes visual variants (road corners, river bends, shore
shapes) and probably property ownership too.
"""
import pathlib
import struct
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from lz77 import decompress  # noqa: E402


def load_map(rom, off):
    data, _ = decompress(rom, off)
    w, h = struct.unpack_from("<HH", data, 0)
    if 4 + w * h * 2 != len(data):
        raise ValueError(f"size mismatch: {w}x{h} but {len(data)} bytes")
    tiles = list(struct.unpack_from(f"<{w*h}H", data, 4))
    return w, h, tiles


def main(path, off):
    rom = pathlib.Path(path).read_bytes()
    w, h, tiles = load_map(rom, off)
    print(f"map {w} x {h} = {w*h} tiles\n")
    for y in range(h):
        row = tiles[y * w:(y + 1) * w]
        print("  " + " ".join(f"{t:3d}" for t in row))

    c = Counter(tiles)
    print(f"\n{len(c)} distinct tile ids")
    print("frequency (most common first):")
    for tid, n in c.most_common():
        print(f"  id {tid:3d}  x{n:3d}" + ("   <- rare, likely a property or unit" if n <= 4 else ""))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2], 0))

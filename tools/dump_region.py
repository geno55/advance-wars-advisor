"""Dump a ROM region at a given stride so table boundaries are visible by eye."""
import sys, pathlib

rom = pathlib.Path(sys.argv[1]).read_bytes()
base = int(sys.argv[2], 16)
stride = int(sys.argv[3])
nrows = int(sys.argv[4])
anchor = int(sys.argv[5], 16) if len(sys.argv) > 5 else base

for r in range(nrows):
    off = base + r * stride
    row = rom[off:off + stride]
    rel = (off - anchor) // stride
    mark = "*" if off == anchor else " "
    print(f"{mark}{off:06X} r{rel:+03d} | " + " ".join(f"{b:3d}" for b in row))

"""Diff the two apparent copies of the damage matrices."""
import sys, pathlib

rom = pathlib.Path(sys.argv[1]).read_bytes()
N = 24
for label, a, b in (("primary", 0x283B48, 0x3F4BBC),
                    ("secondary", 0x283D88, 0x3F4DFC)):
    A = rom[a:a + N * N]
    B = rom[b:b + N * N]
    diffs = [(i, A[i], B[i]) for i in range(N * N) if A[i] != B[i]]
    print(f"{label}: {len(diffs)} differing bytes of {N*N}")
    for i, x, y in diffs[:40]:
        print(f"   row {i//N:2d} col {i%N:2d}:  0x{a:06X}={x:3d}   0x{b:06X}={y:3d}")
    print()

"""Locate the per-unit stat table in the ROM.

All anchors are OBSERVED, never recalled:
  max fuel, read from a live unit array: Infantry 99, Mech 70, Recon 80,
    Tank 70, APC 70, Artillery 50
  vision, read off the in-game unit info: Infantry 2, Mech 2, Tank 3, Recon 5,
    APC 1

Searching fuel alone found nothing at any stride from 1 to 40, in either the
sparse 24-slot id space the damage tables use or the compact 18-unit order the
name blob implies, as u8 or u16. So fuel is not a plain per-unit array.

Vision is a weaker anchor on its own -- the values are all 1..5 and will match
noise everywhere -- so the real test is the two TOGETHER: a genuine stat record
should carry vision and fuel at a constant offset from each other.
"""
import pathlib
import sys

SPARSE_FUEL = {0: 99, 1: 70, 4: 70, 5: 80, 6: 70, 9: 50}
SPARSE_VIS = {0: 2, 1: 2, 4: 3, 5: 5, 6: 1}
COMPACT_FUEL = {0: 99, 1: 70, 3: 70, 4: 80, 5: 70, 6: 50}
COMPACT_VIS = {0: 2, 1: 2, 3: 3, 4: 5, 5: 1}


def matches(rom, base, stride, spec):
    for i, v in spec.items():
        a = base + i * stride
        if a >= len(rom) or rom[a] != v:
            return False
    return True


def hunt(rom, vis, fuel, maxid, label):
    # Anchor on Recon's vision of 5 -- the most distinctive of the vision values.
    recon_id = max(vis, key=lambda k: vis[k])
    anchors = [i for i, b in enumerate(rom) if b == vis[recon_id]]
    cands = []
    for a in anchors:
        for stride in range(1, 41):
            base = a - recon_id * stride
            if base < 0 or base + maxid * stride >= len(rom):
                continue
            if matches(rom, base, stride, vis):
                cands.append((base, stride))
    print(f"{label}: {len(cands)} vision-only match(es)")
    for base, stride in cands[:10]:
        v = [rom[base + i * stride] for i in range(maxid + 1)]
        plausible = all(0 <= x <= 6 for x in v)
        print(f"    stride {stride:2d} @0x{base:06X}"
              + ("  all values 0..6, plausible as vision" if plausible else "")
              + f"\n      {v}")

    both = []
    for base, stride in cands:
        for k in range(-64, 65):
            if k == 0:
                continue
            fb = base + k
            if fb < 0 or fb + maxid * stride >= len(rom):
                continue
            if matches(rom, fb, stride, fuel):
                both.append((base, stride, k))
                break
    print(f"{label}: {len(both)} with fuel at a constant offset too")
    for base, stride, k in both[:8]:
        v = [rom[base + i * stride] for i in range(maxid + 1)]
        f = [rom[base + k + i * stride] for i in range(maxid + 1)]
        print(f"    stride {stride:2d}  vision @0x{base:06X}  fuel at {k:+d}")
        print(f"      vision: {v}")
        print(f"      fuel  : {f}")
    return both


def main(path):
    rom = pathlib.Path(path).read_bytes()
    hunt(rom, SPARSE_VIS, SPARSE_FUEL, 23, "sparse 24-slot ids")
    print()
    hunt(rom, COMPACT_VIS, COMPACT_FUEL, 17, "compact 18-unit order")


if __name__ == "__main__":
    main(sys.argv[1])

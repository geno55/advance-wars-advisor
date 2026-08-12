"""Locate the unit-vs-unit damage matrices in the ROM.

Two independent methods; we only trust an offset both agree on (and even then
the emulator harness is the final judge).

  A. Anchor search - the infantry row is the one matchup nobody misremembers:
     Infantry vs Infantry = 55, Infantry vs Mech = 45. Look for that pair and
     eyeball the surrounding block.

  B. Profile search - a damage chart has a distinctive byte profile that
     graphics and code do not: a large minority of zeros ("cannot target"),
     a pile of very small values (infantry chipping armour), and only a thin
     tail of heavy hitters. Slide a window and score against that profile.
"""
import sys, pathlib
from collections import Counter

CAND_N = (16, 18, 20, 24)     # plausible unit counts / row strides


def grid(chunk, n, label, base):
    print(f"\n--- {label} @ 0x{base:06X}, {n} wide ---")
    for r in range(0, len(chunk) // n):
        row = chunk[r * n:(r + 1) * n]
        print(f"  {base + r*n:06X} " + " ".join(f"{b:3d}" for b in row))


def anchor_search(rom):
    print("=== A. anchor search: byte pair (55, 45) ===")
    hits = []
    for i in range(len(rom) - 1):
        if rom[i] == 55 and rom[i + 1] == 45:
            # require the whole prospective matrix to be plausible damage bytes
            win = rom[i:i + 324]
            if len(win) == 324 and max(win) <= 150:
                hits.append(i)
    print(f"{len(hits)} plausible hits: " + ", ".join(f"0x{h:06X}" for h in hits[:20]))
    return hits


def profile_score(win):
    """Lower is better. Compare byte profile against a damage-chart profile."""
    n = len(win)
    c = Counter(win)
    zero = c[0] / n
    tiny = sum(v for k, v in c.items() if 1 <= k <= 20) / n
    big = sum(v for k, v in c.items() if k > 70) / n
    distinct = len(c)
    if max(win) > 150 or distinct < 20:
        return None
    # target: ~30% zeros, ~30% tiny, ~12% heavy
    return abs(zero - 0.30) + abs(tiny - 0.30) + abs(big - 0.12)


def profile_search(rom, n, top=6):
    size = n * n
    best = []
    for i in range(0, len(rom) - size, 2):     # tables are at least 2-aligned
        s = profile_score(rom[i:i + size])
        if s is not None and s < 0.30:
            best.append((s, i))
    best.sort()
    print(f"\n=== B. profile search, {n}x{n} ({size}B): {len(best)} hits ===")
    for s, i in best[:top]:
        print(f"  score={s:.3f}  0x{i:06X}")
    return [i for _, i in best[:top]]


if __name__ == "__main__":
    rom = pathlib.Path(sys.argv[1]).read_bytes()
    hits = anchor_search(rom)
    for h in hits[:6]:
        grid(rom[h:h + 18 * 8], 18, "anchor", h)
    for n in CAND_N:
        profile_search(rom, n)

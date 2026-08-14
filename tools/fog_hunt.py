"""Pin the fog-of-war flag by diffing labelled RAM probes.

    python tools/fog_hunt.py --off fog_off.json --on fog_on.json
    python tools/fog_hunt.py --off off1.json off2.json --on on1.json on2.json

VS mode lets you build the same map twice and toggle fog when you create it,
which turns "search RAM for a flag" into a controlled experiment: capture the
same situation with one setting deliberately changed, and read off the byte
that tracked it. Dump each with the probe on:

    state("C:/tmp/fog_off.json", true)

A byte is a candidate only if it is CONSTANT across every capture sharing a
label and DIFFERENT between the labels. One capture per side cannot tell the
flag apart from a frame counter, so two per side is worth the extra minute --
the tool says how much the second capture bought and refuses to pretend a
single pair narrowed things more than it did.

This is the same labelled-snapshot method that surfaced the weather byte; the
difference is that the comparison happens here rather than in the emulator
console, so a third capture is cheap and the reasoning is reproducible.
"""
import argparse
import json
import pathlib
import sys

# From static analysis of the settings struct at 0x03004310 -- offsets whose
# reads are overwhelmingly `cmp #0`, which is the shape of a boolean. Priors
# only. A prediction that survives the measurement is worth something; one that
# replaces it is worth nothing, so these annotate the result and never filter it,
# and a prior that fails is printed as REFUTED rather than quietly dropped.
PRIORS = {
    0x03004342: "settings+0x32, 80 of 81 reads are compare-against-zero",
    0x03004318: "settings+0x08, 223 of 257 boolean reads, but it also gates the "
                "CO movement table so it is probably more general",
}

# Addresses that must NOT move, as a control: if they did, the two captures
# differed by more than the fog toggle and every candidate below is suspect.
CONTROLS = {
    0x0300433C: "weather",
    0x030036E0: "map width",
    0x030036E1: "map height",
}

# Known IWRAM landmarks, so a hit reads as "in the settings struct" rather than
# as a bare address. Half-open ranges.
LANDMARKS = [
    (0x03001D30, 0x03001D34, "combat luck state"),
    (0x03003600, 0x030036E0, "map row-pointer table"),
    (0x030036E0, 0x030036E2, "map dimensions"),
    (0x03004310, 0x030043B0, "battle settings"),
    (0x03004420, 0x03004440, "turn block"),
    (0x03004500, 0x03004600, "property list"),
]


def landmark(addr):
    for lo, hi, name in LANDMARKS:
        if lo <= addr < hi:
            return f"{name} +0x{addr - lo:02X}"
    return ""


def runs(addrs, gap=4):
    """Group nearby candidates. A contiguous run is a buffer or a mask; an
    isolated byte is a switch. Telling them apart is most of the reading."""
    out = []
    for a in sorted(addrs):
        if out and a - out[-1][-1] <= gap:
            out[-1].append(a)
        else:
            out.append([a])
    return out


def load_probe(path):
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    probe = raw.get("probe")
    if not probe:
        sys.exit(f"{path} has no probe block -- re-dump with state(path, true)")
    return {name: (int(r["base"], 16), bytes.fromhex(r["hex"]))
            for name, r in probe.items()}


def constant_across(group):
    """Offsets whose value is identical in every capture, per region."""
    out = {}
    for region in group[0]:
        blobs = [g[region][1] for g in group]
        n = min(len(b) for b in blobs)
        out[region] = bytes(blobs[0][:n]), [
            i for i in range(n) if all(b[i] == blobs[0][i] for b in blobs)]
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--off", nargs="+", required=True,
                   help="dumps with fog OFF")
    p.add_argument("--on", nargs="+", required=True,
                   help="dumps with fog ON")
    p.add_argument("--max", type=int, default=40,
                   help="candidates to print")
    a = p.parse_args()

    off = [load_probe(f) for f in a.off]
    on = [load_probe(f) for f in a.on]
    regions = sorted(set(off[0]) & set(on[0]))
    if not regions:
        sys.exit("the two groups share no probe regions")

    thin = len(off) < 2 or len(on) < 2
    print(f"fog OFF: {len(off)} capture(s)   fog ON: {len(on)} capture(s)")
    if thin:
        print("!! With one capture on a side, a byte that merely drifts between\n"
              "!! two separately started matches is indistinguishable from the\n"
              "!! flag. Take a second capture per side and re-run.")

    off_const = constant_across(off)
    on_const = constant_across(on)

    total_candidates = 0
    for region in regions:
        base = off[0][region][0]
        off_bytes, off_stable = off_const[region]
        on_bytes, on_stable = on_const[region]
        stable = set(off_stable) & set(on_stable)
        n = min(len(off_bytes), len(on_bytes))

        differing = [i for i in range(n) if off_bytes[i] != on_bytes[i]]
        candidates = [i for i in differing if i in stable]
        noise = len(differing) - len(candidates)
        total_candidates += len(candidates)

        print(f"\n=== {region} @ 0x{base:08X}, {n} bytes ===")
        print(f"  {len(differing):5d} bytes differ between the labels")
        if not thin:
            print(f"  {noise:5d} of those also vary WITHIN a label -- noise, dropped")
        print(f"  {len(candidates):5d} candidate(s)")

        addrs = [base + i for i in candidates]
        moved = {base + i for i in differing}

        print("\n  CONTROLS -- these must not move")
        for addr, name in sorted(CONTROLS.items()):
            if not base <= addr < base + n:
                continue
            bad = addr in moved
            print(f"    0x{addr:08X}  {name:12s} "
                  + ("MOVED -- the captures differed by more than fog"
                     if bad else "unchanged"))

        if any(base <= p < base + n for p in PRIORS):
            print("\n  PREDICTIONS -- from static analysis, checked against this run")
            for addr, why in sorted(PRIORS.items()):
                if not base <= addr < base + n:
                    continue
                if addr in addrs:
                    verdict = "CONFIRMED, it tracked the label"
                elif addr in moved:
                    verdict = "moved, but not consistently -- noise"
                else:
                    verdict = "REFUTED, it did not move at all"
                print(f"    0x{addr:08X}  {verdict}")
                print(f"                  ({why})")

        grouped = runs(addrs)
        singles = [g[0] for g in grouped if len(g) < 4]
        blocks = [g for g in grouped if len(g) >= 4]

        def shape(addr):
            o, v = off_bytes[addr - base], on_bytes[addr - base]
            return "boolean" if {o, v} <= {0, 1} else "value"

        if singles:
            print("\n  FLAG-LIKE -- isolated bytes, the shape of a switch")
            for addr in sorted(singles, key=lambda x: (shape(x) != "boolean", x)):
                o, v = off_bytes[addr - base], on_bytes[addr - base]
                where = landmark(addr)
                print(f"    0x{addr:08X}  {o:3d} -> {v:<3d}  {shape(addr):7s}"
                      + (f"  {where}" if where else ""))

        if blocks:
            print("\n  BUFFER-LIKE -- contiguous runs, so a table or a mask "
                  "rather than a switch")
            for g in blocks:
                lo, hi = g[0], g[-1]
                vals = [on_bytes[x - base] for x in g]
                allzero = all(off_bytes[x - base] == 0 for x in g)
                print(f"    0x{lo:08X}..0x{hi:08X}  {len(g)} bytes"
                      + ("  off is ALL ZERO" if allzero else "")
                      + f"  on: {vals[:6]}{'...' if len(vals) > 6 else ''}")
                w = landmark(lo)
                if w:
                    print(f"                  {w}")

    print()
    if total_candidates == 0:
        print("No byte tracked the label. Either the probe missed the region "
              "(fog may be held in EWRAM or only in the map header), or the "
              "captures differed in when they were taken rather than in fog.")
        return 0

    print("Reading this: a fogged game needs BOTH a switch and a per-tile "
          "visibility mask,\nso expect one flag-like byte and one buffer-like "
          "run, not a single answer.\n"
          "A run whose OFF side is all zero is the shape a mask has when "
          "there is nothing\nto hide.\n")
    print("Next, in order of what it buys:")
    print("  1. Capture a pair on a DIFFERENT map. Anything that survives both "
          "is not\n     a coincidence of one layout, and a mask will change "
          "size with the map.")
    print("  2. Write the best flag candidate mid-match. If fog turns on, it "
          "is the flag\n     and not merely something that moves with it -- "
          "correlation is what this\n     tool can show and causation is not.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

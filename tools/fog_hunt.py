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
# replaces it is worth nothing, so these annotate the result and never filter it.
PRIORS = {
    0x03004342: "settings+0x32: 80 of 81 reads are compare-against-zero",
    0x03004318: "settings+0x08: 223 of 257 reads are compare-against-zero, but "
                "it also gates the CO movement table, so it is probably a "
                "more general flag",
    0x0300433C: "settings+0x2C: the WEATHER byte -- if this moved, the capture "
                "differed by more than fog",
}


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

        for i in candidates[:a.max]:
            addr = base + i
            note = PRIORS.get(addr, "")
            flag = "  <-- PREDICTED" if addr in PRIORS else ""
            print(f"    0x{addr:08X}  off={off_bytes[i]:3d} on={on_bytes[i]:3d}"
                  f"{flag}")
            if note:
                print(f"                  {note}")
        if len(candidates) > a.max:
            print(f"    ... and {len(candidates) - a.max} more")

    print()
    if total_candidates == 0:
        print("No byte tracked the label. Either the probe missed the region "
              "(fog may be held in EWRAM or only in the map header), or the "
              "captures differed in when they were taken rather than in fog.")
    elif total_candidates == 1:
        print("One byte tracked the label. Confirm it by WRITING it in the "
              "emulator mid-match: if fog turns on, that is the flag and not "
              "merely something correlated with it.")
    else:
        print(f"{total_candidates} bytes tracked the label. Narrow with another "
              "pair of captures on a different map, then confirm the survivor "
              "by writing it mid-match.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

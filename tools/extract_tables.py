"""Extract the AW1 damage matrices from the ROM into JSON.

Everything here is derived from the binary, not from memory. The unit ID map
below was recovered structurally (see docs/DERIVATION.md) and is asserted
against the ROM on every run -- if a future ROM revision disagrees, this
crashes rather than silently emitting wrong numbers.

Layout (Advance Wars USA Rev 1, sha1 1505...4d0c):
    primary   weapon matrix @ 0x283B48, 24 rows x 24 cols, u8
    secondary weapon matrix @ 0x283D88, 24 rows x 24 cols, u8
    (a byte-identical second copy of both lives at 0x3F4BBC / 0x3F4DFC)
    dived-defender table    @ 0x283FC8, 24 u8, indexed by ATTACKER id alone
Value semantics: base damage in internal-HP points (100 = a full-health unit).
0 means "this weapon cannot target that unit".

The dived table replaces the PRIMARY lookup when the defender's flags byte
carries bit 0x20 (the Dive state, set at 0x08066E90 and cleared by Rise at
0x08066EAC; the gate is 0x08022E12). Cruiser 90 and Sub 55 -- the same
values as the surfaced matrix -- and zero for the other 22 attackers, so
diving does not soften the hunters' shots, it deletes everyone else's. No
unit has a secondary against a Sub, which makes the primary-only gate
complete. DERIVATION.md 31.
"""
import json, hashlib, pathlib, sys

N = 24
PRIMARY = 0x283B48
SECONDARY = 0x283D88
PRIMARY_COPY = 0x3F4BBC
SECONDARY_COPY = 0x3F4DFC
DIVED = 0x283FC8
EXPECT_SHA1 = "15053499d5b3f49128a941d7f2d84876f5424d0c"

# Internal unit IDs. Gaps are vestigial slots with no unit behind them.
UNIT_IDS = {
    0: "Infantry", 1: "Mech", 2: "MdTank", 4: "Tank", 5: "Recon", 6: "APC",
    9: "Artillery", 10: "Rockets", 13: "AntiAir", 14: "Missiles",
    15: "Fighter", 16: "Bomber", 18: "BCopter", 19: "TCopter",
    20: "Battleship", 21: "Cruiser", 22: "Lander", 23: "Sub",
}
GAPS = [3, 7, 8, 11, 12, 17]

# Structural invariants that pinned the mapping. Each is (matrix, att, def,
# expected, why). If any fails, the mapping is wrong -- fail loudly.
ASSERTIONS = [
    ("primary",   0,  0,   0, "Infantry has no primary weapon"),
    ("primary",   1,  0,   0, "Mech bazooka cannot target foot soldiers"),
    ("primary",   1,  2,  15, "Mech bazooka vs Md Tank"),
    ("primary",   2,  4,  85, "Md Tank cannon vs Tank"),
    ("primary",   4,  2,  15, "Tank cannon vs Md Tank (asymmetry pins 2=MdTank)"),
    ("primary",   4,  4,  55, "Tank vs Tank"),
    ("primary",   5,  0,   0, "Recon has no primary (MG only)"),
    ("primary",   6,  0,   0, "APC is unarmed"),
    ("primary",  21, 23,  90, "Cruiser depth charges vs Sub -- pins col 23=Sub"),
    ("primary",  23, 20,  55, "Sub torpedoes vs Battleship"),
    ("primary",  22,  0,   0, "Lander is unarmed"),
    ("primary",  19,  0,   0, "T Copter is unarmed"),
    ("secondary", 0,  0,  55, "Infantry vs Infantry -- the anchor"),
    ("secondary", 0,  1,  45, "Infantry vs Mech -- the anchor"),
    ("secondary", 2,  0, 105, "Md Tank MG vs Infantry"),
    ("secondary", 4,  0,  75, "Tank MG vs Infantry"),
    ("secondary", 6,  0,   0, "APC has no secondary either"),
]


def read_matrix(rom, base):
    return [list(rom[base + r * N: base + (r + 1) * N]) for r in range(N)]


def main(rom_path, out_path):
    rom = pathlib.Path(rom_path).read_bytes()
    sha1 = hashlib.sha1(rom).hexdigest()
    if sha1 != EXPECT_SHA1:
        sys.exit(f"ROM sha1 {sha1} != expected {EXPECT_SHA1}; offsets unverified for this build")

    mats = {"primary": read_matrix(rom, PRIMARY),
            "secondary": read_matrix(rom, SECONDARY)}
    copies = {"primary": read_matrix(rom, PRIMARY_COPY),
              "secondary": read_matrix(rom, SECONDARY_COPY)}
    discrepancies = []
    for k in mats:
        for a in range(N):
            for d in range(N):
                if mats[k][a][d] != copies[k][a][d]:
                    discrepancies.append({
                        "matrix": k, "attacker_id": a, "defender_id": d,
                        "attacker": UNIT_IDS.get(a, f"slot{a}"),
                        "defender": UNIT_IDS.get(d, f"slot{d}"),
                        "main_copy": mats[k][a][d], "alt_copy": copies[k][a][d],
                    })
    if discrepancies:
        print(f"WARNING: {len(discrepancies)} bytes differ between the two ROM copies.")
        for x in discrepancies:
            print(f"   {x['matrix']:9s} {x['attacker']:>10s} vs {x['defender']:<10s} "
                  f"main={x['main_copy']:3d} alt={x['alt_copy']:3d}")
        print("   RESOLVED: tools/find_xrefs.py shows the alternate copies at 0x083F4BBC/")
        print("   0x083F4DFC have ZERO literal-pool references anywhere in the ROM, while")
        print("   the main copies are referenced by 10 code sites. The alternates are dead")
        print("   data. Using the main copy; an out-of-ammo Fighter cannot attack.")
    else:
        print("both ROM copies byte-identical")

    for mat, a, d, want, why in ASSERTIONS:
        got = mats[mat][a][d]
        if got != want:
            sys.exit(f"ASSERTION FAILED {mat}[{a}][{d}]={got}, expected {want} ({why})")
    print(f"{len(ASSERTIONS)} structural assertions passed")

    dived = list(rom[DIVED:DIVED + N])
    if not (dived[21] == 90 and dived[23] == 55
            and all(v == 0 for i, v in enumerate(dived) if i not in (21, 23))):
        sys.exit(f"ASSERTION FAILED: dived table should be Cruiser 90 / Sub 55 "
                 f"and zero elsewhere, got {dived}")
    print("dived-defender table matches: Cruiser 90, Sub 55, 22 zeroes")

    for g in GAPS:
        for mat in mats.values():
            if any(mat[g]):
                print(f"  note: gap slot {g} has a nonzero row (vestigial data, unreferenced)")

    out = {
        "game": "Advance Wars (USA) Rev 1",
        "rom_sha1": sha1,
        "provenance": {
            "method": "extracted from ROM binary",
            "primary_offset": hex(PRIMARY),
            "secondary_offset": hex(SECONDARY),
            "duplicate_offsets": [hex(PRIMARY_COPY), hex(SECONDARY_COPY)],
            "stride": N,
        },
        "primary_raw": mats["primary"],
        "secondary_raw": mats["secondary"],
        # Named view for humans and for the engine.
        "primary": {UNIT_IDS[a]: {UNIT_IDS[d]: mats["primary"][a][d]
                                  for d in UNIT_IDS} for a in UNIT_IDS},
        "secondary": {UNIT_IDS[a]: {UNIT_IDS[d]: mats["secondary"][a][d]
                                    for d in UNIT_IDS} for a in UNIT_IDS},
        # The dived-defender override: attacker -> base vs a defender whose
        # flags carry bit 0x20. Replaces the PRIMARY lookup only (0x08022E12);
        # measured live in harness/mesen_dive.lua / _dive2.lua.
        "dived_raw": dived,
        "dived": {UNIT_IDS[a]: dived[a] for a in UNIT_IDS},
    }
    # THE ROM HALF ONLY, and nothing else touched.
    #
    # This script reads a cartridge. It cannot know whether the formula wrapped
    # around these tables reproduces what the game did, and it did not write the
    # open questions, the code analysis or the unit id map either -- those
    # arrived later, by hand and by other tools. It rewrote the file wholesale
    # regardless, which made re-extracting a destructive act twice over: it
    # re-asserted `verified_against_emulator: True` whatever the state of the
    # model, and it dropped six hand-curated blocks on the floor.
    #
    # So: update the keys this script actually derives, in place, and leave
    # every other key exactly as found. The measurement-derived fields belong to
    # tools/verify_corpus.py, which computes them by replaying the corpus.
    ROM_OWNED = ("game", "rom_sha1", "provenance", "primary_raw",
                 "secondary_raw", "primary", "secondary",
                 "dived_raw", "dived")
    MEASURED = ("verified_against_emulator", "verification")
    out_file = pathlib.Path(out_path)
    if out_file.exists():
        doc = json.loads(out_file.read_text(encoding="utf-8"))
        kept = {k: doc.get("provenance", {})[k] for k in MEASURED
                if k in doc.get("provenance", {})}
        for key in ROM_OWNED:
            doc[key] = out[key]
        doc["provenance"].update(kept)
        carried = [k for k in doc if k not in ROM_OWNED]
        print(f"kept {len(carried)} block(s) this script does not own: "
              f"{', '.join(carried)}")
        print(f"kept measurement fields: {', '.join(kept) or 'NONE'}"
              " -- tools/verify_corpus.py --write recomputes them")
    else:
        doc = out
        print("no existing file: the measurement-derived fields are ABSENT, so "
              "resolve() will refuse advice until you run "
              "tools/verify_corpus.py --write")
    out_file.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out_path}")

    order = [UNIT_IDS[i] for i in sorted(UNIT_IDS)]
    print("\nprimary weapon matrix (rows=attacker, cols=defender, '.'=cannot target)")
    print(f"{'':11s}" + "".join(f"{n[:4]:>5s}" for n in order))
    for a in sorted(UNIT_IDS):
        cells = "".join(f"{(mats['primary'][a][d] or '.'):>5}" for d in sorted(UNIT_IDS))
        print(f"{UNIT_IDS[a]:11s}{cells}")
    print("\nsecondary weapon matrix")
    print(f"{'':11s}" + "".join(f"{n[:4]:>5s}" for n in order))
    for a in sorted(UNIT_IDS):
        cells = "".join(f"{(mats['secondary'][a][d] or '.'):>5}" for d in sorted(UNIT_IDS))
        print(f"{UNIT_IDS[a]:11s}{cells}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

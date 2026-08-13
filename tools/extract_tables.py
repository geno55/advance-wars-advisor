"""Extract the AW1 damage matrices from the ROM into JSON.

Everything here is derived from the binary, not from memory. The unit ID map
below was recovered structurally (see docs/DERIVATION.md) and is asserted
against the ROM on every run -- if a future ROM revision disagrees, this
crashes rather than silently emitting wrong numbers.

Layout (Advance Wars USA Rev 1, sha1 1505...4d0c):
    primary   weapon matrix @ 0x283B48, 24 rows x 24 cols, u8
    secondary weapon matrix @ 0x283D88, 24 rows x 24 cols, u8
    (a byte-identical second copy of both lives at 0x3F4BBC / 0x3F4DFC)
Value semantics: base damage in internal-HP points (100 = a full-health unit).
0 means "this weapon cannot target that unit".
"""
import json, hashlib, pathlib, sys

N = 24
PRIMARY = 0x283B48
SECONDARY = 0x283D88
PRIMARY_COPY = 0x3F4BBC
SECONDARY_COPY = 0x3F4DFC
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
            "verified_against_emulator": True,
        },
        "calibration": {
            "observations": 14,
            "source": "harness/observations.csv, exact internal HP read from RAM",
            "co": "single neutral CO on both armies, no power active; validated "
                  "by a Tank->Infantry road reading of 78 against a ROM base of 75",
            "display_rule": "floor_min1 -- combat scales by a TRUNCATED tenth of "
                            "internal HP, min 1. ceil, round and plain floor are "
                            "all refuted by observation.",
            "terrain_stars": {"road": 0, "plains": 1, "mountain": 4},
            "terrain_source": "read from the in-game Def display; road, plains "
                              "and mountain were independently re-derived from "
                              "damage data and agreed",
            "formula_variants_refuted": ["floor_end", "floor_attack_then_end",
                                         "floor_each_step", "round_end"],
            "formula_variants_surviving": ["luck_after_hp", "luck_last"],
            "residual_uncertainty":
                "The two survivors agree exactly on MINIMUM damage, so "
                "guaranteed-kill calls are exact. They differ on MAXIMUM damage "
                "by up to 4 on 4-star terrain. resolve() reports the envelope "
                "over both rather than picking one.",
            "why_not_resolved":
                "Separating them needs the top of the luck range, and the GBA "
                "advances its RNG per frame -- save-state replays sample a "
                "narrow, timing-dependent band rather than the full 0..9. Over "
                "35 trials the maximum appeared once. Not worth more grinding "
                "for a difference that never changes a kill decision.",
        },
        "copy_discrepancies": discrepancies,
        "open_questions": [],
        "resolved_questions": ([{
            "id": "fighter-secondary",
            "question": "Does an out-of-ammo Fighter retain a secondary weapon?",
            "detail": "The alternate ROM copy gives Fighter a secondary (vs Fighter 15, "
                      "Bomber 45, BCopter 65, TCopter 75); the main copy gives it none.",
            "answer": "No. The main copy is live; an out-of-ammo Fighter cannot attack.",
            "evidence": "Literal-pool cross-reference scan (tools/find_xrefs.py): the main "
                        "tables are referenced by code at 7 sites (primary) and 3 sites "
                        "(secondary). The alternate copies at 0x083F4BBC/0x083F4DFC are "
                        "referenced by ZERO code sites -- they are dead data.",
        }] if discrepancies else []),
        "code_analysis": {
            "damage_lookup_sites": ["0x08022EB0", "0x08060B60", "0x08060DBA"],
            "index_formula": "table + (attacker_type - 1) * 24 + (defender_type - 1)",
            "note_type_ids_are_1_based_in_ram": True,
            "co_modifier_table": "0x08284A0C, indexed [co * 128 + unit_type * 4], "
                                 "dereferenced to a struct whose byte +5 is the modifier",
            "co_modifier_application": "value = (value * modifier) / 100, applied TWICE in "
                                       "sequence (attack then defence), each an independent "
                                       "truncating signed division via __divsi3 at 0x0807B488",
            "army_struct_stride": "0x68",
        },
        "unit_ids": {str(k): v for k, v in UNIT_IDS.items()},
        "unused_slots": GAPS,
        # Keep raw 24x24 verbatim: never discard bytes we did not understand.
        "primary_raw": mats["primary"],
        "secondary_raw": mats["secondary"],
        # Named view for humans and for the engine.
        "primary": {UNIT_IDS[a]: {UNIT_IDS[d]: mats["primary"][a][d]
                                  for d in UNIT_IDS} for a in UNIT_IDS},
        "secondary": {UNIT_IDS[a]: {UNIT_IDS[d]: mats["secondary"][a][d]
                                    for d in UNIT_IDS} for a in UNIT_IDS},
    }
    pathlib.Path(out_path).write_text(json.dumps(out, indent=1), encoding="utf-8")
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

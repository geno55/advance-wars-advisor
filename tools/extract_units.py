"""Extract the AW1 unit stats table from the ROM into JSON.

This is the table milestone 3 was blocked on: movement points, movement type,
min/max range, cost, vision, fuel, ammo, and per-turn fuel burn -- for all 18
units, from the binary.

HOW IT WAS FOUND, reproducibly, and it is the same trick as the terrain table
in DERIVATION.md section 9. The unit name strings are plain ASCII at 0x282CC8.
Searching the ROM for the four little-endian bytes of each string's mapped
address returns exactly one reference per name, and those references are spaced
**0x70 apart** -- with double and triple gaps falling exactly on unit ids
3, 7, 8, 11, 12 and 17, the six vestigial slots already known from the damage
matrices. Nothing else lines up that way.

    table @ 0x2830C8, 24 records x 0x70

24 * 0x70 = 0xA80, and 0x2830C8 + 0xA80 = 0x283B48 -- which is exactly where
the primary damage matrix begins. The table ends where the next known table
starts, so the base, the stride and the count are all pinned by construction.

FIELD MAP (offsets that are identified; the record has 0x70 bytes and plenty
is still unknown -- see UNIDENTIFIED at the bottom of the emitted JSON)

    +0x00  u32  name string pointer
    +0x0C  u8   movement points
    +0x0D  u8   max ammo
    +0x0E  u8   vision
    +0x10  u8   min range   (0 for unarmed)
    +0x11  u8   max range   (0 for unarmed)
    +0x12  u8   max fuel
    +0x14  u8   unit class bitfield: 1 foot, 2 tires, 4 treads, 16 air, 32 naval
    +0x17  u8   target class bitfield: 1 air, 2 naval, 4 ground, 8 sub
    +0x18  u16  cost / 10
    +0x38  u8   fuel burned per turn (0 ground, 2 copters, 5 planes, 1 naval)
                -- the first of a 20-byte per-terrain block, uniform; see
                tools/extract_supply.py, which also reads +0x24 (service)
    +0x4C  s8[20] per-terrain CAN-STAND block: the drop-tile validity check at
                0x0802564A reads stats[cargo]+0x4C+terrain and accepts >= 0
                (DERIVATION 35). Signs agree with the clear movement table
                on every real terrain (asserted); the magnitudes are NOT the
                clear costs everywhere (tires read 4 on Wood where the
                movement table says 3) and are not used for movement.
    +0x60  u8   movement type, indexing aw1_movecost.json movement_types

ON canMoveAndFire: there is no such flag anywhere in the record. AW1's rule is
that a unit with range beyond 1 cannot fire after moving, so the behaviour
falls out of max_range and needs no per-unit branch -- which is why no flag
exists to find. It is emitted as a DERIVED field and labelled as such, because
the rule itself has not been read out of the combat code.
"""
import json, hashlib, pathlib, sys, struct

EXPECT_SHA1 = "15053499d5b3f49128a941d7f2d84876f5424d0c"
ROM_BASE = 0x08000000
BASE, STRIDE, N = 0x2830C8, 0x70, 24
DAMAGE_PRIMARY = 0x283B48        # the table must end exactly here

UNIT_IDS = {
    0: "Infantry", 1: "Mech", 2: "MdTank", 4: "Tank", 5: "Recon", 6: "APC",
    9: "Artillery", 10: "Rockets", 13: "AntiAir", 14: "Missiles",
    15: "Fighter", 16: "Bomber", 18: "BCopter", 19: "TCopter",
    20: "Battleship", 21: "Cruiser", 22: "Lander", 23: "Sub",
}
GAPS = [3, 7, 8, 11, 12, 17]

# The name string each record points at, as the ROM spells it.
ROM_NAMES = {
    0: "Infantry", 1: "Mech", 2: "Md Tank", 4: "Tank", 5: "Recon", 6: "APC",
    9: "Artlry", 10: "Rockets", 13: "A-Air", 14: "Missiles", 15: "Fighter",
    16: "Bomber", 18: "B Cptr", 19: "T Cptr", 20: "B Ship", 21: "Cruiser",
    22: "Lander", 23: "Sub",
}

# Build-menu prices. Observable in game, used here only as anchors, and every
# one is re-asserted on each run -- same discipline as the damage anchors.
COSTS = {
    "Infantry": 1000, "Mech": 3000, "Recon": 4000, "APC": 5000, "Tank": 7000,
    "Artillery": 6000, "AntiAir": 8000, "BCopter": 9000, "Missiles": 12000,
    "Lander": 12000, "Rockets": 15000, "MdTank": 16000, "Cruiser": 18000,
    "Fighter": 20000, "Sub": 20000, "Bomber": 22000, "Battleship": 28000,
    "TCopter": 5000,
}

# Read out of live RAM long before this table was located (the old partial
# aw1_unit_stats.json). Asserting them ties the ROM table to the running game.
RAM_OBSERVED = {
    "Infantry": (99, 0, 2), "Mech": (70, 3, 2), "Tank": (70, 9, 3),
    "Recon": (80, 0, 5), "APC": (70, 0, 1), "Artillery": (50, 9, None),
}

UNARMED = {"APC", "TCopter", "Lander"}
RANGES = {"Artillery": (2, 3), "Rockets": (3, 5), "Missiles": (3, 5),
          "Battleship": (2, 6)}

CLASS_BITS = {1: "foot", 2: "tires", 4: "treads", 16: "air", 32: "naval"}
TARGET_BITS = {1: "air", 2: "naval", 4: "ground", 8: "sub"}


def main(rom_path, out_path):
    rom = pathlib.Path(rom_path).read_bytes()
    sha1 = hashlib.sha1(rom).hexdigest()
    if sha1 != EXPECT_SHA1:
        sys.exit(f"ROM sha1 {sha1} != expected {EXPECT_SHA1}; "
                 "offsets unverified for this build")

    checks = 0

    def check(cond, why):
        nonlocal checks
        if not cond:
            sys.exit(f"ASSERTION FAILED: {why}")
        checks += 1

    def cstr(off):
        end = off
        while rom[end]:
            end += 1
        return rom[off:end].decode("ascii")

    check(BASE + N * STRIDE == DAMAGE_PRIMARY,
          f"the table should end exactly at the damage matrix {DAMAGE_PRIMARY:#x}")

    mc = json.loads((pathlib.Path(out_path).parent /
                     "aw1_movecost.json").read_text(encoding="utf-8"))
    move_types = mc["movement_types"]

    units = {}
    for uid, name in UNIT_IDS.items():
        r = BASE + uid * STRIDE
        rec = rom[r:r + STRIDE]
        name_ptr = struct.unpack_from("<I", rec, 0)[0]
        rom_name = cstr(name_ptr - ROM_BASE)
        check(rom_name == ROM_NAMES[uid],
              f"id {uid} should name itself {ROM_NAMES[uid]!r}, got {rom_name!r}")

        cost10 = struct.unpack_from("<H", rec, 0x18)[0]
        u = {
            "id": uid,
            "rom_name": rom_name,
            "cost": cost10 * 10,
            "move": rec[0x0C],
            "move_type": move_types[rec[0x60]],
            "move_type_index": rec[0x60],
            "min_range": rec[0x10],
            "max_range": rec[0x11],
            "vision": rec[0x0E],
            "max_fuel": rec[0x12],
            "max_ammo": rec[0x0D],
            "fuel_per_turn": rec[0x38],
            "unit_class": CLASS_BITS.get(rec[0x14], f"raw{rec[0x14]}"),
            "targeted_as": TARGET_BITS.get(rec[0x17], f"raw{rec[0x17]}"),
            "armed": rec[0x11] > 0,
            # DERIVED, not a field in the record -- see the module docstring.
            # None, not True, for unarmed units: their max_range is 0, so the
            # `max_range <= 1` test would otherwise hand an APC permission to
            # fire after moving. Absence of a weapon is not a direct weapon.
            "can_move_and_fire": (rec[0x11] <= 1) if rec[0x11] > 0 else None,
        }

        u["can_stand"] = [t for t in range(20)
                          if struct.unpack_from("<b", rec, 0x4C + t)[0] >= 0]

        check(u["cost"] == COSTS[name],
              f"{name} cost {u['cost']}, build menu says {COSTS[name]}")
        check(rec[0x60] < len(move_types),
              f"{name} movement type index {rec[0x60]} out of range")
        check(1 <= u["move"] <= 9, f"{name} movement {u['move']} implausible")

        if name in RAM_OBSERVED:
            fuel, ammo, vis = RAM_OBSERVED[name]
            check(u["max_fuel"] == fuel,
                  f"{name} fuel {u['max_fuel']}, live RAM said {fuel}")
            check(u["max_ammo"] == ammo,
                  f"{name} ammo {u['max_ammo']}, live RAM said {ammo}")
            if vis is not None:
                check(u["vision"] == vis,
                      f"{name} vision {u['vision']}, live RAM said {vis}")

        if name in UNARMED:
            check((u["min_range"], u["max_range"]) == (0, 0),
                  f"{name} is unarmed and should have range 0/0")
            check(u["max_ammo"] == 0, f"{name} is unarmed and should hold no ammo")
        elif name in RANGES:
            check((u["min_range"], u["max_range"]) == RANGES[name],
                  f"{name} range {u['min_range']}-{u['max_range']}, "
                  f"expected {RANGES[name]}")
        else:
            check((u["min_range"], u["max_range"]) == (1, 1),
                  f"{name} is direct-combat and should have range 1/1, "
                  f"got {u['min_range']}-{u['max_range']}")

        # -- transports: +0x20 points at a cargo struct, 0x30 apart -------
        cargo_ptr = struct.unpack_from("<I", rec, 0x20)[0]
        if cargo_ptr:
            c = cargo_ptr - ROM_BASE
            u["capacity"] = rom[c]
            u["carries"] = [UNIT_IDS[k - 1] for k in range(24)
                            if rom[c + 1 + k] and (k - 1) in UNIT_IDS]
            u["carries_vestigial_ids"] = [k - 1 for k in range(24)
                                          if rom[c + 1 + k] and (k - 1) not in UNIT_IDS]
            u["cargo_struct"] = hex(c)
            # +0x1A..+0x2D: the per-terrain UNLOAD-FROM table the Drop
            # predicate reads at 0x080259FC (cargo struct +0x1A + terrain):
            # nonzero means the transport may unload while standing there.
            u["unload_from"] = [t for t in range(20) if rom[c + 0x1A + t]]
            check(1 <= u["capacity"] <= 2,
                  f"{name} capacity {u['capacity']} outside 1..2")
            check(bool(u["carries"]), f"{name} is a transport that carries nothing")
            check(bool(u["unload_from"]),
                  f"{name} is a transport that can unload nowhere")
        else:
            u["capacity"] = 0
            u["carries"] = []

        units[name] = u

    # -- cross-check against the independently extracted damage matrices ---
    dmg = json.loads((pathlib.Path(out_path).parent /
                      "aw1_damage.json").read_text(encoding="utf-8"))
    for name, u in units.items():
        prim = dmg["primary"].get(name, {})
        sec = dmg["secondary"].get(name, {})
        armed = any(prim.values()) or any(sec.values())
        check(u["armed"] == (name not in UNARMED),
              f"{name}: the armed flag derived from max_range disagrees with "
              "the unarmed list")
        check(armed == (name not in UNARMED),
              f"{name}: unit table says "
              f"{'unarmed' if name in UNARMED else 'armed'}, damage matrices "
              f"say {'armed' if armed else 'unarmed'}")
        if u["max_ammo"] == 0:
            check(not any(prim.values()),
                  f"{name} holds no ammo but has a nonzero primary weapon row")
    # -- cargo sets, checked against groupings derived from this same table,
    # -- so nothing here depends on knowing the game from outside ---------
    def by_movetype(*kinds):
        return {n for n, v in units.items() if v["move_type"] in kinds}

    transports = {n for n, v in units.items() if v["capacity"]}
    check(transports == {"APC", "TCopter", "Lander", "Cruiser"},
          f"expected exactly four transports, got {sorted(transports)}")

    foot = by_movetype("Infantry", "Mech")
    ground = by_movetype("Infantry", "Mech", "Treads", "Tires")
    # copters are the air units that burn 2 fuel a turn; planes burn 5
    copters = {n for n, v in units.items()
               if v["move_type"] == "Air" and v["fuel_per_turn"] == 2}

    for name, want, why in (("APC", foot, "the foot-soldier move types"),
                            ("TCopter", foot, "the foot-soldier move types"),
                            ("Lander", ground, "every ground move type"),
                            ("Cruiser", copters, "the air units that burn 2 fuel/turn")):
        got = set(units[name]["carries"])
        check(got == want,
              f"{name} carries {sorted(got)}, expected {why}: {sorted(want)}")

    for name, v in units.items():
        if name not in transports:
            check(not v["carries"],
                  f"{name} is not a transport but lists cargo")

    # -- the can-stand block agrees in SIGN with the clear movement table on
    # -- every real terrain id (vestigial ids 15..18 and Out differ, unused)
    clear = next(t for t in mc["tables"] if t["weather"] == "Clear")
    real_terrains = [int(k) for k in mc["terrain_columns"]]
    for name, v in units.items():
        row = clear["costs"][v["move_type"]]
        for t in real_terrains:
            check((t in v["can_stand"]) == (row[t] != 255),
                  f"{name}: can-stand block disagrees with the clear movement "
                  f"table on terrain {t}")
    # unload-from: the APC's set is inside the TCopter's (the copter adds
    # River and Mountain), the Lander unloads from Port and Shoal only, and
    # no transport unloads from a terrain it cannot stand on itself
    check(set(units["APC"]["unload_from"]) < set(units["TCopter"]["unload_from"]),
          "APC unload-from should be a strict subset of the TCopter's")
    check(units["Lander"]["unload_from"] == [11, 13],
          f"Lander should unload from Port and Shoal, got "
          f"{units['Lander']['unload_from']}")
    # The Cruiser's table flags River and Shoal as well as Sea/Port/Reef --
    # two terrains no ship stands on, so the table was authored over the
    # whole terrain space (like the Lander's cargo mask) and is not derived
    # from the transport's own passability. Recorded, not asserted.
    check(set(units["Cruiser"]["unload_from"]) >= {7, 11, 19},
          "Cruiser should unload from Sea, Port and Reef at least")

    print(f"{checks} structural assertions passed")
    print("cross-check passed: armed/unarmed agrees with the damage matrices")
    print("cargo sets match groupings derived from the unit table itself")

    out = {
        "_comment": [
            "Unit stats, extracted from the ROM table at 0x2830C8 (24 records,",
            "stride 0x70). Regenerate with tools/extract_units.py; it exits",
            "non-zero on a ROM sha1 mismatch or any failed assertion.",
            "",
            "SUPERSEDES the earlier hand-collected file, which had vision for 5",
            "of 18 units and fuel/ammo for 6, and noted that the ROM table 'was",
            "not found -- searched every stride 1-40 in two id spaces as u8 and",
            "u16'. The stride is 0x70, outside that search window. Searching for",
            "REFERENCES to the name strings found it immediately; searching for",
            "the values themselves never would have.",
            "",
            "cost is stored as cost/10 in a u16 at +0x18.",
            "",
            "can_move_and_fire is DERIVED (max_range <= 1), not read from the",
            "record. No such flag exists in the table -- the behaviour falls out",
            "of range, so there is no per-unit branch to find. The rule itself",
            "has not been read out of the combat code, so treat it as a",
            "well-supported inference rather than an extracted fact.",
        ],
        "game": "Advance Wars (USA) Rev 1",
        "rom_sha1": sha1,
        "provenance": {
            "method": "extracted from ROM binary",
            "table_offset": hex(BASE),
            "stride": hex(STRIDE),
            "records": N,
            "found_by": "one reference per unit name string, spaced 0x70 apart, "
                        "with gaps on the six vestigial unit ids",
            "extent_pinned_by": f"{hex(BASE)} + 24*{hex(STRIDE)} == "
                                f"{hex(DAMAGE_PRIMARY)}, the damage matrix base",
            "cross_checked_against": ["aw1_damage.json", "aw1_movecost.json",
                                      "live RAM observations"],
            "complete": True,
        },
        "field_offsets": {
            "+0x00": "u32 name string pointer", "+0x0C": "u8 movement points",
            "+0x0D": "u8 max ammo", "+0x0E": "u8 vision",
            "+0x10": "u8 min range", "+0x11": "u8 max range",
            "+0x12": "u8 max fuel",
            "+0x14": "u8 unit class bitfield (1 foot, 2 tires, 4 treads, 16 air, 32 naval)",
            "+0x17": "u8 target class bitfield (1 air, 2 naval, 4 ground, 8 sub)",
            "+0x18": "u16 cost/10",
            "+0x20": "u32 pointer to a cargo struct, nonzero only on transports",
            "+0x38": "u8 fuel burned per turn (first of a uniform 20-byte "
                     "per-terrain block)",
            "+0x4C": "s8[20] per-terrain can-stand block, read by the drop "
                     "validity check (>= 0 passable); sign agrees with the "
                     "clear movement table on real terrains",
            "+0x60": "u8 movement type, indexes aw1_movecost movement_types",
        },
        "cargo_struct": {
            "_comment": "Four structs 0x30 apart, reached from unit +0x20. "
                        "Exactly the shape the design called for -- a capacity "
                        "plus an allowed-cargo-type mask, no per-unit-type code.",
            "+0x00": "u8 capacity",
            "+0x01..+0x18": "24-byte allowed-cargo mask, indexed by the 1-BASED "
                            "unit type (entry k is unit id k-1), the same "
                            "off-by-one as the damage matrices",
            "+0x1A..+0x2D": "per-terrain UNLOAD-FROM table, read by the Drop "
                            "predicate at 0x080259FC: nonzero = may unload "
                            "while standing on that terrain (DERIVATION 35)",
            "+0x19, +0x2E..+0x2F": "unidentified, zero on all four",
            "note": "Lander's mask also sets four vestigial ids (3, 7, 8, 12). "
                    "Harmless -- no unit has those ids -- but it means the mask "
                    "was authored over the full 24-slot space, not the 18 real "
                    "units, so do not use it to infer which ids are real.",
        },
        "unidentified": [
            "+0x04 and +0x08 are two more string pointers, not decoded",
            "+0x0F holds 1..4 with no pattern that matches any known grouping",
            "+0x13 and +0x1D are nonzero for exactly the same eight units "
            "(Infantry, Mech, MdTank, Tank, Recon, Fighter, BCopter, Cruiser) "
            "-- a flag and a value for one feature, but not the secondary-weapon "
            "set, which would exclude Fighter",
            "+0x15, +0x16, +0x1A, +0x1C, +0x1E unknown",
            "+0x38..+0x4B is the per-terrain burn block and +0x24..+0x37 the "
            "per-terrain service block -- both read by tools/extract_supply.py",
        ],
        "units": units,
    }
    pathlib.Path(out_path).write_text(json.dumps(out, indent=2) + "\n",
                                      encoding="utf-8")
    print(f"wrote {out_path}: {len(units)} units, all fields populated")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: extract_units.py <rom> <out.json>")
    main(sys.argv[1], sys.argv[2])

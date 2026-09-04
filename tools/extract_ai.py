"""Extract the tables the game's CPU reads into data/aw1_ai.json.

The AI (0x0805E000..0x0806A000, DERIVATION 44/45) decides from a handful of
ROM tables and a per-mission "profile". This pulls every one of them out of
the binary so engine/cpu.py can predict from data, not from numbers typed in
by hand. Layout, all in Advance Wars (USA) (Rev 1):

    unit stats      0x283058 + 0x70 * type, the AI's bytes:
                    +0x0C move, +0x0F cost class (1..4, indexes the counter
                    weights), +0x10/+0x11 min/max range, +0x12 max fuel,
                    +0x14 move class (1 foot, 2 tyres, 4 treads, 16 air,
                    32 sea), +0x15 AI class (the sub-phase lists filter on
                    it), +0x16 what it threatens, +0x17 what threatens it
    0x3B7DB4 [20]   terrain ids that are factories (1 base/airport, 8 port)
    0x3B7DC8 [20]   terrain ids that are properties
    0x3B7DDC [20]   capture bonus by terrain id (HQ handled apart, +8)
    0x3B7DF0 [26]   behaviour code by unit type (0x08061A64 -> 0x030050DC)
    0x3B7E09/E22/E3B per-type bytes the transport and target lists read
    0x3B7ED8 [8]    the drop direction pairs (dx, dy) 0x08066D64 walks
    0x284314 [32]   terrain value the from-tile picker and movers score
    0x11A8E8 [6]    counter-damage weight by attacker cost class
    0x11A900..      the mode-4 hunt multipliers (5 bytes), the fuel-band
                    thresholds (10 u16), the transport-load class table (8)
    0x11A97C        profiles, 0x130 bytes each: 16 header bytes then one
                    12-byte record per unit type at +4+12*type
    0x2872C8        profile index by [12 * mission_row + co_id]
    0x287478        mission records, 60 bytes: +0x22 (0xFF = VS), +0x23 row
    0x284A0C        CO records, 0x124 each: +0x14 the AI power predicate,
                    +0x40 + 4*(type + 32*power_state) -> a stats record with
                    +5 attack %, +6 defence %, +7 move bonus, +9 range
                    bonus. The luck pair and the all-units multipliers are
                    header fields of the CO record (data/aw1_co.json via
                    engine/co.py), not per-type ones.

    python tools/extract_ai.py [rom]
"""
import json
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ROM = ROOT.parent / "Advance Wars (USA) (Rev 1).gba"
OUT = ROOT / "data" / "aw1_ai.json"
BASE = 0x08000000

TYPE_NAMES = {1: "Infantry", 2: "Mech", 3: "MdTank", 5: "Tank", 6: "Recon",
              7: "APC", 10: "Artillery", 11: "Rockets", 14: "AntiAir",
              15: "Missiles", 16: "Fighter", 17: "Bomber", 19: "BCopter",
              20: "TCopter", 21: "Battleship", 22: "Cruiser", 23: "Lander",
              24: "Sub"}

SUBPHASE_NAMES = {
    0x08063C7C: "power", 0x080638A4: "foot_capture", 0x08063964: "class3",
    0x08063A1C: "indirect_fire", 0x08063ADC: "air_strike", 0x08063B8C: "direct",
    0x08063C4C: "clear_targeted", 0x08063D6C: "foot", 0x08063CAC: "transport_empty",
    0x08063E24: "transport", 0x08063EE4: "transport_loaded", 0x08063FA4: "indirect_move",
    0x08064064: "lander", 0x0806411C: "apc_supply", 0x080641C0: "end",
}


def u8(rom, a):
    return rom[a - BASE]


def s8(rom, a):
    return struct.unpack("b", rom[a - BASE:a - BASE + 1])[0]


def u16(rom, a):
    return struct.unpack_from("<H", rom, a - BASE)[0]


def u32(rom, a):
    return struct.unpack_from("<I", rom, a - BASE)[0]


def extract(rom: bytes) -> dict:
    stats = {}
    for t, name in TYPE_NAMES.items():
        r = rom[0x283058 + 0x70 * t: 0x283058 + 0x70 * t + 0x70]
        stats[name] = {"type": t, "move": r[0xC], "cost_class": r[0xF],
                       "min_range": r[0x10], "max_range": r[0x11],
                       "max_fuel": r[0x12], "move_class": r[0x14],
                       "ai_class": r[0x15], "hits": r[0x16], "hit_by": r[0x17]}
    lists = {}
    for key, addr in (("clear", 0x083B7CEC), ("fog", 0x083B7D38)):
        ptrs = []
        while True:                     # up to and including the end entry
            ptrs.append(u32(rom, addr + 4 * len(ptrs)) & ~1)
            if ptrs[-1] == 0x080641C0:
                break
        lists[key] = [SUBPHASE_NAMES.get(p, f"0x{p:08X}") for p in ptrs]
    modes = [u32(rom, 0x083B7EB8 + 4 * i) & ~1 for i in range(8)]
    co_rows = [list(rom[0x2872C8 + 12 * i: 0x2872C8 + 12 * i + 12]) for i in range(12)]
    n_prof = max(max(r) for r in co_rows) + 1
    profiles = []
    for k in range(n_prof):
        p = rom[0x11A97C + 0x130 * k: 0x11A97C + 0x130 * (k + 1)]
        profiles.append({"header": list(p[:16]),
                         "units": {name: list(p[4 + 12 * t: 16 + 12 * t])
                                   for t, name in TYPE_NAMES.items()}})
    missions = []
    for m in range(0xA4):
        r = rom[0x287478 + 60 * m: 0x287478 + 60 * m + 60]
        missions.append({"vs": r[0x22] == 0xFF, "b22": r[0x22], "row": r[0x23]})
    cos = []
    for co in range(12):
        base = 0x08284A0C + 0x124 * co
        recs = {}
        for state in (0, 1):
            per_type = {}
            for t, name in TYPE_NAMES.items():
                p = u32(rom, base + 0x40 + 4 * (t + 32 * state))
                per_type[name] = {"attack": u8(rom, p + 5), "defence": u8(rom, p + 6),
                                  "move": s8(rom, p + 7), "range": s8(rom, p + 9)}
            recs[str(state)] = per_type
        cos.append({"co_id": co, "power_fn": f"0x{u32(rom, base + 0x14) & ~1:08X}",
                    "by_power_state": recs})
    return {
        "_comment": ["The CPU's tables, read from the ROM by tools/extract_ai.py",
                     "(DERIVATION 44/45). Addresses in that tool's docstring."],
        "unit_stats": stats,
        "factory_terrain": list(rom[0x3B7DB4:0x3B7DB4 + 20]),
        "property_terrain": list(rom[0x3B7DC8:0x3B7DC8 + 20]),
        "capture_bonus": list(rom[0x3B7DDC:0x3B7DDC + 20]),
        "behaviour_by_type": list(rom[0x3B7DF0:0x3B7DF0 + 26]),
        "t7E09": list(rom[0x3B7E09:0x3B7E09 + 25]),
        "t7E22": list(rom[0x3B7E22:0x3B7E22 + 25]),
        "t7E3B": list(rom[0x3B7E3B:0x3B7E3B + 25]),
        "drop_dirs": list(rom[0x3B7ED8:0x3B7ED8 + 8]),
        "terrain_value": [s8(rom, 0x08284314 + i) for i in range(32)],
        "counter_weight": list(struct.unpack_from("<6i", rom, 0x11A8E8)),
        "hunt_multiplier": list(rom[0x11A900:0x11A905]),
        "fuel_bands": list(struct.unpack_from("<10H", rom, 0x11A906)),
        "load_classes": list(rom[0x11A910:0x11A918]),
        "flags_92C": list(rom[0x11A92C:0x11A931]),
        "subphases": lists,
        "mode_fns": [f"0x{m:08X}" for m in modes],
        "profile_index": co_rows,
        "profiles": profiles,
        "missions": missions,
        "cos": cos,
    }


def main(path=ROM):
    rom = pathlib.Path(path).read_bytes()
    data = extract(rom)
    OUT.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({len(data['profiles'])} profiles)")


if __name__ == "__main__":
    main(*sys.argv[1:])

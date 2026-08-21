"""Extract the AW1 supply/repair/fuel-burn tables from the ROM into JSON.

Everything the turn-start walkers and the APC Supply command consult, found
by reading the code that indexes it (DERIVATION 33):

  * unit stats +0x24..+0x37 -- a 20-byte PER-TERRAIN byte block, indexed by
    terrain id, read by the property-service walker at 0x0802A3D6: nonzero
    means "this terrain repairs/resupplies this unit" (owner must match).
    Ground units: City/HQ/Base. Air: Airport. Naval: Port.
  * unit stats +0x38..+0x4B -- the same shape, read by the daily-burn
    function at 0x080239AC: fuel burned per turn BY TERRAIN. Every unit's
    20 entries are identical (asserted here), which is why the earlier
    extractor's single +0x38 read was right anyway.
  * 0x08282ECC -- can-be-supplied, indexed by the 1-BASED RAM type (entry k
    is unit id k-1, the damage-matrix off-by-one). Gate of the auto-supply
    scan at 0x0802A700.
  * 0x08282EE5 -- is-a-supplier, same indexing. Read at 0x08025874 by the
    menu predicate, the menu executor and the turn-start walker. APC only
    (plus vestigial id 8 -- a cut second supplier).
  * 0x08282EFE -- the terrain on which an OWN property exempts the unit
    from that day's burn, same indexing, read at 0x080239C8. Airport for
    air ids, Port for naval ids, 0 (never) for ground.
  * CO record header byte +0x2D (aw1_co.json header[9]) -- the repair-cost
    multiplier the repair routine 0x08029D9C applies: bar cost =
    cost/10 * this / 100. Kanbei 120, everyone else 100. This is the +09
    header byte DERIVATION 27 had not yet seen a reader for.
  * CO pool entry byte +0x0A -- signed per-unit-type fuel-burn adjustment,
    added at 0x08023A46 and floored at 0: Eagle -2 on the five air type
    slots (both blocks), zero everywhere else in the game.

Code constants carried with their addresses because they live in code, not
tables: dived burn = 5 (0x080239DC, replaces the table value outright),
property repair = 2 bars (0x0802A4CC: 2 + header[+0x2E] + pool[+4], both
zero on every record, asserted here).

Measured behaviour that anchors these tables is in
tests/fixtures/supply_probes.json; this extractor asserts the table values
those measurements exercised.
"""
import json
import hashlib
import pathlib
import struct
import sys

EXPECT_SHA1 = "15053499d5b3f49128a941d7f2d84876f5424d0c"
ROM_BASE = 0x08000000
STATS_BASE, STATS_STRIDE = 0x2830C8, 0x70
SUPPLIABLE, SUPPLIER, NO_BURN = 0x282ECC, 0x282EE5, 0x282EFE
CO_BASE, CO_STRIDE, CO_COUNT = 0x284A0C, 292, 12

UNIT_IDS = {
    0: "Infantry", 1: "Mech", 2: "MdTank", 4: "Tank", 5: "Recon", 6: "APC",
    9: "Artillery", 10: "Rockets", 13: "AntiAir", 14: "Missiles",
    15: "Fighter", 16: "Bomber", 18: "BCopter", 19: "TCopter",
    20: "Battleship", 21: "Cruiser", 22: "Lander", 23: "Sub",
}
CO_NAMES = ["Nell", "Andy", "Max", "Olaf", "Sami", "Grit", "Kanbei", "Sonja",
            "Eagle", "Drake", "Sturm", "Sturm"]
TERRAIN_NAMES = {1: "Plain", 2: "River", 3: "Mountain", 4: "Wood", 5: "Road",
                 6: "City", 7: "Sea", 8: "HQ", 9: "Sky", 10: "Airport",
                 11: "Port", 12: "Bridge", 13: "Shoal", 14: "Base", 19: "Reef"}


def s8(b):
    return b - 256 if b > 127 else b


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

    units = {}
    for uid, name in UNIT_IDS.items():
        rec = rom[STATS_BASE + uid * STATS_STRIDE:
                  STATS_BASE + (uid + 1) * STATS_STRIDE]
        svc = [t for t in range(20) if rec[0x24 + t]]
        burn_block = rec[0x38:0x4C]
        check(len(set(burn_block)) == 1,
              f"{name}: the 20-entry per-terrain burn block is not uniform")
        ram = uid + 1
        units[name] = {
            "id": uid,
            "service_terrains": svc,
            "service_terrain_names": [TERRAIN_NAMES[t] for t in svc],
            "fuel_per_turn": burn_block[0],
            "can_be_supplied": bool(rom[SUPPLIABLE + ram]),
            "is_supplier": bool(rom[SUPPLIER + ram]),
            "no_burn_on_own_terrain": rom[NO_BURN + ram] or None,
        }

    # -- structural cross-checks against the class bitfield ------------------
    for name, u in units.items():
        cls = rom[STATS_BASE + u["id"] * STATS_STRIDE + 0x14]
        if cls & 0x10:                                          # air
            check(u["service_terrains"] == [10],
                  f"{name} is air and should be serviced on Airport only")
            check(u["no_burn_on_own_terrain"] == 10,
                  f"{name} is air and should skip burn on an own Airport")
        elif cls & 0x20:                                        # naval
            check(u["service_terrains"] == [11],
                  f"{name} is naval and should be serviced on Port only")
            check(u["no_burn_on_own_terrain"] == 11,
                  f"{name} is naval and should skip burn on an own Port")
        else:
            check(u["service_terrains"] == [6, 8, 14],
                  f"{name} is ground and should be serviced on City/HQ/Base")
            check(u["no_burn_on_own_terrain"] is None,
                  f"{name} is ground and burns everywhere (at rate 0)")
            check(u["fuel_per_turn"] == 0,
                  f"{name} is ground and should burn 0 a turn")

    # burn rates the emulator measured (supply_probes.json rows)
    for name, want in (("BCopter", 2), ("TCopter", 2), ("Fighter", 5),
                       ("Bomber", 5), ("Sub", 1), ("Battleship", 1)):
        check(units[name]["fuel_per_turn"] == want,
              f"{name} burn {units[name]['fuel_per_turn']}, measured {want}")

    suppliers = sorted(n for n, u in units.items() if u["is_supplier"])
    check(suppliers == ["APC"], f"expected APC as the one supplier, "
                                f"got {suppliers}")
    check(rom[SUPPLIER + 9] == 1,
          "vestigial id 8 should carry the cut second supplier flag")
    check(all(u["can_be_supplied"] for u in units.values()),
          "every real unit should be suppliable")

    # -- CO knobs -------------------------------------------------------------
    repair_pct = {}
    heal_adjust = {}
    burn_adjust = {}
    for co in range(CO_COUNT):
        r = CO_BASE + co * CO_STRIDE
        repair_pct[co] = rom[r + 0x2D]
        heal_adjust[co] = rom[r + 0x2E]
        for blk, tag in ((0, "normal"), (128, "power")):
            for t in range(1, 25):
                ptr = struct.unpack_from("<I", rom, r + 0x40 + blk + t * 4)[0]
                adj = s8(rom[ptr - ROM_BASE + 10])
                if adj:
                    burn_adjust.setdefault(co, {}).setdefault(tag, {})[
                        UNIT_IDS.get(t - 1, f"vestigial{t - 1}")] = adj

    check(repair_pct[6] == 120 and all(v == 100 for c, v in repair_pct.items()
                                       if c != 6),
          f"repair multiplier should be Kanbei 120 / rest 100, got {repair_pct}")
    check(all(v == 0 for v in heal_adjust.values()),
          "the +0x2E repair-amount adjust should be zero on every record")
    check(set(burn_adjust) == {8}, f"only Eagle should adjust burn, "
                                   f"got COs {sorted(burn_adjust)}")
    for tag in ("normal", "power"):
        check(burn_adjust[8][tag] == {"Fighter": -2, "Bomber": -2,
                                      "vestigial17": -2, "BCopter": -2,
                                      "TCopter": -2},
              f"Eagle {tag} burn adjust should be -2 on the five air slots")

    out = {
        "_comment": [
            "Supply, repair and daily-fuel-burn tables, extracted from the",
            "code-indexed blocks named in tools/extract_supply.py (DERIVATION",
            "33). Regenerate with that tool; it exits non-zero on a ROM sha1",
            "mismatch or any failed assertion.",
            "",
            "Rules that live in code, carried with their addresses:",
            "dived subs burn a flat 5 (0x080239DC) regardless of the table;",
            "a loaded unit burns nothing (flags bit3 gate at 0x080239B0);",
            "burn is skipped entirely on an own-side no_burn_on_own_terrain",
            "match (0x080239C8); fuel floors at 0 and an air or naval unit",
            "at 0 after burn is removed (class test 0x08023A92, remover",
            "0x080243D8); property repair is 2 display bars (0x0802A4CC),",
            "charged at cost/10 * repair_cost_pct/100 per bar unless the",
            "settings byte 0x03004357 is nonzero, and the routine 0x08029D9C",
            "always exits by snapping internal HP up to bars*10.",
        ],
        "game": "Advance Wars (USA) Rev 1",
        "rom_sha1": sha1,
        "provenance": {
            "method": "extracted from ROM binary at the offsets the supply "
                      "code indexes",
            "service_table": "unit stats +0x24..+0x37, read at 0x0802A3D6",
            "burn_table": "unit stats +0x38..+0x4B, read at 0x080239AC",
            "suppliable_table": hex(ROM_BASE + SUPPLIABLE),
            "supplier_table": hex(ROM_BASE + SUPPLIER),
            "no_burn_table": hex(ROM_BASE + NO_BURN),
            "co_repair_pct": "CO header +0x2D, read at 0x08029E5E",
            "co_burn_adjust": "CO pool entry byte +0x0A, read at 0x08023A46",
            "measured_against": "tests/fixtures/supply_probes.json",
        },
        "dived_burn": 5,
        "repair_bars": 2,
        "units": units,
        "co_repair_cost_pct": {CO_NAMES[c] if CO_NAMES.count(CO_NAMES[c]) == 1
                               else f"{CO_NAMES[c]}{c}": v
                               for c, v in repair_pct.items()},
        "co_repair_cost_pct_by_id": repair_pct,
        "co_fuel_burn_adjust": {str(c): v for c, v in burn_adjust.items()},
    }
    pathlib.Path(out_path).write_text(json.dumps(out, indent=2) + "\n",
                                      encoding="utf-8")
    print(f"{checks} structural assertions passed")
    print(f"wrote {out_path}: {len(units)} units, {CO_COUNT} CO records")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: extract_supply.py <rom> <out.json>")
    main(sys.argv[1], sys.argv[2])

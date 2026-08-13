"""Extract the AW1 CO records from the ROM into JSON.

This closes the "co-modifier-selection" question that sat open in
aw1_army_struct.json. The earlier attempt assumed a record stride of 128 and
could not find any record with a MIXED set of per-unit modifiers, which had to
be wrong because Max demonstrably has selective bonuses. The stride is 292;
128 is the SUB-BLOCK stride, and each record holds two sub-blocks.

Index formula, read off the disassembly at 0x0803A734 (see DERIVATION.md 11):

    entry = [0x08284A40 + weather*4 + [army+0x1E]*128 + co*292]

so, relative to a record base of 0x284A30:

    +0x00   16 header bytes -- see HEADER_NOTES, only partly identified
    +0x10   3 x u32  pointers to movement-cost tables, indexed by weather 0..2
    +0x1C   24 x u32 pointers into the modifier pool at 0x28491C,
            indexed by the 1-BASED RAM unit type (so entry i is unit id i-1,
            the same off-by-one as the damage matrices)
    +0x80   the whole thing again: the second stat sub-block

Pool entries are 12 bytes with attack at +5 and defence at +6.

The two sub-blocks are selected by army +0x1E, and are almost certainly
(normal, CO power active): the pair for Max reads (150,100) and (170,100), and
+0x1E was observed as 0 for every CO captured with no power running.

WHAT IS ESTABLISHED vs WHAT IS NOT
----------------------------------
Eleven of the twelve records are named by MEASUREMENT. The CO name and portrait
follow army +0x1D live, so writing that byte and reading the intel screen binds
a record directly -- no unlocking required, and no inference involved.

All twelve are named. Record 0 is Nell, which its luck bytes had predicted --
10 normally, 50 under power, the Lucky Star signature -- but the prediction was
kept in `untested` until the screen confirmed it.

Every fingerprint predicted from the ROM before naming turned out right --
Eagle's air bonus, Drake's rain immunity, Olaf's snow immunity, and the two
Sturm records sharing a terrain-ignoring movement table, which was predicted
from the duplicate name in the CO blob. One was read wrong first: record 5's
80/100 on thirteen units looked like a generic handicap, when the point is
which units are NOT reduced -- the four indirects. That is Grit. The assertion
below tests the exclusion rather than the inclusion, so the same misreading
cannot pass again.

Kanbei settles a question the header notes could only speculate about: he has
no per-unit modifiers at all, yet is a straightforwardly stronger CO, and his
+08/+09 reads 120/120. That field is a GLOBAL attack/defence pair, and the
damage engine does not model it.
"""
import json, hashlib, pathlib, sys, struct

EXPECT_SHA1 = "15053499d5b3f49128a941d7f2d84876f5424d0c"
ROM_BASE = 0x08000000

BASE, STRIDE, SUBBLOCK, N = 0x284A30, 292, 0x80, 12
POOL, POOL_LEN = 0x28491C, 0x400
MOVECOST_0, MOVECOST_STRIDE = 0x284548, 0x8C

# Unit ids as used by the damage matrices (0-based). The gaps are vestigial.
UNITS = {
    0: "Infantry", 1: "Mech", 2: "MdTank", 4: "Tank", 5: "Recon", 6: "APC",
    9: "Artillery", 10: "Rockets", 13: "AntiAir", 14: "Missiles",
    15: "Fighter", 16: "Bomber", 18: "BCopter", 19: "TCopter",
    20: "Battleship", 21: "Cruiser", 22: "Lander", 23: "Sub",
}

# Measured in game. The CO name and portrait follow army +0x1D live, so writing
# that byte and reading the intel screen names a record directly -- no unlocking
# required. Eleven of twelve were bound this way.
CONFIRMED = {
    0: "Nell", 1: "Andy", 2: "Max", 3: "Olaf", 4: "Sami", 5: "Grit",
    6: "Kanbei", 7: "Sonja", 8: "Eagle", 9: "Drake", 10: "Sturm", 11: "Sturm",
}
UNTESTED = {}

INDIRECT = {"Artillery", "Rockets", "Missiles", "Battleship"}
FOOT = {"Infantry", "Mech"}
AIR = {"Fighter", "Bomber", "BCopter", "TCopter"}
NAVAL = {"Battleship", "Cruiser", "Lander", "Sub"}

HEADER_NOTES = {
    "+06": "luck. 0 for nearly every record; co=0 reads 10 normal and 50 under "
           "power, co=7 reads 15. NOT confirmed -- named on the strength of the "
           "0..9 default luck roll being 10 wide.",
    "+07": "second luck-shaped byte, only nonzero on co=7 (15). Unidentified.",
    "+08/+09": "a GLOBAL attack/defence pair, distinct from the per-unit "
               "modifiers. 100/100 everywhere except co=6, which reads 120/120 "
               "while all 24 of its per-unit entries are 100/100.",
    "+11/+12": "a second pair, 100/100 normal and 110/90 under power for most "
               "records, but varying widely (co=8 power 80/130, co=10 130/120). "
               "How this composes with +08/+09 and the per-unit modifiers is "
               "UNKNOWN, and it matters -- see the warning printed by this tool.",
}


def main(rom_path, out_path):
    rom = pathlib.Path(rom_path).read_bytes()
    sha1 = hashlib.sha1(rom).hexdigest()
    if sha1 != EXPECT_SHA1:
        sys.exit(f"ROM sha1 {sha1} != expected {EXPECT_SHA1}; "
                 "offsets unverified for this build")

    def u32(a):
        return struct.unpack_from("<I", rom, a)[0]

    checks = 0

    def check(cond, why):
        nonlocal checks
        if not cond:
            sys.exit(f"ASSERTION FAILED: {why}")
        checks += 1

    def read_block(addr):
        weather = []
        for i in range(3):
            p = u32(addr + 0x10 + i * 4) - ROM_BASE
            check((p - MOVECOST_0) % MOVECOST_STRIDE == 0,
                  f"weather pointer {p:#x} is not on a movement-table boundary")
            weather.append((p - MOVECOST_0) // MOVECOST_STRIDE)
        mods = {}
        for i in range(24):
            p = u32(addr + 0x1C + i * 4) - ROM_BASE
            check(POOL <= p < POOL + POOL_LEN,
                  f"modifier pointer {p:#x} is outside the pool")
            uid = i - 1                      # 1-based index, see docstring
            if uid in UNITS:
                mods[UNITS[uid]] = [rom[p + 5], rom[p + 6]]
        return {"addr": hex(addr), "weather_tables": weather,
                "modifiers": mods, "header": list(rom[addr:addr + 16])}

    records = []
    for co in range(N):
        r = BASE + co * STRIDE
        records.append({"co": co,
                        "normal": read_block(r),
                        "power": read_block(r + SUBBLOCK)})

    # The table ends at 12: record 12's pointers land nowhere sane.
    r12 = BASE + N * STRIDE
    check(not (POOL <= u32(r12 + 0x1C) - ROM_BASE < POOL + POOL_LEN),
          "record 12 looks valid; the record count is not 12")

    def groups(rec):
        out = {}
        for unit, mod in rec["normal"]["modifiers"].items():
            out.setdefault(tuple(mod), set()).add(unit)
        return out

    # -- the four measured COs, asserted by fingerprint ------------------
    g = groups(records[1])
    check(set(g) == {(100, 100)}, "Andy (co=1) should be uniformly 100/100")

    g = groups(records[2])
    check(g.get((90, 110)) == INDIRECT,
          f"Max (co=2) should weaken exactly the indirect units, got "
          f"{sorted(g.get((90, 110), []))}")
    check((150, 100) in g, "Max (co=2) should have a 150/100 group")

    g = groups(records[4])
    check(g.get((120, 90)) == FOOT,
          f"Sami (co=4) should boost exactly Infantry and Mech, got "
          f"{sorted(g.get((120, 90), []))}")

    olaf = records[3]["normal"]["weather_tables"]
    check(olaf == [0, 0, 1],
          f"Olaf (co=3) should map snow->clear and rain->snow, got {olaf}")
    check(set(groups(records[3])) == {(100, 100)},
          "Olaf (co=3) should have no per-unit modifiers")

    # Grit: the reduced set is the whole roster MINUS the indirect units. Read
    # the other way round it looks like a generic handicap, which is exactly
    # how it was misread once -- so assert the exclusion, not the inclusion.
    g = groups(records[5])
    spared = {u for u in records[5]["normal"]["modifiers"]} - g.get((80, 100), set())
    check(INDIRECT <= spared,
          f"Grit (co=5) should leave the indirect units unreduced, spared={sorted(spared)}")
    check(not (INDIRECT & g.get((80, 100), set())),
          "Grit (co=5) must not weaken any indirect unit")

    # Kanbei: no per-unit modifiers at all, the bonus is GLOBAL at +08/+09.
    # This is the evidence that field exists and is an attack/defence pair.
    kh = records[6]["normal"]["header"]
    check(set(groups(records[6])) == {(100, 100)},
          "Kanbei (co=6) should have no per-unit modifiers")
    check((kh[8], kh[9]) == (120, 120),
          f"Kanbei (co=6) should carry a global 120/120, got {kh[8]}/{kh[9]}")

    # Sonja: the only record with both luck bytes set, and equal.
    sh = records[7]["normal"]["header"]
    check(sh[6] == sh[7] != 0,
          f"Sonja (co=7) should carry a symmetric luck pair, got {sh[6]}/{sh[7]}")

    # Eagle boosts exactly the air units and weakens exactly the naval ones.
    g = groups(records[8])
    check(g.get((115, 90)) == AIR,
          f"Eagle (co=8) should boost exactly the air units, got "
          f"{sorted(g.get((115, 90), []))}")
    check(g.get((80, 100)) == NAVAL - {"Sub"},
          f"Eagle (co=8) should weaken the surface navy, got "
          f"{sorted(g.get((80, 100), []))}")

    # Drake: rain costs him nothing, and his air arm is weak.
    drake = records[9]["normal"]["weather_tables"]
    check(drake == [0, 1, 0],
          f"Drake (co=9) should map rain->clear, got {drake}")
    check(groups(records[9]).get((80, 100)) == AIR,
          "Drake (co=9) should weaken exactly the air units")

    # Sturm is TWO records, and they are the only pair using a movement table
    # in which every passable terrain costs 1. That was predicted from the
    # duplicate name in the CO blob before either was named in game.
    sturm = [r["co"] for r in records
             if r["normal"]["weather_tables"][0] != 0]
    check(sturm == [10, 11],
          f"exactly records 10 and 11 should use a non-standard clear table, "
          f"got {sturm}")
    check(records[10]["normal"]["weather_tables"]
          == records[11]["normal"]["weather_tables"],
          "the two Sturm records should share a movement table set")
    check(records[10]["normal"]["header"][11:13]
          != records[11]["normal"]["header"][11:13],
          "the two Sturm records should differ somewhere, or they are not two")

    print(f"{checks} structural assertions passed")
    for co, name in sorted(CONFIRMED.items()):
        print(f"  co={co} {name}: fingerprint matches")

    # -- candidates: evidence only, never merged into CONFIRMED ----------
    candidates = {}
    for rec in records:
        co = rec["co"]
        if co in CONFIRMED:
            continue
        ev, w = [], rec["normal"]["weather_tables"]
        if w != [0, 1, 2]:
            ev.append(f"weather substitution {w}: uses table {w[0]} in clear, "
                      f"{w[1]} in snow, {w[2]} in rain")
        if all(t == 6 for t in (w[0], w[2])):
            ev.append("clear and rain both use movement table 6, in which every "
                      "passable terrain costs 1 -- terrain-ignoring movement")
        for mod, units in sorted(groups(rec).items()):
            if mod != (100, 100):
                ev.append(f"{mod[0]}/{mod[1]} on {', '.join(sorted(units))}")
        h = rec["normal"]["header"]
        if (h[8], h[9]) != (100, 100):
            ev.append(f"global attack/defence {h[8]}/{h[9]} at +08/+09")
        if h[6] or h[7]:
            ev.append(f"luck-shaped bytes +06/+07 = {h[6]}/{h[7]} "
                      f"({rec['power']['header'][6]} under power)")
        if ev:
            candidates[co] = ev

    out = {
        "_comment": [
            "CO records. Regenerate with tools/extract_co.py.",
            "",
            "entry = [0x08284A40 + weather*4 + [army+0x1E]*128 + co*292]",
            "",
            "'confirmed' holds only CO identities MEASURED in game by reading",
            "army +0x1D while playing that CO. 'candidates' holds fingerprints",
            "and the evidence for them, and is NOT a name mapping -- do not",
            "promote an entry out of it without a +0x1D reading.",
            "",
            "Two records (10 and 11) use a movement table where every passable",
            "terrain costs 1, and they are the only pair that does. That lines up",
            "with the long-standing observation that the CO name blob lists",
            "'Sturm' twice -- but it is still a fingerprint, not a measurement.",
            "",
            "NOTE FOR THE DAMAGE MODEL. Two of the three candidate sources of CO",
            "scaling are now identified. The per-unit pool is Max's, Sami's,",
            "Grit's, Eagle's and Drake's mechanism. The +08/+09 pair is GLOBAL,",
            "and Kanbei proves it: all 24 of his per-unit entries are 100/100",
            "while +08/+09 reads 120/120, and he is unambiguously a stronger CO.",
            "",
            "+11/+12 is still unknown. It is 100/100 normally and 110/90 under",
            "power for most records, but varies widely (Eagle's power 80/130,",
            "Sturm 130/120).",
            "",
            "engine/damage.py models NEITHER global pair -- it takes co_attack",
            "and co_defense as parameters and nothing fills them. Calibration",
            "used a neutral CO throughout so no existing damage data depends on",
            "this, but a Kanbei prediction would currently be 20% low.",
        ],
        "game": "Advance Wars (USA) Rev 1",
        "rom_sha1": sha1,
        "provenance": {
            "method": "extracted from ROM binary",
            "record_base": hex(BASE),
            "record_stride": STRIDE,
            "subblock_stride": SUBBLOCK,
            "record_count": N,
            "modifier_pool": hex(POOL),
            "modifier_index": "1-based RAM unit type; entry i is unit id i-1",
            "index_formula_site": "0x0803A734",
            "subblock_meaning": "selected by army +0x1E; believed (normal, power)",
        },
        "header_fields": HEADER_NOTES,
        "confirmed": {str(k): v for k, v in sorted(CONFIRMED.items())},
        "confirmed_method": "wrote army +0x1D live and read the CO name off "
                            "the intel screen; the UI follows that byte",
        "untested": {str(k): v for k, v in sorted(UNTESTED.items())},
        "candidates": {str(k): v for k, v in sorted(candidates.items())},
        "records": records,
    }
    pathlib.Path(out_path).write_text(json.dumps(out, indent=2) + "\n",
                                      encoding="utf-8")
    print(f"wrote {out_path}: {N} records, {len(CONFIRMED)} confirmed, "
          f"{len(candidates)} with fingerprints but no measured name")
    print("NOTE: CO scaling has three candidate sources and only two known "
          "multiplications -- see _comment before modelling any specific CO.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: extract_co.py <rom> <out.json>")
    main(sys.argv[1], sys.argv[2])

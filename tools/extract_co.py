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
Established, and asserted below: the record geometry, the pointer layouts, the
per-unit modifier fingerprints, and four CO identities measured in-game by
reading army +0x1D while playing that CO.

NOT established: every other CO name. The fingerprints below are suggestive --
an air-unit specialist, a rain-immune CO, and so on -- but a fingerprint is not
a measurement. They are emitted under "candidates" with the evidence attached,
and deliberately NOT merged into the confirmed mapping. Fill them in by playing
each CO and reading army +0x1D.
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

# Measured in game: select the CO, then read army record +0x1D.
CONFIRMED = {1: "Andy", 2: "Max", 3: "Olaf", 4: "Sami"}

INDIRECT = {"Artillery", "Rockets", "Missiles", "Battleship"}
FOOT = {"Infantry", "Mech"}

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
            "NOTE FOR THE DAMAGE MODEL: there are now THREE candidate sources of",
            "CO scaling -- the per-unit modifier pool, the +08/+09 global pair,",
            "and the +11/+12 pair. DERIVATION.md section 7 established that a",
            "modifier is applied as (value * mod) / 100 truncating, twice in",
            "sequence. Which of these three feeds those two multiplications is",
            "UNKNOWN. Calibration used a neutral CO throughout, so no existing",
            "damage data depends on the answer, but any attempt to model a",
            "specific CO does.",
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
        "confirmed_method": "read army record +0x1D while playing that CO",
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

"""Extract the AW1 terrain definition table from the ROM into JSON.

Supersedes the hand-read version of aw1_terrain.json. The defence stars were
previously typed in from the in-game "Def" display; they are now read from the
binary, and every one of those 14 hand-read values is asserted against the ROM
here. If a future ROM revision disagrees, this crashes rather than silently
emitting wrong numbers.

Layout (Advance Wars USA Rev 1, sha1 1505...4d0c):

    terrain struct array @ 0x284170, 20 entries, stride 0x14

    +0   u32  pointer to the terrain's name string
    +4   u32  income per turn -- 1000 for capturable properties, else 0
    +8   u8   defence stars x 10  (Mountain = 40, i.e. 4 stars)
    +9   u8[3] always zero
    +12  u32  asset pointer
    +16  u32  asset pointer

Twenty entries is not a guess: the movement-cost tables at 0x284548 are
7 move types x 20 terrain slots (stride 0x8C = 7*20), so the terrain ID space
is exactly 0..19 and the two tables index each other. A 21st struct-shaped
record sits at 0x284300 but has no movement-cost column, so it is outside the
addressable space and is not extracted.

HOW THE TABLE WAS FOUND, reproducibly: the terrain name strings are plain
ASCII at 0x2840D8 ('Out', 'Plain', 'River', 'Mt', 'Wood', ...). Searching the
ROM for the four little-endian bytes of each string's mapped address returns
exactly one reference each, and those references are spaced 0x14 apart from a
common base -- which is the struct array. Same literal-pool trick used to
settle the duplicate damage matrices in DERIVATION.md.

TWO RESULTS WORTH READING THE ASSERTIONS FOR:

  * ID 9 is THE SKY. Its name pointer is a reuse of the 'Sea' string, but it
    is impassable to all six ground/naval move types and costs 1 to Air; its
    info-panel description reads "The sky is not considered terrain."; and its
    defence is 0. This is how the game gives air units no terrain cover --
    the defender's terrain is substituted, not special-cased in the damage
    code. An engine that reads the ground tile under a Fighter will award it
    the mountain's 4 stars and be wrong.

  * THERE IS NO MISSILE SILO IN AW1. The strings "Silo" and "Pipe" appear zero
    times in the ROM, and all 20 terrain slots are accounted for: 14 real
    terrains, the sky, and 5 dead fillers. The long-standing "silo's terrain
    ID has never been observed" gap was resting on a false premise.
"""
import json, hashlib, pathlib, sys, struct

TABLE = 0x284170
STRIDE = 0x14
N = 20
EXPECT_SHA1 = "15053499d5b3f49128a941d7f2d84876f5424d0c"
ROM_BASE = 0x08000000

# id -> (canonical name, the abbreviation the ROM actually stores)
CANON = {
    0:  ("Out",      "Out"),
    1:  ("Plain",    "Plain"),
    2:  ("River",    "River"),
    3:  ("Mountain", "Mt"),
    4:  ("Wood",     "Wood"),
    5:  ("Road",     "Road"),
    6:  ("City",     "City"),
    7:  ("Sea",      "Sea"),
    8:  ("HQ",       "HQ"),
    9:  ("Sky",      "Sea"),      # placeholder string; see module docstring
    10: ("Airport",  "Arprt"),
    11: ("Port",     "Port"),
    12: ("Bridge",   "Brdg"),
    13: ("Shoal",    "Shoal"),
    14: ("Base",     "Base"),
    15: ("filler15", "Sea"),
    16: ("filler16", "Sea"),
    17: ("filler17", "Sea"),
    18: ("filler18", "Sea"),
    19: ("Reef",     "Reef"),
}

FILLER = [15, 16, 17, 18]
SKY = 9
OUT = 0
PROPERTIES = {6, 8, 10, 11, 14}      # the five capturable types
INCOME = 1000

# Every value here was read off the in-game Def display BEFORE the ROM table
# was located. They are asserted, not re-derived -- that is the whole point.
DISPLAY_READ = {
    "Plain": 1, "River": 0, "Mountain": 4, "Wood": 2, "Road": 0, "City": 3,
    "Sea": 0, "HQ": 4, "Airport": 3, "Port": 3, "Bridge": 0, "Shoal": 0,
    "Base": 3, "Reef": 1,
}

# Spellings a human might type when recording a battle. Every key that existed
# in the hand-written aw1_terrain.json is preserved here; calibrate.py and
# Board.defence() look terrain up by lowercased name.
ALIASES = {
    "Plain":    ["plain", "plains"],
    "River":    ["river"],
    "Mountain": ["mountain", "mountains", "mtn"],
    "Wood":     ["wood", "woods", "forest"],
    "Road":     ["road"],
    "City":     ["city"],
    "Sea":      ["sea", "water", "ocean"],
    "HQ":       ["hq", "headquarters"],
    "Sky":      ["sky", "air"],
    "Airport":  ["airport", "airfield"],
    "Port":     ["port", "harbor", "harbour"],
    "Bridge":   ["bridge"],
    "Shoal":    ["shoal", "beach"],
    "Base":     ["base", "factory"],
    "Reef":     ["reef"],
}


def cstr(rom, off):
    end = off
    while rom[end]:
        end += 1
    return rom[off:end].decode("ascii")


def read_entry(rom, i):
    a = TABLE + i * STRIDE
    raw = rom[a:a + STRIDE]
    name_ptr, income = struct.unpack("<II", raw[0:8])
    return {
        "addr": a,
        "name_ptr": name_ptr,
        "rom_name": cstr(rom, name_ptr - ROM_BASE),
        "income": income,
        "def10": raw[8],
        "pad": list(raw[9:12]),
        "assets": list(struct.unpack("<II", raw[12:20])),
        "raw": list(raw),
    }


def main(rom_path, out_path):
    rom = pathlib.Path(rom_path).read_bytes()
    sha1 = hashlib.sha1(rom).hexdigest()
    if sha1 != EXPECT_SHA1:
        sys.exit(f"ROM sha1 {sha1} != expected {EXPECT_SHA1}; "
                 "offsets unverified for this build")

    e = [read_entry(rom, i) for i in range(N)]
    checks = 0

    def check(cond, why):
        nonlocal checks
        if not cond:
            sys.exit(f"ASSERTION FAILED: {why}")
        checks += 1

    # -- the name strings pin the ID order -------------------------------
    for i, (canon, abbrev) in CANON.items():
        check(e[i]["rom_name"] == abbrev,
              f"id {i} ({canon}) should hold the string {abbrev!r}, "
              f"got {e[i]['rom_name']!r}")

    # -- defence stars, against values read off the screen ---------------
    for i, (canon, _) in CANON.items():
        d = e[i]["def10"]
        check(d % 10 == 0, f"id {i} defence byte {d} is not a multiple of 10")
        check(0 <= d // 10 <= 4, f"id {i} defence {d // 10} outside 0..4 stars")
        if canon in DISPLAY_READ:
            check(d // 10 == DISPLAY_READ[canon],
                  f"{canon}: ROM says {d // 10} stars, the in-game Def display "
                  f"said {DISPLAY_READ[canon]}")

    # -- income identifies exactly the capturable properties -------------
    for i in range(N):
        want = INCOME if i in PROPERTIES else 0
        check(e[i]["income"] == want,
              f"id {i} ({CANON[i][0]}) income {e[i]['income']}, expected {want}")

    # -- the sky ----------------------------------------------------------
    check(e[SKY]["name_ptr"] == e[7]["name_ptr"],
          "id 9 should reuse id 7's 'Sea' string pointer")
    check(e[SKY]["def10"] == 0, "the sky must give no defence cover")
    check(e[SKY]["income"] == 0, "the sky is not capturable")

    # -- dead slots -------------------------------------------------------
    check(e[OUT]["def10"] == 0 and e[OUT]["income"] == 0,
          "id 0 (out of bounds) should be inert")
    for f in FILLER[1:]:
        check(e[f]["raw"] == e[FILLER[0]]["raw"],
              f"filler slot {f} differs from {FILLER[0]}; it may not be dead")

    for i in range(N):
        check(e[i]["pad"] == [0, 0, 0], f"id {i} bytes +9..+11 are not zero")

    print(f"{checks} structural assertions passed")

    # -- cross-check against the independently extracted movement costs ---
    mcf = pathlib.Path(out_path).parent / "aw1_movecost.json"
    passability = None
    if mcf.exists():
        mc = json.loads(mcf.read_text(encoding="utf-8"))
        clear = next(t for t in mc["tables"] if t["weather"] == "Clear")["costs"]
        types = mc["movement_types"]
        passability = {i: [t for t in types if clear[t][i] != 255] for i in range(N)}
        if passability[SKY] != ["Air"]:
            sys.exit(f"id 9 should be reachable by Air alone, got {passability[SKY]}")
        for dead in [OUT] + FILLER:
            if passability[dead]:
                sys.exit(f"dead slot {dead} is passable to {passability[dead]}")
        print("movement-cost cross-check passed: id 9 is air-only, "
              "slots 0/15/16/17/18 are impassable to everything")
    else:
        print("note: aw1_movecost.json absent, skipping the movement cross-check")

    # -- emit -------------------------------------------------------------
    stars = {}
    for i, (canon, _) in CANON.items():
        for alias in ALIASES.get(canon, []):
            stars[alias] = e[i]["def10"] // 10

    terrain = {}
    for i, (canon, _) in CANON.items():
        if canon.startswith("filler"):
            continue
        terrain[str(i)] = {
            "name": canon,
            "rom_name": e[i]["rom_name"],
            "stars": e[i]["def10"] // 10,
            "income": e[i]["income"],
            "capturable": i in PROPERTIES,
            "passable_by": passability[i] if passability else None,
        }

    out = {
        "_comment": [
            "Terrain definitions, extracted from the ROM struct array at",
            "0x284170 (20 entries, stride 0x14). Run tools/extract_terrain.py",
            "to regenerate; it exits non-zero on a ROM whose sha1 differs or if",
            "any structural assertion fails.",
            "",
            "Defence stars are stored in the ROM as stars*10. All 14 real",
            "terrains match the values previously read off the in-game Def",
            "display, and calibration had independently derived road=0,",
            "plains=1 and mountain=4 from damage observations alone. Three",
            "routes, no shared inputs, same answers.",
            "",
            "ID 9 IS THE SKY -- the terrain an air unit occupies. Zero defence,",
            "reachable by Air alone, and its info-panel text reads 'The sky is",
            "not considered terrain.' Damage against an air unit must use this,",
            "NOT the ground tile underneath, or a Fighter over a mountain wrongly",
            "collects 4 stars.",
            "",
            "There is no Missile Silo in AW1: 'Silo' and 'Pipe' appear zero times",
            "in the ROM and all 20 slots are accounted for. The old 'silo Def not",
            "yet read' gap was resting on a false premise and is closed.",
        ],
        "game": "Advance Wars (USA) Rev 1",
        "rom_sha1": sha1,
        "provenance": {
            "method": "extracted from ROM binary",
            "table_offset": hex(TABLE),
            "stride": hex(STRIDE),
            "entries": N,
            "fields": {
                "+0": "u32 pointer to name string",
                "+4": "u32 income per turn (1000 for capturable, else 0)",
                "+8": "u8 defence stars x 10",
                "+9..+11": "always zero",
                "+12/+16": "u32 asset pointers, not decoded",
            },
            "cross_checked_by_display": sorted(DISPLAY_READ),
            "cross_checked_by_calibration": ["road", "plains", "mountain"],
            "cross_checked_by_movecost": bool(passability),
            "complete": True,
        },
        "sky_id": SKY,
        "out_of_bounds_id": OUT,
        "dead_slots": FILLER,
        "capturable_ids": sorted(PROPERTIES),
        "income_per_property": INCOME,
        "terrain": terrain,
        "stars": dict(sorted(stars.items())),
    }
    pathlib.Path(out_path).write_text(json.dumps(out, indent=2) + "\n",
                                      encoding="utf-8")
    print(f"wrote {out_path}: {len(terrain)} terrains, {len(stars)} name aliases")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: extract_terrain.py <rom> <out.json>")
    main(sys.argv[1], sys.argv[2])

"""Extract terrain movement-cost tables from the ROM into JSON.

Layout: 7 movement types x 20 terrain slots, 140 bytes, 255 = impassable.
Each CO record points at three consecutive tables (see the damage-path
disassembly at 0x08060BD4); more table sets follow, presumably for COs with
terrain movement abilities.

Movement types were identified structurally, not assumed:
  row 4 is passable on every terrain            -> Air
  rows 5,6 only on the water columns            -> Ships, Lander
  row 6 additionally enters one land column     -> Lander (beaches on shoal)
  rows 2,3 cost 1/2 and 2/3 on two columns      -> Treads, Tires
  rows 0,1 cost 2 and 1 on the mountain/river   -> Infantry, Mech

Terrain columns pinned the same way, cross-checked against the wiki's movement
table (which agrees on every value we can identify):
  1 Plain, 4 Wood, 7 Sea, 11 Port, 13 Shoal, 19 Reef.
Columns 2 and 3 are Mountain and River in some order -- their movement costs are
identical for every unit, so movement alone cannot separate them. Defence would
(4 vs 0), and the game displays it.
"""
import json
import pathlib
import sys

BASES = [0x284548, 0x2845D4, 0x284660]
SIZE, WIDTH, ROWS = 140, 20, 7
IMPASSABLE = 255

MOVE_TYPES = ["Infantry", "Mech", "Treads", "Tires", "Air", "Ships", "Lander"]

# Weather, identified by the SHAPE of the differences rather than assumed:
#   table 1 raises costs for 27 cells including foot units      -> Snow
#   table 2 differs in exactly 4 cells: Treads and Tires on
#     Plain and Wood, +1 each, foot units untouched             -> Rain (mud
#     slows wheels and tracks on soft ground, not infantry)
WEATHER = ["Clear", "Snow", "Rain"]
TERRAIN_COLS = {1: "Plain", 2: "River", 3: "Mountain", 4: "Wood", 5: "Road",
                6: "City", 7: "Sea", 8: "HQ", 11: "Port", 12: "Bridge",
                13: "Shoal", 19: "Reef"}
# Resolved: a live map array read row 5 as river at terrain id 2, and the map
# ids share this index space. That forces col 2 = River and col 3 = Mountain,
# which the movement costs alone could never separate (identical for every unit).
AMBIGUOUS = {}


def read_table(rom, base):
    return [list(rom[base + r * WIDTH: base + (r + 1) * WIDTH])
            for r in range(ROWS)]


def main(rom_path, out_path):
    rom = pathlib.Path(rom_path).read_bytes()
    tables = [read_table(rom, b) for b in BASES]

    print(f"{'':10s}" + "".join(f"{c:>4d}" for c in range(WIDTH)))
    for r, name in enumerate(MOVE_TYPES):
        cells = "".join("   ." if v == IMPASSABLE else f"{v:>4d}"
                        for v in tables[0][r])
        print(f"{name:10s}{cells}")
    print("\ncolumns identified: "
          + ", ".join(f"{c}={n}" for c, n in sorted(TERRAIN_COLS.items()))
          + ", " + ", ".join(f"{c}={n}" for c, n in sorted(AMBIGUOUS.items())))

    diffs01 = sum(1 for r in range(ROWS) for c in range(WIDTH)
                  if tables[0][r][c] != tables[1][r][c])
    diffs02 = sum(1 for r in range(ROWS) for c in range(WIDTH)
                  if tables[0][r][c] != tables[2][r][c])
    print(f"\ntable0 vs table1: {diffs01} cells differ "
          f"(table1 costs are higher -- consistent with snow)")
    print(f"table0 vs table2: {diffs02} cells differ")

    out = {
        "_comment": [
            "Terrain movement costs extracted from the ROM. 255 = impassable.",
            "Rows are movement types, columns are terrain slots.",
            "Movement types and most terrain columns were identified by the",
            "STRUCTURE of the tables, not assumed. Columns 2 and 3 are Mountain",
            "and River in some order: their movement costs are identical for",
            "every unit, so movement cannot separate them.",
            "The three tables are weather variants. The table ORDER is the",
            "weather index itself: the game reads a u8 at 0x0300433C, shifts it",
            "left 2, and uses it to pick one of three pointers at 0x08284A40 --",
            "see DERIVATION.md section 11.",
            "",
            "The LABELS were originally inferred from the shape of the",
            "differences: table 1 raises 27 cells including foot, air and naval",
            "units (snow), while table 2 differs in exactly 4 -- Treads and",
            "Tires on Plain and Wood, +1 each, foot untouched (rain turning soft",
            "ground to mud).",
            "",
            "CONFIRMED, no longer inferred: 0x0300433C reads 0 on a clear day",
            "and 1 on a snow day in a live VS match, so index 1 is Snow and by",
            "elimination index 2 is Rain. The signature above and the measured",
            "index agree.",
        ],
        "provenance": {"method": "extracted from ROM binary",
                       "offsets": [hex(b) for b in BASES],
                       "layout": "7 movement types x 20 terrain slots, u8",
                       "impassable": IMPASSABLE,
                       "weather_index_address": "0x0300433C",
                       "weather_labels_confirmed_in_game": True,
                       "weather_inferred_from_difference_pattern": False},
        "movement_types": MOVE_TYPES,
        "terrain_columns": {str(k): v for k, v in
                            {**TERRAIN_COLS, **AMBIGUOUS}.items()},
        "tables": [{"weather": WEATHER[i], "offset": hex(b),
                    "costs": {MOVE_TYPES[r]: t[r] for r in range(ROWS)}}
                   for i, (b, t) in enumerate(zip(BASES, tables))],
    }
    pathlib.Path(out_path).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

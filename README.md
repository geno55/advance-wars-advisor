# Advance Wars advisor

Reverse-engineered game model for **Advance Wars (USA) Rev 1** on GBA: a
ROM-exact damage engine, a live board reader, and the harness that proved both
against a running emulator.

> **You supply the ROM.** No ROM is included or redistributed here, and none ever
> should be. The tools take a path to your own legally obtained copy and verify
> its SHA-1 (`15053499d5b3f49128a941d7f2d84876f5424d0c`) before reading anything;
> a mismatch is a hard error, because the table offsets are build-specific.
> What *is* checked in is extracted numeric data — base damage values and the
> like — which the tool needs in order to interoperate with the game.

The rule this project runs on: **a tool that gives confidently wrong advice is
worse than no tool.** Established facts and open guesses are kept strictly
apart, values are never invented to fill a gap, and anything resting on a single
observation is carried raw rather than named. That discipline has repeatedly
paid off — see "What got caught" below.

## Status

**Milestone 2 — the damage model. Done.**

- Both 24×24 damage matrices, all 18 units, extracted from `0x283B48` and
  `0x283D88`, with 17 structural assertions re-checked on every extraction.
- Internal unit ID map recovered structurally, not guessed.
- Weapon selection reproducing known behaviour (Md Tank hits Infantry for 105
  with its MG but Tank for 85 with its cannon; out-of-ammo fallback).
- Calibrated against 14 exact-HP observations from a live game. Display rule
  determined as `floor_min1`; terrain stars confirmed; four of six formula
  variants refuted.
- Two variants survive and **never disagree on kill/no-kill**, so `resolve()`
  reports the envelope over both: exact on the certain end, generous on the
  uncertain one.
- 33 regression tests.

**Milestone 1 — the state reader. Done.**

`harness/mgba_state.lua` dumps the whole board as JSON; `engine/state.py` loads
it and joins it to the ROM-derived tables.

| structure | source | trust |
|---|---|---|
| Unit array | `[0x08282CB8]` | ROM pointer — correct on any map |
| Army records | `[0x08282CBC]`, 1-indexed | ROM pointer |
| Map dimensions | `0x03003600` row table | fixed IWRAM, read live |
| Terrain map | `0x02016C2A` | **static address, no pointer exists** |

Unit record: type, position, HP, ammo, **capture progress**, fuel, **cargo**,
and a state bitfield (acted / carrying / loaded). Army record: available funds,
income, **CO power meter**. Terrain: ids, `type + 32×owner` ownership encoding,
and movement costs for all three weathers.

The terrain address is the weak link, so it is verified rather than trusted:
every read checks that land units are not standing on water, naval units are not
on land, and every terrain id is known. A wrong address fails loudly instead of
yielding a plausible but wrong board.

**Milestone 3 — pathing. Not started.** Everything it needs now exists, plus a
free oracle: the game writes its own reachable set into `0x03003600`, so a
Dijkstra can be diffed against the real thing rather than assumed correct.

## Layout

```
engine/damage.py          weapon selection, formula variants, damage envelopes
engine/state.py           Board: terrain, defence, movement cost, units, cargo
harness/mgba_state.lua    dump the live board as JSON          <- the state reader
harness/mgba_ramtool.lua  RAM search/diff, map and army inspection, unit records
harness/record.py         interactive battle recorder with live convergence
harness/observations.csv  14 recorded battles

data/aw1_damage.json      damage matrices + provenance + resolved questions
data/aw1_terrain.json     terrain defence stars (read from the in-game display)
data/aw1_terrain_ids.json terrain ids and the ownership bitfield
data/aw1_movecost.json    movement costs, 7 move types x 20 terrains x 3 weathers
data/aw1_army_struct.json army record layout + open CO questions
data/aw1_unit_stats.json  vision/fuel/ammo -- PARTIAL, see "known gaps"

tests/test_damage.py      33 regression tests
tests/calibrate.py        hypothesis elimination, --suggest, --diagnose, --explain

tools/extract_tables.py   ROM -> damage JSON, 17 structural assertions
tools/extract_movecost.py ROM -> movement cost JSON
tools/quote.py            CLI: quote a single matchup
tools/battle_plan.py      which battles to record, by expected information
tools/plan_sim.py         simulate recording strategies before spending time
tools/luck_fit.py         recover the luck distribution from repeated trials
tools/shopping_list.py    rank matchups by discriminating power
tools/co_mods.py          per-CO modifier tables (refuses to report unsound data)

RE toolkit, reusable for milestone 3 and beyond:
tools/rom_header.py  find_tables.py  find_names.py  find_strings.py
tools/find_xrefs.py  find_calls.py   find_ptr_table.py  find_unit_stats.py
tools/disasm.py      dump_region.py  dump_words.py   diff_copies.py
tools/lz77.py        map_dump.py

docs/DERIVATION.md        exactly how everything was found, reproducibly
docs/ASSUMPTIONS.md       established / assumed / refuted, with kill conditions
```

## Reading a live board

Load `harness/mgba_state.lua` in mGBA (Tools → Scripting), then:

```
state("C:/tmp/state.json")
```

```bash
python engine/state.py C:/tmp/state.json
```

```
19x26 board
  P1: 2000 funds (+1000), power 2900, 8 units, 3 properties
      Infantry   ( 5, 0) 10 bars ammo 0 fuel 99 on City  acted  capturing 10/20 (1 more turn(s))
      APC        ( 1, 1) 10 bars ammo 0 fuel 70 on Bridge
        carrying Infantry (10 bars, fuel 97)
```

`Board` exposes `terrain_name`, `defence`, `move_cost(x, y, type, weather)`,
`unit_at`, `cargo_of`, `properties_of`. It raises on unknown terrain rather than
substituting a default.

## Quoting a matchup

```bash
python tools/quote.py Tank Infantry --stars 4
```

Prints the damage envelope, whether the kill is guaranteed, and the counterattack.
Where the two surviving formula variants disagree at the top of the range, it
says so explicitly rather than picking one.

## Re-extracting from the ROM

```bash
python tools/extract_tables.py "../Advance Wars (USA) (Rev 1).gba" data/aw1_damage.json
```

Exits non-zero on a ROM whose SHA-1 does not match, or if any structural
assertion about the unit ID mapping fails.

## Recording more battles

Only needed to narrow the last two formula variants, which currently never
disagree about anything that matters.

```bash
python harness/record.py --exact --counter
```

One number per battle; it re-runs the hypothesis sweep after every entry and
tells you when to stop. `--counter` also records the defender's return strike —
a partial-health observation on a second terrain, free with every battle, which
roughly halves the work.

Protocol: a **neutral CO** with no power active, both units at full health, and
note the **defender's** terrain. CO modifiers are real and large (the ROM holds
records at 150/100 and 170/100), so the wrong CO silently scales everything.

**On the RNG.** Confirmed empirically that replaying from a save state gives
different results, so repeats do sample — but the sampling is *clustered*, not
uniform: the GBA advances its RNG per frame, so a consistent tempo draws from a
narrow band. Vary the delay deliberately. `tools/luck_fit.py` inverts observed
damage back into rolls and distinguishes "unlucky sample" from "wrong model".

## Known gaps

None of these block milestone 3.

- **Terrain array has no pointer.** Static address, verified stable across map
  switches and emulator restarts, sanity-checked on every read.
- **Missile Silo's terrain id** has never been observed. `terrain()` prints
  `?N` for anything unrecognised, so it will announce itself.
- **Unit vision**: 5 of 18 units. The ROM table was not found — searched every
  stride 1–40 in two id spaces as u8 and u16. The game displays vision in the
  unit info panel, which is the better source anyway.
- **CO modifier selection.** The modifier *pool* is found (12-byte structs from
  `0x28491C`, attack `+5`, defence `+6`, including Max's `150/100`), but the CO
  record layout is not. The table appears to hold more records than there are
  COs, apparently because stats differ between VS and Campaign — the name blob
  lists "Sturm" twice. Calibration used a neutral CO throughout, so no damage
  data depends on this.
- **CO power scale.** The meter is at army `+0x20` and charges both the dealer
  and receiver of damage, but the activation threshold and gain formula are
  unknown, so it is exposed as a raw number and never as a percentage.
- **Unit record `+8`–`+11`.** `+11` reads 0 for foot units and 4 for vehicles;
  the rest have been zero in every capture.

## What got caught

Kept as a record of why the project is built this way. Every one of these was a
confident inference overturned by a measurement:

- **`ceil` display HP.** Attack strength scales by a *truncated* tenth of
  internal HP, not the bar count — a Mech at 57 HP shows 6 bars but attacks as
  5. Screen rounding and combat rounding are different functions.
- **`ammo = v >> 7`.** Correct only while capture progress was zero, which it
  was in everything dumped until a capturing infantry reported "ammo 160". `+4`
  packs three fields: hp, ammo, capture.
- **`+1` as "has acted".** Named from one observation; a loaded transport that
  had not moved falsified it. It is a bitfield.
- **A 357:1 likelihood ratio** favouring one formula variant, computed from the
  absence of high rolls — invalid, because the sampling turned out to be
  clustered rather than uniform. Withdrawn.
- **`floor_each_step`** as the leading variant, backed by the disassembly's
  house style of truncating at every step. One well-chosen battle refuted it.
- **"COs have no passive modifiers"**, implied by a partial reading of the
  damage path. Max's documented 150% firepower says otherwise.

The pattern: the tell was almost always a value that was *impossible for the
domain* — ammo 160 on an infantryman, fuel 189 on a tank, 23 HP bars — rather
than anything a type check or unit test would flag. Hence the readers assert
domain invariants and surface unrecognised values instead of swallowing them.

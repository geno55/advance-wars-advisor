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
- CO modifiers filled from the ROM record, and refused where a CO's strength
  lives in header fields the damage path has never been shown to read.
- **The formula is determined: `luck_after_hp`.** `resolve()` returns an exact
  range, not an envelope, so "cannot kill" is as reliable as "will kill".
  Settled by crossing two independent eliminations that neither could finish
  alone -- see `DERIVATION.md` 17.
- 39 regression tests.

**Milestone 1 — the state reader. Done.**

`harness/mgba_state.lua` dumps the whole board as JSON; `engine/state.py` loads
it and joins it to the ROM-derived tables.

| structure | source | trust |
|---|---|---|
| Unit array | `[0x08282CB8]` | ROM pointer — correct on any map |
| Army records | `[0x08282CBC]`, 1-indexed | ROM pointer |
| Map dimensions | `0x030036E0` | `{u8 w, u8 h}`; width cross-checked against the `0x03003600` row stride |
| Property list | `0x03004500` | `[0x08282CC4]`; used to validate the map extent |
| Terrain map | `0x02016C2A` | **static address, no pointer exists** |
| Turn block | `0x03004420` | **static address**, sanity-checked every read |
| Weather | `0x0300433C` | static address, confirmed against the disassembly |

Unit record: type, position, HP, ammo, **capture progress**, fuel, **cargo**,
and a state bitfield (acted / carrying / loaded). Army record: available funds,
income, **CO power meter**. Terrain: ids, `type + 32×owner` ownership encoding,
and movement costs for all three weathers. Turn block: **day** (1-based) and
**active player**, the latter stored pre-multiplied as `32 × player` — the same
shift the terrain array uses for ownership.

The active player used to be *inferred* from "only the current player's army
record holds nonzero funds". It now comes from a real field, and the old
inference is retained as a cross-check that the reader reports on every dump.

**Weather** is the u8 at `0x0300433C`, an index selecting one of the three
movement-cost tables — read off the disassembly at `0x0803A734`, and separately
surfaced from RAM by a labelled-snapshot hunt. `move_cost()` now defaults to the
board's own weather, so callers no longer have to know it; pass `weather=` to
ask a hypothetical. Reading 0 on clear and 1 on snow in a live match also
confirmed which extracted table is Snow, which had been an inference.

The terrain address is the weak link, so it is verified rather than trusted:
every read checks that land units are not standing on water, naval units are not
on land, and every terrain id is known. A wrong address fails loudly instead of
yielding a plausible but wrong board.

**Milestone 3 — pathing. Done.**

`engine/pathing.py` is one Dijkstra over `moveCost[moveType][terrain]` with no
branch on unit type anywhere — a test asserts that, by checking no unit name
appears in the module. Movement allowance is `min(move points, fuel)`, enemies
block passage, friendlies can be crossed but not stopped on, and loading uses
the ROM's capacity and cargo mask. 22 regression tests.

Some rules cost nothing because the tables already imply them. A Tank cannot
board a Lander sitting at sea — not because loading checks terrain, but because
the Tank cannot enter a Sea tile at all, so the question never arises. That has
its own test, since it is exactly the case someone would otherwise hand-code.

**Verified against the game across every movement class.** Each is one live
dump compared tile-for-tile against the game's own flood fill:

| move class | conditions | result |
|---|---|---|
| Infantry | clear | exact |
| Tires (Rockets) | **snow** | exact |
| Air | clear | exact |
| Naval | clear | exact |

Treads is the one class not separately dumped; it shares its table row shape
with the others and is exercised throughout the unit tests, but it has not been
put in front of the oracle.

That dump also settled what the grid *is*. It is the **pass-through** set, not
the stoppable set: all six tiles where it differed from `destinations()` are
occupied by friendly units, which the game paints as in-range and then refuses
to let you stop on. So `tools/path_diff.py` scores against `reachable()` only,
and reports `destinations()` unscored with the blocking unit named on each
excluded tile — an oracle that cannot check a claim must not look like it did.
Grid values are movement **spent**, not remaining, 255 unreachable.

**The one thing the oracle cannot check.** The game never writes down where a
unit may *end*, so `destinations()` — the set the advisor will actually consume
— is validated only by the observation that the game rejects a move onto an
occupied tile. `path_diff` reports it unscored, with the blocking unit named on
each excluded tile, rather than letting it look confirmed.

Still not modelled, and stated in the module docstring rather than discovered
later: fog of war, joining two damaged units, and a transport's second
passenger (the unit record exposes one cargo slot, so a half-full Lander reads
as full — under-reporting destinations, which is the safe direction).

**Automated case generation — proven.** Cases no longer cost a human an
emulator session each. `harness/mgba_spike.lua` restores a save state, writes a
unit record, presses A via `setKeys`, and reads the game's own flood fill;
`tools/spike_check.py` diffs it against the engine. 18 unit types swept from one
fixture, 18/18 matching.

The claim that matters is not those 18 — it is the **control case**, which loads
the fixture and presses A having written nothing. Comparing it against the
written case of the same type is a direct write-versus-play test, and it came
back identical down to the cost values. Writing the unit record before selection
is transparent to the game's own computation.

That result is scoped: writes to type/fuel/ammo/HP, before the game reads them,
checked against movement. It says nothing about writing mid-animation, writing
terrain, or the damage path, which writes at a different moment and involves the
RNG. So the rule is **every sweep ships a control case** — one extra iteration,
and the only thing separating a measurement from a machine agreeing with itself.

**Milestone 4 — threat projection. Composed, and not yet put in front of the
game.**

`engine/threat.py` answers the question a player actually asks before moving
anything: *if I put this unit here, what happens to it on the enemy's next turn.*

Every input is already verified — enemy movement is milestone 3's Dijkstra,
legality is the ROM damage matrices, damage is the calibrated formula, cover is
the terrain table including the sky substitution that stops aircraft inheriting
a mountain's stars. What is new is only the **composition**, and the composition
is the part no oracle has checked. That distinction is the whole status line;
see "What cannot be checked" below.

Not a single branch on unit type, same as pathing, and `tests/test_threat.py`
asserts it the same way. Who threatens what falls out of three ROM fields:

| field | what it decides |
|---|---|
| `armed` | transports threaten nothing, without being enumerated |
| `can_move_and_fire` | direct units threaten around everywhere they may **stop**; indirects threaten the ring around where they **stand** |
| `min_range` / `max_range` | the ring, as Manhattan distance — so the tile beside an Artillery is the safest square on the map |

Four things turned out to matter more than the damage arithmetic:

- **Simultaneity.** A tile can only be shot from by one unit at a time, so
  attackers are matched to *distinct* firing tiles. Three tanks that can all
  reach the single square beside a chokepoint are one attack, not three.
  Counting all three is how a tool talks a player out of the best ground on the
  map.
- **Sequencing.** The defence term reads the defender's *current* displayed HP,
  so each hit lands into a weaker unit and is worth more than the last. Two
  Tanks into a Md Tank on a mountain deal 14 then 16, not 14 twice — summing
  independent quotes understates a gang-up on cover, and the ordering is
  searched rather than assumed.
- **Blocking.** Asking "what if I stood at X" moves the unit there and re-runs
  the enemy searches against *that* board. A tile you would plug scores as
  plugged, and the tile you vacate opens back up. On the test fixture a Md Tank
  is safe one square behind its own infantry screen and exposed the moment the
  screen is what moves.
- **Whose turn it is.** Enemy `acted` flags are ignored by default, because the
  question spans the opponent's next whole turn, by which point every one of
  their units has refreshed.

**The state reader now carries CO identity.** `co_id` at army `+0x1D` was
documented and confirmed — it is the field that named eleven of the twelve
records — but `mgba_state.lua` was not dumping it, so every prediction off a
live board was quietly assuming a neutral CO. Against Max that is wrong by half:
on the fixture, a Tank quote moves from 15–24 to 22–31. Dumps that predate the
field still load, and say so rather than defaulting silently.

**What cannot be checked.** Unlike milestones 1–3 this has no ground truth in
the game: Advance Wars never writes down what it *would* do to you next turn.
Two thirds of it are still checkable and are not yet checked —

- the reachability half is `path_diff`'s oracle applied to enemy units, which
  the existing harness can already drive;
- the damage half inherits milestone 2's calibration;
- the *matching and ordering* on top is the genuinely new logic, and it is
  covered only by unit tests whose expected values are worked from the formula
  by hand rather than measured.

So the numbers are stated as composition, not as measurement, and
`worst_damage` is deliberately an **upper** bound: attackers are never weakened
by the counterattacks they would eat. That fails safe for "can I die here" and
is a real overstatement when your unit hits back hard.

**Fog of war — modelled, detection outstanding.**

Under fog the reader is holding more than the player is allowed to know, so an
advisor built straight on it answers questions using units you cannot see. That
is not a missing feature, it is the confidently-wrong failure this project is
organised against, so `engine/fog.py` models it.

The two ways to be wrong pull opposite ways: see too much and the advisor
cheats, warning you about an ambush you had no way of spotting; see too little
and it calls a tile safe with a tank parked beside it. No single dial is safe
in both directions, so a fogged answer is split — what is **known** from visible
units, and a count of the **unlit tiles** an unseen attacker could be sitting
in. The same board that reads `DIES` in the clear reads:

```
Infantry #10 ( 7, 6) 10 bars on Road   nothing VISIBLE can reach it  [131 unlit tiles in reach]
```

which is "you are blind here", not "you are safe". A fogged report without that
second half would be lying by omission.

`vision` is a real ROM field and its values are strongly consistent with a
sight radius — Recon, Missiles and Sub at 5, the artillery family at 1. That is
where the established part stops. Every rule layered on it is a named switch in
`fog.RULES` with its own kill condition (`ASSUMPTIONS.md` A6), defaulting toward
seeing *less*, because an advisor that cheats is worse than one that is blind.

**Detection is done.** Fog is the **u8 at `0x0300431D`** — battle settings
`+0x0D`, 0 clear and 1 fogged — so `Board.fog` is real and the reader reports
it. Dumps predating the field still read `None`, carried as UNKNOWN rather than
collapsed to off.

Finding it was a controlled experiment rather than a search, because VS mode
toggles fog per map: build the same map twice, dump each with
`state(path, true)` for the IWRAM probe, and diff.

```bash
python tools/fog_hunt.py --off off1.json off2.json --on on1.json on2.json
```

Two captures per side, because 3,735 bytes differed between the labels and
3,678 of those also varied *within* a label. One capture per side cannot tell a
frame counter from the flag. That left 57 candidates, exactly one of them a
clean `0 → 1` inside a known structure — and writing it mid-match turned fog
on, which is the step that makes it causal rather than correlated.

Static analysis had predicted `+0x32` and `+0x08` on their overwhelmingly
boolean read patterns. **Both were refuted** — neither byte moved. The tally
did put the answer in the top three of a 32K space, but it was not evidence,
and `fog_hunt` now prints failed priors as REFUTED rather than dropping them.

**The remaining question is the oracle.** The same diff turned up
`0x03007910..0x0300792C`: all zero with fog off, bitmask-shaped with it on
(`192, 83, 3, 232, 253, 128`). That is what a per-tile hidden mask looks like.
If it is one, the four assumed rules above become *measurable*:

```bash
python tools/fog_diff.py fogged.json
```

It pins the mask layout first, using only the fact that your own units must
stand on visible tiles — an anchor that owes nothing to `fog.py`, because
pinning the layout with our own rules would launder the assumptions into the
test meant to check them. Only then does it score the rules, and a tie is
reported as *unexercised on this board* rather than as agreement. Validated
against a synthetic planted mask; **not yet run against a real capture.**

## Layout

```
engine/damage.py          weapon selection, formula variants, damage envelopes
engine/state.py           Board: terrain, defence, movement cost, units, cargo
engine/pathing.py         one Dijkstra: reachable, destinations, path
engine/co.py              CO modifiers, and what it refuses to model
engine/threat.py          what the enemy can do to you next turn  <- the advisor
engine/fog.py             what you can legally see, and what you cannot
harness/mgba_state.lua    dump the live board as JSON          <- the state reader
harness/mgba_ramtool.lua  RAM search/diff, map and army inspection, unit records
                          reset/mark/chg/unc, tag+tagfilter (labelled states),
                          clusters (group survivors into runs)
harness/record.py         interactive battle recorder with live convergence
harness/observations.csv  14 recorded battles

data/aw1_damage.json      damage matrices + provenance + resolved questions
data/aw1_terrain.json     terrain defs: stars, income, the sky, dead slots
data/aw1_terrain_ids.json terrain ids and the ownership bitfield
data/aw1_movecost.json    movement costs, 7 move types x 20 terrains x 3 weathers
data/aw1_army_struct.json army record layout + open CO questions
data/aw1_co.json          12 CO records; 4 names measured, 8 fingerprints only
data/aw1_unit_stats.json  cost, move, move type, range, vision, fuel, ammo

tests/test_damage.py      39 regression tests
tests/test_pathing.py     22 regression tests, incl. "no unit-type branches"
tests/test_threat.py      23 regression tests, incl. the same branch ban
tests/test_fog.py         21 regression tests, incl. the same branch ban
tools/threat_report.py    exposure, per-unit safety, and the coverage grid
tools/fog_hunt.py         pin the fog flag by diffing labelled RAM probes
tools/fog_diff.py         our predicted visibility vs the game's own mask
tools/path_diff.py        our reachable set vs the game's own flood fill
harness/mgba_spike.lua    write state, drive input, sweep cases unattended
tools/spike_check.py      sweep vs engine, and the write-vs-play control
tests/calibrate.py        hypothesis elimination, --suggest, --diagnose, --explain

tools/extract_tables.py   ROM -> damage JSON, 17 structural assertions
tools/extract_movecost.py ROM -> movement cost JSON
tools/extract_terrain.py  ROM -> terrain JSON, 121 structural assertions
tools/extract_co.py       ROM -> CO records, 655 assertions; confirmed names
                          and unmeasured fingerprints kept strictly apart
tools/extract_units.py    ROM -> unit stats, 152 structural assertions
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

## Reading the threat

```bash
python tools/threat_report.py state.json
```

One line per unit: what the enemy can do to it where it stands, and which of
your units can be killed outright this turn.

```
P1 exposure -- what the enemy can do on their next turn:
  Infantry   #10  ( 7, 6) 10 bars on Road    100 dmg from 4 attackers  DIES
  Infantry   #11  ( 6, 7)  7 bars on Plain   64 dmg from 1 attacker  DIES  [+2 crowded out]
  MdTank     #12  ( 7, 7) 10 bars on Road    22-31 dmg from 1 attacker  survives on 7 bars
  Artillery  #13  ( 6, 8) 10 bars on Plain   untouched
```

`crowded out` is the simultaneity rule showing its work: three units can reach
that infantry and only one of them can find a tile to shoot from.

```bash
python tools/threat_report.py state.json --unit 12
```

Ranks every tile that unit can move to, safest first, re-running the enemy
searches against each placement so blocking is real. Then:

```bash
python tools/threat_report.py state.json --map --for Infantry
```

prints the coverage grid — how many enemies can put a shot on each tile.
`--for` matters: without it a Lander looks threatened by an Anti-Air.

## Re-extracting from the ROM

```bash
python tools/extract_tables.py "../Advance Wars (USA) (Rev 1).gba" data/aw1_damage.json
```

Exits non-zero on a ROM whose SHA-1 does not match, or if any structural
assertion about the unit ID mapping fails.

## Recording more battles

No longer needed to settle the formula -- that is done. Still the way to add
coverage for matchups, terrains and CO modifiers the corpus has never seen.

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

- **Fog visibility rules are unmeasured.** The flag is found and the reader
  reports it, but *what you can see* rests on four assumed rules
  (`ASSUMPTIONS.md` A6). The candidate mask at `0x03007910` would settle them;
  `tools/fog_diff.py` is written and has never seen a real capture.
- **The composition itself is unmeasured.** Threat projection's inputs are all
  verified; the matching and ordering built on top of them are covered by unit
  tests and by nothing else. The reachability half is checkable with the
  existing `path_diff` oracle pointed at enemy units, and has not been.
- **Terrain array has no pointer.** Static address, verified stable across map
  switches and emulator restarts, sanity-checked on every read.
- ~~**Missile Silo's terrain id**~~ — closed, see "What got caught". AW1 has no
  missile silo. `terrain()` still prints `?N` for anything unrecognised.
- ~~**Unit vision**, and the unit stats table generally~~ — closed. The table is
  at `0x2830C8`, **stride 0x70**, which is outside the 1–40 window that earlier
  search swept. Searching for *references to the name strings* found it at once;
  searching for the values never would have. See `DERIVATION.md` section 12.
- ~~**CO modifier selection**~~ — solved. Records are 292 bytes at `0x284A30`,
  and all twelve are named by writing army `+0x1D` and reading the screen.
  `engine/co.py` fills `co_attack`/`co_defense` from the per-unit pool. It
  refuses to quote Kanbei or Sturm, whose strength lives in header fields the
  damage path has never been shown to read — a prediction would be ~20% low.
  The "more records than COs" puzzle is answered: **Sturm has two**, records 10
  and 11, both reporting as Sturm on screen and both using a movement table in
  which every passable terrain costs 1. That was predicted from the duplicate
  name in the CO blob before either was named.
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
- **Map height, wrong since milestone 1.** `mapdims()` counted entries in the
  movement row-pointer table, which is sized to the largest map loaded since
  boot rather than the current one. On a 19×12 board it returned 26, and the
  terrain array was read 14 rows into the previous map's data — coherent,
  symmetric, with its own HQs and ports, and every unit still on legal ground,
  so `verifymap()` passed. Every earlier dump happened to be on a map where the
  high-water mark matched. Caught only because a movement-range diff showed a
  blob *disconnected* from the unit, and a flood fill cannot be disconnected.
  Dimensions now come from `0x030036E0`, and the property list validates the
  extent on every read.
- **A gap that never existed.** "Missile Silo's terrain id has never been
  observed" sat in this file for weeks. AW1 has no missile silo: the strings
  `Silo` and `Pipe` appear **zero** times in the ROM, and all 20 terrain slots
  are now accounted for. An unknown was being tracked as a missing measurement
  when it was a wrong premise — cheaper to check than to carry.
- **Air units and terrain cover.** Nothing flagged this, because it was never
  written down as an assumption: `defence(x, y)` returns the ground tile's
  stars, which would have handed a Fighter over a mountain 4 stars. The game
  substitutes terrain id 9, the sky, at 0 defence. Found only because slot 9
  had a movement profile no real terrain could have — air-passable, everything
  else impassable.

The pattern: the tell was almost always a value that was *impossible for the
domain* — ammo 160 on an infantryman, fuel 189 on a tank, 23 HP bars — rather
than anything a type check or unit test would flag. Hence the readers assert
domain invariants and surface unrecognised values instead of swallowing them.

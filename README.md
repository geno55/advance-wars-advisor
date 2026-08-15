# Advance Wars advisor

Reverse-engineered game model for **Advance Wars (USA) Rev 1** on GBA: a
ROM-exact damage engine, a live board reader, movement, threat projection and
fog of war — with the harness that proved each of them against a running
emulator.

Milestones below are numbered in the order they were built, not the order they
are listed; 2 came before 1 because the damage tables could be read out of the
ROM alone, while the board reader needed an emulator.

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
- Calibrated against 75 exact-HP observations from a live game — 14
  hand-recorded, 61 from the automated sweep. Terrain stars confirmed; five of
  six formula variants refuted.
- **Display rule measured as `ceil`, on both operands** — by writing HP directly
  and sweeping 64 luck seeds per board, because the 75-row corpus turns out to be
  blind to the question: every first strike in it has the attacker at 100 or 9
  internal HP, where all four candidate rules agree. It read `floor_min1` for
  months on the strength of two rows that were counterattacks. See A5.
- CO modifiers filled from the ROM record, and refused where a CO's strength
  lives in header fields the damage path has never been shown to read.
- **Luck is per-CO, from the record's `+06`/`+07` bytes** —
  `uniform(0, 9 + good) - bad`. Ten records carry 0/0 and roll 0..9; Nell reads
  0..19 (0..59 powered) and Sonja **-15..9**. Sonja's is a correctness fix, not
  a refinement: a negative roll lowers the *minimum*, so quoting her as a
  standard CO reported guaranteed kills that will not land. See A11.
- **The formula is determined: `luck_after_hp`.** `resolve()` returns an exact
  range, not an envelope, so "cannot kill" is as reliable as "will kill".
  Settled by crossing two independent eliminations that neither could finish
  alone -- see `DERIVATION.md` 17.
- 57 regression tests.

**Milestone 1 — the state reader. Done.**

`harness/mgba_state.lua` dumps the whole board as JSON; `engine/state.py` loads
it and joins it to the ROM-derived tables.

| structure | source | trust |
|---|---|---|
| Unit array | `[0x08282CB8]` | ROM pointer — correct on any map |
| Army records | `[0x08282CBC]`, 1-indexed | ROM pointer |
| Map dimensions | `0x030036E0` | `{u8 w, u8 h}`; width cross-checked against the `0x03003600` row stride |
| Property list | `0x03004500` | static IWRAM address; the ROM pointer `[0x08282CC4]` is its provenance but the reader does not dereference it. Validates the map extent |
| Fog flag | `0x0300431D` | battle settings `+0x0D`; confirmed by writing it mid-match |
| Vision counts | `0x0201763A` | static, byte per tile, the **active player's** view |
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

**Verified against the game on four of the seven movement classes.** Each is
one live dump compared tile-for-tile against the game's own flood fill:

| move class | conditions | result |
|---|---|---|
| Infantry | clear | exact |
| Tires (Rockets) | **snow** | exact |
| Air | clear | exact |
| Naval | clear | exact |

Treads, Mech and Lander are the three not separately dumped. They share their
table row shape with the others and are exercised throughout the unit tests,
but none has been put in front of the oracle.

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

Still not modelled by pathing, and stated in the module docstring rather than
discovered later: movement being interrupted by walking into a hidden unit,
joining two damaged units, and a transport's second passenger (the unit record
exposes one cargo slot, so a half-full Lander reads as full — under-reporting
destinations, which is the safe direction). Fog itself *is* modelled; see
below.

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
documented and confirmed — it is the field that named all twelve records, by
being written and read back off the screen — but `mgba_state.lua` was not
dumping it, so every prediction off a live board was quietly assuming a neutral
CO. Against Max that is wrong by half:
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

**Milestone 5 — fog of war. Done, detection and rules both measured.**

Under fog the reader holds more than the player is allowed to know, so an
advisor built straight on it answers using units you cannot see — not a missing
feature but the confidently-wrong failure this project exists to avoid.

**Detection.** Fog is the **u8 at `0x0300431D`**, battle settings `+0x0D`, 0
clear and 1 fogged. Found by diffing labelled RAM probes across a VS fog toggle
and confirmed by *writing* it mid-match, which is what separates cause from the
57 bytes that merely correlated. `Board.fog` is real; dumps predating the field
read `None`, carried as UNKNOWN rather than collapsed to off.

**The rules are measured, not assumed.** The game keeps its own answer in
EWRAM: a byte per tile holding *how many of the active player's units can see
it* — a count, not a flag. `engine/fog.py` reproduces it exactly, and three of
its four rules were wrong until it did:

| rule | measured | previously assumed |
|---|---|---|
| radius | Manhattan, from the ROM `vision` stat | correct |
| mountain bonus | **+3** | +1, and switched off |
| property vision | own tile only | switched off |
| concealing terrain | Wood/Reef dark beyond 1 step, **on the tile** | applied to the unit, tile left lit |

The concealment one mattered most: treating it as "the unit is hidden but the
tile is lit" lit ground the game keeps dark, and would have called a wood tile
visible when nothing could see into it. Erring toward seeing *less* was the
right instinct and still left the model disagreeing on 13 tiles — conservative
is not the same as correct.

Each rule was then re-confirmed on a capture built to isolate it: adjacency does
reveal wood and reef; +3 holds for Infantry as well as Mech, with a unique
minimum at 3 (+2 and +4 each miss 29 tiles); and a 19×16 board with **no units
at all** lights exactly its eight properties, measuring property vision alone.

**The reader reads the array rather than reproducing it.** The address is
static — same `0x0201763A` on both map sizes, stride following the map width —
so `Board.vision` carries the game's numbers and `viewer_count()` prefers them.
It is the **active player's** view: on a three-player board where P1/P2/P3 own
8/7/9 properties and nobody has units, it reads 8, 7 or 9 lit tiles purely
according to whose turn it is, matching that player exactly and the other two
not at all.

The rules stay load-bearing for two things the array cannot answer. An
opponent's sight lines — there is no second array — and *what could I see from
a tile I have not moved to yet*, which is about a board that does not exist.
`threat` drops the observed array whenever it relocates a unit, or every
candidate placement would be scored with the sight lines of where the unit
actually is. Every dump carrying the array is then a free re-test of all four
rules.

**What it does to the advice.** The two ways to be wrong pull opposite ways:
see too much and the advisor cheats, warning about an ambush you had no way of
spotting; see too little and it calls a tile safe with a tank beside it. So a
fogged answer is split — what is **known** from visible units, and a count of
the **unlit tiles** an unseen attacker could occupy. The board that reads `DIES`
in the clear reads:

```
Infantry #10 ( 7, 6) 10 bars on Road   nothing VISIBLE can reach it  [131 unlit tiles in reach]
```

"You are blind here", not "you are safe". A fogged report without that second
half lies by omission.

**How both were found**, in one line each, with the full account in
`DERIVATION.md` 20–23. Build the same map twice with the fog toggle flipped,
dump each with `state(path, true)` for the full IWRAM+EWRAM probe, and diff:

```bash
python tools/fog_hunt.py --off off1.json off2.json --on on1.json on2.json
python tools/fog_diff.py fog_on.json fog_on2.json --off fog_off.json
```

Two captures per side, because 3,735 bytes differed between the labels and
3,678 also varied *within* one — a single capture per side cannot tell a frame
counter from a flag. `fog_diff` then pins the array's layout **before**
consulting `fog.py`, since pinning it with our own rules would launder the
assumptions into their own test.

Three wrong turns are worth keeping. Static analysis predicted the flag at
`+0x32` and `+0x08` and **both were refuted** — the tally put the answer in the
top three of a 32K space, but a prior is not evidence, so `fog_hunt` now prints
failed predictions as REFUTED rather than dropping them. Then `0x03007910`
looked bitmask-shaped in a hex dump and is a table of pointers, one of them odd
and therefore a THUMB function pointer; a bitmask and a pointer are the same
bytes, and only alignment tells them apart. Finally the search itself assumed
the array would be zero with fog off and would be a bitmap — it is `01`
everywhere and a byte per tile — and both assumptions were hard filters, so it
confidently found nothing.

## Layout

```
engine/damage.py          weapon selection, formula variants, damage envelopes
engine/state.py           Board: terrain, defence, movement cost, units, cargo
engine/pathing.py         one Dijkstra: reachable, destinations, path
engine/co.py              CO modifiers, and what it refuses to model
engine/threat.py          what the enemy can do to you next turn  <- the advisor
engine/fog.py             what you can legally see; reads the game's own array
harness/mgba_state.lua    dump the live board as JSON          <- the state reader
harness/mgba_ramtool.lua  RAM search/diff, map and army inspection, unit records
                          reset/mark/chg/unc, tag+tagfilter (labelled states),
                          clusters (group survivors into runs)
harness/record.py         interactive battle recorder with live convergence
harness/mgba_dmg.lua      damage sweeps: frame-delay and written-seed, plus
                          RNG read/write/trace
harness/observations.csv  75 recorded battles (14 by hand, 61 swept)

data/aw1_damage.json      damage matrices + provenance + resolved questions
data/aw1_terrain.json     terrain defs: stars, income, the sky, dead slots
data/aw1_terrain_ids.json terrain ids and the ownership bitfield
data/aw1_movecost.json    movement costs, 7 move types x 20 terrains x 3 weathers
data/aw1_army_struct.json army record layout + open CO questions
data/aw1_co.json          12 CO records, all 12 names measured (Sturm has two)
data/aw1_unit_stats.json  cost, move, move type, range, vision, fuel, ammo

tests/test_damage.py      57 regression tests
tests/test_pathing.py     22 regression tests, incl. "no unit-type branches"
tests/test_threat.py      23 regression tests, incl. the same branch ban
tests/test_fog.py         32 tests, incl. five real-board oracles and branch ban
tests/test_corpus.py      13 tests: replay every recorded measurement against
                          the engine, strikes and counters kept apart
tests/fixtures/           captured boards, seeded sweeps, and the game's own
                          vision array
harness/fixtures/         mGBA save states parked at target-select, so a sweep
                          is reproducible rather than re-played by hand
tools/threat_report.py    exposure, per-unit safety, and the coverage grid
tools/fog_hunt.py         pin the fog flag by diffing labelled RAM probes
tools/fog_diff.py         our predicted visibility vs the game's own count array
tools/path_diff.py        our reachable set vs the game's own flood fill
harness/mgba_spike.lua    write state, drive input, sweep cases unattended
tools/spike_check.py      sweep vs engine, and the write-vs-play control
tools/dmg_ingest.py       damage sweep -> observations.csv, with survival report
tools/rng_fit.py          seed sweep -> the luck distribution it implies
tools/luck_range_check.py invert a sweep to the rolls it witnessed, and
                          score the record's predicted range against them
tools/counter_check.py    the two counterattack hypotheses against a seed sweep
tests/calibrate.py        hypothesis elimination: --suggest, --explain,
                          --shared-luck, --selftest. Diagnosis is automatic
                          when nothing survives; there is no --diagnose.

tools/extract_tables.py   ROM -> damage JSON, 17 structural assertions
tools/extract_movecost.py ROM -> movement cost JSON
tools/extract_terrain.py  ROM -> terrain JSON, 121 structural assertions
tools/extract_co.py       ROM -> CO records, 667 structural assertions
tools/extract_units.py    ROM -> unit stats, 179 structural assertions
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
15x10 board, day 10, P1 to move, weather Clear (index 0), FOG
  !! the active-player field disagrees with which army holds funds -- one of the two is wrong
  P1: 16000 funds (+2000), power 0, 8 units, 2 properties
      Artillery  ( 0, 6) 10 bars ammo 9 fuel 50 on Plain
      APC        ( 1, 7) 10 bars ammo 0 fuel 43 on Road
        carrying Mech (10 bars, fuel 69)
```

The header carries day, active player, weather and fog; `fog unknown` appears
instead of `FOG` on a dump that predates the flag. Cross-checks that fail are
printed as `!!` lines rather than swallowed — the one above is real, and it is
the funds heuristic disagreeing with the turn field on a VS board where both
players hold funds.

`Board` exposes `terrain_name`, `defence`, `move_cost(x, y, type, weather)`,
`unit_at`, `cargo_of`, `properties_of`. It raises on unknown terrain rather than
substituting a default.

## Quoting a matchup

```bash
python tools/quote.py Tank Infantry --stars 4
```

Prints the damage envelope, whether the kill is guaranteed, and the
counterattack. Only `luck_after_hp` survives calibration, so the range is exact
rather than an envelope over competing variants.

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

Ranks the tiles that unit can move to, safest first, re-running the enemy
searches against each placement so blocking is real. Six are printed; pass
`--limit N` for more. Then:

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

- **An opponent's visibility is not observable.** The array holds the active
  player's view and there is no second one, so what the *enemy* can see of you
  is modelled, never read. That is the natural next question for fog-aware
  threat projection.
- **The identical copy at `0x02017B42`** has no known purpose; it is dumped
  only as a cross-check.
- **Sonja's vision trait**, and CO powers that reveal the map.
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

- **`floor_min1` display HP, and the retraction of the retraction.** The
  original model said `ceil`. It was withdrawn on the strength of a recorded
  counterattack — a Mech at 57 hitting a Tank for 27 — and `floor_min1` took its
  place, in this list, for months. Both were wrong to be confident: the game
  computes a *counter* with a formula that has no display term at all, so that
  observation never spoke to the question. `floor_min1` then agreed with all 71
  first strikes for a reason that had nothing to do with being right — every one
  of them had the attacker at 100 or 9 HP, the values where all four candidate
  rules return the same number. Writing HP to 57 and 81 and sweeping the luck
  settled it as `ceil` in three runs. The lesson is not "be more careful with
  the corpus": no amount of care would have got there, because the corpus never
  varied the input the question turns on. It is that a rule agreeing with every
  observation you have is worth nothing until at least one of them could have
  disagreed.
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

- **Three of the four fog visibility rules**, held for weeks with a green test
  suite. The mountain bonus was switched off *and* set to a third of its real
  value; property vision was off; concealment was applied to the unit while
  leaving its tile lit. Nothing caught them because every test asserted what
  the model does, and only a measurement can say what the game does. The rules
  had been written deliberately conservative — biased toward seeing less — and
  were still wrong on 13 tiles. Being careful is not the same as being right.
- **A bitmask that was a pointer table.** `0x03007910` reads `c0 53 00 03 ...`
  with fog on and zero with it off, which is exactly what a per-tile mask looks
  like in a hex dump. As words it is `0x030053C0`, `0x0823066C`, `0x080780FD` —
  the last odd, so a THUMB function pointer. Only alignment distinguishes the
  two, and a byte dump hides alignment.
- **"The mask will be zero when fog is off."** It is `01` everywhere: with fog
  off every tile is seen, and the natural encoding of that is a count of one,
  not a blank. Baked in as a search filter, it rejected the real array at every
  offset and the search reported, confidently, that nothing was there. The
  companion assumption — that a mask is a *bitmap* — was equally wrong; it is a
  byte per tile, like the terrain map it sits beside.

The pattern: the tell was almost always a value that was *impossible for the
domain* — ammo 160 on an infantryman, fuel 189 on a tank, 23 HP bars — rather
than anything a type check or unit test would flag. Hence the readers assert
domain invariants and surface unrecognised values instead of swallowing them.

The second pattern, from the fog work: **a search is only as good as its
filters, and a filter encodes an assumption.** Twice a hunt returned "nothing
found" when the thing was there and the premise was wrong. So `fog_hunt` and
`fog_diff` now state their assumptions as switches, print refuted predictions
rather than dropping them, and say what shape they would have missed.

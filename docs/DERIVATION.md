# How the damage tables were found

Reproducible from the ROM alone. No wiki values were typed in; the handful of
matchups quoted below were used only as *search anchors* and every one of them
is re-asserted by `tools/extract_tables.py` on each run.

Target: `Advance Wars (USA) (Rev 1).gba`, sha1 `15053499d5b3f49128a941d7f2d84876f5424d0c`,
game code `AWRE`, version 1, header checksum valid.

## 1. Narrow the search

`tools/find_tables.py` scans for regions whose byte *profile* matches a damage
chart rather than code or graphics: every byte a plausible damage value, a large
minority of zeros ("cannot target"), many small values, a thin tail of heavy
hitters. That alone gave 120 candidates — too fuzzy to act on.

## 2. Anchor

Infantry vs Infantry (55) and Infantry vs Mech (45) are the two values least
likely to be misremembered. Searching for the adjacent byte pair `55 45`, with
the constraint that the following 324 bytes are all plausible damage values,
gave exactly **two** hits: `0x283D88` and `0x3F4DFC`.

## 3. Recover the stride

Dumping at the assumed 18-wide stride produced rows that did not line up. The
real row stride is **24**: the game reserves a 24-slot unit ID space and AW1
fills 18 of it. Re-dumping at stride 24 produced clean, structured rows.

## 4. Find the second matrix

The rows immediately *above* the anchor are also chart-shaped. Aligning to a
multiple of 24 below `0x283D88` shows a second 24×24 matrix ending exactly where
the first begins:

| matrix | offset | size |
|---|---|---|
| primary (ammo) weapon | `0x283B48` | 24×24 u8 |
| secondary (machine gun) weapon | `0x283D88` | 24×24 u8 |

Row 0 of the primary matrix is all zeros — Infantry has no primary weapon — which
is the first sign the alignment is right.

## 5. Pin the unit IDs

The name strings sit at `0x282CC8` (`Infantry`, `Mech`, `Md Tank`, `Tank`,
`Recon`, `APC`, `Artlry`, `Rockets`, `A-Air`, `Missiles`, `Fighter`, `Bomber`,
`B Cptr`, `T Cptr`, `B Ship`, `Cruiser`, `Lander`, `Sub`), followed by weapon
names (`M Gun`, `Bazooka`, `Cannon`, …). That is *display* order, not internal ID
order, so it names the roster but does not map it.

The mapping was recovered structurally instead — each of these is a fact about
the matrix that only one assignment satisfies:

- **Unarmed units** have all-zero rows in *both* matrices → IDs 6, 19, 22 are
  APC, T Copter, Lander.
- **Cruiser's primary is zero everywhere except one column** (value 90). The
  only unit whose primary hits exactly one target is the Cruiser (depth charges
  vs Subs) → that column is **Sub**, ID 23.
- **One row hits only columns 20–23** (55, 25, 95, 55) → Sub torpedoes, which
  fixes the naval block as 20=B Ship, 21=Cruiser, 22=Lander, 23=Sub.
- **Row 2 does 85 to row 4, row 4 does 15 back.** That asymmetry only works one
  way round: row 2 is **Md Tank**, row 4 is **Tank**. This is the load-bearing
  one — swap it and every number downstream is wrong, so it has its own test.
- **Two rows hit only air columns** (100/100/120/120 and 55/100/100/100) →
  Missiles and Fighter.
- **Mech's primary is zero against IDs 0 and 1** — a bazooka cannot target foot
  soldiers.

Result: IDs 0,1,2,4,5,6,9,10,13,14,15,16,18,19,20,21,22,23 are in use; **3, 7, 8,
11, 12, 17 are vestigial gaps** (24 slots − 6 gaps = the 18 named units).

`tools/extract_tables.py` re-checks 17 such invariants on every extraction and
exits non-zero if any fails.

## 6. The duplicate-copy question, settled by cross-reference

The two ROM copies are byte-identical except for **four bytes**: the alternate
copy at `0x3F4DFC` gives **Fighter a secondary weapon** (vs Fighter 15, Bomber
45, B Copter 65, T Copter 75) where the main copy gives it none. It only matters
for a Fighter at 0 ammo.

Settled without touching the game. ARM/THUMB loads a 32-bit constant from a
literal pool, so a table's mapped address appears verbatim as four little-endian
bytes near any code that uses it. `tools/find_xrefs.py` scans for those:

| table | mapped address | code references |
|---|---|---|
| primary (main) | `0x08283B48` | 7 |
| secondary (main) | `0x08283D88` | 3 |
| primary (alt) | `0x083F4BBC` | **0** |
| secondary (alt) | `0x083F4DFC` | **0** |

The alternates are dead data. **An out-of-ammo Fighter cannot attack.**

## 7. What the code says about the formula

`tools/disasm.py` (THUMB, with literal-pool annotation) on the damage sites.

**Index computation**, at `0x08022EB0` and again at `0x08060B60`:

```
ldrb r3, [r3]        ; attacker type
subs r1, r3, #1      ; type - 1
lsls r0, r1, #1      ; *2
adds r0, r0, r1      ; *3
lsls r0, r0, #3      ; *24
subs r0, #1
adds r0, r4, r0      ; + defender type
adds r1, r0, r2      ; + table base
ldrb r0, [r1]        ; base damage
```

So `table + (attacker_type - 1) * 24 + (defender_type - 1)`. Independent
confirmation of the stride-24 layout, and it reveals that **in-RAM unit type IDs
are 1-based** while the table rows are 0-based — worth knowing before the state
reader reads a type byte and indexes off by one.

**CO modifiers** come from a table at `0x08284A0C`, indexed
`[co * 128 + unit_type * 4]`, dereferenced to a struct whose byte `+5` is the
modifier. Applied as:

```
muls r0, r5, r0      ; value * modifier
movs r1, #0x64       ; 100
bl   0x0807b488      ; signed division
```

`0x0807B488` is confirmed `__divsi3` (divide-by-zero check, sign XOR into `ip`,
then shift-subtract) — so it **truncates toward zero**. This happens **twice in
sequence**, attack then defence, each truncating independently.

Scope of that finding, stated precisely: with a neutral CO at 100/100 both
divisions are exact and the truncation is a no-op, so this does **not** by itself
eliminate any of the six variants under calibration conditions. What it does is
(a) settle how CO modifiers must be applied once we model Andy's alternatives,
and (b) show the codebase's house style is to truncate at every step, which is
circumstantial support for `floor_each_step` — support, not proof.

Also noted in passing: the army struct stride is `0x68`, and the AI keeps a
separate damage estimator at `0x08060Axx` that loops over candidate targets —
useful later when we want to predict enemy behaviour.

**Not yet recovered from code:** HP scaling, terrain defence, and the luck roll.
Those live further along the combat path than we have walked. Calibration still
covers them.

## 8. The unit array in RAM

The AI loop at `0x08060B06` walks the live units:

```
lsls r1, r2, #1 ; add r1, sb ; lsls r1, r1, #2   ; r1 = index * 12
ldr  r0, [r0]                                     ; base = [0x08282CB8]
adds r7, r0, r1
ldrb r2, [r7]        ; type, skipped when 0
ldrb r4, [r7, #2]    ; used as a map x index
ldrb r3, [r7, #3]    ; used as a map y index
```

so:

| field | offset | notes |
|---|---|---|
| unit type, **1-based** | +0 | 0 = empty slot |
| has acted this turn | +1 | confirmed: set on exactly the unit that acted, cleared at turn end |
| map x | +2 | |
| map y | +3 | |
| hp / ammo | +4 (u16) | `hp = v & 0x7F`, `ammo = v >> 7` |
| fuel | +6 (u8) | `fuel = v & 0x7F`; bit 7 is a separate, unidentified flag |

Records are **12 bytes**; the base is the EWRAM pointer stored in ROM at
`0x08282CB8`, which reads `0x02019F34`.

**The army is the block, not a field.** 64 slots per army, so `army = slot / 64`
— P1's units occupy slots 1–8, P2's 65–72. Confirmed structurally: 4 × 64 × 12 =
`0xC00`, and `base + 0xC00` = `0x0201AB34`, exactly the next pointer in ROM.

**Bit 7 is a flag bit in two different fields**, which is the trap here. A Mech
read `hp=228` and a Tank read `fuel=189` when taken as plain bytes; they are
`100 | (3<<7)` and `61 | 0x80`. Masking is safe for fuel because AW1's maximum
is 99. Verified against a live 8-unit capture where seven units matched a fresh
unit's ammo and fuel exactly, and the eighth was a Tank at 42 HP with 8 ammo
(one spent on a counterattack) and 58 of 70 fuel.

The fuel bit-7 flag is **not** identified. It is not "has acted" (it survives
end-of-turn, while +1 clears), not "damaged" and not "has moved" — a Tank that
is both does not carry it.

Confirmed against a live capture rather than assumed. A Tank showing 5 bars was
found by change-detection at `0x02019F8C`; that is
`0x02019F34 + 12*7 + 4`, i.e. unit index 7 with HP at +4. Its record reads
type `5`, army `0`, x `7`, y `5`, hp `42` — and `ceil(42/10) = 5` bars.

Note the type: `5` is **Tank**, not Recon. The damage tables are 0-based but RAM
type ids are 1-based, exactly as the `subs r1, r3, #1` in the damage path
implies. Reading a RAM type straight into the damage table is an off-by-one that
silently returns the wrong row, so `harness/mgba_ramtool.lua` subtracts before
naming anything.

Neighbouring pointers, since decoded: `0x08282CBC` -> `0x0201AB34` (the army
structs, stride 0x68 per the damage path), `0x08282CC0` -> `0x0201AD3C` (a
static tile-variant -> terrain-type decoder, **not** a live board),
`0x08282CC4` -> `0x03004500` (the property list: 8-byte records of
`{terrain type, x, y, ...}`, sorted by y then x, `0xFF` terminated). The map row
pointer table is at `0x03003600`.

The property list carries no owner: both HQs on a live map read all-zero in
bytes `+3`..`+7` despite being owned from turn one. Ownership stays in the
terrain array's `type + 32*owner` encoding, so the list answers *where* the
properties are and the terrain array answers *who* holds them.

## 9. The terrain table

Same literal-pool trick as section 6, applied to strings instead of tables.

The terrain names are plain ASCII at `0x2840D8`: `Out`, `Plain`, `River`, `Mt`,
`Wood`, `Road`, `City`, `Sea`, `HQ`, `Arprt`, `Port`, `Brdg`, `Shoal`, `Base`,
`Reef`. That is packed order, not ID order — the same trap the unit names set in
section 5. So instead of reading the blob, search the ROM for the four
little-endian bytes of each string's *mapped* address. Every name returns
exactly one reference, and those references are spaced **`0x14` apart from a
common base of `0x284170`**. That spacing is the struct array.

| field | offset | meaning |
|---|---|---|
| name | +0 | u32 pointer to the name string |
| income | +4 | u32, **1000** for capturable properties, 0 otherwise |
| defence | +8 | u8, **stars × 10** — Mountain reads 40 |
| — | +9..+11 | always zero |
| assets | +12, +16 | u32 pointers, not decoded |

**Twenty entries, and that is not a guess.** The movement-cost tables at
`0x284548` have stride `0x8C` = 7 move types × 20 terrain slots, so the terrain
ID space is exactly 0..19 and the two tables index each other. A 21st
struct-shaped record at `0x284300` has no movement-cost column and is outside
the addressable space.

Byte `+8` divided by 10 reproduces all 14 defence values previously read off the
in-game Def display, and calibration had already derived road=0, plains=1 and
mountain=4 from damage observations alone. Three routes, no shared inputs.

### ID 9 is the sky

The ID space has six slots with no visible terrain: 0, 9, 15, 16, 17, 18. Five
of them are inert — impassable to all seven movement types. **Slot 9 is not.**
It costs 1 to Air and is impassable to everything else, which is a profile no
real terrain can have.

Its name pointer is a reuse of the `Sea` string, so the name blob hides it. But
the info-panel description blob at `0x2EC884` runs in ID order, and the entry
sitting between HQ and Airport reads: *"The sky is not considered terrain."*

Its defence is 0. That is the mechanism by which air units get no terrain cover
— the game substitutes the sky as the defender's terrain rather than branching
on unit type in the damage path. Reading the ground tile under an aircraft is an
off-by-four-stars error, and nothing in the engine would have caught it, because
"air units ignore terrain" had never been written down as an assumption in the
first place. `Board.defence_for(x, y, move_type)` exists for this.

### There is no Missile Silo in AW1

`Silo` and `Pipe` appear **zero** times in the ROM. All 20 slots are accounted
for: 14 real terrains, the sky, and 5 dead fillers. The standing gap "Missile
Silo's terrain id has never been observed" was not an unmade measurement, it was
a false premise, and it survived because an absent value looks the same either
way.

## 10. The turn block

Found by change detection, not by reading code — `0x0201AD3C` and `0x03004500`
were both checked first and were byte-identical across a day boundary, so the
targeted guesses cost nothing and ruled themselves out.

The filter that mattered was not "increased across the day boundary" — dozens of
counters do that — but **"unchanged when a single player ends their turn"**. A
day counter must hold across a player-turn boundary and advance only at the day
boundary; that pair of conditions leaves almost nothing standing.

Result: `0x03004420`, u32 fields, fixed IWRAM.

| offset | meaning |
|---|---|
| +0x00 | **day**, 1-based, matches the on-screen Day |
| +0x04 | **32 × active player**, 1-based — 32 = P1, 64 = P2 |
| +0x08 | constant 1 in every capture; *not* the active player |
| +0x10 | two s16, camera scroll |

The `×32` is not an oddity, it is the same shift the terrain array uses for
ownership (`type + 32*owner`), so the active player can be added onto a terrain
byte on capture without shifting. Confirmed by dumping the identical 32-byte
window on each player's turn: `+0x04` was the only field that moved.

That field replaces an inference. The active player had been derived from "only
the current player's army record holds nonzero funds", recorded in
`aw1_army_struct.json` as a guess resting on one capture. The reader now uses
the field and reports `funds_heuristic_agrees` on every dump, so if the old rule
was ever wrong it says so rather than being quietly inherited.

**Weather is not in this block.** It is at `0x0300433C` — see below.

## 11. Weather, and the CO index formula

Weather resisted RAM scanning badly. A labelled-snapshot hunt over 14 days
(`tag`/`tagfilter` in the ramtool: same value on same-weather days, different on
different-weather days) got it to ~220 addresses and stalled, because most of
those are things *derived* from weather rather than weather itself. What broke
it open was going back to the ROM.

The three movement-cost tables have no direct code references — they are reached
through **CO records**. `0x08284A40`, the base of the weather-pointer array, has
exactly two literal-pool references, and the code at `0x0803A734` reads:

```
mov  r0, ip           ; ip = 0x03004310
adds r0, #0x2c        ; r0 = 0x0300433C
ldrb r0, [r0]         ; <- the weather index
lsls r3, r0, #2       ; * 4
...
ldrb r2, [r1, #0x1e]  ; army +0x1E
lsls r0, r2, #7       ; * 128
adds r3, r3, r0
ldrb r1, [r1, #0x1d]  ; army +0x1D, the CO id
...                   ; r0 = r3 + co*292
ldr  r2, = 0x08284A40
adds r0, r2, r0
ldr  r5, [r0]         ; -> the active movement-cost table
```

So:

```
table = [0x08284A40 + weather*4 + [army+0x1E]*128 + co*292]
```

**Weather is the u8 at `0x0300433C`**, an index 0..2 selecting the three tables
in the order they appear in `aw1_movecost.json`. The labelled-snapshot hunt had
surfaced this exact address from RAM alone, so two routes with no shared inputs
agree.

### The CO record stride is 292, not 128

`aw1_army_struct.json` recorded the index as `co*128 + unit_type*4` and could not
make it work. **128 is the sub-block stride, not the record stride.** A CO record
is 292 bytes and holds *two* 128-byte stat blocks, selected by army `+0x1E`;
each block is 3 weather pointers followed by 24 per-unit modifier pointers.

That matches the record scan: pairs 0x80 apart with 0xA4 between pairs, which is
`0x80 + 0xA4 = 0x124 = 292`. And the pair at `0x284C78`/`0x284CF8` holds
(150,100) and (170,100) — Max's documented firepower and a stronger variant — so
the two blocks are almost certainly **normal and CO-power-active**. That also
explains why `+0x1E` read 0 for Andy, Max and Sami alike: no power was active in
any of those captures, so all three selected block 0.

Still open: which record index is which CO. `+0x1D` supplies it (Andy 1, Max 2,
Sami 4), but that has not been checked against the record contents in-game.

### Which table is Snow and which is Rain — settled

Index 1 raises costs for **every** movement type including Air, Ships and
Lander; index 2 raises them for **Treads and Tires only, on Plain and Wood
only**. Snow slows everything; rain muddies ground vehicles.

That was the inference. It is now a measurement: `0x0300433C` reads **0 on a
clear day and 1 on a snow day** in a live VS match, so index 1 is Snow and by
elimination index 2 is Rain. `weather_inferred_from_difference_pattern` is now
false in the extractor.

`0x0300433E` also tracks weather (0 on clear, 1 on snow) and is not identified.
`0x0300433D` held 1 under both, so it is not weather-related.

Incidentally the snow table contains `Air/Sky 1->2` — terrain id 9 carries a
weather-varying air movement cost exactly as the real terrains do, which is an
independent corroboration that the sky is a genuine terrain slot and not a
filler.

## 12. The unit stats table

Same trick as section 9, and it is now four-for-four: don't search for the
values, search for **references to the name strings**.

The unit names are ASCII at `0x282CC8`. Each one's mapped address appears
exactly once elsewhere in the ROM, and those references are **`0x70` apart** —
with double and triple gaps landing precisely on unit ids 3, 7, 8, 11, 12 and
17, the six vestigial slots already known from the damage matrices. Nothing
else in the ROM lines up that way.

    table @ 0x2830C8, 24 records x 0x70

`24 * 0x70 = 0xA80`, and `0x2830C8 + 0xA80 = 0x283B48` — the base of the primary
damage matrix. The table ends exactly where the next known table begins, so the
base, stride and count are pinned by construction rather than assumed.

| field | offset | |
|---|---|---|
| name pointer | +0x00 | u32 |
| movement points | +0x0C | |
| max ammo | +0x0D | |
| vision | +0x0E | |
| min / max range | +0x10 / +0x11 | 0/0 when unarmed |
| max fuel | +0x12 | |
| unit class | +0x14 | bitfield: 1 foot, 2 tires, 4 treads, 16 air, 32 naval |
| target class | +0x17 | bitfield: 1 air, 2 naval, 4 ground, 8 sub |
| **cost** | +0x18 | u16, stored as **cost / 10** |
| fuel per turn | +0x38 | 0 ground, 2 copters, 5 planes, 1 naval |
| **movement type** | +0x60 | indexes `aw1_movecost.json` movement_types |

Every one of the 18 costs matches the build menu, and the fuel, ammo and vision
values match what had been read out of live RAM months earlier for the six units
that had been captured. The armed/unarmed split cross-checks against the damage
matrices independently.

### Why the earlier search missed it

`aw1_unit_stats.json` recorded that the table "was not found — searched every
stride 1–40 in two id spaces as u8 and u16". The stride is **0x70 = 112**, well
outside that window, and the interesting fields are a u16 holding cost/10 rather
than the cost itself. A value-driven search could not have found this table at
any stride; a reference-driven one found it in a single query.

### Transport capacity

`+0x20` is a pointer, nonzero on exactly four units — APC, T Copter, Lander and
Cruiser. The targets are `0x282DDC`, `0x282E0C`, `0x282E3C` and `0x282E6C`,
spaced **0x30 apart**, so they are not strings but an array of structs:

| field | offset |
|---|---|
| capacity | +0x00 |
| allowed-cargo mask, 24 bytes | +0x01..+0x18, indexed by the **1-based** unit type |
| unidentified | +0x19..+0x2F |

| transport | capacity | carries |
|---|---|---|
| APC | 1 | Infantry, Mech |
| T Copter | 1 | Infantry, Mech |
| Lander | 2 | all ten ground units |
| Cruiser | 2 | B Copter, T Copter |

The cargo sets are asserted against groupings derived from the unit table
itself, so nothing depends on knowing the game from outside: APC and T Copter
must carry exactly the units whose movement type is Infantry or Mech; Lander
exactly those with a ground movement type; Cruiser exactly the air units that
burn 2 fuel per turn rather than 5, which is what separates copters from planes.

Lander's mask also sets four vestigial ids (3, 7, 8, 12). Harmless, since no
unit has those ids — but it shows the mask was authored across the full 24-slot
space rather than the 18 real units, so it cannot be used to infer which ids are
real.

This is the design doc's third example landing exactly as written: *"Transports
aren't special: they're a capacity + allowed-cargo-type field."* There is no
per-unit transport code to find, because the game does not have any.

### There is no canMoveAndFire flag

Nothing in the 112-byte record encodes it, and that is the answer rather than a
gap: AW1's rule is that a unit with range beyond 1 cannot fire after moving, so
the behaviour falls out of `max_range` and needs no per-unit branch. The
extractor emits it as a derived field, and as `null` rather than `true` for
unarmed units — their `max_range` is 0, so a naive `max_range <= 1` test would
hand an APC permission to attack.

## 13. The movement oracle

The game computes its own movement range and leaves it in RAM, so the Dijkstra
in `engine/pathing.py` does not have to be trusted -- it can be diffed. The grid
is reached through the same row-pointer table the map dimensions come from:

```
row = [0x03003600 + y*4] ; value = row[x]
```

`mgba_state.lua` dumps it as `move_grid`, and `tools/path_diff.py` compares it.
It is only meaningful **while a unit is selected** and its range is on screen;
otherwise it holds whatever the previous selection left behind.

### It is the pass-through set, not the stoppable set

First real dump, an Infantry at (1,6) with 3 movement on a 15x10 board: the game
marked **19** tiles, our `reachable()` returned **19**, identical tile for tile.
Our `destinations()` returned 13.

The six-tile difference is not an error in either direction — it is the game
keeping the same two sets apart that we do. All six are occupied by friendly
units (Artillery, Tank, Mech, Recon, Infantry, Mech), and the game paints them
as part of the range and then **refuses the move if you try to stop there**,
which the player confirmed by trying it.

So the grid answers "where can this unit route through", and nothing the game
writes down answers "where may it end". `path_diff.py` therefore scores against
`reachable()` only, and reports `destinations()` unscored with the blocking unit
named on each excluded tile — an oracle that cannot check a claim must not be
allowed to look like it did.

### Grid values are cost spent

| grid | our cost |
|---|---|
| 0 | 0 |
| 1 | 1 |
| 2 | 2 |
| 3 | 3 |

Movement **spent**, not movement remaining, and 255 for unreachable. Worth
stating because the synthetic fixture written before the first real dump assumed
remaining, and would have inverted every value.

### What this does and does not establish

It establishes that one Infantry's reachable set is exactly right on one board
in clear weather. That is the first contact between the pathing engine and the
real machine, and it matched — but the interesting rows of the movement table
are the ones not yet exercised: Tires paying 2 on plains, naval units against
shoals and reefs, Air over everything, and any move type under snow, where every
cost changes at once. Those need their own dumps before pathing is done.

## 14. Map dimensions, and a bug that had been there since milestone 1

`mapdims()` derived both dimensions by walking the movement row-pointer table
at `0x03003600`: consecutive entries one row apart give the width, and counting
them gives the height. The width part is sound. **The height part was never
measuring the map.**

That table is populated to the size of the largest map loaded since boot. On a
19x12 VS map it still held 26 evenly spaced entries left over from a taller map,
so the walk returned 26 and the terrain array was read 14 rows too far, into the
previous map's data.

Nothing looked wrong. The extra rows held coherent, bilaterally symmetric
terrain with its own HQs and ports. Every unit still stood on legal ground, so
`verifymap()` passed. It had been wrong since milestone 1 and every earlier dump
happened to be on a map where the high-water mark matched the real height.

What exposed it was `tools/path_diff.py`: the game's movement grid marked a
19-tile blob disconnected from the unit, at rows 12-16. A flood fill cannot be
disconnected, so either the grid or the geometry was wrong.

### The check that catches it

The game's own property list at `0x03004500` is authoritative for where the
properties are. Any capturable tile in the terrain array that is **absent from
that list** is being read out of bounds. On the bad dump that fired immediately:

```
37 propert(ies) in the terrain array are absent from the game's own list,
first at (0, 14) -- everything from row 12 down is stale data
```

Row 12, which is exactly the real height. The check does not merely detect the
problem, it locates the boundary.

### Where the dimensions actually live

`0x030036E0` holds `{u8 width, u8 height}`. Found by searching IWRAM for the
map's dimensions as adjacent bytes: **exactly one hit in 32KB**, and it sits
four bytes before `0x030036E4`, an address the turn block already pointed at
from `+0x1C`. So it is a real map descriptor rather than a coincidence.

Width now has two independent sources -- this byte and the row-pointer stride --
and the reader records whether they agree. Height comes from the descriptor
only; the walked height is still reported, purely so a disagreement is visible.

`setdims(w, h)` remains as a manual override for when a future build moves the
address. An explicit override you can see beats a silent inference that is right
only sometimes.

### Coverage of the movement diff

Every movement class has now been put in front of the oracle on a live board and
matched tile-for-tile: Infantry in clear, Tires in snow, Air, and Naval. The
Tires-in-snow case is the load-bearing one -- it exercises a non-unit cost (3
per plain) and the weather indirection at the same time, and it is where an
error in either would have shown.

Treads was never dumped separately. It is exercised throughout the unit tests
and shares its table shape with the classes that were checked, but it has not
faced the oracle, and that is a difference in kind rather than degree.

The diff validates `reachable()` only. Nothing the game writes down answers
"where may this unit END", so `destinations()` rests on the observation that the
game paints occupied tiles and then refuses the move. That is exactly the set an
advisor consumes, so it is the weakest link in an otherwise measured chain.

## 15. Generating cases by writing state instead of playing to it

Every observation in this project had cost a human one emulator session. That is
why there are 14 damage observations and not 14,000, and why two damage formula
variants are still alive.

mGBA exposes `loadStateFile`, `write8/16/32`, `setKeys` and `runFrame`, so in
principle a case is: restore a fixture, overwrite some bytes, press A, read the
result. The open question was whether a state assembled that way behaves like
one reached by playing -- if it does not, the harness measures itself.

### Avoiding the cursor

Selecting a unit needs the cursor on it, and the cursor's address is unknown.
Rather than hunt for it, the fixture is saved with the cursor already resting on
one of your units and the sweep **overwrites that unit in place** -- type, fuel,
ammo, HP, never position. The selection stays valid, and pressing A selects
whatever was just written. That is enough to sweep every movement type.

### The control case

A sweep on its own only shows that written state agrees with `pathing.py`; both
could be wrong together. So every sweep begins with a case that loads the
fixture and presses A having written **nothing**. Restoring a save state and
pressing A is simply playing, so comparing that grid against the written case of
the same unit type is a direct write-versus-play test.

Result, on a 15x10 board whose fixture slot held a Tank:

```
write-vs-play [Tank]: reachable sets MATCH, full cost grids match
18/18 cases match
```

Identical, cost values included. **Writing the unit record before selection is
transparent to the game's own movement computation.**

### Scope of that claim, and the rule it implies

It covers writes to the unit record's type, fuel, ammo and HP, made before the
game reads them, checked against the movement flood fill. It does **not** cover
writing during an animation, writing terrain, or writing anything the game may
have already cached. The damage sweep in particular writes at a different moment
(the attack confirmation) and involves the RNG, so it carries its own risk.

Hence the rule: **every sweep ships a control case**. It costs one extra
iteration and it is the only thing separating a measurement from a machine
agreeing with itself.

## 16. The RNG

Found from the ROM in one search. Advance Wars uses the classic 16-bit LCG
constants (20077, 12345), and those cannot be built from THUMB immediates, so
they sit in a literal pool -- adjacent, at `0x3D69EC` and `0x3D69F0`. The code
that loads them is directly above:

```
083D69D4  ldr  r2, = 0x03000750    ; the state
083D69D6  ldr  r1, [r2]
083D69D8  ldr  r0, = 20077
083D69DA  muls r0, r1, r0
083D69DC  ldr  r1, = 12345
083D69DE  adds r0, r0, r1
083D69E0  str  r0, [r2]
083D69E2  lsls r0, r0, #1
083D69E4  lsrs r0, r0, #0x11       ; return (state >> 16) & 0x7FFF
083D69E6  bx   lr
```

    state  = (state * 20077 + 12345) mod 2^32     u32 at 0x03000750
    output = (state >> 16) & 0x7FFF

`0x083D69C8` is the matching seed setter: `str r0, [0x03000750] ; bx lr`.

### Why this matters more than it looks

A4 records that luck sampling was **clustered**, and that a 357:1 inference
built on the absence of high rolls had to be withdrawn because of it. The
frame-delay sweep in section 15 improved on that but did not fix it: 61
deliberately varied cases produced a structural gap at damage 47 -- zero
occurrences where its neighbours got 9 to 15 -- which proves the sweep walks a
small orbit of the sequence rather than covering it. Idling k frames advances
the state by whatever that frame happened to consume, and that is not a
uniform sample of anything.

Writing the state removes the problem rather than mitigating it. **Luck stops
being inferred from damage and becomes an input.** A variant is then refuted
because it predicts the wrong damage at a KNOWN roll, which requires no
distributional assumption and cannot be undermined the way the 357:1 was.

### What is deliberately still unknown

How many times the generator is consumed between the write and the luck roll,
and what the roll is reduced by. `tools/rng_fit.py` searches over both and
accepts a fit only if it is a well-defined function -- the same roll must never
produce two different damages across the sweep. Guessing either would be
inventing a value to fill a gap, which is the one thing this project does not do.

This is reading the RNG to validate sampling, not manipulating it for
advantage. The design doc's "don't chase RNG manipulation yet" still stands:
the engine deals in damage ranges, and nothing in it consults this address.

### That generator is NOT what combat uses — refuted by measurement

The section above was written as though finding an LCG meant finding *the* LCG.
A seed sweep settled it: 64 attacks with `0x03000750` written to 64 different
values produced **one** damage value, 50, every time. Writing it changes
nothing about combat.

Two further checks agree. Decoding every THUMB `bl` in the ROM gives that
routine exactly **three callers**, all at `0x3C5Axx`, nowhere near the combat
path at `0x8022xxx`; and the code immediately after it is a memory/list library.
It is a general-purpose RNG for some other subsystem.

Meanwhile the frame-delay sweep proves the roll *is* sensitive to timing. So
some byte that differs between two differently-delayed states determines it,
and the way to find that byte is not to read more disassembly but to bisect:
snapshot RAM at the confirmation point for two delays that give different
damage, diff them, and binary-search the diff by patching half of one state
into the other and seeing which damage comes out. `rng_bisect()` does this in
about log2(n) attacks.

The lesson is the one this project keeps relearning: a plausible mechanism found
by pattern-matching is a candidate, not a conclusion. The only reason this cost
one sweep rather than a wrong damage model is that the seed sweep was built to
test the claim rather than assume it.

## 17. The combat luck source, found by bisection

Reading the ROM had run out: the only LCG in the binary is not what combat uses,
and no amount of disassembly around the damage path turned up a roll. But the
frame-delay sweep proved the roll is sensitive to timing, so whatever decides it
must differ between two states that produce different damage.

So bisect instead of read. Snapshot RAM at the confirmation point for k=0
(damage 50) and k=6 (damage 45); 2766 bytes differ. Patching all of them into
the k=0 state reproduces 45, which confirms the source is inside that diff.
Then binary-search: patch half, attack, see which damage comes out.

Ten probes narrowed 2766 bytes to five, and the shape of the answer is the
answer:

```
0x03001D30  0x03001D31  0x03001D32  0x03001D33   <- four CONSECUTIVE bytes
0x03001FF2                                        <- incidental
```

The search stopped with "neither half flips it alone", which is not a failure:
each split cut the u32 in two, and half a word does not carry the state.

**The combat luck state is the u32 at `0x03001D30`.** Its algorithm is unknown
and matches none of eight standard LCGs tested against the observed before/after
pair. That does not matter -- to make luck an input we only need to write it.

### And that settled the damage formula

128 seeds spread across the 32-bit space gave

    45:19  46:30  47:14  48:30  49:24  50:11

Nothing above 50, in 128 attacks, where `luck_last` predicts about 40% of
results in 51..54. And the 2:1 shape is the signature of `luck_after_hp`
collapsing ten rolls onto six damages, scoring chi2/df 0.74 against
`luck_last`'s 12.29.

This is a **positive identification from distribution shape**, not the argument
from absence that had to be withdrawn twice before. The uniformity it rests on
is a property of the seeds we chose, not a hope about how the game samples.

Crossed with `calibrate.py`'s set-based elimination over 75 observations, which
independently leaves `{luck_after_hp, luck_last}`, the intersection is one
variant. Neither route could have done it alone.

    damage = floor((base * co_atk/100 * display_hp/10 + luck) * defence)

`SURVIVING_VARIANTS` is now a tuple of one and `resolve()` returns an exact
range, so "cannot kill" is finally as reliable as "will kill".

## 18. Naming the CO records

The remaining eight records were named without unlocking a single CO, because
**the CO name and portrait follow army `+0x1D` live**. Write the byte, look at
the intel screen, read the name off it. That is a measurement, not a fingerprint.

    0  (untested)   1  Andy    2  Max     3  Olaf
    4  Sami         5  Grit    6  Kanbei  7  Sonja
    8  Eagle        9  Drake  10  Sturm  11  Sturm

Constrain writes to 0..11: the index is `co * 292` into a twelve-record table,
so anything higher walks into garbage pointers. Work from a save state and do
not let the game save, since a modified CO id reaching the `.sav` would persist.

### The fingerprints held

Every identity predicted from the ROM before naming turned out right: Eagle's
air bonus and weak navy, Drake's rain-to-clear substitution, Olaf's
snow-to-clear, and the two Sturm records sharing a terrain-ignoring movement
table -- that last one predicted from the duplicate name in the CO blob.

One was read wrong. Record 5 shows 80/100 on thirteen units, which looked like
a deliberately weak "handicap" record. The point is which units are **not**
reduced: Artillery, Rockets, Missiles and Battleship. That is Grit, the indirect
specialist, and reading the negative space would have named him immediately.
`extract_co.py` now asserts the *exclusion* rather than the inclusion, so the
same misreading cannot pass again.

### Kanbei identifies the global modifier pair

Kanbei has **no per-unit modifiers at all** -- all 24 entries are 100/100 --
and is nonetheless straightforwardly stronger. His header reads **120/120 at
`+08/+09`**. That settles what those bytes are: a global attack/defence pair,
applied army-wide and independent of the per-unit pool.

It also exposes a gap. `engine/damage.py` takes `co_attack` and `co_defense` as
parameters and nothing fills them, so a Kanbei prediction today would be 20%
low. Calibration used a neutral CO throughout, so no existing damage data
depends on this -- but modelling any specific CO does.

`+11/+12` remains unidentified: 100/100 normally and 110/90 under power for most
records, but Eagle's power reads 80/130 and Sturm 130/120.

## 19. Modelling CO modifiers, and refusing to model what is not known

Record 0 is **Nell**, confirmed on screen -- so all twelve are named by
measurement. Her luck bytes (10 normally, 50 under power) had predicted it.

`engine/co.py` now fills `co_attack` and `co_defense`, which had been
parameters with nothing behind them since milestone 2. It fills them from the
**per-unit modifier pool only**, because that is the source the disassembly
shows the damage path indexing (section 7): `[co*128 + unit_type*4]`,
dereferenced, applied as `(value * mod) / 100` twice in sequence.

`Attack.between(attacker, defender, attacker_co, defender_co)` exists so
callers do not wire the crossing by hand: attack comes from the ATTACKER's
record indexed by the ATTACKING unit, defence from the DEFENDER's record
indexed by the DEFENDING unit. Sami makes a crossed implementation visible --
her Infantry is 120/90 and her Tank 90/100 -- and a test pins it.

### What it refuses to do

Kanbei and Sturm have **no per-unit modifiers at all**. Their strength sits in
record header fields (`+08/+09` = 120/120 for Kanbei, `+11/+12` = 130/120 for
one Sturm record) that have never been observed in the damage path. Applying
the pool alone would predict *no bonus at all* for them, which is certainly
wrong and would look perfectly reasonable in output.

So `Attack.between()` raises `UnmodelledCO` for those COs instead, naming the
fields it cannot account for. An explicit `co_attack=`/`co_defense=` still
works for deliberate experiments. `tools/quote.py --att-co Kanbei` prints the
reason and exits non-zero.

This is the same shape as the `Unverified` guard on the formula: the tool
declines to answer rather than answering plausibly and wrongly.

### How to settle it

One sweep. Write army `+0x1D` to Kanbei (6) and to Andy (1) on the same attack
fixture, seed the RNG so luck is held constant, and compare:

  * identical damage -> the header pairs do not reach the damage path, and
    Kanbei's strength is applied somewhere else entirely
  * ~20% apart -> they do, and the ratio names the combination rule

Until then the refusal is the honest answer.

## 20. The fog-of-war flag, and two refuted predictions

`vision` had been sitting in the extracted stats table since section 12, but
nothing could use it, because the reader could not tell a fogged board from a
clear one. Without that, an advisor under fog answers with units the player
cannot see.

### The static pass, which was wrong

Weather is read as `[0x03004310 + 0x2C]`, so `0x03004310` is a battle-settings
struct and a per-battle fog flag plausibly lives in the same blob. Every
literal-pool reference to that base was disassembled and the byte offsets it
loads were tallied, scoring each by how often the load is followed by
`cmp #0` -- the shape of a boolean:

```
  +0x08   257 reads, 223 boolean
  +0x32    81 reads,  80 boolean
  +0x0D    51 reads,  19 boolean
  +0x2C    52 reads,   0 boolean   <- weather, an index, so never a boolean
```

That predicted `+0x32`, with `+0x08` as runner-up. **Both were wrong.** Neither
byte moved between fog off and fog on. The flag is `+0x0D`, which the same pass
ranked third on a much weaker signal.

The scoring was not useless -- it put the real answer in the top three of a
32,768-byte space, and the weather row confirms the method separates indices
from booleans. It just was not evidence, and the two predictions are recorded
here as refuted rather than quietly replaced.

### The measurement

VS mode lets the same map be built twice with fog toggled, which makes this a
controlled experiment rather than a search. `mgba_state.lua` grew a probe that
dumps 32K of IWRAM into the JSON, and `tools/fog_hunt.py` diffs labelled
captures: a byte is a candidate only if it is constant across every capture
sharing a label and differs between labels.

Two captures per side, not one. 3,735 bytes differed between the labels and
3,678 of those also varied *within* a label -- frame counters, cursor state,
RNG. One capture per side could not have told those from the flag.

That left 57. Weather and both map dimensions were checked as controls and had
not moved, so the captures differed by fog rather than by setup. Of the 57,
exactly one was a clean `0 -> 1` inside a known structure:

```
  0x0300431D   0 -> 1   battle settings +0x0D
```

### Confirmation, which is the part that matters

The diff shows correlation. 57 bytes correlated. Writing `1` to `0x0300431D`
mid-match with fog off turned fog **on**, which is causation and is what
settles it.

**Fog of war is the u8 at `0x0300431D`**, battle settings `+0x0D`, 0 clear and
1 fogged. The reader ships the raw byte alongside the boolean so an
unrecognised value stays visible, and the turn-block sanity check now rejects
anything that is not 0 or 1.

### A mask that was not a mask

`0x03007910..0x0300792C` is all zero with fog off and reads
`c0 53 00 03 e8 02 00 03 ...` with it on. Called bitmask-shaped, and it is not.
Read as 32-bit words:

```
  0x03007910  0x030053C0   IWRAM pointer
  0x03007914  0x030002E8   IWRAM pointer
  0x0300791C  0x0823066C   ROM pointer
  0x03007920  0x080780FD   ROM pointer, odd -> a THUMB function pointer
  0x03007924  0x030063F0   IWRAM pointer
```

It is a handler table the game populates when fog switches on. The bytes that
looked like masks -- 192, 83, 3, 232 -- are the low halves of pointers. A
bitmask and a pointer are the same bytes; only the alignment tells them apart,
and a hex dump does not show alignment.

Two lessons, both cheap in hindsight. Read a candidate structure at every width
before naming it, since a byte view of a pointer table looks like anything you
want. And the probe covered IWRAM only, on the reasoning that every
battle-state address found so far lived there -- while the terrain map is at
`0x02016C2A` in **EWRAM**, which is exactly where a per-tile array would sit.
The probe's own comment said casting narrowly is how you miss the thing you are
looking for.

### Hunting the mask properly

`state(path, true)` now probes both work RAMs in full. `tools/fog_diff.py`
searches for a per-tile visibility array in either, in both plausible shapes --
bit-per-tile and byte-per-tile, the latter being what sitting next to the
byte-per-tile terrain map would suggest.

It pins the layout **before** consulting `engine/fog.py`, because pinning it
with our own rules would launder the assumptions into the test meant to check
them. Three rule-independent constraints:

  * every one of the player's own units stands on a tile the mask calls visible
  * with a clear capture of the same map as a control, the span must read all
    zero there and must contain bytes fog changed
  * every lit tile is near *something* -- a unit or an owned property. Fog means
    sight is local, and without this a `1 = hidden` layout that lights 139 of
    150 tiles passes the own-units anchor trivially

The first run had none of these but the first, survived 984 layouts, and
top-ranked the pointer table. The control is what makes the search tractable;
the locality filter is what makes the ranking mean anything.

Ranking then prefers the natural row stride and, among those, the span that
accounts for the most of what fog actually wrote -- neighbouring offsets decode
nearly the same picture, so covering the writes is the discriminator.

Validated against synthetic dumps carrying a planted bit-per-tile mask in IWRAM
and a planted byte-per-tile one in EWRAM, each with a deliberately non-default
rule set. It recovers both. It also demonstrates the failure it warns about: on
the byte-per-tile fixture, whose surrounding memory was zeroed so that
neighbouring offsets alias, it pinned one byte early and step 2 then reported
rule disagreements that are artefacts of the mis-pin.

Unrun against a real capture. No mask has been found.

## 21. The vision oracle, and three rules that were wrong

Section 20 left the visibility rules as four assumptions and the mask unfound.
The mask exists. It is at **`0x0201763A`** on a 15x10 board, stride 15, one
**byte per tile**, and it does not hold a boolean -- it holds **how many of
your units can see that tile**, values 0 to 6.

### Why the first search found nothing

Two wrong assumptions, both mine, both baked into the search as hard filters.

**"The array is zero when fog is off."** It is `01` everywhere. Of course it
is: with fog off every tile is seen, and the natural encoding of that is a
count of one, not a blank. Requiring an all-zero span rejected the real array
on every offset. Relaxing that filter to "assume nothing about the clear
capture" is what let it surface.

**"A mask is a bitmap."** It is a byte per tile, the same shape as the terrain
map it sits near. The bit-per-tile sweep would never have matched.

The rest of the search held up. The control capture, the noise filter across
two fogged captures, the locality requirement and the own-units anchor between
them cut 262,144 bytes to seven candidate layouts, of which two were the array
and its duplicate and three were row-shifted aliases.

### Reading it

```
RAW ARRAY at 0x0201763A            P1 units at (0,6) (1,6) (2,6) (1,7)
    012345678901234                (1,8) (0,9) and a Mech on the mountain
 4  122111000000000                at (4,8). Property at (4,1), HQ at (0,8).
 5  334311100000000
 6  564431110000000                The 6s sit on the unit cluster; the value
 7  665322111000000                falls off with distance. That is a count
 8  454222011100000                of viewers, not a visibility flag.
 9  333322101000000
```

An identical copy lives at `0x02017B42`. Double buffer or per-player slot, not
yet established.

### What it measures

Modelling the count as "number of units within `vision` Manhattan steps" and
sweeping the open rules gives an **exact match, 150 tiles out of 150**:

| rule | measured | what the code assumed |
|---|---|---|
| radius | Manhattan, from the ROM `vision` stat | correct |
| mountain bonus | **+3** | +1, and switched off |
| property vision | own tile only, radius 0 | switched off |
| concealing terrain | Wood and Reef dark beyond 1 step, **on the tile** | applied to the unit only, tile left lit |

Three of four were wrong. The concealment one is the one that mattered:
modelling it as "the unit is hidden but the tile is lit" meant the advisor lit
ground the game keeps dark, and would have called a wood tile visible when
nothing could see into it.

The bias toward seeing less was the right instinct and still left the model
disagreeing with the game on 13 tiles -- being conservative is not the same as
being correct.

`engine/fog.py` now exposes `viewer_count()` as the primitive and derives
`visible_tiles()` from it, so the thing checked against the oracle and the
thing the advisor uses are one computation.
`tests/fixtures/fog_vision_15x10.json` carries the board and the game's array,
and a test asserts each measured rule is load-bearing -- turn any one off and
the match breaks -- so a rule cannot be quietly wrong and still pass because
nothing on the board exercised it.

### What this did NOT settle

* **Whether adjacency reveals concealing terrain.** No unit stood within 1 of
  any wood tile, so "visible from adjacent" and "never visible" fit the data
  identically. `can_see` keeps the adjacency branch on the strength of series
  convention alone.
* **Whether the mountain bonus is +3 for every unit class.** One Mech, one
  mountain.
* **Whether `0x0201763A` is stable.** One capture is not an address. The reader
  does not read it, and should not until a second map says where it lives.

## 22. Closing the three loose ends, and reading the answer instead of computing it

Section 21 left three things open. All three are now settled, each by a capture
built to isolate one of them.

**Adjacency reveals concealing terrain.** Confirmed on screen. The rule is
"Wood and Reef are dark beyond 1 step", not "never visible" -- the two fit the
earlier data identically because no unit had stood next to a wood tile.

**The mountain bonus is +3 for Infantry as well as Mech.** A capture with both
on mountains matches at +3 with a *unique* minimum: +2 and +4 each miss 29
tiles, +1 misses 46, +0 misses 59. Two unit classes and a sharp optimum is a
long way from the single Mech that produced the number.

**The address is static.** On a 19x16 map the array is at the same
`0x0201763A`, stride following the map width, with the duplicate still at
`0x02017B42`. That map is Day 1 with **no units at all**, so the only vision
source is property ownership -- and the array holds exactly eight ones on the
eight P1 properties and zero everywhere else. Property vision measured on its
own, with nothing else in the frame to confuse it.

### The reader now reads it

`mgba_state.lua` dumps the array, `Board.vision` carries it, and
`fog.observed_count()` hands back the game's own numbers. `viewer_count()`
prefers them over the rules, because a reproduction of ground truth is worth
less than ground truth.

The rules do not become dead code. The observed array is a photograph of the
board as it stands, and the advisor's central question -- *what could I see
from a tile I have not moved to yet* -- is about a board that does not exist.
So `threat._relocate` sets `vision=None` on every hypothetical board and the
model answers those. Carrying the array forward would silently evaluate each
candidate placement using the sight lines of where the unit actually is.

That split also turns every future dump into a free regression test:
`model_disagreement()` re-checks all four rules against the game on any board
that carries the array.

### On the fixtures

Three real boards are checked in, and the oracle tests deliberately call
`computed_count()` rather than `viewer_count()`. Pointing them at the latter
would compare the fixture against itself now that it prefers the observed
array, and pass no matter how wrong the rules were.

One test asserts each rule is load-bearing on at least one fixture. That exists
because `mountain_bonus` spent its whole assumed life switched off and wrong
without a single test noticing -- nothing on the board exercised it.

## 23. Whose view the array holds

The last thing section 22 could not settle. The array had matched P1 on every
capture, but P1 had been the active player on every capture, so "P1's view" and
"the active player's view" fit the evidence equally.

A three-player map decides it, and does so without needing a single unit on the
board. P1, P2 and P3 own **8, 7 and 9** properties; with no units anywhere,
property ownership is the only vision source, so each player's expected array
is a different number of lit tiles. Capture the same board on three consecutive
turns and the array reads:

| turn | lit tiles | matches |
|---|---|---|
| P1 | 8 | P1's properties exactly; P2's and P3's not at all |
| P2 | 7 | P2's exactly |
| P3 | 9 | P3's exactly |

Nothing about the board changes between the three captures except whose move it
is. **The array is the active player's view.** `fog.observed_count()` answers
for the active player and returns None for anyone else -- which was already the
behaviour, adopted as a hedge and now justified.

Three consequences worth stating.

**An opponent's visibility is not observable.** There is no second array
anywhere in EWRAM; the P2-predicted and P3-predicted arrays appear nowhere on
P1's turn. Anything that needs to reason about what the ENEMY can see of you --
which is the natural next question for fog-aware threat projection -- has to
model it. That is now the load-bearing use of the rules, alongside hypothetical
placement.

**The rules were re-confirmed for three different players.** Every previous
check was P1. The same computation reproduces P2's and P3's arrays exactly.

**Property vision got a third independent measurement**, on a board where it is
the only thing happening. It lights the property's own tile and nothing else.

## 24. Where the damage path gets its CO, and the flag that switches it off

Section A12 recorded a measurement — four seed sweeps, four COs written to army
`+0x1D`, one damage band — and drew the wrong conclusion from it: that the
damage path does not read `+0x1D`. It reads it. There is a gate in front of it,
and the gate was shut.

`0x08284A0C` is the CO modifier pointer array. It has 96 literal references;
one sits at `0x022EA4`, immediately after the damage routine. The attacker's
modifier fetch reads:

```
08022E40  adds r4, r0, r1        ; r4 = the attacker's army record
08022E42  ldrb r1, [r4, #0x1e]   ; +0x1E, the stat sub-block index
08022E44  lsls r0, r1, #7        ; * 128
08022E46  adds r3, r3, r0
08022E48  ldr  r0, = 0x03004310  ; the battle-settings struct
08022E4A  ldrb r1, [r0, #8]      ; +0x08
08022E4C  cmp  r1, #0
08022E4E  beq  #0x8022e70        ; <- clear: skip the CO lookup entirely
08022E50  ldrb r1, [r4, #0x1d]   ; +0x1D, the CO id
08022E52  lsls r0, r1, #3        ; co*8
08022E54  adds r0, r0, r1        ; co*9
08022E56  lsls r0, r0, #3        ; co*72
08022E58  adds r0, r0, r1        ; co*73
08022E5A  lsls r0, r0, #2        ; co*292   <- the record stride from section 11
08022E5C  adds r1, r3, r0
08022E5E  b    #0x8022e76
08022E70  movs r2, #0x92
08022E72  lsls r2, r2, #1        ; 0x92 * 2 = 292
08022E74  adds r1, r3, r2        ; <- record 1. ANDY, hardcoded.
08022E76  ...
08022E7C  ldr  r0, [r0]          ; -> the per-unit modifier struct
08022E7E  ldrb r0, [r0, #5]      ; the modifier byte
08022E80  muls r0, r5, r0
08022E82  movs r1, #0x64
08022E84  bl   0x0807B488        ; __divsi3, truncating
```

The defender's fetch at `0x08022F3E` is the same shape with the same gate,
falling back to the same `292 * 1`.

**So `[0x03004318]` decides whether COs exist at all.** Clear, and every unit on
both sides is computed as Andy — neutral 100/100 — no matter what `+0x1D` says.
Set, and the CO id is read per attack, which means writing it works after all.

### The flag was already in front of us

Section 20's static pass over the settings struct tallied which byte offsets are
read as `cmp #0`, looking for the fog flag. `+0x08` topped it at **223 of 257
reads boolean**, and was dismissed in one line as "probably a more general flag"
because it also appears in the movement-cost path at `0x0803A758`. It is a more
general flag. It is *this* flag, and the movement path gates the CO's movement
table on it for exactly the same reason.

The tally was right, the ranking was right, and the note attached to it treated
generality as a reason to look elsewhere rather than as the finding.

### What this changes

A12's measurement stands: four COs, one band. Its conclusion does not. The
correct statement is that the sweeps were taken in matches where CO abilities
are disabled, so they measured Andy four times.

That makes the sweep method viable again, including for Kanbei, provided the
flag is set. Two ways: write `0x03004318 = 1` alongside `+0x1D`, or find the
setup option that turns CO abilities on and build the fixture there. The write
is the cheap test and predicts a specific result — Max on Tank → Infantry in
woods should move from 60-67 to **90-97**.

**Not yet established:** which option sets it, and whether writing it
mid-fixture is enough or whether the game latches CO state earlier. Reading 0 in
four VS captures says only that those matches had it clear.


## 25. The headless route: Mesen2's testrunner, and how A15 died in it

Everything before this section was measured through mGBA's GUI scripting
console, one human session per run. mGBA 0.10.5 has no script CLI, so the
harness shipped paste-ready blocks and waited. Mesen2 (the locally built
Expanded branch) has a GBA core and a true headless mode:

    Mesen.exe --testrunner --timeout=600       --debug.scriptWindow.allowIoOsAccess=true       "Advance Wars (USA) (Rev 1).gba" harness/mesen_capture.lua

which runs a Lua script against the ROM with no window and exits with the
script. Three requirements that cost real debugging:

- **A real `gba_bios.bin`** in Mesen's Firmware folder. The core has no HLE
  fallback: without it the game boots straight into ROM and hangs at its
  first `svc` — and AW1 calls BIOS `Div` inside the damage and capture paths
  and LZ77 everywhere. The blank screen looks like a bad ROM; it is a
  missing BIOS.
- **`emu.loadSavestate()` only runs inside an exec memory callback** on the
  main CPU. The runner resumes its coroutine from a callback on the BIOS IRQ
  vector at `0x18` — executed every VBlank — gated by an `endFrame` flag to
  once per frame. Event callbacks alone cannot reload state.
- **GUI savestates are script-loadable.** `SaveStateManager::LoadState`
  parses the same MSS container the GUI writes, so a human parks a fixture
  once in the GUI (slot file under `SaveStates/`) and every headless case
  reloads it by path. The fixture for A15: cursor resting on a P1 Infantry
  one tile south of a neutral city, day 3, fog off, gate `0x03004318` = 0.

Two addresses found along the way, by pressing keys and diffing IWRAM:
the **cursor is the byte pair `0x030033F0/F1`** (mirrored as u16s at
`0x030036A4/A6`), which turns menu driving from blind tap counts into
closed-loop navigation — read, compare, tap. And a **negative** result that
is itself a finding: writing a unit record's `x,y` moves the record but the
game will not select the unit at the written tile, so a tile→unit index
exists somewhere beyond the unit array. Position writes are not transparent;
real moves are driven instead.

The probes themselves are `tests/fixtures/capture_probes.json`: 26 reloaded
cases (controls, rate rows, the 18-type menu sweep) plus the two-round stay
probe played with real turns — End Turn is the fifth item of the map menu,
opened with A on an empty tile. Every case ships the screenshot it ended on,
because the drive is verified by looking, not assumed: the run that proved
the rig showed the action menu reading "Capt / Wait" before anything was
scored against it.


## 26. The counter's bracket at a damaged target, and the truncation it flushed out

The last assumption. A16 needed counters landing on a target below full HP on
starred terrain — the one configuration every earlier sweep had pinned away.
Design, all through the headless route of section 25, from the same savestate:

- My Infantry's tile written to Wood (2 stars) in the logic map, an enemy
  teleported adjacent, my HP written to 100, 81 or 57, the RNG seeded, and
  Fire driven. The observed strike damage names the survivor exactly —
  `survivor = 100 − damage` — so every (damage, counter) pair is one exact
  equation per display rule, no luck bookkeeping.
- 81 separates `ceil` (display 9, bracket 82) from `round` and the floors
  (display 8, bracket 84); 57 separates the floors (5) from `ceil`/`round`
  (6). The full-HP controls are the case where all rules agree, run FIRST —
  they also validate the two unproven writes, because their counters
  reproduce the written Wood's bracket to the point.
- One trap dodged at design time: at 57 HP a Tank's counter (58–65) exceeds
  the target's HP and the cap at `target_hp` blinds the case, so the 57 sweep
  attacks the enemy Infantry instead (counter 29–35, uncapped).

Predictions were computed and recorded before the run. Result, 27 battles:
`ceil` 27/27; `round` 0/12 on the 81 sweep; the floors 0/24 across both. The
counter's bracket rounds the damaged target's HP up, exactly like the strike
(section 17 / A9a), which is what `counter_damage()` already implemented.

**The sweep also caught a strike-formula error nothing else could reach.**
Case h81_3 dealt damage 3 where `resolve()` said the minimum was 4. The
engine carried the display-HP term as an exact fraction — `5 × 9/10 = 4.5` —
where the ROM divides via BIOS Div at `0x080232C8` and truncates BEFORE the
luck roll: `floor(4.5) + 0` then the terrain bracket gives 3. No earlier
measurement could object because base × display was divisible by 10 in every
one of them: Tank-family bases at displays 6, 9 and 10 all divide, and full
health always does. It took an Infantry (base 5) at display 9 to leave a
fraction on the table — a board no sweep had ever built, reached here as a
side effect of aiming at the counter. `v_luck_after_hp` now floors the term,
all 75 observations and 21 sweeps still reproduce (they were blind either
way), and the verification record was re-derived over the grown corpus.

The fourth time this formula hid a truncation behind a variable pinned at
the one value where the question disappears — after `floor_min1`,
base-vs-value, and the counter's luck range. The lesson is unchanged and
apparently inexhaustible: a term that never leaves the integers is a term
that has never been tested.


## 27. The CO power system, whole

The last big Unknown: the meter at army `+0x20` charged both sides of one
observed attack (+3725/+2900) and nothing else about powers was known — not
the gain formula, not the threshold, not what activation does. Two routes ran
in parallel: static reads with the offset-scan tools, and a new capability in
the headless rig — **write watchpoints with the PC attached**. Mesen's
`addMemoryCallback(write)` plus `emu.getState()["cpu.r15"]` turns "something
wrote this byte" into "0x0801BFC4 wrote this byte", and that address is where
the disassembler starts. Most of this section was found by letting the game
name its own code.

### Two null results, and the flag pair

The static route drowned first: `+0x20` halfword accesses near army-pointer
literals are mostly OTHER structs (tile iterators keep x at `+0x1e`/`+0x20`,
UI objects too), and two full clusters were read before this was accepted.

The dynamic route then returned a null that mattered: on the A15/A16 fixture,
one driven attack produced **zero writes anywhere in the army array** — while
a control watch on the defender's HP fired twice, so the rig was sound and
the meter genuinely does not charge in that match. Writing `0x03004318 = 1`
changed nothing. The user parked a fresh VS match built with the **CO Power
rule on** (savestate slot 2), and in it `0x03004318` reads 1 from the start
— settling section 24's open question: *the VS setup rule is what sets the
gate*. Charging turned out to answer to a different byte entirely:
`0x0801BF74` tests **`[0x03004317]`**, the rule's second flag. Off means no
charge, whatever the modifier gate says.

### The charge, read at its own write

With powers on, the watchpoint caught both writes at `0x0801BFC4` on the
first driven attack — Tank → Infantry, 80 internal dealt, 1 taken: attacker
+200, defender +800. The adder (`0x0801BF68`) reads the meter as a **u32**
(`+0x22` was never a field, just the high half), skips charging entirely
while that army's power is active (`+0x1E` nonzero), and **clamps at the
threshold**. The amount, computed at `0x0802D2A0`:

    value = (stats.cost/10 + pool[+0]) * record[+0x2C] / 100
    own   = value * display_HP_lost          (dead: the full display HP)
    gain  = own + other_side_own / 4         (truncating, both sides)

Three identifications fall out. `pool[+0]` is an s16 per-unit adjustment,
zero on all 18 referenced entries. `record[+0x2C]` is header byte **+08 — the
pair Kanbei reads 120/120 — and it is a unit VALUE multiplier, not the
attack/defence pair section 18 guessed**: the damage path was already fully
accounted for by `+11/+12` (A14), and here `+08` makes Kanbei's units worth
20% more meter, the same 20% his deployments cost. And the HP term is
display-HP lost, `ceil` on both ends. The formula reproduces the measured
(200, 800) exactly, and the old A-Air/Tank capture solves to display losses
(4, 3) — the unique non-negative solution, pinned as such in the tests.

### The record starts 0x24 bytes earlier than extracted

The threshold function (`0x0801C018`) indexes the CO record from
**`0x08284A0C`** — extract_co's base minus 0x24 — so the true header holds:
name pointer (+0), **power cost, u32 at +0x08**, power-name string id
(+0x0C), **per-CO activation function pointer (+0x10)**, banner style
(+0x18). Costs: Sami 25000, Drake 40000, Kanbei/Eagle/both Sturms 50000,
everyone else 30000 — the community star counts × 10000, and the menu
measurement below confirms they are activation thresholds, not lore.

    threshold = cost * (100 + 20 * uses) / 100    capped at 200% past 9 uses

`uses` is army `+0x25`, incremented per activation, saturating at 255 — the
"powers cost more each time" rule, exact. `+0x24` is a one-shot "CO Power
available" latch set by `0x0801C0A4` when the meter reaches threshold.

### Activation, driven and diffed

With the meter written to the cost and the latch set, **Power appears as the
third map-menu item** (Unit, Intel, Power, Save, Options, End). Driving it
for every record and diffing the world — army fields, weather, fog, all unit
records, write-PCs on — gave the whole effect table in one run:

  - every CO: uses+1 (`0x0801C104`), latch cleared (`0x0801C140`), **meter
    reset to 0** (`0x0801BF62`), **`+0x1E` = 1** (`0x0801C170`)
  - **Andy** drives the standard repair routine per unit with funds forced
    to 999999 and restored after — a free +2 display HP (the formula at
    `0x0801C314` is `2 + header[+0x0A] + pool[+4]`, both zero everywhere)
  - **Olaf** writes weather = snow (`0x080352C4`)
  - **Drake** subtracts 10 internal from every enemy unit, floored at 1
    internal (`0x0801C640` — mass damage cannot kill)
  - **Sturm** records differ only in the constant handed to the meteor
    object: **80 internal (record 10) vs 40 (record 11)**, hitting the
    enemy cluster within Manhattan 2 of its centre — the target-selection
    rule itself is still unread
  - **Eagle** clears unit flags bit 0 — the acted bit — via a walker over
    non-foot units that have acted; measured by acting the Tank first
  - **Max/Sami/Grit/Kanbei/Nell/Sonja**: no world writes; stat-block only

The per-unit reach of each power lives in a **descriptor table at
`0x2858C4`** (12 bytes per CO): a predicate pointer (+4: all / direct
non-foot / the four indirects / foot / non-foot-acted) and an effect pointer
(+8: heal / refresh / no-op). Its first two bytes looked exactly like bonus
magnitudes (Max 1, Sami 1, Grit 3...) and are nothing of the sort — they are
banner layout parameters, consumed only by the banner drawer. That hypothesis
died in the disassembly of the walker, which passes units to the two
pointers and reads nothing else.

### The bonuses that are not one-shots

**Sami's mobility** is the power block's movement-table set: tables 3/4/5,
which differ from 0/1/2 in exactly one way — Infantry (and Mech in snow) pay
1 on every passable terrain. Not +1 movement; terrain-cost erasure, already
sitting in the extracted data. **Grit's range** was measured by teleporting
enemies to distances 2..7 of his Artillery and enumerating Fire targets
through the cursor: control offers d∈{2,3}, power offers {2,3,4,5} and
refuses 6 — **max range +2, min range unchanged**. (The first attempt fired
a real battle instead: the activation ride-out taps had left the UI one
state ahead, and the enumeration read map tiles. The retake closes the menu
stack with B-taps before driving. The stray screenshot did show Snipe
Attack's 150-pool attack as a 133% forecast, a bonus confirmation.)

### Lifetime

Activate Olaf, end turns, watch `+0x1E` and the weather byte: both survive
the opponent's whole turn and clear at the start of the **caster's next
turn** (`0x0801BFFC`, same boundary for the snow). So a power's stat block —
including the universal 110/90 — is live while the opponent attacks, and the
meter, which does not charge passively, stays frozen at 0 until then.

Still unread: Sonja's power semantics (her record's header byte 0 is 0 where
everyone else's is 1, and her power block alone sets header byte 1 — shaped
like the HP-hiding trait and its power-side reveal, but fog territory and
unmeasured — settled in section 28); the meteor's target-selection scoring;
and header `+09`, the value pair's defence-side twin, which no code path has
been seen to read.


## 28. Sonja: the marker read whole, and the fog model grown to match

Two unknowns from section 27: Sonja's header bytes 0/1, and her vision trait.
Both fell to the same combination — find the consumer, then measure the
consumer's behaviour — and reading the game's fog marker on the way upgraded
the vision model beyond her.

### The vision computation, at last

Scanning for readers of the stats vision byte (`+0x0E` off `0x08283058`)
landed on the per-unit marker at `0x0801EC90`, and it reads the CO record:
after fetching the stats vision it adds **pool entry byte +8** — the same
per-unit-type pool the damage modifiers live in, indexed by the same
`co*292 + blk*128` — as a signed adjustment (`0x0801ED06`). Dumping +8
across all records: **Sonja and only Sonja, +1 on every type except Sub
normally, +3 under her power.** Her trait was sitting in the pool the whole
time, three bytes past the attack/defence pair section 7 read.

The same function settled three more things the model had been quietly
wrong or silent about:

  - the **mountain bonus is gated on RAM type ≤ 2** (`0x0801ECCE`) — foot
    only. Invisible until now: nothing else can stand on a mountain except
    air units, and no capture had parked one there.
  - **rain costs 1 vision, floored at 1** (`0x0801ED90`). Read, unmeasured.
  - the wood/reef concealment check consults a **per-tile unit index**
    (`0x0801EAB8`) and lights the tile anyway when an **air unit** stands on
    it — and that index at map `+0x51A` is the tile→unit structure A15
    could not find (a second layer sits at map `+0x12`; which is air and
    which is ground is not yet pinned).

And the concealment check itself opens with **CO header byte 1**
(`0x0801EA60`): nonzero, and the whole wood/reef clause is skipped. Sonja's
power block alone sets it. So **Enhanced Vision = +3 vision on everything
but Sub, plus sight INTO woods and reefs** — both halves data, not code.

### Measured against the game's own array

Fog is a writable byte (section 20), so the powers-ON fixture becomes a fog
board mid-turn: write `0x0300431D = 1`, write P2's CO, drive one real Tank
move to force the recompute, and dump the count array at `0x0201763A`. Three
captures — Andy, Sonja, Sonja with the power block written — and the model
grown with `co_vision` and `co_conceal_pierce` reproduces **all three
exactly, 150 tiles out of 150 each** (`tests/fixtures/sonja_vision_*.json`,
replayed by the fog oracle tests; both rules are load-bearing there).

One reading error caught by its own dump: the height byte next to the width
read 13, and rows 10..12 came back all zero — the board is 15×10, both HQs
inside rows 0..9. The fixture script pins 10 and says why.

### The HP question answered by a read watchpoint

Header byte 0 had no findable static reader — so the emulator found it. A
READ watchpoint on Sonja's record header in ROM, her CO written onto the
ENEMY army, a damaged Mech hovered: **`0x0802B2F6` reads header[0] every
frame** while the panel is up, and the screenshots show the panel drawing
**`?` in place of the HP digit** where Andy's shows `5`. Byte 0 is an "HP
visible to enemies" flag, 0 only on Sonja's records, consulted for the
UNIT'S OWNER — her units hide their HP from the opponent's UI, in both
blocks, display-side only. The engine exposes it as `co.hides_hp()`;
nothing else changes, because the real HP never left the unit record.

Still unread after this: the two-layer split of the tile→unit index, and
`air_over_concealment`/`rain_penalty` remain code-reads with no capture
exercising them — both carried as rules with their provenance stated, and
deliberately absent from the load-bearing test.

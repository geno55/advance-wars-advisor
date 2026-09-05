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
(The "but Sub" was an extraction artefact: the pool has 25 pointers, not
24, and the Sub's is the last one. Section 46.)

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
deliberately absent from the load-bearing test. (Both measured in section
29, the next day.)


## 29. The two unexercised rules, exercised

Section 28 shipped `rain_penalty` and `air_over_concealment` as code-reads
with no capture behind them. Both are measurements now, from boards built
with the write toolkit the harness already trusts:

- **Rain**: the weather byte is as writable as the fog byte. Write 2, drive
  the same Tank move, dump the array. The model reproduces it **150/150**;
  with the rule off it misses 32 tiles — and the **floor at 1 carries 7 of
  its own**, because the board's vision-1 units (Mechs, Artillery) still
  light their neighbours where an unfloored `v-1` would go blind.
- **Air over concealment**: no air unit exists on the map and none can be
  produced, but the occupancy check reads the unit's TYPE from its record —
  and type writes are proven transparent. So: Wood written UNDER P1's
  Infantry at (6,3) (the tile index keeps mapping it, since the unit never
  moved), its type written to BCopter, and P2's Mech real-moved onto the
  mountain at (9,3) — a legal distance-3 viewer with mountain vision 4. The
  wood tile reads **lit (1)** with the BCopter on it and **dark (0)** in the
  Infantry control, and both full arrays reproduce exactly. All seven vision
  rules are now load-bearing in the oracle test.

Two rig lessons paid for along the way. The **map cursor bytes do not track
inside move-select mode**: `goto_tile` driven navigation silently did
nothing (three "moved" units all sat where they started, caught by logging
positions off the records), so real moves are driven by counted direction
taps and verified off the unit record after — which is also why earlier
scripts that tapped `left` once worked and the one that navigated did not.
And the negative shape of the air test matters: teleporting an air unit ONTO
the wood would have proven nothing, because the tile→unit index does not
follow position writes — the test only works because the unit already stood
there and only its type changed.


## 30. The meteor's mind, and the RNG generator falling out of it

The last CO-power unknown: how Sturm's meteor picks its target. The meteor
object's template at `0x08285A44` names its phase functions, one of which
(`0x0801C9DC`) calls **`0x08063358(active_player)`** and stores the result
as the target — a UNIT SLOT, not a tile. The meteor centres on a unit.

### Three scans, chosen at random

The selector's first act is a call to `0x08010A84` — the RNG — reduced
mod 3, and the result dispatches to one of **three different scoring
scans**. All three share a skeleton: for every unit of every hostile army
(the hostility bitmask at army `+0x28`, scanned army-ascending then
slot-ascending), mark a Manhattan-2 diamond around its tile, then walk the
whole map and score the occupants — found through the tile→unit index at
map `+0x51A`, allies of the caster subtracting and units at **10 internal
HP or less contributing nothing**. The best strictly-greater score wins,
so the earliest candidate keeps a tie, and no positive score means no
target. They differ only in the per-unit value:

    strategy 0   hp × (cost/10)/10            funds value
    strategy 1   hp                           raw internal HP
    strategy 2   funds value, ×2 when stats min_range > 1   indirects doubled

### The generator, read and then confirmed twelve times

`0x08010A84` is five instructions:

    state' = ((4·state + 2) · (4·state + 3)  mod 2³²) >> 2

on the u32 at `0x03001D30` — **the combat RNG's update function, which had
never been derived**. The probe that confirmed the meteor confirmed it too:
a board rebuilt IN PLACE (type writes are transparent and keep the tile
index valid — teleports would have been invisible to the scorer) into three
clusters, each one scan's favourite:

    G1  four Infantry            raw hp 400,  value  4000
    G2  MdTank, MdTank, Tank     raw hp 300,  value 39000
    G3  a lone Battleship        raw hp 100,  weighted 56000

Twelve seeds written, twelve activations: the state read back after each
equalled `next(seed)` **exactly** — one draw per activation, no hidden
consumers — and the struck cluster matched `next(seed) % 3` twelve out of
twelve (0 → G2, 1 → G1, 2 → G3). `engine/rng.py` now carries the
generator; what it deliberately does not carry is a luck prediction,
because HOW the luck path consumes the state is still unread. That is the
obvious next kill: seed, fight once, check the roll against `next(seed) %
10`.

### The blast rules, each measured on its own

- **Friendly fire is real.** A P2 Infantry teleported into G2's blast took
  the full 80 — the applier walks records, not the tile index, so the
  teleport landed in it — which is why the scorer bothers subtracting
  allied value.
- **Units at ≤10 internal are immune**, not floored: a 5-internal Tank
  inside a struck blast stayed at 5. (Drake's tsunami floors those same
  units to 1 — the two mass-damage appliers genuinely differ.)
- **Overkill clamps at 1**: 50 internal minus 80 leaves 1. The meteor
  cannot kill.
- The damage constants are the entry functions' parameters: 80 internal
  for record 10, 40 for record 11 (`0x0801CC92`/`0x0801CCAA`).

`co.meteor_strategy/meteor_target/meteor_victims` model all of it, with one
honesty note in the docstring: the alliance test is reduced to `player !=
attacker`, exact for two-sided matches; a teamed match could diverge and
none has been captured.


## 31. The 24-byte table is the dived sub, and the board that proved it had no sea

The table at `0x08283FC8` had been an Unknown since the weapon-selection
read: a defender whose flags byte carries bit `0x20` routes the PRIMARY
lookup through it, indexed by attacker type alone. Dumping it settled the
shape in one look — **Cruiser 90, Sub 55, twenty-two zeroes** — exactly the
surfaced matrix's two anti-sub answers with everyone else deleted. So the
hypothesis wrote itself: bit `0x20` is the Dive state, and diving does not
soften the hunters' shots, it removes everyone else's entirely. A zero
entry branches to the no-primary path, and no unit in the game has a
SECONDARY against a Sub, so the primary-only gate decides everything.

The fixture map has no sea and no subs, and needed neither: Sea is a
terrain write, a Sub is a type write, and both are proven transparent. A
P2 unit turned Sub on written Sea offered **Wait / Dive** in its action
menu; driving Dive set bit `0x20` (the write landed at `0x08066E90`), and
a sub given a WRITTEN `0x20` offered **Wait / Rise** instead — the game
honours the written bit — with Rise clearing it at `0x08066EAC`. (The
first run picked Wait, because Dive is the second item; the flags
watchpoint is what said "no dive happened" instead of letting a
wrong-menu tap masquerade as a measurement.)

The battles, all four rolling the same luck 5 off the reloaded state:

    Cruiser vs dived sub      95   = 90 + 5, the table's row
    Cruiser vs surfaced sub   95   identical -- dive changes nothing here
    BCopter vs dived sub      no Fire offered, sub untouched
    BCopter vs surfaced sub   30   = 25 + 5, the control that makes the
                                   refusal above a measurement

The ammo and min_range gates sit BEFORE the dive check in the code, so
they still bind. The counter direction follows for free: a dived sub that
opens fire is countered through the same table — unobservable in legal
play, since its only contact-countering targets are the Cruiser and Sub
whose table and matrix values coincide, but the model routes it anyway.

`select_weapon` takes `defender_dived`, `Attack` carries both sides' dive
states, `Unit.dived` reads the bit, `actions.py` and `threat_map` thread
it, and the extractor now pulls the table into `aw1_damage.json` with its
own assertions. One bullet leaves ASSUMPTIONS whole: nothing about the
table is undecoded any more.


## 32. The luck consumption, and the envelope collapsing to a point

The RNG's generator fell out of the meteor probe (section 30); what
remained was how the combat luck path consumes it. One `bl` scan for the
generator's callers put a single site inside the damage function —
`0x0802333A` — and the block around it reads:

    roll  = next_state() % (10 + good) − bad     good = record +0x2A,
    total = max(0, damage_so_far + roll)          bad = record +0x2B

The reduction goes through the modulo entry at `0x080796CC`, the good/bad
bytes are the header pair A11 named, both fetched behind the same
`[0x03004318]` gate as everything else, and the flooring at 0 is the
`max(0, raw)` the engine already carried. One branch above it was new:
**settings byte `+0x06` (`0x03004316`) nonzero skips the RNG entirely and
adds a flat +5** — a fixed-average-luck mode. Which setup option sets it
is unread; every capture reads 0.

### Twenty seeded battles, and the draw index the fit forced

The sweep drives the same Tank → Infantry-on-road attack (base 75, zero
stars, full HP — so `damage − 75` IS the roll), writes the RNG between
target-select and the confirm tap, and reads both the damage and the state
after the battle. Three COs give three reductions: Andy `%10`, Nell `%20`,
Sonja `%25 − 15`.

Every one of the twenty battles advanced the state by **exactly four
draws**, and the observed roll equals the THIRD draw reduced — the unique
single index consistent with all twenty rows, which the test asserts by
refuting indices 1, 2 and 4 individually. Nell rolled a live **16** and
Sonja a live **−14**, the widened reductions on screen rather than
inferred. And with the fixed-luck byte written, the whole battle drew
NOTHING — `rng_after == seed` — and dealt exactly base + 5, which also
says all four draws of a normal battle come from this one gated block:
the resolver runs it four times, the strike keeping the third result and
the counter's draw being made and discarded, which is why A9b's sweeps
measured a counter with no luck of its own.

### What this changes

Given a read of `0x03001D30` at target-confirm, the strike's damage is a
POINT: `rng.strike_luck(state, good, bad)` returns the roll and
`damage_for_luck()` the exact number — 20/20 on the fixture. `resolve()`
keeps returning the envelope, because the advisor usually cannot read a
live state; a harness that can, predicts. The one stated boundary: the
draw index is measured on the standard drive (confirm from the forecast);
no other UI path into the resolver has been swept.


## 33. Supply, repair and the daily burn: three dead guesses and a Recon in disguise

The last hole `engine/actions.py` had named out loud: "Terrain
repair/resupply on WAIT. Real, unmeasured, absent." The handoff sketched
four unknowns — the APC's Supply command, property repair, the daily fuel
burn, the crash at fuel 0 — and, usefully, three hypotheses about where the
answers lived. All three were wrong, each killed by a read before any
emulator started, and the truth was better organised than the guesses.

### The statics: everything lives in the unit record

The handoff suggested the "repeats at +0x39.." in the stats table might be
the dived-sub burn rates, and that a who-repairs-whom table "plausibly sits
in the terrain record's unread bytes". Neither. Dumping the full records
showed stats `+0x38..+0x4B` is a **20-entry per-terrain burn table** (one
slot per terrain id, every entry identical per unit — the uniformity is now
an extraction assertion), and immediately before it, `+0x24..+0x37`, the
same shape again: the **service-class table**, nonzero where that terrain
repairs that unit. Ground: City/HQ/Base. Air: Airport. Naval: Port. The
terrain record itself held nothing but name pointer, income (1000 on
exactly the five properties), stars×10 and two graphics pointers — fully
decoded and boring. The dived rate is not data at all: `movs r6, #5` at
`0x080239DC`, a code constant.

Three side tables indexed by the 1-based RAM type: `0x08282EE5` is-a-
supplier (APC, plus the vestigial id 8 — a cut second supplier), `0x08282ECC`
can-be-supplied (every real unit), `0x08282EFE` the terrain where an OWN
property exempts the unit from that day's burn (air→Airport, naval→Port).

The repair routine the handoff had already located (`0x08029D9C`) read
cleanly: per requested bar it charges `(cost/10 + pool[+2]) ×
header[+0x2D] / 100` funds, adds 10 internal HP capped at 100, and **every
exit path snaps internal HP up to the display bar's ceiling** — finished,
broke, or already displaying 10 (91..99 becomes an exact free 100). Broke
mid-way keeps the paid bars, snaps, stops. That `+0x2D` is header `+09` —
the byte DERIVATION 27 said no code path had been seen to read. It is the
repair-cost twin of the charge path's `+0x2C`: Kanbei's 120 makes his
repairs a fifth dearer, measured later at 840 a bar for a Tank. And the
walker that calls all this hands it `1 − [0x03004357]` as the charge flag —
a settings byte that makes repairs free, which the parked VS fixture has
SET. What menu option writes it joins the fixed-luck byte in the unknowns.

The burn function (`0x08023978`) reads the per-terrain entry, skips loaded
units, skips a unit standing on its own no-burn terrain — the skip returns
before the crash check, so an empty copter on an own airport lives — forces
5 when the dive bit is up, adds the CO pool byte `+0x0A` (Eagle: −2 on the
five air slots, both blocks, the only nonzero in the game), floors fuel at
0, and returns "remove me" when fuel hits 0 and the class byte says air or
naval. The remover zeroes the type byte at `0x080243D8`.

### The dynamics: measured, with two rig lessons and one blunder

The blunder first, because it cost three runs: the fixture peek listed
"unit70 type=6" next to the APC's cargo pointer, and two whole probe rounds
supplied nothing because **type 6 is the Recon** — RAM types are 1-based,
the real APC is type 7 in slot 69, and the "MdTanks" were Mechs. The trace
run built to explain the failure instead proved the gates: the supplier
predicate returned 0 for the Recon at exactly `0x08025874`, and the
auto-supply walker scanned all seven still-standing P2 units and correctly
found no APC beside anyone. The lesson is old but newly paid for: verify
the TYPE byte against the table before building a probe on a slot's
reputation.

Also refuted en passant: DERIVATION 28's "two layers, presumably air and
ground" reading of the tile→unit index. The layers at map `+0x12` and
`+0x51A` dumped **identical** across the whole board, and a real move
updated both — they are copies, like the visibility array's twin, consulted
by different readers (auto-supply reads `+0x12`, the menu need-scan
`+0x51A`).

With the real APC:

- **The menu.** `[Drop, Supply, Wait]` with cargo aboard; Supply vanishes
  when the adjacent Tank is written full (the need-check at `0x0802588C`:
  fuel AND ammo at the stats maxima), and appears for any adjacent needy
  same-army unit at the DESTINATION of the move. Driving it filled both
  neighbours to their maxima — fuel and ammo, free, cargo untouched — and
  set the APC's acted bit. Supply-after-move works the same.
- **Turn start, in order**, off one write-PC log: income (`0x0802416A`),
  the burn walker (fuel writes at `0x08023A80`, slot-ascending, removals
  inline at `0x080243D8`), the property walker (refuel `0x08029D72` one
  point per write, re-ammo `0x08029CBC`, repair hp at `0x08029EB0` with the
  snap at `0x08029F20`, the spend at `0x0802413E`), then auto-supply (same
  helper PCs, no charge). **Crash beats supply**: two 1-fuel copters, one
  beside the APC and one far control, both burned to 0 and were removed.
- **The repair sweeps** reproduced the routine to the digit: 45→70 for
  1400; 81→**100 for 700** (the second bar is the free snap — the row that
  kills any "charge per requested bar" reading); 95→100 free; broke at 0
  funds → 45→**50**, nothing charged (the snap is why a broke army still
  creeps upward); funds 700 → 60 with an empty treasury; funds 1050 → 60
  with 350 left. Kanbei paid 840 a bar. A BCopter on an own City got
  nothing but its burn; on an own Airport it repaired, refueled to 99,
  re-ammoed to 6 and skipped the burn. A dived sub on an own Port was
  serviced straight through the dive — and its written ammo 9 wrapped mod
  16 down to the max 6, fourteen increments on the watch, because the
  refill loops run until EQUAL, not until ≥.
- **The funds override lesson**: forcing the treasury at the property
  walker's first exec write happened BEFORE income and was silently
  refilled — phase objects execute their entry every frame while waiting
  on the effect queue. The broke rows only became broke when the override
  moved to the repair routine's own entry, which re-reads funds per bar.

One free rider: the fuel spent by a move equals the PATH's movement cost,
not the tile count — a Recon paid 3 for plain+road, an APC 2 for two road
tiles — so `Action.fuel_after`, which already subtracts the Dijkstra cost,
was right all along. And the fuel byte's bit 7 is not fuel: both writers
mask around it, and a written bit rode through a full turn untouched. What
sets it remains unread.

### What shipped

`tools/extract_supply.py` → `data/aw1_supply.json` (service classes, burn
table, the three side tables, Kanbei's `+0x2D`, Eagle's `+0x0A`, the code
constants with their addresses; 78 assertions). `engine/supply.py` replays
the routine and the measured turn-start order; `engine/actions.py` now
offers SUPPLY from the supplier table with the menu's own need-gate and
attaches `turn_start` facts (burn, crash, repair with the exact charge,
auto-supply) to every action's ending tile — a completing capture is quoted
as serviced, because the city is yours by morning. The state reader dumps
the free-repair byte; old dumps get a warning and a charged assumption.
The two Mesen fixture dumpers that hardcoded `"fuel": 99` now read the
byte. 26 new tests replay `tests/fixtures/supply_probes.json` and refute
the shapes the derivation itself had to discard: display-bar repair,
all-or-nothing charging, additive dive burn, and an APC that could rescue
a 0-fuel neighbour.


## 34. Joining: display bars add, and the change comes back as funds

The handoff had set joining aside unless it fell out of the supply work for
free. It did not, so it was taken on its own: one static read, one run,
five cases, all five predicted before the run and all five exact.

The Join record in the action-menu table (`0x0828BB60`) names its executor
`0x0802E239`, which calls the merge routine **`0x0802649C`** and then the
ordinary move applier. The menu predicate is the generic destination check
`0x0802DA6C`, whose pair test at **`0x08024664`** is the whole rule: same
type byte, same army (slot & 0xC0), NEITHER record carrying cargo, and the
TARGET — the unit already on the tile — displaying fewer than 10 bars. The
mover's HP is not read. The merge itself:

- **display bars are added**, `bars_target + bars_mover`, and the survivor's
  internal HP is written as `bars × 10` (`0x0802661E`). 45 + 45 internal
  is not 90: it is 100, two five-bar units making a perfect one (J2).
- bars over 10 are **refunded** at the unit's value per bar —
  `cost/10 × header[+0x2C]/100`, the same unit-value multiplier the meter
  charge uses — through the generic funds-add writer at `0x0802416A` (the
  one income also uses). A 7+6 Tank join paid 2100 (J3) and 2520 under
  Kanbei (J4).
- fuel is the mover's **post-move** fuel plus the target's, capped at the
  stats max (30 − 3 + 30 = 57 in J1, 110 → 70 in J2); ammo is the sum,
  capped (4+4 = 8; 6+6 → 9). The fuel byte's bit 7 is OR-ed in from the
  target — another writer that carries the bit without explaining it.
- **capture progress comes from the target** (`0x08026632` copies byte +5
  above the ammo bits): J1 set the mover to 3 and the target to 7, and the
  merged unit read 7.
- the mover's record survives, acted, and the target's type byte is zeroed
  at `0x08026710`.

J5 is the refusal: a full-HP target does not even accept the destination
confirm — no menu opens and nothing is written. The join menu itself, when
it opens, is the single item `[Join]`.

`engine/join.py` replays the routine; `actions.py` offers JOIN from the
pass-through reachable set (destinations() refuses friendly tiles except
transports) filtered by the pair rule, with the exposure computed for the
merged unit on a board where the target is gone, and the turn-start facts
of the survivor. `tests/fixtures/join_probes.json` holds the rows;
`tests/test_join.py` replays them and refutes the internal-sum, lost-excess
and mover-keeps-capture readings.


## 35. Unloading: the transport's table, the passenger's feet, and the tile it just left

The last unit action `actions.py` had refused. Two reasons were on record:
the reader's single cargo slot, and a rule never put in front of the game.
Both are closed.

### The read

The action-menu table carries two Drop records (`0x0828BB20`/`BB40`), one
per cargo slot — their predicates differ only in reading record `+7` or
`+8`, which also settles the **second cargo slot at `+8`** the Cruiser's
turn-start cargo walker (DERIVATION 33) had already indexed as `+7+i`. The
reader dumps `cargo2` now and `transport_has_room` counts both slots. Each
predicate wants two things:

- the TRANSPORT standing on a terrain its cargo struct flags at
  `+0x1A+terrain` — the struct's "unidentified" tail, now `unload_from` in
  the unit-stats extraction: APC anywhere but river/mountain (which it
  cannot reach anyway), TCopter river and mountain too, **Lander port and
  shoal only**, Cruiser sea/port/reef plus a river and a shoal it can
  never stand on — authored over the whole terrain space, like the
  Lander's cargo mask;
- at least one neighbour qualifying under `0x080255EC`: in bounds, EMPTY in
  the tile→unit index, and the **PASSENGER's** stats `+0x4C+terrain` byte
  ≥ 0. That `+0x4C` block is a per-unit passability table whose SIGN
  matches the clear movement table on every real terrain (asserted at
  extraction) but whose magnitudes do not (tires read 4 on Wood where
  movement says 3) — it is consulted for standing, not moving.

The executors set the slot index and hand off to the drop phase; the apply
writes are the passenger's flags = acted with the loaded bit cleared
(`0x0802642E`), its position (`0x08026444/58`), the transport's slot zeroed
(`0x08026462`) and its carrying bit cleared (`0x08026484`), then the
ordinary move applier — so the transport is acted too: one drop per turn.

### The drives

Seven cases, APC69 and its Infantry on the slot-2 fixture:

- in place, and after a move: the passenger lands on the selector's
  default (north), acted, fuel and hp untouched; the transport ends
  acted with the slot and carrying bit clear.
- sea written east and west, a Tank real-moved north: the selector held
  only the south tile and the Infantry landed there — occupancy and
  terrain both measured in one row.
- sea on all four sides: the Drop item is gone, the menu reads `[Wait]`.
- the passenger typed Tank with mountains north and south: only the road
  east was offered — the PASSENGER's passability, since the APC itself
  could not enter a mountain in either case; the Infantry control took
  the mountain.
- **the origin tile is free**: the APC moved one tile west with sea on its
  other three neighbours, Drop was still offered, and the Infantry landed
  on the tile the APC had just left. The index the predicate reads has the
  mover already gone — `drop_tiles()` ignores the transport's own record
  accordingly.

Unmeasured and stated as such: the selector's default tile (north in every
drive with north free; the mask order is W,E,N,S, so the default is not the
mask's first bit — left as a recorded observation, not a rule), and the
second-slot drop itself, which no fixture can reach without fabricating a
loaded state; the model carries it from the two-record read.

### What shipped

`unload_from` and `can_stand` in `data/aw1_unit_stats.json` (extractor
assertions grew to 402), `cargo2` through reader, `Board` and pathing,
`engine/unload.py`, DROP actions in `actions.py` — one per passenger and
landing tile, with the dropped unit's exposure and turn-start facts — the
seven rows in `tests/fixtures/drop_probes.json`, `tests/test_unload.py`,
and the report. The "WHAT IS NOT MODELLED" list is down to production and
power activation, both army actions.


## 36. Production: one list, three masks, fifty slots

The last refusal in `actions.py`. The fixture map has no factory, which
turned out not to matter: terrain writes are transparent, so a Base, an
Airport and a Port were written under an empty plain and pressed.

### The read

The build menu is **one 18-entry list at `0x08080D14`**, `-1`-terminated, in
the menu's order — Infantry, Mech, Recon, Tank, MdTank, APC, Artillery,
Rockets, AntiAir, Missiles, then the four air units, then the four ships.
The shop builder (`0x0802E818`) walks it and keeps every unit whose class
byte (stats `+0x14`) intersects a mask the factory terrain selected:
terrain id − 8 indexes a jump table at `0x0802E7C0`, landing on **7 for a
Base, `0x10` for an Airport, `0x20` for a Port** (a fourth branch, `8`,
answers to no real terrain). Per entry it prices the unit — `(cost/10 ×
header[+0x2C] / 100) × 10`, the unit-value multiplier yet again — and
flags it unaffordable when `funds < price` (`0x0802E904`, so exact funds
buy). A purchase goes through `0x0802425C`: the **free-slot finder at
`0x080240EC` scans `base+1 .. base+50`** and returns nothing past that —
fifty units an army, no exceptions — then the unit is initialised at
`0x080241DC` (acted, hp 100, fuel and ammo at the stats maxima, capture and
cargo zero), positioned, and the spend routine (`0x08024128`) takes the
price and adds it to the army's lifetime spend at `+4`, capped at 999999.
The spawn also bumps a per-army per-type built counter at `army+0x36+type`.

### The drives

Eleven presses on written factories, two scripts:

- the three shops on screen, lists and prices matching the read exactly;
  Kanbei's Airport read 24000/26400/10800/6000 — ×1.2 across the board.
- Infantry bought for 1000 at Andy's Base, for 1200 at Kanbei's; the new
  record in slot 73 (lowest free above the eight in 65..72) with flags 1,
  fuel 99, ammo 0, hp 100; after freeing slot 65 the next purchase took 65.
- funds 900: the shop opened, Infantry greyed at 1000, A did nothing;
  funds 1000: bought, treasury 0.
- A on the own HQ, a neutral Base and an enemy Base: the map menu, not a
  shop, every time.

### What shipped

`shop_order` in the unit-stats extraction, `engine/production.py` (shops,
prices, offers, the slot finder and its cap), `actions.build_actions()` —
the army's purchases as Actions with the new unit's exposure and
turn-start facts, unaffordable offers listed and flagged rather than
hidden, a warning when all fifty slots are full — `tests/fixtures/
build_probes.json`, `tests/test_production.py`, and the report's factory
lines. With that, every unit-facing mechanic the advisor once declined is
enumerated from measured data; what remains un-offered is CO power
activation, which `engine/co.py` models and an army module can now pick
up beside `build_actions`.


## 37. CO power activation, offered: the meter is the gate, the latch is a bystander

DERIVATION 27 derived the power system whole and left one thing
un-offered: activation as an action. What was missing was not the effects
— those are measured and modelled in `engine/co.py` — but the GATE: which
army field makes the map menu's Power item appear, so the advisor can say
"you could fire it now" from a dump.

Eight map-menu presses on the slot-2 fixture, each after writing the
army's meter (`+0x20`), ready latch (`+0x24`), power-active block (`+0x1E`)
and activation count (`+0x25`):

- latch set, meter empty: no item. Meter at the cost, latch clear: **item**.
  Both: item. So the menu reads the METER and ignores the one-shot latch.
- meter at the cost with the power-active block already set: item — which
  normal play cannot reach, since activation zeroes the meter and the adder
  skips charging while the block is up; the model excludes it and says why.
- uses written to 1: meter 30000 no item, 36000 item, **35990 no item** —
  the comparison is `meter >= cost × (100 + 20·uses) / 100`, the threshold
  function DERIVATION 27 read at `0x0801C018`, to the unit.

`engine/power.py` turns this into facts for the board in hand: available
or not, meter against threshold, what the next threshold will be, and what
activating does — Andy's heals per unit through the same snap-to-bar
repair routine (45 → 70, free), Eagle's refreshed units (acted, non-foot,
not loaded), Drake's per-enemy damage floored at 1, Sturm's three candidate
meteors with their victims (the RNG draw picks one; a state read would
pin it), Olaf's snow, and the power stat block everyone gets. The reader
now dumps the meter as the u32 it is, plus `power_active`, `power_ready`
and `power_uses`; old dumps get the uses assumed 0, out loud.
`actions.power_action(board, player)` offers it beside `build_actions()`.
Not composed here: the turn AFTER activation (Eagle's refreshed Tank
attacking) — a caller builds that from `actions_for` on the refreshed
board, which is one `dataclasses.replace` per refreshed unit.

With this, nothing the advisor's docstrings once declined is left
un-offered: every unit action and both army actions enumerate from
measured data. The remaining gap in the action layer is the fog ambush
rule in pathing, which is stated where it lives.


## 38. The fog ambush: enterable, not expandable, and the move ends where it stops

The one caveat left in `actions.py`: "Hidden units interrupting movement
under fog. Pathing does not model the ambush rule." Measured now, and the
rule is smaller and sharper than the sentence.

### Building a hidden enemy

The fixture's P1 units all sit in P2's sight, so the board was arranged:
fog written on (`0x0300431D`, proven to drive the vision array), P1's Mech
real-moved from (7,6) onto the road at (6,7) during P1's own turn, and P2's
Tank at (9,7) typed to an APC (vision 1) before P2's turn began, so that at
P2's turn start the Mech three tiles west read 0 in the game's vision array.
Two rig lessons on the way: under fog the turn-start card waits for a
press, and the cursor then rests ON a unit, so the dismissing taps select it
and freeze the cursor bytes (DERIVATION 29's trap in a new coat) — the
driver now cancels with B after every turn change.

### The grid says it first

With the APC selected, the game's own move-select grid
(`[0x03003600 + y·4][x]`, DERIVATION 13) read, row 7, from x=5: **255, 3,
2, 1, 0** under fog — the hidden Mech's tile (6,7) is reachable at its
honest path cost, and (5,7) beyond it, reachable only through the Mech, is
not. With fog off the same row reads 255, **255**, 2, 1, 0: the visible
enemy's tile is excluded as always, and nothing else changes. So **a hidden
enemy's tile is enterable but not expandable**: the fill marks it as a
destination and refuses to continue through it. The overlay therefore
does not reach past a hidden unit — only around it, if another route
exists.

### The drive says the rest

Three confirms, each from the same prepared board:

- two tiles west (7,7): an ordinary move — fuel −2, the action menu, Wait.
- three tiles west, **onto the hidden tile**: the unit moved to (7,7) and
  stopped; fuel −2, for the two tiles actually travelled; the acted bit
  set at once (`0x0802609C`) with no menu — the action is spent. The
  Trap. The Mech was lit afterwards, but the mover's own vision 1 reaches
  it from (7,7), so whether the game reveals an ambusher independently of
  vision is not separable on this board and is not claimed.
- four tiles west, past it: the confirm was refused outright, as the grid
  predicted.

### What shipped

`engine/pathing.py` keeps `reachable()` as the true pass-through set and
adds `trap_tiles()`: the hidden-enemy tiles the game's grid would offer,
each with the tile the mover would stop on (the cheapest adjacent reachable
tile — the game walks its drawn route, so a tie between approaches is a
stated caveat) and the cost actually paid. `actions.py` offers them as
kind `"trap"`: the tile the player would pick, `stop_tile`, fuel for the
tiles travelled, acted, exposure and turn-start facts AT THE STOP TILE —
because a board that knows where the hidden units are should say "that
tile ends your move at (7,7)" rather than pretend it is a destination. The
rows are `tests/fixtures/ambush_probes.json`; `tests/test_ambush.py`
replays the grid against the model's reach with fog on and off.

## 39. Income: one multiplication, and the 9500 was never a mystery

The supply runs left a loose number. Every P2 turn start wrote **+9500** at
the generic funds-add writer, and `income_per_property` in the terrain
struct reads **1000**, and P2 owned exactly its HQ. So the obvious reading —
1000 × owned — was off by an order of magnitude, and the obvious escape
hatch — "HQ must not count" — makes it worse, predicting zero.

Both readings are wrong in the same place: the terrain struct's income field
is not what the income path reads.

### The path, top to bottom

Three call sites reach the funds-add writer (`0x08024154`, army `+0`, capped
at `0x000F423F` = 999,999). The turn-start one is `0x080253C2`, the tail of
the payer at `0x08025310`, which sits directly after the property walker
that ends at `0x0802530A`. The walker accumulates into **army `+0x08`** —
the field the reader already ships as `Army.income` — bumping per-type
counters at `+0x0D/+0x0E/+0x0F` as it goes, and it gets each tile's
contribution from a helper at `0x08025138`.

That helper is the whole answer:

```
08025138  lsls/lsrs r0, #24        ; terrain id, byte
0802513C  subs  r0, #6             ; ids below 6 fall out
0802513E  cmp   r0, #0xc
08025140  bhi   0x08025190         ; ... and above 18
08025142  lsls  r0, #2
08025144  ldr   r1, =0x08025150    ; the jump table
0802514A  mov   pc, r0
...
08025184  ldr   r0, =0x03004310    ; the battle-settings struct
08025186  ldr   r0, [r0, #0x28]    ; THE RATE
08025188  b     0x08025192         ; bx lr
08025190  movs  r0, #0             ; pays nothing
```

Thirteen jump-table entries, ids 6..18, and every one of them points at
either `0x08025184` or `0x08025190`. There is no third body, no per-terrain
amount, no read of the terrain struct anywhere on the path. Reading the
table settles which terrains pay without driving anything:

| pays | id 6 City · 8 HQ · 10 Airport · 11 Port · 14 Base | (+ unused slots 17, 18) |
|---|---|---|
| pays nothing | 7 Sea · 9 Sky · 12 Bridge · 13 Shoal · 15 · 16 | |

**HQ pays.** And the amount is a single u32 at **`0x03004338`** — battle
settings `+0x28`, the same struct that holds weather at `+0x2C`, fog at
`+0x0D`, fixed-luck at `+0x06` and free-repair at `+0x47`. It is a SETTING.
The parked VS fixture has it at 9500 because that was picked on the VS setup
screen; one HQ × 9500 = 9500, exactly what the writer logged.

### Two dead terms, closed by exhaustion

The payer does not just forward the walker's sum. It reads the CO record —
`0x08284A0C + co*292 + block*128`, the table `aw1_co.json` already
describes, indexed by army `+0x1D` and `+0x1E` — and adds the signed
halfword at `+0x28`; when the turn block's day reads 1 it adds `+0x26` as
well. Two candidate rules sitting right there in the code: a per-CO income
modifier and a day-one bonus.

Both fields read **zero for all twelve COs in both stat blocks**. That is
the entire domain, so this is not evidence, it is proof: no AW1 CO modifies
income, and there is no day-one bonus. `test_the_co_income_terms_are_dead`
re-reads all 48 halfwords rather than trusting this paragraph. (The gate on
the CO term, settings `+0x08`, falls back to CO record index 1 when clear —
the same shape as the `+0x07` CO-power rule byte at `0x03004317`.)

So income collapses to:

```
income(player) = rate × |{tiles owned by player, terrain ∈ {City, HQ, Airport, Port, Base}}|
```

### Checked against eleven boards it was not fitted to

`Army.income` is the game's own running total, so every parked state already
carries the answer — the model just has to reproduce it. Across the eleven
full-board fixtures, **two maps, two rate settings, property counts 0 to 9,
four armies each**: every single army matches to the funds.

| fixture | rate | armies (properties → income) |
|---|---|---|
| `fog_vision_15x10` | 1000 | P1 2→2000, P2 1→1000, P3/P4 0→0 |
| `fog_vision_19x16_*` | 1000 | P1 8→8000, P2 7→7000, P3 9→9000 |
| `sonja_vision_*`, `fog_vision_airwood/groundwood/rain` | 9500 | P1 1→9500, P2 1→9500 |

The 1000 rows and the 9500 rows are the same rule with a different cell, and
having both is what makes the claim testable at all: a corpus at one rate
cannot tell a setting from a constant. The 9500 rows refute "terrain
constant"; the HQ-only rows refute "HQ is not a property"; the neutral
Cities on the 19×16 map refute "count every property on the board".

### Campaign, without a campaign savestate

Campaign is the advisor's real target and no campaign state has been dumped.
It does not matter, and that is a claim about the code rather than a hope:
the helper at `0x08025138` has no mode branch, the game has exactly one
income path, and the only three callers of the funds writer are this one,
the join refund and the shop. Campaign can differ only in **what value it
writes into `0x03004338`**, never in the rule.

Better still, the advisor need not read that cell at all. Any army holding
at least one property publishes the rate through its own `+0x08`:
`rate = income / properties`. So `funds_rate()` prefers the dumped cell,
falls back to deriving it from the board, and only then to 1000 — and says
which of the three it did, so a plan built on the guess can refuse to quote
a forecast. A campaign dump gets the right rate whatever the campaign sets.

**Still unread:** which value campaign writes there (almost certainly 1000,
the terrain struct's parallel constant, but that is an expectation and it is
labelled as one), and which setup option writes the `+0x08` CO gate. Neither
blocks anything: one read of `0x03004338` on any parked campaign state
closes the first.

### What shipped

`engine/economy.py`: `FUNDING_TERRAIN` from the jump table, `properties()`,
`property_tiles()`, `funds_rate()` with its source label, `income()`
returning an `Income` record that carries the game's own figure beside the
model's, `check()` for the per-dump regression, and `forecast()` — the one
function here that is an ASSUMPTION rather than a fact, because it holds the
property set still, and it says so. `harness/mgba_state.lua` ships
`funds_per_property`; `state.py` carries it as `Board.funds_per_property`,
`None` for dumps that predate the field. `tests/test_economy.py` replays all
eleven boards, refutes the four wrong readings, and re-derives the jump
table and the dead CO terms from the ROM.


## 40. Dive and Rise: the menu's own gates, read off the predicate table

The action layer offered every menu item but two. Dive and Rise had been
measured as far as the flag (DERIVATION 31: Wait/Dive on a surfaced Sub,
Wait/Rise on a written `0x20`, the bit set at `0x08066E90` and cleared at
`0x08066EAC`) and then left out of `actions_for`, unstated — a planner built
on that layer could never choose to submerge. Closing it meant reading WHO
the game offers the two items to, so the engine could gate them on a table
field like everything else rather than on a name.

### The table

The handoff cited the unit action menu table at `0x0828BB00`; it starts at
**`0x0828BA80`** and has twelve entries of 0x20 bytes, not ten. Each entry
is `{predicate, 0, 0, 0, action, common, label, id}`; the label pointers
resolve to the 2-byte-prefixed strings at `0x0828B6B4..`:

```
0x0828BA80  pred 0x0802DB99  act 0x0802E43D  "Fire"    id 0x19
0x0828BAA0  pred 0x0802DC3D  act 0x0802E43D  "Fire"    id 0x18   (the second Fire)
0x0828BAC0  pred 0x0802DB59  act 0x0802E1B1  "Capt"    id 0x18
0x0828BAE0  pred 0x0802DB19  act 0x0802E1B1  "Capt*"   id 0x1C   (the second Capt)
0x0828BB00  pred 0x0802DD5D  act 0x0802E1F5  "Load"    id 0x1D
0x0828BB20  pred 0x0802DD85  act 0x0802E2B5  "Drop"    id 0x1E
0x0828BB40  pred 0x0802DDC1  act 0x0802E2ED  "Drop"    id 0x1F   (second cargo slot)
0x0828BB60  pred 0x0802DA6D  act 0x0802E239  "Join"    id 0x20
0x0828BB80  pred 0x0802DDFD  act 0x0802E2A5  "Supply"  id 0x21
0x0828BBA0  pred 0x0802DA31  act 0x0802E149  "Wait"    id 0x1B
0x0828BBC0  pred 0x0802DE4D  act 0x0802E30D  "Dive"    id 0x1B
0x0828BBE0  pred 0x0802DE89  act 0x0802E349  "Rise"    id 0xFF
```

A predicate returns **0 to offer** and 1 to suppress — Wait's (`0x0802DA30`)
returns 1 exactly when the destination's tile→unit byte (map `+0x12`) is
nonzero, i.e. the move is onto a transport, which is why a load offers no
Wait. Join's (`0x0802DA6C`) returns 0 when that byte is nonzero AND the pair
check at `0x08024664` passes; Load's (`0x0802DD5C`) returns 0 when the
destination check at `0x08025AA4` says 1. Both are reused below.

### The Dive predicate, `0x0802DE4C`

```
0802DE4E  ldr   r0, =0x03004464        ; -> the selected unit's record
0802DE52  ldrb  r0, [r1]               ; +0: RAM type, 1-BASED
0802DE54  cmp   r0, #0x18              ; 0x18 = 24 = Sub (id 23)
0802DE56  bne   suppress
0802DE58  movs  r0, #0x20
0802DE5A  ldrb  r1, [r1, #1]           ; +1: flags
0802DE5C  ands  r0, r1
0802DE60  bne   suppress               ; already dived
0802DE62  bl    0x0802DA6C             ; Join predicate ...
0802DE6A  beq   suppress               ; ... offered? then no Dive
0802DE6C  bl    0x0802DD5C             ; Load predicate ...
0802DE74  beq   suppress               ; ... offered? then no Dive
0802DE76  movs  r0, #0                 ; offer
```

So Dive is offered iff the unit's type byte is `0x18`, the dive bit is
clear, and the destination is neither a join nor a load. **No terrain
test**: any tile the Sub may stop on, Port and Reef included, will do.

### The Rise predicate, `0x0802DE88`

Same shape with two differences: the Join and Load exclusions come first,
and then only `flags & 0x20 != 0` decides — **there is no type compare**.
Any record with the bit up gets Rise, which is exactly why the written
`0x20` in DERIVATION 31 produced Wait/Rise.

### The actions, `0x0802E30C` and `0x0802E348`

Each sets the menu-busy byte, plays the confirm, calls its bit writer
(`0x08066E7C` sets, `0x08066E98` clears — the DERIVATION 31 write PCs are
their `bx lr`), then `0x08026060`, the same end-of-action call every other
item makes, and a sound. No fuel write, no funds write, nothing else. The
CPU has its own dispatcher into the same two writers at `0x08066A98/9E`.

### What shipped

`tools/extract_units.py` reads the immediate off `cmp r0, #imm` (asserting
the `ldrb r0,[r1]` before it and the `movs r0,#0x20` after) and emits
**`can_dive`** per unit, labelled in the JSON as a code constant and not a
table column, with the gate recorded under `dive_gate`; the extractor also
asserts the diver is the one unit whose target-class byte reads `sub` —
two independent reads agreeing. `actions_for` offers `"dive"` to
`can_dive` units with the bit clear and `"rise"` to ANY unit with the bit
set, on every wait destination (which is already the non-load, non-join
set), with the exposure and turn-start facts computed for the unit as it
will then stand. Getting that exposure right exposed a bug: `threat.py`
built every enemy attack without the defender's dive state, so a dived
sub's wait was scored as if a Bomber could reach it. `_Damage` now passes
both dive states through.

**Not measured, stated:** the enemy's view of a submerged sub. The game is
expected to hide it unless an enemy unit is adjacent; nothing in the
harness reads the opponent's visibility, so a dive's exposure counts every
hunter that can reach the tile. ASSUMPTIONS carries it as Unknown. Also
unobserved: whether the CPU's dispatcher shares the menu's gates — it does
not matter to the advisor, which never enumerates for the CPU.


## 41. The dived sub's concealment: shown to whoever is beside it, owns the tile, or owns it

DERIVATION 40 left one thing stated rather than measured: what the enemy is
shown of a submerged sub. The expectation was "hidden unless adjacent"; the
game turned out to have three reveal branches and a move grid that behaves
unlike fog. Three headless runs, nine driven cases, all on the savestate-2
fixture with fog written off: row y=7 written to Sea from x=6 to x=10 plus
(7,6), P2's Tank 71 typed Cruiser at (9,7), and on P1's turn the Mech 3
typed Sub, real-moved from (7,6) and dived through its own menu (`Wait /
Dive`; `Fire / Wait / Dive` when it dives beside the Cruiser -- the second
run's W-case picked Wait by mistake and the third redid it). Scripts
`harness/mesen_subhide.lua`, `_subhide2.lua`, `_subhide3.lua`; fixture
`tests/fixtures/sub_conceal_probes.json`; screenshots `harness/out/sh*`.

### The check, read first

A scan for readers of flags bit `0x20` beside a flags-byte load turned up
21 sites. One small function, **`0x08023BFC(unit, x, y)`**, reads only the
map and unit pointers and returns:

```
1                                   if the unit is not dived
1                                   if army[owner]  +0x1C & 2
1                                   if army[terrain byte >> 5] +0x1C & 2     (the tile's owner)
1                                   if any of the 4 neighbours (tile->unit index map+0x51A)
                                       holds a unit whose army +0x1C & 2
0                                   otherwise
```

Its wrapper `0x08023D2C(slot)` is called from the sprite loop at
`0x0801FE2A`, which skips drawing the unit when it returns 0. A twin at
`0x08023DD0` does the same with team compares (`army +0x26`) for the AI
(`0x08060B40`). **Army `+0x1C`** read `03` on the active army and `00` on
the other, on both P1's and P2's turns -- the active side's flag. So the
rule is: a submerged sub is shown to the viewing side iff it is theirs, or
it stands on their property, or one of their units stands beside it.

### Measured

| case | setup | P2's grid at (7,7) | shown? | Fire? |
|---|---|---|---|---|
| H0 | surfaced control | 255 | drawn | — |
| H1 | dived, nothing adjacent | **2**, and (6,7)/(7,6) at 3 | not drawn; A on the tile opens the map menu | — |
| H2 | Cruiser moves to (8,7), adjacent | — | — | menu `Wait` only; 0 dmg |
| H3 | Cruiser confirms onto (7,7) | — | — | stops at (8,7), fuel −1, acted, no menu |
| V4 | Cruiser confirms onto (6,7), beyond | arrow drawn through (7,7) | — | stops at (8,7), same |
| V2 | Cruiser parked at (8,7) the turn before | — | — | `Fire / Wait`; **95** (90+5) |
| W1 | sub dives at (8,7), beside the Cruiser | (8,7) = 255 | shown | `Fire` first; **95** |
| W2 | (7,7) written as a **P2 Port** before P1 ends | 255 | shown | Cruiser moves in, fires: **66** = 95 × 0.7 |
| W3 | P2's **APC** parks at (7,8) first | 255 | shown | Cruiser moves in, fires: **95** |

Three things the expectation did not have. First, the **move grid enters
and expands through** a concealed sub -- (6,7) and (7,6) at 3 -- where the
fog rule (DERIVATION 38) enters a hidden tile and never leaves it. The trap
is the same on confirm: the walker stops where the route meets the sub. It
follows that tiles beyond the sub are traps too (V4). Second, a hunter
that **becomes adjacent by moving gets no Fire that action** (H2): the
check reads the tile index, which still holds the mover's origin when its
menu is built. Parked adjacent the turn before, it fires (V2). Third, any
unit of the side, not only a hunter, reveals the sub by parking beside it
(W3), and the property branch reveals it to everyone (W2) -- in both a
hunter that then moves in fires the same turn. The dived table's numbers
held throughout (90 + luck 5, × 0.7 on the port's 3 stars), and the dived
sub's flat 5 burn showed again (65 → 60 across P1's turn start).

### What shipped

`fog.concealed(board, unit, viewer)` is the check, with the alliance
reduced to `player == viewer` as everywhere. `threat.hostiles` drops
concealed enemy subs in the clear as under fog -- not a target, not a
projected attacker -- and `threats_to` warns. For the sub's OWN exposure
`threat._reveal_filter` keeps a hunter only if it is parked adjacent, the
sub sits on its side's property, or another hostile unit can end its move
beside the sub while leaving the hunter a firing tile; the hunters dropped
ride on `FocusFire.unseen`, and each kept `Threat` says which branch let it
fire (`revealed_by`). `pathing.conceal_traps` rebuilds the game's grid with
the sub removed and turns every tile our own fill does not reach at that
cost into a trap stopping where the cheapest route meets the sub;
`actions_for` offers them as kind `"trap"` in the clear. Stated
approximations: the revealer is any hostile that can END beside the sub,
one revealer/hunter tile pair is checked rather than a full assignment,
equal-cost detours around the sub are listed as ordinary destinations
although the game's own arrow may run through it, and under fog a
submerged sub on an unlit tile is handled by the fog rule -- which of the
two grids the game uses when both apply is unread.


## 42. The forward model, and the one rule it could not read: a Wait keeps the capture

`engine/sim.py` is the handoff's first deliverable: `apply(board, action)
-> board` for every action kind, `battle()` resolving a strike to a point
once the roll is chosen, and `end_turn()` / `turn_start()` for the boundary
-- expiry of the power block and Olaf's snow, income, then the burn,
property and auto-supply walkers in slot order with the treasury threaded
from one repair to the next. Everything it does is a composition of
measured modules (DERIVATION 17, 27, 31-41), and the action layer's four
hand-built hypothetical boards (`_after_trade`, `_loaded_board`,
`_merged_board`, `_dropped_board`) are gone: `actions_for` now builds each
Action first, advances the board through `sim.apply(luck="min")` -- the same
low-roll world it always scored exposure in -- and scores on that. One
side effect surfaced by making the two agree: the attack quote had never
carried the active power block into `Attack.between`, though DERIVATION 27
measured the block live through the opponent's turn; both layers pass the
flags now.

One record edit had no measurement behind it. Moving off a property resets
the capture field (A15), but what does a **Wait in place** do to it -- the
game's move applier runs either way. `harness/mesen_capwait.lua` on the
A15 fixture:

```
after-cap1      (4,1)  capture 10  acted  city neutral      Capt from the south
p1-day2         (4,1)  capture 10                           two End Turns later
after-wait      (4,1)  capture 10  acted                    Capt / Wait -> Wait
p1-day3         (4,1)  capture 10
after-cap-day3  (4,1)  capture  0  acted  city P1           Capt: 10 + 10 = 20
```

The Wait kept the 10, and the next Capt continued from it. So the reset
is tied to changing tile, not to skipping a capture turn, and `sim._moved`
clears the field only when the tile changes; an attack from the tile is
taken to be the same case and is stated as such. (The probe's turn-change
poll read the active byte as 0 throughout on this fixture, so its helper
reported `false` each time -- the day counter and the acted bit show the
turns ran; the poll address is not this fixture's, a harness note and not
a game finding.)

Stated in `sim.py` rather than measured, each a line in ASSUMPTIONS: the
weather Olaf's snow reverts to (Clear), Sturm's strategy when none is given
(0, with a warning), no meter charge from mass damage, no resupply of a
passenger by its Cruiser, the HQ changing hands without ending the match,
and income obeying the shop's 999,999 clamp. The differential test the
handoff designed -- dump, `apply()`, drive one action, dump, diff -- is what
certifies the composition; `tests/test_sim.py` pins the wiring against the
modules until it runs.


## 43. The differential test: sixty-three drives, nine contradictions, and what each one was

ROADMAP step 2. Everything `engine/sim.py` composes had been measured on
its own; the composition had not. The handoff designed the check -- one
parked state, ONE action: dump, `apply()` in Python, drive the same action
on the game, dump again, diff every field -- and this section is that check
run, the rig it took, and the nine places the game disagreed.

### The rig

Three pieces, all in the repo:

- `harness/mesen_state.lua` -- `mgba_state.lua` ported to Mesen's API, the
  same addresses, the same JSON schema `engine/state.py` loads, plus the
  RNG and the cursor. A library on a global table, not a script. Asserted
  against the mGBA dump of the same map: the two parked states
  (`tests/fixtures/sim_diff/states/`) match `fog_vision_15x10.json` tile
  for tile, property list included -- both savestates turn out to be the
  same 15x10 VS map.
- `harness/mesen_drive.lua` -- one Action executed with read-back after
  every step and reload-and-retry on a miss: `goto_tile` closed-loop on the
  cursor bytes, the move as counted direction taps along `pathing.path()`,
  the menu item by its index in the offered list, the Fire target cursor,
  the drop selector, the shop, the Power item, End Turn with the fog card
  ridden out. The checks are per kind (position and acted bit, the target
  hit or the RNG drawn, the capture field or the tile's owner, the loaded
  bit, the partner gone, the dive bit, the new slot, the active player).
- `tools/sim_diff.py` -- compiles `tests/fixtures/sim_diff/corpus.json`
  into driver steps from the engine's own facts (the route from
  `pathing.path`, the menu index from `actions_for`'s offers in the table
  order of DERIVATION 40, the shop index from `production.shop`), writes
  one Lua script, launches Mesen, and diffs `apply()` on the before-dump
  against the after-dump: every unit field, every army field, the turn
  block, weather, fog, both grids.

Two things the handoff wanted measured first. **Wall clock**: the two-state
dump takes 2 seconds end to end; the 63-case run took 907 seconds with
every case landing on its first attempt -- about 14 seconds a drive
including the reload, a multi-step setup counting extra. Headless Mesen is
well over real time, so the corpus is a background run, not an afternoon.
**The Fire target cursor**: the cursor bytes read the target's tile after
the Fire pick in all twelve attacks, so the driver steers closed-loop there
too; the blind fallback never fired. The drop selector's north default held
(`drop-in-place-north` with no taps), and one tap in the landing direction
from that default reached east, south and west.

### The corpus

Sixty-three drives over two parked states (the P2-to-move VS fixture and
the A15 capture fixture): six waits, twelve attacks (a counter, a kill, an
attacker dying to the counter, an indirect shot, a secondary-only attacker,
a bazooka spending a round, Max, Sonja, Nell, Kanbei defending, a written
Max power block, a four-star mountain target), three captures (a full
Mech, Sami, a 45-HP Mech), two supplies, two loads, four drops, three
joins (a refund, Kanbei's refund, the caps with inherited capture), Dive
and Rise, the fog ambush, seven builds (three shops, Kanbei's price, exact
funds, slot reuse), seven powers (Andy, Max, Eagle, Drake, Olaf, Sturm, a
second use), eight End Turns (income, burn and crash, repair free and
charged, a written rate, auto-supply, Olaf's expiry, the funds cap) and the
capture-keeping rows of DERIVATION 42 replayed. Boards were shaped with
transparent writes only -- type, hp, ammo, fuel, capture, the dive bit,
terrain, army fields, settings -- and driven setup steps where a unit had
to stand somewhere; no position was ever written.

### Nine contradictions, and one from the smoke run

The first full run: 54 agree, 9 differ. Each was a finding.

1. **End Turn clears the ending player's acted bits, and a passenger keeps
   its own.** Three rows (`end-turn-p2-to-p1`, `-burn-and-crash`,
   `-income-cap`) had P2's acted Infantry #65 predicted acted after P2's
   End Turn and read clear; two rows (`-auto-supply`,
   `-power-expiry-snow`) had the loaded Infantry #66 predicted clear at
   P2's next turn start and read acted -- flag byte `0x0B` throughout,
   across four boundaries. The model cleared at the new player's turn
   start and cleared everyone; now `end_turn` clears the ending side's
   non-passengers and `turn_start` leaves passengers alone. Whether the
   clear happens at End Turn or at the next side's start is not separable
   from turn-boundary dumps and does not matter to a planner.
2. **A finished capture raises army +0x08 at once.** `a15-capture-completes`:
   the city fell and P1's income field read 19000 before any turn start.
   `_apply_capture` refreshes the field for both sides of the transfer, at
   the rate derived from the board before the tile changed hands.
3. **The strike's draw depends on whether a counter is POSSIBLE.**
   `attack-artillery-in-place` predicted 90 damage and read 91: the
   resolution made two RNG draws (counted from the confirm-time state to
   the after-dump's) and the strike took the second. Every countered
   battle made four with the strike third, `attack-tank-kills-mech`
   included -- the Mech died to the strike and never fired, and the roll
   was still draw three. So DERIVATION 32's four-draws-strike-third holds
   when the defender's weapon can answer at contact, and an unanswerable
   shot is two-draws-strike-second. `rng.strike_luck` takes
   `counter_possible`; `sim.battle` decides it from the same weapon
   selection the counter uses.
4. **Sonja's reducer.** `attack-sonja-negative-luck` predicted 56 and read
   61. Not the game: `sim._luck_value` turned the CO's (min, max) back into
   `luck_reduce`'s (good, bad) as `good = max - 9`, which is right for
   every CO but the one whose bad luck shifts both ends -- Sonja's range
   is `draw % 25 - 15`, so `good = max - min - 9 = 15`. Draw three gave
   -9 and 61. The engine's own luck tables were right; the composition
   was wrong, which is exactly the class of bug this test exists for.
5. **A written rate cell is not what the payer reads.**
   `end-turn-repair-broke` wrote 200 into `0x03004338`, dumped it back as
   200 before and after, and the game paid 9500. DERIVATION 39 read the
   paying body as a load from that cell and confirmed the cell's VALUE on
   eleven boards; this row shows the cell mirrors the setting but a live
   write to it does not reach the payer -- a copy elsewhere, or a read the
   disassembly attributed to the wrong pool constant. Open, and it blocks
   nothing: `economy.funds_rate` now derives the rate from an army's own
   income field first and falls back to the cell, which is what any real
   dump gives. Kill by a write-watch on the payer's load.
6. **Empty army records are not players.** The three-case smoke run before
   the corpus: `sim.end_turn` sent P2's End Turn to a P3 that never plays,
   because `players_in_order` counted every army record and the dumps
   carry four. Presence is a unit on the board or a tile on the map.

After the fixes, `tools/sim_diff.py rescore` replays the recorded dumps
without the emulator: **63 of 63 agree**, and `tests/test_sim_diff.py`
asserts that on every run.

### What the corpus confirmed of the six stated assumptions

- Olaf's snow reverts to **Clear** at his next turn start
  (`end-turn-power-expiry-snow`: weather 1 -> 0 with the block cleared).
- Income obeys the **999,999 clamp** (`end-turn-income-cap`: 995,000 +
  9,500 read 999,999).
- Mass damage charges **no meter** (`power-drake-mass-damage`: every P1
  unit -10 internal, the 5-HP Infantry floored at 1, P1's meter still 0).
- Sturm's strategy is the RNG read at the Power confirm: one draw,
  `draw % 3`, the blast landing on slots 1, 4, 6, 8 at 80 internal each.
  The strategy-0 default for a board with no RNG state remains a default.
- Not exercised and still stated: a passenger aboard a Cruiser and its
  resupply; capturing the HQ ending the match. Also not exercised: the
  second cargo slot, dived combat, snow and rain movement, Sturm's
  alternate record, a power followed by an attack in the same turn.

Two smaller facts the agreeing rows carry. A transport's move leaves the
passenger record's coordinates where they were (`wait-apc-with-passenger`:
the APC went (12,7) -> (10,7), Infantry #66 stayed at (13,2) in both the
model and the game), so nothing may read a passenger's position off its
record. And the join refund at Kanbei's value came out at 2520 and the
charged HQ repair at 800, both to the funds.


## 44. Reading the CPU, begun: the control byte, the command record, and seven traced turns

ROADMAP step 3 asks for `engine/cpu.py`: the turn the CPU will take. The
first thing that step needs is not the AI's reasoning but a way to watch
it -- park a state, let the game's own CPU play, record what it decided in
a form a predictor can be diffed against. This section is that rig and
what its first seven turns measured. The predictor itself is not written.

### The way in

The in-match phase machine switches on the halfword at `0x0300357C`
(eighteen phases, table at `0x08034A54`). Phase 6 is the CPU's turn: the
arm at `0x08034AD8` calls the AI driver `0x0806820C` while `0x03004474`
reads 0. Who gets phase 6 is decided at `0x08035034`, which reads the
active army's byte **`+0x1B`** and switches on it (table `0x08035060`):
**1 is a human** (phase 5), **2 is the CPU** (phase 6, unless settings
`+0x32` and `0x080308AC` divert to phase 0x10), 3..6 phase 0x10. Who the next side IS comes from the search at
`0x08024D58`: it steps the index at `0x030036AC` (5 wraps to 1 and starts
the new day, `0x08016938`) until `0x08024CD0` accepts a side -- `+0x1B`
nonzero and the halfword `+0x14` zero (the AI's End Turn writes 4 into
`+0x14` of the sides with no units, `0x0802872A`). So a written
`+0x1B = 2` on the next side is what hands the board to the AI, and
`harness/mesen_drive.lua` grew a `control` write and a `cpu_turn` step:
both sides' bytes go to 2 before End Turn, the command hook puts every
other side back to 1 on the CPU's first command, and the step waits for
the human phase to run for the side that ended. The event-driven restore
matters: a CPU turn can finish inside the End Turn's own tap gaps (the
P2 trace's five commands were over before any poll ran), and a byte left
at 2 gives the AI the human's next turn as well. And each side's byte goes
back to what it WAS: writing 1 into the empty P3/P4 records (their byte is
0, no controller) made the AI's end-of-turn elimination check at
`0x0802849x` treat them as live sides -- `+0x14 := 4`, and tile (0,0) used
as scratch and left as a City by the restore at `0x080285C2`, which was
the one field a replay then disagreed on.

The AI driver is its own six-state machine on `0x030051B0`. State 0
(`0x08068368`) sets the turn up: it copies a 60-byte **profile** into
`0x020235DC` -- `0x0811A97C + 60 * [0x082872C8 + 12 * k + co_id]`, with
`k` from the mission record at `0x08287478 + 60 * settings[+0x02]` (byte
`+0x23`; VS records carry `0xFF` at `+0x22`), the second half summed with
a second block -- and builds its unit ORDER. State 1 (`0x08068584`) steps
through one of two function lists, `0x083B7CEC` with fog off and
`0x083B7D38` with fog on (19 entries each; 0x080638A4, 0x08063964,
0x08063A1C ... are the AI's sub-phases, each appending to the order list at
`0x03005020` the units whose unit-stats byte `+0x15` at
`0x08283058 + 0x70 * type` matches its class). State 2 (`0x080642C8`) takes
the next slot off that list (terminated by `0x40`), marks it (flag bit 2),
clears its tile in the tile->unit index, draws **one RNG value into the
record's `+0xA` as `draw % 100`** -- the unread unit-record bytes of
DERIVATION 8 are the AI's per-unit random -- and calls the decision proper
at `0x08061A64`. State 3 is the step function `0x080667A4`, which executes
the decision as a command.

### The command record

The decision is a 20-byte record at **`0x030050F0`**, the same shape the
human's input builder at `0x08034988` writes for link play: `+0` the id,
`+1` the unit slot, `+2/+3` the tile the unit moves to, `+4/+5` the tile it
started on, `+6/+7` two arguments, `+8` a u32 the executor writes into the
RNG (`0x08010A78`) before acting, `+0x12` the fuel. The step function's
state 2 is the switch at **`0x080669A0`**: ids 2..11 first apply the move
(acted bit, position from `0x030033AC`), then the jump table at
`0x08066A04` dispatches -- 1 a move, 2 wait, 3 capture (`0x08026178`), 4
fire (`0x08066BB4`: the target is the unit in slot `+6`), 6 load, 7 drop
(`0x08066D64` once per cargo slot: `+6`/`+7` are 0 keep, 5 special, else a
direction index), 9 join (`0x0802649C`, DERIVATION 34), 10 dive and 11
rise (`0x08066E7C/98`, DERIVATION 40), 12 build (`0x08066B48`), 13 a move
that writes the record's `+9..+11`. An exec hook on the switch's entry
copies the record: that is the trace.

### Eight turns

`tools/cpu_trace.py run` reloads a state, writes the control bytes, dumps,
ends the human's turn, traces, dumps again; `tests/fixtures/cpu/` holds
the eight. On the VS fixture with P1 as the CPU (Andy, clear):

```
 1. capture  Infantry #2 (6,3) -> (5,1)        the neutral city
 2. wait     Recon #6    (1,8) -> (3,2)
 3. fire     Tank #7     (4,2) -> (7,4)  at #65 the Infantry beside it
 4. wait     Infantry #1 (1,6) -> (2,8)
 5. fire     Mech #3     (7,6) -> (8,5)  at #65 -- and it dies
 6. wait     Mech #4     (1,7) -> (3,7)
 7. wait     APC #5      (6,2) -> (2,4)
 8. wait     Artillery #8 (0,6) -> (3,4)
```

The order is the sub-phase list, not the slot order: the capturer first,
then the Recon and Tank, then the foot soldiers, the transport, the
indirect. Under **fog** the eight records were identical, RNG values
included -- this AI does not look at fog. Under **Max** the Mech went for
the Tank at (9,7) instead of finishing the Infantry, so the CO reaches the
target choice. With a **full meter** (Andy, threshold written, latch set)
the power was not fired. With a **written P1 Base** and 19,000 funds
nothing was bought -- the build phase walks the game's property list at
`0x03004500` (`0x080685D0`), which a terrain-byte write never joins, so a
build trace needs a map with a real factory. On the A15 fixture with P2 as
the CPU: a capture, three waits, a **load** (id 6: the Mech onto the APC),
the APC's move and a **drop** (`+6 = 1`, the Mech set down north), the
Artillery's wait. And P2 as the CPU on the VS fixture after P1's human
turn: five commands for six movable units -- the Mech captures the city,
the Recon and Mech advance, the Tank moves beside P1's Mech and fires, the
Artillery advances, and the **APC carrying the Infantry issued nothing**
(no record at all, not a wait), which the order-list sub-phases will have
to explain.

### Replayed through the forward model

`engine/cpu.py` decodes the record and finds the engine Action it names;
`cpu.replay` applies the turn through `sim.apply` with each record's own
RNG state, after `sim.end_turn` has handed the board to the CPU. Seven of
the eight traces replay to the game's after-dump **field for field**: every
move's fuel, the capture's progress, the load, the drop, the kill, both
meters, the boundary. Two findings on the way:

- **The AI's strike is the first draw.** On the Max trace the Mech's
  bazooka dealt 55; draw 1 of the record's RNG gives 55, the human path's
  draw 3 (DERIVATION 32, 43) gives 57. The other two AI battles agree under
  either, so this rests on one trace: `cpu.AI_STRIKE_DRAW = 1`, labelled.
- **The fog trace does not replay**, and not because of the CPU: `actions_for`
  offers no shot at an enemy the mover can only see once it has moved, so
  the two fires find no Action. The AI plays as if fog were off; the human
  action layer stops one step short. ASSUMPTIONS carries it.

And one artefact worth a line: the written Base made the terrain grid and
the game's property list disagree, so deriving the rate from P1's income
over its grid count gave 4750; `economy.funds_rate` now derives only when
every property-holding army agrees and otherwise takes the dumped cell.

### What is not done

The predictor -- which DERIVATION 45 delivers: `0x08061A64` is a
classifier, the decision is the sub-phases' behaviours, and
`engine/cpu_ai.py` reproduces every traced turn draw for draw. Settled
there too: the drop directions, the unfired meter (Andy's predicate wants
a damaged unit), the profile's shape and meaning, and the fog list (the
same passes, four of them repeated at the end).


## 45. The CPU, read: nineteen sub-phases, one mover, one scorer, and a predictor that matches seven turns draw for draw

DERIVATION 44 left the decision routine `0x08061A64` unread. It turned out
to be a classifier of a dozen instructions (a per-type byte from
`0x083B7DF0` into `0x030050DC`, transports with nothing to carry demoted
to 0); the decision itself is spread over the nineteen **sub-phase**
functions and the **behaviours** they install, all of which
`engine/cpu_ai.py` now ports routine by routine. `engine/cpu.predict`
reproduces all seven traced turns record for record, and -- because the
trace now logs every RNG draw with its caller (an exec hook on
`0x08010A84`, `harness/mesen_drive.lua`) -- draw for draw
(`tests/test_cpu.py`, `tools/cpu_trace.py predict`).

### The shape of a turn

State 1 of the AI driver (`0x08068584`) steps through a list of function
pointers, `0x083B7CEC` with fog off (19 entries) and `0x083B7D38` with fog
on (25: the same, then indirect-fire, air, direct and indirect-move
passes repeated twice more). Each **sub-phase function** builds the order
list at `0x03005020` from the side's unacted units of one AI class
(unit-stats byte `+0x15`: 1 foot, 2 transport, 4 indirect, 5 direct, 6
Lander), sorts it by the type's move descending (`0x080641CC`, a stable
bubble sort, so ties keep slot order), and stores the behaviour in
`0x030051A8`. In list order, fog off:

```
 0 power            0x0806490C   fire the CO power if meter and predicate allow
 1 foot_capture     0x080646B0   class 1: capture, else walk to a property
 2 class3           0x08064820   (no unit type has class 3)
 3 indirect_fire    0x080648A8   class 4: shoot from where it stands
 4 air_strike       0x080648EC   Fighter and Bomber: attack, else move by mode
 5 direct           0x080648EC   class 5: attack, else move by mode
 6 direct           (again: whoever issued nothing)
 7 clear_targeted   0x08064900   zero the per-unit "targeted" counters 0x03005160
 8 foot             0x08064C94   class 1 again: join, capture, sometimes shoot, walk
 9 transport_empty  0x08064980   APC/TCopter with no AI-loaded cargo: fetch a rider
10 transport        0x080649B0   loaded: drop beside a property
11 transport_loaded 0x08064C58   loaded: move (0x08060708 / 0x080607C4, unread)
12 indirect_move    0x08064C88   class 4: move by mode
13 lander           0x08064DF4   class 6 (unread)
14 lander           (again)
15 apc_supply       0x08065034   APC: supply a low-fuel neighbour, else go to one
16 power            (again, for the COs whose predicate fires at turn end)
17 direct           (again)
18 end              0x080641C0   driver state 5
```

State 2 (`0x080642C8`) takes each listed unit in turn: draws the unit's
random into record `+0xA` (`draw % 100`), runs the classifier, runs the
behaviour. The behaviour writes at most one record through `0x080644D8`,
and that writer **drops a Wait whose destination is the unit's own tile**
-- a unit that decides to stay issues nothing and is listed again by the
next sub-phase of its class, drawing a fresh random each time. The
writer also converts a Sub's Wait to Dive or Rise (enemies in reach and
not dived: Dive; dived and none: Rise), and builds the path from the unit
to its destination (`0x0801DC38`) walking back over the move grid: where
two, three or four predecessors tie on cost it draws the RNG to choose
(bit 14; mod 3; the low two bits). Those path draws are why the Recon's
record followed its random directly and the Infantry's came two draws
later, and the predictor replays them.

### Grids

Everything moves on one flood fill, `0x0801CD00` (through the pointer
`0x03000D8C`): from a tile, for a unit type, within a budget, the cost so
far to every tile (start 0, -1 unreached). With its flag set it will not
enter a tile holding a unit of a side in the moving side's enemy mask
(`0x03004870`, the active player except while the threat builder walks an
enemy). The unit's own reach is `0x0801D968`: budget `min(fuel, move + CO
bonus)`, flag set. `0x0801D2EC` marks every unreached neighbour of a
reached tile with `0x79`, the "one step past reach" ring; `0x08060938`
rings once per point of attack range. Indirect units use the plain
distance ring `0x0801DAE0`. The scan order everywhere is rows outer,
columns inner.

### Attacking

`0x0805F71C` picks the coverage (direct: own reach ringed once; indirect:
the range ring from where it stands), `0x0805F7E0` walks it for enemy
units, and for each: the tile to shoot from (`0x0805FB08`, direct only:
the four neighbours W, E, N, S that are reachable and empty, scored by the
terrain value at `0x08284314` plus 100 if no enemy threatens the tile,
later neighbours winning ties), then the game's own **forecast**
(`0x08023888` / `0x08023550`). The forecast is the battle code: each side's
value is base damage through the CO's per-type attack, all-units
multipliers and the defender's per-type defence (`0x08022BFC`, weapon by
the DERIVATION 32 rule), scaled by hp bars, and **each side that can
deal damage draws the RNG for luck** (`0x080232C8`, attacker first);
damage taken is value times (100 - terrain defence times bars / 10) over
100, and the defender's counter is recomputed from its hp after, with no
luck. `0x0805F948` scores the shot:

```
  taken   = counter damage, +50 if the attacker would die
  reject if taken >= profile[type][0]
  worth   = foot soldier on a property it could capture:
              1 (32 on an HQ, x8 on a Base/Airport/Port)
              x (((hp-1)/10 + capture points + 1) / 5 + 1) x 100
            else 10 x (target cost class + 9) x (10 direct / 15 indirect)
            transports: x2 with the second cargo slot empty
  dealt   = damage dealt, at least 50 if the target would die
  score   = dealt x (worth >> 4) - taken x weight[attacker cost class]
            (weights 0x0811A8E8: 0, 31, 34, 37, 41, 44)
```

Negative scores count as 0; `0x0805F778` takes the strictly highest, so a
turn with nothing positive fires nothing. The Max trace's Mech went for
the Tank because Max's per-type attack changed the two scores' order;
the Andy trace's went for the Infantry (3968 to 3417).

### Moving

Every move is `0x08060078`: a cost map from the goal for the unit's type
over the whole map, the unit's own reach, and the reachable tile whose
goal cost is lowest -- strictly lower than the unit's own tile's unless
the profile's threat byte is 100 -- among tiles the unit may stop on
(`0x080604D0`: empty or its own; not a neutral property; own factories
only for the matching behaviour; enemy properties only for foot soldiers
or within two of an enemy HQ) and, with probability `profile[type][1]`
percent, tiles no enemy threatens. Later tiles win ties. Land units never
stop on a Port. The threat grid (`0x08068F68`) is every enemy that can hit
this unit's class and is within reach, flood-filled from where it stands
(direct: its base move, ringed once; indirect: its range) -- built once
per unit, at the first attack evaluation. A unit that cannot stay where it
stands and issued nothing falls to `0x08066248`: the reachable tile with
the best terrain value less its cost.

Vehicles move by **mode** (record byte `+0xB`, table `0x083B7EB8`): every
vehicle in the traces sat in mode 4, `0x08065B30`, which lists every enemy
it can damage (`0x08060A90`, worth = base damage times a fuel-band
multiplier), takes the lowest and moves toward it -- so the Recon's
"wait (3,2)" is the first step of a hunt. Foot soldiers sit in mode 0
(nothing) and move only through their own pass. Modes 1 (toward the
nearest enemy HQ, `0x08065870`), 2, 3, 5, 6, 7 are read only as far as
this listing goes; nothing in a trace entered them.

### The foot soldiers

Pass 1 (`0x080646B0`): if standing on an own non-factory property with an
enemy Infantry within an Infantry's walk, stay (`0x080651AC`, the guard);
if standing on a capturable property, capture; else capture the reachable
empty property with the highest move cost plus 8 for an HQ or 4 for the
rest -- first in scan order on ties. Pass 2 (`0x08064C94`): a damaged
unit's join check (`0x080650B8`, unread: no trace had one under 50 hp);
pass 1 again; **an attack only when the unit's random exceeds
`profile[type][0]`** (90 for Infantry and Mech: a 9% chance) **or the CO
power is running** -- which is why Mech #3's kill on the Andy trace came
with a random of 95; then the property to walk to: every capturable
property in reach (`0x08025DFC`), the nearest whose "taken" counter
(`0x08282CC4 +3`, per property) is at most the side's foot count over
profile header byte 9 (`0x0805F150`), counter bumped. `0x080630B8` then
asks whether an APC (or a TCopter, if the side has one) would get it there
faster -- `4 x foot cost / move` against `3 x tread cost / 6` -- and if so
sets the unit's pickup flag (`+9` bits 3..5 = 1) and boards an adjacent
transport with room (`0x080665B8`), else walks.

### Transports

An empty APC (`0x080605AC`) floods the map from where it stands, finds
the flagged foot soldier with the shortest cost and a tile beside it the
APC can stand on (`0x08061AB0`), and drives there -- the APC on the Andy
trace went to (2,4) because Mech #4, four tiles from its city, had asked.
A loaded one (`0x080649B0`) drops beside the reachable capturable
property with the highest move-grid value (the `0x79` ring beats every
cost, so a property one step past reach is the favourite), from the
last of its W, E, N, S neighbours the APC can reach and stand on
(`0x0805FC94`), the direction encoded as the a15 trace read it. The
supply pass (`0x08065034`) serves the neediest unit flagged low on fuel
(`+9` bits 0..2 = 1, set at turn start when fuel is under
`profile[type][2]` percent), else goes to it. TCopter (`0x08060670`) and
the loaded transport's move (`0x08060708`, `0x080607C4`) are unread.

### The profile

`0x0806826C` copies 0x130 bytes to `0x020235DC` (DERIVATION 44 counted
60): sixteen header bytes then a twelve-byte record per unit type at
`+4 + 12 x type`. A VS mission's record (`0x08287478 + 60 x map`, `+0x22`
= 0xFF) names a row, and `0x082872C8[12 x row + co]` names one of 89
profiles at `0x0811A97C`. Map 38 is row 1; Andy takes profile 4, and the
live copy in the after-dumps is that profile byte for byte
(`tests/test_cpu.py`). The bytes the port reads: `[0]` the attack
threshold (a foot soldier's shooting chance, an indirect's, and the
counter cap in the score), `[1]` the threat-avoidance chance, `[2]` the
low-fuel percentage, `[3]` the low-hp threshold, header `[9]` the
property share. `data/aw1_ai.json` carries all 89 and every other table
the AI reads (`tools/extract_ai.py`).

### The RNG, accounted for

Per unit visit one draw; per forecast one per side that can damage; per
path tie one; per battle two (the strike is the first -- DERIVATION 44's
`AI_STRIKE_DRAW = 1` is now explained: the AI path has no forecast screen
before its battle, so the battle's own pair is the first it draws). The
hook's callers on the Andy trace: `0x080643A1` twelve times (eight units,
four of them visited twice), `0x0802333F` ten (three forecasts and two
battles), `0x0801DD77` six (path ties). Seven traces, 169 draws, none
unaccounted for.

### The power, explained

`0x0806490C` fires the power when the meter is at its threshold
(`0x0801C07C`) and the CO's predicate (`0x08284A0C + 0x124 x co + 0x14`)
says so. Most COs' (`0x08063298`) fires at the turn's first power
sub-phase; **Andy's (`0x080632C4`) only with a unit at 90 hp or less**;
co 8's at the second pass; co 3's under a settings weather. The full
meter on `vs15-p1-cpu-power` sat on an army at full health. What the
activation does to the RNG and the record is untraced.

### Not read

Modes 2, 3, 5, 6, 7 and the sea variant of mode 1; the Lander pass; the
loaded transport's move; the TCopter; the join and retreat pre-steps
(`0x08065590` -> `0x08065438` / `0x08065338`, `0x080650B8`); the "nothing
to do" fallbacks `0x0806606C`; firing the power; building (driver states
4/5); campaign profiles (`0x080683B0`). Each raises NotImplementedError
with its address in `engine/cpu_ai.py`.

## 46. The Sub the CO pool had been missing

Found while building a differential corpus in parallel with section 43 on
a machine without the emulator: validating a Cruiser-into-dived-Sub case
raised `KeyError: 'Sub' has no modifier entry for Andy` out of
`Attack.between`. The CO records' per-unit pool had **17 entries and no
Sub**.

`tools/extract_co.py` read `24` pointers from `+0x1C` with `uid = i - 1`.
The index is 1-based, so the Sub (RAM type 24) is entry 24 -- at `+0x7C`,
the LAST word of the 128-byte sub-block -- and the loop stopped one short.
Read off the ROM (sha1 `15053499…`) for all twelve records, both blocks:
every entry 24 points inside the pool at `0x28491C` and carries real
values --

| CO | Sub, normal | Sub, power | vision +8 |
|---|---|---|---|
| Max | 150/100 | 170/100 | -- |
| Sami | 90/100 | 90/100 | -- |
| Grit | 80/100 | 80/100 | -- |
| Eagle | 80/100 | 80/100 | -- |
| Sonja | 100/100 | 100/100 | +1 / +3 |
| the rest | 100/100 | 100/100 | -- |

-- exactly the pattern each CO applies to its other direct, naval or
all-unit entries. Two of the extractor's own assertions had encoded the
artefact ("Eagle weakens the surface navy", `NAVAL - {"Sub"}`; "Sonja's
vision bonus, Sub spared"). Both are corrected, the extractor reads 25
pointers, `data/aw1_co.json` is regenerated (18 entries per block), and
the claim "+1 on everything but Sub" is retracted where it was repeated
(`engine/co.py`, `engine/fog.py`, ASSUMPTIONS, section 28). No measured
fixture changes: no capture ever had a Sub under a CO with a non-neutral
pool, which is why nothing caught it.

What the engine had been getting wrong, silently: a Max Sub's torpedo
quoted at 100 instead of 150 (170 under power), Eagle's and Grit's Subs at
100 instead of 80, Sami's at 100 instead of 90, a Sonja Sub's sight one
short -- and every Sub attack with BOTH COs known raising instead of
quoting, which the threat layer's `_modifier` had been swallowing to a
neutral 100. Section 43's sixty-three drives did not exercise a Sub under
a non-neutral CO, so the differential corpus is not contradicted; a
Max-vs-dived-Sub drive is the case that would confirm the 150 live.

## 47. The CPU builds: driver state 4, read and reproduced on twelve traces

DERIVATION 45 left building unread: "driver states 4/5, outside this
module and untraced". Both halves of that sentence turned out to be
slightly wrong, and the second in a way that explains why no trace had
ever shown a purchase.

### Where the build is

The AI driver's state table (`0x0806822C`) names six handlers: 0
`0x08068368` setup, 1 `0x08068584` sub-phases, 2 `0x080642C8` decide, 3
`0x080667A4` execute, **4 `0x08066EC8` build**, 5 `0x08068548` end. The
"end" sub-phase writes 5, and the end-of-turn path runs the build routine
once before handing over -- so the CPU buys at the END of its turn, after
every unit has moved (the purchase hook logged driver state 5 on every
trace). And the purchase is made directly: the build writer `0x08067A48`
calls the purchase routine `0x080243DC(x, y, type)` itself. Nothing passes
the command dispatcher, so the record trace of DERIVATION 44 cannot see a
build; `harness/mesen_drive.lua` now hooks the purchase's entry (r0 x,
r1 y, r2 type, the funds, the record's +7) and `tools/cpu_trace.py` ships
the hits as `builds`. Command id 12 at the dispatcher is the LINK-play
receiver of the same purchase (`0x08066B48`), not the AI's.

### Where the parameters are

`0x08068346` copies the AI profile block (`0x020235DC`, DERIVATION 45) to
`0x0202370C`, and every chooser reads that copy through `0x083B7CE4`. The
build has no table of its own: header bytes 0..8 and, per unit type, the
row's bytes 4..10 (a cumulative table for the mode roll) and 11 (a
weight) -- with three "globals" that are really the Mech row's bytes 5..7
(offsets `0x21`..`0x23`). `data/aw1_ai.json` carried it all already. Two
small tables are new: `0x0811A92C` (5 bytes) and `0x083B7D9C` (26).

### The decision, per free factory

`0x08067684` builds the factory list from the game's PROPERTY LIST
(`0x03004500`): own Base/Airport/Port with no unit on it, class from
`0x083B7DDC` (Base 2, Airport 4, Port 6). Then `0x08066F10` runs once per
listed factory, with fresh unit counts each time, through five choosers
in order until one names a type:

1. **foot** (`0x08066FCC`): needs a Base. Unless the foot count is at least
   header[0] AND its share of the army is over header[2] percent AND
   100 x foot over (army record 0's bytes +0xC..+0xF, summed, +1 --
   always 1 in VS) is over header[1]: one RNG draw, `draw % 100 <
   min(80, day x header[3])` (day 4 on, 0 before) is a Mech, else an
   Infantry.
2. **transport** (`0x08067064`): a TCopter when the side flags' bit 0 is
   set, an Airport is free, and TCopters are under header[4] percent of
   the foot soldiers; an APC when a Base is free and APCs are under
   header[5] (bit 0) or header[6] percent of the foot; a Lander when bit
   1, a free Port, more ground units than byte 0x22, and Landers under
   header[7] percent of them.
3. **counter** (`0x080671E0`): `0x08069748` scores every ENEMY type as its
   total hp less what our army's PRIMARY weapons deal it, CO-modified
   (base x the pool attack / 100 x the universal attack / 100, times our
   hp of each attacker type), stored as u16 and read as s16. Take the
   highest positive; threshold 40 if it is over 100, else a third of it;
   among our types with a nonzero weight, score each by the same modified
   primary damage against that enemy type if it clears the threshold,
   take the highest, and accept it if a factory of its class is free, it
   is affordable, it is not an indirect while indirects exceed byte 0x21
   percent of the army, and its price is at most byte 0x23 percent of the
   funds. A candidate failing the first two is skipped for the next; one
   failing the last two zeroes that enemy type and the search restarts
   with the next-highest -- and with every other score negative, that is
   the end of it.
4. **Sub** (`0x080671AC`): when the enemy has more Battleships than we
   have Subs and a Port is free.
5. **fallback** (`0x08067624`): each type's share of the army in per
   mille over its weight; types with weight 0, no free factory of their
   class or a price over the funds read 255; the lowest wins if it is at
   most header[8], ties going to the heaviest weight.

The writer (`0x08067A48`) then re-checks the price and the 63-record cap,
**draws once more for the new unit's movement mode** (`0x08067BD0`: the
row's bytes 4..10 are cumulative percentages; the first over the roll
names the mode, a row starting 0xFF gives foot soldiers 0 and others 1),
picks the factory (`0x08067C38`: for each of a list of candidate modes --
TCopter 3, Lander 4, everyone 0 = properties not ours, foot 1 = our empty
TCopters, non-ground 2 = shoals and ports -- flood-fill 120 points from
each free factory of the class and write its distance to the nearest
candidate into the record; the first mode where every such factory has
one wins, `0x080680D0` takes the nearest, ties to the earlier record),
buys, and writes the mode into the new record's +0xB.

Two things the map decides that the dumps do not carry. The side-flags
byte `0x030050E4` is computed by the AI's setup (`0x080689A8`) as the OR,
over EVERY tile of the map whatever its owner, of the 5-byte table at
`0x0811A92C` `[0, 0, 1, 2, 4]` indexed by the terrain's factory class
>> 1: an Airport anywhere on the map sets bit 0, a Port bit 1, terrain 18
bit 2. Bit 0 also decides whether a foot soldier's ride check
(`0x080630B8`, called from `0x08064D20`) asks for a TCopter or an APC --
which is why, on the Airport trace, the APC that used to fetch Mech #4
issued nothing: the Mech had asked for a TCopter. The port computes the
byte the same way (`Turn.side_flags`).

### The rig, and two things it had to learn

The fixture map has no factory, so each case WRITES one: the terrain
byte, and now a record in the property list, because the AI's factory
list walks the list, not the grid. Two corrections followed from the
first traces:

- **The tile-to-record index.** `0x0805F150`'s per-property "taken"
  counters live in the list records, reached through an s8 index per
  tile at map `+0x193A`. Inserting a record shifts every later record, so
  the driver shifts the index table with it; before it did, the counters
  landed one record off and Infantry #1 walked to the HQ instead of the
  city on every trace, same RNG.
- **Income is cached.** The walker at `0x08025208` -- every tile, owner
  from the terrain byte, `+0x08` accumulated and the per-type counters
  `+0xC` Base, `+0xD` City, `+0xE` Airport, `+0xF` Port bumped -- does NOT
  run at turn start: P1's income field read 9500 after the whole turn
  with a P1 Base on the board, and the payer paid exactly that. It runs
  at map load and, by every natural fixture agreeing with the grid, at
  capture. So a written property has to join the cache too, and the
  driver now adds the rate to `+0x08` and bumps the type's counter. The
  first traces, with the cache stale, had the AI shopping with 9500 less
  than the grid says -- and that is where a fallback Rockets (over 50%
  of 28500) instead of a counter-chooser MdTank (under 50% of 37000)
  came from. `engine/economy.py`'s grid-based figure is the cache's
  value on any board the game built itself; it is stated as such in
  ASSUMPTIONS.

### Twelve traces, every one reproduced

`tests/fixtures/cpu/build-b*`: one Base (an Infantry); two Bases (an
Infantry, then -- five foot soldiers of nine being over both caps -- the
counter chooser's MdTank against the least-answered enemy type, the
Recon); funds 900 (income first, so an Infantry after all); day 11 (the
Mech chance at 55%, and a Mech); an Airport and a Port (nothing: air and
naval rows weigh 0 in this profile); an all-Tank enemy (foot first
regardless); five foot soldiers (MdTank, mode 6 -- the roll landed in the
236 band); Max and Kanbei (an Infantry, Kanbei's at 1200); 100,000 funds;
and five foot soldiers with 28500 at the shop (the fallback's Rockets,
weight 40 over MdTank, Recon and AntiAir at 0 share, mode 3). `engine/
cpu_ai.py` reproduces the eleven purchases and the one refusal factory
for factory, type for type, mode for mode, and every RNG draw (the foot
roll at `0x0806702A`, the mode roll at `0x08067BD8`) lines up with the
log; the after-boards match through `sim.apply` of the engine's own
build action (`tests/test_cpu.py`).

Not exercised: the TCopter and Lander purchases (the profile in play
weighs them 0 and no trace had both the flag and the ratio), a nonzero
army-record-0 divisor, and campaign profiles.

## 48. The condition byte: what a damaged or dry CPU unit does first

The sparring harness (ROADMAP step 5) aborted its first game on day 2 at
`0x080650B8`, the join pass for a damaged foot unit, which the port had
never read. Eight traces on the `vs15_p2` state with P1 as the CPU and one
unit written damaged, dry or empty (`tests/fixtures/cpu/prestep-*`) read
the branch, and the port reproduces all eight command for command and draw
for draw.

**The condition.** Record byte `+9` bits 0..2 are set once per turn by
state 0 of the AI driver (`0x08068910`, called from `0x08068368` after the
profile copy and the side flags) through a three-entry table at
`0x0811A920` indexed by the current bits:

- bits 0 (`0x08068848`): hp under `profile[type][3]` -> 2 (needs repair);
  else a unit with an ammo gauge (stats `+0xD` nonzero) and no ammo -> 1
  (needs supply); else fuel x 100 / max fuel (stats `+0x12`) under
  `profile[type][2]` -> 1. Andy's row on map 38 puts the hp bar at 20 and
  the fuel bar at 10% for foot units, 20% for a Tank.
- bits 1 (`0x080688CC`): cleared once fuel is above max - 5, and only
  then -- an empty gauge keeps a unit at 1 until its fuel reads full.
- bits 2 (`0x080688F8`): cleared once hp is above 91.
- anything above 2 is cleared.

The after-dumps carry the byte: 15 hp reads 2, fuel 5 of 70 reads 1, a
retyped Mech with Infantry's zero ammo reads 1 (`prestep-join-sum-over`),
and the port's context ends every traced turn with the same bits on every
CPU unit (`tests/test_cpu.py TestThePreStep`).

**The pre-step.** Decide (`0x080642C8`) runs `0x08065590` for a unit at
condition 1 or 2 with no capture in progress (`+5 & 0xF8` clear), before
the sub-phase's behaviour: it sets `0x03005008` bit 1, calls the join
`0x08065608`, then the condition's routine from the two-entry table at
`0x0811A918`. Whatever issues a record ends the unit's decision; the
behaviour runs only when nothing did (the 15 hp Infantry with no own city
walked its ordinary capture walk, fourth in the turn; with a city written
it moved toward the city as the first command).

- **The join** (`0x08065608`): a unit at 50 hp or less joins the same-type
  friendly with the most hp within its move grid (`0x0801D968`, the
  partner's tile at a positive cost), when the two together stay within
  100 hp, neither carries anything, and the partner's `+1` bit 3 is clear
  -- record id 9 onto the partner's tile. Mech #1 at 40 beside Mech #4 at
  50 joined (`prestep-join-mech`, 90 hp after); beside Mech #4 at 100 it
  did not (`prestep-join-sum-over`). The survivor's condition bits read 0
  after the join; the port clears them on every join until a trace says
  otherwise.
- **Condition 1, supply** (`0x08065438`): the unit's move grid, its `0x79`
  ring, and `0x0806138C`'s list -- own suppliers (`0x08282EE5`: the APC
  and vestigial type 9) on reached tiles, for a type the table
  `0x08282ECC` lets be supplied; for a BCopter or TCopter an own Cruiser
  with its second bay empty instead. `0x08060A34` picks the cheapest
  (later entries winning ties). A copter boards a Cruiser it reaches this
  turn (record 6, the Cruiser's `+9` bits 6..7 incremented); anything else
  moves toward the pick (`0x08060078`), which puts it on the tile beside
  the supplier -- the Tank at fuel 5 stopped at (6,3) next to the APC at
  (6,2) (`prestep-fuel-tank`). With nothing in reach, a whole-map grid
  (`0x78`, enemies blocking) and `0x080614A8`'s list: own suppliers
  anywhere, and own properties whose class (`0x083B7E3B` by terrain) is
  the one the type is served at (`0x083B7E22`, 1 ground / 2 air / 3 sea);
  the Infantry at fuel 5 went straight to the HQ (`prestep-fuel-inf`), the
  ammo-less Mech toward it (`prestep-join-sum-over`).
- **Condition 2, repair** (`0x08065338`): a whole-map grid and
  `0x0806121C`'s list -- own properties whose class (`0x083B7DDC`) is the
  one that repairs the type (`0x083B7DF0`: 2 ground, 4 air, 6 sea), empty
  or under the unit itself. The HQ codes 0 in `0x083B7DDC`, so it is not a
  repair point here, which is why 15 hp with only the HQ owned changed
  nothing (`prestep-hp-inf`, `prestep-hp-tank`) and a written own city
  drew the Tank onto it (`prestep-repair-tank`, reached this turn: a Wait
  onto the tile) and the Infantry one step from it
  (`prestep-repair-inf`). The routine runs its search with the profile's
  threat-avoidance byte set to 100, and again with it at 0 when the first
  pass issued nothing.

**The foot pass's own join** (`0x080650B8`, first in `0x08064C94`, for a
foot unit at 50 hp or less): the first same-type friendly in scan order
that stands on a capturable tile within reach, the two together within
120 hp -- a reinforcement for a capturer. Read, not traced: no parked
state puts a damaged foot unit within reach of a friendly on a property,
and position writes are refused by the rig. The ROM does not exclude the
unit's own tile; the port does.

**Not read:** `0x0806636C`, the check a move made with `0x03005008` bit 1
set rolls into with probability `profile[type][1]` (10% on these rows) --
none of the eight traces rolled under it. The port raises there.

**Rig notes.** A unit's type is written by name (`{"unit": 1, "type":
"Mech"}`); the retyped Mech kept Infantry's zero ammo, which is what made
the join traces double as the empty-gauge case. An own city written onto
the terrain needs the cached income bumped (`raw` write on army `+8`,
DERIVATION 47) or the after-dump's funds disagree with the model by one
property's rate.

## 49. Nothing to do: the foot unit with no property left, and the fallback

The sparring harness's second trace request (ROADMAP step 5): playing P2
against the port on the step 3 fixture, the port aborted on day 10 at
`0x08064D6A` -- P1's four foot units against three properties not P1's,
with the profile's header byte 9 at 5 putting the per-property target
limit at 4 / 5 = 0, one unit per property, so the fourth Mech had nowhere
to walk. Two traces on `vs15_p2` with P1 as the CPU reproduce the shape
by writing neutral cities to P1 (`tests/fixtures/cpu/noprop-*`):
`noprop-foot` writes four of the six and removes the APC, leaving two
cities and the enemy HQ for four foot units; `noprop-apc` writes all six
and keeps the APC, leaving the HQ alone. The port reproduces both record
for record and draw for draw (18 and 25 draws).

**The tail of the foot pass** (`0x08064D6A`): with no goal, side flag
bit 0 (`0x030050E4`, an Airport somewhere on the map) decides. Set, the
unit takes pickup state 2 (`+9` bits 3..5), boards an adjacent transport
(`0x080665B8`), lists the TCopters with room (`0x08060858`) and walks
toward the nearest (`0x08063188`, `0x08060078`) -- unread beyond the
listing, no traced map has an Airport, the port raises at `0x08064D76`.
Clear, it falls into `0x0806606C`.

**The fallback** (`0x0806606C`), which the mode 1 mover with no enemy HQ
reachable and the mode 4 mover with nothing to hunt also call: with side
flag bit 1 clear (no Port on the map) it calls `0x08066248`, settle; so
does a unit whose type the Lander cannot carry (`0x083B7D9C`, the table
DERIVATION 47's factory ranking reads). Otherwise: pickup state 3, board
an adjacent transport, the list of own Landers with fewer than two
passengers (`0x08061808`: type 0x17, `+9` bits 6..7 at most 1), then over
the unit's whole-map grid the Port (11) or Shoal (13) tile whose mark byte
is not 0x7F with the least mark plus move cost -- a walk toward it when
the cost exceeds the unit's effective move (`0x0805F22C`), a Wait onto it
when not (`0x0806198C` allowing). That pickup is read only this far and
raises at `0x080660A6`.

**Settle does not return.** Every path through `0x08066248` -- the unit
may stop where it stands (`0x080604D0`), or the best-valued reachable tile
it may stop on gets a Wait -- ends at `0x0806635A`, `0x080796B4(0x030050B0,
1)`, which hands control back to the AI driver; the `bl` never comes back
to its caller. That is why the fallback can call settle three times in a
row without checking what it did, and why the port returns after each
`settle()`. On these traces the unit could stop where it stood, so the
record is nothing at all: `noprop-foot`'s Mech #4 and `noprop-apc`'s
Infantry #2 and Mech #4 are absent from the command list, and the port
issues nothing for them. The abort dump from day 10 now plays through.

**The grids the fallback walks**, for whoever reads the pickup: the unit's
move grid is row pointers at `0x03003600` (signed bytes, negative
unreached); the map struct at `[0x08282CB4]` holds width at `+0`, height
at `+2`, per-row offsets at `+18050`, the terrain bytes at `+5170` (low
five bits the id) and the mark bytes at `+15474`.

**Rig notes.** Four written cities put P1's cached income at 5 x 9500 =
47500, six at 66500 (`raw` write on army `+8`, `0x0201ABA4`); `{"unit":
5, "remove": true}` takes the APC off the board. Removing the APC changed
nothing about the fallback: the units with nothing to do stayed put with
or without a transport to board, since boarding is only reached behind
the Port flag.

## 50. The CPU fires its power, and what the CO records had been hiding

The sparring harness's third trace request: with the fallback ported the
P2 game ran to day 13, where the port stopped at `0x0801C120` -- Andy's
meter full, a 30-hp Infantry to heal, and no trace had ever seen the AI
fire. Three traces on `vs15_p2` with P1 as the CPU and its meter written
to the threshold (`tests/fixtures/cpu/power-*`): Andy with Infantry #1
at 50 hp, Max, and Eagle. The port reproduces all three record for record
and draw for draw (28, 27 and 38 draws) and leaves the game's board.

**The firing** (`0x0801C120`, from the power sub-phase `0x0806490C` when
`0x0801C07C` says the meter is at its threshold and the CO's predicate
agrees, DERIVATION 45): uses + 1 (`0x0801C0E4`, army `+0x25`, capped at
255), the ready flag (`+0x24`) cleared, then `0x0803BC2C` -- the
activation the human's menu fires, whose effects `sim.apply` already
models from DERIVATION 27 and 37. It is not a dispatcher command (the
command list is the same eight records with or without it), and it draws
nothing: the draw logs line up with the port applying the engine's own
power action and moving on. The after-dumps read meter 0, uses 1, the
block active; Andy's Infantry 50 -> 70.

**Eagle fires at the end and the turn goes round again.** Eagle's
predicate (`0x080632AC`) allows the second power pass only, sub-phase 16,
after every unit has moved. The driver's state log shows what follows:
the power fires, and the next sub-phase is 1, not 17 -- the whole list
runs again, and the units Lightning Drive refreshed (the Recon, Tank, APC
and Artillery; not the foot soldiers) each take a second command in the
same order, twelve records for eight units. The port resets its sub-phase
cursor to 1 after a power fired at any pass past the first.

**What the Max trace exposed below the port.** The APC drove seven
tiles, `(6,2) -> (1,4)`; the port predicted it (its move budget reads the
CO block's per-type move byte, DERIVATION 45) and then found no engine
Action to execute, because `pathing.allowance` knew only the stats move.
The CO records' pool entries carry more than attack and defence: `+7` is
a signed move adjustment and `+9` a signed range adjustment, and neither
had been extracted -- Sami's transports +1 move and Drake's navy +1 move
at all times, Max's direct units +1 and Sami's foot +1 under power;
Grit's indirects +1 range (+3 under power) and Max's -1, on the maximum
range and only where the base maximum exceeds 1, which is how the AI's
threat grid applies it. `tools/extract_co.py` now records both,
`co.move_bonus` and `co.range_bonus` read them, `pathing.allowance` takes
the board and adds the move (fuel still caps it), and `actions.actions_for`
and `threat.covered_tiles` add the range. The 63-drive corpus still
replays: none of its drives crossed either bonus, which is why the model
had been silently short a tile for Sami's and Drake's units and a ring for
Grit's and Max's artillery since DERIVATION 43. The move bonus is measured
once, by the CPU's own move; the range bonus is read off the ROM and
stated (ASSUMPTIONS).

**Not read.** Olaf's predicate (`0x08063324`, under a settings weather)
still raises; the co-8 second pass is traced for Eagle only, and the
meter-threshold arithmetic is DERIVATION 27's.

**Rig notes.** `{"army": 1, "co": N, "meter": "threshold", "ready": 1}`
puts the meter at the CO's own threshold for its use count; Andy's
predicate needs a unit at 90 hp or less, so his trace also writes
`{"unit": 1, "hp": 50}`.

## 51. The APC's supply record, and the sweep that asked for it

The sparring harness run across every parked dump the port can load (64
starts, both sides) stopped three of its eight workers not on an unread
branch but on a disagreement: the port issued its supply pass's record --
id 5, ported from the listing in DERIVATION 45 -- and `cpu.to_action` had
no mapping for an id no trace had ever shown. Two traces on `vs15_p2`
with P1 as the CPU, its four foot units removed and the Tank written dry
(`tests/fixtures/cpu/supply-apc`, fuel 2; `supply-apc-move`, fuel 0),
show the record: **id 5, the APC's ending tile beside the unit it
refills, and that unit's slot in byte `+6`** -- the APC at `(6,2)` drove
to `(5,1)` for the Tank that had walked to `(5,2)`, and to `(4,1)` for
the Tank stuck at `(4,2)`. The Tank reads fuel 70 and 65 after. The port
reproduces both record for record and draw for draw (9 and 17 draws),
`to_action` maps id 5 to the engine's Supply at that tile whose fills
include the named slot, and the harness now records a port/action-layer
disagreement as an abort with its dump rather than dying.

**The trailing direct pass.** The sub-phase list (DERIVATION 45) ends
`apc_supply, power, direct, end`, and the second trace shows what the
last `direct` is for: the Tank that could not move at all was refilled at
sub-phase 15 and then, at sub-phase 17, moved and fired -- a unit
supplied by the APC gets its turn after all.

**The sweep** (`tools/sparring.py` over the 32 before- and 32 after-dumps
in `tests/fixtures/cpu/`, both sides, day cap 20): the port's trace queue
is now dominated by two routines, the retreat-after-move check
`0x0806636C` and the loaded transport's move `0x08060708` /
`0x080607C4`, with movement modes 2, 3 and 5 behind them on the states
where the CPU has a factory to build from. The results are in ROADMAP
step 5.

## 52. The retreat check: a threatened unit's move is voided, and the grid it reads

The sweep's largest queue entry (27 of 58 aborts): `0x0806636C`, the
check a conditioned unit's move rolls into with probability
`profile[type][1]` (10 on these rows), first named in DERIVATION 48. Five
traces on `vs15_p2` with P1 as the CPU, one unit written dry and the RNG
written so that the port's own arithmetic put that unit's random under
ten (`tests/fixtures/cpu/retreat-*`; the seeds were found by running the
port over seeds, which is what a predictor is for). The port reproduces
all five record for record and draw for draw.

**The mover's tail** (`0x08060078` at `0x0806024C`): the move's Wait is
written (`0x080644D8`), then with `0x03005008` bit 1 set and
`profile[type][1] > random % 100` the check is called, then settle
(`0x08066248`, terminal) on both paths -- the port's reading stands.

**The check** (`0x0806636C`): build the threat grid (`0x08068E78`) and
test the unit's OWN tile against it with the type's hit mask (stats
`+0x17`). Standing safe, return -- `retreat-roll-inf`, `-inf2`
(Infantry #1, fuel 5, seeds 64 and 99) and `-tank` (Tank #7, fuel 5,
seed 127) rolled it and walked to the HQ and the APC as the untouched
pre-step traces did. Standing threatened, scan the unit's move grid
(`0x0801D968`) for the cheapest tile the grid does not cover that the unit
may stop on (`0x080604D0`), later tiles winning ties, and write a Wait
onto it; then, only with bit 1 clear (never, from this caller), a second
scan by terrain value. **A second record in one decision voids the
unit's command for that pass.** `retreat-mech` and `-mech2` (Mech #3 at
`(7,6)`, fuel 5, seeds 1 and 141, an Infantry two tiles off and a Tank
three): at its first visit the Mech rolled a 5, chose `(7,4)`, found
itself threatened with `(7,5)` the safe tile -- and issued nothing. It
was decided again at the foot pass, sub-phase 8, with a fresh random (58,
86) and walked to `(6,5)`: three draws more than one decision, exactly
the port's count once it drops the pending record and lets the unit fall
through. How `0x080644D8` voids on the second call is not read; the
behaviour is measured on two seeds and modelled as the void.

**The grid the supply pass reads.** `retreat-roll-tank` disagreed on one
record before any of this: the APC supplied the Tank from where it
stood, `(6,2)`, where the port drove to `(6,4)`. The from-tile chooser
(`0x0805FB08`, terrain value plus 100 if unthreatened, later neighbours
winning ties, the unit's own tile allowed) is the port's; the difference
was the threat grid it scores against. The supply pass (`0x08064820`)
never builds one, and the game's lives in the map struct at `+12898`
from whichever decision built it last -- here the Artillery's, which
marked `(6,4)` threatened and left `(6,2)` clear, 100 against 10. The
port had been clearing its grid at every decision; it now keeps it, and
every one of the 39 traces still predicts.

**Rig notes.** `{"rng": N}` writes the RNG state before the dump, and the
AI's first draw is from that state (the End Turn taps draw nothing). The
port finds the seed: with the check raising, run it over seeds until the
raise names the unit you want; with the check ported, patch it to raise
when the unit stands threatened.

## 53. The campaign profile: the mission's header over the CO's rows

ROADMAP step 5 asked for this read before any mission-level tuning, and
step 6 needs it before the port can build a context on a campaign
state. State 0's profile copy (`0x0806826C`) branches on the mission
record's byte `+0x22` (`0x08287478 + 60 x map_id`): 0xFF is a VS mission,
whose row `+0x23` picks a column of `0x082872C8` by the CO and so a
profile of the table at `0x0811A97C` (DERIVATION 45). Anything else is a
campaign mission and calls `0x080683B0(dest, +0x22, +0x23, player)`:

- the 16 header bytes -- the thresholds and counts the choosers read --
  are copied from **the mission's own profile**, row `+0x22` of the same
  table (116 to 132 for the eighteen campaign records; map 0 names row
  0);
- each of the 24 unit rows, 12 bytes, is the mission row's byte **plus**
  the CO's VS profile row's byte, `strb` truncated -- a per-mission
  adjustment on top of the CO's personality, all zero on the rows
  sampled.

Maps past the 164-entry table (`0x080682F8`, the design room) take a row
from `0x083B7EE8` by the side flags, 4 when those exceed 3, and are not
modelled; `profile_for` raises there with the address.

`tools/extract_ai.py` now reads 133 profiles (it had stopped at the 89
the CO rows index); `cpu_ai.profile_for` merges as above. Read off the
ROM and checked against nothing yet: the dump carries the live copy at
`0x020235DC` as `ai_profile`, so the first parked campaign state will
confirm or refute the merge byte for byte, the way `vs15-p1-cpu.after`
confirms the VS path.


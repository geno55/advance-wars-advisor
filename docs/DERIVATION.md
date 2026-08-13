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

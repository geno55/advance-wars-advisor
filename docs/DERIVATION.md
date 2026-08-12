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

| field | offset |
|---|---|
| unit type, **1-based** (0 = empty slot) | +0 |
| owning army | +1 |
| map x | +2 |
| map y | +3 |
| internal HP, 1..100 | +4 |

Records are **12 bytes**; the base is the EWRAM pointer stored in ROM at
`0x08282CB8`, which reads `0x02019F34`.

Confirmed against a live capture rather than assumed. A Tank showing 5 bars was
found by change-detection at `0x02019F8C`; that is
`0x02019F34 + 12*7 + 4`, i.e. unit index 7 with HP at +4. Its record reads
type `5`, army `0`, x `7`, y `5`, hp `42` — and `ceil(42/10) = 5` bars.

Note the type: `5` is **Tank**, not Recon. The damage tables are 0-based but RAM
type ids are 1-based, exactly as the `subs r1, r3, #1` in the damage path
implies. Reading a RAM type straight into the damage table is an off-by-one that
silently returns the wrong row, so `harness/mgba_ramtool.lua` subtracts before
naming anything.

Neighbouring pointers, not yet decoded: `0x08282CBC` -> `0x0201AB34` (the army
structs, stride 0x68 per the damage path), `0x08282CC0` -> `0x0201AD3C`,
`0x08282CC4` -> `0x03004500`. The map row pointer table is at `0x03003600`.

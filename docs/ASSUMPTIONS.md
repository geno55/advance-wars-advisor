# Assumptions, and how to kill each one

Everything in this project is in exactly one of three states. The point of this
file is that nothing gets to sit in the middle state quietly.

| state | meaning |
|---|---|
| **established** | derived from the ROM and asserted by a test |
| **assumed** | plausible, load-bearing, and *not yet checked against the game* |
| **unknown** | not modelled at all yet |

## Established

- Base damage matrices, both weapons, all 18 units — extracted from ROM, 17
  structural assertions, 24 regression tests. See `DERIVATION.md`.
- Internal unit ID map, including the six vestigial gaps.
- Unit roster and names.
- **Index formula** `table + (att-1)*24 + (def-1)`, read off the disassembly.
  In-RAM type IDs are 1-based; table rows are 0-based.
- **Out-of-ammo Fighters cannot attack.** The alternate ROM copy that gives them
  a secondary weapon has zero code cross-references — dead data.
- **CO modifiers truncate individually.** `(value * mod) / 100` via `__divsi3`,
  applied twice in sequence. Any variant that rounds only at the end is wrong.
- **Combat display HP = `max(1, internal // 10)`** (`floor_min1`). Not the bar
  count on screen, and not `ceil`. Both halves of this were measured, not
  assumed — see A5, which is now closed.
- **Terrain defence stars**, all 20 slots, from the ROM struct array at
  `0x284170` byte `+8` (stored as stars × 10). Asserted against the 14 values
  read off the in-game display. See A3.
- **Per-property income is 1000/turn**, from the same struct at `+4`, nonzero
  for exactly the five capturable types (City, HQ, Airport, Port, Base).
- **Terrain id 9 is the sky**, and it has 0 defence. Air units are given the
  sky as their terrain instead of the tile beneath them; that is how the game
  denies them terrain cover, with no per-unit branch anywhere. Use
  `Board.defence_for(x, y, move_type)`, not `Board.defence(x, y)`, for anything
  that might be an aircraft.

## Assumed — these are the ones that will bite

### A0. Where the formula's remaining steps live
The disassembly walk reached the base lookup and the CO modifiers but not HP
scaling, terrain defence, or the luck roll — those are further along the combat
path at `0x08022xxx`. They are still assumptions, and calibration still has to
resolve them. Continuing the walk would settle them without any play at all.

### A1. Weapon selection = "whichever deals more base damage"
`select_weapon()` picks the higher-base weapon, subject to ammo. This reproduces
the known behaviour that a Md Tank hits Infantry for 105 (machine gun) and Tank
for 85 (cannon). But "max damage" is a guess at the *rule*; the game might
instead use an explicit per-matchup weapon flag that happens to agree here.
**Kill it by:** finding a matchup where primary and secondary are close, and
checking which one the ammo counter decrements.

### A2. The damage formula itself — **CLOSED. It is `luck_after_hp`.**
Six variants lived in `engine/damage.py`, differing in where they truncate and
where luck enters. They agreed on most inputs and disagreed at the margins —
exactly where "is this a guaranteed kill?" gets decided.

    damage = floor((base * co_atk/100 * display_hp/10 + luck) * defence)

Settled by crossing two independent eliminations:

- **`tests/calibrate.py`** over 75 observations left `{luck_after_hp,
  luck_last}` — set-based, no distributional assumption.
- **A seeded sweep** over the combat luck state left `{floor_end,
  floor_attack_then_end, floor_each_step, luck_after_hp}`, refuting `luck_last`
  (chi2/df 12.29 against 0.74) and `round_end`.

The intersection is one variant. Neither route could have done it alone: the
corpus cannot separate the four that coincide on a 4-star matchup, and the sweep
cannot separate them either — but they do not fail together.

`resolve()` now returns an exact range rather than an envelope over two
variants, so "cannot kill" is finally as reliable as "will kill". It still
raises `Unverified` unless the caller opts in, because that guard is about the
formula having been checked against the game at all, which remains the right
question to force a caller to answer.

### A3. Terrain defence stars — **CLOSED, extracted from ROM**
Now read from the terrain struct array at `0x284170` (20 entries, stride
`0x14`), where byte `+8` holds **stars × 10**. `tools/extract_terrain.py`
regenerates `data/aw1_terrain.json` and asserts every value.

Three independent routes now agree with no shared inputs: the ROM table, the
in-game Def display (all 14 terrains), and calibration, which had derived
road=0, plains=1 and mountain=4 from damage observations alone. See
`DERIVATION.md` for how the table was located.

That table also settled two things that were never listed as assumptions
because nobody had thought to doubt them — see **Established** above for the
sky, and `README.md` for the missile silo that does not exist.

### A4. Luck roll is uniform 0..9 — **RESOLVED, and the sampling problem with it**

The combat luck state is a **u32 at `0x03001D30`**, found by bisection (see
`DERIVATION.md` 16). Writing it makes the roll an input, which removes the
sampling problem entirely rather than mitigating it.

128 seeds spread across the 32-bit space produced this histogram for Tank →
Infantry on mountain:

    45:19  46:30  47:14  48:30  49:24  50:11

Two things fall out at once. The **2:1 pattern** — 46 and 48 at ~30, 47 and 50
at ~12 — is exactly what a uniform 0..9 roll produces when `luck_after_hp`
collapses ten rolls onto six damages, so the *range assumption in this
heading is confirmed* rather than merely unrefuted. And `luck_last`, which maps
ten rolls onto ten distinct damages and is therefore flat, is refuted on shape.

The structural gap at 47 that the frame-delay sweep showed is **gone**,
confirming it was an artifact of walking a narrow orbit.

Everything below is kept as the record of how long this took to get right.

### A4 (history). The sampling problem, and two withdrawn inferences
20 repeated trials of one matchup produced only three distinct damage values
(47x9, 48x3, 49x8). Inverted, every variant implies a **contiguous band** of
rolls with both extremes absent — `luck_after_hp` says 4-8, `luck_last` says
2-4. Scattered gaps would indict the model; a contiguous band indicts the
sampling.

Cause: the GBA advances its RNG as frames pass, so reloading a save state and
confirming after a similar delay each time samples a narrow window of the
sequence. Consistent human tempo produces consistent rolls.

**Consequence, and it bit us:** an earlier "357:1 in favour of `luck_after_hp`"
was computed from the absence of high rolls, assuming every roll had a fair
chance of showing up. Under clustered sampling that inference is worthless, and
it has been withdrawn.

**Kill it by:** ~~deliberately varying the delay between loading and
confirming~~ — tried, and it is not enough. An automated sweep of 61 cases
varying the confirm delay produced a *structural* gap at damage 47: zero
occurrences where its neighbours got 9 to 15. Idling k frames advances the state
by whatever that frame consumed, so the sweep walks a small orbit rather than
covering the sequence. Better than a human's consistent tempo, still not
uniform, and still not a basis for any argument from absence.

**An RNG was located and then refuted as the combat source.** A (20077, 12345)
LCG with its u32 state at `0x03000750` — but 64 attacks with 64 different seeds
written produced **one** damage value, and decoding every `bl` in the ROM gives
it three callers, none in the combat path. See `DERIVATION.md` section 16.

So the goal stands and the route changed: **stop sampling, make the roll an
input.** That still requires knowing where the roll comes from, and it is now
being found by bisection rather than by reading — `rng_bisect()` snapshots RAM
at the confirmation point for two delays that give different damage, diffs
them, and binary-searches the diff by patching one state into the other. Once
the source is known, `dmg_seedsweep()` and `tools/rng_fit.py` are already built
to refute variants at **known rolls**, which needs no distributional assumption
and cannot fail the way the 357:1 did.

The 0..9 range itself remains an assumption; `rng_fit.py` searches the modulus
rather than presuming it.

### A5. Display HP = ceil(internal / 10) — **REFUTED, and the replacement is now CLOSED**
This was wrong, and it is the most instructive error in the project.

Attack strength scales with a *truncated* tenth of internal HP, not the bar
count on screen. A Mech at 57 internal HP **displays 6 bars but attacks as 5**.
Screen rounding and combat rounding are different functions, and the engine now
keeps them apart: `display_hp()` for the maths, `screen_bars()` for the player.

How it died: a recorded counterattack (Mech at 57 HP hitting a Tank on plains
for 27) was impossible under `ceil` for every terrain value, while a direct
attack on the same tile demanded 0 or 1 stars. The two were irreconcilable. The
first instinct was that the terrain label was mis-recorded — it was not, and the
player said so. Re-testing the four candidate rules against all observations
refuted `ceil` and `round`, and left `floor` fitting everything.

The display rule is now a free parameter in calibration rather than an
assumption, exactly like the formula variant.

**`floor` vs `floor_min1` is settled too.** They differ only below 10 internal
HP, where plain `floor` says a unit on its last bar attacks at strength 0. The
kill condition was "attack with a unit under 10 internal HP", and it was run: an
Infantry at **9** internal HP dealt **11** damage to a full-health Infantry on a
road (`observations.csv`, "last-bar trial 2").

Under `floor` that attacker has display HP 0, so every one of the six variants
collapses to at most the bare luck roll — a hard ceiling of **9 damage**. 11 is
not reachable. A unit on its last bar attacks at strength 1, not 0.

The same battle is doubly load-bearing: under `floor_min1`, four of the six
variants cap at 6, so only `luck_after_hp` and `luck_last` survive it. It is one
of the observations doing the work in "2 of 24 hypotheses survive".

`calibrate.py` reports `display rule (1): floor_min1 (determined)` and
`engine/damage.py` sets `DEFAULT_DISPLAY = "floor_min1"`.

## Unknown — not modelled

- **CO powers.** The second 128-byte stat block is selected by army `+0x1E`,
  and its contents are extracted, but nothing triggers or models activation.
- **Where Kanbei's and Sturm's strength is applied.** Their per-unit modifiers
  are all 100/100; their records carry `+08/+09` and `+11/+12` pairs that the
  damage path has never been shown to read. `engine/co.py` reports these as
  unmodelled and `Attack.between()` refuses to quote those COs rather than
  returning a number that would be ~20% low. **Kill it by:** writing army
  `+0x1D` to Kanbei and to Andy on one fixture, seeding the RNG so luck is
  fixed, and comparing the damage.
- The `fighter-secondary` ROM discrepancy (see `DERIVATION.md`).
- Weather effects, fog of war.
- Terrain movement costs, capture, supply, repair — all of milestone 1/3.
- Whether the RNG can be read and predicted. Explicitly out of scope for now;
  the model deals in damage *ranges*.

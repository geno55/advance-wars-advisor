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

### A2. The damage formula itself
Six variants live in `engine/damage.py`, differing in where they truncate and
where luck enters. They agree on most inputs and disagree at the margins —
exactly where "is this a guaranteed kill?" gets decided.
**Kill it by:** `tests/calibrate.py` (see README). Until then `resolve()` raises
`Unverified` unless you explicitly opt in.

### A3. Terrain defence stars
Not extracted. Deliberately *not* typed in from memory either — they are solved
as free parameters by calibration alongside the formula.
**Kill it by:** the same calibration run.

### A4. Luck roll is uniform 0..9
Standard for AW1 with a plain CO. Nell and Flak differ.
**Kill it by:** repeated identical attacks from a save state; the observed
outcome spread bounds the roll.

### A5. Display HP = ceil(internal / 10) — **REFUTED**
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
assumption, exactly like the formula variant. `floor` vs `floor_min1` remains
open: they differ only below 10 internal HP, where plain `floor` says a unit on
its last bar attacks at strength 0 and deals nothing.
**Kill that one by:** attacking with a unit at under 10 internal HP.

## Unknown — not modelled

- CO attack/defence modifiers per unit (Andy, Max, Sami, …) and CO powers.
  The engine takes `co_attack`/`co_defense` as parameters but nothing fills them.
- The `fighter-secondary` ROM discrepancy (see `DERIVATION.md`).
- Weather effects, fog of war.
- Terrain movement costs, capture, supply, repair — all of milestone 1/3.
- Whether the RNG can be read and predicted. Explicitly out of scope for now;
  the model deals in damage *ranges*.

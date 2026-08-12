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

### A5. Display HP = ceil(internal / 10)
Confident, and tested for self-consistency, but self-consistency is not proof.
**Kill it by:** reading a damaged unit's internal HP from RAM and comparing.

## Unknown — not modelled

- CO attack/defence modifiers per unit (Andy, Max, Sami, …) and CO powers.
  The engine takes `co_attack`/`co_defense` as parameters but nothing fills them.
- The `fighter-secondary` ROM discrepancy (see `DERIVATION.md`).
- Weather effects, fog of war.
- Terrain movement costs, capture, supply, repair — all of milestone 1/3.
- Whether the RNG can be read and predicted. Explicitly out of scope for now;
  the model deals in damage *ranges*.

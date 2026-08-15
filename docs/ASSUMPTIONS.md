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
- **Combat display HP = `ceil(internal / 10)`**, and the on-screen bar count is
  the same function. Measured by writing HP and sweeping 64 luck seeds: an
  attacker at 57 deals 27-32 (only `ceil`), a defender at 81 takes 48-53 (only
  `ceil` -- 81 is the value that also excludes `round`). Observed multiplicities
  match ceil's 2:2:1:2:1:2 collapse, not just its endpoints. `display_hp()` and
  `screen_bars()` are kept as separate functions because they answer separate
  questions, but nothing measured separates their values. See A5 and A9a.
- **The counterattack formula** is `base * raw_internal_hp / 100`, then the
  defence multiplier -- no display quantisation and no luck roll. Measured: a
  64-seed sweep held the counter at 2 while the opening ranged 45-50. See A9b.
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

### A5. Display HP = ceil(internal / 10) — **RE-ESTABLISHED. The refutation was the error.**

> **The original assumption was right.** `ceil` was withdrawn on the strength of
> two observations that were counterattacks, and the game computes counters with
> a formula that has no display term at all (A9b). Fitting them with the strike
> formula produced `floor_min1`, which then agreed with all 71 first-strike rows
> for the wrong reason: every one of them had the attacker at 100 or 9 internal
> HP, the only values where all four candidate rules return the same number.
>
> Settled by measurement, not by re-reading the same rows — see A9a for the
> sweeps. `DEFAULT_DISPLAY` is now `ceil`, all 75 observations still reproduce,
> and `tests/test_corpus.py` replays every one of them on each run.
>
> **What this cost:** a wrong rounding rule sat in `DEFAULT_DISPLAY` for months,
> under a heading that said CLOSED, with a green suite. What it took to catch
> was not more care with the same corpus — the corpus could not settle it at any
> level of care — but noticing that the corpus never varied the one input the
> question turns on.
>
> **What survives from the original entry:** that screen rounding and combat
> rounding are *questions* worth keeping apart, and the discipline of writing
> down which observation killed which rule. What does not survive is the claim
> that they are different functions. They are not, and the Mech-at-57 example
> repeated throughout this repo as the proof was never evidence for it.

The original entry follows, kept because the reasoning is still instructive even
though its conclusion is now in doubt.

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

### A6. The fog-of-war visibility rules — **CLOSED, measured against the game**

The game keeps a byte per tile holding **how many of your units can see it**,
at `0x0201763A` on a 15x10 board. `engine/fog.py` reproduces it exactly, 150
tiles of 150, and `tests/fixtures/fog_vision_15x10.json` checks it in as a
regression oracle. Measured:

| rule | measured value | what was assumed below |
|---|---|---|
| radius | Manhattan, from the ROM `vision` stat | correct |
| mountain bonus | **+3** | +1, and off |
| property vision | own tile only | off |
| concealing terrain | Wood/Reef dark beyond 1 step, **on the tile** | applied to the unit, tile left lit |

Three of four were wrong. All four were then re-confirmed on further captures:
adjacency does reveal wood and reef; +3 holds for Infantry as well as Mech with
a unique minimum at 3; and a 19x16 board with no units lights exactly its eight
properties, measuring property vision on its own.

The address is **static** — same `0x0201763A` on both map sizes — so the reader
now dumps the array and `fog.observed_count()` returns the game's own numbers.
The rules are a cross-check against ground truth rather than the source of it,
and `model_disagreement()` re-tests them on every dump that carries the array.
They still do the work for hypothetical placements, where no observed array can
exist. See `DERIVATION.md` 21 and 22.

The array holds the **active player's** view — settled on a three-player board
where P1/P2/P3 own 8/7/9 properties and nobody has units, so the lit count
alone names the owner and it follows whoever is to move (`DERIVATION.md` 23).
There is no second array, so an opponent's visibility is not observable and
must be modelled; that and hypothetical placement are what keep the rules
load-bearing.

Still open: what the identical copy at `0x02017B42` is for, and Sonja's vision
trait.

The original text follows, kept because it is the record of what was assumed
and how wrong being careful can still be.

### A6 (history). The rules while they were assumptions

`vision` is a real ROM field (stats record `+0x0E`, extracted with 152
structural assertions), and its values are strongly consistent with a sight
radius: Recon, Missiles and Sub at 5, the artillery family at 1. That much is
established. Everything `engine/fog.py` builds on top of it is assumption, and
each one is a named switch in `fog.RULES` so a disagreement points at a single
rule rather than at "the fog model".

The defaults lean toward seeing *less*. Between an advisor that cheats — warning
you about an ambush you had no way of spotting — and one that is blind, the
first is worse, because it teaches you to trust a number that will not exist in
a real game.

| rule | default | kill it by |
|---|---|---|
| `radius` — lit within `vision` Manhattan steps | on | standing a Recon alone on empty ground and counting the lit ring: 5 steps means Manhattan, a 5×5 box means Chebyshev |
| `hiding_terrain` — Wood and Reef conceal their occupant unless a viewer is adjacent | on | parking an infantry in woods three tiles from a Recon and seeing whether it renders |
| `property_vision` — an owned property lights its own tile | off | owning a city far from any unit and checking whether it is lit |
| `mountain_bonus` — units on mountains see further | off | the same Recon, on a mountain, counting the ring. Documented in later games in the series; may not be in this one |

Also unmodelled and not currently switchable: Sonja's vision trait, and CO
powers that reveal the map.

**Detection is CLOSED.** Fog is the u8 at `0x0300431D`, battle settings
`+0x0D`, 0 clear and 1 fogged — found by diffing labelled probes across a VS
fog toggle and confirmed by writing it mid-match. `Board.fog` is now real, and
`None` survives only for dumps that predate the field. Static analysis had
predicted `+0x32` and `+0x08`; both were refuted, neither moved. See
`DERIVATION.md` 20.

**The oracle is still open.** A per-tile visibility mask would make every rule
in the table above measurable. The first candidate, `0x03007910`, turned out to
be a table of pointers — including a THUMB function pointer — that the game
fills in when fog switches on, not a mask. See `DERIVATION.md` 20.

`tools/fog_diff.py` now searches both work RAMs for both plausible shapes and
pins any layout using only rule-independent constraints, so the rules cannot
launder themselves into their own test. Validated against planted masks; no
real mask found yet. Until one is, every rule above stays an assumption and
`engine/fog.py`'s defaults stay biased toward seeing less.

### A7. A counterattack happens iff both range rings include 1
`counterattack()` returns a return strike only when `min_range <= 1 <= max_range`
holds for the attacker *and* the defender, which is `fights_at_contact()`. The
ROM ranges are established; the **rule built on them is not**. It encodes two
claims at once: that a direct unit always strikes from an adjacent tile, and
that the game gates the counter on the defender's ordinary range ring rather
than on a separate flag. Both are how the game is documented to behave, and
neither has been put in front of the emulator here.

Before this the function had no range term at all and would quote a Battleship
returning fire from six tiles, so the assumption is strictly better than what it
replaced — but it is an assumption, and it now decides whether a number appears.

**Kill it by:** `harness/record.py --exact --counter` already records the
defender's return strike. Run it once with an Artillery attacking and once with
an Artillery defending: the model says neither battle produces a counter, and a
single observed one refutes it. A Sub or a Cruiser is the case to check for a
hidden per-matchup flag, since both are direct units with restricted targets.

### A8. The counter is quoted from the strongest survivor
The return strike scales with the survivor's HP, and the survivor's HP depends on
which luck roll the opening attack drew. `counterattack()` uses
`max_remaining_hp` — the defender that took the *smallest* roll and hits back
hardest — so the quote is the worst case for the attacker. That is a deliberate
choice of which end of the range to report, not a measurement, and it is the
opposite of the `min_remaining_hp` the function used to read under a comment
claiming that was the pessimistic one.

The asymmetry is intentional and matches `possible_kill`: err outward on what can
be done *to you*, exactly on what you can guarantee. It does mean the counter is
an upper bound and will overstate the return strike whenever the opening attack
rolls well.

**Which half of the formula this actually rests on.** Trace the HP: `survivor_hp`
becomes the return strike's `attacker_hp`, so in the *model* it scales the
attacker-side display term. The return strike's own `defender_hp` is the original
attacker's HP, usually 100, which lands on the degenerate point where every
display rule agrees. So the counter is no more exposed to the defender-HP term
than any other quote — an earlier draft of this entry claiming it depended on
that term entirely was wrong.

**But the model's shape is wrong here anyway — see A9.** The ROM does not run the
strike formula for a counter. `0x080234DA-0x080234F6` overwrites the scaled value
with `base * raw_internal_hp / 100`: no display quantisation, no luck. So
`counterattack()` is not merely reporting the wrong end of a range, it is
applying the wrong arithmetic — it quotes a luck-carrying range where the game
returns a single deterministic number. The one thing A8 asserted that survives is
that the counter uses **post-damage** HP, which all four recorded counters
confirm.

**Kill it by:** nothing — which end of the range to report is a policy, not a
claim about the game. The one thing here that *is* wrong and fixable is below.

**The reported range is not a range.** `counterattack()` picks one survivor and
then calls `resolve()`, which spans the luck of the *counter* only — while the
survivor's HP is itself uncertain across the luck of the *opening* attack. Two
independent variables, one of them collapsed. For a Tank attacking a Tank on
1-star ground the function reports 27–36, but the weakest survivor (43 HP) can
counter for as little as 22, so the true envelope is 22–36 and the printed lower
bound is **5 too high**. The upper bound is correct, which is why "can I die
here" still fails safe — but "my counter will do at least 27" is not supported.
Either report `max_damage` alone, or take the envelope from the weakest survivor
at luck 0 to the strongest at luck 9.

### A9. The strike formula's display rule, and the counter's separate formula
Two claims read off the ROM, **both now confirmed against the game**. Between
them they re-decided A5 and corrected the shape of `counterattack()`. Kept in
full because the route matters: the ROM said what the answer was, and a
measurement said the ROM was describing the live path.

**A9a. The strike path scales both operands by `ceil(hp/10)` — CLOSED, MEASURED.**

`0x080232C8` computes `ctx[+0x0C] = ctx[+0x10] * ((hp-1)/10 + 1) / 10`, and
`0x080232F8` the same into `ctx[+0x0E]` from `ctx[+0x06]`. `(hp-1)/10 + 1` is
`ceil(hp/10)` at every hp in 1..100. Both divides are BIOS `Div` (`svc #6` at
`0x080796C8`).

Confirmed against the game with three seeded sweeps, each writing one unit's HP
and running 64 luck seeds. Predictions were computed before the runs:

| board | `ceil` predicts | `floor_min1` predicts | observed |
|---|---|---|---|
| attacker at 57 | 27–32 | 22–27 | **27–32** |
| defender at 81 | 48–53 | 51–57 | **48–53** |
| defender at 85 | 48–53 | 51–57 | **48–53** |

`round` survives the first and third and dies on the second: at 81 it displays 8
where `ceil` displays 9. `floor` and `floor_min1` die on all three. The
intersection is `ceil` alone, and the observed histograms match its 2:2:1:2:1:2
shape rather than only its endpoints.

Every sweep shipped the control pair — an identity HP write scoring the same as
no write at all — so the numbers are a measurement rather than the machine
agreeing with itself. Sweeps are checked in under `tests/fixtures/` and replayed
by `tests/test_corpus.py`.

A fourth sweep tests the shape of the term rather than the rounding. The
defender factor is `terrain_stars * display_hp(defender)`, a **product**, and
two points can only fit a line -- they cannot test one. Writing the defender to
65 gives a third point on the display axis at 4 stars:

| defender HP | display | stars x display | linear predicts | observed |
|---|---|---|---|---|
| 100 | 10 | 40 | 45-50 | **45-50** |
| 81 | 9 | 36 | 48-53 | **48-53** |
| 65 | 7 | 28 | 54-60 | **54-60** |

Three for three, multiplicities included. `def65` also re-refutes `floor_min1`
by itself: that rule reads 65 as display 6 and predicts 57-63.

**`calibrate.py` can now express an asymmetric rule.** It applied a *single*
display rule to both operands, so "the attacker rounds one way and the defender
another" was not in its search space -- it could be neither eliminated nor
confirmed, and the tool reported a confident single answer either way. The rule
is now two parameters. Against the real corpus it reports the attacker slot with
three survivors and the defender slot with four, which is the honest picture: the
Infantry-at-9 row constrains the attacker side and *nothing* constrains the
defender side. The selftest was strengthened to match, and now recovers a
deliberately asymmetric truth (attacker `floor`, defender `ceil`) exactly.

**Still open, and the sweeps do not touch it:**

- Whether the two operands are the same *function* or two copies that happen to
  agree. Every observation is consistent with either, and the ROM shows two
  separate instruction sequences.
- ~~**The product form across the STARS axis.**~~ **CLOSED.** Two new fixtures
  put the Infantry on Wood (2 stars) and City (3 stars). At a fixed display of
  9, three terrains now pin the stars factor:

  | terrain | stars | stars x display | linear predicts | observed |
  |---|---|---|---|---|
  | Wood | 2 | 18 | 61-68 | **61-68** |
  | City | 3 | 27 | 54-61 | **54-61** |
  | Mountain | 4 | 36 | 48-53 | **48-53** |

  And at full HP: Wood 60-67 observed 60-67, City 52-58 observed 52-58. So
  `terrain_stars * display_hp(defender)` is a product in both factors, and
  `ceil` is confirmed on three terrains rather than one -- it was not an
  artifact of the mountain.
- Where the CO defence byte enters, since every observation is neutral.

**A9b. A counterattack uses `base * raw_internal_hp / 100` — CLOSED, MEASURED.**

`0x080234DA-0x080234F6` overwrites the value `0x080232C8` wrote, discarding both
the ceil scaling and the luck addition. Confirmed against the game, and the
elimination is complete because three independent lines each kill something the
others cannot:

| evidence | refutes |
|---|---|
| 64-seed sweep, Tank→Infantry on mountain: opening ranged **45–50**, counter read **2 every time** | any luck term. No formula carrying a roll can hold constant while the opening varies |
| the same sweep, survivors 50–55 all countering for 2 | `ceil` and `round` display rules — they would give 3 for survivors 51–55 |
| the recorded Mech at 57 HP, observed 27 | `floor` and `floor_min1` display rules — both give 24. Raw internal gives (55·57/100)·90/100 = **27** |

Nothing else survives. `engine/damage.py:counter_damage()` implements it and
`tests/test_damage.py` locks in both the sweep result and all four recorded
counters as regressions.

**Consequences, both already applied.** `counterattack()` no longer runs the
strike formula: it walks the *opening* attack's luck range, maps each surviving
defender through the counter formula, and returns that envelope — so its spread
now comes from which survivor your roll leaves, which is the only variable there
is, and the lower bound is finally a real lower bound. And **A5 is reopened**:
the two counter rows that refuted `ceil` were fitted with arithmetic the game
does not use for them, so they never constrained the strike rule at all.

**Still open, and the sweep was blind to all of it.** Where CO modifiers enter
the counter path — every counter ever recorded was neutral on both sides, so
`counterattack()` now refuses a non-neutral CO rather than inventing a position
for it. The target's display rule, because the target was at 100 HP in every
observation, where all four rules agree. And whether the counter's weapon
selection follows the same max-base rule as a strike (A1).

### A10. A fixture's attacker tile IS the pre-move tile — **CLOSED, MEASURED**

A damage fixture sits at target-select: the move has been chosen, the target
cursor is on the defender, A has not been pressed. The attacker's unit record at
that moment holds **the tile it started on**, not the tile it will fire from.

Measured directly. Re-swept with the position read again after the exchange, the
City fixture reports the same thing on every seed:

    record read (9,7) terrain 5 before confirming, (10,8) terrain 1 after

Road to Plain — 0 stars to 1. Found by arithmetic before anyone looked: the City
counters reproduced at 1 star on all fifteen distinct survivors and at 0 stars on
none, which is what prompted reading the position twice. The formula came out
confirmed and the reader came out wrong, which is the opposite of the way that
usually goes and worth recording as such.

**What it affects.** `attacker_terrain` in every sweep header this harness has
written. Nothing in `engine/`: `damage.counterattack()` takes `attacker_stars`
from its caller, and `threat.py` places units itself.

**Why seven earlier sweeps still scored correctly.** Not because the header was
right — because their pre-move and post-move tiles happened to carry the same
defence. The mountain fixture records Bridge (0 stars) and its counters fit at 0;
Wood records Plain (1 star) and fits at 1. Either those units did not move, or
they moved between tiles of equal defence. That is luck, and it held until a
fixture crossed a star boundary.

**Now recorded rather than inferred.** `dmg_seedsweep` emits
`attacker_terrain_before`, `attacker_terrain_after` and
`attacker_moved_on_confirm`, and errors loudly when they differ.
`tools/counter_check.py` scores against the post-confirm tile and says so.
`tests/test_corpus.py` asserts the finding both ways: the tile it fought from
reproduces every counter, and the recorded tile reproduces none.

**Still open:** whether the record updates at the moment of confirmation or
somewhere later in the animation. Nothing here depends on it -- the reads are
taken after the exchange has settled -- but a live reader polling mid-move would
need to know.

### A11. Per-CO luck comes from the record's +06/+07 bytes — **CONFIRMED**

`engine/damage.py` rolled a flat 0..9 for every CO. Two of the twelve records
say otherwise, at header `+06` and `+07`:

| CO | +06 | +07 | implied range |
|---|---|---|---|
| ten of twelve | 0 | 0 | 0..9 |
| Nell | 10 | 0 | 0..19 |
| Nell, power | 50 | 0 | 0..59 |
| Sonja | 15 | 15 | −15..9 |

One rule covers all of them, with no per-CO branch:

    luck = uniform(0, 9 + good) − bad          good = +06, bad = +07

**Sonja's symmetric pair is what forces that reading.** Take `+06` alone as
"wider roll" and she swings 0..24, a *better* roll than everyone else, which no
one describes her as having. Take `+07` alone as "worse roll" and she sits at
−15..−6, unable to reach zero. Only both together give a window the same width
as everybody else's, slid downward — which is what a luck penalty should look
like. The rule then reproduces Nell's two ranges for free.

Those are also exactly the ranges the community documents. Two sources agreeing
on a rule that neither states outright was worth a great deal, and the game has
now been seen to agree with both.

**Measured.** One fixture, Tank → Infantry in woods, three COs written with
`co_abilities = 1` so the write actually reaches the damage path (A12):

| CO | predicted damage | observed | witness |
|---|---|---|---|
| Andy | 60–67 | 60–67 | — |
| Nell | 60–75 | **60–75** | damage 75 needs a roll of **19** |
| Sonja | 48–67 | **48–67** | damage 48 needs a roll of **−13** or lower |

A standard CO cannot exceed 67 on this board or fall below 60, so each result
is outside the band by construction, not by inference. Nell's top is pinned
exactly — a roll of 19 was witnessed. Sonja's floor is not: the lowest roll
seen was −13, so her range is confirmed to extend below zero without −15 itself
being observed.

One model value went unrolled in each sweep (63 for Nell, 55 for Sonja). At 64
seeds over 20 and 25 rolls a given roll is missed 3.8% and 7.3% of the time, so
that is sampling. What matters is that neither sweep produced a damage the
model *cannot*, which is the direction that would refute it.

**Why it was applied before it was measured.** For Sonja the alternative was
worse than being unverified: a negative roll lowers the minimum, so treating her
as standard reports guaranteed kills that will not land. On Tank → Infantry alone there are 15
defender-HP values where the old model said KILL and hers does not. Nell's error
ran the safe way — understating her maximum — but Sonja's ran the dangerous way,
and that asymmetry is the reason this is a correctness fix rather than a
refinement.

**Kill it by:** `tools/luck_range_check.py` on a seed sweep taken with the CO
**chosen in VS setup**, or written alongside `0x03004318 = 1`. Writing
`+0x1D` alone does nothing while that flag is clear -- see A12. Four sweeps
were taken that way (Andy, Nell, Sonja, Max) and all four returned the same
60-67 band, which measured nothing. It inverts each observed damage back to the
roll that produced it and reports what was *witnessed*, not what fits. The bar
is deliberately high: rolls landing inside 0..19 prove nothing, because 0..9
lands inside 0..19 too. Only a roll **above 9** confirms Nell and only one
**below 0** confirms Sonja, and the tool says NOT SETTLED rather than
CONFIRMED when a sweep fails to produce one. Use a 0-star matchup so damage
separates every roll; on starred terrain several rolls collapse onto one damage
and witness nothing.

## Unknown — not modelled

- **CO powers.** The second 128-byte stat block is selected by army `+0x1E`,
  and its contents are extracted, but nothing triggers or models activation.
- **Where Kanbei's and Sturm's strength is applied.** Their per-unit modifiers
  are all 100/100; their records carry `+08/+09` and `+11/+12` pairs that the
  damage path has never been shown to read. `engine/co.py` reports these as
  unmodelled and `Attack.between()` refuses to quote those COs rather than
  returning a number that would be ~20% low. **Kill it by:** building two
  fixtures in VS mode, one with Kanbei chosen and one with Andy, and comparing
  the damage with the RNG seeded. NOT by writing army `+0x1D` mid-fixture,
  which this file recommended for months and which cannot work -- see A12.

  **Strong lead, not yet acted on:** decoding `+11/+12` as
  `(UniversalATK, 200 − UniversalDEF)` yields 100/100 for every CO normally and
  110/110 under power — and **Eagle under Lightning Strike at 80/70**, which is
  an arbitrary, specific pair that the community documents outright. Kanbei
  reads 120/120 (140/130 powered) and the two Sturm records 130/80 and 80/120.
  If that decoding holds, `+11/+12` is not an unknown at all: it is the
  universal attack/defence pair, and the refusal below can be lifted by
  applying it. The subtraction convention came from the community formula, not
  from us. Worth confirming against the disassembly before shipping.
- The `fighter-secondary` ROM discrepancy (see `DERIVATION.md`).
- ~~Weather effects, fog of war.~~ Weather is read from `0x0300433C` and drives
  movement cost. Fog is modelled but unverified and cannot yet be detected —
  see A6.
- Terrain movement costs, capture, supply, repair — all of milestone 1/3.
- Whether the RNG can be read and predicted. Explicitly out of scope for now;
  the model deals in damage *ranges*.

### A12. A written CO changes nothing while `0x03004318` is clear — **TRACED**

The CO id at army `+0x1D` is real and confirmed: writing it swaps the CO the
intel screen reports, which is how all twelve records were named. It does not
follow that combat reads it, and combat does not.

Four seed sweeps on the same target-select fixture, Tank → Infantry in woods,
identical but for a CO written after every reload:

| CO written | per-unit Tank mods | predicted band | observed |
|---|---|---|---|
| Andy (1) | 100/100 | 60–67 | 60–67 |
| Nell (0) | 100/100 | 60–75 | 60–67 |
| Sonja (7) | 100/100 | 48–67 | 60–67 |
| **Max (2)** | **150/100** | **90–97** | **60–67** |

Max is the one that matters. His modifiers are not in doubt and they are large;
if the write reached the damage path his band could not sit on Andy's. The
write itself is fine — the harness reads the byte back and it holds. Combat has
simply already resolved its CO by the time the cursor is on the target,
presumably when Fire was chosen.

**Why, and the first answer here was wrong.** This entry originally concluded
that the damage path does not read `+0x1D`. It does. `DERIVATION.md` 24 traces
the fetch: both the attacker's and the defender's modifier lookups read
`[army+0x1D]`, multiply by the 292-byte record stride, and index
`0x08284A0C` — but each is gated on `[0x03004318]`, and when that byte is clear
they branch to a hardcoded `292 * 1`, **record 1, Andy**, for both sides.

That byte reads 0 in all four VS captures. So the sweeps did not fail to change
the CO; they ran four times in a match where COs are switched off, and measured
Andy each time. The write was never the problem.

The flag was in the file already. Section 20's tally of the settings struct
ranked `+0x08` top for boolean-shaped reads, 223 of 257, and dismissed it in a
line as "probably a more general flag" because it also appears in the
movement-cost path. It is general — it gates the CO's movement table for the
same reason — and that generality was the finding, not a reason to look
elsewhere.

**What it cost.** Two documented kill conditions ran through this mechanism —
A11's, and the long-standing plan to settle Kanbei by writing `+0x1D` and
comparing damage. Both are usable again *provided the flag is set*, and both
were unusable as written, because with it clear they return "no difference" for
every CO. The Kanbei one would have concluded "the header fields do not reach
the damage path" — confidently, and backwards.

**What it did not cost.** Nothing measured. Every checked-in sweep has
`co_written: null` and all 75 corpus rows are neutral, so no shipped number
came through this route. The luck ranges in A11 remain a ROM-derived
interpretation — untested, not refuted.

`harness/mgba_dmg.lua` still accepts `opts.co`, because it does swap the
identity for anything reading `+0x1D` live, but it now prints a warning, and
`tools/luck_range_check.py` refuses to interpret a sweep that used it.

**The open question this leaves:** what sets `0x03004318`, and whether writing
it mid-fixture is enough or the game latches CO state earlier. Reading 0 in four
VS captures says only that those matches had it clear — not which setup option
clears it. The cheap test writes `0x03004318 = 1` alongside `+0x1D` and predicts
Max on Tank → Infantry in woods moves from 60-67 to **90-97**; a null there
means the flag is latched earlier and the fixture has to be built with CO
abilities already on.

### A13. The CO attack modifier truncates before anything else — **MEASURED**

`DERIVATION.md` 7 read the modifier application off the code years ago:
`muls` then `__divsi3`, which truncates toward zero. It also said, correctly,
that this eliminated nothing at the time — with a neutral CO at 100/100 the
division is exact and the truncation is a no-op.

Every one of the 75 corpus rows and every seeded sweep was neutral. So the
engine carried the term as an exact `Fraction(base * co_atk, 100)` for the
project's whole life, and no test could tell.

The first non-neutral measurement caught it immediately. Max is 150/100 on
Tank; on Tank → Infantry in woods, both at full HP:

| | attack term | predicted band | doubled values |
|---|---|---|---|
| exact | 75 × 150/100 = 112.5 | 90..97 | 90, 94 |
| **truncated** | **floor(112.5) = 112** | **89..96** | **92, 96** |
| observed | — | **89..96** | **92 (×11), 96 (×14)** |

Both the range and the shape. The other six damages appeared 5–8 times each, so
the two doubled values are unambiguous, and they are the pair only truncation
produces. `engine/damage.py` now computes `base * co_atk // 100`.

**Not settled: the defence modifier.** The disassembly applies its divide twice
in sequence, attack then defence, so it very likely truncates too — but the
engine folds `co_def` into a single combined defence term and `co_def` has been
100 in every measurement taken. There is nothing to fit, so the second
truncation point stays unmodelled rather than guessed. **Kill it by:** a sweep
against a defender whose CO carries a non-100 defence modifier — Sami is 90 on
foot units, Eagle 90 on air — with `co_abilities = 1` set.

`tests/fixtures/max_wood_co.json` is the sweep, checked in, and
`tests/test_corpus.py` replays it. The replay had to learn the gate from A12:
it computes the CO the game *actually used*, which is Andy for any sweep that
wrote a CO without also setting `0x03004318`.

### A14. The universal pair at +11/+12, and where the defence modifier lands — **MEASURED**

Two sweeps on one board, Tank → Infantry in woods, where a neutral CO is
confined to 60–67. Both with `co_abilities = 1`, without which the damage path
uses Andy regardless (A12).

| | value | predicted | observed |
|---|---|---|---|
| Kanbei attacking (`+11` = 120) | 75 → **90** | 72–79 | **72–79** |
| Kanbei defending (`+12` = 80) | 75 → **60** | see below | **48–55** |

**The attacking half lifts the refusal.** Kanbei's per-unit entries are all
100/100 and his strength is entirely in the header, so `Attack.between` used to
decline rather than quote him as neutral. `+11/+12` is now read as a pair of
percentages applied raw — `value * x / 100` — so 100 is neutral, a higher
attack number hits harder, and a *lower* defence number takes less, because the
defence byte is stored already subtracted. `unmodelled()` returns empty for
every CO and the refusal is gone. `+08/+09` holds the same pair before any
power bonus, so applying `+11/+12` accounts for it.

**The defending half changed the formula's shape**, which was not the point of
the experiment. Three candidates existed:

| where the defence modifier goes | predicts |
|---|---|
| nowhere, ignored | 60–67 |
| added inside the terrain bracket, `200 − (co_def + stars·hp)` | 45–50 |
| **multiplying the value, before luck** | **48–55** |

The engine had used the second form since the beginning. It is wrong, and no
measurement could have said so: at `co_def = 100` it is arithmetically
identical to the third, and all 75 corpus rows and all twelve earlier sweeps
were neutral on defence. Kanbei is the first defender that separates them.

So the strike is now two truncating multiplies on the value — attacker's side,
then defender's — exactly the "twice in sequence, attack then defence" that
`DERIVATION.md` 7 read off `__divsi3` years ago, with the terrain bracket
reduced to `(100 − stars·hp_d)/100`. `counter_damage` gets the same correction
for the same reason.

**Measured for the universal half only.** The per-unit defence value is assumed
to be a multiplier in the same convention — so Max's 110 on indirect defence
means taking 10% *more*, not less — because it comes from the same table and is
applied at the same point. No sweep has yet used a CO with a non-100 per-unit
defence. **Kill it by:** sweeping against Sami's Infantry (90 on foot) or
Eagle's air (90), with `co_abilities = 1`. If the convention is inverted there,
the sign of every per-unit defence modifier is wrong.

Also still open: the counter path under a non-neutral CO. `counterattack()`
raises `CounterModifiersUnknown` rather than guess, because the ROM folds the
defence byte in at a different point there and every counter ever recorded was
neutral on both sides.

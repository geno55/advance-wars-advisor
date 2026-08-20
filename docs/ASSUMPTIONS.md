# Assumptions, and how to kill each one

Everything in this project is in exactly one of three states. The point of this
file is that nothing gets to sit in the middle state quietly.

| state | meaning |
|---|---|
| **established** | derived from the ROM or measured against the running game, and asserted by a test |
| **assumed** | plausible, load-bearing, and *not yet checked against the game* |
| **unknown** | not modelled at all yet |

An assumption that gets measured is compressed to an **Established** bullet and
its number parked in the **Retired** ledger at the bottom, because code, tests
and the README cite entries by number. The full accounts — the sweeps, the
refuted alternatives, the wrong turns — live in `DERIVATION.md`, in the checked-in
fixtures, and in this file's git history.

## Established

- Base damage matrices, both weapons, all 18 units — extracted from ROM, 17
  structural assertions, 24 regression tests. See `DERIVATION.md`.
- Internal unit ID map, including the six vestigial gaps.
- Unit roster and names.
- **Index formula** `table + (att-1)*24 + (def-1)`, read off the disassembly.
  In-RAM type IDs are 1-based; table rows are 0-based.
- **Out-of-ammo Fighters cannot attack.** The alternate ROM copy that gives them
  a secondary weapon has zero code cross-references — dead data.
- **Weapon selection is a comparison, not a flag.** In the combat damage
  function at `0x08022BFC`: the primary's modifier-applied damage lands in
  `r8` — or 0 when a gate zeroes it — the secondary's in `[sp,#4]`, and
  `cmp r8, r2` / `bhi` at `0x08023294` fires whichever is larger. There is
  **no per-matchup weapon flag** anywhere in the ROM, which is the alternative
  A1 suspected the rule of merely coinciding with. `bhi` is strict, so in
  combat a tie keeps the **secondary** — a correction: this bullet first cited
  the forecast helper at `0x08060D00`, whose `blt` keeps the *primary* on a
  tie. The two sites genuinely disagree, unobservably: no matchup ties (the
  closest gap is 14) and the shared attacker-indexed modifier cannot collapse
  one — that needs m < 8 against CO records that bottom out at 80. The engine
  mirrors combat. Measured in **both directions**, which is what rules out an
  always-primary or always-secondary reading: the corpus fires the secondary
  for Tank → Infantry (75 over 35, observed 78 on road where the primary caps
  at 44) and Tank → Mech (70 over 30, observed 43 on mountain where the
  primary caps at 23), and the **primary** for Mech → Tank (55 over 6, the
  recorded counter at 57 HP reading exactly 27 where the secondary gives 2).
  Retired A1.
- **The out-of-ammo fallback is the ROM's own arithmetic.** The primary is
  skipped when `ldrh [record+4] & 0x780 == 0` — bits 7..10, exactly the ammo
  field the reader established — at `0x08022E04` (contact) and `0x0802306C`
  (range ≥ 2). A gated primary scores 0 into the same comparison a weak one
  does, so "out of ammo" falls back to the secondary *even when the primary is
  the larger weapon*, and a unit with neither stores nothing and cannot
  attack. The converse is four instructions later: `subs #1` on ctx `+0x0A` at
  `0x080232B2` executes **only on the primary branch** — the primary spends a
  round per shot, the secondary never does, on strikes and counters alike,
  since both roles run the same function. Retired A17.
- **The counter's gates are the range fields, read where they act.** The
  caller at `0x080235D2/DE` runs the damage function once per role with the
  Manhattan distance between the two records in `r2`; the counter role is
  rejected outright at any distance other than 1 (`cmp ip, #1` at
  `0x0802307C`), and at contact the shooter's primary additionally requires
  stats byte `+0x10` — min range — to equal exactly 1 (`0x08022DF8`), its
  secondary needing only a nonzero base. No separate counter flag exists.
  `fights_at_contact()`'s `min_range <= 1 <= max_range` reproduces all of it
  on this stats table. Retired A7.
- **The capture arithmetic**, read at `0x08026180–0x080262E4`: each capture
  action adds `ceil(hp/10)` — computed as `(hp−1)/10 + 1` via BIOS Div, the
  same idiom A9a read on the strike path, so the rate IS the displayed bar
  count — **plus a CO bonus** from record byte `+0x0D`: `bars >> (8 − byte)`,
  fetched behind the same `[0x03004318]` gate as the damage modifiers. Eleven
  records carry 0 (a `>> 8` no-op); **Sami carries 7 in both blocks** —
  `bars >> 1`, her documented 1.5× rate, truncated — which the engine had
  silently omitted until this read. Progress clamps at 20 (`cmp #0x13` /
  `movs #0x14`), the property falls when the re-read value exceeds 19, and
  units spawn with the field zeroed (unit-init at `0x08024226`). The transfer
  special-cases HQ — terrain `& 0x1F == 8` — into a branch that reads the fog
  flag and army `+0x1C`; not decoded. **And now measured live, headlessly**
  (`tests/fixtures/capture_probes.json`, driven by `harness/mesen_capture.lua`):
  exactly the foot class captures — Infantry and Mech read +10, all twelve
  testable non-foot types moved onto the city, executed Wait and read 0, and
  the four naval types never reached the menu, which is asserted UNREADABLE
  rather than read as a "no", because a naval unit on a city is not a board
  the game can reach. The rate read 10/7/5/1 at 100/70/45/9 HP on a gate-0
  board (Andy, +0 bonus). **Moving resets progress** — written progress 15
  plus a one-tile move captured at 10, the fresh bar count — and **staying
  keeps it**: a second stationary capture two rounds later completed 20 and
  the city FELL, owner byte flipping to P1 with the progress field cleared.
  Retired A15.
- **The strike formula is `luck_after_hp`, and it is exact.** One surviving
  variant: both CO modifiers folded into the base (each truncating), the
  attacker's display-HP term, the luck roll added after it, and the terrain
  bracket `(100 − stars × display_hp_d)/100` on the whole — a product in both
  of its factors, pinned on three terrains and three display values.
  `resolve()` returns an exact range, so "cannot kill" is as reliable as "will
  kill". Settled by crossing two independent eliminations — the 75-row corpus
  and seeded sweeps — neither sufficient alone. **The display-HP term
  truncates before luck is added** — `0x080232C8` is BIOS Div and always said
  so, but the engine carried the product as an exact fraction for the
  project's whole life and nothing could object: every measurement had
  base × display divisible by 10, so the fraction never survived to the
  floor. The A16 bracket sweep was the first to fight an Infantry at display
  9 — 5 × 9/10 = 4.5 — and the game dealt 3 at luck 0, which only
  `floor(4.5) + 0` produces. The fourth pinned-variable truncation in this
  formula, caught by a sweep aimed at something else. `DERIVATION.md` 17, 26.
  Retired A2, A14.
- **CO modifiers truncate individually.** `(value * mod) / 100` via `__divsi3`,
  applied twice in sequence. Any variant that rounds only at the end is wrong.
  Measured on both sides: Max's 150 lands 89–96, the band only `floor(112.5) =
  112` produces, and Sami's 90 gives `75 * 90 // 100 = 67`, not 67.5. Retired
  A13.
- **Header `+11/+12` is the universal CO pair**, applied raw as
  `value * x / 100` on each side — the defence byte is stored already
  subtracted, which is why Kanbei reads 120/80. Measured in both directions
  (Kanbei attacking 72–79, defending 48–55, against a neutral band of 60–67),
  and the per-unit pool multiplies with the same convention and sign (Sami's
  90 on Infantry: 53–60). `+08/+09` is the same pair before the power bonus.
  Retired A14.
- **Combat display HP = `ceil(internal / 10)`**, and the on-screen bar count is
  the same function. Measured by writing HP and sweeping 64 luck seeds: an
  attacker at 57 deals 27-32 (only `ceil`), a defender at 81 takes 48-53 (only
  `ceil` -- 81 is the value that also excludes `round`). Observed multiplicities
  match ceil's 2:2:1:2:1:2 collapse, not just its endpoints. `display_hp()` and
  `screen_bars()` are kept as separate functions because they answer separate
  questions, but nothing measured separates their values. Retired A5, A9a.
- **The counterattack formula** is the strike's arithmetic on the survivor's
  RAW internal HP, with no display quantisation and no luck roll: both CO
  modifiers multiply the base, each truncating, then `* survivor / 100`, then
  the terrain bracket. Measured twice over -- a 64-seed sweep held the counter
  at 2 while the opening ranged 45-50, and four more located the CO pair across
  256 cases. `counterattack()` maps every survivor of the opening's own luck
  range through it, so the quote is a real envelope over the one variable there
  is. **And the bracket's rounding at a damaged target is measured too**
  (`tests/fixtures/counter_bracket_probes.json`, 27 live battles): the target's
  display HP is `ceil`, the strike's own rule — 12/12 at 81 HP where `round`
  and the floors fit none, 12/12 at 57 HP where the floors fit none, and 3/3
  full-HP controls where all rules agree, run first to prove the rig. Every
  observed strike damage names the survivor exactly, so each row is one exact
  equation with no luck term. Retired A8, A9b, A16.
- **Combat luck is a u32 at `0x03001D30`**, found by bisection; writing it
  makes the roll an input, which is what removed the sampling problem. The
  roll is `uniform(0, 9 + good) − bad` from the CO record's `+06/+07` bytes:
  ten records carry 0/0 and roll 0..9, Nell reads 0..19 (0..59 powered) and
  Sonja **−15..9**. Measured — a roll of 19 witnessed for Nell and −13 for
  Sonja on a board where a standard CO is confined to 60–67. Sonja's exact −15
  endpoint has not itself been witnessed; the rule that implies it has, twice.
  `DERIVATION.md` 16. Retired A4, A11.
- **The damage path reads `[army+0x1D]` only while `[0x03004318]` is set.**
  With the gate clear it branches to a hardcoded record 1 — Andy — for both
  sides, which is why writing a CO mid-fixture in a VS match with abilities
  off measures nothing. Traced in `DERIVATION.md` 24 and measured with Max,
  whose 150 cannot hide. Retired A12.
- **A fixture's unit record holds the pre-move tile** at target-select; the
  tile it fires from reaches the record only after confirming. Found by
  arithmetic — fifteen distinct survivors fitting 1 star and none fitting the
  recorded 0 — then read directly. The harness records both tiles and errors
  when they differ. Retired A10.
- **Terrain defence stars**, all 20 slots, from the ROM struct array at
  `0x284170` byte `+8` (stored as stars × 10). Asserted against the 14 values
  read off the in-game display, and three independent routes agree with no
  shared inputs. Retired A3.
- **Per-property income is 1000/turn**, from the same struct at `+4`, nonzero
  for exactly the five capturable types (City, HQ, Airport, Port, Base).
- **Terrain id 9 is the sky**, and it has 0 defence. Air units are given the
  sky as their terrain instead of the tile beneath them; that is how the game
  denies them terrain cover, with no per-unit branch anywhere. Use
  `Board.defence_for(x, y, move_type)`, not `Board.defence(x, y)`, for anything
  that might be an aircraft.
- **Fog of war, detection and rules.** The flag is the u8 at `0x0300431D`,
  battle settings `+0x0D`, confirmed by writing it mid-match. The game's own
  visibility is a byte per tile at `0x0201763A` — a viewer *count*, the
  **active player's** view — and `engine/fog.py` reproduces it 150 tiles of
  150: Manhattan radius from the ROM `vision` stat, **+3** on mountains, a
  property lights its own tile, and Wood/Reef are dark beyond one step **on
  the tile itself**. Each rule re-confirmed on a capture built to isolate it.
  `DERIVATION.md` 20–23. Retired A6.

- **The CO power system** — charge, threshold, activation, effects, lifetime
  — read off the ROM at named addresses and measured live (DERIVATION 27,
  `tests/fixtures/power_probes.json`). The meter is a u32 at army `+0x20`,
  charging `value × display_HP_lost` for your own losses plus a quarter of
  the opponent's term, where `value = cost/10 × header[+08]/100` — that
  header pair is a unit-VALUE multiplier (Kanbei 120), not attack/defence.
  Threshold = record cost (u32 at true base +0x08; Sami 25000, Drake 40000,
  Kanbei/Eagle/Sturm 50000, rest 30000) × (100 + 20 per use, capped 200%)
  / 100; uses at `+0x25`, ready latch at `+0x24`. Charging requires
  `[0x03004317]` (the VS CO Power rule; the rule also sets the modifier gate
  `0x03004318`, closing that unknown) and stops while a power runs.
  Activation (map-menu Power item): meter to 0, `+0x1E` = 1 until the start
  of the caster's next turn. One-shots, all measured: Andy +2 display HP
  free via the repair path; Olaf snow for the power's lifetime; Drake −10
  internal to every enemy, floor 1; Sturm −80 internal (record 10) or −40
  (record 11) in a Manhattan-2 blast; Eagle clears the acted bit on
  non-foot units; Sami's block swaps to movement tables where foot pays 1
  everywhere; Grit's indirects reach max range +2 (measured 5, refused 6).

## Assumed — these are the ones that will bite

Nothing, currently. Every assumption this file has carried is either in
Established above or in the Retired ledger below, each killed by a
measurement or a read with its account named. The section stays, because the
next composed feature will refill it — that is what happened with A15 and
A16, both born the day action enumeration was written.

## Unknown — not modelled

- **Sonja's power semantics.** Her record's header byte 0 reads 0 where every
  other record reads 1, and her power block alone sets header byte 1 — shaped
  like the HP-hiding trait and its power-side reveal, but both bytes are
  unconsumed by any code path read so far and unmeasured (the powers-on
  fixture has fog off). Same bucket as her vision trait below.
- **The meteor's target selection.** Damage and radius are measured
  (DERIVATION 27); the scoring that picked the enemy cluster is not read.
  The constant lives in the entry functions at `0x0801CC88`/`0x0801CCA0`;
  the picker is somewhere in the meteor object's tick.
- **Header `+09`.** The value pair's second byte (Kanbei 120). Its partner
  `+08` is the meter-value multiplier; nothing has been seen reading `+09`.
- **The tile→unit index.** Writing a unit record's `x,y` relocates the
  record but not the unit the game lets you select — the stay-position probe
  read `acted 0` with the record sitting on the city. Some structure beyond
  the unit array maps tiles to units, and it has not been found. Harness
  rule until it is: type/hp/ammo/capture writes are proven transparent,
  position writes are NOT for selecting your own unit — drive real moves
  instead. The A16 probe sharpened the boundary: a teleported ENEMY is
  targetable (Fire found it and the battle resolved), and a terrain byte
  written into the logic map carries real defence (the full-HP controls
  reproduced the written Wood's bracket exactly), so whatever the index
  gates, it is selection, not combat.
- **The 24-byte table at `0x08283FC8`.** At contact, a defender whose record
  `+1` carries bit `0x20` routes the *primary* lookup through it, indexed by
  **attacker type alone** (`0x08022E12–2E32`). A damage table that ignores the
  defender's type fits damage-vs-dived-sub, but the bit and the bytes are
  undecoded and nothing models it — an engine quote against whatever state
  bit `0x20` is would use the wrong table. Found in passing while reading the
  weapon-selection gates.
- **The identical visibility copy at `0x02017B42`.** Dumped only as a
  cross-check; no known purpose.
- **Sonja's vision trait** under fog.
- **When the unit record updates during a move.** The pre-move tile is what a
  fixture holds (Established); whether the record flips at confirmation or
  later in the animation is unread. Nothing shipped depends on it — only a
  live reader polling mid-move would care.
- **The RNG's generator.** The combat luck state at `0x03001D30` is writable,
  which is how sweeps seed it, but the update function has never been derived,
  so rolls are ranges, never predictions.
- **Supply, repair, joining, unloading, production** — the turn mechanics
  `engine/actions.py` declines to offer rather than guess; each is named in
  its docstring with the reason.

## Retired — measured, and compressed into Established above

Numbers cited by code, tests, the README and old commit messages resolve here.
The measured content is in the Established bullets; the full accounts are in
`DERIVATION.md`, the checked-in fixtures, and this file's git history.

| entry | finding | full account |
|---|---|---|
| A0 | the disassembly walk was never continued; everything it deferred was settled by measurement instead | git history |
| A1 | weapon selection is `cmp`/`bhi` at `0x08023294` — the larger fires, a tie keeps the secondary; no weapon flag exists (an earlier reading cited the forecast site `0x08060D00`, whose `blt` disagrees on the unobservable tie) | `tests/test_damage.py`, `data/aw1_damage.json` `code_analysis.weapon_selection` |
| A7 | the counter role is gated to distance 1 and the primary to min_range 1 — the range fields, no separate flag | `code_analysis.weapon_selection`, `engine/damage.py` `fights_at_contact` |
| A2 | the formula is `luck_after_hp`, exact | `DERIVATION.md` 17 |
| A3 | terrain stars from the ROM struct at `0x284170+8` | `DERIVATION.md` |
| A4 | combat luck is a u32 at `0x03001D30`; the roll is uniform 0..9 | `DERIVATION.md` 16 |
| A5, A9a | display HP is `ceil(internal/10)`, both operands, both paths that use display | fixtures `att57`/`def81`/`def85`/`def65`, `wood`/`city` |
| A6 | the four fog rules, measured; detection at `0x0300431D` | `DERIVATION.md` 20–23 |
| A8 | superseded by A9b: the counter has its own formula, and the quote is an envelope over the opening's luck | git history |
| A9b | counter = both CO modifiers on the base, × survivor's raw HP / 100, then the strike's bracket; no luck | `tests/test_corpus.py`, four 64-seed sweeps |
| A10 | a fixture's record holds the pre-move tile | sweep headers, `tests/test_corpus.py` |
| A11 | per-CO luck from record `+06/+07` | fixtures `nell_wood_luck`/`sonja_wood_luck` |
| A12 | `0x03004318` gates the CO fetch; clear means Andy on both sides | `DERIVATION.md` 24 |
| A13 | the CO attack modifier truncates | fixture `max_wood_co` |
| A14 | `+11/+12` is the universal pair; the defence modifier lands on the base; both sides truncate | fixtures `kanbei_att_wood`/`kanbei_def_wood`/`sami_def_wood` |
| A15 | capture: foot-only, rate = bar count (+CO shift), moving resets, staying accumulates to a fall at 20 — arithmetic read off the ROM, rules measured live | `tests/fixtures/capture_probes.json`, `harness/mesen_capture.lua` |
| A16 | the counter's terrain bracket reads the damaged target's display HP with `ceil`, the strike's rule — 27 live battles, alternatives refuted 0/12 each | `tests/fixtures/counter_bracket_probes.json`, `harness/mesen_counter_bracket.lua` |
| A17 | ammo gates the primary (`& 0x780` at `0x08022E04`/`0x0802306C`); the fallback is the same branch as "weaker weapon"; only the primary decrements ammo (`0x080232B2`) | `code_analysis.weapon_selection`, `tests/test_damage.py` |

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
- **The strike formula is `luck_after_hp`, and it is exact.** One surviving
  variant: both CO modifiers folded into the base (each truncating), the
  attacker's display-HP term, the luck roll added after it, and the terrain
  bracket `(100 − stars × display_hp_d)/100` on the whole — a product in both
  of its factors, pinned on three terrains and three display values.
  `resolve()` returns an exact range, so "cannot kill" is as reliable as "will
  kill". Settled by crossing two independent eliminations — the 75-row corpus
  and seeded sweeps — neither sufficient alone. `DERIVATION.md` 17. Retired
  A2, A14.
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
  is. Retired A8, A9b.
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

## Assumed — these are the ones that will bite

### A15. The capture rules behind `engine/actions.py`

The capture PROGRESS FIELD is measured: unit record `+4` packs it alongside HP
and ammo, it reads 0..20 in live captures, and a mid-capture infantry is what
exposed the `ammo = v >> 7` error. The capturable terrain ids `{6, 8, 10, 11,
14}` come out of the ROM terrain extraction with its structural assertions.
Everything else action enumeration does with capture is assumed from the
game's manual and from play, and none of it has been put in front of the
running game:

- **Who may capture = `unit_class == "foot"`.** The class is a ROM field, so
  the code stays free of unit names, but the claim that this class is exactly
  the set of capturers is an inference — the field could mean something else
  and coincide on Infantry and Mech.
- **Rate = displayed HP bars per capture action**, accumulating to a fall at
  exactly 20. The 0..20 range is observed; the per-turn increment and the
  threshold behaviour have never been swept.
- **Moving off the tile resets progress to 0.** Held from play; never
  measured, and the reset has never been observed in the progress byte.

**Kill them by:** the spike harness already proves write-then-drive is
transparent to the game (README, milestone 3). One sweep settles all three:
restore a fixture with a unit beside a neutral city, write its type across all
18 units, press A on the city, and read whether the menu offers Capture — that
is the class rule. Then write HP to 45 and 100 on a capturer, capture, and
read the progress byte — that is the rate. Move it off and back and read the
byte again — that is the reset. Ship the control case like every sweep.

### A16. The counter's terrain bracket at a damaged target

`counter_damage()` closes with the strike's terrain bracket on the **target's**
display HP, and display is `ceil` — but every measured counter landed on a
target at 100 internal HP, display 10, where all four display rules agree. So
the bracket's rounding on the counter path is inherited from the strike, not
measured; A9b's sweeps were blind to it by construction, the same way the
corpus was blind to the strike's rule for months (retired A5).

**Kill it by:** one counter sweep with the ORIGINAL attacker written below
full HP on starred terrain. 81 internal separates `ceil` from `round`, 57
separates `ceil` from the floors — the same discriminating values that settled
the strike (retired A9a).

## Unknown — not modelled

- **CO powers.** The second 128-byte stat block is selected by army `+0x1E`,
  and its contents are extracted, but nothing triggers or models activation —
  including powers that reveal the map. The meter at army `+0x20` charges both
  the dealer and receiver of damage; the activation threshold and gain formula
  are unknown, so it is exposed raw and never as a percentage.
- **What sets `0x03004318`.** Reading 0 in four VS captures says those matches
  had CO abilities off, not which setup option clears it. Until that is
  settled, a fixture that needs a live CO must be built with the CO chosen in
  VS setup. The cheap test writes it to 1 alongside `+0x1D` and predicts Max
  on Tank → Infantry in woods moving from 60-67 to **90-97**; a null there
  means the game latches CO state earlier than target-select.
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
| A17 | ammo gates the primary (`& 0x780` at `0x08022E04`/`0x0802306C`); the fallback is the same branch as "weaker weapon"; only the primary decrements ammo (`0x080232B2`) | `code_analysis.weapon_selection`, `tests/test_damage.py` |

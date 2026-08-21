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
- **Sonja, whole** (DERIVATION 28). Vision: pool entry `+8` is a per-unit
  vision adjustment the fog marker adds — Sonja +1 on everything but Sub,
  +3 under Enhanced Vision — and her power block's header byte 1 makes the
  marker skip wood/reef concealment entirely. Both measured against the
  game's own count array, 150/150 tiles on three captures
  (`tests/fixtures/sonja_vision_*.json`). HP: header byte 0 is an
  "HP visible to enemies" flag, 0 only on her records; the enemy-side panel
  reads it every frame (`0x0802B2F6`) and draws `?` for the HP digit.
  Display-side only — combat uses the real HP. Also read off the same
  marker: the mountain +3 is FOOT-ONLY (`0x0801ECCE`).
- **The luck consumption, whole** (DERIVATION 32). The combat luck block
  at `0x0802333A` draws once from the derived generator and reduces
  `draw % (10 + good) − bad` through the CO record's `+0x2A/+0x2B` pair,
  flooring the total at 0. A battle resolution makes exactly four such
  draws — the strike keeps the THIRD, the counter's is discarded (A9b's
  "no counter luck", explained) — measured 20/20 across Andy (%10), Nell
  (%20, a live 16) and Sonja (%25−15, a live −14), with the fixed-luck
  byte proving all four draws come from this one block (set, the battle
  draws nothing and adds +5 flat). Given a state read at target-confirm
  the strike's damage is a point: `rng.strike_luck()` +
  `damage.damage_for_luck()`. The stated boundary: the draw index is
  measured on the standard drive; no other UI path into the resolver has
  been swept. `tests/fixtures/luck_probes.json`.
- **The dived sub, whole** (DERIVATION 31). Unit flags bit `0x20` is the
  Dive state — set by the Dive command (`0x08066E90`), cleared by Rise
  (`0x08066EAC`), menu-visible as Wait/Dive and Wait/Rise, and honoured
  when written. A dived defender swaps the primary damage lookup for the
  24-byte attacker-indexed table at `0x08283FC8`: Cruiser 90, Sub 55,
  zero for the other twenty-two — the same values the surfaced matrix
  gives the two hunters, so diving deletes attackers rather than softening
  them; no unit has a secondary against a Sub, making the primary-only
  gate complete. Measured: Cruiser 95 into dived and surfaced alike
  (90+5), BCopter refused Fire against dived and dealt 30 (25+5) to the
  surfaced control. `select_weapon(defender_dived=)`, `Unit.dived`,
  `tests/fixtures/dive_probes.json`.
- **Meteor Strike, whole** (DERIVATION 30). The meteor centres on an enemy
  UNIT picked by one of three scans, chosen by a single RNG draw mod 3:
  funds value / raw internal HP / funds value with indirects doubled — each
  scan sums a Manhattan-2 diamond, allies subtract, units at ≤10 internal
  count nothing, best strictly-greater score wins, none positive means no
  meteor. Confirmed 12/12 on seeded activations against a board built to
  make each scan prefer a different cluster. Blast: BOTH sides take the
  damage (80 internal record 10, 40 record 11), ≤10-internal units are
  immune (not floored — unlike the tsunami), overkill clamps at 1. The same
  probe confirmed the RNG generator itself: the state read back after each
  activation equalled the derived `next(seed)` exactly, twelve times.
  `engine/co.py meteor_*`, `engine/rng.py`,
  `tests/fixtures/meteor_probes.json`.
- **The last two vision rules, measured** (DERIVATION 29). Rain costs every
  unit 1 vision, floored at 1 — the written-rain capture reproduces 150/150,
  misses 32 tiles with the rule off, and the floor itself carries 7 tiles
  (the capture's Mechs and Artillery still light their neighbours). A
  wood/reef tile is lit by the AIR unit standing on it — a BCopter written
  onto Wood is lit at distance 3 where the Infantry control stays dark.
  Fixtures `fog_vision_rain/airwood/groundwood.json`; all seven vision rules
  are now load-bearing in the oracle test.

- **Supply, repair and daily fuel burn, whole** (DERIVATION 33; fixtures
  `supply_probes.json`, tables `data/aw1_supply.json`). The turn-start order,
  measured off write-PCs: income (`0x0802416A`), daily burn with crashes
  applied inline (`0x08023978`, remover `0x080243D8`), property
  repair/resupply (walker `0x0802A334`), APC auto-supply (`0x0802A8A4`) —
  which is why a 0-fuel air unit beside an APC dies. Burn is the 20-byte
  per-terrain block at stats `+0x38` (uniform: 0 ground, 2 copters, 5
  planes, 1 naval), skipped for loaded units and on an OWN
  Airport/Port (`0x08282EFE` — the skip also bypasses the crash check);
  dived subs burn a flat 5; Eagle's pool byte `+0x0A` takes 2 off air.
  Fuel 0 after burn removes air AND naval; ground parks. Property service
  is owner match + the per-terrain block at stats `+0x24` (ground
  City/HQ/Base, air Airport, naval Port): free refuel (`0x08029CEC`) and
  re-ammo (`0x08029C1C`) to the stats maxima, then 2 bars of repair via
  `0x08029D9C` — per bar `(cost/10) × header[+0x2D]/100` funds (Kanbei
  120), hp +10 capped at 100, and EVERY exit snaps internal HP up to
  `bars×10` (91–99 becomes a free exact 100; broke pays what it can, snaps,
  stops). Charging is off while settings byte `0x03004357` is nonzero (the
  VS fixture's default). The APC: supplier table `0x08282EE5`, menu item
  offered only when an adjacent same-army unit is under a stats max
  (`0x0802588C`), executor fills ALL adjacent friendlies fuel+ammo free and
  spends the action; turn-start auto-supply does the same without one, and
  never reaches the transport's own cargo (that is the Cruiser's walker
  branch, type 0x16 at `0x0802A8F2`).

- **Joining, whole** (DERIVATION 34; fixture `join_probes.json`). Pair rule
  at `0x08024664`: same type, same army, neither carrying, TARGET under 10
  display bars (the mover's HP is not read). Merge at `0x0802649C`: display
  bars add, survivor HP = `bars×10` (45+45 → 100), bars over 10 refunded at
  `cost/10 × header[+0x2C]/100` per bar (2100 for a 7+6 Tank, Kanbei 2520),
  fuel = mover's post-move fuel + target's and ammo = sum, both capped at
  the stats maxima, capture progress taken from the TARGET, the mover's
  record survives acted and the target's type byte is zeroed
  (`0x08026710`). A full-HP target refuses the destination outright.

## Assumed — these are the ones that will bite

Nothing, currently. Every assumption this file has carried is either in
Established above or in the Retired ledger below, each killed by a
measurement or a read with its account named. The section stays, because the
next composed feature will refill it — that is what happened with A15 and
A16, both born the day action enumeration was written.

## Unknown — not modelled

- **The tile→unit index.** The two per-tile unit-slot arrays at map `+0x12`
  and map `+0x51A` are IDENTICAL COPIES, not air/ground layers — dumped
  matching over a whole fixture board and again after a real move updated
  both (DERIVATION 33; different readers pick different copies: the
  auto-supply scan `+0x12`, the Supply menu need-scan `+0x51A`). Nothing
  writes them from the harness yet, so the rule stands: type/hp/ammo/capture
  writes are proven transparent, position writes are NOT for selecting your
  own unit — drive real moves instead. (History: the stay-position probe
  read `acted 0` with the record written onto the city; a teleported ENEMY
  is targetable and written terrain carries real defence, so the index gates
  selection, not combat.) Kill by: writing both copies alongside `x,y` and
  re-running the stay-position probe.
- **The identical visibility copy at `0x02017B42`.** Dumped only as a
  cross-check; no known purpose.
- **When the unit record updates during a move.** The pre-move tile is what a
  fixture holds (Established); whether the record flips at confirmation or
  later in the animation is unread. Nothing shipped depends on it — only a
  live reader polling mid-move would care.
- **What sets the fixed-luck byte.** Settings `+0x06` (`0x03004316`)
  nonzero makes every strike roll exactly +5 with no RNG draw at all —
  read at `0x08023330`, measured on two battles (DERIVATION 32). Which
  setup option or mode sets it is unread; every capture reads 0. Until
  then the reader should surface a nonzero value loudly rather than
  predict with the wrong luck model.
- **What sets the free-repair byte.** Settings `+0x47` (`0x03004357`)
  nonzero makes property repairs charge nothing — the walker hands `1 −
  this` to `0x08029D9C` as its charge flag (measured both ways, fixture
  rows R1/R2). The parked VS fixture reads 1; which setup option or mode
  writes it is unread. The reader now dumps it, and `actions.py` assumes
  charging out loud when a dump predates the field.
- **The fuel byte's bit 7.** Not fuel: every fuel writer masks it off and
  preserves it (`0x08023A68`, `0x08029D6E`), and a written bit 7 rides
  through a full turn untouched (fixture row B4). What, if anything, sets
  or reads it has not been found.
- **Unloading, production** — the turn mechanics `engine/actions.py`
  declines to offer rather than guess; each is named in its docstring with
  the reason. (Supply, repair and fuel burn left this bullet for Established
  via DERIVATION 33, joining via DERIVATION 34.)

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

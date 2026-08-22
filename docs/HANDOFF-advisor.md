# Handoff: building the advisor

For the session that picks this up. The measurement programme is complete:
every unit action (wait, attack, capture, load, join, drop, supply, trap),
both army actions (build, power), the turn-start economy (burn, crash,
repair, resupply, auto-supply) and the fog ambush are enumerated from
tables read off the ROM and rules measured on the running game
(DERIVATION 1–38). What does not exist yet is an OPINION: nothing in the
repo says which of the enumerated actions to take. That is the advisor.

The repo's rule still applies, and it is the whole design problem here:
**every number so far is a fact the game can be asked to check; a
recommendation is the first number it cannot.** The advisor must keep the
two apart in code, in output, and in tests. Do not try to talk the game
into checking the opinion — the way to honour the rule here is to make
everything the opinion COMPOSES from game-checkable, and to say plainly
that the weights on top are not (§3).

## Established — the inventory you compose from

Everything below has a test, a fixture, and a DERIVATION section. Do not
re-derive; import.

- **Board**: `engine/state.py` — `load(path)` from `harness/mgba_state.lua`
  dumps; `Board` (terrain/owner grids, units, armies, day, active player,
  weather index, fog, the game's own vision array, `repair_free`), `Unit`
  (hp internal 1..100, ammo, fuel, capture, flags incl. `dived`, two cargo
  slots), `Army` (funds, CO id, meter, `power_active/uses/ready`).
- **Facts per unit**: `engine/actions.actions_for(board, unit, ...)` →
  `Action` records of kind attack / capture / join / drop / supply / load /
  wait / trap, each with exposure (`threat.FocusFire` at the ENDING tile,
  worst case in one consistent world), strike/counter envelopes
  (`damage.Outcome`), `turn_start` facts (`supply.TurnStart`: burn, crash,
  repair with the exact funds charge, auto-supply), and per-kind fields
  (`merge`, `supplies`, `drop_tile`, capture progress). `all_actions()`
  for an army's units.
- **Facts per army**: `actions.build_actions(board, player)` (every own
  empty factory's shop, price with the CO value multiplier, affordability,
  the fifty-slot cap, the new unit's exposure) and
  `actions.power_action(board, player)` (`power.Activation`: availability
  against the uses-scaled threshold, what firing does to this board).
- **Threat**: `engine/threat.py` — `focus_fire`, `firing_positions`,
  `hostiles` (fog-aware), the coverage grid `tools/threat_report.py` draws.
- **Damage**: `engine/damage.py` exact envelopes; `engine/rng.py`
  `strike_luck(state, good, bad)` collapses a strike to a POINT given a
  read of `0x03001D30` — available inside the headless loop, not from a
  plain dump.
- **CO**: `engine/co.py` — modifiers, luck, universal pair, capture shift,
  vision, meteor scoring, power meta/threshold/effects.
- **Fog**: `engine/fog.py` — visible tiles/units by the seven measured
  rules, or the game's array when the dump carries it.
- **Pathing**: `reachable` (the game's own fill, oracle-matched),
  `destinations`, `path` (one cheapest route), `trap_tiles`.
- **Rendering**: `tools/action_report.py` (per unit, per army, powers,
  factories, traps) and `tools/threat_report.py` are the current user
  surface; `tools/quote.py` for one matchup.
- **Menu orders, for a driver**: map menu Unit / Intel / [Power] / Save /
  Options / End (End is last; up from the top wraps to it). Unit action
  menu, in table order (`0x0828BB00`): Fire, Capt, Load, Drop, Drop(2nd
  slot), Join, Supply, Wait, Dive, Rise — only the offered items show
  (observed `[Drop, Supply, Wait]`, `[Capt, Wait]`, `[Wait, Dive]`,
  `[Join]`). Shops list in `aw1_unit_stats.json` `shop_order` filtered per
  factory. The drop selector starts on the north tile when free (recorded,
  not a rule).

## What the advisor phase has to decide

1. **Where the line goes.** Facts stay in `engine/actions.py` and friends;
   `engine/sim.py` (§2) is also below the line — it composes facts into a
   next board and is game-checkable like any of them. Above it, the
   opinion layer is a NEW module (`engine/advisor.py` or a package)
   that consumes `Action`s and returns a ranked plan. Nothing in the
   existing modules should grow a "score" field. Every number the opinion
   layer prints must either be a fact it is quoting or be labelled as a
   weight/estimate with its provenance ("heuristic", not "measured").
2. **What a plan is.** One turn: a sequence of (unit, Action) plus builds
   and the power, in an ORDER — order matters (a supply before a move, a
   power before attacks, a join that frees a slot before a build). The
   facts are per-action on the current board; a plan needs the board
   re-evaluated after each committed action. That re-evaluation is a
   FORWARD MODEL, and four fifths of it is already written as one-off
   hypotheticals inside `actions.py` (`_after_trade`, `_loaded_board`,
   `_merged_board`, `_dropped_board`, `_turn_start_facts`, all
   `dataclasses.replace` on units). Factor them out into `engine/sim.py`
   behind one entry point, `apply(board, action) -> board`, BEFORE
   writing any planner. It is the load-bearing piece of this phase: with
   it, planning, search, weight tuning and self-play all run in Python at
   whatever speed they need; without it the only way to advance a board
   is the emulator, which is the mistake an earlier draft of this handoff
   made. Keep it honest: a plan's second step quotes facts on the board
   its first step leaves behind.
3. **What the emulator is for now.** Three clocks, and only one of them
   can afford the game:
   - *Advice time* — a dump in, a ranked plan out. Milliseconds. The
     emulator is never in this path.
   - *Search and tuning* — comparing two weight sets, or any planner that
     looks past greedy, needs 10^4–10^6 hypothetical boards. The emulator
     is categorically excluded here; this is `sim.py`'s job.
   - *Validation* — offline, rare, minutes are fine. The only place the
     game runs.

   An opinion cannot be measured wrong. `apply()` can, and so can every
   fact it composes — so spend the whole emulator budget certifying
   `apply()`, as a BOUNDED DIFFERENTIAL TEST, not a loop: N parked
   states, ONE action each; dump → `apply()` in Python → drive that one
   action on the game → dump again → diff the after-states field by
   field. A fixed corpus (~40–60 cases: every action kind, both army
   actions, the turn-start economy) run once, and again whenever
   `sim.py` changes. `mesen_drive.lua` is still needed, but only to
   execute a SINGLE Action with read-back verification; nothing ever
   chains it.

   Do NOT build win-rate-against-the-CPU evaluation. The cost is not
   emulation speed (headless Mesen runs well over realtime) but the
   driver: cursor taps, menu animation frames, a read-back and a
   retry-with-B per action, hundreds of actions per game — and resolving
   a 5% difference between two weight sets needs on the order of 200
   games, so the metric's own noise multiplies that by two orders of
   magnitude. AW1's CPU is weak enough that a mediocre planner probably
   saturates it, so it would not discriminate even if it were free. No
   COM fixture is needed; do not ask the user to park one.

   What checks the WEIGHTS instead is advisor-vs-advisor self-play in
   Python, free once `apply()` exists, giving a relative ranking without
   the game's CPU. Write its failure mode down where the planner lives:
   a bug in `apply()` becomes a shared delusion both players believe, and
   self-play will happily converge on exploiting it. That is the whole
   argument for spending the emulator budget on `apply()` — and for
   weighting the differential corpus toward the action kinds the planner
   actually picks.
4. **What the first opinion should be.** Start thin and explicit: a
   one-turn greedy planner whose scoring is a handful of named terms over
   facts (expected damage dealt vs taken from the envelopes, funds value
   of kills and losses via `co.unit_value`, capture progress, exposure at
   the ending tile, repair/resupply gained at turn start) with weights in
   one place and a test that the ranking is invariant under the things it
   should be invariant under (board translation, unit slot renumbering,
   CO-neutral boards giving the same answer as Andy). Do not start with
   search depth; start with a planner whose every preference can be
   traced to a quoted fact. Then two different critics say where it is
   naive: the differential corpus for the facts and boards it composed
   wrongly, Python self-play for the weights.

## Unknowns, each with its designed measurement

- **Income — CLOSED, DERIVATION 39.** `income = rate × owned properties`
  over {City, HQ, Airport, Port, Base}, HQ included, rate = u32 at
  `0x03004338` (settings +0x28). Read off the jump table at `0x08025150`,
  confirmed against `Army.income` on all eleven full-board fixtures at two
  rate settings. `engine/economy.py`. Campaign uses the same path — only
  the cell's value can differ, and `funds_rate()` derives it from the board
  when the dump lacks the cell, so mode never has to be known. What campaign
  writes there is the one open item, and it blocks nothing.
- **Wall clock for one driven action, headless.** Unmeasured, and it is
  the number that sizes the differential corpus — a 3-minute test or a
  3-hour one. Measure it first from a parked state (select, move, confirm,
  menu pick, read back) before committing to a corpus size. Nothing else
  in the plan depends on it.
- **A headless state dumper.** `mgba_state.lua` is an mGBA console script;
  the headless Mesen runs have no equivalent writer of the loader schema
  (only the two fixture dumpers, `mesen_sonja_fix.lua` /
  `mesen_vision_rules.lua`, which skip cargo, armies' power fields, the
  repair-free byte). Port the full dumper to Mesen's API (`emu.read*`,
  the same addresses) as `harness/mesen_state.lua`, and assert it against
  an mGBA dump of the same fixture tile for tile. Without it the
  differential test has no before/after.
- **A driver that executes ONE Action.** The pieces exist across the probe
  scripts: select at a tile (`goto_tile` + A, map mode only), move by
  COUNTED direction taps along `pathing.path()` (the cursor bytes do not
  track in move-select, DERIVATION 29), confirm, pick the menu item by its
  position in the offered list (table order above), Fire's target cursor
  (`mesen_luck.lua`), the drop selector (default north; direction taps
  move among valid tiles), End Turn (`end_turn()` in the supply scripts,
  with the fog card and B-cancels from DERIVATION 38). Build it once as a
  library with verification after every step (read the record back; the
  position write PCs are `0x08026076/7C`) and a retry-with-B on failure.
  This is the expensive part of the phase, and the reason nothing chains
  it: one Action per parked state, then back to Python.
- **Whether the drawn route matters.** `trap_tiles` assumes the cheapest
  approach; the game walks the arrow the player drew. A driver that taps
  the path tile by tile draws exactly `pathing.path()`, so in the
  differential test this is not a divergence — state it where the driver
  lives. `sim.py` must make the same assumption explicitly.
- **Unit record `+9..+11`.** Still unread (`+11` reads 0 foot / 4 vehicle
  in old captures; spawn writes 1 then 0 at `0x0802423C/46`). Not needed
  for advice; listed so nobody rediscovers it.

## Housekeeping owed before or alongside

- `README.md` "Known gaps" is stale: the CO-power scale, the damage path's
  CO source, the action layer's legality, Sonja, and unit record `+8` are
  all closed (DERIVATION 24–38); rewrite the list from `ASSUMPTIONS.md`'s
  Unknown section, which is current.
- `README.md` milestone 4 still says threat projection is "not yet put in
  front of the game". The supply/join/drop/build/power drives exercised
  `focus_fire` only through the action layer's tests; a direct oracle
  (threat's reachability half via `path_diff` on enemy units) is still
  unrun and cheap.
- `tools/battle_plan.py`, `plan_sim.py`, `shopping_list.py` are milestone-2
  measurement planners, not advisors; say so in the README layout or move
  them under a `tools/derivation/` note so the word "plan" stops
  misleading.

## Traps this repo has already paid for (the short list)

- RAM unit type bytes are 1-BASED (type 7 is the APC, 6 the Recon, 2 the
  Mech); check the type byte against the table before building on a slot.
- Position writes do not relocate units for selection or for any walker
  that reads the tile index (supply, drop, trap, meteor scoring) — drive
  real moves; type/hp/ammo/fuel/terrain/owner/fog/weather writes are
  transparent.
- The map cursor bytes freeze in move-select; under fog the turn-start
  card waits for a press and the cursor then rests ON a unit, so stray A
  taps select it — cancel with B after every turn change.
- Exec hooks on a phase object's entry fire every frame while it waits;
  to override a value that income refills, hook the consumer (the repair
  routine's entry), not the walker.
- Mesen must be launched from the PowerShell tool with
  `Start-Process ... -PassThru -Wait`; a bash→powershell hop mangles the
  quoted ROM path and Mesen exits −1 at once. `pytest | grep passed` is
  not a green check; test the exit code.
- Savestates reproduce the RNG; fixtures are `.mss` the user parks; do not
  touch `Documents/Mesen2/Saves`.

## Deliverables, repo-style

- `engine/sim.py` first, before any planner: `apply(board, action) ->
  board`, the forward model factored out of `actions.py`'s one-off
  hypotheticals, plus the turn-start economy.
- `engine/advisor.py` (opinion layer, weights in one place, every
  preference traceable to a quoted fact) and `tools/advise.py` (the
  user-facing plan, facts and opinions visibly separate).
- `harness/mesen_state.lua` (headless dumper, asserted against mGBA) and
  `harness/mesen_drive.lua` (single-Action executor with read-back
  verification), then `tools/sim_diff.py` over a checked-in corpus of
  parked states: dump → `apply()` → drive one action → dump → diff,
  logging every field the game contradicted.
- DERIVATION 39+ for anything measured on the way (income first); a new
  `docs/ADVISOR.md` for the opinion layer's own rules — the line, the
  weights, what the differential corpus has and has not caught, and the
  shared-delusion caveat on Python self-play.
- Tests: invariance and scenario tests for the planner; the `sim_diff`
  corpus and its result log as checked-in fixtures once it runs clean.
- Single-sentence-finding commit subjects, straight to main, push.

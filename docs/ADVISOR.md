# The advisor: the opinion layer's own rules

Everything below `engine/actions.py` and `engine/sim.py` is a fact the game
can be asked to check, and has been. `engine/advisor.py` is the first module
that is not. This file is where its rules live: where the line is, what the
weights are and what each one reads, what is tested, what is naive, and the
caveat anyone tuning it by self-play has to carry.

## The line

- **Facts** are the `Action` records from `actions.all_actions`,
  `build_actions` and `power_action`, and the boards `sim.apply` produces.
  Each carries numbers the game has been shown to agree with: strike and
  counter envelopes, next turn's focus fire, capture points, the morning's
  burn and repair and its charge, a join's refund, a shop's prices.
- **The opinion** is `advisor.WEIGHTS`, one table, every entry labelled
  heuristic, and the `Term` arithmetic that multiplies a weight by a
  quantity read off a fact. A `Term` carries the fact it read as a
  sentence, so a plan can be audited line by line: the weight is the only
  number on the line that was not measured.
- Nothing below the line grows a score field; nothing above it reads RAM.
  `tools/advise.py` prints `w (heuristic) x quantity <- fact` on every term
  line so the two kinds of number never share a column.

## The loop

Greedy with sequential commit, one turn:

1. enumerate every action of every unit that can still act, every
   *affordable* build at every empty own factory, and the CO power when the
   meter is there -- on the board as it now stands;
2. score each as a sum of named terms, all in funds so they add;
3. commit the best; advance the board with `sim.apply(luck="min")`, the same
   worst-case world the action layer scores exposure in; re-enumerate with
   the units that remain;
4. stop when no unit can act and no build or power scores above zero. A
   unit that can act always gets an action (a wait in place is one); army
   actions are optional.

Ties break on the filing order -- kind, the actor's tile, the ending tile,
the target's tile, the build type -- and never on slot numbers, which is what
makes the invariance tests below hold.

## The weights, and what each reads

| weight | value | multiplies | read from |
|---|---|---|---|
| `damage_dealt` | 1.0 | funds of enemy bars taken at the worst roll | `Outcome.min_damage` / `max_remaining_hp`, `co.unit_value` |
| `kill` | 0.5 | the target's price, on a guaranteed kill | `Outcome.guaranteed_kill` |
| `damage_taken` | 1.0 | funds of own bars lost: the counter at its worst, then next turn's focus fire on the ending tile | `Outcome.max_damage`, `FocusFire.worst_remaining` |
| `army_share` | 1.0 | `damage_taken` x (1 + this x the unit's share of the army's value) | `unit_worth` over the board |
| `loss` | 0.5 | the actor's price when the worst case kills it | `FocusFire.lethal`, a counter's `guaranteed_kill` |
| `capture` | 1.0 | the property's worth x points gained / 20; points abandoned by stepping off count against | `Action.progress_after`, `Unit.capture`, A15 |
| `capture_horizon` | 6 | days of income a property is worth | `economy.funds_rate` |
| `enemy_property` | 2.0 | a property taken from an enemy is worth this x | `Board.owner` |
| `win` | 1,000,000 | an HQ that falls this turn | `Action.captures_now`, terrain id 8 |
| `objective_pull` | 40 | funds per movement point closer to the objective | `advisor.distance_field` over `Board.move_cost` |
| `repair` | 1.0 | funds of bars the morning repairs | `TurnStart.hp_after` |
| `repair_spend` | 0.5 | the repair's charge | `TurnStart.repair_spent` |
| `resupply` | 0.3 | a unit's price x the fraction of its fuel and ammo restored | `TurnStart`, `SupplyFill`, `supply.resupply_caps` |
| `crash` | 1.0 | the unit's remaining value when the morning removes it | `TurnStart.crashes` |
| `refund` | 1.0 | a join's refund | `Merge.refund` |
| `build_matchup` | 0.5 | average over visible enemies of (best base damage / 100) x their price | `damage.select_weapon`, `threat.hostiles` |
| `build_capture` | 1.0 | a property's worth x min(1, unowned / (own foot + 1)), foot units | the terrain and owner grids |
| `build_spend` | 0.3 | the price, against | `Action.cost` |
| `build_bias` | 1.0 | `BUILD_BIAS[type]`, the fixed early table | -- |
| `power_refresh` | 0.3 | a unit's price per unit Eagle refreshes | `Activation.refreshes` |
| `power_block` | 0.5 | the army's value x (attack% - 100) / 100 | `Activation.universal` |

`BUILD_BIAS` is the one place in the module that names a unit type: Infantry
+500, APC -1500, TCopter -2000, Lander -4000. Transports are held back
because the planner cannot yet plan a load-and-drop, so their matchup value
is zero and they would otherwise never be bought or always be.

Objectives for the pull: foot units head for the nearest property not
theirs; armed units for the nearest visible enemy; transports for the
enemy's properties; each falls back down that list when its own set is
empty. The distance is movement points along the weather's table, units
ignored.

## What is tested (`tests/test_advisor.py`)

- **Arithmetic**: every term's weight is the table's; terms sum to the
  step's score and steps to the plan's; every term quotes a fact; the
  rendering labels every weight heuristic; the input board is untouched and
  each step's `board_before` is the previous step's `board_after`.
- **Invariance**: the same scene translated inside a border of the
  out-of-bounds terrain plans the same plan shifted; slots permuted within
  each army's block plan the same plan; a board whose COs are unknown --
  which every fact module quotes as neutral -- plans term for term as Andy,
  whose record is neutral everywhere the facts look.
- **Scenarios**, one preference each: a guaranteed kill over a wait; the one
  tile an enemy cannot reach over the exposed ones; a capture in progress
  kept, and leaving it charged; the HQ taken when it can be; the second
  strike quoting the HP the first left; every unit that can act gets one
  action; a build needs funds and an empty factory and is skipped when it
  scores nothing; Andy's power fires first when it heals and the units then
  act on the healed board; the objective pull moves an idle unit; the
  morning's repair and a crash both score; a trap is never a candidate.

None of this says the plans are good. It says the planner is the function
its docstring describes.

## Where it is naive

Written down so nobody tunes a weight to fix a shape problem.

- **Exposure is summed per unit.** Each action is charged the focus fire on
  its own ending tile, so two units parked in one enemy's reach are each
  charged for that enemy. Over a plan this overcounts, in the safe
  direction; a plan that spreads out looks better than it is.
- **Greedy.** The best first action, then the best second, never the pair
  that is best together. A softening hit that only pays off through the
  kill it sets up is committed only if it scores best alone. Sequential
  commit does let the second attacker see the softened target -- that much
  composes -- but nothing looks ahead to prefer it.
- **One turn, worst case.** Nothing past the enemy's reply, and the reply
  is the action layer's worst case (every enemy converges, none is weakened
  by counters), not a modelled opponent. ROADMAP steps 3 and 4 replace it.
- **Builds see one turn.** The matchup term reads the enemy's current
  composition, the capture term counts properties, and nothing saves for
  next turn; `build_spend` at 0.3 is the whole notion of money having a
  future.
- **Transports.** A load scores only the ride's lethality and the pull; a
  drop scores the passenger where it lands. Nothing plans "load, drive,
  drop" as one thing across turns.
- **Powers.** Olaf's snow, Sami's roads, Grit's range score nothing (the
  term says so). Sturm's meteor scores its worst strategy for us because the
  RNG draw that picks it is not in a dump.
- **Traps are never chosen**, which is right, but a trap's stop tile is
  scored as the wait it also is -- the planner does not know a route that
  would walk into a hidden unit on the way to a different tile. The action
  layer's fill is the game's, so no destination it offers is unreachable;
  the route drawn is `pathing.path`, the cheapest, and whether the game's
  arrow would differ is an assumption `sim.py` shares (HANDOFF).

## Running time

The action layer re-enumerates every remaining unit after each commit, so a
turn costs about (units^2 / 2) unit enumerations. On the fogged 15x10
fixture that is tens of seconds; on the hand-built test boards it is
instant. The emulator is never in this path.

## The differential corpus

`sim.apply` has six stated assumptions (ASSUMPTIONS, "What engine/sim.py
states") and no differential test yet -- ROADMAP step 2. Until it runs, a
plan's second step stands on a board the game has not been shown to agree
with. What the corpus has caught: nothing yet, because it has not run. This
paragraph is to be rewritten when it has, with the fields the game
contradicted and which action kinds the planner actually picks (weight the
corpus toward those).

## The shared-delusion caveat

Tuning these weights by advisor-vs-advisor self-play in Python is the plan
(ROADMAP step 5). Its failure mode: a bug in `sim.apply` is a belief both
players hold, and self-play converges on exploiting it, cheerfully and
invisibly. A weight set that wins self-play on a wrong model is tuned to the
wrong game. That is why the emulator budget goes to certifying `apply()`
one action at a time and never to win-rate evaluation, and why the corpus is
to be weighted toward the action kinds the planner picks: those are the
ones a shared delusion would live in.

## Reading a plan

```
python tools/advise.py state.json
```

One line per step with its score, then the terms:

```
 2. Infantry #2 (1,1) CAPTURE City here   [+2444]
      damage_taken        -556  = 1 (heuristic) x -556  <- next turn 52-58 from 1 attacker (5 bars at worst) x 100/bar x 1.11 army share
      capture            +3000  = 1 (heuristic) x +3000  <- City at (1,1): 10 -> 20/20, falls THIS TURN; the property is worth 6000 (6 days of income)
      next best: Tank #1 (0,0) -> (0,1) WAIT on Plain [+0]
```

The number after `<-` is the fact; the number after `=` is the weight;
`next best` is the runner-up on that board, so a close call is visible.
`--weight name=value` overrides a weight for one run; `--luck max` plans in
the kindest world instead of the worst; `--board` prints the board the plan
leaves behind.

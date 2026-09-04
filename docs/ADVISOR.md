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

## The reply

The loop scores every action against the action layer's worst case: every
enemy that can reach the ending tile does, and none is weakened on the way.
ROADMAP step 4 puts a modelled opponent in its place. With `reply` set (the
default in `tools/advise.py`), the loop becomes the proposer and the reply
the arbiter:

1. **propose**: the greedy plan, and up to `branches` (default 3) variants.
   A variant is made at a close call -- a step whose winner's own actor had
   a next-best action scoring near it -- by committing that alternative and
   re-planning the rest of the turn greedily. The alternative is the *same
   actor's* next-best, not the overall runner-up: the runner-up is usually
   another unit's best move, and forcing it first only reorders the plan
   onto the same board. Close calls where the worst case had a say (a
   damage, kill, loss or capture term differs between winner and
   alternative) are tried first, then the rest by margin.
2. **reply**: after each proposal, End Turn (`sim.end_turn`), the
   opponent's whole turn, End Turn again -- so the board scored is the one
   at *our* next turn start, both sides having taken one income. The
   opponent is played by `engine/cpu_ai.predict`, the game's own AI ported
   routine by routine, when the opponent is the CPU (`reply="cpu"`, with a
   `cpu_ai.Context` from the dump); by this planner, one ply deep, when it
   is not (`reply="planner"`). Its battles roll at `reply_luck` ("max":
   high against us) under the planner model; under the CPU model the port
   draws from the dump's RNG state as the game would. Where the port meets
   a branch it has not read (it raises `NotImplementedError` naming the
   routine) the planner stands in for that turn and the reply says so.
   Two proposals that leave the same board share one reply.
3. **evaluate**: `advisor.evaluate` scores that board from our side, in
   funds, as named terms -- the same `weight x quantity <- fact` lines as a
   step's: material (our units' value less the enemy's), treasury, income
   over the horizon, captures in hand, and against the start board an HQ
   that changed hands or a side that lost its last unit, at `win`.
4. **choose** the proposal whose reply scores best. Ties keep the greedy
   plan. The plan carries the reply (what the opponent did, line by line;
   the board; the terms), every proposal with its reply score, and the
   start board evaluated the same way, so the reply's score reads as a
   change.

What this fixes: a proposal is charged for what the modelled opponent
*does*, not for every enemy converging at once, and the opponent's captures
and builds count -- the worst case cannot see an enemy Infantry finishing
a city. What it does not: see "Where it is naive".

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
| `material` | 1.0 | (reply) our units' value less the enemy's, on the board after the reply | `unit_worth` over the board |
| `treasury` | 0.7 | (reply) our funds less the enemy's | `Army.funds` |
| `income` | 1.0 | (reply) (our income - the enemy's) x `capture_horizon` | `economy.income` |

The reply's evaluation also reuses `capture` (captures in hand, ours less
theirs, each as worth x points / 20), `capture_horizon` and `win` (an HQ
that changed hands against the start board; a side that had units and has
none -- the rout, stated in ASSUMPTIONS).

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

- **The reply**: without `reply` the plan is the greedy one and carries no
  reply; the model must be named and the CPU model needs its context; the
  board scored is the one at our next turn start (the next day, both
  sides' acted bits clear, our finished capture paying); the evaluation's
  terms are the table's weights times quoted facts and sum to the score,
  the rendering labels them heuristic, and the start board's evaluation is
  the baseline; the HQ and the rout score `win` in both directions; a
  variant commits the same actor's next-best action; the CPU port plays
  the reply on the step 3 fixture (P2 as the CPU) without writing into the
  caller's context, and the planner stands in, saying why, when the port
  meets a sub-phase it has not read; and one scenario -- an Infantry
  between two neutral cities with an enemy Infantry beside the right one
  -- where the greedy plan takes the safe left city and the reply, seeing
  the enemy then start on the right one, chooses to stand on the right
  city instead, against the proposal's own score.

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
- **One turn, worst case, in the proposals.** Each step is still scored
  against the action layer's worst case; the modelled reply only arbitrates
  between the greedy plan and one alternative per close call. A plan the
  worst case ranks fourth at some step is never proposed, so the reply
  cannot choose it. The variants are the same actor's next-best action;
  "do nothing with this unit" is proposed only when a wait in place is that
  next-best.
- **Two plies.** The reply is the opponent's next turn and nothing after
  it: a capture the reply starts counts as points in hand, a unit it leaves
  exposed to us counts as material, and our own next turn is never
  planned. The evaluation is static -- material at price, funds at 0.7 of
  it, income over the horizon -- and every one of those numbers is a
  weight.
- **The reply is the model's.** Under the CPU model it is exactly the
  port's turn: right wherever the port has been traced (`docs/DERIVATION.md`
  45, 47), and the planner's own greed wherever it has not, which the
  reply names. Its luck draws start from the dump's RNG state, which
  `sim.apply` does not advance -- so after a battle of ours the port's
  draws are those of a turn in which we fought none. Under the planner
  model the opponent is this same greedy loop, one ply, with its battles
  rolled high against us: a pessimistic stand-in, not a prediction. A
  human opponent is neither.
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

The reply multiplies that: each variant re-plans the rest of the turn from
its close call, and each proposal is followed by the opponent's turn -- the
CPU port plays one in under a second, the planner model in about the cost
of one more plan. On the sixteen-unit step 3 fixture a plan with the CPU
reply and three variants takes about fifteen seconds; with the planner
reply about a minute. `--branches 0` puts only the greedy plan to the
reply; `--reply none` is the step 1 planner.

## The differential corpus

`sim.apply` has been put in front of the game: 63 parked-state drives,
every action kind, both army actions and the turn-start economy
(`tests/fixtures/sim_diff/`, DERIVATION 43, `tools/sim_diff.py`). The
first run contradicted the model on nine rows, and every one was a bug in
the composition rather than in a measured module: the acted bits the game
clears at End Turn (and the passenger's it keeps), the income field a
capture raises at once, the RNG draw an unanswerable shot takes, Sonja's
reducer parameters, a written rate cell the payer ignores, and empty army
records counted as players. Fixed, the corpus replays at 63 of 63 in
`tests/test_sim_diff.py`, so a plan's second step now stands on a board
the game has agreed with for every kind of first step. Two of the six
stated assumptions are still stated (a Cruiser's passenger resupply, the
HQ capture ending the match), and the corpus is weighted toward what the
greedy planner picks -- waits, attacks, captures, builds, End Turn -- with
one or two rows for the rest.

## The shared-delusion caveat

Tuning these weights in Python -- against the CPU port first, planner
against planner where the port cannot play and as a check (ROADMAP step 5)
-- has one failure mode in two forms. A bug in `sim.apply` is a belief both
players hold, and tuning converges on exploiting it, cheerfully and
invisibly. A bug in the port is a habit the tuner will learn to exploit that
the real CPU does not have. A weight set that wins on a wrong model is
tuned to the wrong game. That is why the emulator budget goes to certifying
`apply()` one action at a time and to tracing the CPU's branches, never to
win-rate evaluation, and why the corpus is to be weighted toward the action
kinds the planner picks: those are the ones a shared delusion would live in.

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

After End Turn comes the reply: who modelled it, what the opponent did,
the board at our next turn start as terms, and every proposal with its
reply score:

```
 9. END TURN
    then P2's reply, modelled by the CPU port (engine/cpu_ai):
      P2: Mech #68 (11,3) -> (11,2) CAPTURE
      P2: Tank #71 (9,7) -> (8,6) FIRE at Mech #3 (9,6)
    the board at P1's next turn start, from P1's side   [+11050, from +6650 now]
      material           +4400  = 1 (heuristic) x +4400  <- P1's units are worth 28800, the enemy's 24400
      treasury           +6650  = 0.7 (heuristic) x +9500  <- funds 38000 against the enemy's 28500
      income                +0  = 1 (heuristic) x +0  <- income 9500 a day against the enemy's 9500, over 6 days
      capture               +0  = 1 (heuristic) x +0  <- captures in hand: P1 Infantry #2 at (5,1) 10/20 (+28500); P2 Mech #68 at (11,2) 10/20 (-28500)
    proposals, by the reply's score:
         +11050  the greedy plan (proposal +30555)   [chosen]
          +8750  step 2: Mech #3 (7,6) -> (8,7) FIRE at Tank #71 (9,7) instead (proposal +28395)
         -17450  step 1: Infantry #2 (6,3) -> (5,1) WAIT on City instead (proposal +2055)
```

`--reply` picks the model: `auto` (default) uses the CPU port when the
dump says the opponent is CPU-controlled (army `+0x1B` = 2) or, when the
dump does not say, whenever a CPU context can be built from it, and the
planner otherwise; `cpu`, `planner` and `none` force one. `--branches N`
is how many variants are proposed.

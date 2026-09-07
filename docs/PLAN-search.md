# Plan: the lean enumerator and the search loop on Field Training 2

The pivot, decided 2026-09-07: the hand-weighted planner is replaced by
search in the port, and later by a network trained on what the search
finds. The reason is horizon, not weights. The greedy planner scores one
turn plus the modelled reply, so a three-day approach past two Tanks has
no value until its last day, and every weight set was a substitute for
the lookahead it lacks. Field Training 3 took the HQ on day 11 in the
port and day 14 in the game under the best set found; par is 9.

Two pieces, in this order, each checked before the next starts:

1. a lean enumerator -- the legal actions and nothing else, because the
   search evaluates boards by playing them out, not by quoting facts;
2. a search over whole turns against the port, scored by the rank
   total of the finished game, run on Field Training 2 first because it
   is small enough to be solved outright, which tests the pipeline and
   not the search.

Then a profile, and only then the two decisions this plan does not make:
a compiled rules layer, and the network. Both are gated on what the
profile of the running search says, not on what it might say.

## What the numbers are today

Measured 2026-09-07 on one core, a random-policy game against the port
(`tools/bench_port.py`, checked in with this plan):

| state | game | decisions | enumeration per decision | CPU turn |
|---|---|---|---|---|
| ft2 | 0.44 s, 21 days | 64 | 5.7 ms | 3 ms |
| ft3 | 1.55 s, 21 days | 102 | 12.7 ms | 12 ms |
| m01 | 3.84 s, 6 days | 47 | 66 ms | 122 ms |

`sim.apply` is free at this scale (under 0.05 ms). Enumeration is the
cost, and a profile of `actions.all_actions` on ft3 puts about 60% of it
in `threat.focus_fire` / `threats_to` (the exposure facts the hand
planner quoted), another share in `_turn_start_facts` and the `_after`
hypotheticals, and about a tenth in pathing. None of the first three is
needed to know whether an action is legal.

## Part 1: the lean enumerator

**What it is.** `actions.legal_actions(board, player)` returns the same
`{slot: [Action]}` as `all_actions`, with the same kinds, tiles, targets,
drop tiles, move costs and order, and with `exposure`, `turn_start` and
`counter` left `None`. `build_actions` and `power_action` get the same
treatment (the build's exposure at lines ~774-783 is the same
`focus_fire` call). `sim.apply` recomputes the battle from the board and
reads none of the three (`_apply_attack` calls `battle` afresh), so a
lean Action applies exactly as a full one does.

**How.** One enumerator, two levels of resolution: a `facts: bool = True`
parameter on `actions_for`, `build_actions` and `power_action`, with
`legal_actions` and `all_actions` as the two thin wrappers. Not a second
copy of the enumerator: the legality rules (the load exception, the join
pass-through set, drop tiles, fog traps and concealment traps, the dive
gates, the fire-from set and the range check) must not drift between two
functions, and a shared body with the fact calls skipped keeps them one.
What `facts=False` skips: every `focus_fire`, every `_turn_start_facts`,
every `_after` whose only consumer was one of those, and the
`counterattack` on attacks. What it keeps: `damage.resolve` on attacks,
because a strike that resolves to nothing is not an action.

**The test.** For every state in `tools/sim_diff.py STATES` and for each
player in it, the lean and the full enumeration agree as a list of
`(kind, unit slot, tile, target slot, drop tile, move cost)`, in order.
That is the whole correctness claim; it makes the lean enumerator as
trusted as the full one, which the fixtures already check.

**The number to hit.** Under 2 ms per decision on ft3 (from 12.7 ms in
the game loop, 6.7 ms on the parked board), measured by
`tools/bench_port.py --lean`. If pathing turns out to dominate after
that, `pathing.destinations` is the next thing to look at, not before.

## Part 2: the search loop on Field Training 2

**The state.** `ft2` is the parked day-2 board of map 117: four Infantry
against two Mechs, no funds, no fog, no properties to build from, par 3.
Each Infantry has 16 to 20 legal actions. The existing planner already
makes a 999 of it (`data/mission_weights.json`, fixture `ft2b`), so the
search's job on this state is to reproduce that from no weights at all,
reproducibly, and to leave behind the pipeline the next missions use.
Field Training 3 (par 9, best so far day 11 in the port) is the first
state where the search has something to prove.

**The game frame.** `tools/sparring.py` already plays one side against
the port with the debrief's counters (units fielded, units lost, joins
merged away, kills credited on the planner's own day) and scores a win
with `rank.score` at the map's par. The search reuses that frame through
one hook: `spar(..., turn_policy=fn)`, where `fn(board, ctx) -> [Action]`
returns the planner's whole turn (ending in End Turn). The default is
the greedy planner, as now. The search is a `turn_policy`. Nothing else
in `spar` changes, so the sparring tests, the abort dumps and the rank
arithmetic stay exactly as they are.

**The objective.** The rank total of the finished game, 0 for a loss or
for the day cap, and nothing per turn. Speed, Power and Technique are
what the debrief computes, so this is the score the user reads in the
game. No shaping term: a per-turn reward is a weight under another
name, and this plan exists to stop writing those.

**The search.** Beam search over whole turns, days deep, with rollouts
to the end of the game as the leaf value:

- *Turn candidates.* A turn is a sequence of legal actions, one per unit
  in some order, then End Turn. The full product is too large (about
  20^4 on ft2 alone), so candidates are sampled: `N` sequences per
  turn, each built by taking the units in a random order and a random
  legal action for each from the board as the earlier actions left it
  (re-enumerated after each, since a move changes what the next unit
  can do), plus the greedy planner's turn as one seed candidate so the
  search starts no worse than the planner. Candidates whose end boards
  hash equal (units, owners, capture points, day) are one candidate.
- *Leaf value.* Each candidate is played out: the port's reply
  (`cpu_ai.predict`, the sparring frame's own call), then a default
  policy for our side to the end or the day cap, `M` times with the
  strikes rolled, and the value is the mean rank total over the `M`
  games (the minimum is recorded beside it). The default policy is
  fixed and dumb by design: attack if the strike kills, else capture if
  standing on a capturable, else the legal action that ends nearest
  the nearest enemy (or the enemy HQ on an HQ map), ties by the
  enumeration's order. It has no weights and it is not the objective;
  it is what makes a rollout finish in a bounded number of turns, which
  random play does not (the bench's random games ran to the 21-day cap).
- *Beam.* Keep the best `B` candidates by leaf value, apply each to its
  board with rolled strikes, take the port's reply, and repeat one day
  deeper up to depth `D` (or the game's end). The turn actually chosen
  is the root candidate that led to the best line found. Then the
  frame applies it on the real board, the port replies, and the search
  runs again from the new board (the line is not replayed blind: the
  rolls on the real board may differ from the rolls that made the
  line).
- *Luck.* Rolled everywhere in the search, never worst-case: a synthetic
  `random.Random` seeded from the run, the same device `--roll-strikes`
  uses. This samples luck; it does not model the game's RNG, which stays
  outside per the decision of 2026-09-05. A policy chosen by its mean
  over rolled games is the one the real game's rolls will find
  prepared.
- *Reproducibility.* One `--seed` fixes the candidate sampling and the
  rolls; the same seed on the same board gives the same game. The port
  is deterministic given `board.rng`.

**Budget, to be measured rather than believed.** On ft2 with the lean
enumerator: `N=200`, `M=4`, a rollout of about 15 turns at roughly 4
decisions and one CPU turn each is on the order of 200 x 4 x 15 x
(4 x 2 ms + 3 ms), about two minutes per searched day on one core, and
`multiprocessing` over candidates divides that by the core count. If
that is wrong by a factor of ten in either direction the plan does not
change; the numbers `N`, `M`, `B`, `D` do, and they are flags.

**The tools.** `tools/search.py` with two entry points:

- `play STATE --seed --days --samples N --rollouts M --beam B --depth D
  --out DIR`: the whole game in the port from a parked state; writes
  each turn's chosen actions (as `advisor.describe_action` prints them,
  the same lines `advise.py` shows) with the board before it, the
  port's reply, and the final rank, into `DIR`, plus a one-page
  `game.txt` a person can play from.
- `advise STATE.json ...`: one turn's search from a dump of the user's
  running game, printed as moves. This is the in-game verification
  path: dump, search, play the turn, dump again. A line found by
  `play` is only good while the game follows it, and the user's game
  will not once a strike rolls differently, so `advise` re-searches
  from the real board each turn, which is what the frame does too.

What `play` writes is also the first training data for the network step
that follows this plan: for each decision, the board, the legal actions,
the action taken, and the game's final rank. It is written from the first
run so that step starts with data and not with a data format.

**The tests.** `tests/test_search.py`:

- on a hand-built two-unit board the search picks the killing attack
  and the game ends on the first turn (the objective and the frame's
  hook, end to end);
- the same seed on the same board gives the same game (reproducibility);
- the chosen turn applies on the real board (a candidate built by
  re-enumeration after each action never proposes a move the board
  refuses);
- the default policy finishes a game from ft2 in under the day cap
  (it is a rollout policy, so it must terminate).

The port's fidelity is untouched by all of this: no engine semantics
change, and the acceptance fixtures keep their meaning.

**Done on ft2 means:** `play` from the parked state reaches a 999 in the
port (a rout on or before day 3 with no unit lost), with a seed, in the
time the budget above predicts within a factor of a few; and the user
plays the printed turns in the game and the debrief reads the same. Then
ft3 is run the same way and its port rank and its game rank are written
down next to day 11 and day 14, whatever they are.

## Part 3: what the profile decides

After ft2 and ft3, `cProfile` on one searched day, and two gates:

- **Compiled rules layer.** Only if pathing, enumeration and `sim.apply`
  together are the wall after the lean enumerator, and then only those
  three, behind Python bindings, checked by the same sweep fixtures and
  the equivalence test above. `engine/cpu_ai.py` stays Python: one call
  per five to ten decisions, still being read, with its own trace
  fixtures.
- **The network.** Only if search alone stops reaching the A within the
  budget. Its shape is fixed now so the data written above fits it: a
  value over boards (replacing the leaf rollout) or a scorer over the
  enumerated actions (guiding the candidate sampling), trained on the
  search's own games, then searched with again. Input is the player's
  view under fog, never the full board.

## Out of scope here

Fog (ft2 and ft3 have none; under fog the search plans on
`fog.remember`'s board as the sparring frame already does, and that is a
later mission's problem); building (no funds on Field Training; the
enumerator's build actions join the candidate sampling wherever a base
and funds exist); the port's open gaps (Field Training 11 days 1-2, 12's
scripted days, mission one's known days, the unread naval modes), which
the search will exploit if they are wrong and which the user's turn
dumps keep finding.

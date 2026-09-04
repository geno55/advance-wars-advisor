# Roadmap: from the first opinion to beating the campaign

The target is a planner good enough to beat the built-in AI on every
campaign mission. The repo's rule still holds above the opinion line: the
facts are measured, the opinion is the first number the game cannot check by
play, and so the plan below is built so that every step is checked by one of
two critics -- the differential test for the facts and boards a plan
composes, and an acceptance harness that plays the campaign, because "beats
every mission" is a claim only the campaign can verify.

Three decisions, made once so they do not get re-argued:

- **The CPU port is the sparring partner; self-play is the fallback.**
  This decision was first written the other way round -- "self-play is the
  core" -- before step 3 succeeded. Once the game's AI plays a turn in
  Python in under a second (`engine/cpu_ai`), tuning against it is as
  cheap as planner-vs-planner and is the actual objective: the campaign is
  won by exploiting this particular AI's habits (it waits whole turns, it
  does not retreat damaged units, it captures one city at a time), which
  a competent self-play opponent would never show. Self-play keeps two
  jobs: the opponent wherever the port cannot play yet, and a sanity check
  that a weight set tuned against the port is not merely tuned to the
  port's passivity or to a bug in it. On these small maps a rollout search
  over `sim.apply` stays affordable in Python either way.
- **The CPU is read, not guessed.** Campaign missions are designed to be won
  from behind against this particular AI. A planner robust against a
  competent opponent may correctly conclude a mission is lost on material
  when the intended win depends on how the CPU actually behaves. Self-play
  cannot discover habits it never plays against; the ROM can tell us, and
  the reader is checked the same way everything else is.
- **Campaign facts wait until a mission demands them.** The planner's
  objective term defaults to the game's standard win conditions (HQ capture
  or rout). The funds rate is already derived from the board and the team
  byte is located. A mission's objective and scripted events are read only
  when that mission fails acceptance or ends in a way the defaults do not
  explain -- one dump and a line in a mission file each, not a project.

## The steps

1. **The first opinion.** `engine/advisor.py` and `tools/advise.py`: a
   one-turn greedy planner with sequential commit. Enumerate every action
   for every unit plus builds and the power, score each with named terms
   over quoted facts, commit the best, advance the board through
   `sim.apply`, re-enumerate, repeat, end the turn. Weights live in one
   place and every step prints its score broken into terms, each citing the
   fact it reads. Starting terms, all in funds so they add: damage dealt
   (worst-case strike in unit value, kill bonus), damage taken (counter plus
   the ending tile's exposure, weighted by the share of the army it is),
   capture progress (income over a horizon, HQ capture as the win), objective
   distance (a small pull along pathing costs), turn-start gains (repair and
   resupply already on `turn_start`), builds (a fixed early table over enemy
   composition and terrain). Checked by the invariance tests -- board
   translation, slot renumbering, a neutral-CO board ranking as Andy -- and
   by reading its plans on the parked fixtures.

   *Delivered.* `engine/advisor.py` (the loop, `WEIGHTS`, `Term`/`Scored`/
   `Step`/`Plan`, `render`), `tools/advise.py`, `docs/ADVISOR.md`, and 21
   tests in `tests/test_advisor.py`: the arithmetic, the three invariances,
   and one-preference scenarios. Read on the fogged 15x10 fixture it
   captures the open city, tops up the dry Tank and drifts the rest toward
   the enemy's properties, in about thirty seconds. Its naiveties are
   listed in `docs/ADVISOR.md`; none is a weight's fault.

2. **The differential test.** `harness/mesen_state.lua` (the headless
   dumper, asserted tile for tile against an mGBA dump), `harness/
   mesen_drive.lua` (one Action executed with read-back verification), and
   `tools/sim_diff.py` over forty to sixty parked states: dump, `apply()`,
   drive, dump, diff every field. This certifies `sim.apply` and kills or
   confirms its six stated assumptions (ASSUMPTIONS, "What engine/sim.py
   states"). Everything after this stands on it; self-play on an uncertified
   model converges on its bugs.

   *Delivered* (DERIVATION 43). The dumper matches the mGBA dump tile for
   tile; the driver landed all 63 drives on their first attempt in about
   fifteen minutes; the corpus covers every action kind, both army actions
   and the turn-start economy. The first run contradicted the model nine
   times and each was a finding: End Turn clears the ending side's acted
   bits with passengers excepted, a finished capture raises the income
   field at once, an unanswerable shot takes the RNG's second draw rather
   than its third, Sonja's reducer parameters, a written rate cell the
   payer ignores, and empty army records counted as players. Fixed, the
   corpus replays at 63 of 63 (`tests/test_sim_diff.py`); four of the six
   stated assumptions became measurements and two remain stated.

3. **Read the CPU.** The AI loop at `0x08060B06` and its action dispatcher
   at `0x08066A98` are located, and DERIVATION 41 found the AI's own twin of
   the visibility check at `0x08023DD0`, so the entry points are known. The
   deliverable is `engine/cpu.py`: given a board, the turn the CPU will
   take -- movement, targets, builds, power use. It is checked by parking
   states, letting the real CPU play its turn headlessly, and diffing the
   prediction against what it did; that reuses the step 2 harness with the
   driver replaced by End Turn. The CPU's build logic falls out of the same
   read and informs the planner's build term.

   *Delivered for what the traces exercise* (DERIVATION 44, 45). The rig:
   army byte `+0x1B` = 2 hands the turn to the AI, `mesen_drive.lua`'s
   `cpu_turn` step lets it play and traces every 20-byte command record it
   dispatches and every RNG draw it makes, `tools/cpu_trace.py` runs,
   replays and now `predict`s a trace. The predictor: `engine/cpu.predict`
   over `engine/cpu_ai.py`, the AI ported routine by routine -- the
   nineteen sub-phases, the capture, attack, move, drop and supply passes,
   the forecast with its luck draws, the mover, the threat grid, the
   profile (`data/aw1_ai.json`, `tools/extract_ai.py`) -- reproduces all
   seven traced turns record for record and draw for draw and leaves the
   game's board (`tests/test_cpu.py`). **Building is read and ported**
   (DERIVATION 47): the CPU buys at the end of its turn through five
   choosers over the profile's own bytes, and twelve build traces --
   a factory inserted into the game's property list -- reproduce purchase
   for purchase, mode for mode and draw for draw. Not yet: movement modes
   2, 3, 5, 6, 7, the Lander, the TCopter, the loaded transport's move,
   the join and retreat pre-steps, firing a power, the TCopter and Lander
   purchases -- each raises NotImplementedError naming its routine, and
   each needs a trace that enters it (an air or sea side, a damaged or
   dry unit) before it is ported.

4. **The enemy reply.** The planner's lookahead scores against a modelled
   reply instead of worst-case focus fire: `cpu.py` when the opponent is the
   CPU, the planner itself when it is not. This is the step that turns sound
   play into winning play from behind, and where the CPU read pays for
   itself. Built after step 3 on purpose: a lookahead tuned against the
   wrong opponent would need retuning.

   *Delivered.* `advisor.plan(..., reply=)`: the greedy plan becomes a
   proposal, and with it up to `branches` variants -- at the closest calls
   the same actor's next-best action committed and the rest of the turn
   re-planned. Each proposal is followed by End Turn, the opponent's whole
   turn and End Turn again: `engine/cpu_ai.predict` when the opponent is
   the CPU, this planner one ply deep when not (and wherever the port meets
   a branch it has not read, saying so). The board at our next turn start
   is evaluated from our side as named terms (`advisor.evaluate`: material,
   treasury, income over the horizon, captures in hand, the HQ, the rout)
   and the proposal whose reply scores best is the plan; the plan carries
   the reply and every proposal's score. `tools/advise.py` models the reply
   by default, choosing the CPU port when the dump's controller byte says
   the opponent is the CPU. Nine tests in `tests/test_advisor.py`; the
   naiveties -- one alternative per close call, two plies, a static
   evaluation, the model's own errors -- are in `docs/ADVISOR.md`.

5. **Tuning against the port.** Planner against `engine/cpu_ai` in Python,
   on the parked VS states first and then on mission states, scoring days
   to the win, material lost and properties held rather than the bare
   win -- the port's speed makes thousands of games affordable, and the
   richer score separates two weight sets even when both win. Self-play
   (planner against planner) is the opponent wherever the port raises
   `NotImplementedError` and the check that a tuned set still plays
   soundly against something that fights back. Rollout search over
   `sim.apply` comes after, if greedy plus reply is not enough.

   Two rules make this loop honest. Every game the port aborts is a trace
   request: park that state, let the real CPU play it (the step 3 rig),
   port the branch -- so the port's coverage is driven toward what the
   campaign actually reaches (air and sea sub-phases, the retreat and join
   pre-steps) instead of guessed. And the shared-delusion caveat now
   covers two models: a bug in `apply()` is a belief both players hold,
   and a bug in the port is a habit the tuner will learn to exploit that
   the real CPU does not have; the cure for both is the same, differential
   traces on the game, never win-rate evaluation on the emulator (HANDOFF).

   Before any mission-level tuning, the campaign profile table
   (`0x080683B0`) is read: each mission's CO profile sets the AI's
   thresholds, so a set tuned against the VS profiles could miss a
   mission's personality. That read is one table, not a project. Steps 4
   and 5 are one iteration loop.

6. **Acceptance.** A whole-turn driver built on the single-action one, and
   one headless run per mission from a parked savestate, once per release.
   Whether a mission was won is read off the game, so no campaign facts are
   needed to run it. This is the definition of done. The handoff's argument
   against win-rate evaluation stands for tuning -- too slow and too noisy
   to compare two weight sets -- and does not apply here, where the win is
   the claim.

7. **Campaign facts, on demand.** A mission that fails acceptance, or ends
   in a way the standard win conditions do not explain, triggers reading its
   objective and events: victory and loss condition, scripted reinforcements
   and turn triggers, and the team byte (`army +0x26`) if the mission is not
   two-sided. Each becomes a line in a mission file the planner's objective
   term reads. A mission that still fails gets an opening book or a
   mission-specific term, written down as the concession it is.

## Order dependencies

- Step 2 before step 5: tuning needs a certified model.
- Step 3 before step 4 and 5: the reply model is what makes hard missions
  winnable, and the port is the sparring partner tuning is done against.
- Step 6 can start as soon as step 1 plays a turn; it only becomes the gate
  once steps 4 and 5 have something to prove.

## What each step leaves behind

| step | code | measured record | test |
|---|---|---|---|
| 1 | `engine/advisor.py`, `tools/advise.py`, `docs/ADVISOR.md` (delivered) | -- | invariance and scenario tests (`tests/test_advisor.py`, delivered) |
| 2 | `harness/mesen_state.lua`, `harness/mesen_drive.lua`, `tools/sim_diff.py` (delivered) | `tests/fixtures/sim_diff/`: the corpus, both parked states, 126 before/after dumps, the result log (delivered) | `sim_diff` clean: 63/63 (`tests/test_sim_diff.py`, delivered) |
| 3 | `engine/cpu.py` | DERIVATION: the CPU's rules, and the turns it played | prediction vs played turn -- seven traces, record for record and draw for draw (DERIVATION 45) |
| 4 | the reply lookahead in `advisor.py` (delivered: `reply=`, `evaluate`, `Reply`, `Candidate`; `tools/advise.py --reply`) | -- | scenario tests: the reply overturns a worst-case call; the CPU port plays the step 3 fixture's reply; the planner stands in where it cannot (`tests/test_advisor.py`, delivered) |
| 5 | `tools/sparring.py` (planner vs the port, self-play as fallback) | weight sets and their results per parked state; the traces the aborts requested | -- |
| 6 | the whole-turn driver, `tools/campaign_run.py` | a result per mission per release | the win |
| 7 | `data/missions/*.json` | one dump per mission read | -- |

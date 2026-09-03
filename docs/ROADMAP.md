# Roadmap: from the first opinion to beating the campaign

The target is a planner good enough to beat the built-in AI on every
campaign mission. The repo's rule still holds above the opinion line: the
facts are measured, the opinion is the first number the game cannot check by
play, and so the plan below is built so that every step is checked by one of
two critics -- the differential test for the facts and boards a plan
composes, and an acceptance harness that plays the campaign, because "beats
every mission" is a claim only the campaign can verify.

Three decisions, made once so they do not get re-argued:

- **Self-play is the core.** A certified forward model plus planner-vs-
  planner tuning makes a strong general player without a line of the CPU's
  code, and on these small maps a rollout search over `sim.apply` is
  affordable in Python.
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

2. **The differential test.** `harness/mesen_state.lua` (the headless
   dumper, asserted tile for tile against an mGBA dump), `harness/
   mesen_drive.lua` (one Action executed with read-back verification), and
   `tools/sim_diff.py` over forty to sixty parked states: dump, `apply()`,
   drive, dump, diff every field. This certifies `sim.apply` and kills or
   confirms its six stated assumptions (ASSUMPTIONS, "What engine/sim.py
   states"). Everything after this stands on it; self-play on an uncertified
   model converges on its bugs.

3. **Read the CPU.** The AI loop at `0x08060B06` and its action dispatcher
   at `0x08066A98` are located, and DERIVATION 41 found the AI's own twin of
   the visibility check at `0x08023DD0`, so the entry points are known. The
   deliverable is `engine/cpu.py`: given a board, the turn the CPU will
   take -- movement, targets, builds, power use. It is checked by parking
   states, letting the real CPU play its turn headlessly, and diffing the
   prediction against what it did; that reuses the step 2 harness with the
   driver replaced by End Turn. The CPU's build logic falls out of the same
   read and informs the planner's build term.

4. **The enemy reply.** The planner's lookahead scores against a modelled
   reply instead of worst-case focus fire: `cpu.py` when the opponent is the
   CPU, the planner itself when it is not. This is the step that turns sound
   play into winning play from behind, and where the CPU read pays for
   itself. Built after step 3 on purpose: a lookahead tuned against the
   wrong opponent would need retuning.

5. **Self-play.** Planner against planner in Python for weight tuning, then
   rollout search over `sim.apply` if greedy plus reply is not enough.
   Relative rankings only; the shared-delusion caveat (a bug in `apply()`
   is a belief both players share and will exploit) is written where the
   planner lives. Steps 4 and 5 are one iteration loop.

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

- Step 2 before step 5: self-play needs a certified model.
- Step 3 before step 4: the reply model is what makes hard missions winnable.
- Step 6 can start as soon as step 1 plays a turn; it only becomes the gate
  once steps 4 and 5 have something to prove.

## What each step leaves behind

| step | code | measured record | test |
|---|---|---|---|
| 1 | `engine/advisor.py`, `tools/advise.py`, `docs/ADVISOR.md` | -- | invariance and scenario tests |
| 2 | `harness/mesen_state.lua`, `harness/mesen_drive.lua`, `tools/sim_diff.py` | the corpus and its result log as fixtures | `sim_diff` clean |
| 3 | `engine/cpu.py` | DERIVATION: the CPU's rules, and the turns it played | prediction vs played turn |
| 4 | the reply lookahead in `advisor.py` | -- | scenario tests |
| 5 | `tools/selfplay.py` | weight sets and their relative results | -- |
| 6 | the whole-turn driver, `tools/campaign_run.py` | a result per mission per release | the win |
| 7 | `data/missions/*.json` | one dump per mission read | -- |

"""The opinion layer, engine/advisor.py: invariance and scenario tests.

An opinion cannot be measured wrong, so these tests do not ask whether the
plans are GOOD. They ask two things the ROADMAP names: that the planner is
invariant under what it should be invariant under -- board translation,
slot renumbering, a neutral-CO board planning exactly as Andy -- and that
on hand-built boards where one preference is isolated, its plan reads the
way the weights say it should (a guaranteed kill over a wait, a safe tile
over an exposed one, a capture kept rather than abandoned, the HQ taken
when it can be, the power fired when it heals). And that the arithmetic is
honest: every Term's weight is the table's, the terms sum to the score, and
the rendering labels every weight heuristic.

Boards are built by hand, one rule per test, the same way test_sim.py and
test_actions.py do it.
"""
import dataclasses
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import advisor                                                # noqa: E402
import sim                                                    # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

PLAIN, MOUNTAIN, WOOD, ROAD, CITY, SEA, HQ, BASE = 1, 3, 4, 5, 6, 7, 8, 14
VOID = 0                    # out-of-bounds terrain: 255 for every move type
ANDY = 1


def board(rows, units=(), owner=None, armies=(), active=1, day=1, fog=False,
          **kw):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies), terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0, active_player=active, day=day, fog=fog,
                 funds_per_property=1000, repair_free=False, **kw)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False,
         capture=0, state=0, cargo=0, loaded=False):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=capture, fuel=fuel, acted=acted,
                carrying=bool(cargo), loaded=loaded, state=state, cargo=cargo)


def army(player, funds=10000, co_id=ANDY, power=0, uses=0):
    return Army(player=player, funds=funds, income=0, power=power,
                co_id=co_id, power_active=False, power_uses=uses,
                power_ready=False)


def shape(step, dx=0, dy=0):
    """A step without its slot numbers: (kind, actor tile, ending tile,
    target tile, build type), shifted back by (dx, dy)."""
    a = step.action
    actor = (a.unit.x - dx, a.unit.y - dy) if a.unit is not None else None
    tile = (a.tile[0] - dx, a.tile[1] - dy) if a.kind != "power" else None
    tgt = None
    if a.target is not None and a.kind != "build":
        tgt = (a.target.x - dx, a.target.y - dy)
    return (a.kind, actor, tile, tgt, a.build_type)


def two_armies(funds=10000, co_id=ANDY, funds2=0, co_id2=ANDY):
    return [army(1, funds, co_id), army(2, funds2, co_id2)]


# --------------------------------------------------------------------------
# the arithmetic
# --------------------------------------------------------------------------

class TestArithmetic(unittest.TestCase):
    def setUp(self):
        rows = [[PLAIN] * 8,
                [PLAIN, CITY, PLAIN, PLAIN, PLAIN, PLAIN, CITY, PLAIN],
                [BASE] + [PLAIN] * 6 + [HQ]]
        owner = [[0] * 8, [0] * 8, [1] + [0] * 6 + [2]]
        self.b = board(rows, [unit("Tank", 0, 0, slot=1),
                              unit("Infantry", 1, 1, slot=2, capture=10),
                              unit("Infantry", 6, 0, player=2, slot=70, hp=30),
                              unit("Tank", 5, 2, player=2, slot=71)],
                       owner=owner, armies=two_armies(9000))
        self.plan = advisor.plan(self.b)

    def test_every_weight_is_the_tables(self):
        for s in self.plan.steps:
            for t in s.scored.terms:
                self.assertIn(t.name, advisor.WEIGHTS)
                self.assertEqual(t.weight, advisor.WEIGHTS[t.name], t)

    def test_terms_sum_to_the_score_and_steps_to_the_plan(self):
        for s in self.plan.steps:
            self.assertAlmostEqual(sum(t.value for t in s.scored.terms),
                                   s.scored.score)
        self.assertAlmostEqual(sum(s.scored.score for s in self.plan.steps),
                               self.plan.score)

    def test_every_term_quotes_a_fact(self):
        for s in self.plan.steps:
            for t in s.scored.terms:
                self.assertTrue(t.fact.strip(), t)

    def test_a_weight_override_reaches_the_terms_and_unknown_ones_are_refused(self):
        p = advisor.plan(self.b, weights={"kill": 0.0})
        for s in p.steps:
            for t in s.scored.terms:
                if t.name == "kill":
                    self.assertEqual(t.weight, 0.0)
        self.assertEqual(p.weights["kill"], 0.0)
        with self.assertRaises(KeyError):
            advisor.plan(self.b, weights={"charisma": 1.0})

    def test_the_rendering_labels_every_weight_heuristic(self):
        text = advisor.render(self.plan)
        term_lines = [ln for ln in text.splitlines()
                      if ln.startswith("      ") and " <- " in ln]
        self.assertTrue(term_lines)
        for ln in term_lines:
            self.assertIn("(heuristic)", ln)
        self.assertIn("END TURN", text)

    def test_the_input_board_is_untouched_and_each_step_chains(self):
        before = repr(self.b)
        p = advisor.plan(self.b)
        self.assertEqual(repr(self.b), before)
        self.assertIs(p.steps[0].board_before, self.b)
        for a, b in zip(p.steps, p.steps[1:]):
            self.assertIs(b.board_before, a.board_after)
        self.assertIs(p.board_after, p.steps[-1].board_after)


# --------------------------------------------------------------------------
# invariance
# --------------------------------------------------------------------------

class TestInvariance(unittest.TestCase):
    def scene(self):
        rows = [[PLAIN, PLAIN, WOOD, PLAIN, PLAIN, PLAIN, PLAIN],
                [PLAIN, CITY, PLAIN, PLAIN, MOUNTAIN, PLAIN, CITY],
                [BASE, PLAIN, PLAIN, ROAD, ROAD, PLAIN, HQ]]
        owner = [[0] * 7, [1, 0, 0, 0, 0, 0, 0], [1, 0, 0, 0, 0, 0, 2]]
        units = [unit("Tank", 0, 0, slot=1, hp=70),
                 unit("Infantry", 1, 1, slot=2, capture=10),
                 unit("Artillery", 0, 1, slot=3),
                 unit("Infantry", 5, 0, player=2, slot=70, hp=40),
                 unit("Tank", 5, 2, player=2, slot=71, hp=60),
                 unit("Mech", 6, 1, player=2, slot=72)]
        return rows, owner, units

    def test_translation(self):
        """The same scene one tile down and right, inside a border of the
        out-of-bounds terrain (cost 255 for every move type): the plan is
        the same plan, shifted."""
        rows, owner, units = self.scene()
        a = board(rows, units, owner=owner, armies=two_armies(9000))
        w = len(rows[0])
        rows2 = [[VOID] * (w + 2)] + [[VOID] + r + [VOID] for r in rows] \
            + [[VOID] * (w + 2)]
        owner2 = [[0] * (w + 2)] + [[0] + r + [0] for r in owner] \
            + [[0] * (w + 2)]
        units2 = [dataclasses.replace(u, x=u.x + 1, y=u.y + 1) for u in units]
        b = board(rows2, units2, owner=owner2, armies=two_armies(9000))
        pa, pb = advisor.plan(a), advisor.plan(b)
        self.assertEqual([shape(s) for s in pa.steps],
                         [shape(s, 1, 1) for s in pb.steps])
        self.assertEqual([round(s.scored.score) for s in pa.steps],
                         [round(s.scored.score) for s in pb.steps])

    def test_slot_renumbering(self):
        """Slots permuted within each army's block: the plan is the same
        modulo the numbers."""
        rows, owner, units = self.scene()
        a = board(rows, units, owner=owner, armies=two_armies(9000))
        renum = {1: 17, 2: 3, 3: 1, 70: 90, 71: 66, 72: 100}
        units2 = [dataclasses.replace(u, slot=renum[u.slot]) for u in units]
        b = board(rows, list(reversed(units2)), owner=owner,
                  armies=two_armies(9000))
        pa, pb = advisor.plan(a), advisor.plan(b)
        self.assertEqual([shape(s) for s in pa.steps],
                         [shape(s) for s in pb.steps])
        self.assertEqual([round(s.scored.score) for s in pa.steps],
                         [round(s.scored.score) for s in pb.steps])

    def test_a_neutral_co_board_plans_as_andy(self):
        """Andy's record is neutral everywhere the facts look (100/100 per
        unit and universal, luck 0..9, no capture shift, value x1), so a
        board whose COs are UNKNOWN -- which every fact module quotes as
        neutral, out loud -- must plan identically, term for term."""
        rows, owner, units = self.scene()
        a = board(rows, units, owner=owner,
                  armies=two_armies(9000, ANDY, 0, ANDY))
        b = board(rows, units, owner=owner,
                  armies=two_armies(9000, None, 0, None))
        pa, pb = advisor.plan(a), advisor.plan(b)
        self.assertEqual([shape(s) for s in pa.steps],
                         [shape(s) for s in pb.steps])
        for sa, sb in zip(pa.steps, pb.steps):
            self.assertEqual([(t.name, round(t.value)) for t in sa.scored.terms],
                             [(t.name, round(t.value)) for t in sb.scored.terms])
        self.assertTrue(any("CO unknown" in w or "CO is unknown" in w
                            for w in pb.warnings))


# --------------------------------------------------------------------------
# scenarios: one preference isolated per board
# --------------------------------------------------------------------------

class TestScenarios(unittest.TestCase):
    def test_a_guaranteed_kill_beats_waiting(self):
        """A Tank beside a 2-bar Infantry nothing else can reach: the kill
        is worth its bars plus the kill bonus, and the wait is worth
        nothing."""
        b = board([[PLAIN] * 4], [unit("Tank", 0, 0, slot=1),
                                  unit("Infantry", 3, 0, player=2, slot=70, hp=20)],
                  armies=two_armies())
        p = advisor.plan(b)
        s = p.steps[0]
        self.assertEqual(s.action.kind, "attack")
        self.assertTrue(s.action.strike.guaranteed_kill)
        names = {t.name for t in s.scored.terms}
        self.assertIn("damage_dealt", names)
        self.assertIn("kill", names)
        self.assertIsNone(sim.unit_in(p.board_after, 70))

    def test_an_exposed_unit_retreats_to_the_tile_nothing_reaches(self):
        """An Infantry in an enemy Tank's reach with one tile out of it:
        the plan parks it there, and the exposed alternatives carry a
        damage_taken term."""
        rows = [[PLAIN] * 9]
        b = board(rows, [unit("Infantry", 2, 0, slot=1),
                         unit("Tank", 8, 0, player=2, slot=70)],
                  armies=two_armies(0))
        p = advisor.plan(b, weights={"objective_pull": 0})
        s = p.steps[0]
        self.assertEqual(s.action.kind, "wait")
        self.assertEqual(s.action.tile, (0, 0))       # Tank moves 6: 8-6-1 = 1
        self.assertEqual(s.action.exposure.worst_damage, 0)
        exposed = [c for c in advisor.candidates(b, 1, advisor.Context(b, 1))
                   if c.action.kind == "wait" and c.action.tile == (2, 0)]
        self.assertTrue(any(t.name == "damage_taken" and t.value < 0
                            for t in exposed[0].terms))

    def test_a_capture_in_progress_is_kept_and_leaving_it_is_charged(self):
        b = board([[CITY, PLAIN, PLAIN]],
                  [unit("Infantry", 0, 0, slot=1, capture=10)],
                  armies=two_armies())
        p = advisor.plan(b)
        s = p.steps[0]
        self.assertEqual((s.action.kind, s.action.tile), ("capture", (0, 0)))
        self.assertTrue(s.action.captures_now)
        self.assertEqual(p.board_after.owner[0][0], 1)
        ctx = advisor.Context(b, 1)
        walk = [c for c in advisor.candidates(b, 1, ctx)
                if c.action.kind == "wait" and c.action.tile == (2, 0)][0]
        abandon = [t for t in walk.terms if t.name == "capture"]
        self.assertEqual(len(abandon), 1)
        self.assertLess(abandon[0].value, 0)
        self.assertIn("abandons 10/20", abandon[0].fact)

    def test_the_hq_is_taken_when_it_can_be(self):
        """A 10-bar Infantry on the enemy HQ at 10/20 finishes it, and the
        win term dominates every other number on the board -- even an
        adjacent enemy Tank's exposure."""
        rows = [[HQ, PLAIN, PLAIN]]
        owner = [[2, 0, 0]]
        b = board(rows, [unit("Infantry", 0, 0, slot=1, capture=10),
                         unit("Tank", 2, 0, player=2, slot=70)],
                  owner=owner, armies=two_armies())
        p = advisor.plan(b)
        s = p.steps[0]
        self.assertEqual(s.action.kind, "capture")
        self.assertTrue(any(t.name == "win" for t in s.scored.terms))
        self.assertGreater(s.scored.score, advisor.WEIGHTS["win"] / 2)

    def test_the_second_step_is_scored_on_the_board_the_first_leaves(self):
        """Two Tanks on one enemy Tank: the second strike quotes the
        target at the HP the first strike (worst case) left it."""
        rows = [[PLAIN] * 3, [PLAIN] * 3, [PLAIN] * 3]
        b = board(rows, [unit("Tank", 0, 0, slot=1), unit("Tank", 0, 2, slot=2),
                         unit("Tank", 2, 1, player=2, slot=70)],
                  armies=two_armies(0))
        p = advisor.plan(b)
        attacks = [s for s in p.steps if s.action.kind == "attack"]
        self.assertEqual(len(attacks), 2)
        first, second = attacks
        hp_after_first = sim.unit_in(first.board_after, 70).hp
        self.assertLess(hp_after_first, 100)
        self.assertEqual(second.action.target.hp, hp_after_first)
        self.assertEqual(second.action.strike.max_remaining_hp,
                         0 if sim.unit_in(p.board_after, 70) is None
                         else sim.unit_in(p.board_after, 70).hp)

    def test_every_unit_that_can_act_gets_exactly_one_action(self):
        b = board([[PLAIN] * 5, [PLAIN] * 5],
                  [unit("Infantry", 0, 0, slot=1), unit("Recon", 0, 1, slot=2),
                   unit("Tank", 1, 0, slot=3, acted=True),
                   unit("Mech", 4, 1, player=2, slot=70)],
                  armies=two_armies(0))
        p = advisor.plan(b)
        actors = [s.action.unit.slot for s in p.steps if s.action.unit]
        self.assertEqual(sorted(actors), [1, 2])
        self.assertTrue(all(u.acted for u in p.board_after.units
                            if u.player == 1))

    def test_a_build_needs_funds_and_an_empty_own_factory(self):
        rows = [[BASE, PLAIN, PLAIN, CITY]]
        owner = [[1, 0, 0, 0]]
        rich = board(rows, [], owner=owner, armies=two_armies(9000))
        p = advisor.plan(rich)
        builds = [s for s in p.steps if s.action.kind == "build"]
        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0].action.build_type, "Infantry")
        names = {t.name for t in builds[0].scored.terms}
        self.assertIn("build_capture", names)
        self.assertIn("build_spend", names)
        self.assertEqual(p.board_after.army(1).funds, 9000 - 1000)
        broke = board(rows, [], owner=owner, armies=two_armies(500))
        self.assertEqual(advisor.plan(broke).steps, [])
        busy = board(rows, [unit("Infantry", 0, 0, slot=1, acted=True)],
                     owner=owner, armies=two_armies(9000))
        self.assertEqual(advisor.plan(busy).steps, [])

    def test_a_build_that_scores_nothing_is_not_made(self):
        """No enemy to match, no property to take, and the bias against
        transports: the shop is open and the plan buys nothing."""
        rows = [[BASE, PLAIN, PLAIN]]
        owner = [[1, 1, 1]]
        b = board(rows, [], owner=owner, armies=two_armies(90000))
        p = advisor.plan(b, weights={"build_bias": 0})
        self.assertEqual(p.steps, [])

    def test_the_power_fires_first_when_it_heals(self):
        """Andy at the threshold with two damaged units: the heals are
        worth bars x bar value, the power is the best first step, and the
        units then act on the healed board."""
        b = board([[PLAIN] * 4],
                  [unit("Tank", 0, 0, slot=1, hp=50),
                   unit("Infantry", 1, 0, slot=2, hp=60)],
                  armies=[army(1, 0, ANDY, power=30000), army(2, 0)])
        p = advisor.plan(b)
        self.assertEqual(p.steps[0].action.kind, "power")
        names = [t.name for t in p.steps[0].scored.terms]
        self.assertEqual(names.count("repair"), 2)       # one heal per unit
        self.assertIn("power_block", names)               # the 110/90 block
        self.assertEqual(sim.unit_in(p.steps[0].board_after, 1).hp, 70)
        later = [s for s in p.steps[1:] if s.action.unit is not None]
        self.assertTrue(later)
        self.assertEqual(later[0].action.unit.hp,
                         sim.unit_in(p.steps[0].board_after,
                                     later[0].action.unit.slot).hp)
        self.assertEqual(p.board_after.army(1).power, 0)
        self.assertTrue(p.board_after.army(1).power_active)

    def test_the_objective_pulls_an_idle_unit_forward(self):
        """Nothing to shoot, nothing to fear: a Tank drifts toward the
        enemy, and the term names the objective and the two distances."""
        rows = [[PLAIN] * 12]
        b = board(rows, [unit("Tank", 0, 0, slot=1),
                         unit("Mech", 11, 0, player=2, slot=70)],
                  armies=two_armies(0))
        p = advisor.plan(b)
        s = p.steps[0]
        self.assertEqual(s.action.kind, "wait")
        self.assertGreater(s.action.tile[0], 0)
        pull = [t for t in s.scored.terms if t.name == "objective"]
        self.assertEqual(len(pull), 1)
        self.assertIn("nearest visible enemy", pull[0].fact)
        self.assertGreater(pull[0].value, 0)

    def test_the_morning_facts_score_a_repair_and_a_crash(self):
        rows = [[CITY, PLAIN, PLAIN]]
        owner = [[1, 0, 0]]
        b = board(rows, [unit("Tank", 1, 0, slot=1, hp=40)],
                  owner=owner, armies=two_armies(9000))
        p = advisor.plan(b)
        s = p.steps[0]
        self.assertEqual((s.action.kind, s.action.tile), ("wait", (0, 0)))
        names = {t.name for t in s.scored.terms}
        self.assertIn("repair", names)
        self.assertIn("repair_spend", names)
        # a BCopter down to its last fuel crashes tomorrow wherever it parks
        c = board([[PLAIN] * 3], [unit("BCopter", 1, 0, slot=1, fuel=2)],
                  armies=two_armies(0))
        q = advisor.plan(c)
        crash = [t for t in q.steps[0].scored.terms if t.name == "crash"]
        self.assertEqual(len(crash), 1)
        self.assertLess(crash[0].value, 0)

    def test_a_trap_is_never_a_candidate(self):
        """Under fog, an Infantry whose grid reaches a hidden enemy is
        offered the trap by the action layer and never by the planner."""
        b = board([[PLAIN] * 5], [unit("Infantry", 0, 0, slot=1),
                                  unit("Infantry", 3, 0, player=2, slot=70)],
                  armies=two_armies(0), fog=True)
        import actions
        kinds = {a.kind for a in actions.actions_for(b, b.units[0], warnings=[])}
        self.assertIn("trap", kinds)
        ctx = advisor.Context(b, 1)
        self.assertFalse(any(c.action.kind == "trap"
                             for c in advisor.candidates(b, 1, ctx)))


if __name__ == "__main__":
    unittest.main()

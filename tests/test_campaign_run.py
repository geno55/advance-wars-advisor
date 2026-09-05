"""tools/campaign_run.py, the acceptance loop's Python half (ROADMAP step 6):
the judge that reads a win or a loss off a board, and the plan compiler
that turns the planner's steps into driver steps. The loop itself runs on
the emulator and is not tested here; harness/out/play holds its runs.
"""
import dataclasses
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import campaign_run                                           # noqa: E402
from engine import advisor                                    # noqa: E402
from engine.state import Army, Board, Unit, load              # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "cpu"
PLAIN, CITY, HQ = 1, 6, 8


def board(rows, units=(), owner=None, armies=(), active=1, day=1):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies), terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0, active_player=active, day=day, fog=False,
                 funds_per_property=1000, repair_free=False)


def unit(utype, x, y, player=1, slot=1):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=100,
                ammo=9, capture=0, fuel=99, acted=False, carrying=False,
                loaded=False, state=0, cargo=0)


def armies():
    return [Army(player=p, funds=0, income=0, co_id=1, power_uses=0,
                 power_ready=False) for p in (1, 2)]


class TestTheJudge(unittest.TestCase):
    def start(self):
        return board([[HQ, PLAIN, PLAIN, HQ]],
                     [unit("Infantry", 1, 0), unit("Infantry", 2, 0, player=2, slot=70)],
                     owner=[[1, 0, 0, 2]], armies=armies())

    def test_still_playing(self):
        s = self.start()
        self.assertEqual(campaign_run.judge(s, 1, s, 30), (None, ""))

    def test_the_enemy_hq_taken_is_the_win_and_ours_the_loss(self):
        s = self.start()
        won = dataclasses.replace(s, owner=[[1, 0, 0, 1]], day=7)
        over, note = campaign_run.judge(won, 1, s, 30)
        self.assertEqual(over, "win")
        self.assertIn("(3,0) is ours on day 7", note)
        lost = dataclasses.replace(s, owner=[[2, 0, 0, 2]])
        self.assertEqual(campaign_run.judge(lost, 1, s, 30)[0], "loss")
        # from P2's side the same boards read the other way
        self.assertEqual(campaign_run.judge(won, 2, s, 30)[0], "loss")
        self.assertEqual(campaign_run.judge(lost, 2, s, 30)[0], "win")

    def test_the_rout_both_ways_and_the_day_cap(self):
        s = self.start()
        routed = dataclasses.replace(s, units=[s.units[0]])
        self.assertEqual(campaign_run.judge(routed, 1, s, 30)[0], "win")
        self.assertEqual(campaign_run.judge(routed, 2, s, 30)[0], "loss")
        capped = dataclasses.replace(s, day=31)
        self.assertEqual(campaign_run.judge(capped, 1, s, 30)[0], "daycap")
        self.assertEqual(campaign_run.judge(dataclasses.replace(s, day=30), 1, s, 30)[0], None)


class TestThePlanCompiler(unittest.TestCase):
    def test_a_plans_steps_compile_to_driver_steps_with_checks(self):
        """The planner's turn on a traced dump becomes one driver step per
        action, each tagged, each carrying the read-back checks the
        single-action driver verifies with, in the plan's order."""
        b = load(FIX / "vs15-p1-cpu.before.json")
        plan = advisor.plan(b, 2, reply=None)
        steps = campaign_run.compile_plan(plan, 2, "t01r0", [])
        self.assertEqual(len(steps), len([s for s in plan.steps if s.action.kind != "trap"]))
        for i, (st, s) in enumerate(zip(plan.steps, steps)):
            self.assertEqual(s["tag"], f"t01r0-{i + 1}")
            self.assertEqual(s["kind"], st.action.kind)
            self.assertTrue(s["checks"])
            self.assertEqual(s["describe"], advisor.describe_action(st.action))
            if st.action.unit is not None:
                self.assertEqual(s["slot"], st.action.unit.slot)
                self.assertEqual((s["dest"]["x"], s["dest"]["y"]), tuple(st.action.tile))
        # the Lua serialisation the loop loads is one `return {...}` table
        src = "return " + campaign_run.sim_diff.lua({"steps": steps, "over": None, "note": "x"})
        self.assertTrue(src.startswith("return {") and src.endswith("}"))
        self.assertNotIn("None", src)


if __name__ == "__main__":
    unittest.main()

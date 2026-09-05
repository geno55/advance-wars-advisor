"""tools/fidelity.py: the CPU port against the real CPU's turns of the two
mission-one acceptance runs kept under tests/fixtures/acceptance (m01a is
days 1-4, m01b the resumed run, days 5-25; DERIVATION 55). Every turn is
replayed through the forward model (a no-luck match, so our driven steps
land exactly), the port plays the CPU's reply, and the board it leaves is
diffed against the after-dump the loop saved.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import fidelity                                                      # noqa: E402
import sim_diff                                                      # noqa: E402
from engine import actions                                           # noqa: E402
from engine.state import load                                        # noqa: E402

ACC = ROOT / "tests" / "fixtures" / "acceptance"

# What the check still finds wrong, by run and turn. A port fix that clears
# one moves it OUT of here (the test says which); a change that adds one is
# a regression the test names by turn.
KNOWN_GAPS = {
    ("m01a", 1): "AntiAir #67's hunt goal: the game drove it to (11,3), the port to (15,5)",
    ("m01a", 2): "AntiAir #67's and Artillery #70's goals",
    ("m01b", 4): "Tank #73's hunt goal once repaired (day 8: game (11,2), port (15,6))",
    ("m01b", 5): "Tank #73's hunt goal (day 9)",
    ("m01b", 6): "Tank #73's hunt goal (day 10)",
    ("m01b", 11): "MdTank #66 took 20 from the counter where the model says 24",
    ("m01b", 12): "Tank #73 stood at (0,9) where the port moves it to (5,10)",
    ("m01b", 16): "not the port: Olaf issued no command on day 20 and the loop let the "
                  "AI play our turn 17 as well (mesen_drive.lua cpu_turn, fixed)",
}


class TestTheStepsFile(unittest.TestCase):
    def test_a_lua_table_reads_back_as_the_python_it_was(self):
        v = {"steps": [{"kind": "wait", "taps": ["up", "up"], "dest": {"x": 3, "y": 7},
                        "ok": True, "n": -2, "empty": []}], "note": 'a "quoted" note'}
        text = "return " + sim_diff.lua(v) + "\n"
        self.assertEqual(fidelity.lua_table(text), v)

    def test_the_runs_steps_files_all_parse(self):
        n = 0
        for f in ACC.glob("*/t*.steps.lua"):
            t = fidelity.lua_table(f.read_text(encoding="utf-8"))
            self.assertIn("steps", t)
            n += len(t["steps"])
        self.assertGreater(n, 100)


class TestTheInverse(unittest.TestCase):
    def test_a_compiled_step_names_the_action_it_came_from(self):
        # day 6 (the resumed run's turn 2): the AntiAir #8 that fired and
        # the Infantry #2 that captured; one action of each kind they have
        # goes round
        board = load(ACC / "m01b" / "t02.json")
        seen = set()
        for slot in (8, 2):
            u = next(u for u in board.units if u.slot == slot)
            every = actions.actions_for(board, u)
            for a in every:
                if a.kind == "trap" or a.kind in seen:
                    continue
                step, _, _ = sim_diff.compile_action(board, a, every, "t", [])
                back = fidelity.action_for(board, step, board.active_player)
                self.assertEqual((back.kind, back.tile, back.target.slot if back.target else None),
                                 (a.kind, a.tile, a.target.slot if a.target else None))
                seen.add(a.kind)
        self.assertTrue({"attack", "wait", "capture"} <= seen, seen)


class TestTheCheck(unittest.TestCase):
    def test_the_port_replays_every_turn_the_known_gaps_do_not_cover(self):
        found = {}
        for run in ("m01a", "m01b"):
            d = ACC / run
            steps = fidelity.driven_steps(d)
            for t in sorted(steps):
                if not (d / f"t{t:02d}.after.json").exists():
                    continue
                r = fidelity.check_turn(d, t, 1, steps[t])
                if r["error"] or r["diffs"]:
                    found[(run, t)] = r["error"] or r["diffs"]
        cleared = sorted(set(KNOWN_GAPS) - set(found))
        new = {k: v for k, v in found.items() if k not in KNOWN_GAPS}
        self.assertEqual(cleared, [], f"now agreeing -- move out of KNOWN_GAPS: "
                                      f"{[(k, KNOWN_GAPS[k]) for k in cleared]}")
        self.assertEqual(new, {}, f"turns that disagree and are not known gaps: {new}")


if __name__ == "__main__":
    unittest.main()

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
    # the hunt goals of days 1, 2, 8-10 and 16 were one misreading of the
    # hunt list, cleared by DERIVATION 56
    ("m01b", 11): "MdTank #66 took 20 from the counter where the model says 24 "
                  "(the forward model: m01-day15 agrees record for record)",
    ("m01b", 16): "not the port: Olaf issued no command on day 20 and the loop let the "
                  "AI play our turn 17 as well (mesen_drive.lua cpu_turn, fixed)",
    # m01c is the tuned game of DERIVATION 59 (weights.json beside it): a
    # rout on day 10, the debrief's A 945
    ("m01c", 9): "not the port (m01s-day9 agrees record for record): our Recon's "
                 "zero-damage shot at the MdTank was called failed by the driver's "
                 "read-back and the replay leaves it out; the check is widened now",
    # m01d is the first tuned set's game (weights.json beside it): a win by
    # HQ on day 18, the debrief's B 732
    ("m01d", 11): "not the port: the same zero-damage Recon shot called failed",
    ("m01d", 15): "not the port: our Mech #3's capture step failed its read-back after "
                  "the unit had moved, and the replay leaves a failed step out",
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
        # m01e is the S-rank game of DERIVATION 59 (weights.json beside it,
        # also data/weights_m01_s.json): every one of its nine turns agrees.
        # ft2b, ft3c and ft4b are Field Training 2, 3 and 4 played through their
        # lessons (DERIVATION 62); their dumps carry settings +7 = 0, the
        # meter rule off, which the port must honour
        for run in ("m01a", "m01b", "m01c", "m01d", "m01e", "ft2b", "ft3c", "ft4b"):
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

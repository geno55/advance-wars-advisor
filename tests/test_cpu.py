"""The CPU's traced turns, replayed through engine/cpu.py (ROADMAP step 3,
begun -- DERIVATION 44).

tools/cpu_trace.py let the game's own AI play a turn from a parked state
and recorded every command record it dispatched, with the boards either
side. These tests pin what that measured: the record decoding, the drop
direction, the AI's strike on the first RNG draw, and that seven of the
eight traces replay to the game's after-board field for field through the
same forward model the differential corpus certified. The eighth (fog) is
pinned as the action-layer gap it is.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cpu_trace                                              # noqa: E402
from engine import cpu, sim                                   # noqa: E402
from engine.state import load                                 # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "cpu"
EXACT = ["vs15-p1-cpu", "vs15-p1-cpu-max", "vs15-p1-cpu-power",
         "vs15-p1-cpu-build", "a15-p2-cpu", "vs15-p2-cpu"]


def trace(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class TestTheRecord(unittest.TestCase):
    def test_every_trace_was_driven_with_at_most_one_command_per_unit(self):
        for name in EXACT + ["vs15-p1-cpu-fog"]:
            with self.subTest(name=name):
                t = trace(name)
                self.assertTrue(t["driven"])
                before = load(FIX / t["before"])
                movers = {u.slot for u in before.units
                          if u.player == t["cpu"] and not u.loaded and not u.acted}
                slots = [c["slot"] for c in t["commands"]]
                self.assertEqual(len(slots), len(set(slots)))
                self.assertTrue(set(slots) <= movers)

    def test_the_loaded_apc_on_the_p2_trace_issued_no_command(self):
        """Five records for six movable units: the APC carrying the
        Infantry got no record at all (DERIVATION 44)."""
        t = trace("vs15-p2-cpu")
        self.assertEqual(len(t["commands"]), 5)
        self.assertNotIn(69, [c["slot"] for c in t["commands"]])

    def test_the_fire_record_names_its_target_slot(self):
        t = trace("vs15-p1-cpu")
        fires = [cpu.from_record(c) for c in t["commands"] if c["id"] == 4]
        self.assertEqual([f.arg for f in fires], [65, 65])
        self.assertEqual([f.tile for f in fires], [(7, 4), (8, 5)])

    def test_the_drop_record_is_a_direction_per_cargo_slot(self):
        t = trace("a15-p2-cpu")
        drop = [cpu.from_record(c) for c in t["commands"] if c["id"] == 7][0]
        self.assertEqual((drop.arg, drop.arg2), (1, 0))
        after = load(FIX / t["after"])
        self.assertEqual((sim.unit_in(after, 67).x, sim.unit_in(after, 67).y),
                         cpu.DROP_DIRS[1](drop.tile))

    def test_the_load_record_is_id_six(self):
        t = trace("a15-p2-cpu")
        names = [c["name"] for c in t["commands"]]
        self.assertIn("cmd6", names)          # the trace's own label
        cmds = [cpu.from_record(c) for c in t["commands"]]
        self.assertEqual([c.name for c in cmds if c.id == 6], ["load"])


class TestTheReplay(unittest.TestCase):
    def test_seven_traces_replay_to_the_game_board_exactly(self):
        for name in EXACT:
            with self.subTest(name=name):
                diffs, warnings = cpu_trace.replay(trace(name))
                self.assertEqual(diffs, [])
                self.assertEqual([w for w in warnings if "matching" in w], [])

    def test_the_ai_strike_takes_the_first_draw(self):
        """The Max trace: the Mech's bazooka dealt 55 to the Tank; draw 1
        of the record's RNG says 55, the human path's draw 3 says 57."""
        t = trace("vs15-p1-cpu-max")
        self.assertEqual(cpu_trace.replay(t, luck_draw=1)[0], [])
        self.assertNotEqual(cpu_trace.replay(t, luck_draw=3)[0], [])

    def test_fog_changed_nothing_the_ai_did(self):
        clear = [(c["id"], c["slot"], c["x"], c["y"], c["b6"], c["rng"])
                 for c in trace("vs15-p1-cpu")["commands"]]
        fog = [(c["id"], c["slot"], c["x"], c["y"], c["b6"], c["rng"])
               for c in trace("vs15-p1-cpu-fog")["commands"]]
        self.assertEqual(clear, fog)

    def test_the_fog_trace_names_the_action_layer_gap(self):
        """Under fog actions_for offers no shot at an enemy the mover only
        sees after moving, so the two fires find no Action -- recorded here
        so the gap cannot close silently."""
        diffs, warnings = cpu_trace.replay(trace("vs15-p1-cpu-fog"))
        unmatched = [w for w in warnings if "0 matching" in w]
        self.assertEqual(len(unmatched), 2)
        self.assertTrue(diffs)


class TestWhatTheCpuDidNotDo(unittest.TestCase):
    def test_a_full_meter_was_not_fired(self):
        t = trace("vs15-p1-cpu-power")
        after = load(FIX / t["after"])
        self.assertEqual((after.army(1).power, after.army(1).power_active), (30000, False))

    def test_a_written_base_bought_nothing(self):
        t = trace("vs15-p1-cpu-build")
        before, after = load(FIX / t["before"]), load(FIX / t["after"])
        self.assertEqual(len(after.units_of(1)), len(before.units_of(1)))


if __name__ == "__main__":
    unittest.main()

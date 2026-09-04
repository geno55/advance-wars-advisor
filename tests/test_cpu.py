"""The CPU's traced turns, replayed and PREDICTED through engine/cpu.py
(ROADMAP step 3 -- DERIVATION 44 and 45).

tools/cpu_trace.py let the game's own AI play a turn from a parked state
and recorded every command record it dispatched, every RNG draw it made,
and the boards either side. These tests pin what that measured -- the
record decoding, the drop direction, the AI's strike on the first RNG
draw, the traces replaying to the game's after-board field for field --
and then the step's deliverable: engine/cpu.predict, the AI ported from
the ROM, reproduces every traced turn record for record and draw for
draw, and leaves the board the game left.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cpu_trace                                              # noqa: E402
from engine import cpu, cpu_ai, sim                           # noqa: E402
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


class TestThePrediction(unittest.TestCase):
    """engine/cpu.predict against every trace (tools/cpu_trace.py predict)."""
    ALL = EXACT + ["vs15-p1-cpu-fog"]

    def test_every_trace_is_predicted_record_for_record(self):
        for name in self.ALL:
            with self.subTest(name=name):
                r = cpu_trace.predict(trace(name))
                self.assertEqual(r["predicted"], r["traced"], "\n".join(r["log"]))

    def test_every_rng_draw_the_ai_made_is_accounted_for(self):
        """The trace logs the RNG state before each draw at 0x08010A84;
        the predictor's draws -- the unit's random, the forecast's luck,
        the path builder's ties, the battle's two -- line up with it."""
        for name in self.ALL:
            with self.subTest(name=name):
                t = trace(name)
                self.assertTrue(t["draws"], "re-trace this fixture: no draw log")
                r = cpu_trace.predict(t)
                self.assertIsNone(r["first_bad_draw"])
                self.assertEqual(r["draws"], r["logged_draws"])

    def test_the_predicted_turn_leaves_the_game_board(self):
        for name in self.ALL:
            with self.subTest(name=name):
                t = trace(name)
                r = cpu_trace.predict(t)
                board = sim.end_turn(r["turn"].board)
                after = load(FIX / t["after"])
                self.assertEqual(cpu_trace.sim_diff.diff_boards(board, after), [])

    def test_the_profile_is_the_missions_row_by_the_co(self):
        """Map 38 is a VS mission on row 1; Andy (co 1) takes profile 4,
        and the after-dump's live copy at 0x020235DC is byte for byte
        that profile (DERIVATION 45)."""
        raw = json.loads((FIX / "vs15-p1-cpu.after.json").read_text(encoding="utf-8"))
        prof = cpu_ai.profile_for(raw["map_id"], {1: 1, 2: 1}, 1)
        live = bytes.fromhex(raw["ai_profile"])
        self.assertEqual(prof["header"], list(live[:16]))
        self.assertEqual(prof["units"]["Infantry"], list(live[16:28]))
        self.assertEqual(prof["units"]["Infantry"][:4], [90, 10, 10, 20])

    def test_andys_power_predicate_wants_a_damaged_unit(self):
        """The full meter on vs15-p1-cpu-power went unfired because Andy's
        AI predicate (0x080632C4) fires only with a unit at 90 hp or less
        -- the predictor runs that turn through its power sub-phase and
        issues nothing."""
        r = cpu_trace.predict(trace("vs15-p1-cpu-power"))
        self.assertTrue(r["agree"])
        self.assertEqual(cpu_ai.tables()["cos"][1]["power_fn"], "0x080632C4")


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

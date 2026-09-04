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
import dataclasses
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
# The build traces (DERIVATION 47): a factory inserted into the game's
# property list, P1 as the CPU, one variable each.
BUILDS = ["build-b1-base", "build-b2-two-bases", "build-b3-broke",
          "build-b4-day10", "build-b5-airport", "build-b6-port",
          "build-b7-enemy-tanks", "build-b8-five-foot", "build-b9-max",
          "build-b10-kanbei", "build-b11-rich-tanks", "build-b12-fallback-rockets"]
# The condition byte and the pre-step (DERIVATION 48): one CPU unit written
# damaged, dry or empty on the vs15_p2 state, P1 as the CPU.
PRESTEP = ["prestep-join-mech", "prestep-join-sum-over", "prestep-hp-inf",
           "prestep-hp-tank", "prestep-fuel-tank", "prestep-fuel-inf",
           "prestep-repair-inf", "prestep-repair-tank"]
# The "nothing to do" fallback (DERIVATION 49): neutral cities written to
# the CPU's side on vs15_p2 so that its foot units outnumber the properties
# left to walk to -- four written and the APC removed, then all six.
NOPROP = ["noprop-foot", "noprop-apc"]
# The CO power (DERIVATION 50): P1's meter written to its threshold on
# vs15_p2 -- Andy with a damaged unit, Max, and Eagle whose predicate
# fires at the end-of-turn pass and sends the refreshed units round again.
POWER = ["power-andy", "power-max", "power-eagle"]


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
    ALL = EXACT + ["vs15-p1-cpu-fog"] + BUILDS + PRESTEP + NOPROP + POWER

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


class TestThePreStep(unittest.TestCase):
    """The condition byte (record +9 bits 0..2) and what a conditioned unit
    does before its pass -- DERIVATION 48, eight traces."""

    def predicted(self, name):
        return cpu_trace.predict(trace(name))

    def cmds(self, name):
        return [(c["name"], c["slot"], (c["x"], c["y"])) for c in trace(name)["commands"]]

    def test_the_classifier_leaves_the_bits_the_game_left(self):
        """The after-dump carries each unit's +9 byte; the port's context
        ends the turn with the same condition bits for every CPU unit."""
        for name in PRESTEP:
            with self.subTest(name=name):
                t = trace(name)
                r = self.predicted(name)
                after = json.loads((FIX / t["after"]).read_text(encoding="utf-8"))
                game = {u["slot"]: u["ai"][0] & 7 for u in after["units"] if u["player"] == t["cpu"]}
                port = {s: v[0] & 7 for s, v in r["turn"].ctx.ai.items() if s in game}
                self.assertEqual(port, game)

    def test_low_hp_is_condition_two_and_empty_gauges_condition_one(self):
        after = json.loads((FIX / "prestep-hp-tank.after.json").read_text(encoding="utf-8"))
        tank = next(u for u in after["units"] if u["slot"] == 7)
        self.assertEqual(tank["ai"][0] & 7, 2)                     # 15 hp < 20
        after = json.loads((FIX / "prestep-fuel-tank.after.json").read_text(encoding="utf-8"))
        tank = next(u for u in after["units"] if u["slot"] == 7)
        self.assertEqual(tank["ai"][0] & 7, 1)                     # fuel 5 of 70 < 20%
        after = json.loads((FIX / "prestep-join-sum-over.after.json").read_text(encoding="utf-8"))
        mech = next(u for u in after["units"] if u["slot"] == 1)
        self.assertEqual((mech["ammo"], mech["ai"][0] & 7), (0, 1))  # an empty gauge

    def test_a_weak_unit_joins_its_weak_neighbour(self):
        """Mech #1 at 40 hp beside Mech #4 at 50: a join (id 9) onto (1,7)
        as its command, before the foot pass -- 90 hp together."""
        self.assertIn(("join", 1, (1, 7)), self.cmds("prestep-join-mech"))
        after = load(FIX / "prestep-join-mech.after.json")
        self.assertIsNone(sim.unit_in(after, 4))
        self.assertEqual(sim.unit_in(after, 1).hp, 90)
        # with the neighbour at 100 the pair would exceed 100: no join, and
        # the empty gauge sends the Mech toward the HQ instead
        cmds = self.cmds("prestep-join-sum-over")
        self.assertNotIn("join", [c[0] for c in cmds])
        self.assertIn(("wait", 1, (0, 7)), cmds)

    def test_a_dry_unit_seeks_a_supplier_in_reach_else_a_property(self):
        # the Tank (fuel 5) walks up to the APC at (6,2) and stops beside it
        self.assertIn(("wait", 7, (6, 3)), self.cmds("prestep-fuel-tank"))
        # the Infantry (fuel 5) has no supplier in reach: the HQ resupplies
        cmds = self.cmds("prestep-fuel-inf")
        self.assertEqual(cmds[0], ("wait", 1, (0, 8)))

    def test_low_hp_without_an_own_repairing_property_changes_nothing(self):
        """The HQ is not a repair point for the retreat (0x083B7DDC codes
        it 0); with no own city the 15-hp Infantry and Tank play their
        ordinary passes."""
        self.assertIn(("wait", 1, (2, 8)), self.cmds("prestep-hp-inf"))
        self.assertIn(("fire", 7, (7, 4)), self.cmds("prestep-hp-tank"))

    def test_low_hp_with_an_own_city_retreats_to_it(self):
        # the Tank at 15 hp reaches the written city (4,1) this turn: a Wait onto it
        self.assertIn(("wait", 7, (4, 1)), self.cmds("prestep-repair-tank"))
        # the Infantry at 15 hp cannot reach (3,8) in one move: it moves
        # toward it in the pre-step -- the FIRST command of the turn, where
        # without the city (prestep-hp-inf) the same unit walked in the
        # foot pass, fourth -- and stops one tile from the city
        cmds = self.cmds("prestep-repair-inf")
        self.assertEqual(cmds[0], ("wait", 1, (2, 8)))
        plain = self.cmds("prestep-hp-inf")
        self.assertEqual(plain.index(("wait", 1, (2, 8))), 3)
        self.assertEqual(sim.unit_in(load(FIX / "prestep-repair-inf.before.json"), 1).hp, 15)


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


class TestBuilding(unittest.TestCase):
    """Driver state 4 (DERIVATION 47): the purchases the game made, hooked
    at 0x080243DC, against the port's -- type, factory, and the mode byte
    the new unit is given -- and the RNG draws the choosers make."""

    def test_every_build_trace_is_predicted_purchase_for_purchase(self):
        for name in BUILDS:
            with self.subTest(name=name):
                r = cpu_trace.predict(trace(name))
                self.assertEqual(r["predicted_builds"], r["traced_builds"], "\n".join(r["log"]))

    def test_the_build_happens_after_the_last_unit_and_draws_twice(self):
        """b1: the two draws after the Artillery's own random are the
        Infantry/Mech roll (0x0806702A) and the mode roll (0x08067BD8)."""
        t = trace("build-b1-base")
        self.assertEqual([hex(d["lr"]) for d in t["draws"][-2:]], ["0x806702f", "0x8067bdd"])
        self.assertEqual(t["builds"][0]["state"], 5)
        r = cpu_trace.predict(t)
        self.assertEqual([d["why"] for d in r["turn"].draws[-2:]],
                         ["build foot roll", "build mode roll Infantry"])

    def test_the_foot_share_cap_hands_the_second_base_to_the_counter_chooser(self):
        """b2: one Infantry makes five foot soldiers of nine (over
        header[0] and header[2]), so the second Base goes to the counter
        chooser: the enemy Recon is the least-answered type and the MdTank
        (primary 105 against it) is within 50% of the 37000 in the bank."""
        t = trace("build-b2-two-bases")
        self.assertEqual([(b["type"], b["x"], b["y"]) for b in t["builds"]],
                         [(1, 2, 4), (3, 1, 4)])
        r = cpu_trace.predict(t)
        self.assertIn("build counter: MdTank vs Recon", "\n".join(r["turn"].log))

    def test_with_less_in_the_bank_the_fallback_buys_by_share(self):
        """b12: the same five foot soldiers with 28500 at the shop: the
        MdTank is over 50% of the funds, no other enemy type scores, and
        the fallback (0x08067624) buys the heaviest of the zero-share types
        -- Rockets, weight 40, over MdTank 10, Recon 9 and AntiAir 5."""
        t = trace("build-b12-fallback-rockets")
        self.assertEqual([(b["type"], b["funds"]) for b in t["builds"]], [(11, 28500)])
        r = cpu_trace.predict(t)
        self.assertIn("build fallback: Rockets", "\n".join(r["turn"].log))

    def test_the_day_scales_the_mech_chance(self):
        """b4: day 11 makes min(80, 11 x header[3]) = 55 percent; the roll
        landed under it and a Mech was bought."""
        t = trace("build-b4-day10")
        self.assertEqual(t["builds"][0]["type"], 2)
        r = cpu_trace.predict(t)
        self.assertEqual(r["predicted_builds"][0][2], 2)

    def test_air_and_naval_rows_of_weight_zero_buy_nothing(self):
        for name in ("build-b5-airport", "build-b6-port"):
            with self.subTest(name=name):
                t = trace(name)
                self.assertEqual(t["builds"], [])
                self.assertEqual(cpu_trace.predict(t)["predicted_builds"], [])

    def test_kanbei_pays_the_value_multiplier(self):
        t = trace("build-b10-kanbei")
        r = cpu_trace.predict(t)
        self.assertEqual(r["turn"].builds[0]["price"], 1200)
        before, after = load(FIX / t["before"]), load(FIX / t["after"])
        # 19000 in the bank, the HQ and the written Base paying 9500 each
        self.assertEqual(after.army(1).funds, before.army(1).funds + 19000 - 1200)

    def test_the_mode_roll_follows_the_rows_cumulative_table(self):
        """Rockets' row is [0, 0, 90, 90, 100, 0, 0]: a roll under 90 gives
        mode 3 (b12); the MdTank's [20, 30, 50, 70, 80, 236, 0] gives 6 to a
        roll of 80 or more (b8); a foot soldier's 0xFF row gives 0 (b1)."""
        for name, mode in (("build-b12-fallback-rockets", 3), ("build-b8-five-foot", 6),
                           ("build-b1-base", 0)):
            with self.subTest(name=name):
                t = trace(name)
                self.assertEqual(t["builds"][0]["mode"], mode)


class TestTheFallback(unittest.TestCase):
    """DERIVATION 49: a foot unit with no property left to walk to
    (0x08064D6A) falls into the AI's "nothing to do" routine (0x0806606C),
    which on a map without a Port is settle -- a unit that may stay where
    it stands issues nothing."""

    def cmds(self, name):
        return [(c["name"], c["slot"], (c["x"], c["y"])) for c in trace(name)["commands"]]

    def test_the_foot_unit_left_over_issues_nothing(self):
        # four cities written to P1: three properties for four foot units,
        # and Mech #4, last in slot order, gets none -- no record at all
        slots = [c[1] for c in self.cmds("noprop-foot")]
        self.assertEqual([s for s in slots if s in (1, 2, 3, 4)], [1, 2, 3])
        # all six written: the HQ alone is left, Infantry #1 takes it and
        # Infantry #2 and Mech #4 issue nothing (Mech #3 rolled its attack)
        slots = [c[1] for c in self.cmds("noprop-apc")]
        self.assertNotIn(2, slots)
        self.assertNotIn(4, slots)
        self.assertIn(("wait", 1, (2, 4)), self.cmds("noprop-apc"))

    def test_the_port_reaches_the_fallback_and_issues_nothing_there(self):
        for name, silent in (("noprop-foot", {4}), ("noprop-apc", {2, 4})):
            with self.subTest(name=name):
                r = cpu_trace.predict(trace(name))
                log = "\n".join(r["log"])
                self.assertEqual(log.count("goal None"), len(silent))
                self.assertFalse(silent & {c.slot for c in r["turn"].commands})
                self.assertEqual(r["predicted"], r["traced"])


class TestThePower(unittest.TestCase):
    """DERIVATION 50: the CPU fires its power (0x0801C120) when the meter
    is at its threshold and the CO's predicate allows -- not a dispatcher
    record, no RNG draw, the forward model's activation -- and the turn
    goes on under it."""

    def cmds(self, name):
        return [(c["name"], c["slot"], (c["x"], c["y"])) for c in trace(name)["commands"]]

    def test_andy_and_max_fire_at_the_first_pass(self):
        for name in ("power-andy", "power-max"):
            with self.subTest(name=name):
                r = cpu_trace.predict(trace(name))
                self.assertEqual([p["subphase"] for p in r["turn"].powers], [1])
                after = load(FIX / f"{name}.after.json")
                a = after.army(1)
                self.assertEqual((a.power, a.power_uses, a.power_active), (0, 1, True))
        # Hyper Repair: the 50-hp Infantry reads 70 after
        after = load(FIX / "power-andy.after.json")
        self.assertEqual(sim.unit_in(after, 1).hp, 70)
        # a full-health army leaves Andy's meter alone (the older trace)
        r = cpu_trace.predict(trace("vs15-p1-cpu-power"))
        self.assertEqual(r["turn"].powers, [])

    def test_max_force_moves_the_apc_seven_tiles(self):
        """Direct units +1 move under Max's power: the APC's 7-tile move is
        offered by the action layer only with co.move_bonus in the budget."""
        self.assertIn(("wait", 5, (1, 4)), self.cmds("power-max"))
        before = load(FIX / "power-max.before.json")
        from engine import pathing
        apc = sim.unit_in(before, 5)
        self.assertEqual((apc.x, apc.y), (6, 2))
        self.assertEqual(pathing.allowance(apc), 6)
        fired = dataclasses.replace(before, armies=[
            dataclasses.replace(a, power_active=True) if a.player == 1 else a
            for a in before.armies])
        self.assertEqual(pathing.allowance(apc, fired), 7)
        self.assertIn((1, 4), pathing.destinations(fired, apc))

    def test_eagles_power_at_the_end_pass_sends_the_refreshed_units_round_again(self):
        cmds = self.cmds("power-eagle")
        self.assertEqual(len(cmds), 12)
        again = [c[1] for c in cmds[8:]]
        self.assertEqual(again, [6, 7, 5, 8])            # Recon, Tank, APC, Artillery
        self.assertNotIn(1, again)                       # foot units are not refreshed
        r = cpu_trace.predict(trace("power-eagle"))
        self.assertEqual([p["subphase"] for p in r["turn"].powers], [17])
        self.assertIn("back to sub-phase 1", "\n".join(r["log"]))
        self.assertEqual(r["predicted"], r["traced"])


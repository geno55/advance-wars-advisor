"""The forward model, engine/sim.py: one action in, the next board out.

Hand-built boards, one rule per test, and every number checked against the
module sim composes -- damage, join, supply, economy, co -- called directly
on the same inputs, so what this file pins is the WIRING: the right unit
moved, the right record edited, funds threaded through repairs in slot
order, the meter charged to the measured formula and no further. The action
layer now scores its exposure on boards this module produces, so
tests/test_actions.py exercises it too.

Two measured rules are cited where they bite: moving off a property resets
capture progress and a Wait in place keeps it (A15 and DERIVATION 42,
tests/fixtures/capwait_probes.json); a power block and Olaf's snow expire
at the caster's next turn start (DERIVATION 27).
"""
import dataclasses
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions                                                # noqa: E402
import co                                                     # noqa: E402
import damage                                                 # noqa: E402
import sim                                                    # noqa: E402
import supply                                                 # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

PLAIN, MOUNTAIN, WOOD, ROAD, CITY, SEA, BASE, AIRPORT, SHOAL = 1, 3, 4, 5, 6, 7, 14, 10, 13
CAPWAIT = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                      "capwait_probes.json").read_text(encoding="utf-8"))
ANDY, OLAF, EAGLE, DRAKE, STURM = 1, 3, 8, 9, 10


def board(rows, units=(), owner=None, armies=(), active=1, day=1, **kw):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies), terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0, active_player=active, day=day, fog=False,
                 **kw)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False,
         loaded=False, cargo=0, capture=0, state=0, cargo2=0):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=capture, fuel=fuel, acted=acted,
                carrying=bool(cargo or cargo2), loaded=loaded, state=state,
                cargo=cargo, cargo2=cargo2)


def army(player, funds=10000, co_id=ANDY, power=0, active=False, uses=0):
    return Army(player=player, funds=funds, income=0, power=power, co_id=co_id,
                power_active=active, power_uses=uses, power_ready=False)


def act(b, slot, kind, **match):
    """The one action of `kind` for unit `slot` matching the given fields."""
    u = sim.unit_in(b, slot)
    found = [a for a in actions.actions_for(b, u, warnings=[])
             if a.kind == kind and all(getattr(a, k) == v for k, v in match.items())]
    assert len(found) == 1, (kind, match, len(found))
    return found[0]


class TestMovesAndFlags(unittest.TestCase):
    def test_wait_moves_pays_the_path_and_acts(self):
        b = board([[PLAIN] * 5], [unit("Infantry", 0, 0, fuel=50)])
        after = sim.apply(b, act(b, 1, "wait", tile=(2, 0)))
        me = sim.unit_in(after, 1)
        self.assertEqual((me.x, me.y, me.fuel, me.acted), (2, 0, 48, True))
        self.assertIsNone(after.vision)

    def test_the_input_board_is_untouched(self):
        b = board([[PLAIN] * 5], [unit("Infantry", 0, 0)])
        before = repr(b)
        sim.apply(b, act(b, 1, "wait", tile=(2, 0)))
        self.assertEqual(repr(b), before)

    def test_moving_resets_capture_progress_and_staying_keeps_it(self):
        """A15 (moving resets) and DERIVATION 42 (a Wait in place keeps):
        the probe read 10 before and after the Wait, and the next Capt took
        the city from 10 to 20."""
        rows = {r["case"]: r for r in CAPWAIT["drives"]}
        self.assertEqual(rows["after-wait"]["capture"], 10)
        self.assertEqual(rows["after-cap-day3"]["city_owner"], 1)
        b = board([[CITY, PLAIN]], [unit("Infantry", 0, 0, capture=10)])
        stay = sim.apply(b, act(b, 1, "wait", tile=(0, 0)))
        self.assertEqual(sim.unit_in(stay, 1).capture, 10)
        move = sim.apply(b, act(b, 1, "wait", tile=(1, 0)))
        self.assertEqual(sim.unit_in(move, 1).capture, 0)

    def test_dive_and_rise_toggle_the_bit_and_nothing_else(self):
        b = board([[SEA] * 3], [unit("Sub", 0, 0)])
        down = sim.apply(b, act(b, 1, "dive", tile=(1, 0)))
        me = sim.unit_in(down, 1)
        self.assertTrue(me.dived)
        self.assertEqual((me.x, me.acted, me.hp, me.fuel), (1, True, 100, 98))
        up = sim.apply(dataclasses.replace(down, units=[dataclasses.replace(me, acted=False)]),
                       act(dataclasses.replace(down, units=[dataclasses.replace(me, acted=False)]),
                           1, "rise", tile=(1, 0)))
        self.assertFalse(sim.unit_in(up, 1).dived)

    def test_a_trap_ends_on_the_stop_tile(self):
        b = board([[SEA] * 6], [unit("Cruiser", 4, 0, player=2, slot=71),
                                unit("Sub", 1, 0, player=1, slot=3, state=0x20)])
        a = act(b, 71, "trap", tile=(1, 0))
        after = sim.apply(b, a)
        me = sim.unit_in(after, 71)
        self.assertEqual(((me.x, me.y), me.fuel, me.acted), (a.drop_tile, 97, True))


class TestAttack(unittest.TestCase):
    def pair(self, my_hp=100, their_hp=100, ammo=9, armies=()):
        return board([[PLAIN] * 4],
                     [unit("Tank", 0, 0, slot=1, hp=my_hp, ammo=ammo),
                      unit("Tank", 3, 0, player=2, slot=2, hp=their_hp)],
                     armies=armies)

    def test_min_luck_is_the_action_layers_worst_case(self):
        b = self.pair()
        a = act(b, 1, "attack", tile=(2, 0))
        after = sim.apply(b, a, luck="min")
        self.assertEqual(sim.unit_in(after, 2).hp, a.strike.max_remaining_hp)
        self.assertEqual(sim.unit_in(after, 1).hp, a.hp_after)
        self.assertEqual(sim.unit_in(after, 1).hp, a.counter.min_remaining_hp)

    def test_max_luck_is_the_other_end_of_the_envelope(self):
        b = self.pair()
        a = act(b, 1, "attack", tile=(2, 0))
        after = sim.apply(b, a, luck="max")
        self.assertEqual(sim.unit_in(after, 2).hp, a.strike.min_remaining_hp)
        self.assertEqual(sim.unit_in(after, 1).hp, a.counter.max_remaining_hp)

    def test_an_int_roll_lands_between(self):
        b = self.pair()
        a = act(b, 1, "attack", tile=(2, 0))
        hps = [sim.unit_in(sim.apply(b, a, luck=lk), 2).hp for lk in range(10)]
        self.assertEqual(hps, sorted(hps, reverse=True))
        self.assertEqual(hps[0], a.strike.max_remaining_hp)
        self.assertEqual(hps[-1], a.strike.min_remaining_hp)
        with self.assertRaises(ValueError):
            sim.apply(b, a, luck=10)

    def test_primary_shots_spend_a_round_on_both_sides(self):
        b = self.pair()
        after = sim.apply(b, act(b, 1, "attack", tile=(2, 0)))
        self.assertEqual(sim.unit_in(after, 1).ammo, 8)
        self.assertEqual(sim.unit_in(after, 2).ammo, 8)     # the counter, too

    def test_a_secondary_spends_nothing(self):
        b = board([[PLAIN] * 4], [unit("Infantry", 0, 0, slot=1, ammo=0),
                                  unit("Infantry", 3, 0, player=2, slot=2, ammo=0)])
        after = sim.apply(b, act(b, 1, "attack", tile=(2, 0)))
        self.assertEqual(sim.unit_in(after, 1).ammo, 0)

    def test_an_indirect_strike_draws_no_counter_and_stays_put(self):
        b = board([[PLAIN] * 5], [unit("Artillery", 0, 0, slot=1),
                                  unit("Tank", 2, 0, player=2, slot=2)])
        a = act(b, 1, "attack")
        after = sim.apply(b, a)
        me = sim.unit_in(after, 1)
        self.assertEqual((me.x, me.hp), (0, 100))
        self.assertLess(sim.unit_in(after, 2).hp, 100)

    def test_a_kill_removes_the_target_and_its_passenger(self):
        b = board([[PLAIN, PLAIN, PLAIN, ROAD]],
                  [unit("MdTank", 0, 0, slot=1),
                   unit("APC", 3, 0, player=2, slot=2, cargo=3),
                                  unit("Infantry", 3, 0, player=2, slot=3, loaded=True)])
        a = act(b, 1, "attack", tile=(2, 0))
        self.assertTrue(a.strike.guaranteed_kill)
        after = sim.apply(b, a)
        self.assertEqual({u.slot for u in after.units}, {1})

    def test_the_meters_charge_to_the_measured_formula(self):
        b = self.pair(armies=[army(1), army(2)])
        a = act(b, 1, "attack", tile=(2, 0))
        after = sim.apply(b, a, luck="min")
        res = sim.battle(dataclasses.replace(b, units=[
            dataclasses.replace(sim.unit_in(b, 1), x=2), sim.unit_in(b, 2)]),
            dataclasses.replace(sim.unit_in(b, 1), x=2), sim.unit_in(b, 2))
        value = co.unit_value(7000, ANDY)
        want = co.charge_gains(value, value,
                               damage.display_hp(100) - damage.display_hp(res.attacker_hp),
                               damage.display_hp(100) - damage.display_hp(res.defender_hp))
        self.assertEqual((after.army(1).power, after.army(2).power), want)

    def test_an_active_power_takes_no_charge_and_the_rest_clamps(self):
        thr = co.power_threshold(ANDY, 0)
        b = self.pair(armies=[army(1, active=True), army(2, power=thr - 1)])
        after = sim.apply(b, act(b, 1, "attack", tile=(2, 0)))
        self.assertEqual(after.army(1).power, 0)
        self.assertEqual(after.army(2).power, thr)

    def test_a_power_block_reaches_the_quote_on_both_layers(self):
        """Andy's block is 110/90 universal; with it up, the action's
        envelope and sim's point both move, and still agree."""
        calm = self.pair(armies=[army(1), army(2)])
        hot = self.pair(armies=[army(1, active=True), army(2)])
        a_calm, a_hot = act(calm, 1, "attack", tile=(2, 0)), act(hot, 1, "attack", tile=(2, 0))
        self.assertGreater(a_hot.strike.min_damage, a_calm.strike.min_damage)
        self.assertEqual(sim.unit_in(sim.apply(hot, a_hot), 2).hp,
                         a_hot.strike.max_remaining_hp)


class TestTheOtherKinds(unittest.TestCase):
    def test_capture_adds_the_gain_and_the_city_falls_at_twenty(self):
        b = board([[CITY]], [unit("Infantry", 0, 0, capture=10)])
        after = sim.apply(b, act(b, 1, "capture"))
        self.assertEqual(after.owner[0][0], 1)
        self.assertEqual(sim.unit_in(after, 1).capture, 0)
        b2 = board([[CITY]], [unit("Infantry", 0, 0, hp=70)])
        after2 = sim.apply(b2, act(b2, 1, "capture"))
        self.assertEqual((after2.owner[0][0], sim.unit_in(after2, 1).capture), (0, 7))

    def test_sami_captures_faster_through_co_ids(self):
        b = board([[CITY]], [unit("Infantry", 0, 0)])
        after = sim.apply(b, act(b, 1, "capture"), co_ids={1: 4})
        self.assertEqual(sim.unit_in(after, 1).capture, 15)

    def test_supply_fills_the_neighbours_it_named(self):
        b = board([[PLAIN] * 3], [unit("APC", 0, 0, slot=1),
                                  unit("Tank", 2, 0, slot=2, fuel=10, ammo=1)])
        after = sim.apply(b, act(b, 1, "supply", tile=(1, 0)))
        t = sim.unit_in(after, 2)
        self.assertEqual((t.fuel, t.ammo), supply.resupply_caps("Tank"))
        self.assertTrue(sim.unit_in(after, 1).acted)

    def test_load_puts_the_passenger_aboard(self):
        b = board([[PLAIN] * 3], [unit("Infantry", 0, 0, slot=1), unit("APC", 1, 0, slot=2)])
        after = sim.apply(b, act(b, 1, "load"))
        p, ride = sim.unit_in(after, 1), sim.unit_in(after, 2)
        self.assertEqual((p.x, p.loaded, p.acted), (1, True, True))
        self.assertEqual((ride.cargo, ride.carrying), (1, True))
        self.assertIsNone(after.unit_at(0, 0))
        self.assertEqual(after.unit_at(1, 0).slot, 2)

    def test_a_second_passenger_takes_the_second_slot(self):
        b = board([[SEA, SHOAL, PLAIN]], [unit("Lander", 1, 0, slot=2, cargo=3),
                                        unit("Tank", 1, 0, slot=3, loaded=True),
                                        unit("Infantry", 2, 0, slot=1)])
        after = sim.apply(b, act(b, 1, "load"))
        ride = sim.unit_in(after, 2)
        self.assertEqual((ride.cargo, ride.cargo2), (3, 1))

    def test_drop_puts_the_passenger_down_acted(self):
        b = board([[PLAIN] * 3], [unit("APC", 0, 0, slot=1, cargo=2),
                                  unit("Infantry", 0, 0, slot=2, loaded=True)])
        a = act(b, 1, "drop", tile=(1, 0), drop_tile=(2, 0))
        after = sim.apply(b, a)
        p, ride = sim.unit_in(after, 2), sim.unit_in(after, 1)
        self.assertEqual(((p.x, p.y), p.loaded, p.acted), ((2, 0), False, True))
        self.assertEqual((ride.x, ride.cargo, ride.carrying, ride.acted), (1, 0, False, True))

    def test_join_merges_refunds_and_removes_the_partner(self):
        b = board([[PLAIN] * 3], [unit("Tank", 0, 0, slot=1, hp=70, fuel=30),
                                  unit("Tank", 1, 0, slot=2, hp=60, fuel=30)],
                  armies=[army(1, funds=1000)])
        a = act(b, 1, "join")
        after = sim.apply(b, a)
        self.assertIsNone(sim.unit_in(after, 2))
        me = sim.unit_in(after, 1)
        self.assertEqual((me.x, me.hp, me.fuel, me.acted), (1, 100, 59, True))
        self.assertEqual(after.army(1).funds, 1000 + a.merge.refund)
        self.assertGreater(a.merge.refund, 0)

    def test_build_adds_the_unit_and_pays(self):
        b = board([[BASE]], owner=[[1]], armies=[army(1, funds=5000)])
        a = next(x for x in actions.build_actions(b, 1, warnings=[])
                 if x.build_type == "Infantry")
        after = sim.apply(b, a)
        self.assertEqual(after.unit_at(0, 0).type, "Infantry")
        self.assertTrue(after.unit_at(0, 0).acted)
        self.assertEqual(after.army(1).funds, 4000)


class TestPower(unittest.TestCase):
    def charged(self, cid, units, rows=None, owner=None):
        thr = co.power_threshold(cid, 0)
        return board(rows or [[PLAIN] * 5], units, owner=owner,
                     armies=[army(1, co_id=cid, power=thr), army(2, co_id=ANDY)])

    def test_activation_spends_the_meter_and_raises_the_block(self):
        b = self.charged(ANDY, [unit("Tank", 0, 0, hp=55)])
        a = actions.power_action(b, 1, warnings=[])
        after = sim.apply(b, a)
        ar = after.army(1)
        self.assertEqual((ar.power, ar.power_uses, ar.power_active), (0, 1, True))
        self.assertEqual(sim.unit_in(after, 1).hp, a.power.heals[0][1])
        self.assertEqual(sim.unit_in(after, 1).hp, 80)      # 55 -> +2 bars, snapped

    def test_eagle_refreshes_drake_hurts_olaf_snows(self):
        b = self.charged(EAGLE, [unit("Tank", 0, 0, acted=True),
                                 unit("Infantry", 1, 0, slot=2, acted=True)])
        after = sim.apply(b, actions.power_action(b, 1, warnings=[]))
        self.assertFalse(sim.unit_in(after, 1).acted)
        self.assertTrue(sim.unit_in(after, 2).acted)            # foot: not refreshed
        b = self.charged(DRAKE, [unit("Tank", 0, 0), unit("Tank", 3, 0, player=2, slot=2, hp=5)])
        after = sim.apply(b, actions.power_action(b, 1, warnings=[]))
        self.assertEqual((sim.unit_in(after, 1).hp, sim.unit_in(after, 2).hp), (100, 1))
        b = self.charged(OLAF, [unit("Tank", 0, 0)])
        after = sim.apply(b, actions.power_action(b, 1, warnings=[]))
        self.assertEqual(after.weather_index, sim.SNOW_INDEX)

    def test_sturm_needs_a_strategy_or_says_so(self):
        b = self.charged(STURM, [unit("Tank", 0, 0),
                                 unit("Tank", 3, 0, player=2, slot=2),
                                 unit("Infantry", 4, 0, player=2, slot=3)])
        a = actions.power_action(b, 1, warnings=[])
        w = []
        after = sim.apply(b, a, meteor_strategy=None, warnings=w)
        self.assertTrue(any("strategy 0" in x for x in w))
        want = {u.slot: hp for u, hp in a.power.meteors[0][2]}
        for slot, hp in want.items():
            self.assertEqual(sim.unit_in(after, slot).hp, hp)

    def test_the_block_and_the_snow_expire_at_the_casters_next_turn(self):
        b = self.charged(OLAF, [unit("Tank", 0, 0), unit("Tank", 4, 0, player=2, slot=2)])
        after = sim.apply(b, actions.power_action(b, 1, warnings=[]))
        p2 = sim.end_turn(after)                                # P2's turn: still up
        self.assertTrue(p2.army(1).power_active)
        self.assertEqual(p2.weather_index, sim.SNOW_INDEX)
        p1 = sim.end_turn(p2)                                   # P1 again: gone
        self.assertFalse(p1.army(1).power_active)
        self.assertEqual(p1.weather_index, sim.CLEAR_INDEX)


class TestEndTurn(unittest.TestCase):
    def test_the_turn_passes_and_the_day_turns_over_on_the_wrap(self):
        b = board([[PLAIN] * 2], [unit("Tank", 0, 0, acted=True),
                                  unit("Tank", 1, 0, player=2, slot=2, acted=True)],
                  armies=[army(1), army(2)], active=1, day=3)
        p2 = sim.end_turn(b)
        self.assertEqual((p2.active_player, p2.day), (2, 3))
        self.assertTrue(sim.unit_in(p2, 1).acted)               # P1's stay acted
        self.assertFalse(sim.unit_in(p2, 2).acted)              # P2's cleared
        p1 = sim.end_turn(p2)
        self.assertEqual((p1.active_player, p1.day), (1, 4))
        self.assertFalse(sim.unit_in(p1, 1).acted)

    def test_income_is_paid_before_repairs_and_funds_thread_in_slot_order(self):
        """Two damaged Tanks on own Cities, 700 in the bank, a 1000 rate and
        one property: income lands first (1700), the lower slot repairs
        first, and supply.turn_start called with the treasury as it stands
        at each unit reproduces every field."""
        rows = [[CITY, CITY]]
        owner = [[1, 1]]
        b = board(rows, [unit("Tank", 0, 0, slot=1, hp=40),
                         unit("Tank", 1, 0, slot=2, hp=40)],
                  owner=owner, armies=[army(1, funds=700), army(2)], active=2,
                  funds_per_property=1000, repair_free=False)
        after = sim.end_turn(b)
        funds = 700 + 2 * 1000
        want = {}
        for slot in (1, 2):
            ts = supply.turn_start("Tank", hp=40, fuel=99, ammo=9, terrain_id=CITY,
                                   tile_owner=1, player=1, funds=funds, charge=True,
                                   co_id=ANDY)
            funds -= ts.repair_spent
            want[slot] = ts.hp_after
        self.assertEqual({u.slot: u.hp for u in after.units}, want)
        self.assertEqual(after.army(1).funds, funds)
        self.assertGreater(want[1], want[2])                     # the second ran short

    def test_burn_crash_and_auto_supply(self):
        b = board([[PLAIN] * 4], [unit("BCopter", 0, 0, slot=1, fuel=3),
                                  unit("BCopter", 1, 0, slot=2, fuel=2),
                                  unit("APC", 3, 0, slot=3),
                                  unit("Tank", 2, 0, slot=4, fuel=5, ammo=0)],
                  armies=[army(1), army(2)], active=2)
        after = sim.end_turn(b)
        self.assertEqual(sim.unit_in(after, 1).fuel, 1)         # burned 2, lives
        self.assertIsNone(sim.unit_in(after, 2))                # burned to 0: gone
        t = sim.unit_in(after, 4)
        self.assertEqual((t.fuel, t.ammo), supply.resupply_caps("Tank"))

    def test_a_passenger_burns_nothing(self):
        b = board([[PLAIN] * 2], [unit("APC", 0, 0, slot=1, cargo=2),
                                  unit("Infantry", 0, 0, slot=2, loaded=True, fuel=3)],
                  armies=[army(1), army(2)], active=2)
        after = sim.end_turn(b)
        self.assertEqual(sim.unit_in(after, 2).fuel, 3)


class TestNoUnitTypeBranches(unittest.TestCase):
    def test_sim_names_no_unit_type(self):
        src = (ROOT / "engine" / "sim.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        code = code.split('"""')[0] + '"""'.join(code.split('"""')[2::2])
        names = json.loads((ROOT / "data" / "aw1_unit_stats.json")
                           .read_text(encoding="utf-8"))["units"]
        for name in names:
            self.assertNotIn(f'"{name}"', code, f"{name} is named in sim.py")
            self.assertNotIn(f"'{name}'", code, f"{name} is named in sim.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

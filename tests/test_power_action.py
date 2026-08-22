"""CO power activation as an action -- the menu gate replayed, the effects
composed.

tests/fixtures/power_menu.json holds the map-menu rows driven in
DERIVATION 37: which army fields open the Power item. engine/power.py must
reproduce that gate, and its effect facts must agree with what DERIVATION
27/30 measured (the heal through the repair routine, Drake's floor at 1,
Eagle's non-foot refresh, the three meteor candidates).
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions                                                # noqa: E402
import co as co_mod                                           # noqa: E402
import power                                                  # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                  "power_menu.json").read_text(encoding="utf-8"))
PLAIN = 1
ANDY, OLAF, GRIT, KANBEI, EAGLE, DRAKE, STURM = 1, 3, 5, 6, 8, 9, 10


def board(rows, units=(), armies=()):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies), terrain=[list(r) for r in rows],
                 owner=[[0] * len(rows[0]) for _ in rows],
                 weather_index=0, repair_free=True)


def unit(utype, x, y, player=1, slot=1, hp=100, acted=False, loaded=False):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=9, capture=0, fuel=99, acted=acted, carrying=False,
                loaded=loaded, state=0, cargo=0)


def army(player, co, meter=0, uses=0, active=False, ready=None):
    return Army(player=player, funds=19000, income=0, power=meter, co_id=co,
                power_active=active, power_uses=uses, power_ready=ready)


class TestMenuGate(unittest.TestCase):
    def test_every_measured_row(self):
        """Meter vs the uses-scaled threshold decides; the latch does not."""
        for row in FIX["rows"]:
            with self.subTest(row["case"]):
                b = board([[PLAIN]], armies=[
                    army(1, ANDY, meter=row["meter"], uses=row["uses"],
                         active=bool(row["active"]), ready=bool(row["latch"]))])
                a = power.activation(b, 1, warnings=[])
                # the game shows the item whenever meter >= threshold; the
                # model's `available` additionally excludes a running power,
                # which the fixture marks so the reasoning stays visible
                shown = a.meter >= a.threshold
                self.assertEqual(shown, row["power_item"])
                self.assertEqual(a.available,
                                 row["power_item"] and not row["active"])

    def test_threshold_scales_with_uses(self):
        self.assertEqual(co_mod.power_threshold(ANDY, 0), 30000)
        self.assertEqual(co_mod.power_threshold(ANDY, 1), 36000)
        b = board([[PLAIN]], armies=[army(1, ANDY, meter=35990, uses=1)])
        self.assertFalse(power.activation(b, 1, warnings=[]).available)
        b = board([[PLAIN]], armies=[army(1, ANDY, meter=36000, uses=1)])
        self.assertTrue(power.activation(b, 1, warnings=[]).available)


class TestEffects(unittest.TestCase):
    def test_andy_heals_through_the_repair_routine(self):
        # 45 -> 55 -> 65 -> snap 70, free: the same routine as property repair
        b = board([[PLAIN, PLAIN]],
                  [unit("Tank", 0, 0, hp=45), unit("Infantry", 1, 0, slot=2,
                                                    player=2, hp=45)],
                  armies=[army(1, ANDY, meter=30000), army(2, ANDY)])
        a = power.activation(b, 1, warnings=[])
        self.assertEqual([(u.slot, hp) for u, hp in a.heals], [(1, 70)])

    def test_drake_damages_every_enemy_floored_at_one(self):
        b = board([[PLAIN, PLAIN, PLAIN]],
                  [unit("Tank", 0, 0), unit("Tank", 1, 0, slot=2, player=2, hp=55),
                   unit("Mech", 2, 0, slot=3, player=2, hp=5)],
                  armies=[army(1, DRAKE, meter=40000), army(2, ANDY)])
        a = power.activation(b, 1, warnings=[])
        self.assertEqual(sorted((u.slot, hp) for u, hp in a.damages),
                         [(2, 45), (3, 1)])

    def test_eagle_refreshes_acted_non_foot_only(self):
        b = board([[PLAIN] * 4],
                  [unit("Tank", 0, 0, acted=True), unit("Infantry", 1, 0, slot=2, acted=True),
                   unit("BCopter", 2, 0, slot=3, acted=False),
                   unit("Tank", 3, 0, slot=4, player=2, acted=True)],
                  armies=[army(1, EAGLE, meter=50000), army(2, ANDY)])
        a = power.activation(b, 1, warnings=[])
        self.assertEqual([u.slot for u in a.refreshes], [1])

    def test_sturm_lists_three_candidate_meteors(self):
        b = board([[PLAIN] * 5],
                  [unit("Tank", 0, 0), unit("Tank", 4, 0, slot=2, player=2)],
                  armies=[army(1, STURM, meter=50000), army(2, ANDY)])
        a = power.activation(b, 1, warnings=[])
        self.assertEqual([s for s, _, _ in a.meteors], [0, 1, 2])
        self.assertTrue(all(c is not None for _, c, _ in a.meteors))
        self.assertTrue(any("RNG" in n for n in a.notes))

    def test_olaf_snow_and_the_stat_block(self):
        b = board([[PLAIN]], armies=[army(1, OLAF, meter=30000)])
        a = power.activation(b, 1, warnings=[])
        self.assertEqual(a.weather, "snow")
        self.assertEqual(a.universal, co_mod.universal(OLAF, power=True))
        self.assertEqual(a.next_threshold, 36000)


class TestPowerAction(unittest.TestCase):
    def test_offered_only_when_the_meter_is_there(self):
        short = board([[PLAIN]], armies=[army(1, ANDY, meter=29999)])
        self.assertIsNone(actions.power_action(short, 1, warnings=[]))
        full = board([[PLAIN]], armies=[army(1, ANDY, meter=30000)])
        act = actions.power_action(full, 1, warnings=[])
        self.assertEqual(act.kind, "power")
        self.assertEqual(act.power.co_name, "Andy")

    def test_unknown_co_is_none_out_loud(self):
        b = board([[PLAIN]], armies=[Army(player=1, funds=0, income=0, power=99999)])
        w = []
        self.assertIsNone(actions.power_action(b, 1, warnings=w))
        self.assertTrue(any("CO is unknown" in n for n in w))


if __name__ == "__main__":
    unittest.main()

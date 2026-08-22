"""Production -- the measured shops and purchases replayed.

tests/fixtures/build_probes.json is the spec: three shops read off the
screen, six purchases/refusals driven on written factories (DERIVATION 36).
engine/production.py must reproduce the lists, the prices (Andy and Kanbei),
the affordability edge, and the slot allocation; actions.build_actions must
offer exactly the own empty factories' shops with the new unit's exposure.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions                                                # noqa: E402
import production                                             # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                  "build_probes.json").read_text(encoding="utf-8"))
PLAIN, CITY, HQ, AIRPORT, PORT, BASE, SEA = 1, 6, 8, 10, 11, 14, 7
TID = {"Base": BASE, "Airport": AIRPORT, "Port": PORT}


def board(rows, units=(), owner=None, armies=()):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies), terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0, repair_free=True)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=0, fuel=fuel, acted=acted, carrying=False,
                loaded=False, state=0, cargo=0)


def armies(funds=19000, co=1):
    return [Army(player=1, funds=funds, income=0, co_id=co),
            Army(player=2, funds=funds, income=0, co_id=co)]


class TestShops(unittest.TestCase):
    def test_each_factory_sells_the_measured_list_in_order(self):
        for name, want in FIX["shops"].items():
            with self.subTest(name):
                self.assertEqual(production.shop(TID[name]), want)

    def test_non_factories_sell_nothing(self):
        for t in (PLAIN, CITY, HQ, SEA):
            self.assertEqual(production.shop(t), [])
            self.assertFalse(production.is_factory(t))

    def test_prices_as_seen_on_screen(self):
        for co_name, co in (("Andy", 1), ("Kanbei", 6)):
            for utype, want in FIX["prices_seen"][co_name].items():
                with self.subTest(co=co_name, unit=utype):
                    self.assertEqual(production.price(utype, co), want)

    def test_affordability_is_greater_or_equal(self):
        offs = {o.unit_type: o for o in production.offers(BASE, 1000, 1)}
        self.assertTrue(offs["Infantry"].affordable)
        self.assertFalse(offs["Mech"].affordable)
        self.assertFalse(production.offers(BASE, 900, 1)[0].affordable)


class TestSlots(unittest.TestCase):
    def test_lowest_free_slot_in_the_army_block(self):
        b = board([[PLAIN] * 3],
                  [unit("Infantry", 0, 0, player=2, slot=66),
                   unit("Infantry", 1, 0, player=2, slot=67)])
        self.assertEqual(production.free_slot(b, 2), 65)   # 64 is the base
        b2 = board([[PLAIN] * 3],
                   [unit("Infantry", 0, 0, player=2, slot=65),
                    unit("Infantry", 1, 0, player=2, slot=66)])
        self.assertEqual(production.free_slot(b2, 2), 67)

    def test_fifty_units_is_the_cap(self):
        us = [unit("Infantry", 0, 0, player=1, slot=s) for s in range(1, 51)]
        b = board([[PLAIN]], us)
        self.assertIsNone(production.free_slot(b, 1))
        us49 = us[:-1]
        self.assertEqual(production.free_slot(board([[PLAIN]], us49), 1), 50)


class TestBuildActions(unittest.TestCase):
    def test_own_empty_factories_only(self):
        b = board([[BASE, BASE, BASE, AIRPORT]],
                  [unit("Tank", 1, 0, slot=1)],
                  owner=[[1, 1, 2, 1]], armies=armies())
        acts = actions.build_actions(b, 1, warnings=[])
        tiles = {a.tile for a in acts}
        self.assertEqual(tiles, {(0, 0), (3, 0)})      # (1,0) occupied, (2,0) enemy
        base = [a.build_type for a in acts if a.tile == (0, 0)]
        self.assertEqual(base, FIX["shops"]["Base"])
        air = [a.build_type for a in acts if a.tile == (3, 0)]
        self.assertEqual(air, FIX["shops"]["Airport"])

    def test_offers_carry_price_affordability_and_the_new_unit(self):
        b = board([[BASE, PLAIN]], owner=[[1, 0]], armies=armies(funds=1000))
        acts = actions.build_actions(b, 1, warnings=[])
        inf = next(a for a in acts if a.build_type == "Infantry")
        self.assertEqual((inf.cost, inf.affordable), (1000, True))
        mech = next(a for a in acts if a.build_type == "Mech")
        self.assertEqual((mech.cost, mech.affordable), (3000, False))
        self.assertTrue(inf.target.acted)
        self.assertEqual((inf.target.hp, inf.target.fuel, inf.target.slot),
                         (100, 99, 1))
        self.assertIsNotNone(inf.exposure)
        self.assertIsNotNone(inf.turn_start)

    def test_kanbei_pays_more_and_a_full_army_builds_nothing(self):
        b = board([[BASE]], owner=[[1]], armies=armies(co=6))
        inf = next(a for a in actions.build_actions(b, 1, warnings=[])
                   if a.build_type == "Infantry")
        self.assertEqual(inf.cost, 1200)
        full = board([[BASE] + [PLAIN] * 50],
                     [unit("Infantry", i, 0, slot=i) for i in range(1, 51)],
                     owner=[[1] + [0] * 50], armies=armies())
        w = []
        self.assertEqual(actions.build_actions(full, 1, warnings=w), [])
        self.assertTrue(any("50" in n for n in w))


if __name__ == "__main__":
    unittest.main()

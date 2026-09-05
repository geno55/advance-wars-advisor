"""The CO's movement table under weather (DERIVATION 54): a CO record's
+0x10 pointers map each weather index to one of the seven tables at
0x08284548, and Board.move_cost applies the mover's CO's map. Olaf's
units pay clear costs in snow; Sami's foot units pay 1 everywhere under her
power; everyone else pays the weather's table.
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import pathing                                                # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

PLAIN, MOUNTAIN, WOOD = 1, 3, 4
FOOT = pathing.unit_stats("Infantry")["move_type"]
ANDY, OLAF, SAMI = 1, 3, 4
CLEAR, SNOW = 0, 1


def board(rows, units, cos, weather_index=CLEAR, power=()):
    armies = [Army(player=p, funds=0, income=0, co_id=c, power_uses=0,
                   power_ready=False, power_active=(p in power))
              for p, c in cos.items()]
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=armies, terrain=[list(r) for r in rows],
                 owner=[[0] * len(rows[0]) for _ in rows], weather_index=weather_index,
                 active_player=1, day=1, fog=False, funds_per_property=1000,
                 repair_free=False)


def unit(utype, x, y, player=1, slot=1):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=100, ammo=9,
                capture=0, fuel=99, acted=False, carrying=False, loaded=False,
                state=0, cargo=0)


class TestTheWeatherTables(unittest.TestCase):
    def test_seven_tables_and_the_records_maps(self):
        import co
        import json
        mc = json.loads((ROOT / "data" / "aw1_movecost.json").read_text(encoding="utf-8"))
        self.assertEqual([t["weather"] for t in mc["tables"]],
                         ["Clear", "Snow", "Rain", "Clear (Sami)", "Snow (Sami)",
                          "Rain (Sami)", "Sturm"])
        self.assertEqual(co.record(OLAF).weather_tables, [0, 0, 1])
        self.assertEqual(co.record(SAMI, power=True).weather_tables, [3, 4, 5])
        self.assertEqual(co.record(ANDY).weather_tables, [0, 1, 2])

    def test_olafs_units_pay_clear_costs_in_snow(self):
        rows = [[PLAIN] * 7]
        andy = board(rows, [unit("Infantry", 0, 0)], {1: ANDY, 2: OLAF}, SNOW)
        olaf = board(rows, [unit("Infantry", 0, 0, player=2, slot=70)], {1: ANDY, 2: OLAF}, SNOW)
        clear = board(rows, [unit("Infantry", 0, 0)], {1: ANDY, 2: OLAF}, CLEAR)
        far = lambda b, u: max(x for (x, y) in pathing.reachable(b, u))  # noqa: E731
        self.assertEqual(far(clear, clear.units[0]), 3)
        self.assertEqual(far(olaf, olaf.units[0]), 3)          # snow costs him nothing
        self.assertLess(far(andy, andy.units[0]), 3)           # plains cost 2 in snow
        self.assertEqual(andy.move_cost(1, 0, FOOT), 2)
        self.assertEqual(andy.move_cost(1, 0, FOOT, co_id=OLAF), 1)

    def test_samis_foot_units_pay_one_everywhere_under_her_power(self):
        rows = [[PLAIN, MOUNTAIN, MOUNTAIN, WOOD, PLAIN]]
        off = board(rows, [unit("Infantry", 0, 0)], {1: SAMI, 2: ANDY})
        on = board(rows, [unit("Infantry", 0, 0)], {1: SAMI, 2: ANDY}, power=(1,))
        self.assertEqual(off.move_cost(1, 0, FOOT, co_id=SAMI), 2)          # a mountain
        self.assertEqual(on.move_cost(1, 0, FOOT, co_id=SAMI, power=True), 1)
        self.assertEqual(max(x for (x, y) in pathing.reachable(off, off.units[0])), 1)
        # under her power the table AND her foot units' +1 move (co.move_bonus,
        # DERIVATION 50) apply: four tiles on 3 movement points plus one
        self.assertEqual(max(x for (x, y) in pathing.reachable(on, on.units[0])), 4)


if __name__ == "__main__":
    unittest.main()

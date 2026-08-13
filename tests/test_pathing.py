"""Regression tests for the movement search.

Boards are built by hand from terrain ids so each test isolates one rule. The
point of most of these is that the SAME function produces different answers for
different units purely from table data -- if someone adds a unit-type branch to
pathing.py to make one of these pass, the others should start failing.
"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "engine"))

import pathing                                              # noqa: E402
from state import Board, Unit                               # noqa: E402

PLAIN, RIVER, MOUNTAIN, WOOD, ROAD, SEA, SHOAL = 1, 2, 3, 4, 5, 7, 13


def board(rows, units=(), weather_index=0):
    """rows is a list of lists of terrain ids; units is a list of Unit."""
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=[], terrain=[list(r) for r in rows],
                 owner=[[0] * len(rows[0]) for _ in rows],
                 weather_index=weather_index)


def unit(utype, x, y, player=1, slot=1, fuel=99, acted=False, loaded=False,
         cargo=0):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=100, ammo=9,
                capture=0, fuel=fuel, acted=acted, carrying=bool(cargo),
                loaded=loaded, state=0, cargo=cargo)


class TestTablesDriveEverything(unittest.TestCase):
    def test_same_function_different_answers_per_move_type(self):
        """One row of plains. Infantry moves 3, Tank 6, Recon 8 -- but Recon
        pays 2 per plain on tires, so it does NOT simply go furthest."""
        rows = [[PLAIN] * 12]
        got = {}
        for i, utype in enumerate(("Infantry", "Tank", "Recon")):
            b = board(rows, [unit(utype, 0, 0, slot=i + 1)])
            got[utype] = max(x for (x, _) in pathing.destinations(b, b.units[0]))
        self.assertEqual(got["Infantry"], 3)     # move 3, cost 1 each
        self.assertEqual(got["Tank"], 6)         # move 6, cost 1 each
        self.assertEqual(got["Recon"], 4)        # move 8, cost 2 each on tires

    def test_terrain_cost_comes_from_the_table(self):
        """Infantry pays 2 for mountain, so a mountain eats two of its three."""
        b = board([[PLAIN, MOUNTAIN, PLAIN, PLAIN]], [unit("Infantry", 0, 0)])
        d = pathing.destinations(b, b.units[0])
        self.assertEqual(d[(1, 0)], 2)
        self.assertEqual(d[(2, 0)], 3)
        self.assertNotIn((3, 0), d)

    def test_impassable_is_impassable(self):
        """Treads cannot enter a river; the tile beyond is unreachable too."""
        b = board([[PLAIN, RIVER, PLAIN]], [unit("Tank", 0, 0)])
        d = pathing.destinations(b, b.units[0])
        self.assertNotIn((1, 0), d)
        self.assertNotIn((2, 0), d)

    def test_infantry_fords_the_river_a_tank_cannot(self):
        """Same board, same function, different table row."""
        rows = [[PLAIN, RIVER, PLAIN]]
        inf = board(rows, [unit("Infantry", 0, 0)])
        tank = board(rows, [unit("Tank", 0, 0)])
        self.assertIn((2, 0), pathing.destinations(inf, inf.units[0]))
        self.assertNotIn((2, 0), pathing.destinations(tank, tank.units[0]))

    def test_naval_and_air_use_their_own_rows(self):
        rows = [[SEA, SEA, PLAIN]]
        cru = board(rows, [unit("Cruiser", 0, 0)])
        fig = board(rows, [unit("Fighter", 0, 0)])
        self.assertNotIn((2, 0), pathing.destinations(cru, cru.units[0]))
        self.assertIn((2, 0), pathing.destinations(fig, fig.units[0]))


class TestFuel(unittest.TestCase):
    def test_fuel_caps_movement(self):
        b = board([[PLAIN] * 10], [unit("Tank", 0, 0, fuel=3)])
        self.assertEqual(pathing.allowance(b.units[0]), 3)
        self.assertEqual(max(x for (x, _) in pathing.destinations(b, b.units[0])), 3)

    def test_empty_tank_can_only_stay(self):
        b = board([[PLAIN] * 5], [unit("Tank", 2, 0, fuel=0)])
        self.assertEqual(pathing.destinations(b, b.units[0]), {(2, 0): 0})


class TestUnitsOnTheBoard(unittest.TestCase):
    def test_enemy_blocks_passage_entirely(self):
        b = board([[PLAIN] * 6],
                  [unit("Tank", 0, 0, player=1, slot=1),
                   unit("Infantry", 2, 0, player=2, slot=2)])
        d = pathing.destinations(b, b.units[0])
        self.assertIn((1, 0), d)
        self.assertNotIn((2, 0), d)
        self.assertNotIn((3, 0), d)     # the block is total, not just the tile

    def test_friendly_can_be_crossed_but_not_stopped_on(self):
        b = board([[PLAIN] * 6],
                  [unit("Tank", 0, 0, player=1, slot=1),
                   unit("Infantry", 2, 0, player=1, slot=2)])
        through = pathing.reachable(b, b.units[0])
        ends = pathing.destinations(b, b.units[0])
        self.assertIn((2, 0), through)
        self.assertNotIn((2, 0), ends)
        self.assertIn((3, 0), ends)     # crossing it is fine

    def test_staying_put_is_always_a_destination(self):
        b = board([[PLAIN]], [unit("Infantry", 0, 0)])
        self.assertIn((0, 0), pathing.destinations(b, b.units[0]))

    def test_a_passenger_has_no_moves_of_its_own(self):
        b = board([[PLAIN] * 4],
                  [unit("APC", 1, 0, slot=1, cargo=2),
                   unit("Infantry", 1, 0, slot=2, loaded=True)])
        self.assertEqual(pathing.destinations(b, b.units[1]), {})


class TestLoading(unittest.TestCase):
    def test_infantry_may_end_on_a_friendly_apc(self):
        b = board([[PLAIN] * 4],
                  [unit("Infantry", 0, 0, slot=1),
                   unit("APC", 1, 0, slot=2)])
        self.assertIn((1, 0), pathing.destinations(b, b.units[0]))

    def test_a_tank_may_not_board_an_apc(self):
        """Cargo mask, not a unit-type check: the APC's mask admits foot only."""
        b = board([[PLAIN] * 4],
                  [unit("Tank", 0, 0, slot=1), unit("APC", 1, 0, slot=2)])
        self.assertNotIn((1, 0), pathing.destinations(b, b.units[0]))

    def test_a_tank_boards_a_lander_beached_on_a_shoal(self):
        b = board([[PLAIN, SHOAL, SEA]],
                  [unit("Tank", 0, 0, slot=1), unit("Lander", 1, 0, slot=2)])
        self.assertIn((1, 0), pathing.destinations(b, b.units[0]))

    def test_a_tank_cannot_board_a_lander_at_sea(self):
        """No rule in the code says this. The Tank simply cannot enter a Sea
        tile, so the transport is unreachable and loading never comes up --
        the terrain table enforces it for free. Worth a test precisely because
        it is the kind of case someone would otherwise hand-code."""
        b = board([[PLAIN, SEA, SEA]],
                  [unit("Tank", 0, 0, slot=1), unit("Lander", 1, 0, slot=2)])
        self.assertNotIn((1, 0), pathing.destinations(b, b.units[0]))

    def test_a_full_transport_is_not_a_destination(self):
        b = board([[PLAIN] * 4],
                  [unit("Infantry", 0, 0, slot=1),
                   unit("APC", 1, 0, slot=2, cargo=3),
                   unit("Mech", 1, 0, slot=3, loaded=True)])
        self.assertNotIn((1, 0), pathing.destinations(b, b.units[0]))

    def test_an_enemy_transport_is_not_a_destination(self):
        b = board([[PLAIN] * 4],
                  [unit("Infantry", 0, 0, player=1, slot=1),
                   unit("APC", 1, 0, player=2, slot=2)])
        self.assertNotIn((1, 0), pathing.destinations(b, b.units[0]))


class TestWeather(unittest.TestCase):
    def test_snow_shortens_the_range(self):
        """Snow raises plains to 2 for treads, halving a Tank's reach."""
        rows = [[PLAIN] * 10]
        clear = board(rows, [unit("Tank", 0, 0)], weather_index=0)
        snow = board(rows, [unit("Tank", 0, 0)], weather_index=1)
        self.assertEqual(max(x for (x, _) in pathing.destinations(clear, clear.units[0])), 6)
        self.assertEqual(max(x for (x, _) in pathing.destinations(snow, snow.units[0])), 3)


class TestPaths(unittest.TestCase):
    def test_path_is_contiguous_and_costs_what_the_search_said(self):
        b = board([[PLAIN, MOUNTAIN, PLAIN],
                   [PLAIN, PLAIN, PLAIN]], [unit("Infantry", 0, 0)])
        dest = (2, 0)
        route = pathing.path(b, b.units[0], dest)
        self.assertEqual(route[0], (0, 0))
        self.assertEqual(route[-1], dest)
        for (ax, ay), (bx, by) in zip(route, route[1:]):
            self.assertEqual(abs(ax - bx) + abs(ay - by), 1)
        cost = sum(b.move_cost(x, y, "Infantry") for (x, y) in route[1:])
        self.assertEqual(cost, pathing.destinations(b, b.units[0])[dest])

    def test_it_routes_around_the_mountain_because_that_is_cheaper(self):
        """Straight through costs 2+1=3; around the bottom costs 1+1+1=3 too,
        but the mountain route leaves nothing over. Both are 3, so assert the
        COST rather than the shape -- a test that pins one arbitrary tie-break
        would break on any reordering."""
        b = board([[PLAIN, MOUNTAIN, PLAIN],
                   [PLAIN, PLAIN, PLAIN]], [unit("Infantry", 0, 0)])
        self.assertEqual(pathing.destinations(b, b.units[0])[(2, 0)], 3)

    def test_unreachable_destination_returns_no_path(self):
        b = board([[PLAIN, RIVER, PLAIN]], [unit("Tank", 0, 0)])
        self.assertEqual(pathing.path(b, b.units[0], (2, 0)), [])


class TestNoUnitTypeBranches(unittest.TestCase):
    def test_pathing_names_no_unit_type(self):
        """The design constraint, enforced. Movement must come from tables; a
        unit name appearing in the module means data leaked into code."""
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "engine" / "pathing.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        code = code.split('"""')[0] + '"""'.join(code.split('"""')[2::2])
        names = json.loads((pathlib.Path(__file__).resolve().parent.parent
                            / "data" / "aw1_unit_stats.json")
                           .read_text(encoding="utf-8"))["units"]
        for name in names:
            self.assertNotIn(f'"{name}"', code, f"{name} is named in pathing.py")
            self.assertNotIn(f"'{name}'", code, f"{name} is named in pathing.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

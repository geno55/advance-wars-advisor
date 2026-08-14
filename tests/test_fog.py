"""Regression tests for the fog model and what it does to threat projection.

Same discipline as the other suites: hand-built boards, one rule per test, and
no unit names in the module under test.

The rules here are ASSUMPTIONS, not measurements -- see engine/fog.py. These
tests pin what the code does so a future measurement produces a visible
disagreement instead of a silent drift. A test passing here is not evidence the
game agrees; it is evidence the model is the one we think we wrote.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import fog                                                    # noqa: E402
import threat                                                 # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

PLAIN, RIVER, MOUNTAIN, WOOD, ROAD, CITY, SEA, REEF = 1, 2, 3, 4, 5, 6, 7, 19


def board(rows, units=(), weather_index=0, owner=None, fog_flag=None):
    w, h = len(rows[0]), len(rows)
    return Board(width=w, height=h, units=list(units), armies=[],
                 terrain=[list(r) for r in rows],
                 owner=owner or [[0] * w for _ in rows],
                 weather_index=weather_index, fog=fog_flag)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False,
         loaded=False, cargo=0):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=0, fuel=fuel, acted=acted,
                carrying=bool(cargo), loaded=loaded, state=0, cargo=cargo)


class TestSightIsARadiusFromTheTable(unittest.TestCase):
    def test_vision_comes_from_the_stats_table(self):
        """Recon sees 5, Artillery sees 1, and the same function produces both."""
        rows = [[PLAIN] * 13 for _ in range(13)]
        got = {}
        for utype in ("Recon", "Artillery"):
            b = board(rows, [unit(utype, 6, 6)])
            lit = fog.visible_tiles(b, 1)
            got[utype] = max(abs(x - 6) + abs(y - 6) for (x, y) in lit)
        self.assertEqual(got["Recon"], 5)
        self.assertEqual(got["Artillery"], 1)

    def test_sight_is_manhattan_not_a_box(self):
        """A radius-5 unit lights a diamond. The corner of the 5x5 box is
        Manhattan 10 away and must stay dark."""
        rows = [[PLAIN] * 13 for _ in range(13)]
        b = board(rows, [unit("Recon", 6, 6)])
        lit = fog.visible_tiles(b, 1)
        self.assertIn((11, 6), lit)         # distance 5
        self.assertIn((8, 9), lit)          # distance 5
        self.assertNotIn((9, 9), lit)       # distance 6, inside the box

    def test_sight_is_clipped_to_the_board(self):
        b = board([[PLAIN] * 3 for _ in range(3)], [unit("Recon", 0, 0)])
        lit = fog.visible_tiles(b, 1)
        self.assertEqual(lit, {(x, y) for y in range(3) for x in range(3)})

    def test_a_passenger_contributes_no_sight(self):
        rows = [[PLAIN] * 13 for _ in range(13)]
        b = board(rows, [unit("APC", 6, 6, slot=1, cargo=2),
                         unit("Recon", 6, 6, slot=2, loaded=True)])
        lit = fog.visible_tiles(b, 1)
        # APC vision is 1; the Recon riding it would have made it 5.
        self.assertEqual(max(abs(x - 6) + abs(y - 6) for (x, y) in lit), 1)


class TestConcealingTerrain(unittest.TestCase):
    def test_a_unit_in_woods_is_hidden_from_a_distance(self):
        rows = [[PLAIN] * 8]
        b = board(rows, [unit("Recon", 0, 0, player=1, slot=1),
                         unit("Infantry", 3, 0, player=2, slot=2)])
        self.assertTrue(fog.can_see(b, 1, b.units[1]))

        rows_wood = [[PLAIN, PLAIN, PLAIN, WOOD, PLAIN, PLAIN, PLAIN, PLAIN]]
        b2 = board(rows_wood, [unit("Recon", 0, 0, player=1, slot=1),
                               unit("Infantry", 3, 0, player=2, slot=2)])
        self.assertIn((3, 0), fog.visible_tiles(b2, 1))   # the tile is lit
        self.assertFalse(fog.can_see(b2, 1, b2.units[1]))  # the unit is not

    def test_adjacency_defeats_the_cover(self):
        rows = [[PLAIN, WOOD, PLAIN]]
        b = board(rows, [unit("Recon", 0, 0, player=1, slot=1),
                         unit("Infantry", 1, 0, player=2, slot=2)])
        self.assertTrue(fog.can_see(b, 1, b.units[1]))

    def test_reef_conceals_the_same_way_as_woods(self):
        """Data, not a second rule -- Reef is in the same CONCEALING tuple."""
        rows = [[SEA, SEA, SEA, REEF, SEA]]
        b = board(rows, [unit("Cruiser", 0, 0, player=1, slot=1),
                         unit("Sub", 3, 0, player=2, slot=2)])
        self.assertFalse(fog.can_see(b, 1, b.units[1]))

    def test_the_rule_can_be_switched_off(self):
        rows = [[PLAIN, PLAIN, PLAIN, WOOD]]
        b = board(rows, [unit("Recon", 0, 0, player=1, slot=1),
                         unit("Infantry", 3, 0, player=2, slot=2)])
        self.assertFalse(fog.can_see(b, 1, b.units[1]))
        self.assertTrue(fog.can_see(b, 1, b.units[1],
                                    fog.rules(hiding_terrain=False)))

    def test_an_unknown_rule_name_is_an_error(self):
        with self.assertRaises(KeyError):
            fog.rules(hidng_terrain=False)


class TestOptionalRules(unittest.TestCase):
    def test_property_vision_is_off_by_default(self):
        rows = [[CITY] + [PLAIN] * 9]
        own = [[1] + [0] * 9]
        b = board(rows, [unit("Infantry", 9, 0)], owner=own)
        self.assertNotIn((0, 0), fog.visible_tiles(b, 1))
        self.assertIn((0, 0), fog.visible_tiles(
            b, 1, fog.rules(property_vision=True)))

    def test_mountain_bonus_is_off_by_default(self):
        rows = [[MOUNTAIN] + [PLAIN] * 9]
        b = board(rows, [unit("Infantry", 0, 0)])
        plain_reach = max(x for (x, _) in fog.visible_tiles(b, 1))
        bonus_reach = max(x for (x, _) in fog.visible_tiles(
            b, 1, fog.rules(mountain_bonus=True)))
        self.assertEqual(plain_reach, 2)
        self.assertEqual(bonus_reach, 3)


class TestOwnUnitsAndHiddenTiles(unittest.TestCase):
    def test_you_always_see_your_own(self):
        rows = [[PLAIN] * 20]
        b = board(rows, [unit("Infantry", 0, 0, player=1, slot=1),
                         unit("Infantry", 19, 0, player=1, slot=2)])
        self.assertTrue(fog.can_see(b, 1, b.units[1]))

    def test_hidden_tiles_are_the_complement_of_lit_ones(self):
        rows = [[PLAIN] * 10 for _ in range(10)]
        b = board(rows, [unit("Infantry", 0, 0)])
        lit = fog.visible_tiles(b, 1)
        dark = fog.hidden_tiles(b, 1)
        self.assertEqual(len(lit) + len(dark), 100)
        self.assertEqual(lit & dark, set())

    def test_strike_radius_comes_from_the_table(self):
        """Furthest any armed unit could start and still reach you. The winner
        is the Battleship at move 5 plus range 6, NOT the Fighter, which moves
        9 but has to close to 1 -- reach is the sum, not the movement. If a
        stat is ever misread this number moves."""
        self.assertEqual(fog.strike_radius(), 11)


class TestThreatUnderFog(unittest.TestCase):
    def test_an_unseen_enemy_is_not_counted(self):
        """Two boards, same tank. Lit, it is a threat; unlit, the projection
        drops it and says how much dark it could not see into."""
        rows = [[PLAIN] * 20]
        b = board(rows, [unit("Infantry", 0, 0, player=1, slot=1),
                         unit("Tank", 4, 0, player=2, slot=2)])
        clear = threat.focus_fire(b, b.units[0], fog=False)
        fogged = threat.focus_fire(b, b.units[0], fog=True)
        self.assertEqual(clear.attackers, 1)
        self.assertEqual(fogged.attackers, 0)
        self.assertGreater(fogged.blind_spots, 0)

    def test_a_seen_enemy_is_still_counted(self):
        rows = [[PLAIN] * 20]
        b = board(rows, [unit("Infantry", 0, 0, player=1, slot=1),
                         unit("Tank", 2, 0, player=2, slot=2)])
        self.assertEqual(threat.focus_fire(b, b.units[0], fog=True).attackers, 1)

    def test_unknown_fog_warns_instead_of_assuming_clear(self):
        rows = [[PLAIN] * 8]
        b = board(rows, [unit("Infantry", 0, 0, player=1, slot=1),
                         unit("Tank", 2, 0, player=2, slot=2)])
        w = []
        threat.focus_fire(b, b.units[0], warnings=w)
        self.assertTrue(any("UNKNOWN" in x for x in w))

    def test_the_board_can_carry_the_setting(self):
        rows = [[PLAIN] * 20]
        b = board(rows, [unit("Infantry", 0, 0, player=1, slot=1),
                         unit("Tank", 4, 0, player=2, slot=2)],
                  fog_flag=True)
        self.assertEqual(threat.focus_fire(b, b.units[0]).attackers, 0)
        # An explicit argument still overrides what the board thinks.
        self.assertEqual(threat.focus_fire(b, b.units[0], fog=False).attackers, 1)

    def test_moving_forward_reveals_and_costs(self):
        """Visibility is evaluated on the hypothetical board, so stepping
        toward the enemy can turn an unseen tank into a counted one."""
        rows = [[PLAIN] * 20]
        b = board(rows, [unit("Infantry", 0, 0, player=1, slot=1),
                         unit("Tank", 5, 0, player=2, slot=2)])
        stay = threat.focus_fire(b, b.units[0], (0, 0), fog=True)
        forward = threat.focus_fire(b, b.units[0], (3, 0), fog=True)
        self.assertEqual(stay.attackers, 0)
        self.assertEqual(forward.attackers, 1)

    def test_threat_map_respects_fog(self):
        rows = [[PLAIN] * 20]
        b = board(rows, [unit("Infantry", 0, 0, player=1, slot=1),
                         unit("Tank", 9, 0, player=2, slot=2)])
        self.assertTrue(threat.threat_map(b, 1, fog=False))
        self.assertEqual(threat.threat_map(b, 1, fog=True), {})


class TestNoUnitTypeBranches(unittest.TestCase):
    def test_fog_names_no_unit_type(self):
        src = (ROOT / "engine" / "fog.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        code = code.split('"""')[0] + '"""'.join(code.split('"""')[2::2])
        names = json.loads((ROOT / "data" / "aw1_unit_stats.json")
                           .read_text(encoding="utf-8"))["units"]
        for name in names:
            self.assertNotIn(f'"{name}"', code, f"{name} is named in fog.py")
            self.assertNotIn(f"'{name}'", code, f"{name} is named in fog.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

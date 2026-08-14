"""Regression tests for threat projection.

Same discipline as test_pathing.py: boards are built by hand so each test
isolates one rule, and most of them exist to prove the SAME function produces
different answers for different units purely from table data. A unit-type
branch added to threat.py to make one of these pass should break the others.

The numbers here are worked from the formula rather than copied off a run.
Tank -> Md Tank is base 15, so on 4-star mountain at full health the first hit
is floor((15 + 9) * 0.60) = 14, and the second lands into a Md Tank at 86
internal HP, where the defence term has risen to 0.68 and the same attack is
worth floor(24 * 0.68) = 16. That 14 + 16 = 30, against a naive 14 + 14 = 28,
is the whole reason focus fire is sequenced instead of summed.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import threat                                                 # noqa: E402
from state import Board, Unit                                 # noqa: E402

PLAIN, RIVER, MOUNTAIN, WOOD, ROAD, SEA, SHOAL = 1, 2, 3, 4, 5, 7, 13


def board(rows, units=(), weather_index=0):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=[], terrain=[list(r) for r in rows],
                 owner=[[0] * len(rows[0]) for _ in rows],
                 weather_index=weather_index)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False,
         loaded=False, cargo=0):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=0, fuel=fuel, acted=acted,
                carrying=bool(cargo), loaded=loaded, state=0, cargo=cargo)


class TestTablesDriveTheGeometry(unittest.TestCase):
    """min_range, max_range and can_move_and_fire are ROM fields. Everything
    about who threatens what has to come out of them."""

    def test_unarmed_units_threaten_nothing(self):
        """`armed` is false for transports, so they drop out without being
        named. No rule in threat.py mentions cargo carriers."""
        b = board([[PLAIN] * 6], [unit("APC", 3, 0, player=2)])
        self.assertEqual(threat.covered_tiles(b, b.units[0]), {})

    def test_direct_unit_threatens_around_everywhere_it_can_stop(self):
        """A Tank moves 6 on plains, so from x=5 it covers the whole row."""
        b = board([[PLAIN] * 12], [unit("Tank", 5, 0, player=2)])
        covered = set(threat.covered_tiles(b, b.units[0]))
        self.assertIn((0, 0), covered)
        self.assertIn((11, 0), covered)

    def test_indirect_unit_threatens_only_around_where_it_stands(self):
        """Artillery is can_move_and_fire=false, so driving somewhere and
        shooting is not a thing it may do. Range 2..3 from x=5 is four tiles,
        and its six movement points buy it nothing at all."""
        b = board([[PLAIN] * 12], [unit("Artillery", 5, 0, player=2)])
        covered = set(threat.covered_tiles(b, b.units[0]))
        self.assertEqual(covered, {(2, 0), (3, 0), (7, 0), (8, 0)})

    def test_indirect_cannot_hit_what_is_next_to_it(self):
        """min_range 2. The tile beside an Artillery is the safest on the map,
        and that falls out of the table rather than out of a special case."""
        b = board([[PLAIN] * 12], [unit("Artillery", 5, 0, player=2)])
        covered = set(threat.covered_tiles(b, b.units[0]))
        self.assertNotIn((4, 0), covered)
        self.assertNotIn((6, 0), covered)
        self.assertNotIn((5, 0), covered)

    def test_one_function_splits_direct_from_indirect(self):
        """The point of the whole file: same call, same tile, same board, and
        the answers differ only because the table rows differ."""
        rows = [[PLAIN] * 12]
        direct = board(rows, [unit("Tank", 5, 0, player=2)])
        indirect = board(rows, [unit("Artillery", 5, 0, player=2)])
        self.assertIn((4, 0), threat.covered_tiles(direct, direct.units[0]))
        self.assertNotIn((4, 0), threat.covered_tiles(indirect, indirect.units[0]))

    def test_range_is_manhattan_not_chebyshev(self):
        """Rockets reach 3..5. The diagonal at (3,3) is Manhattan 6 and out."""
        b = board([[PLAIN] * 8 for _ in range(8)],
                  [unit("Rockets", 0, 0, player=2)])
        covered = threat.covered_tiles(b, b.units[0])
        self.assertIn((5, 0), covered)          # distance 5
        self.assertIn((2, 3), covered)          # distance 5
        self.assertNotIn((3, 3), covered)       # distance 6
        self.assertNotIn((1, 1), covered)       # distance 2, inside min_range


class TestLegality(unittest.TestCase):
    def test_a_matchup_the_damage_table_forbids_is_not_a_threat(self):
        """An Anti-Air sits next to a Lander and cannot touch it. Geometry says
        threatened, the damage matrix says no, and the matrix wins."""
        b = board([[SHOAL, SEA]],
                  [unit("Lander", 1, 0, player=1, slot=1),
                   unit("AntiAir", 0, 0, player=2, slot=2)])
        lander = b.units[0]
        self.assertIn((1, 0), threat.covered_tiles(b, b.units[1]))
        self.assertEqual(threat.threats_to(b, lander), [])

    def test_threat_map_filtered_by_type_drops_the_impossible_attacker(self):
        b = board([[SHOAL, SEA]],
                  [unit("Lander", 1, 0, player=1, slot=1),
                   unit("AntiAir", 0, 0, player=2, slot=2)])
        unfiltered = threat.threat_map(b, 1)
        filtered = threat.threat_map(b, 1, unit_type="Lander")
        self.assertIn((1, 0), unfiltered)
        self.assertNotIn((1, 0), filtered)


class TestTerrain(unittest.TestCase):
    def test_air_units_get_no_cover_from_the_ground(self):
        """A Fighter over a mountain must take exactly what it would take over
        a road. If defence() leaked in where defence_for() belongs, the
        mountain would hand it 4 stars and this breaks."""
        got = {}
        for tile in (MOUNTAIN, ROAD):
            b = board([[tile, PLAIN]],
                      [unit("Fighter", 0, 0, player=1, slot=1),
                       unit("AntiAir", 1, 0, player=2, slot=2)])
            got[tile] = threat.focus_fire(b, b.units[0]).worst_damage
        self.assertEqual(got[MOUNTAIN], got[ROAD])
        self.assertGreater(got[ROAD], 0)

    def test_cover_reduces_the_worst_case(self):
        """Two boards identical but for the tile the defender stands on, so
        terrain is the only variable. Putting the mountain on the SAME board as
        the attacker would test something else entirely -- treads cannot climb
        it, so it would be blocking the route rather than granting cover."""
        got = {}
        for tile in (ROAD, MOUNTAIN):
            b = board([[tile, PLAIN]],
                      [unit("Infantry", 0, 0, player=1, slot=1),
                       unit("Tank", 1, 0, player=2, slot=2)])
            got[tile] = threat.focus_fire(b, b.units[0]).worst_damage
        self.assertGreater(got[ROAD], got[MOUNTAIN])
        self.assertGreater(got[MOUNTAIN], 0)


class TestHypotheticalPlacement(unittest.TestCase):
    def test_asking_about_a_tile_actually_moves_the_unit_there(self):
        """Out of reach is out of reach. If relocation were ignored the unit
        would be scored where it already stands and this returns a threat."""
        b = board([[PLAIN] * 20],
                  [unit("Infantry", 0, 0, player=1, slot=1),
                   unit("Tank", 1, 0, player=2, slot=2)])
        inf = b.units[0]
        self.assertTrue(threat.threats_to(b, inf, (0, 0)))
        self.assertEqual(threat.threats_to(b, inf, (19, 0)), [])

    def test_a_friendly_plug_blocks_the_enemy_route(self):
        """Enemies cannot move through your units. The gap at (1,1) is the only
        way south for treads, so corking it makes the bottom row safe."""
        rows = [[PLAIN, PLAIN, PLAIN],
                [MOUNTAIN, PLAIN, MOUNTAIN],
                [PLAIN, PLAIN, PLAIN]]
        enemy = unit("Tank", 1, 0, player=2, slot=9)
        target = unit("Infantry", 0, 2, player=1, slot=1)
        plug = unit("Mech", 1, 1, player=1, slot=2)

        open_road = board(rows, [target, enemy])
        corked = board(rows, [target, plug, enemy])
        self.assertTrue(threat.threats_to(open_road, open_road.units[0]))
        self.assertEqual(threat.threats_to(corked, corked.units[0]), [])


class TestSimultaneity(unittest.TestCase):
    def test_a_chokepoint_limits_how_many_can_shoot_at_once(self):
        """Two Tanks, one land tile beside the target. Both can reach it, only
        one can stand on it, so exactly one attack lands. Counting both is how
        a tool argues a player out of the best square on the map."""
        rows = [[SEA, PLAIN, SEA],
                [SEA, PLAIN, SEA],
                [PLAIN, PLAIN, PLAIN]]
        b = board(rows, [unit("Infantry", 1, 0, player=1, slot=1),
                         unit("Tank", 0, 2, player=2, slot=2),
                         unit("Tank", 2, 2, player=2, slot=3)])
        inf = b.units[0]
        self.assertEqual(len(threat.threats_to(b, inf)), 2)
        ff = threat.focus_fire(b, inf)
        self.assertEqual(ff.attackers, 1)
        self.assertEqual(len(ff.crowded_out), 1)

    def test_open_ground_lets_everyone_in(self):
        rows = [[PLAIN] * 5 for _ in range(5)]
        b = board(rows, [unit("Infantry", 2, 2, player=1, slot=1),
                         unit("Tank", 0, 2, player=2, slot=2),
                         unit("Tank", 4, 2, player=2, slot=3)])
        ff = threat.focus_fire(b, b.units[0])
        self.assertEqual(ff.attackers, 2)
        self.assertEqual(ff.crowded_out, ())


class TestFocusFireIsSequenced(unittest.TestCase):
    """The defence term reads the defender's CURRENT displayed HP, so hits get
    worth more as the target weakens. On flat ground the term is constant and
    the total is a plain sum; on cover it is not, and summing quotes
    understates the gang-up."""

    def _two_tanks_on(self, tile):
        rows = [[tile, PLAIN, PLAIN],
                [PLAIN, PLAIN, PLAIN]]
        b = board(rows, [unit("MdTank", 0, 0, player=1, slot=1),
                         unit("Tank", 1, 0, player=2, slot=2),
                         unit("Tank", 0, 1, player=2, slot=3)])
        return b, threat.focus_fire(b, b.units[0])

    def test_no_stars_means_the_total_is_a_plain_sum(self):
        b, ff = self._two_tanks_on(ROAD)
        self.assertEqual(ff.attackers, 2)
        singles = [t.max_damage for t in ff.delivered]
        self.assertEqual(singles, [24, 24])
        self.assertEqual(ff.worst_damage, 48)

    def test_on_cover_the_second_hit_is_worth_more_than_the_first(self):
        b, ff = self._two_tanks_on(MOUNTAIN)
        self.assertEqual(ff.attackers, 2)
        singles = [t.max_damage for t in ff.delivered]
        self.assertEqual(singles, [14, 14])
        self.assertEqual(ff.worst_damage, 29)      # 14 then 15, not 14 + 14
        self.assertGreater(ff.worst_damage, sum(singles))
        # The effect survived the display rule changing from floor_min1 to the
        # measured ceil, and it had to: a defender that has lost HP keeps a
        # smaller share of its terrain bonus whichever way the tenth rounds.
        # Only the size moved -- under ceil a survivor on 86 displays 9 rather
        # than 8, so it holds slightly more cover and the second hit gains 1
        # instead of 2.

    def test_damage_is_capped_at_the_defenders_health(self):
        rows = [[ROAD, ROAD, ROAD]]
        b = board(rows, [unit("Infantry", 0, 0, player=1, slot=1, hp=20),
                         unit("Tank", 1, 0, player=2, slot=2)])
        ff = threat.focus_fire(b, b.units[0])
        self.assertEqual(ff.worst_damage, 20)
        self.assertEqual(ff.worst_remaining, 0)
        self.assertTrue(ff.lethal)


class TestTurnModel(unittest.TestCase):
    def test_an_enemy_that_has_acted_still_threatens_next_turn(self):
        """Projection is about the opponent's next whole turn, by which point
        their units have refreshed. The flag only matters if you ask about the
        turn currently in progress."""
        b = board([[PLAIN] * 4],
                  [unit("Infantry", 0, 0, player=1, slot=1),
                   unit("Tank", 2, 0, player=2, slot=2, acted=True)])
        inf = b.units[0]
        self.assertTrue(threat.threats_to(b, inf))
        self.assertEqual(threat.threats_to(b, inf, ignore_acted=False), [])

    def test_a_passenger_shoots_nobody(self):
        b = board([[PLAIN] * 4],
                  [unit("Infantry", 0, 0, player=1, slot=1),
                   unit("APC", 1, 0, player=2, slot=2, cargo=3),
                   unit("Mech", 1, 0, player=2, slot=3, loaded=True)])
        self.assertEqual(threat.threats_to(b, b.units[0]), [])


class TestSafety(unittest.TestCase):
    def test_it_ranks_the_reachable_tiles_and_finds_the_safe_one(self):
        """A long row with one Tank at the far end. Running away works, and the
        safest tile should come back first with nothing able to reach it."""
        b = board([[PLAIN] * 14],
                  [unit("Infantry", 6, 0, player=1, slot=1),
                   unit("Tank", 13, 0, player=2, slot=2)])
        ranked = threat.safety(b, b.units[0])
        safe = {r.tile for r in ranked if r.focus.worst_damage == 0}
        exposed = {r.tile for r in ranked if r.focus.worst_damage > 0}

        # The Tank moves 6 from x=13 and hits one further, so x>=6 is covered
        # and the Infantry's three movement points reach exactly three havens.
        self.assertEqual(safe, {(3, 0), (4, 0), (5, 0)})
        self.assertEqual(exposed, {(6, 0), (7, 0), (8, 0), (9, 0)})

        worst = [r.focus.worst_damage for r in ranked]
        self.assertEqual(worst, sorted(worst))
        # Safety first, then the cheapest move among equally safe tiles, so
        # retreating one square beats retreating three for no extra benefit.
        self.assertEqual(ranked[0].tile, (5, 0))
        self.assertEqual(ranked[0].move_cost, 1)

    def test_it_reports_the_terrain_and_stars_it_scored_with(self):
        b = board([[PLAIN, WOOD]],
                  [unit("Infantry", 0, 0, player=1, slot=1)])
        by_tile = {r.tile: r for r in threat.safety(b, b.units[0])}
        self.assertEqual(by_tile[(1, 0)].terrain, "Wood")
        self.assertEqual(by_tile[(1, 0)].stars, 2)
        self.assertEqual(by_tile[(0, 0)].stars, 1)


class TestUnknownCoIsLoud(unittest.TestCase):
    def test_a_board_without_co_ids_warns_rather_than_assuming(self):
        b = board([[PLAIN] * 4],
                  [unit("Infantry", 0, 0, player=1, slot=1),
                   unit("Tank", 1, 0, player=2, slot=2)])
        warnings = []
        threat.focus_fire(b, b.units[0], warnings=warnings)
        self.assertTrue(any("CO is unknown" in w for w in warnings))


class TestNoUnitTypeBranches(unittest.TestCase):
    def test_threat_names_no_unit_type(self):
        """The design constraint, enforced -- the same test pathing.py carries.
        Who threatens what must come from `armed`, `can_move_and_fire` and the
        range fields, never from a name."""
        src = (ROOT / "engine" / "threat.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        code = code.split('"""')[0] + '"""'.join(code.split('"""')[2::2])
        names = json.loads((ROOT / "data" / "aw1_unit_stats.json")
                           .read_text(encoding="utf-8"))["units"]
        for name in names:
            self.assertNotIn(f'"{name}"', code, f"{name} is named in threat.py")
            self.assertNotIn(f"'{name}'", code, f"{name} is named in threat.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Unloading -- the measured drops replayed, and the rule's parts kept apart.

tests/fixtures/drop_probes.json is the spec: seven Drop drives on the real
game (DERIVATION 35). engine/unload.py must reproduce each valid-tile set,
and the shape tests separate the three table parts -- the transport's
unload-from terrain, the PASSENGER's passability, occupancy -- from one
another, because a plausible implementation could have used the transport's
passability for the landing tile (wrong: the Tank-typed cargo refused the
mountain its APC could never enter either, and the Infantry accepted it) or
treated the vacated origin as occupied (wrong: row D6).
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions
import sim                                                # noqa: E402
import unload                                                 # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                  "drop_probes.json").read_text(encoding="utf-8"))
PLAIN, MOUNTAIN, ROAD, SEA, SHOAL, PORT = 1, 3, 5, 7, 13, 11


def board(rows, units=(), owner=None, armies=()):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies), terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0, repair_free=True)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False,
         loaded=False, cargo=0, cargo2=0, capture=0):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=capture, fuel=fuel, acted=acted,
                carrying=bool(cargo or cargo2), loaded=loaded, state=0,
                cargo=cargo, cargo2=cargo2)


def of_kind(acts, kind):
    return [a for a in acts if a.kind == kind]


ARMIES = [Army(player=1, funds=19000, income=0),
          Army(player=2, funds=19000, income=0)]


def fixture_board(row):
    """Rebuild the slot-2 fixture's relevant corner: a 5x5 plain board with
    the APC at (2,2) (= fixture (12,7)), its neighbours written per the row,
    and an occupying friendly where the row says so."""
    rows = [[PLAIN] * 5 for _ in range(5)]
    rows[2][1] = ROAD; rows[2][2] = ROAD; rows[2][3] = ROAD   # the fixture's road row
    off = lambda k: (int(k.split(",")[0]) - 10, int(k.split(",")[1]) - 5)
    for k, v in row.get("terrain", {}).items():
        x, y = off(k); rows[y][x] = v
    units = [unit("APC", 2, 2, slot=69, cargo=66),
             unit(row["cargo"], 2, 2, slot=66, loaded=True)]
    for k in row.get("occupied", []):
        x, y = off(k); units.append(unit("Tank", x, y, slot=71))
    return board(rows, units, armies=ARMIES)


class TestMeasuredDrops(unittest.TestCase):
    def test_every_row_replays(self):
        for row in FIX["drops"]:
            with self.subTest(row["case"]):
                b = fixture_board(row)
                apc = b.units[0]
                here = (2, 2)
                if "apc_move" in row:
                    dx = row["apc_move"][1][0] - row["apc_move"][0][0]
                    here = (2 + dx, 2)
                tiles = unload.drop_tiles(b, apc, here, row["cargo"])
                if row.get("offered") is False:
                    self.assertEqual(tiles, [])
                    continue
                off = lambda xy: (xy[0] - 10, xy[1] - 5)
                if "valid_tiles" in row:
                    self.assertEqual(sorted(tiles),
                                     sorted(off(t) for t in row["valid_tiles"]))
                self.assertIn(off(row["dropped_to"]), tiles)

    def test_the_selectors_default_is_recorded_not_modelled(self):
        """Every drive with the north tile free landed north, while the
        validity mask is ordered W,E,N,S -- so the default is not the mask's
        first bit. The advisor offers every valid tile and models no
        default; this pins the observation so a later reading has to argue
        with it."""
        row = next(r for r in FIX["drops"] if r["case"] == "D1-in-place")
        self.assertEqual(row["dropped_to"], [12, 6])


class TestShape(unittest.TestCase):
    def test_passenger_passability_not_transport(self):
        # an APC could never enter a mountain either; the rule reads the
        # PASSENGER's +0x4C block
        self.assertTrue(unload.can_stand("Infantry", MOUNTAIN))
        self.assertFalse(unload.can_stand("Tank", MOUNTAIN))
        self.assertFalse(unload.can_stand("Infantry", SEA))

    def test_unload_from_is_the_transports_table(self):
        self.assertTrue(unload.unload_from("APC", ROAD))
        self.assertFalse(unload.unload_from("APC", MOUNTAIN))
        self.assertTrue(unload.unload_from("TCopter", MOUNTAIN))
        self.assertEqual([t for t in range(20) if unload.unload_from("Lander", t)],
                         [PORT, SHOAL])

    def test_vacated_origin_is_free(self):
        b = board([[SEA, ROAD, ROAD], [SEA, SEA, SEA]],
                  [unit("APC", 2, 0, slot=1, cargo=2),
                   unit("Infantry", 2, 0, slot=2, loaded=True)],
                  armies=ARMIES)
        # APC moves west to (1,0): its only land neighbour is (2,0), its origin
        self.assertEqual(unload.drop_tiles(b, b.units[0], (1, 0), "Infantry"),
                         [(2, 0)])

    def test_occupied_and_out_of_bounds_excluded(self):
        b = board([[ROAD, ROAD, ROAD]],
                  [unit("APC", 1, 0, slot=1, cargo=2),
                   unit("Infantry", 1, 0, slot=2, loaded=True),
                   unit("Tank", 0, 0, slot=3)],
                  armies=ARMIES)
        self.assertEqual(unload.drop_tiles(b, b.units[0], (1, 0), "Infantry"),
                         [(2, 0)])


class TestDropAction(unittest.TestCase):
    def test_drop_actions_enumerate_passenger_by_tile(self):
        b = board([[PLAIN, ROAD, PLAIN]],
                  [unit("APC", 1, 0, slot=1, cargo=2),
                   unit("Infantry", 1, 0, slot=2, loaded=True)],
                  armies=ARMIES)
        drops = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "drop")
        in_place = [(d.drop_tile, d.target.slot) for d in drops if d.tile == (1, 0)]
        self.assertEqual(sorted(in_place), [((0, 0), 2), ((2, 0), 2)])
        d = next(d for d in drops if d.tile == (1, 0) and d.drop_tile == (2, 0))
        self.assertEqual(d.hp_after, 100)
        self.assertIsNotNone(d.exposure)
        self.assertIsNotNone(d.turn_start)

    def test_two_slot_transport_offers_both_passengers(self):
        b = board([[SHOAL, PORT, SHOAL]],
                  [unit("Lander", 1, 0, slot=1, cargo=2, cargo2=3),
                   unit("Infantry", 1, 0, slot=2, loaded=True),
                   unit("Tank", 1, 0, slot=3, loaded=True)],
                  armies=ARMIES)
        drops = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "drop")
        self.assertEqual({d.target.slot for d in drops if d.tile == (1, 0)},
                         {2, 3})

    def test_no_drop_where_the_transport_may_not_unload(self):
        # a TCopter on a mountain unloads; an APC typed onto one does not --
        # here: Lander on Sea (not Port/Shoal) offers nothing
        b = board([[SHOAL, SEA, SHOAL]],
                  [unit("Lander", 1, 0, slot=1, cargo=2),
                   unit("Infantry", 1, 0, slot=2, loaded=True)],
                  armies=ARMIES)
        drops = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "drop")
        self.assertFalse([d for d in drops if d.tile == (1, 0)])

    def test_the_dropped_unit_is_acted_on_the_hypothetical_board(self):
        b = board([[PLAIN, ROAD, PLAIN]],
                  [unit("APC", 1, 0, slot=1, cargo=2),
                   unit("Infantry", 1, 0, slot=2, loaded=True)],
                  armies=ARMIES)
        drop = next(d for d in actions.actions_for(b, b.units[0], warnings=[])
                    if d.kind == "drop" and d.tile == (1, 0)
                    and d.drop_tile == (2, 0))
        hypo = sim.apply(b, drop)                 # the board the action scores on
        walker = next(u for u in hypo.units if u.slot == 2)
        self.assertTrue(walker.acted and not walker.loaded)
        self.assertEqual((walker.x, walker.y), (2, 0))
        rider = next(u for u in hypo.units if u.slot == 1)
        self.assertEqual((rider.cargo, rider.carrying, rider.acted),
                         (0, False, True))


if __name__ == "__main__":
    unittest.main()

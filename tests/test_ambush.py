"""The fog ambush -- the game's move grid replayed, the trap action checked.

tests/fixtures/ambush_probes.json holds the grid the game wrote with a
hidden Mech on the mover's road (fog on) and with the Mech visible (fog
off), plus the three confirms (DERIVATION 38). The corridor is rebuilt here
tile for tile from the fixture map's rows 6..8, x = 3..14, and the model's
reach plus trap_tiles must reproduce the grid: hidden tile enterable at its
cost, nothing beyond it, visible tile excluded.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions                                                # noqa: E402
import pathing                                                # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                  "ambush_probes.json").read_text(encoding="utf-8"))
PLAIN, RIVER, MOUNTAIN, WOOD, ROAD, CITY, SEA, BRIDGE = 1, 2, 3, 4, 5, 6, 7, 12

# the fixture map, rows 5..9 (row 5 is the river with bridges), x = 0..14
ROWS = {
    5: [RIVER, BRIDGE, RIVER, RIVER, RIVER, RIVER, RIVER, BRIDGE, RIVER, RIVER,
        RIVER, RIVER, RIVER, BRIDGE, RIVER],
    6: [PLAIN, ROAD, PLAIN, PLAIN, MOUNTAIN, MOUNTAIN, PLAIN, ROAD, PLAIN,
        MOUNTAIN, MOUNTAIN, PLAIN, PLAIN, ROAD, PLAIN],
    7: [PLAIN, ROAD, PLAIN, PLAIN, MOUNTAIN, MOUNTAIN, PLAIN, ROAD, ROAD, ROAD,
        ROAD, ROAD, ROAD, ROAD, PLAIN],
    8: [40, ROAD, PLAIN, CITY, MOUNTAIN, MOUNTAIN, WOOD, PLAIN, PLAIN, PLAIN,
        PLAIN, PLAIN, PLAIN, WOOD, PLAIN],
    9: [PLAIN, PLAIN, PLAIN, PLAIN, MOUNTAIN, MOUNTAIN, PLAIN, WOOD, PLAIN,
        CITY, CITY, PLAIN, PLAIN, PLAIN, WOOD],
}
ROWS[8][0] = 8        # the P1 HQ byte 0x28 = HQ(8) | P1


def board(fog, hidden):
    rows = [ROWS[y] for y in range(5, 10)]
    mover = Unit(slot=71, player=2, type="APC", x=9, y=7 - 5, hp=100, ammo=0,
                 capture=0, fuel=64, acted=False, carrying=False, loaded=False,
                 state=0, cargo=0)
    mech = Unit(slot=3, player=1, type="Mech", x=6, y=7 - 5, hp=100, ammo=3,
                capture=0, fuel=64, acted=False, carrying=False, loaded=False,
                state=0, cargo=0)
    apc69 = Unit(slot=69, player=2, type="APC", x=12, y=7 - 5, hp=100, ammo=0,
                 capture=0, fuel=64, acted=False, carrying=False, loaded=False,
                 state=0, cargo=0)
    b = Board(width=15, height=5, units=[mover, mech, apc69],
              armies=[Army(1, 19000, 0, co_id=1), Army(2, 19000, 0, co_id=1)],
              terrain=rows, owner=[[0] * 15 for _ in rows], weather_index=0,
              fog=fog, repair_free=True)
    # the game's vision array, shifted: under fog only the mover's 1-tile
    # sight and the other APC's -- the Mech at (6,7) is dark
    if fog:
        vis = [[0] * 15 for _ in rows]
        for (cx, cy) in ((9, 2), (12, 2)):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if abs(dx) + abs(dy) <= 1 and 0 <= cx + dx < 15 and 0 <= cy + dy < 5:
                        vis[cy + dy][cx + dx] = 1
        b.vision = vis
    return b, mover, mech


def grid_row(b, unit, y, hidden):
    reach = pathing.reachable(b, unit)
    traps = pathing.trap_tiles(b, unit, hidden)
    out = []
    for x in range(15):
        t = (x, y - 5)
        if t in reach:
            out.append(reach[t])
        elif t in traps:
            out.append(traps[t][1] + b.move_cost(x, y - 5, "Treads"))
        else:
            out.append(255)
    return out


class TestGrid(unittest.TestCase):
    def test_fog_on_matches_the_games_grid(self):
        b, mover, mech = board(True, {3})
        for y in (6, 7, 8):
            with self.subTest(y=y):
                self.assertEqual(grid_row(b, mover, y, {mech.slot}),
                                 FIX["grid"]["fog_on"][str(y)])

    def test_fog_off_matches_the_games_grid(self):
        b, mover, mech = board(False, set())
        for y in (6, 7, 8):
            with self.subTest(y=y):
                self.assertEqual(grid_row(b, mover, y, set()),
                                 FIX["grid"]["fog_off"][str(y)])

    def test_beyond_the_hidden_tile_is_unreachable(self):
        b, mover, mech = board(True, {3})
        self.assertNotIn((5, 2), pathing.reachable(b, mover))
        self.assertNotIn((5, 2), pathing.trap_tiles(b, mover, {3}))


class TestTrapAction(unittest.TestCase):
    def test_trap_stops_short_pays_travelled_fuel_and_is_acted(self):
        b, mover, mech = board(True, {3})
        acts = actions.actions_for(b, mover, fog=True, warnings=[])
        traps = [a for a in acts if a.kind == "trap"]
        self.assertEqual([(a.tile, a.drop_tile) for a in traps],
                         [((6, 2), (7, 2))])
        t = traps[0]
        self.assertEqual(t.fuel_after, 62)
        self.assertEqual(t.target.slot, 3)
        self.assertIsNotNone(t.exposure)
        # the hidden tile is not a wait, and nothing lies beyond it
        self.assertFalse([a for a in acts if a.kind == "wait" and a.tile in ((6, 2), (5, 2))])

    def test_no_trap_when_fog_is_off_or_the_enemy_is_lit(self):
        b, mover, mech = board(False, set())
        self.assertFalse([a for a in actions.actions_for(b, mover, fog=False,
                                                         warnings=[])
                          if a.kind == "trap"])

    def test_drives_in_the_fixture_agree(self):
        rows = {r["case"]: r for r in FIX["drives"]}
        self.assertEqual(rows["T2-onto-hidden-tile"]["ended"], [7, 7])
        self.assertEqual(rows["T2-onto-hidden-tile"]["fuel_after"], 62)
        self.assertIsNone(rows["T3-beyond-hidden-tile"]["ended"])


if __name__ == "__main__":
    unittest.main()

"""The dived sub's concealment -- the game's reveal check replayed, and what
it does to targeting, exposure and the move grid (DERIVATION 41).

tests/fixtures/sub_conceal_probes.json holds nine driven cases on the
savestate-2 map: row y=7 written to Sea from x=6..10 plus (7,6), P2's unit 71
typed Cruiser at (9,7), P1's unit 3 typed Sub and real-moved from (7,6). The
grids are the game's own move-select grid with the Cruiser selected. The
corridor is rebuilt here from the fixture map's rows 6..8 (test_ambush.py
carries the same rows) with the six written tiles applied.

What is pinned: fog.concealed() agrees with the game on every case; a
concealed sub is not a target and not a projected attacker; the sub's own
exposure keeps exactly the hunters the game would show it to; and the tiles
the game offers only through the sub are traps that stop where it stands.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions                                                # noqa: E402
import fog                                                    # noqa: E402
import pathing                                                # noqa: E402
import threat                                                 # noqa: E402
from state import Board, Unit                                 # noqa: E402

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                  "sub_conceal_probes.json").read_text(encoding="utf-8"))
PLAIN, MOUNTAIN, WOOD, ROAD, CITY, SEA, PORT = 1, 3, 4, 5, 6, 7, 11
DIVED = 0x20

# the fixture map, rows 6..8, x = 0..14, as the probes left it
ROWS = {
    6: [PLAIN, ROAD, PLAIN, PLAIN, MOUNTAIN, MOUNTAIN, PLAIN, SEA, PLAIN,
        MOUNTAIN, MOUNTAIN, PLAIN, PLAIN, ROAD, PLAIN],
    7: [PLAIN, ROAD, PLAIN, PLAIN, MOUNTAIN, MOUNTAIN, SEA, SEA, SEA, SEA,
        SEA, ROAD, ROAD, ROAD, PLAIN],
    8: [PLAIN, ROAD, PLAIN, CITY, MOUNTAIN, MOUNTAIN, WOOD, PLAIN, PLAIN,
        PLAIN, PLAIN, PLAIN, PLAIN, WOOD, PLAIN],
}


def unit(utype, x, y, player, slot, state=0, acted=False):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=100,
                ammo=9, capture=0, fuel=60, acted=acted, carrying=False,
                loaded=False, state=state, cargo=0)


def corridor(sub_at=(7, 7), dived=True, port_owner=0, apc_at=None,
             cruiser_at=(9, 7)):
    rows = [list(ROWS[y]) for y in (6, 7, 8)]          # y offsets 6..8 -> 0..2
    owner = [[0] * 15 for _ in rows]
    if port_owner:
        rows[sub_at[1] - 6][sub_at[0]] = PORT
        owner[sub_at[1] - 6][sub_at[0]] = port_owner
    units = [unit("Sub", sub_at[0], sub_at[1] - 6, 1, 3,
                  state=DIVED if dived else 0),
             unit("Cruiser", cruiser_at[0], cruiser_at[1] - 6, 2, 71)]
    if apc_at:
        units.append(unit("APC", apc_at[0], apc_at[1] - 6, 2, 69))
    return Board(width=15, height=3, units=units, armies=[], terrain=rows,
                 owner=owner, weather_index=0, fog=False)


def by_slot(b, slot):
    return next(u for u in b.units if u.slot == slot)


class TestTheRevealCheck(unittest.TestCase):
    """fog.concealed() against the game's check on every fixture case."""

    def test_surfaced_is_never_concealed(self):
        b = corridor(dived=False)
        self.assertFalse(fog.concealed(b, by_slot(b, 3), viewer=2))

    def test_dived_with_nothing_of_the_viewers_adjacent_is_concealed(self):
        b = corridor()
        self.assertTrue(fog.concealed(b, by_slot(b, 3), viewer=2))
        self.assertFalse(fog.concealed(b, by_slot(b, 3), viewer=1))   # its own

    def test_a_viewer_unit_parked_alongside_reveals_it(self):
        for layout in (dict(sub_at=(8, 7)),                     # W1: the Cruiser
                       dict(apc_at=(7, 8))):                     # W3: an APC south
            b = corridor(**layout)
            self.assertFalse(fog.concealed(b, by_slot(b, 3), viewer=2), layout)

    def test_the_viewers_property_under_it_reveals_it(self):
        b = corridor(port_owner=2)                               # W2
        self.assertFalse(fog.concealed(b, by_slot(b, 3), viewer=2))
        b = corridor(port_owner=1)                               # its own port
        self.assertTrue(fog.concealed(b, by_slot(b, 3), viewer=2))

    def test_the_fixture_cases_agree(self):
        """Each case's `drawn` / blocking grid maps onto concealed()."""
        cases = {c["case"]: c for c in FIX["drives"]}
        self.assertTrue(cases["H0-surfaced-control"]["drawn"])
        self.assertFalse(cases["H1-dived-nothing-adjacent"]["drawn"])
        # the sub's tile in the game's grid: open (a cost) when concealed,
        # 255 (-1) when shown
        self.assertEqual(cases["H1-dived-nothing-adjacent"]["grid_y7"][7], 2)
        self.assertEqual(cases["H0-surfaced-control"]["grid_y7"][7], -1)
        self.assertEqual(cases["W1-dives-adjacent"]["grid_y7"][8], -1)
        self.assertEqual(cases["W2-on-a-P2-port"]["grid_y7"][7], -1)
        self.assertEqual(cases["W3-revealed-by-a-second-unit"]["grid_y7"][7], -1)


class TestTargeting(unittest.TestCase):
    def test_a_concealed_sub_is_not_offered_as_a_target(self):
        """H1/H2: the Cruiser can reach adjacency but the game offers no Fire."""
        b = corridor()
        acts = actions.actions_for(b, by_slot(b, 71), warnings=[])
        self.assertEqual([a for a in acts if a.kind == "attack"], [])

    def test_a_revealed_sub_is_a_target_at_the_dived_tables_numbers(self):
        """W1 (adjacent), W2 (P2 port, 3 stars), W3 (APC alongside): Fire
        offered; 95 and 66 were the luck-5 rolls, inside the envelopes."""
        for layout, luck5 in ((dict(sub_at=(8, 7)), 95),
                              (dict(port_owner=2), 66),
                              (dict(apc_at=(7, 8)), 95)):
            b = corridor(**layout)
            acts = actions.actions_for(b, by_slot(b, 71), warnings=[])
            hits = [a for a in acts if a.kind == "attack"]
            self.assertTrue(hits, layout)
            s = hits[0].strike
            self.assertLessEqual(s.min_damage, luck5, layout)
            self.assertGreaterEqual(s.max_damage, luck5, layout)

    def test_hostiles_drops_it_with_a_warning(self):
        b = corridor()
        self.assertEqual(threat.hostiles(b, 2), [])
        w = []
        threat.threats_to(b, by_slot(b, 71), warnings=w)
        self.assertTrue(any("submerged" in x for x in w))


class TestTheSubsOwnExposure(unittest.TestCase):
    """The reveal rule from the sub's side: which hunters get to fire."""

    def test_a_lone_hunter_two_tiles_off_cannot_fire_this_turn(self):
        """H2: it closes to adjacent, opens its menu, and reads Wait."""
        b = corridor()
        ff = threat.focus_fire(b, by_slot(b, 3))
        self.assertEqual(ff.attackers, 0)
        self.assertEqual([t.attacker.slot for t in ff.unseen], [71])

    def test_a_hunter_already_adjacent_fires(self):
        b = corridor(sub_at=(8, 7))                              # W1
        ff = threat.focus_fire(b, by_slot(b, 3))
        self.assertEqual(ff.attackers, 1)
        self.assertEqual(ff.delivered[0].revealed_by, "adjacent")

    def test_the_hunters_property_under_the_sub_reveals_it(self):
        b = corridor(port_owner=2)                               # W2
        ff = threat.focus_fire(b, by_slot(b, 3))
        self.assertEqual(ff.attackers, 1)
        self.assertEqual(ff.delivered[0].revealed_by, "property")

    def test_another_enemy_that_can_park_alongside_reveals_it(self):
        """W3: the APC at (12,7) reaches (7,8) in exactly its six points,
        then the Cruiser moves in and fires. From (13,7) it cannot reach,
        and the Cruiser is unseen again."""
        b = corridor(apc_at=(12, 7))
        ff = threat.focus_fire(b, by_slot(b, 3))
        self.assertEqual(ff.attackers, 1)
        self.assertEqual(ff.delivered[0].revealed_by, "revealer")
        far = corridor(apc_at=(13, 7))
        self.assertEqual(threat.focus_fire(far, by_slot(far, 3)).attackers, 0)

    def test_surfacing_hands_the_lone_hunter_its_shot(self):
        b = corridor(dived=False)
        self.assertEqual(threat.focus_fire(b, by_slot(b, 3)).attackers, 1)


class TestTheGridAndTheTrap(unittest.TestCase):
    def test_the_grid_expands_through_a_concealed_sub(self):
        """H1/V4: (7,7) at 2, (6,7) and (7,6) at 3 -- offered by the game,
        traps here, all stopping at (8,7) having paid 1."""
        b = corridor()
        cruiser = by_slot(b, 71)
        traps = pathing.conceal_traps(b, cruiser, {3})
        self.assertEqual({t: (s, p) for t, (s, p, _) in traps.items()},
                         {(7, 1): ((8, 1), 1), (6, 1): ((8, 1), 1),
                          (7, 0): ((8, 1), 1)})
        self.assertTrue(all(sub == (7, 1) for _, _, sub in traps.values()))
        game = {c["case"]: c for c in FIX["drives"]}["H1-dived-nothing-adjacent"]
        self.assertEqual(game["grid_y7"][6:8], [3, 2])
        self.assertEqual(game["grid_y6"][7], 3)
        # our own fill stops short of the sub, the conservative half
        self.assertNotIn((7, 1), pathing.destinations(b, cruiser))
        self.assertNotIn((6, 1), pathing.destinations(b, cruiser))

    def test_a_surfaced_sub_is_nobodys_trap(self):
        """The caller states which units are concealed (fog.concealed_units,
        the same contract as trap_tiles); a surfaced sub is never in that
        set, so nothing is offered through it."""
        b = corridor(dived=False)
        concealed = {u.slot for u in fog.concealed_units(b, 2)}
        self.assertEqual(concealed, set())
        self.assertEqual(pathing.conceal_traps(b, by_slot(b, 71), concealed), {})
        acts = actions.actions_for(b, by_slot(b, 71), warnings=[])
        self.assertEqual([a for a in acts if a.kind == "trap"], [])

    def test_the_trap_actions_stop_where_the_game_stopped(self):
        """H3 and V4: picked (7,7) or (6,7), ended (8,7), fuel -1, acted."""
        b = corridor()
        acts = actions.actions_for(b, by_slot(b, 71), warnings=[])
        traps = {a.tile: a for a in acts if a.kind == "trap"}
        self.assertEqual(set(traps), {(7, 1), (6, 1), (7, 0)})
        for a in traps.values():
            self.assertEqual(a.drop_tile, (8, 1))
            self.assertEqual(a.move_cost, 1)
            self.assertEqual(a.fuel_after, 59)
            self.assertEqual(a.target.slot, 3)
        self.assertNotIn((7, 1), {a.tile for a in acts if a.kind == "wait"})

    def test_a_revealed_sub_blocks_like_any_enemy(self):
        b = corridor(apc_at=(7, 8))
        acts = actions.actions_for(b, by_slot(b, 71), warnings=[])
        self.assertEqual([a for a in acts if a.kind == "trap"], [])


class TestNoUnitTypeBranches(unittest.TestCase):
    def test_the_rule_names_no_unit_type(self):
        src = (ROOT / "engine" / "fog.py").read_text(encoding="utf-8")
        i = src.index("def concealed(")
        body = src[i:src.index("def concealed_units(")]
        for name in ("Sub", "Cruiser", "Battleship", "Lander"):
            self.assertNotIn(f'"{name}"', body)


if __name__ == "__main__":
    unittest.main(verbosity=2)

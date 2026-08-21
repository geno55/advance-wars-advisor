"""Supply, property repair and daily fuel burn -- the measured rows replayed.

The spec is tests/fixtures/supply_probes.json: every turn_start row there was
driven on the real game (DERIVATION 33) and is replayed here through
engine/supply.py. The refutation tests then pin the SHAPE of the rules the
way the derivation did: each one encodes an alternative reading that a
plausible implementation might have shipped -- display-bar repair without the
internal snap, all-or-nothing charging, additive dive burn, supply rescuing a
0-fuel neighbour -- and shows the measured row disagreeing with it.

The actions.py integration tests prove the supply action and the turn-start
facts fall out of TABLE data: the same enumeration offers Supply to the unit
the ROM's supplier table names and not to its neighbours, with no unit-type
branch anywhere in the path (the extractor asserts the table's content; these
tests assert the table is what gates the behaviour).
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions                                                # noqa: E402
import supply                                                 # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                  "supply_probes.json").read_text(encoding="utf-8"))
STATS = json.loads((ROOT / "data" / "aw1_unit_stats.json")
                   .read_text(encoding="utf-8"))["units"]
ID2NAME = {v["id"]: k for k, v in STATS.items()}

PLAIN, MOUNTAIN, WOOD, ROAD, CITY, SEA, HQ = 1, 3, 4, 5, 6, 7, 8
AIRPORT, PORT, BASE = 10, 11, 14


def board(rows, units=(), owner=None, armies=(), repair_free=None):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies),
                 terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0, repair_free=repair_free)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False,
         loaded=False, cargo=0, capture=0, state=0):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=capture, fuel=fuel, acted=acted,
                carrying=bool(cargo), loaded=loaded, state=state, cargo=cargo)


def of_kind(acts, kind):
    return [a for a in acts if a.kind == kind]


class TestMeasuredRows(unittest.TestCase):
    def test_every_turn_start_row_replays(self):
        """The fixture rows ARE the spec; supply.turn_start must reproduce
        each one -- hp, fuel, ammo, funds charged and removal."""
        for row in FIX["turn_start"]:
            if "type" not in row or "fuel_raw" in row:
                continue                    # composite/reader rows, below
            with self.subTest(row["case"]):
                name = ID2NAME[row["type"]]
                hp = row.get("hp", [100, 100])
                fuel = row.get("fuel", [50, None])
                ammo = row.get("ammo")
                caps = supply.resupply_caps(name)
                ts = supply.turn_start(
                    name, hp=hp[0], fuel=fuel[0],
                    ammo=ammo[0] if ammo else caps[1],
                    terrain_id=row.get("terrain", PLAIN),
                    tile_owner=1 if row.get("own") else 0, player=1,
                    funds=row.get("funds_at_repair"),
                    charge=not row.get("free_repair", False),
                    co_id=row.get("co"), dived=row.get("dived", False),
                    loaded=row.get("loaded", False))
                self.assertEqual(ts.crashes, row.get("removed", False))
                if fuel[1] is not None and not ts.crashes:
                    self.assertEqual(ts.fuel_after, fuel[1])
                self.assertEqual(ts.hp_after, hp[1])
                self.assertEqual(ts.repair_spent, row.get("spent", 0))
                if ammo:
                    self.assertEqual(ts.ammo_after, ammo[1])

    def test_crash_beats_supply(self):
        """Fixture S5: burn and its crash run before auto-supply, so an APC
        cannot save a 0-fuel neighbour -- both driven copters were removed,
        adjacent and control alike."""
        ts = supply.turn_start("BCopter", hp=100, fuel=1, ammo=0,
                               terrain_id=PLAIN, tile_owner=0, player=1,
                               apc_adjacent=True)
        self.assertTrue(ts.crashes)

    def test_auto_supply_two_sides(self):
        """Fixture S4: at turn start any adjacent needy friendly is topped
        to the stats maxima, free -- here replayed unit by unit."""
        for name, fuel0, ammo0 in (("Recon", 5, 0), ("Tank", 7, 2)):
            with self.subTest(name):
                caps = supply.resupply_caps(name)
                ts = supply.turn_start(name, hp=100, fuel=fuel0, ammo=ammo0,
                                       terrain_id=ROAD, tile_owner=0,
                                       player=1, apc_adjacent=True)
                self.assertEqual((ts.fuel_after, ts.ammo_after), caps)
                self.assertEqual(ts.repair_spent, 0)


class TestRepairShape(unittest.TestCase):
    """Each test is a refutation: the alternative it names would have passed
    every pre-derivation observation and fails a measured row."""

    def test_internal_snap_not_display_bars(self):
        # a "+2 display bars" model says 45 -> 65; the game snaps to 70
        self.assertEqual(supply.repair(45, "Tank").hp_after, 70)

    def test_cap_then_snap_charges_one_bar_not_two(self):
        # hp 81: the second iteration finds display 10 and tops up FREE --
        # a per-request model would charge 1400, a no-snap model stops at 91
        r = supply.repair(81, "Tank", funds=99999)
        self.assertEqual((r.hp_after, r.spent), (100, 700))

    def test_91_to_99_is_a_free_exact_100(self):
        r = supply.repair(95, "Tank", funds=99999)
        self.assertEqual((r.hp_after, r.spent), (100, 0))

    def test_broke_snaps_and_stops(self):
        # refuted alternatives: "no repair at all" (45) and "repair on
        # credit" (70) -- the game wrote 50 and charged nothing
        r = supply.repair(45, "Tank", funds=0)
        self.assertEqual((r.hp_after, r.spent), (50, 0))

    def test_partial_repair_is_per_bar_not_all_or_nothing(self):
        for funds, hp_after, spent in ((700, 60, 700), (1050, 60, 700),
                                       (1400, 70, 1400)):
            with self.subTest(funds=funds):
                r = supply.repair(45, "Tank", funds=funds)
                self.assertEqual((r.hp_after, r.spent), (hp_after, spent))

    def test_kanbei_pays_120_percent(self):
        self.assertEqual(supply.repair_cost_per_bar("Tank", co_id=6), 840)
        self.assertEqual(supply.repair(45, "Tank", funds=99999,
                                       co_id=6).spent, 1680)

    def test_free_flag_repairs_without_charging(self):
        r = supply.repair(45, "Tank", funds=0, charge=False)
        self.assertEqual((r.hp_after, r.spent), (70, 0))


class TestBurnShape(unittest.TestCase):
    def test_dive_replaces_the_rate_rather_than_adding(self):
        # surfaced 1, dived 5 -- an additive reading would say 6
        self.assertEqual(supply.daily_burn("Sub", terrain_id=SEA), 1)
        self.assertEqual(supply.daily_burn("Sub", terrain_id=SEA, dived=True),
                         5)

    def test_eagle_takes_two_off_air_only(self):
        self.assertEqual(supply.daily_burn("BCopter", co_id=8), 0)
        self.assertEqual(supply.daily_burn("Bomber", co_id=8), 3)
        self.assertEqual(supply.daily_burn("Sub", terrain_id=SEA, co_id=8,
                                           dived=True), 5)

    def test_loaded_units_burn_nothing_and_cannot_crash(self):
        self.assertEqual(supply.daily_burn("BCopter", loaded=True), 0)
        ts = supply.turn_start("BCopter", hp=100, fuel=0, ammo=0,
                               terrain_id=PLAIN, tile_owner=0, player=1,
                               loaded=True)
        self.assertFalse(ts.crashes)

    def test_own_service_terrain_skips_burn_and_the_crash_check(self):
        # measured R9/R11: no burn on an own Airport/Port; and the code
        # returns before the crash test, so even fuel 0 survives there
        self.assertEqual(supply.daily_burn("BCopter", terrain_id=AIRPORT,
                                           tile_owner=1, player=1), 0)
        ts = supply.turn_start("BCopter", hp=100, fuel=0, ammo=0,
                               terrain_id=AIRPORT, tile_owner=1, player=1,
                               charge=False)
        self.assertFalse(ts.crashes)
        self.assertEqual(ts.fuel_after, supply.resupply_caps("BCopter")[0])

    def test_enemy_airport_is_no_refuge(self):
        self.assertEqual(supply.daily_burn("BCopter", terrain_id=AIRPORT,
                                           tile_owner=2, player=1), 2)

    def test_ground_survives_fuel_zero(self):
        ts = supply.turn_start("Rockets", hp=100, fuel=0, ammo=9,
                               terrain_id=PLAIN, tile_owner=0, player=1)
        self.assertFalse(ts.crashes)


class TestSupplyAction(unittest.TestCase):
    def _armies(self, funds=99999):
        return [Army(player=1, funds=funds, income=0),
                Army(player=2, funds=funds, income=0)]

    def test_supplier_table_gates_the_action(self):
        """APC next to a needy friendly gets a supply action; the same board
        with a Recon in its place does not -- measured on the real menu
        (Wait-only for the Recon), and driven here purely by the extracted
        supplier table."""
        needy = unit("Tank", 0, 0, slot=2, fuel=5, ammo=2)
        for utype, offered in (("APC", True), ("Recon", False)):
            with self.subTest(utype):
                b = board([[PLAIN, PLAIN, PLAIN]], [unit(utype, 1, 0), needy],
                          armies=self._armies())
                sup = of_kind(actions.actions_for(b, b.units[0], warnings=[]),
                              "supply")
                self.assertEqual(bool(sup), offered)

    def test_full_neighbours_hide_supply(self):
        """The menu need-check measured on the game: a neighbour at both
        maxima offers nothing to top up."""
        caps = supply.resupply_caps("Tank")
        full = unit("Tank", 0, 0, slot=2, fuel=caps[0], ammo=caps[1])
        b = board([[PLAIN, PLAIN, PLAIN]], [unit("APC", 1, 0), full],
                  armies=self._armies())
        acts = actions.actions_for(b, b.units[0], warnings=[])
        self.assertFalse([a for a in of_kind(acts, "supply")
                          if a.tile == (1, 0)])

    def test_supply_lists_every_adjacent_needy_friendly(self):
        """Fixture supply-in-place: both sides fill, to the stats maxima."""
        tank = unit("Tank", 0, 0, slot=2, fuel=7, ammo=2)
        recon = unit("Recon", 2, 0, slot=3, fuel=5, ammo=0)
        b = board([[PLAIN, PLAIN, PLAIN]], [unit("APC", 1, 0), tank, recon],
                  armies=self._armies())
        sup = [a for a in of_kind(actions.actions_for(b, b.units[0],
                                                      warnings=[]), "supply")
               if a.tile == (1, 0)]
        self.assertEqual(len(sup), 1)
        fills = {f.target.slot: (f.fuel_to, f.ammo_to)
                 for f in sup[0].supplies}
        self.assertEqual(fills, {2: supply.resupply_caps("Tank"),
                                 3: supply.resupply_caps("Recon")})

    def test_enemies_are_not_supplied(self):
        enemy = unit("Tank", 0, 0, slot=2, player=2, fuel=5, ammo=2)
        b = board([[PLAIN, PLAIN, PLAIN]], [unit("APC", 1, 0), enemy],
                  armies=self._armies())
        acts = actions.actions_for(b, b.units[0], warnings=[])
        self.assertFalse(of_kind(acts, "supply"))


class TestTurnStartFacts(unittest.TestCase):
    def _armies(self, funds=99999):
        return [Army(player=1, funds=funds, income=0),
                Army(player=2, funds=funds, income=0)]

    def test_wait_on_own_city_quotes_the_repair(self):
        b = board([[CITY]], [unit("Tank", 0, 0, hp=45, fuel=20, ammo=2)],
                  owner=[[1]], armies=self._armies(), repair_free=False)
        w = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "wait")
        ts = w[0].turn_start
        self.assertTrue(ts.serviced)
        self.assertEqual((ts.hp_after, ts.repair_spent), (70, 1400))
        self.assertEqual(ts.fuel_after, supply.resupply_caps("Tank")[0])
        self.assertEqual(ts.ammo_after, supply.resupply_caps("Tank")[1])

    def test_unknown_repair_byte_assumes_charging_out_loud(self):
        """A dump without the 0x03004357 byte gets the charged quote and a
        warning, never a silently free repair."""
        b = board([[CITY]], [unit("Tank", 0, 0, hp=45)], owner=[[1]],
                  armies=self._armies())
        warnings = []
        w = of_kind(actions.actions_for(b, b.units[0], warnings=warnings),
                    "wait")
        self.assertEqual(w[0].turn_start.repair_spent, 1400)
        self.assertTrue(any("0x03004357" in n for n in warnings))

    def test_free_repair_byte_zeroes_the_charge(self):
        b = board([[CITY]], [unit("Tank", 0, 0, hp=45)], owner=[[1]],
                  armies=self._armies(), repair_free=True)
        w = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "wait")
        self.assertEqual(w[0].turn_start.repair_spent, 0)
        self.assertEqual(w[0].turn_start.hp_after, 70)

    def test_enemy_city_services_nothing(self):
        b = board([[CITY]], [unit("Tank", 0, 0, hp=45)], owner=[[2]],
                  armies=self._armies(), repair_free=False)
        w = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "wait")
        self.assertFalse(w[0].turn_start.serviced)
        self.assertEqual(w[0].turn_start.hp_after, 45)

    def test_wait_beside_an_apc_reports_the_top_up(self):
        b = board([[PLAIN, PLAIN]],
                  [unit("Tank", 0, 0, fuel=7, ammo=2),
                   unit("APC", 1, 0, slot=2)],
                  armies=self._armies(), repair_free=False)
        w = [a for a in of_kind(actions.actions_for(b, b.units[0],
                                                    warnings=[]), "wait")
             if a.tile == (0, 0)]
        ts = w[0].turn_start
        self.assertTrue(ts.auto_supplied)
        self.assertEqual((ts.fuel_after, ts.ammo_after),
                         supply.resupply_caps("Tank"))

    def test_a_completing_capture_is_serviced_the_same_night(self):
        """owner_override: the tile is the capturer's at turn start, so a
        finishing Infantry gets the city's repair quoted."""
        b = board([[CITY]],
                  [unit("Infantry", 0, 0, hp=95, capture=18)],
                  owner=[[2]], armies=self._armies(), repair_free=False)
        cap = of_kind(actions.actions_for(b, b.units[0], warnings=[]),
                      "capture")
        self.assertTrue(cap[0].captures_now)
        self.assertTrue(cap[0].turn_start.serviced)
        self.assertEqual(cap[0].turn_start.hp_after, 100)
        # the wait on the same tile is NOT serviced -- the city is not ours
        w = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "wait")
        self.assertFalse(w[0].turn_start.serviced)


if __name__ == "__main__":
    unittest.main()

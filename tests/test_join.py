"""Joining -- the measured merges replayed, and the alternatives refuted.

tests/fixtures/join_probes.json is the spec: five joins driven on the real
game (DERIVATION 34). engine/join.py must reproduce each; the refutation
tests name the readings a plausible implementation would have shipped --
internal HP summed (45+45=90), excess lost instead of refunded, the mover
keeping its capture progress, a full mover being refused -- and show the
measured rows disagreeing. The actions.py tests prove the join comes from
the pair rule and the reachable set, not from a unit-type name.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions                                                # noqa: E402
import join                                                   # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures" /
                  "join_probes.json").read_text(encoding="utf-8"))
PLAIN, ROAD, CITY = 1, 5, 6


def board(rows, units=(), owner=None, armies=()):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies), terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0, repair_free=True)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False,
         loaded=False, cargo=0, capture=0):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=capture, fuel=fuel, acted=acted,
                carrying=bool(cargo), loaded=loaded, state=0, cargo=cargo)


def of_kind(acts, kind):
    return [a for a in acts if a.kind == kind]


ARMIES = [Army(player=1, funds=19000, income=0),
          Army(player=2, funds=19000, income=0)]


class TestMeasuredJoins(unittest.TestCase):
    def test_every_row_replays(self):
        for row in FIX["joins"]:
            if row.get("offered") is False:
                continue
            with self.subTest(row["case"]):
                m, t = row["mover"], row["target"]
                r = join.merge(FIX["unit_type"], mover_hp=m["hp"],
                               target_hp=t["hp"],
                               mover_fuel_after_move=m["fuel"] - FIX["move_cost"],
                               target_fuel=t["fuel"], mover_ammo=m["ammo"],
                               target_ammo=t["ammo"],
                               target_capture=t.get("capture", 0),
                               co_id=row.get("co", 1))
                a = row["after"]
                self.assertEqual(r.hp_after, a["hp"])
                self.assertEqual(r.fuel_after, a["fuel"])
                self.assertEqual(r.ammo_after, a["ammo"])
                self.assertEqual(r.refund, row["refund"])
                if "capture" in a:
                    self.assertEqual(r.capture_after, a["capture"])

    def test_full_target_is_refused(self):
        mover = unit("Tank", 0, 0, hp=50)
        target = unit("Tank", 1, 0, slot=2, hp=100)
        self.assertFalse(join.can_join(mover, target))


class TestShape(unittest.TestCase):
    def test_display_bars_add_not_internal_hp(self):
        # 45 + 45 internal is 90 under an internal-sum model; the game writes
        # 100 -- two five-bar units make a perfect one
        r = join.merge("Tank", mover_hp=45, target_hp=45,
                       mover_fuel_after_move=50, target_fuel=50,
                       mover_ammo=0, target_ammo=0)
        self.assertEqual(r.hp_after, 100)

    def test_excess_is_refunded_at_unit_value(self):
        r = join.merge("Tank", mover_hp=70, target_hp=60,
                       mover_fuel_after_move=50, target_fuel=50,
                       mover_ammo=0, target_ammo=0, co_id=1)
        self.assertEqual((r.hp_after, r.refund), (100, 2100))
        self.assertEqual(join.merge("Tank", mover_hp=70, target_hp=60,
                                    mover_fuel_after_move=50, target_fuel=50,
                                    mover_ammo=0, target_ammo=0,
                                    co_id=6).refund, 2520)

    def test_capture_progress_is_the_targets(self):
        r = join.merge("Infantry", mover_hp=50, target_hp=50,
                       mover_fuel_after_move=50, target_fuel=50,
                       mover_ammo=0, target_ammo=0, target_capture=7)
        self.assertEqual(r.capture_after, 7)

    def test_a_full_mover_may_join_a_damaged_target(self):
        # the pair check reads only the TARGET's bars
        self.assertTrue(join.can_join(unit("Tank", 0, 0, hp=100),
                                      unit("Tank", 1, 0, slot=2, hp=50)))

    def test_carriers_and_different_types_do_not_join(self):
        self.assertFalse(join.can_join(unit("Tank", 0, 0, hp=50),
                                       unit("MdTank", 1, 0, slot=2, hp=50)))
        self.assertFalse(join.can_join(unit("APC", 0, 0, hp=50, cargo=9),
                                       unit("APC", 1, 0, slot=2, hp=50)))
        self.assertFalse(join.can_join(unit("Tank", 0, 0, hp=50, player=1),
                                       unit("Tank", 1, 0, slot=2, hp=50,
                                            player=2)))


class TestJoinAction(unittest.TestCase):
    def test_join_is_offered_on_a_reachable_damaged_twin(self):
        b = board([[PLAIN, PLAIN, PLAIN]],
                  [unit("Tank", 0, 0, hp=50, fuel=30, ammo=4),
                   unit("Tank", 2, 0, slot=2, hp=50, fuel=30, ammo=4,
                        capture=0)],
                  armies=ARMIES)
        js = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "join")
        self.assertEqual([(j.tile, j.target.slot) for j in js], [((2, 0), 2)])
        j = js[0]
        self.assertEqual((j.hp_after, j.fuel_after, j.merge.ammo_after),
                         (100, 58, 8))            # 30 - 2 + 30, capped 70
        self.assertIsNotNone(j.exposure)
        self.assertIsNotNone(j.turn_start)
        # the twin's tile is not a wait or a load
        self.assertFalse([a for a in actions.actions_for(b, b.units[0],
                                                         warnings=[])
                          if a.kind in ("wait", "load") and a.tile == (2, 0)])

    def test_no_join_onto_a_full_twin_or_another_type(self):
        b = board([[PLAIN, PLAIN, PLAIN]],
                  [unit("Tank", 0, 0, hp=50),
                   unit("Tank", 2, 0, slot=2, hp=100),
                   unit("MdTank", 1, 0, slot=3, hp=50)],
                  armies=ARMIES)
        self.assertFalse(of_kind(actions.actions_for(b, b.units[0],
                                                     warnings=[]), "join"))

    def test_join_exposure_is_the_merged_unit_with_the_target_gone(self):
        """The merged unit stands alone: the hypothetical board must hold
        one record on the tile, at the merged HP."""
        b = board([[PLAIN, PLAIN, PLAIN, PLAIN]],
                  [unit("Tank", 0, 0, hp=30),
                   unit("Tank", 1, 0, slot=2, hp=30),
                   unit("Tank", 3, 0, slot=3, player=2)],
                  armies=ARMIES)
        js = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "join")
        self.assertEqual(len(js), 1)
        self.assertEqual(js[0].hp_after, 60)
        self.assertGreater(js[0].exposure.worst_damage, 0)


if __name__ == "__main__":
    unittest.main()

"""The sparring harness, tools/sparring.py (ROADMAP step 5): a game runs
to the rout, stops at the day cap, reports an abort where the CPU port
cannot play and leaves a dump both readers load, and reads an HQ change.
"""
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import sparring                                               # noqa: E402
from engine import cpu_ai                                     # noqa: E402
from engine.state import Army, Board, Unit, load              # noqa: E402

PLAIN, CITY, HQ = 1, 6, 8
ANDY = 1


def board(rows, units=(), owner=None, armies=(), active=1, day=1):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies), terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0, active_player=active, day=day, fog=False,
                 funds_per_property=1000, repair_free=False, rng=12345)


def unit(utype, x, y, player=1, slot=1, hp=100):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=9, capture=0, fuel=99, acted=False, carrying=False,
                loaded=False, state=0, cargo=0)


def armies():
    return [Army(player=p, funds=0, income=0, co_id=ANDY, power_uses=0,
                 power_ready=False) for p in (1, 2)]


def context():
    return cpu_ai.Context(ai={}, sides={1: cpu_ai.Side(0, 0b10, None),
                                       2: cpu_ai.Side(1, 0b01, None)},
                          profile=cpu_ai.profile_for(38, {1: ANDY, 2: ANDY}, 2))


class TestSparring(unittest.TestCase):
    def test_a_rout_ends_the_game_and_is_counted(self):
        b = board([[PLAIN] * 4], [unit("Tank", 0, 0),
                                  unit("Infantry", 3, 0, player=2, slot=70, hp=20)],
                  armies=armies())
        r = sparring.spar(b, context(), 1, days=5, reply=None)
        self.assertEqual((r.outcome, r.reason, r.days), ("win", "rout", 1))
        self.assertEqual(r.taken, 200)             # two bars of an Infantry
        self.assertEqual(r.lost, 0)
        self.assertEqual(r.log[0].note, "start")
        self.assertIn("FIRE", r.log[-1].note)
        self.assertEqual(r.held, {1: 0, 2: 0})

    def test_the_day_cap_is_a_draw(self):
        b = board([[CITY, PLAIN, PLAIN, PLAIN, PLAIN, PLAIN, PLAIN, CITY]],
                  [unit("Infantry", 1, 0), unit("Infantry", 6, 0, player=2, slot=70)],
                  armies=armies())
        r = sparring.spar(b, context(), 1, days=1, reply=None)
        self.assertEqual((r.outcome, r.reason), ("draw", "day cap"))
        self.assertEqual(r.log[-1].mover, 2)          # the port had its turn
        self.assertTrue(any("CAPTURE" in d.note for d in r.log))

    def test_an_abort_is_reported_and_leaves_a_loadable_dump(self):
        b = board([[PLAIN] * 6], [unit("Tank", 0, 0),
                                  unit("Bomber", 5, 0, player=2, slot=70)],
                  armies=armies())
        with tempfile.TemporaryDirectory() as tmp:
            r = sparring.spar(b, context(), 1, days=5, reply=None,
                              state_name="bomber", abort_dir=pathlib.Path(tmp))
            self.assertEqual(r.outcome, "abort")
            self.assertIn("sub-phase", r.reason)
            self.assertIsNotNone(r.abort_dump)
            again = load(r.abort_dump)
            self.assertEqual(again.active_player, 2)
            self.assertEqual({u.type for u in again.units}, {"Tank", "Bomber"})
            ctx = cpu_ai.Context.from_dump(r.abort_dump, player=2)
            self.assertEqual(ctx.profile, context().profile)
            self.assertEqual(ctx.sides[2].enemies, 0b01)

    def test_an_hq_that_changes_hands_decides_the_game(self):
        rows = [[HQ, PLAIN, HQ]]
        start = board(rows, [unit("Infantry", 1, 0), unit("Infantry", 2, 0, player=2, slot=70)],
                      owner=[[1, 0, 2]], armies=armies())
        import dataclasses
        taken = dataclasses.replace(start, owner=[[1, 0, 1]])
        self.assertEqual(sparring.decided(start, taken, 1, 2), ("win", "hq"))
        lost = dataclasses.replace(start, owner=[[2, 0, 2]])
        self.assertEqual(sparring.decided(start, lost, 1, 2), ("loss", "hq"))
        self.assertIsNone(sparring.decided(start, start, 1, 2))


if __name__ == "__main__":
    unittest.main()

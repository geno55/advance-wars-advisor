"""The debrief's rank (engine/rank.py, DERIVATION 58): the formulas read
from the ROM against the numbers the game itself showed for the m01
acceptance run's win, and what they say S and A cost on mission one."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import rank                                             # noqa: E402


class TestTheDebriefsOwnNumbers(unittest.TestCase):
    """Mission one, won on day 26 by rout: the army record after the debrief
    read Speed 25, Power 100, Technique 54, total 487, rank code 2 -- twelve
    units fielded, eight lost, a best day of two kills against Olaf's
    seventeen (harness/out/probe/rank2, 2026-09-05)."""

    def test_the_par_is_the_mission_records(self):
        self.assertEqual(rank.par_for(130), 8)
        self.assertEqual(rank.par_for(38), 11)
        self.assertEqual(rank.par_for(130, campaign_table=True), 5)

    def test_speed_power_technique_and_the_total(self):
        r = rank.score(days=26, par=8, best_day=2, enemy_fielded=17, fielded=12, lost=8)
        self.assertEqual((r.speed, r.power, r.technique, r.total, r.letter),
                         (25, 100, 54, 487, "C"))

    def test_the_speed_curve(self):
        self.assertEqual([rank.speed(d, 8) for d in (1, 8, 9, 10, 11, 20, 26, 31, 32, 40)],
                         [100, 100, 96, 92, 88, 50, 25, 5, 0, 0])

    def test_technique_floors_and_caps(self):
        self.assertEqual(rank.technique(12, 0), 100)
        self.assertEqual(rank.technique(12, 2), 100)          # a sixth lost: 20 + 84
        self.assertEqual(rank.technique(12, 3), 95)
        self.assertEqual(rank.technique(12, 12), 20)
        self.assertEqual(rank.technique(12, 2, campaign=False), 94)
        self.assertEqual(rank.technique(0, 0), 0)

    def test_power_is_the_best_days_share(self):
        self.assertEqual(rank.power(2, 17), 100)
        self.assertEqual(rank.power(1, 17), 58)
        self.assertEqual(rank.power(0, 17), 0)

    def test_the_letters(self):
        self.assertEqual([rank.letter(t) for t in (0, 249, 250, 449, 450, 649, 650, 849, 850, 949, 950, 999)],
                         ["E", "E", "D", "D", "C", "C", "B", "B", "A", "A", "S", "S"])
        self.assertEqual(rank.letter(999, campaign=False), "A")


class TestWhatTheRanksCost(unittest.TestCase):
    def test_mission_one_needs_day_ten_for_s_and_day_fifteen_for_a(self):
        self.assertEqual(rank.days_for("S", 8), 10)
        self.assertEqual(rank.days_for("A", 8), 15)
        # with three of twelve lost, Technique 95 costs five days of S
        self.assertEqual(rank.days_for("S", 8, technique_score=95), 9)
        self.assertEqual(rank.score(days=10, par=8, best_day=2, enemy_fielded=17,
                                    fielded=12, lost=2).letter, "S")
        self.assertEqual(rank.score(days=11, par=8, best_day=2, enemy_fielded=17,
                                    fielded=12, lost=2).letter, "A")


if __name__ == "__main__":
    unittest.main()

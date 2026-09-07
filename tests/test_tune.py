"""tools/tune.py: the search's scoring and candidate rules (DERIVATION 59)."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import tune                                                          # noqa: E402


def game(outcome, day, total=None):
    return {"outcome": outcome, "end_day": day,
            "rank": {"total": total, "letter": "?"} if total is not None else None}


class TestTheScore(unittest.TestCase):
    def test_a_win_scores_its_rank_total_and_anything_else_zero(self):
        self.assertEqual(tune.score_of([game("win", 10, 960)]), (960, -10))
        self.assertEqual(tune.score_of([game("draw", 31)]), (0, -31))
        self.assertEqual(tune.score_of([game("loss", 17)]), (0, -17))

    def test_fewer_days_break_a_tie(self):
        self.assertGreater(tune.score_of([game("win", 10, 940)]), tune.score_of([game("win", 11, 940)]))
        self.assertGreater(tune.score_of([game("win", 12, 920)]), tune.score_of([game("win", 10, 900)]))

    def test_a_missing_day_counts_as_the_worst(self):
        self.assertEqual(tune.score_of([game("draw", None)]), (0, -999))


class TestTheCandidates(unittest.TestCase):
    def test_multipliers_of_a_live_weight_skip_the_current_value(self):
        self.assertEqual(tune.candidates("loss", 0.5), [0, 0.125, 0.25, 1, 2])
        self.assertNotIn(0.5, tune.candidates("loss", 0.5))

    def test_a_zero_weight_gets_absolute_values(self):
        self.assertEqual(tune.candidates("hq_pull", 0), [25, 50, 100, 200, 400, 800])
        self.assertEqual(tune.candidates("capture", 0), [1, 2, 5, 10])
        self.assertNotIn(50, tune.candidates("hq_pull", 50))


if __name__ == "__main__":
    unittest.main()


class TestMissionWeightsIndex(unittest.TestCase):
    """data/mission_weights.json names files that exist, load, and match the
    fixtures that played them."""

    def test_every_entry_resolves(self):
        import json
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root))
        from engine import advisor
        idx = json.loads((root / "data" / "mission_weights.json").read_text(encoding="utf-8"))
        self.assertTrue(idx["missions"])
        for m in idx["missions"]:
            with self.subTest(mission=m["name"]):
                w = advisor.load_weights(root / m["weights"]) if m["weights"] else {}
                if m["fixture"]:
                    fx = root / "tests" / "fixtures" / "acceptance" / m["fixture"]
                    self.assertTrue(fx.is_dir(), fx)
                    played = fx / "weights.json"
                    if played.exists():
                        self.assertEqual(advisor.load_weights(played), w)
                    else:
                        self.assertEqual(w, {})

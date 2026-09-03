"""The differential corpus, replayed offline (ROADMAP step 2).

tools/sim_diff.py drove every case in tests/fixtures/sim_diff/corpus.json on
the game once and recorded the before/after dumps under runs/ and the
verdicts in results.json. This file re-applies each case's action to its
before-dump with today's engine/sim.py and requires the verdict and the
list of contradicted fields to be exactly what was recorded -- so a change
to the forward model that gains or loses agreement with the game shows up
here without an emulator, and a recorded contradiction cannot quietly
become a silent one.

It also pins the headless dumper: harness/mesen_state.lua's dump of each
parked state matches the mGBA dump of the same map tile for tile.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import sim_diff                                               # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "sim_diff"
RESULTS = json.loads((FIX / "results.json").read_text(encoding="utf-8"))
CORPUS = {c["name"]: c for c in sim_diff.load_corpus()}


class TestTheDumper(unittest.TestCase):
    def test_every_parked_state_matches_the_mgba_dump_tile_for_tile(self):
        for name in sim_diff.STATES:
            with self.subTest(state=name):
                self.assertTrue((sim_diff.STATES_DIR / f"{name}.json").exists())
                self.assertEqual(sim_diff.mgba_check(name), [])

    def test_the_dumps_load_with_the_full_schema(self):
        for name in sim_diff.STATES:
            b = sim_diff.load(sim_diff.STATES_DIR / f"{name}.json")
            self.assertEqual((b.width, b.height), (15, 10))
            self.assertIsNotNone(b.repair_free)
            self.assertIsNotNone(b.funds_per_property)
            self.assertTrue(all(a.power_uses is not None for a in b.armies))
            self.assertTrue(any(u.cargo for u in b.units) or name != "vs15_p2")


class TestTheCorpus(unittest.TestCase):
    def test_every_corpus_case_compiles(self):
        for case in CORPUS.values():
            with self.subTest(case=case["name"]):
                sim_diff.compile_case(case, [])

    def test_the_result_log_covers_the_corpus(self):
        recorded = {d["name"] for d in RESULTS["drives"]}
        self.assertEqual(set(CORPUS), recorded)

    def test_every_kind_was_driven(self):
        driven = {d["kind"] for d in RESULTS["drives"] if d["driven"]}
        for kind in ("wait", "attack", "capture", "supply", "load", "drop",
                     "join", "dive", "rise", "trap", "build", "power", "end_turn"):
            self.assertIn(kind, driven)

    def test_the_recorded_verdicts_replay_from_the_dumps(self):
        problems = sim_diff.check_recorded()
        self.assertEqual(problems, [])

    def test_the_summary_matches_the_rows(self):
        self.assertEqual(RESULTS["summary"], sim_diff.summarise(RESULTS["drives"]))

    def test_most_of_the_corpus_agrees(self):
        s = RESULTS["summary"]
        self.assertGreaterEqual(s["driven"], 50)
        self.assertGreaterEqual(s["agree"], 45)


if __name__ == "__main__":
    unittest.main()

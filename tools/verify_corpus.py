"""Compute `verified_against_emulator` from the evidence, instead of asserting it.

    python tools/verify_corpus.py            # report
    python tools/verify_corpus.py --write    # recompute and store it

WHAT THE FLAG MEANS. `data/aw1_damage.json` carries
`provenance.verified_against_emulator`, and `engine/damage.py:resolve()` refuses
to give advice while it is false. It is the difference between "these numbers
came off the ROM" and "the arithmetic wrapped around them reproduces what the
game actually did".

WHY THIS FILE EXISTS. Nothing computed it. `tools/extract_tables.py` wrote
`True` into the provenance block unconditionally -- a ROM table extractor
asserting that an emulator calibration passed, which is a fact it has no access
to -- and rewriting the whole file, so re-running it re-armed the flag whatever
the state of the model. `tests/calibrate.py` only ever *printed* "set it to
true" for a human to do by hand, and a test asserted it was true. Between them
there was no green state in which the flag could honestly be false: clearing it
failed the suite, and re-extracting undid it.

The evidence was there the whole time and ran on every test invocation. This
just wires it up: the flag becomes the cached result of replaying every recorded
measurement against the engine, and the record says what was replayed, so a
stale claim is visible rather than plausible.

WHAT IT DOES NOT DO. It does not check that the corpus is *right*, only that the
engine reproduces it. A measurement taken wrong stays wrong, and the flag will
happily go true over it. That is what `docs/ASSUMPTIONS.md` and the control
cases in each sweep are for.
"""
import argparse
import csv
import glob
import io
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.damage import (DEFAULT_DISPLAY, SURVIVING_VARIANTS, VARIANTS,
                           DATA as DAMAGE_JSON)  # noqa: E402

REPLAY_TESTS = "tests/test_corpus.py"


def count_corpus():
    """What is on disk to be replayed, counted rather than remembered."""
    rows = [r for r in csv.DictReader(
        open(ROOT / "harness" / "observations.csv", encoding="utf-8"))
        if r.get("mode") == "exact"]
    sweeps, cases = [], 0
    for p in sorted(glob.glob(str(ROOT / "tests" / "fixtures" / "*.json"))):
        d = json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        if "cases" in d:
            sweeps.append(pathlib.Path(p).name)
            cases += len(d["cases"])
    return {"observations": len(rows), "sweeps": len(sweeps),
            "sweep_cases": cases}


# The replay module also holds one test ABOUT this flag, and it must not be part
# of computing it. Leaving it in closes the loop again by a subtler route: that
# test asserts the flag is true, so clearing the flag fails the test, which makes
# the computed answer false, which then "agrees" with the cleared flag. The tool
# would report agreement in exactly the case it exists to catch -- and it did,
# until this was caught by clearing the flag and watching nothing happen.
NOT_A_REPLAY = "test_the_verification_flag_is_derived_from_this_corpus"


def _flatten(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from _flatten(t)
        else:
            yield t


def run_replays():
    """Run the replay suite and report whether every measurement reproduced."""
    sys.path.insert(0, str(ROOT / "tests"))
    import test_corpus
    loaded = unittest.defaultTestLoader.loadTestsFromModule(test_corpus)
    suite = unittest.TestSuite(t for t in _flatten(loaded)
                               if NOT_A_REPLAY not in t.id())
    buf = io.StringIO()
    result = unittest.TextTestRunner(stream=buf, verbosity=0).run(suite)
    failures = [str(t) for t, _ in result.failures + result.errors]
    return result.wasSuccessful(), result.testsRun, failures, buf.getvalue()


def engine_fingerprint():
    return {"display_rule": DEFAULT_DISPLAY,
            "surviving_variants": list(SURVIVING_VARIANTS)}


def contradictions(doc, corpus):
    """Claims in the stored file that the engine or the corpus disagrees with.

    The `calibration` block was written by the ROM extractor years of findings
    ago and nothing kept it in step, so it still named a display rule that has
    since been refuted. A record nobody checks decays silently; this checks it.
    """
    out = []
    cal = doc.get("calibration", {})
    if cal.get("observations") != corpus["observations"]:
        out.append(f"calibration.observations says {cal.get('observations')}, "
                   f"the corpus holds {corpus['observations']}")
    rule = str(cal.get("display_rule", ""))
    if not rule.startswith(DEFAULT_DISPLAY):
        out.append(f"calibration.display_rule says {rule.split(' ')[0]!r}, "
                   f"the engine uses {DEFAULT_DISPLAY!r}")
    surviving = cal.get("formula_variants_surviving")
    if surviving is not None and list(surviving) != list(SURVIVING_VARIANTS):
        out.append(f"calibration.formula_variants_surviving says {surviving}, "
                   f"the engine has {list(SURVIVING_VARIANTS)}")
    if len(SURVIVING_VARIANTS) == 1 and "two survivors" in str(
            cal.get("residual_uncertainty", "")):
        out.append("calibration.residual_uncertainty still describes two "
                   "surviving variants; one survives")
    return out


def refresh(doc, corpus, ok, ntests):
    doc.setdefault("provenance", {})["verified_against_emulator"] = ok
    doc["provenance"]["verification"] = {
        "method": "every recorded measurement replayed against the engine",
        "computed_by": "tools/verify_corpus.py",
        "tests": REPLAY_TESTS,
        "tests_run": ntests,
        "replayed": corpus,
        "engine": engine_fingerprint(),
    }
    cal = doc.setdefault("calibration", {})
    cal["observations"] = corpus["observations"]
    cal["display_rule"] = (
        f"{DEFAULT_DISPLAY} -- both operands scale by ceil(internal/10). "
        "Measured by writing HP and sweeping luck seeds; floor, floor_min1 and "
        "round are each refuted by a recorded board. See ASSUMPTIONS A9a.")
    cal["formula_variants_surviving"] = list(SURVIVING_VARIANTS)
    cal["formula_variants_refuted"] = [v for v in VARIANTS
                                       if v not in SURVIVING_VARIANTS]
    # The prose either describes an envelope over several variants or a single
    # identified one, and which it is follows from the list above. Leaving the
    # old text in place is how the file came to claim two survivors long after
    # one of them was refuted.
    if len(SURVIVING_VARIANTS) == 1:
        cal["residual_uncertainty"] = (
            f"None on the variant axis: {SURVIVING_VARIANTS[0]} is the only "
            "one left, identified positively by the shape of a 128-attack "
            "histogram rather than by absence of a counter-example, so "
            "max_damage is a figure and not an envelope. What remains open is "
            "listed in docs/ASSUMPTIONS.md.")
        cal["why_not_resolved"] = (
            "Resolved. Seeded sweeps reach the whole luck range, which "
            "frame-timed replays could not, and the surviving variants predict "
            "different histogram SHAPES -- so the top of the range stopped "
            "being the only discriminator. See ASSUMPTIONS A4.")
    return doc


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="store the computed flag in data/aw1_damage.json")
    a = ap.parse_args(argv)

    corpus = count_corpus()
    doc = json.loads(DAMAGE_JSON.read_text(encoding="utf-8"))
    stored = doc.get("provenance", {}).get("verified_against_emulator")

    print(f"corpus on disk: {corpus['observations']} observations, "
          f"{corpus['sweeps']} sweeps, {corpus['sweep_cases']} swept cases")
    ok, ntests, failures, log = run_replays()
    print(f"replay: {ntests} tests from {REPLAY_TESTS} -> "
          f"{'all reproduce' if ok else 'FAILED'}")
    for f in failures:
        print(f"  FAILED {f}")
    if not ok:
        print(log[-2000:])

    for c in contradictions(doc, corpus):
        print(f"  !! {c}")

    print(f"\nstored flag: {stored}    computed: {ok}")
    if stored == ok and not a.write:
        print("in agreement; nothing to do")
    if not a.write:
        if stored != ok:
            print("DISAGREE -- re-run with --write to store the computed value")
        return 0 if stored == ok else 1

    DAMAGE_JSON.write_text(
        json.dumps(refresh(doc, corpus, ok, ntests), indent=1) + "\n",
        encoding="utf-8")
    print(f"wrote {DAMAGE_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

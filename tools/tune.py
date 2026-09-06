"""Tune the planner's weights against the CPU port for the debrief's rank.

A coordinate search: one weight at a time, every candidate value played
out from the start board(s) over several RNG seeds (the port's unit
randoms differ per seed; the planner is deterministic), the best kept,
the next weight taken, passes repeated until none improves. The score of
a weight set is the mean rank total a win earns (engine/rank.py; a loss
or a draw scores 0), with fewer days breaking ties -- the rank IS the
objective, and Speed is the term with room in it (DERIVATION 58, 59).

    python tools/tune.py tests/fixtures/acceptance/m01a/t00.start.json \\
        --seeds 0,1,2 --weights hq_pull,objective_pull,damage_taken,loss \\
        --passes 2 --workers 8 --out harness/out/tune/m01.json

Every game played is appended to the --out file as it finishes, so a
search can be read while it runs and resumed by hand.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from engine import advisor                                       # noqa: E402

# Candidate values per weight: multipliers of the current value, or
# absolute values for a weight that starts at zero.
MULTIPLIERS = (0.0, 0.25, 0.5, 2.0, 4.0)
ABSOLUTE = {"hq_pull": (25, 50, 100, 200, 400, 800)}


def play(job: dict) -> dict:
    """One game in a worker: (state, planner, seed, weights) -> the result."""
    import sparring                                              # noqa: E402  (worker import)
    from engine import cpu_ai, sim                               # noqa: E402
    from engine.state import load                                # noqa: E402
    board = load(job["state"])
    planner = job["planner"] or board.active_player
    cpu = next(p for p in sim.players_in_order(board) if p != planner)
    ctx = cpu_ai.Context.from_dump(job["state"], player=cpu)
    r = sparring.spar(board, ctx, planner, days=job["days"], weights=job["weights"] or None,
                      reply="cpu", branches=1, seed=job["seed"],
                      state_name=pathlib.Path(job["state"]).stem, par=sparring.par_of(job["state"]))
    return {"state": job["state"], "seed": job["seed"], "weights": job["weights"],
            "outcome": r.outcome, "reason": r.reason, "end_day": r.stats.get("days"),
            "rank": r.rank, "stats": r.stats, "lost": r.lost, "taken": r.taken,
            "seconds": round(r.seconds, 1)}


def score_of(results: list) -> tuple:
    """(mean rank total, -mean end day): higher is better."""
    totals = [(r["rank"]["total"] if r["rank"] else 0) for r in results]
    days = [r["end_day"] or 999 for r in results]
    return (statistics.mean(totals), -statistics.mean(days))


def candidates(name: str, current: float) -> list:
    if name in ABSOLUTE or current == 0:
        vals = list(ABSOLUTE.get(name, (1, 2, 5, 10)))
    else:
        vals = [current * m for m in MULTIPLIERS]
    out = []
    for v in vals:
        v = int(v) if float(v).is_integer() else round(v, 4)
        if v != current and v not in out:
            out.append(v)
    return out


def describe(results: list) -> str:
    wins = [r for r in results if r["outcome"] == "win"]
    letters = "".join(r["rank"]["letter"] if r["rank"] else "-" for r in results)
    days = [r["end_day"] for r in results]
    return f"{letters} days {days} wins {len(wins)}/{len(results)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("states", nargs="+")
    ap.add_argument("--planner", type=int, default=0, help="the planner's side (default: the dump's active player)")
    ap.add_argument("--seeds", default="dump",
                    help="RNG seeds, comma-separated; 'dump' is the state's own RNG, the one the "
                         "real game continues from (default). Olaf's unit randoms do move with it: "
                         "the S set of DERIVATION 59 was an S at seed 0 and an A at the dump's.")
    ap.add_argument("--weights", default="hq_pull,objective_pull,damage_taken,loss,kill,capture",
                    help="weights to search, in order")
    ap.add_argument("--start", help="JSON file of weights to start from (else advisor.WEIGHTS)")
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--days", type=int, default=30, help="day cap per game")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default="harness/out/tune/tune.json")
    a = ap.parse_args()
    seeds = [None if x == "dump" else int(x) for x in a.seeds.split(",") if x]
    names = [w for w in a.weights.split(",") if w]
    unknown = set(names) - set(advisor.WEIGHTS)
    if unknown:
        raise SystemExit(f"unknown weight(s) {sorted(unknown)}")
    current = dict(json.loads(pathlib.Path(a.start).read_text(encoding="utf-8"))) if a.start else {}
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    played: list = []

    def evaluate_all(pool, trials: list) -> list:
        """Every (label, weights) trial's games in one parallel batch -- one
        seed on one board is one game, so a weight's candidates run
        together rather than one after another. Returns the trials' result
        lists in order."""
        jobs, owner = [], []
        for i, (label, weights) in enumerate(trials):
            for s in a.states:
                for seed in seeds:
                    jobs.append({"state": s, "planner": a.planner, "seed": seed, "days": a.days,
                                 "weights": dict(weights)})
                    owner.append(i)
        t0 = time.time()
        results = list(pool.map(play, jobs))
        per = [[] for _ in trials]
        for i, r in zip(owner, results):
            r["label"] = trials[i][0]
            per[i].append(r)
        played.extend(results)
        out.write_text(json.dumps(played, indent=1), encoding="utf-8")
        for (label, _), res in zip(trials, per):
            sc = score_of(res)
            print(f"  {label:32s} score {sc[0]:6.1f} mean day {-sc[1]:5.1f}  {describe(res)}", flush=True)
        print(f"  ({len(jobs)} game(s) in {time.time() - t0:.0f}s)", flush=True)
        return per

    def evaluate(pool, weights: dict, label: str) -> list:
        return evaluate_all(pool, [(label, weights)])[0]

    with concurrent.futures.ProcessPoolExecutor(max_workers=a.workers) as pool:
        print(f"baseline: {current or 'advisor.WEIGHTS'}", flush=True)
        best_results = evaluate(pool, current, "baseline")
        best = score_of(best_results)
        for p in range(1, a.passes + 1):
            improved = False
            print(f"== pass {p}", flush=True)
            for name in names:
                cur = current.get(name, advisor.WEIGHTS[name])
                print(f"-- {name} (now {cur})", flush=True)
                trials = []
                for v in candidates(name, cur):
                    trial = dict(current); trial[name] = v
                    trials.append((f"{name}={v}", trial))
                for (label, trial), res in zip(trials, evaluate_all(pool, trials)):
                    sc = score_of(res)
                    if sc > best:
                        best, best_results, current = sc, res, trial
                        improved = True
                        print(f"   ^ kept: {label}", flush=True)
            if not improved:
                print("no weight improved the score this pass; stopping")
                break
    print("\nbest weights:", json.dumps(current))
    print("best:", describe(best_results), "score", best)
    (out.with_suffix(".best.json")).write_text(json.dumps(current, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

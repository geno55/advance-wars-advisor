"""A turn's plan for the active player, read off a live board.

    python tools/advise.py state.json
    python tools/advise.py state.json --player 2 --no-fog
    python tools/advise.py state.json --weight kill=1.0 --weight objective_pull=0

The OPINION tool -- the first one in this repo. Everything it prints is one
of two things, and the output keeps them apart on every line: a FACT quoted
from the action layer (a strike range, a counter, next turn's focus fire,
the capture count, the repair charge), or a WEIGHT from engine/advisor.py's
one table, printed as `w (heuristic) x quantity`. The plan is greedy with
sequential commit: each step is scored on the board the step before it
leaves behind (engine/sim.py, worst case for the actor), so the numbers on
step 3 are about a board where steps 1 and 2 have happened.

Read docs/ADVISOR.md before trusting a plan: where the planner is naive is
written down there, and none of it is fixed by a weight.
"""
import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import advisor, fog                                 # noqa: E402
from engine.state import load, summarise                        # noqa: E402


def parse_weight(text: str) -> tuple:
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"--weight wants name=value, got {text!r}")
    name, value = text.split("=", 1)
    name = name.strip()
    if name not in advisor.WEIGHTS:
        raise argparse.ArgumentTypeError(
            f"no weight named {name!r}; the table is "
            f"{', '.join(sorted(advisor.WEIGHTS))}")
    try:
        return name, float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"weight {name} wants a number, got {value!r}")


def main():
    p = argparse.ArgumentParser(
        description="a turn's plan for one player: facts quoted, weights labelled")
    p.add_argument("state", help="JSON from harness/mgba_state.lua")
    p.add_argument("--player", type=int,
                   help="whose turn to plan; defaults to the active player")
    p.add_argument("--weather", help="ask a hypothetical instead of the board's")
    p.add_argument("--fog", dest="fog", action="store_true", default=None,
                   help="fog is on; only visible enemies count")
    p.add_argument("--no-fog", dest="fog", action="store_false",
                   help="fog is off; silences the unknown warning")
    p.add_argument("--weight", type=parse_weight, action="append", default=[],
                   metavar="NAME=VALUE",
                   help="override one weight for this run (repeatable)")
    p.add_argument("--luck", default="min",
                   help="the strike roll the plan's boards assume: min "
                        "(worst case, default), max, or an int")
    p.add_argument("--no-terms", action="store_true",
                   help="one line per step, without the term breakdown")
    p.add_argument("--board", action="store_true",
                   help="also print the board the plan leaves behind")
    a = p.parse_args()

    board = load(a.state)
    print(summarise(board).splitlines()[0])
    for w in board.warnings:
        print(f"  !! {w}")

    player = a.player or board.active_player
    if not player:
        print("!! no active player in this dump and --player not given")
        return 1
    fog_on = board.fog if a.fog is None else a.fog
    if fog_on:
        print("\n" + fog.summarise(board, player))

    luck = a.luck
    if luck not in ("min", "max"):
        try:
            luck = int(luck)
        except ValueError:
            print(f"!! --luck wants min, max or an int, not {luck!r}")
            return 1

    warnings = []
    t0 = time.time()
    plan = advisor.plan(board, player, weights=dict(a.weight) or None,
                        weather=a.weather, fog=a.fog, luck=luck,
                        warnings=warnings)
    took = time.time() - t0

    print()
    print(advisor.render(plan, terms=not a.no_terms))
    if a.weight:
        print("\n  weights overridden for this run: "
              + ", ".join(f"{k}={v:g}" for k, v in a.weight))

    if a.board:
        print("\nthe board the plan leaves behind (worst case for you):")
        print(summarise(plan.board_after))

    fog_notes = [w for w in warnings if w.startswith("fog of war is ON")]
    if fog_notes:
        warnings = [w for w in warnings if w not in fog_notes]
        warnings.append(
            "fog of war is ON: hidden units are absent from every number "
            "above; the per-step blind-spot counts are the tiles they "
            "could be in.")
    for w in warnings:
        print(f"\n  !! {w}")
    print(f"\n  {len(plan.steps)} step(s) in {took:.1f}s. Every weight above "
          "is a heuristic from engine/advisor.py WEIGHTS; every other number "
          "is a quoted fact. See docs/ADVISOR.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

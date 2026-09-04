"""A turn's plan for the active player, read off a live board.

    python tools/advise.py state.json
    python tools/advise.py state.json --player 2 --no-fog
    python tools/advise.py state.json --weight kill=1.0 --weight objective_pull=0
    python tools/advise.py state.json --reply none          # the greedy plan alone
    python tools/advise.py state.json --reply planner --branches 5

The OPINION tool -- the first one in this repo. Everything it prints is one
of two things, and the output keeps them apart on every line: a FACT quoted
from the action layer (a strike range, a counter, next turn's focus fire,
the capture count, the repair charge), or a WEIGHT from engine/advisor.py's
one table, printed as `w (heuristic) x quantity`. The plan is greedy with
sequential commit: each step is scored on the board the step before it
leaves behind (engine/sim.py, worst case for the actor), so the numbers on
step 3 are about a board where steps 1 and 2 have happened.

The plan is then put in front of a modelled REPLY (ROADMAP step 4): the
greedy plan and the runner-up at its closest calls are each followed by the
opponent's turn -- the game's own AI, ported (engine/cpu_ai), when the
opponent is the CPU; this planner one ply deep when it is not -- and the
proposal whose board at your next turn start evaluates best is the one
printed, with what the opponent did under it. `--reply` picks the model
(auto: the CPU port when the dump says the opponent is the CPU or when a
CPU context can be built from it, the planner otherwise; none: the greedy
plan alone).

Read docs/ADVISOR.md before trusting a plan: where the planner is naive is
written down there, and none of it is fixed by a weight.
"""
import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import advisor, cpu_ai, fog, sim                    # noqa: E402
from engine.state import load, summarise                        # noqa: E402


def reply_model(board, path, player: int, asked: str):
    """(model, cpu_ctx, why): which reply model to use for this dump."""
    if asked == "none":
        return None, None, "no reply modelled (--reply none)"
    if asked == "planner":
        return "planner", None, "the planner, one ply (--reply planner)"
    opponents = [p for p in sim.players_in_order(board) if p != player]
    if not opponents:
        return None, None, "no opponent on this board"
    opp = opponents[0]
    try:
        control = board.army(opp).control
    except StopIteration:
        control = None
    if asked == "auto" and control == 1:
        return "planner", None, f"P{opp} is human-controlled (army +0x1B = 1)"
    try:
        ctx = cpu_ai.Context.from_dump(path, player=opp)
    except (NotImplementedError, KeyError, IndexError) as e:
        if asked == "cpu":
            print(f"!! --reply cpu: no CPU context from this dump ({e}); "
                  f"the planner stands in")
        return "planner", None, f"no CPU context from this dump ({e})"
    why = (f"P{opp} is CPU-controlled (army +0x1B = 2)" if control == 2
           else f"P{opp}'s controller is not in this dump; the CPU port is "
                f"asked first and the planner stands in where it cannot play")
    return "cpu", ctx, why


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
    p.add_argument("--reply", choices=("auto", "cpu", "planner", "none"),
                   default="auto",
                   help="the opponent's reply model: auto (default), cpu "
                        "(engine/cpu_ai from this dump), planner (this "
                        "planner, one ply), none (the greedy plan alone)")
    p.add_argument("--branches", type=int, default=3,
                   help="how many runner-up variants to try against the "
                        "reply (default 3)")
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

    model, cpu_ctx, why = reply_model(board, a.state, player, a.reply)
    print(f"\nreply model: {model or 'none'} -- {why}")

    warnings = []
    t0 = time.time()
    plan = advisor.plan(board, player, weights=dict(a.weight) or None,
                        weather=a.weather, fog=a.fog, luck=luck,
                        warnings=warnings, reply=model, cpu_ctx=cpu_ctx,
                        branches=a.branches)
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
    tried = (f", {len(plan.candidates)} proposals put to the reply"
             if plan.candidates else "")
    print(f"\n  {len(plan.steps)} step(s) in {took:.1f}s{tried}. Every weight "
          "above is a heuristic from engine/advisor.py WEIGHTS; every other "
          "number is a quoted fact. See docs/ADVISOR.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

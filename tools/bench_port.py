"""How fast the port plays: a random-policy game against the CPU port from
a parked state, timing enumeration, apply and the CPU turn -- the number a
search or a trainer lives on (docs/PLAN-search.md).

    python tools/bench_port.py ft2 ft3 m01
"""
import dataclasses
import pathlib
import random
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from engine import actions, cpu_ai, sim                              # noqa: E402
from engine.state import load                                        # noqa: E402
from sparring import decided                                         # noqa: E402


def random_game(name, seed, days=20):
    path = ROOT / "tests" / "fixtures" / "sim_diff" / "states" / f"{name}.json"
    board = load(path)
    planner = board.active_player
    players = sim.players_in_order(board)
    cpu = next(p for p in players if p != planner)
    ctx = cpu_ai.Context.from_dump(path, player=cpu)
    rnd = random.Random(seed)
    start = board
    n_dec = n_cpu = 0
    t_enum = t_apply = t_cpu = 0.0
    while board.day - start.day < days:
        if board.active_player == planner:
            while True:
                t0 = time.perf_counter()
                acts = actions.all_actions(board, planner)
                t_enum += time.perf_counter() - t0
                flat = [a for lst in acts.values() for a in lst]
                if not flat:
                    break
                a = rnd.choice(flat)
                t0 = time.perf_counter()
                board = sim.apply(board, a, luck="min", rng_state=rnd.getrandbits(32))
                t_apply += time.perf_counter() - t0
                n_dec += 1
                if decided(start, board, planner, cpu):
                    return board, n_dec, n_cpu, t_enum, t_apply, t_cpu
        else:
            t0 = time.perf_counter()
            turn = cpu_ai.predict(board, cpu, ctx, rng=board.rng or 0)
            t_cpu += time.perf_counter() - t0
            n_cpu += 1
            ctx = turn.ctx
            board = dataclasses.replace(turn.board, rng=turn.rng)
        if decided(start, board, planner, cpu):
            break
        board = sim.end_turn(board)
    return board, n_dec, n_cpu, t_enum, t_apply, t_cpu


def main():
    for name in sys.argv[1:] or ["ft2", "ft3", "m01"]:
        t0 = time.perf_counter()
        b, nd, nc, te, ta, tc = random_game(name, 1)
        tot = time.perf_counter() - t0
        print(f"{name}: {tot:.2f}s for {b.day} days; {nd} decisions "
              f"(enum {te / max(nd, 1) * 1000:.1f} ms, apply {ta / max(nd, 1) * 1000:.2f} ms each); "
              f"{nc} CPU turns ({tc / max(nc, 1) * 1000:.0f} ms each)")


if __name__ == "__main__":
    main()

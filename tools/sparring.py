"""Sparring: the planner against the CPU port, in Python, to the end.

    python tools/sparring.py state.json
    python tools/sparring.py state.json --planner 2 --days 15
    python tools/sparring.py a.json b.json --both-sides --weight damage_taken=0.5
    python tools/sparring.py state.json --json results.json --aborts aborts/

ROADMAP step 5's harness. From a dumped state the planner (engine/advisor,
with its modelled reply) plays one side and the game's own AI, ported
(engine/cpu_ai), plays the other, turn about through the forward model,
until an HQ falls, a side loses its last unit, or the day cap. What comes
out is what a weight set is judged by: who won and how, the days it took,
the value each side lost, the properties held at the end, and a per-day
log of material and income. Nothing here touches the emulator; a game
costs the planner's plans.

Every game is deterministic: the port draws from the dump's RNG state and
the planner rolls nothing, so a weight set's result on a state is a
number, not a sample -- vary the states (or `--seed`) rather than repeat.

When the port meets a branch it has not read it raises NotImplementedError
naming the routine; the game stops there, is reported as an abort, and
with `--aborts DIR` the board at that moment is written as a dump the
state reader and cpu_ai.Context.from_dump both load -- a trace request for
the step 3 rig, which is how the port's coverage is meant to grow.

The caveats are the ROADMAP's: both players stand on sim.apply, so a bug
there is a belief both share; and the port is right only where it has
been traced, so a weight set that wins here has beaten the port, not yet
the game.
"""
import argparse
import dataclasses
import json
import pathlib
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import advisor, cpu_ai, economy, rank, sim         # noqa: E402
from engine.state import load                                   # noqa: E402

TERRAIN_HQ = 8


@dataclass
class Day:
    day: int
    mover: int                  # who just played
    worth: dict                 # player -> unit value after the turn
    funds: dict
    income: dict
    units: dict
    properties: dict
    note: str = ""


@dataclass
class Result:
    state: str
    planner: int
    cpu: int
    outcome: str                # "win", "loss", "draw" (day cap), "abort"
    reason: str                 # "rout", "hq", "day cap", or the abort's message
    days: int                   # days played, from the start day
    lost: int                   # value the planner's side lost to the enemy's turns
    taken: int                  # value the enemy's side lost to the planner's turns
    held: dict                  # properties held at the end, per player
    log: List[Day] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    abort_dump: Optional[str] = None
    seconds: float = 0.0
    # the debrief's counters as the game keeps them (DERIVATION 58): the
    # day the game ended on, the best day's kills, units the enemy fielded,
    # units we fielded and lost (a join is not a loss)
    stats: dict = field(default_factory=dict)
    # the rank a WIN earns (engine/rank.py), when the map's par is known
    rank: Optional[dict] = None

    def summary(self) -> str:
        hp = ", ".join(f"P{p} {n}" for p, n in sorted(self.held.items()))
        s = (f"{self.state}: planner P{self.planner} vs port P{self.cpu} -- "
             f"{self.outcome} by {self.reason} after {self.days} day"
             f"{'s' if self.days != 1 else ''}; lost {self.lost}, took "
             f"{self.taken}; properties {hp}; {self.seconds:.0f}s")
        if self.rank:
            r = self.rank
            s += (f"; rank {r['letter']} {r['total']} (speed {r['speed']}, power {r['power']}, "
                  f"technique {r['technique']}: day {r['days']}, best day {r['best_day']} of "
                  f"{r['enemy_fielded']}, lost {r['lost']} of {r['fielded']})")
        return s


def worth(board, player: int) -> int:
    return advisor.army_worth(board, player)


def snapshot(board, players, mover: int, note: str = "") -> Day:
    """`players` is the start board's roster: a routed side keeps its row."""
    return Day(day=board.day, mover=mover,
               worth={p: worth(board, p) for p in players},
               funds={p: board.army(p).funds for p in players},
               income={p: economy.income(board, p).amount for p in players},
               units={p: len(board.units_of(p)) for p in players},
               properties={p: economy.properties(board, p) for p in players},
               note=note)


def decided(start, board, planner: int, cpu: int):
    """(outcome, reason) once the game is over by the standard conditions
    -- an HQ changed hands, or a side that had units has none (the rout,
    stated in ASSUMPTIONS) -- else None."""
    for y in range(board.height):
        for x in range(board.width):
            if board.terrain[y][x] != TERRAIN_HQ:
                continue
            was, now = start.owner[y][x], board.owner[y][x]
            if was == now or not was:
                continue
            if was == cpu and now == planner:
                return "win", "hq"
            if was == planner and now == cpu:
                return "loss", "hq"
    if start.units_of(cpu) and not board.units_of(cpu):
        return "win", "rout"
    if start.units_of(planner) and not board.units_of(planner):
        return "loss", "rout"
    return None


def board_to_dump(board, ctx: cpu_ai.Context, source: str) -> dict:
    """The board as a state dump the reader and cpu_ai.Context.from_dump
    load -- what an abort leaves behind for the trace rig."""
    prof = bytearray(304)
    prof[:16] = bytes(ctx.profile["header"])
    for name, row in ctx.profile["units"].items():
        t = cpu_ai.type_id(name)
        prof[4 + 12 * t: 16 + 12 * t] = bytes(row)
    armies = []
    for a in board.armies:
        side = ctx.sides.get(a.player)
        armies.append({
            "player": a.player, "funds": a.funds, "income": a.income,
            "power": a.power, "co_id": a.co_id, "power_active": a.power_active,
            "power_ready": a.power_ready, "power_uses": a.power_uses,
            "control": a.control,
            "team": side.team if side else a.player - 1,
            "enemies": side.enemies if side else 0,
            "hq": list(side.hq) if side and side.hq else [128, 0]})
    units = [{"slot": u.slot, "player": u.player, "type": u.type, "x": u.x,
              "y": u.y, "hp": u.hp, "ammo": u.ammo, "capture": u.capture,
              "fuel": u.fuel, "acted": u.acted, "carrying": u.carrying,
              "loaded": u.loaded, "state": u.state, "cargo": u.cargo,
              "cargo2": u.cargo2, "ai": list(ctx.ai.get(u.slot, [0, 0, 0]))}
             for u in board.units]
    props = ([{"t": t, "x": x, "y": y} for t, x, y in ctx.properties]
             if ctx.properties else
             [{"t": board.terrain[y][x], "x": x, "y": y}
              for (x, y) in sorted(board.listed_properties or (), key=lambda p: (p[1], p[0]))])
    return {
        "_comment": f"sparring abort from {source}: the board the CPU port could not play",
        "source": source, "width": board.width, "height": board.height,
        "day": board.day, "active_player": board.active_player,
        "weather_index": board.weather_index, "fog": bool(board.fog),
        "repair_free": board.repair_free,
        "funds_per_property": board.funds_per_property,
        "rng": board.rng, "settings_6": ctx.settings_6, "settings_8": ctx.settings_8,
        "map_id": ctx.map_id, "settings_9": ctx.settings_9,
        "ai_profile": prof.hex(), "army0": list(ctx.army0), "flags_e4": ctx.flags_e4,
        "armies": armies, "units": units,
        "terrain": [{"y": y, "t": list(board.terrain[y]), "owner": list(board.owner[y])}
                    for y in range(board.height)],
        "properties": props,
    }


def spar(board, ctx: cpu_ai.Context, planner: int, *, days: int = 20,
         weights=None, reply: Optional[str] = "cpu", branches: int = 1,
         seed: Optional[int] = None, state_name: str = "board",
         abort_dir: Optional[pathlib.Path] = None,
         verbose: bool = False, par: Optional[int] = None) -> Result:
    """One game from `board`: the planner plays `planner`, the port the
    other side, until decided or `days` days from the start day. `par`,
    the map's par in days, lets a win be ranked as the debrief would."""
    t0 = time.time()
    start = board
    players = sim.players_in_order(board)
    cpu = next(p for p in players if p != planner)
    if seed is not None:
        board = dataclasses.replace(board, rng=seed)
    ctx = advisor._fresh_ctx(ctx)
    # a no-luck match (settings +6, DERIVATION 54) adds a flat 5 to every
    # strike: the planner's own forward model uses it, so the game played
    # here is the game the loop plays -- the tuned set of DERIVATION 59
    # routed the port on day 11 at worst-case luck and the real game on
    # day 17, from the same weights
    luck = 5 if getattr(ctx, "settings_6", 0) else "min"
    warnings: List[str] = []
    log = [snapshot(board, players, 0, "start")]
    lost = taken = 0
    outcome = reason = None
    abort_dump = None
    # the debrief's counters: every slot each side ever fielded, the
    # planner's slots merged away by joins, and the enemy units destroyed
    # on the PLANNER'S OWN turn each day -- the game credits a kill to the
    # side whose turn it is (mission two's real game: two of Grit's units
    # died on day 4, one of them to a counter on his turn, and the
    # debrief's best day read 1; DERIVATION 61)
    enemy_seen = {u.slot for u in board.units_of(cpu)}
    own_seen = {u.slot for u in board.units_of(planner)}
    joined: set = set()
    kills_by_day: dict = {}
    while board.day - start.day < days:
        mover = board.active_player
        before_w = {p: worth(board, p) for p in (planner, cpu)}
        if mover == planner:
            pl = advisor.plan(board, planner, weights=weights, reply=reply,
                              cpu_ctx=ctx if reply == "cpu" else None,
                              branches=branches, warnings=warnings, luck=luck)
            note = "; ".join(advisor.describe_action(s.action) for s in pl.steps
                             if s.action.kind in ("attack", "capture", "build", "power"))
            joined |= {s.action.target.slot for s in pl.steps
                       if s.action.kind == "join" and s.action.target is not None}
            before_enemy = {u.slot for u in board.units_of(cpu)}
            board = pl.board_after
            kills_by_day[board.day] = len(before_enemy - {u.slot for u in board.units_of(cpu)})
        else:
            try:
                turn = cpu_ai.predict(board, cpu, ctx, rng=board.rng or 0)
            except (NotImplementedError, RuntimeError) as e:
                # NotImplementedError: a branch the port has not read.
                # RuntimeError: the port predicted a command the action
                # layer offers no Action for -- one of the two is wrong,
                # and the dump is the case to settle it on.
                outcome, reason = "abort", str(e)
                if not isinstance(e, NotImplementedError):   # its subclass
                    reason = "port/action-layer disagreement: " + reason
                if abort_dir is not None:
                    abort_dir.mkdir(parents=True, exist_ok=True)
                    path = abort_dir / f"{state_name}-p{planner}-day{board.day}.json"
                    path.write_text(json.dumps(board_to_dump(board, ctx, state_name),
                                               indent=1), encoding="utf-8")
                    abort_dump = str(path)
                break
            note = "; ".join(
                [advisor._describe_command(c, board) for c in turn.commands
                 if c.name in ("fire", "capture")]
                + [f"buys {b['name']} at ({b['x']},{b['y']})" for b in turn.builds])
            ctx = turn.ctx
            board = dataclasses.replace(turn.board, rng=turn.rng)
        after_w = {p: worth(board, p) for p in (planner, cpu)}
        enemy_seen |= {u.slot for u in board.units_of(cpu)}
        own_seen |= {u.slot for u in board.units_of(planner)}
        if mover == planner:
            taken += max(0, before_w[cpu] - after_w[cpu])
        else:
            lost += max(0, before_w[planner] - after_w[planner])
        log.append(snapshot(board, players, mover, note))
        if verbose:
            d = log[-1]
            print(f"  day {d.day} P{mover}: {note or '(moves only)'}")
            print(f"    worth P{planner} {d.worth[planner]} P{cpu} {d.worth[cpu]}; "
                  f"income P{planner} {d.income[planner]} P{cpu} {d.income[cpu]}")
        done = decided(start, board, planner, cpu)
        if done:
            outcome, reason = done
            break
        board = sim.end_turn(board, warnings=warnings)
    if outcome is None:
        outcome, reason = "draw", "day cap"
    own_alive = {u.slot for u in board.units_of(planner)}
    stats = {"days": board.day, "best_day": max(kills_by_day.values(), default=0),
             "enemy_fielded": len(enemy_seen), "fielded": len(own_seen),
             "lost": len(own_seen - own_alive - joined), "par": par}
    ranked = None
    if outcome == "win" and par is not None:
        r = rank.score(days=stats["days"], par=par, best_day=stats["best_day"],
                       enemy_fielded=stats["enemy_fielded"], fielded=stats["fielded"],
                       lost=stats["lost"])
        ranked = dict(dataclasses.asdict(r), **stats)
    return Result(state=state_name, planner=planner, cpu=cpu, outcome=outcome,
                  reason=reason, days=board.day - start.day + 1, lost=lost,
                  taken=taken,
                  held={p: economy.properties(board, p) for p in players},
                  log=log, warnings=sorted(set(warnings)), abort_dump=abort_dump,
                  seconds=time.time() - t0, stats=stats, rank=ranked)


def par_of(path) -> Optional[int]:
    """The map's par in days from the dump's map_id, for the rank."""
    try:
        mid = json.loads(pathlib.Path(path).read_text(encoding="utf-8")).get("map_id")
        return rank.par_for(int(mid)) if mid is not None else None
    except (ValueError, IndexError, KeyError, TypeError):
        return None


def parse_weight(text: str) -> tuple:
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"--weight wants name=value, got {text!r}")
    name, value = text.split("=", 1)
    name = name.strip()
    if name not in advisor.WEIGHTS:
        raise argparse.ArgumentTypeError(f"no weight named {name!r}")
    return name, float(value)


def main():
    p = argparse.ArgumentParser(description="the planner against the CPU port, to the end")
    p.add_argument("states", nargs="+", help="dumps from harness/mesen_state.lua")
    p.add_argument("--planner", type=int, help="the side the planner plays "
                   "(default: the dump's active player)")
    p.add_argument("--both-sides", action="store_true",
                   help="play each state from both sides")
    p.add_argument("--days", type=int, default=20, help="day cap (default 20)")
    p.add_argument("--reply", choices=("cpu", "planner", "none"), default="cpu",
                   help="the planner's reply model (default cpu)")
    p.add_argument("--branches", type=int, default=1,
                   help="the planner's variant budget per turn (default 1)")
    p.add_argument("--weight", type=parse_weight, action="append", default=[],
                   metavar="NAME=VALUE")
    p.add_argument("--seed", type=int, help="override the dump's RNG state")
    p.add_argument("--json", help="write every result (with the day log) here")
    p.add_argument("--aborts", help="directory for the boards the port could not play")
    p.add_argument("-v", "--verbose", action="store_true", help="print each turn")
    a = p.parse_args()

    results = []
    for path in a.states:
        board = load(path)
        sides = sim.players_in_order(board)
        if len(sides) != 2:
            print(f"!! {path}: {len(sides)} players; sparring wants two")
            continue
        planners = sides if a.both_sides else [a.planner or board.active_player]
        for planner in planners:
            cpu = next(s for s in sides if s != planner)
            try:
                ctx = cpu_ai.Context.from_dump(path, player=cpu)
            except NotImplementedError as e:
                print(f"!! {path}: no CPU context ({e})")
                continue
            name = pathlib.Path(path).stem.replace(".before", "")
            print(f"{name}: planner P{planner}, port P{cpu}, day {board.day}")
            r = spar(board, ctx, planner, days=a.days, weights=dict(a.weight) or None,
                     reply=None if a.reply == "none" else a.reply,
                     branches=a.branches, seed=a.seed, state_name=name,
                     abort_dir=pathlib.Path(a.aborts) if a.aborts else None,
                     verbose=a.verbose, par=par_of(path))
            print("  " + r.summary())
            if r.abort_dump:
                print(f"  trace request written: {r.abort_dump}")
            results.append(r)
    if a.json:
        pathlib.Path(a.json).write_text(
            json.dumps([dataclasses.asdict(r) for r in results], indent=1),
            encoding="utf-8")
    if len(results) > 1:
        wins = sum(r.outcome == "win" for r in results)
        print(f"\n{wins}/{len(results)} won; days to win "
              f"{[r.days for r in results if r.outcome == 'win']}; "
              f"lost {sum(r.lost for r in results)}, took {sum(r.taken for r in results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

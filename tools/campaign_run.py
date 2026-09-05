"""Acceptance (ROADMAP step 6): the planner plays a whole match against the
game's own CPU on headless Mesen2, from a parked savestate to the result.

    python tools/campaign_run.py run --state vs15_p2 --player 2 --out harness/out/play/vs15
    python tools/campaign_run.py run --mss "C:/.../Advance Wars (USA) (Rev 1)_3.mss" --player 1 --out DIR
    python tools/campaign_run.py plan --dump t01.json --steps t01.steps.lua --player 2 ...   (the loop calls this)
    python tools/campaign_run.py judge --dump t07.after.json --player 2 --start t01.json

One emulator session per game. harness/mesen_play.lua runs the loop: it
dumps the board, shells out to `plan` here, drives the steps it gets back
through mesen_drive.lua's verified single-action driver, hands the turn
to the CPU through the cpu_turn step, saves a checkpoint savestate, and
repeats. `plan` reads the dump (engine/state.py), judges the game first
-- an enemy HQ that is ours or an enemy with no units left is the win,
our HQ theirs or no unit of ours the loss, both read off the board the
game left -- then plans the turn (engine/advisor.plan with the CPU port as
the modelled reply, or the planner standing in where the port cannot
build a context) and compiles the steps with tools/sim_diff.compile_action.
The steps go back as a Lua table file because Mesen's Lua reads no JSON.

The plan was scored in the worst-case world, so after every attack, build
or power -- and after any step the driver could not verify -- the rest of
the turn is re-planned from a fresh dump of what the game actually did.

What comes out (in --out): tNN.json / tNNrK.json dumps at each plan,
tNN.steps.lua and tNN.plan.txt (the rendered plan), tNN.after.json after
the CPU's turn, tNN.mss checkpoints, screenshots from the driver, play.log
and result.json: who won and how, read off the game.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import sim_diff                                                      # noqa: E402
from engine import actions, advisor, cpu_ai, sim                     # noqa: E402
from engine.state import load                                        # noqa: E402

TERRAIN_HQ = 8


# --------------------------------------------------------------------------
# judging: the win read off the board the game left
# --------------------------------------------------------------------------

def judge(board, player: int, start, days: int) -> tuple:
    """(over, note): "win" / "loss" / "daycap" / None with the reason.
    Standard conditions only (ROADMAP step 7 reads a mission's own when a
    result the standard ones do not explain shows up)."""
    others = sorted({p for b in (start, board)
                     for p in sim.players_in_order(b) if p != player})
    for y in range(board.height):
        for x in range(board.width):
            if board.terrain[y][x] != TERRAIN_HQ:
                continue
            was, now = start.owner[y][x], board.owner[y][x]
            if was in others and now == player:
                return "win", f"the enemy HQ at ({x},{y}) is ours on day {board.day}"
            if was == player and now in others:
                return "loss", f"our HQ at ({x},{y}) is P{now}'s on day {board.day}"
    if start.units_of(player) and not board.units_of(player):
        return "loss", f"no unit of ours is left on day {board.day}"
    if any(start.units_of(p) for p in others) and not any(board.units_of(p) for p in others):
        return "win", f"the enemy has no unit left on day {board.day}"
    if board.day - start.day >= days:
        return "daycap", f"day {board.day}: {days} days played"
    return None, ""


# --------------------------------------------------------------------------
# planning one turn for the driver
# --------------------------------------------------------------------------

def reply_context(dump_path, board, player: int):
    """The CPU port's context for the opponent, or None with why not."""
    cpu = next((p for p in sim.players_in_order(board) if p != player), None)
    if cpu is None:
        return None, None, "no opponent on the board"
    try:
        return "cpu", cpu_ai.Context.from_dump(dump_path, player=cpu), f"engine/cpu_ai as P{cpu}"
    except NotImplementedError as e:
        return "planner", None, f"the port cannot build a context ({e}); the planner stands in"


def compile_plan(plan, player: int, tag: str, warnings: list) -> list:
    steps = []
    for i, st in enumerate(plan.steps):
        a = st.action
        b = st.board_before
        if a.kind == "trap":
            continue
        if a.kind in ("build", "power"):
            every = []
        else:
            every = actions.actions_for(b, a.unit, warnings=warnings)
        step, _, _ = sim_diff.compile_action(b, a, every, f"{tag}-{i + 1}", warnings)
        step["describe"] = advisor.describe_action(a)
        steps.append(step)
    return steps


def cmd_plan(a) -> int:
    dump = pathlib.Path(a.dump)
    board = load(dump)
    start = load(a.start) if a.start and pathlib.Path(a.start).exists() else board
    warnings = list(board.warnings)
    out = {"steps": [], "over": None, "note": ""}
    over, note = judge(board, a.player, start, a.days)
    if over is None and board.active_player != a.player:
        over, note = "stuck", f"the dump says P{board.active_player} is to move, not us"
    text = [f"turn {a.turn} replan {a.replan}: day {board.day}, P{board.active_player} to move"]
    if over:
        out["over"], out["note"] = over, note
        text.append(f"over: {over} -- {note}")
    else:
        model, ctx, why = reply_context(dump, board, a.player)
        if a.reply == "none" or (a.replan > 0 and not a.reply_on_replan):
            model, ctx, why = None, None, "a mid-turn re-plan is greedy only (fast)"
        elif a.reply == "planner":
            model, ctx = "planner", None
        text.append(f"reply model: {model or 'none'} -- {why}")
        t0 = time.time()
        plan = advisor.plan(board, a.player, reply=model, cpu_ctx=ctx,
                            branches=a.branches, warnings=warnings)
        steps = compile_plan(plan, a.player, f"t{a.turn:02d}r{a.replan}", warnings)
        out["steps"] = steps
        out["note"] = "; ".join(s["describe"] for s in steps) or "nothing to do"
        text.append(advisor.render(plan, terms=False))
        text.append(f"{len(steps)} step(s) compiled in {time.time() - t0:.1f}s")
    for w in warnings:
        text.append(f"  !! {w}")
    steps_path = pathlib.Path(a.steps)
    steps_path.write_text("return " + sim_diff.lua(out) + "\n", encoding="utf-8")
    steps_path.with_suffix("").with_suffix(".plan.txt").write_text("\n".join(text) + "\n", encoding="utf-8")
    return 0


def cmd_judge(a) -> int:
    board = load(a.dump)
    start = load(a.start) if a.start else board
    over, note = judge(board, a.player, start, a.days)
    print(over or "playing", note)
    return 0


# --------------------------------------------------------------------------
# the run: one Mesen session for the whole game
# --------------------------------------------------------------------------

def make_play_script(run_dir: pathlib.Path, cfg: dict) -> str:
    libs = (sim_diff._libs()
            + (sim_diff.HARNESS / "mesen_play.lua").read_text(encoding="utf-8") + "\n")
    body = f"""
local M = AW
M.OUT = {sim_diff.lua(run_dir.as_posix() + "/")}
M.log = io.open(M.OUT .. "play.log", "w")
local CFG = {sim_diff.lua(cfg)}
local function main()
  M.wait(5)
  local r = M.play_game(CFG)
  local f = io.open(M.OUT .. "result.json", "w"); f:write(M.json(r)); f:close()
  M.log:close(); emu.stop(0)
end
"""
    return libs + body + sim_diff.SCHEDULER


def find_empty_tile_for(mss: pathlib.Path, dims) -> tuple:
    """An empty plain tile for the map menu, from a one-off dump of the
    parked state (the driver opens the map menu with A on an empty tile)."""
    probe = ROOT / "harness" / "out" / "play" / "probe"
    probe.mkdir(parents=True, exist_ok=True)
    dump = probe / "start.json"
    w, h = dims if dims else (None, None)
    body = f"""
local M = AW
M.OUT = {sim_diff.lua(probe.as_posix() + "/")}
M.log = io.open(M.OUT .. "dump.log", "w")
local function main()
  M.wait(5)
  local fh = assert(io.open({sim_diff.lua(mss.as_posix())}, "rb")); local bytes = fh:read("*a"); fh:close()
  emu.loadSavestate(bytes); M.wait(30)
  M.set_dims({sim_diff.lua(w)}, {sim_diff.lua(h)})
  M.dump({sim_diff.lua(dump.as_posix())}, {{ note = "start of a campaign_run" }})
  M.log:close(); emu.stop(0)
end
"""
    script = probe / "start.lua"
    script.write_text(sim_diff._libs() + body + sim_diff.SCHEDULER, encoding="utf-8")
    rc, _ = sim_diff.run_mesen(script, 120)
    if not dump.exists():
        raise SystemExit(f"the start dump failed (mesen exit {rc}); see {probe}")
    board = load(dump)
    return sim_diff.empty_tile(board), board, dump


def cmd_run(a) -> int:
    if a.mss:
        mss = pathlib.Path(a.mss)
        name = mss.stem
        dims = tuple(int(v) for v in a.dims.split("x")) if a.dims else None
    else:
        st = sim_diff.STATES[a.state]
        mss = sim_diff.MSS_DIR / st["mss"]
        name = a.state
        dims = tuple(int(v) for v in a.dims.split("x")) if a.dims else st["dims"]
    run_dir = pathlib.Path(a.out).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    empty, board, start_dump = find_empty_tile_for(mss, dims)
    start = run_dir / "t00.start.json"
    start.write_text(start_dump.read_text(encoding="utf-8"), encoding="utf-8")
    player = a.player or board.active_player
    cpu = next((p for p in sim.players_in_order(board) if p != player), 3 - player)
    print(f"{name}: planner P{player} against the game's P{cpu}, day {board.day}, "
          f"{board.width}x{board.height}, empty tile {empty}, out {run_dir}")
    plan_args = (f'--start "{start.as_posix()}" --days {a.days} --branches {a.branches} '
                 f'--reply {a.reply}' + (" --reply-on-replan" if a.reply_on_replan else ""))
    hqs = [{"x": x, "y": y, "owner": board.owner[y][x]}
           for y in range(board.height) for x in range(board.width)
           if board.terrain[y][x] == TERRAIN_HQ]
    cfg = {"name": name, "mss": mss.as_posix(), "player": player, "cpu": cpu, "hqs": hqs,
           "hidden_vbs": (ROOT / "harness" / "run_hidden.vbs").as_posix(),
           "run_dir": run_dir.as_posix() + "/", "empty": {"x": empty[0], "y": empty[1]},
           "w": dims[0] if dims else None, "h": dims[1] if dims else None,
           "max_turns": a.max_turns, "cpu_limit": a.cpu_limit,
           "replan_after": bool(a.replan_after),
           # unquoted on purpose: cmd.exe drops a command's leading quote
           "python_cmd": f'{pathlib.Path(sys.executable).as_posix()} '
                         f'{(ROOT / "tools" / "campaign_run.py").as_posix()}',
           "plan_args": plan_args}
    script = run_dir / "play.lua"
    script.write_text(make_play_script(run_dir, cfg), encoding="utf-8")
    t0 = time.time()
    rc, took = sim_diff.run_mesen(script, a.timeout)
    err = run_dir / "error.log"
    if err.exists():
        print("!! " + err.read_text(encoding="utf-8"))
    res_path = run_dir / "result.json"
    if not res_path.exists():
        print(f"mesen exit {rc} in {took:.0f}s, no result.json -- see {run_dir / 'play.log'}")
        return 1
    r = json.loads(res_path.read_text(encoding="utf-8"))
    r["wall_seconds"] = round(took)
    res_path.write_text(json.dumps(r, indent=1), encoding="utf-8")
    print(summary(r))
    return 0 if r.get("over") in ("win", "loss", "daycap") else 1


def summary(r: dict) -> str:
    turns = r.get("turns") or []
    done = sum(1 for t in turns for s in (t.get("steps") or []) if s.get("ok"))
    failed = sum(1 for t in turns for s in (t.get("steps") or []) if not s.get("ok"))
    last = turns[-1] if turns else {}
    return (f"{r.get('over')}: {last.get('note') or last.get('why') or ''} -- "
            f"{len(turns)} turn(s) to day {r.get('day')}, {done} step(s) driven, "
            f"{failed} failed, {sum(t.get('replans', 0) for t in turns)} replan(s), "
            f"{r.get('wall_seconds', '?')}s")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="play a whole match on headless Mesen")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--state", choices=list(sim_diff.STATES), help="a parked state by name")
    g.add_argument("--mss", help="a savestate file (any map)")
    r.add_argument("--player", type=int, help="the side the planner plays (default: the active player)")
    r.add_argument("--out", required=True, help="the run directory")
    r.add_argument("--dims", help="WxH when the state's height byte lies (the 15x10 VS state reads 13)")
    r.add_argument("--days", type=int, default=30, help="day cap counted from the start (default 30)")
    r.add_argument("--max-turns", type=int, default=40)
    r.add_argument("--branches", type=int, default=1, help="proposals per plan (default 1: greedy plus one)")
    r.add_argument("--reply", choices=("cpu", "planner", "none"), default="cpu")
    r.add_argument("--replan-after", action="store_true",
                   help="also re-plan after every attack, build and power (slow; default: only after a failed step)")
    r.add_argument("--reply-on-replan", action="store_true", help="model the reply on re-plans too")
    r.add_argument("--cpu-limit", type=int, default=3000, help="polls of ten frames to wait for the CPU")
    r.add_argument("--timeout", type=int, default=7200, help="Mesen's wall-clock cap in seconds")
    r.set_defaults(fn=cmd_run)
    q = sub.add_parser("plan", help="(called by the loop) judge and plan one turn from a dump")
    q.add_argument("--dump", required=True)
    q.add_argument("--steps", required=True, help="the Lua table file to write")
    q.add_argument("--player", type=int, required=True)
    q.add_argument("--turn", type=int, default=0)
    q.add_argument("--replan", type=int, default=0)
    q.add_argument("--start", help="the start-of-run dump (HQ owners, day)")
    q.add_argument("--days", type=int, default=30)
    q.add_argument("--branches", type=int, default=1)
    q.add_argument("--reply", choices=("cpu", "planner", "none"), default="cpu")
    q.add_argument("--reply-on-replan", action="store_true",
                   help="model the reply on mid-turn re-plans too (slow)")
    q.set_defaults(fn=cmd_plan)
    j = sub.add_parser("judge", help="read a dump: won, lost, or still playing")
    j.add_argument("--dump", required=True)
    j.add_argument("--player", type=int, required=True)
    j.add_argument("--start")
    j.add_argument("--days", type=int, default=30)
    j.set_defaults(fn=cmd_judge)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

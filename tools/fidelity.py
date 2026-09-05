"""The port against the real CPU's turns of an acceptance run.

An acceptance run (tools/campaign_run.py) saves, per turn N, the board at
our turn start (tNN.json), the steps it drove (tNN.steps.lua,
tNNrK.steps.lua after a re-plan; play.log says which ran and which failed)
and the board after the CPU's reply (tNN.after.json). On a no-luck match
(settings byte +6 set: the battle adds a flat 5, DERIVATION 54) our driven
steps replay exactly through sim.apply, so the board the CPU saw at End
Turn is recoverable, the port's prediction of its turn can be compared
with the after-dump field for field, and every disagreement is either a
port branch that went wrong silently or a forward-model gap -- the trace
rig then settles which.

    python tools/fidelity.py check harness/out/play/m01 harness/out/play/m01-2
    python tools/fidelity.py check harness/out/play/m01-2 --turn 7 --verbose
    python tools/fidelity.py trace harness/out/play/m01 4 m01-day4   # the rig on that turn

check's exit status is the number of turns that disagree; trace's is
cpu_trace's (0 when the port reproduces the trace).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import sim_diff                                                      # noqa: E402
from engine import actions, advisor, cpu, cpu_ai, sim                # noqa: E402
from engine.state import load                                        # noqa: E402

NO_LUCK = 5          # the flat luck a no-luck match adds (DERIVATION 54)


# --------------------------------------------------------------------------
# the steps file: a Lua table literal
# --------------------------------------------------------------------------

_TOKEN = re.compile(r'\s*(?:("(?:[^"\\]|\\.)*")|(-?\d+)|(true|false|nil)|([{}\[\]=,]))')


def lua_table(text: str):
    """The value of `return <table>` as Python: a table whose first entry
    is keyed is a dict, any other a list."""
    text = text.strip()
    if text.startswith("return"):
        text = text[len("return"):]
    toks = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"steps file: cannot read {text[pos:pos + 20]!r}")
        pos = m.end()
        s, n, kw, p = m.groups()
        if s is not None:
            toks.append(("str", json.loads(s)))
        elif n is not None:
            toks.append(("num", int(n)))
        elif kw is not None:
            toks.append(("kw", {"true": True, "false": False, "nil": None}[kw]))
        else:
            toks.append(("p", p))

    def value(i):
        kind, v = toks[i]
        if kind != "p":
            return v, i + 1
        if v != "{":
            raise ValueError(f"steps file: unexpected {v!r}")
        i += 1
        if toks[i] == ("p", "}"):
            return [], i + 1
        if toks[i] == ("p", "["):
            out = {}
            while toks[i] != ("p", "}"):
                assert toks[i] == ("p", "["), toks[i]
                key = toks[i + 1][1]
                assert toks[i + 2] == ("p", "]") and toks[i + 3] == ("p", "="), toks[i:i + 4]
                out[key], i = value(i + 4)
                if toks[i] == ("p", ","):
                    i += 1
            return out, i + 1
        out = []
        while toks[i] != ("p", "}"):
            item, i = value(i)
            out.append(item)
            if toks[i] == ("p", ","):
                i += 1
        return out, i + 1

    v, _ = value(0)
    return v


# --------------------------------------------------------------------------
# what the run did
# --------------------------------------------------------------------------

STEP_LINE = re.compile(r"^  step (t(\d\d)r(\d+)-\d+) (\w+): (ok|FAILED)")


def driven_steps(run: pathlib.Path) -> dict:
    """turn -> [(tag, kind, ok)] in the order play.log says they ran."""
    out = {}
    for line in (run / "play.log").read_text(encoding="utf-8", errors="replace").splitlines():
        m = STEP_LINE.match(line)
        if m:
            out.setdefault(int(m.group(2)), []).append((m.group(1), m.group(4), m.group(5) == "ok"))
    return out


def step_specs(run: pathlib.Path, turn: int) -> dict:
    """tag -> the compiled step, over the turn's plan and its re-plans."""
    out = {}
    for f in sorted(run.glob(f"t{turn:02d}*.steps.lua")):
        for st in lua_table(f.read_text(encoding="utf-8")).get("steps") or []:
            out[st["tag"]] = st
    return out


def spec_for(step: dict) -> dict:
    """A compiled driver step as the corpus-style spec sim_diff.find_action
    reads -- the inverse of sim_diff.compile_action, by kind, destination
    and target."""
    k = step["kind"]
    if k == "power":
        return {"kind": "power"}
    if k == "build":
        return {"kind": "build", "factory": [step["factory"]["x"], step["factory"]["y"]],
                "type": step["shop"][step["shop_index"]]}
    spec = {"kind": k, "slot": step["slot"], "tile": [step["dest"]["x"], step["dest"]["y"]]}
    checks = step.get("checks", [])
    if k == "attack":
        spec["target"] = step["target"]["slot"]
    elif k == "join":
        spec["target"] = next(c["slot"] for c in checks if c.get("gone"))
    elif k == "drop":
        c = next(c for c in checks if c.get("loaded") is False and c["slot"] != step["slot"])
        spec["target"] = c["slot"]
        spec["drop_tile"] = [c["x"], c["y"]]
    return spec


def action_for(board, step: dict, player: int):
    """The engine Action a compiled step drove, on `board`."""
    if board.active_player != player:
        raise LookupError(f"{step['tag']}: P{board.active_player} is to move, not P{player}")
    try:
        act, _ = sim_diff.find_action(board, spec_for(step))
    except ValueError as e:
        raise LookupError(f"{step['tag']}: {step.get('describe', step['kind'])}: {e}") from None
    return act


# --------------------------------------------------------------------------
# one turn
# --------------------------------------------------------------------------

def check_turn(run: pathlib.Path, turn: int, player: int, steps: list, *, verbose=False) -> dict:
    dump = run / f"t{turn:02d}.json"
    after = run / f"t{turn:02d}.after.json"
    board = load(dump)
    game = load(after)
    raw = json.loads(dump.read_text(encoding="utf-8"))
    specs = step_specs(run, turn)
    warnings: list = []
    ours = []
    for tag, kind, ok in steps:
        if not ok:
            continue
        act = action_for(board, specs[tag], player)
        kw = {"luck": NO_LUCK} if act.kind == "attack" else {}
        board = sim.apply(board, act, warnings=warnings, **kw)
        ours.append(advisor.describe_action(act))
    handed = sim.end_turn(board, warnings=warnings)
    cpu_player = handed.active_player
    ctx = cpu_ai.Context.from_dump(dump, player=cpu_player)
    res = {"turn": turn, "day": raw["day"], "ours": ours, "cpu": cpu_player,
           "commands": [], "diffs": None, "error": None, "warnings": []}
    try:
        t = cpu.predict(handed, cpu_player, ctx, rng=raw["rng"])
    except (NotImplementedError, RuntimeError) as e:
        res["error"] = f"{type(e).__name__}: {e}"
        return res
    res["commands"] = [advisor._describe_command(c, handed) for c in t.commands]
    res["builds"] = [f"buys {b['name']} at ({b['x']},{b['y']})" for b in t.builds]
    res["powers"] = len(t.powers)
    pred = sim.end_turn(t.board, warnings=warnings)
    res["diffs"] = sim_diff.diff_boards(pred, game)
    res["warnings"] = sorted(set(warnings))
    res["log"] = t.log if verbose else []
    return res


def report(r: dict, verbose: bool) -> str:
    head = f"turn {r['turn']:2d} (day {r['day']}): "
    if r["error"]:
        return head + "PORT ABORT " + r["error"]
    n = len(r["diffs"])
    line = head + ("agree" if n == 0 else f"{n} difference(s)") + \
        f" -- {len(r['commands'])} command(s)" + (f", {len(r['builds'])} build(s)" if r["builds"] else "") + \
        (", power fired" if r["powers"] else "")
    out = [line]
    if n or verbose:
        for c in r["commands"]:
            out.append("      port: " + c)
        for b in r["builds"]:
            out.append("      port: " + b)
        for d in r["diffs"]:
            out.append("      diff: " + d)
    if verbose:
        for w in r["warnings"]:
            out.append("      warn: " + w)
        for line in r["log"]:
            out.append("      log:  " + line)
    return "\n".join(out)


def cmd_trace(a) -> int:
    """The rig on one real turn: load the checkpoint our turn started from
    (tNN-1.mss, or --mss), drive our steps again, End Turn with the command
    hook on (tools/cpu_trace.py run_case) -- the CPU's records and draws for
    the turn the check found wrong, as a fixture under tests/fixtures/cpu."""
    import cpu_trace
    run = pathlib.Path(a.run)
    steps = driven_steps(run).get(a.turn)
    if not steps:
        raise SystemExit(f"{run}: play.log drove no step on turn {a.turn}")
    specs = step_specs(run, a.turn)
    mss = pathlib.Path(a.mss) if a.mss else run / f"t{a.turn - 1:02d}.mss"
    if not mss.exists():
        raise SystemExit(f"no checkpoint {mss}: pass --mss (turn 1 of a resumed run "
                         f"started from the earlier run's last checkpoint)")
    dump = run / f"t{a.turn:02d}.json"
    board = load(dump)
    state = f"ck-{run.name}-t{a.turn:02d}"
    sim_diff.STATES[state] = dict(
        mss=str(mss.resolve()), dims=(board.width, board.height), mgba=None,
        note=f"acceptance run {run.name}, the checkpoint turn {a.turn} started from")
    state_json = sim_diff.STATES_DIR / f"{state}.json"
    state_json.write_text(dump.read_text(encoding="utf-8"), encoding="utf-8")
    setup = [spec_for(specs[tag]) for tag, kind, ok in steps if ok]
    print(f"{a.name}: {state} + {len(setup)} step(s) of ours, then the CPU's turn")
    try:
        rec = cpu_trace.run_case(a.name, state, [], setup, a.limit,
                                 tuple(int(w, 0) for w in a.watch.split(",") if w))
    finally:
        state_json.unlink(missing_ok=True)      # the fixture's before-dump is the record
    print(cpu_trace.show(rec))
    if not rec["driven"]:
        return 1
    r = cpu_trace.predict(rec)
    for lab, rows in (("predicted", r["predicted"]), ("traced", r["traced"])):
        print(f"== {lab}")
        for (cid, slot, tile, b6, b7, rng) in rows:
            print(f"  {cpu_trace.COMMAND_NAMES.get(cid, cid):8s} #{slot} -> {tile} args {b6},{b7} rng {rng}")
    print(f"{a.name}: commands {'agree' if r['agree'] else 'DIFFER'}; "
          f"{r['draws']} draws predicted, {r['logged_draws']} logged, "
          f"first disagreeing draw {r['first_bad_draw']}")
    return 0 if r["agree"] and r["first_bad_draw"] is None else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="the port against every turn's after-dump")
    c.add_argument("runs", nargs="+", help="acceptance run directories")
    c.add_argument("--player", type=int, default=1, help="the side we drove (default 1)")
    c.add_argument("--turn", type=int, help="one turn only")
    c.add_argument("--verbose", action="store_true")
    c.add_argument("--json", help="write every turn's result here")
    t = sub.add_parser("trace", help="trace one turn's real CPU play with the rig")
    t.add_argument("run")
    t.add_argument("turn", type=int)
    t.add_argument("name", help="the fixture name under tests/fixtures/cpu")
    t.add_argument("--mss", help="the checkpoint to start from (default tNN-1.mss of the run)")
    t.add_argument("--limit", type=int, default=3000)
    t.add_argument("--watch", default="", help="comma-separated ROM addresses to log every execution of")
    a = ap.parse_args()
    if a.cmd == "trace":
        return cmd_trace(a)
    bad = 0
    results = []
    for run in a.runs:
        run = pathlib.Path(run)
        steps = driven_steps(run)
        turns = [t for t in sorted(steps) if (run / f"t{t:02d}.after.json").exists()]
        if a.turn is not None:
            turns = [t for t in turns if t == a.turn]
        print(f"{run}: {len(turns)} turn(s) with an after-dump")
        for t in turns:
            try:
                r = check_turn(run, t, a.player, steps[t], verbose=a.verbose)
            except LookupError as e:
                r = {"turn": t, "day": "?", "error": f"replay: {e}", "diffs": None,
                     "commands": [], "builds": [], "powers": 0, "warnings": []}
            results.append({"run": str(run), **{k: v for k, v in r.items() if k != "log"}})
            print(report(r, a.verbose))
            if r["error"] or r["diffs"]:
                bad += 1
    print(f"{bad} of {len(results)} turn(s) disagree")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
    return bad


if __name__ == "__main__":
    sys.exit(main())

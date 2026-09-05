"""Let the game's CPU play a turn and record what it did -- ROADMAP step 3.

    python tools/cpu_trace.py run NAME --state vs15_p2 [--writes JSON] [--setup JSON]
    python tools/cpu_trace.py show NAME
    python tools/cpu_trace.py predict NAME [--log]
    python tools/cpu_trace.py list

The step-2 harness with the driver replaced by End Turn: reload a parked
state, apply the case's writes, run any setup steps, write both sides'
control bytes to 2 (the match phase switch at 0x08035034 sends a 2 to the
AI phase -- DERIVATION 44; the driver restores the human's on the CPU's
first command), dump the board, end the human's turn,
let the AI play while an exec hook on the command dispatcher at
0x080669A0 copies every 20-byte command record it issues, wait for the
turn to come back, dump again. The record is the CPU's decision in the
game's own words: id (1 move, 2 wait, 3 capture, 4 fire, 6 load/supply
family, 7 drop, 9 join, 10 dive, 11 rise, 12 build, 13 a move variant,
16/17 end-of-turn housekeeping -- the ids are read off the switch and
confirmed by the traces), the unit slot, the move's tile, the target tile,
the RNG state the record carries.

Each trace lands in tests/fixtures/cpu/NAME.json with the before and after
dumps beside it: the ground truth engine/cpu.py will be diffed against.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import sim_diff                                                      # noqa: E402
from engine.state import load                                        # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "cpu"
OUT = ROOT / "harness" / "out" / "cpu"

# The command ids as the dispatcher's jump table orders them (0x08066A04),
# named from the routines each arm calls; see DERIVATION 44.
COMMAND_NAMES = {1: "move", 2: "wait", 3: "capture", 4: "fire", 5: "supply",
                 6: "cmd6", 7: "drop", 8: "cmd8", 9: "join", 10: "dive",
                 11: "rise", 12: "build", 13: "move13", 14: "cmd14",
                 15: "cmd15", 16: "cmd16", 17: "end"}


def run_case(name: str, state: str, writes: list, setup: list, limit: int) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    FIX.mkdir(parents=True, exist_ok=True)
    board = load(sim_diff.STATES_DIR / f"{state}.json")
    for step in setup:                          # the side the CPU plays is
        if step["kind"] == "end_turn":          # the one after the setup
            board = sim_diff.sim.end_turn(board)
    human = board.active_player
    cpu = sim_diff.next_player(board)
    # both control bytes are written right before End Turn (the human's
    # goes back to 1 from the driver's command hook on the CPU's first
    # command): the next-player search accepts a side whose +0x1B is
    # nonzero and +0x14 is zero, and the phase-10 switch reads that side's
    # +0x1B -- see mesen_drive.lua cpu_turn and DERIVATION 44
    control = {"kind": "write", "writes": [{"army": cpu, "control": 2},
                                           {"army": human, "control": 2}]}
    case = {"name": name, "state": state, "writes": list(writes),
            "setup": list(setup) + [control],
            "action": {"kind": "cpu_turn", "limit": limit, "cpu": cpu}, "attempts": 1}
    compiled = sim_diff.compile_case(case, [])
    compiled["before"] = (FIX / f"{name}.before.json").as_posix()
    compiled["after"] = (FIX / f"{name}.after.json").as_posix()
    script = OUT / f"{name}.lua"
    script.write_text(sim_diff.make_run_script(OUT, [compiled]), encoding="utf-8")
    jsonl = OUT / "results.jsonl"
    if jsonl.exists():
        jsonl.unlink()
    err = OUT / "error.log"
    if err.exists():
        err.unlink()
    rc, took = sim_diff.run_mesen(script, max(300, limit // 4))
    print(f"mesen exit {rc} in {took:.0f}s")
    if err.exists():
        print("!! " + err.read_text(encoding="utf-8"))
    r = None
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
    if r is None:
        raise SystemExit("no result record")
    rec = {"_comment": [
               f"The game's own CPU (P{cpu}, control byte 2) playing one turn from",
               f"the parked state {state}, traced at the command dispatcher",
               "0x080669A0 by harness/mesen_drive.lua (tools/cpu_trace.py).",
               "`commands` is the ordered list of records it dispatched; the",
               "before/after dumps are the boards either side of its turn."],
           "name": name, "state": state, "human": human, "cpu": cpu,
           "writes": writes, "setup": setup, "driven": bool(r.get("ok")),
           "driver_note": r.get("why"), "wall_seconds": round(took),
           "before": f"{name}.before.json", "after": f"{name}.after.json",
           "commands": [dict(c, name=COMMAND_NAMES.get(c["id"], f"cmd{c['id']}"))
                        for c in (r.get("commands") or [])],
           "draws": r.get("draws") or [],
           "builds": r.get("builds") or [],
           "state_log": r.get("state_log") or []}
    (FIX / f"{name}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def show(rec: dict) -> str:
    lines = [f"{rec['name']}: P{rec['cpu']} (CPU) after P{rec['human']} on {rec['state']}, "
             f"{'driven' if rec['driven'] else 'FAILED: ' + str(rec.get('driver_note'))}, "
             f"{len(rec['commands'])} command(s)"]
    for i, c in enumerate(rec["commands"]):
        unit = f"#{c['slot']} {c.get('unit', '?')} ({c.get('ux', '?')},{c.get('uy', '?')})"
        lines.append(f"  {i + 1:2d}. {c['name']:8s} {unit:26s} tile ({c['x']},{c['y']})"
                     f" target ({c['tx']},{c['ty']}) b6={c['b6']} b7={c['b7']}"
                     f" dest ({c['dest_x']},{c['dest_y']}) sel ({c['sel_x']},{c['sel_y']})"
                     f" fuel {c['fuel']} rng {c['rng']}")
    return "\n".join(lines)


def replay(rec: dict, luck_draw=None) -> tuple:
    from engine import cpu as _cpu
    if luck_draw is None:
        luck_draw = _cpu.AI_STRIKE_DRAW
    """The human's End Turn, the traced commands through engine/cpu.py and
    sim.apply, the CPU's End Turn, against the game's after-dump: the
    contradictions."""
    from engine import cpu, sim                                  # noqa: F811
    before = load(FIX / rec["before"])
    after = load(FIX / rec["after"])
    warnings = []
    board = sim.end_turn(before, warnings=warnings)
    cmds = [cpu.from_record(c) for c in rec["commands"]]
    board = cpu.replay(board, cmds, warnings=warnings, luck_draw=luck_draw)
    board = sim.end_turn(board, warnings=warnings)
    diffs = sim_diff.diff_boards(board, after)
    return diffs, warnings


def predict(rec: dict) -> dict:
    """engine/cpu.predict from the trace's before-board against the trace:
    the predicted and traced command lists, and the first RNG draw that
    disagrees (the trace logs the state before each draw, the predictor
    the state after)."""
    from engine import cpu, cpu_ai, sim                          # noqa: F811
    before_path = FIX / rec["before"]
    before = load(before_path)
    raw = json.loads(before_path.read_text(encoding="utf-8"))
    ctx = cpu_ai.Context.from_dump(before_path, player=rec["cpu"])
    board = sim.end_turn(before)
    turn = cpu.predict(board, rec["cpu"], ctx, rng=raw["rng"])
    predicted = [(c.id, c.slot, c.tile, c.arg, c.arg2, c.rng) for c in turn.commands]
    traced = [(c["id"], c["slot"], (c["x"], c["y"]), c["b6"], c["b7"], c["rng"])
              for c in rec["commands"]]
    # the purchases: the hook at 0x080243DC logs (x, y, type) and the
    # record's +7, the mode the new unit is given (DERIVATION 47)
    predicted_builds = [(b["x"], b["y"], b["type"], b["mode"]) for b in turn.builds]
    traced_builds = [(b["x"], b["y"], b["type"], b["mode"]) for b in rec.get("builds") or []]
    logged = rec.get("draws") or []
    first_bad = None
    for i, d in enumerate(turn.draws):
        if i + 1 < len(logged) and logged[i + 1]["rng"] != d["rng"]:
            first_bad = i + 1
            break
    return {"predicted": predicted, "traced": traced,
            "agree": predicted == traced and predicted_builds == traced_builds,
            "predicted_builds": predicted_builds, "traced_builds": traced_builds,
            "draws": len(turn.draws), "logged_draws": len(logged),
            "first_bad_draw": first_bad, "log": turn.log, "turn": turn}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("name")
    r.add_argument("--state", default="vs15_p2", choices=list(sim_diff.STATES))
    r.add_argument("--writes", default="[]", help="JSON list of corpus-style writes")
    r.add_argument("--setup", default="[]", help="JSON list of corpus-style setup steps")
    r.add_argument("--limit", type=int, default=3000, help="polls of ten frames to wait for the turn back")
    s = sub.add_parser("show")
    s.add_argument("name")
    rp = sub.add_parser("replay", help="apply the traced commands in Python and diff")
    rp.add_argument("name")
    rp.add_argument("--draw", type=int, help="which RNG draw the AI's strike takes")
    pr = sub.add_parser("predict", help="engine/cpu.predict against the trace")
    pr.add_argument("name")
    pr.add_argument("--log", action="store_true", help="print the predictor's reasoning")
    sub.add_parser("list")
    a = p.parse_args()
    if a.cmd == "predict":
        rec = json.loads((FIX / f"{a.name}.json").read_text(encoding="utf-8"))
        r = predict(rec)
        if a.log:
            print("\n".join(r["log"]))
        for lab, rows in (("predicted", r["predicted"]), ("traced", r["traced"])):
            print(f"== {lab}")
            for (cid, slot, tile, b6, b7, rng) in rows:
                print(f"  {COMMAND_NAMES.get(cid, cid):8s} #{slot} -> {tile} args {b6},{b7} rng {rng}")
        print(f"{a.name}: commands {'agree' if r['agree'] else 'DIFFER'}; "
              f"{r['draws']} draws predicted, {r['logged_draws']} logged, "
              f"first disagreeing draw {r['first_bad_draw']}")
        return 0 if r["agree"] and r["first_bad_draw"] is None else 1
    if a.cmd == "replay":
        rec = json.loads((FIX / f"{a.name}.json").read_text(encoding="utf-8"))
        diffs, warnings = replay(rec, a.draw)
        for w in warnings:
            print(f"  !! {w}")
        for d in diffs:
            print(f"  {d}")
        print(f"{a.name}: {len(diffs)} field(s) differ after replay")
        return 1 if diffs else 0
    if a.cmd == "run":
        rec = run_case(a.name, a.state, json.loads(a.writes), json.loads(a.setup), a.limit)
        print(show(rec))
        return 0 if rec["driven"] else 1
    if a.cmd == "show":
        print(show(json.loads((FIX / f"{a.name}.json").read_text(encoding="utf-8"))))
        return 0
    for f in sorted(FIX.glob("*.json")):
        if ".before." in f.name or ".after." in f.name:
            continue
        rec = json.loads(f.read_text(encoding="utf-8"))
        print(show(rec).splitlines()[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())

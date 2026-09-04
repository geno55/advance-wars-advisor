"""The differential test for engine/sim.py -- ROADMAP step 2.

    python tools/sim_diff.py dump                 # the parked states -> JSON
    python tools/sim_diff.py run [--cases PAT]    # dump, apply(), drive, dump, diff
    python tools/sim_diff.py check                # replay the recorded dumps offline
    python tools/sim_diff.py report               # the verdicts

One parked savestate, ONE action: the game is dumped (harness/mesen_state.lua),
the same action is applied in Python (engine/sim.py), the action is driven on
the game with read-back verification (harness/mesen_drive.lua), the game is
dumped again, and the two after-boards are compared field by field. Every
field the game contradicts is logged; the corpus and its result log are
checked in under tests/fixtures/sim_diff/ and tests/test_sim_diff.py replays
the comparison from the recorded dumps without an emulator.

The corpus (tests/fixtures/sim_diff/corpus.json) is a list of drives, each
naming a parked state, transparent RAM writes that shape the board (type,
hp, ammo, fuel, capture, terrain, army fields, settings -- never a position,
see mesen_drive.lua), optional SETUP steps driven and verified but not
measured (an End Turn to reach the other side, a move that parks a unit
where the measured action needs it), and the ACTION under test. Setup boards
are advanced in Python only to compute the next step's taps; the measured
action is always applied to the board the game was dumped from.

What the driver is told is computed here from the engine's own facts: the
route is pathing.path() turned into direction taps, the menu item is its
index in the offered list predicted from actions_for() (the twelve-entry
table order, DERIVATION 40), the shop item is its index in production.shop().
The driver's read-back is what catches a wrong prediction; the diff is what
catches a wrong model.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import fnmatch
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import actions, co as co_mod, pathing, production, sim    # noqa: E402
from engine.state import load                                         # noqa: E402

MESEN = os.environ.get(
    "AW_MESEN",
    r"C:\Users\geno5\Documents\Codex\Translation\Mesen2-Expanded-master"
    r"\bin\win-x64\Release\Mesen.exe")
ROM = os.environ.get("AW_ROM", str(ROOT.parent / "Advance Wars (USA) (Rev 1).gba"))
MSS_DIR = pathlib.Path(os.environ.get("AW_MSS_DIR",
                                      r"C:\Users\geno5\Documents\Mesen2\SaveStates"))

# The parked savestates (GUI slots the user parked; DERIVATION 25, 27).
STATES = {
    "vs15_p2": dict(
        mss="Advance Wars (USA) (Rev 1)_2.mss", dims=(15, 10),
        note="15x10 VS, Andy vs Andy, CO Power rule ON, funds 9500/property, "
             "P2 to move on day 2; the height byte reads 13 and is pinned to 10",
        mgba="fog_vision_15x10.json"),
    "a15_p1": dict(
        mss="Advance Wars (USA) (Rev 1)_1.mss", dims=(15, 10),
        note="the A15 capture fixture: P1 to move, Infantry #2 one tile south "
             "of the neutral city at (4,1), CO Power rule OFF",
        mgba="fog_vision_15x10.json"),
}

FIX = ROOT / "tests" / "fixtures" / "sim_diff"
STATES_DIR = FIX / "states"
RUNS = FIX / "runs"
CORPUS = FIX / "corpus.json"
RESULTS = FIX / "results.json"
OUT = ROOT / "harness" / "out" / "sim_diff"
HARNESS = ROOT / "harness"

# RAM type bytes are 1-BASED (mesen_state.lua UNIT_NAMES).
RAM_TYPE = {"Infantry": 1, "Mech": 2, "MdTank": 3, "Tank": 5, "Recon": 6,
            "APC": 7, "Artillery": 10, "Rockets": 11, "AntiAir": 14,
            "Missiles": 15, "Fighter": 16, "Bomber": 17, "BCopter": 19,
            "TCopter": 20, "Battleship": 21, "Cruiser": 22, "Lander": 23,
            "Sub": 24}

# The unit action menu in table order (0x0828BA80, DERIVATION 40); the two
# Fire and two Capt entries never both show.
KIND_LABEL = {"attack": "Fire", "capture": "Capt", "load": "Load",
              "drop": "Drop", "join": "Join", "supply": "Supply",
              "wait": "Wait", "dive": "Dive", "rise": "Rise"}

UNIT_FIELDS = ("type", "player", "x", "y", "hp", "ammo", "fuel", "capture",
               "acted", "loaded", "carrying", "dived", "cargo", "cargo2")
ARMY_FIELDS = ("funds", "income", "power", "power_active", "power_uses",
               "power_ready")
BOARD_FIELDS = ("width", "height", "day", "active_player", "weather_index", "fog")


# --------------------------------------------------------------------------
# Lua generation
# --------------------------------------------------------------------------

def lua(v) -> str:
    """A Python value as a Lua literal (None inside a dict is dropped)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        s = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{s}"'
    if isinstance(v, (list, tuple)):
        return "{" + ", ".join(lua(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ", ".join(f"[{lua(str(k))}] = {lua(x)}"
                               for k, x in v.items() if x is not None) + "}"
    raise TypeError(f"cannot render {type(v).__name__} as Lua")


SCHEDULER = """
local co = coroutine.create(main)
local pending = false
emu.addEventCallback(function() pending = true end, emu.eventType.endFrame)
emu.addMemoryCallback(function()
  if not pending then return end
  pending = false
  if coroutine.status(co) ~= "dead" then
    local ok, err = coroutine.resume(co)
    if not ok then
      local ef = io.open(AW.OUT .. "error.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)
"""


def _libs() -> str:
    return ((HARNESS / "mesen_state.lua").read_text(encoding="utf-8") + "\n"
            + (HARNESS / "mesen_drive.lua").read_text(encoding="utf-8") + "\n")


def _states_table(names) -> dict:
    out = {}
    for n in names:
        st = STATES[n]
        w, h = st["dims"] or (None, None)
        out[n] = {"path": (MSS_DIR / st["mss"]).as_posix(), "w": w, "h": h}
    return out


def make_dump_script(out_dir: pathlib.Path, targets: dict) -> str:
    """targets: state name -> JSON path."""
    body = f"""
local M = AW
M.OUT = {lua(out_dir.as_posix() + "/")}
M.log = io.open(M.OUT .. "dump.log", "w")
local STATES = {lua(_states_table(targets))}
local TARGETS = {lua({k: v.as_posix() for k, v in targets.items()})}
local function main()
  M.wait(5)
  for name, path in pairs(TARGETS) do
    local st = STATES[name]
    local fh = assert(io.open(st.path, "rb")); local bytes = fh:read("*a"); fh:close()
    emu.loadSavestate(bytes); M.wait(30)
    M.set_dims(st.w, st.h)
    M.dump(path, {{ note = "parked state " .. name .. ", dumped by tools/sim_diff.py dump" }})
    M.L("dumped " .. name .. " -> " .. path)
  end
  M.log:close(); emu.stop(0)
end
"""
    return _libs() + body + SCHEDULER


def make_run_script(out_dir: pathlib.Path, cases: list) -> str:
    names = sorted({c["state"] for c in cases})
    body = f"""
local M = AW
M.OUT = {lua(out_dir.as_posix() + "/")}
M.log = io.open(M.OUT .. "run.log", "w")
local STATES = {lua(_states_table(names))}
local CASES = {lua(cases)}
local function main()
  M.wait(5)
  for name, st in pairs(STATES) do
    local fh = assert(io.open(st.path, "rb")); st.bytes = fh:read("*a"); fh:close()
  end
  local results = io.open(M.OUT .. "results.jsonl", "w")
  for _, c in ipairs(CASES) do
    local r = M.run_case(c, STATES)
    results:write(M.json(r) .. "\\n"); results:flush()
  end
  results:close(); M.log:close(); emu.stop(0)
end
"""
    return _libs() + body + SCHEDULER


def run_mesen(script: pathlib.Path, timeout_s: int) -> tuple:
    cmd = [MESEN, "--testrunner", f"--timeout={timeout_s}",
           "--debug.scriptWindow.allowIoOsAccess=true", ROM, str(script)]
    t0 = time.time()
    p = subprocess.run(cmd, timeout=timeout_s + 120)
    return p.returncode, time.time() - t0


# --------------------------------------------------------------------------
# writes: the corpus form -> the driver's form, and the same edit in Python
# --------------------------------------------------------------------------

def lua_writes(writes) -> list:
    out = []
    for w in writes or []:
        if "unit" in w:
            d = {"kind": "unit", "slot": w["unit"]}
            if w.get("remove"):
                d["type"] = 0
            elif "type" in w:
                d["type"] = RAM_TYPE[w["type"]]
            for f in ("hp", "ammo", "capture", "fuel", "state"):
                if f in w:
                    d[f] = w[f]
            out.append(d)
        elif "terrain" in w:
            x, y = w["terrain"]
            out.append({"kind": "terrain", "x": x, "y": y, "id": w["id"],
                        "owner": w.get("owner", 0)})
        elif "army" in w:
            out.append({"kind": "army", "player": w["army"],
                        "funds": w.get("funds"), "co_id": w.get("co"),
                        "meter": w.get("meter"), "uses": w.get("uses"),
                        "ready": w.get("ready"), "active": w.get("active"),
                        "control": w.get("control")})
        elif "fog" in w:
            out.append({"kind": "fog", "value": int(w["fog"])})
        elif "weather" in w:
            out.append({"kind": "weather", "value": int(w["weather"])})
        elif "rng" in w:
            out.append({"kind": "rng", "value": int(w["rng"])})
        elif "repair_free" in w:
            out.append({"kind": "repair_free", "value": int(w["repair_free"])})
        elif "rate" in w:
            out.append({"kind": "rate", "value": int(w["rate"])})
        elif "proplist" in w:
            x, y = w["proplist"]
            out.append({"kind": "proplist", "x": x, "y": y, "id": w["id"]})
        elif "raw" in w:
            out.append({"kind": "raw", "addr": int(w["raw"], 0), "size": w.get("size", 1),
                        "value": int(w["value"])})
        else:
            raise ValueError(f"unknown write {w}")
    return out


def resolve_writes(board, writes) -> list:
    """The corpus writes with `"meter": "threshold"` turned into the number
    the army's CO (after any co write in the same list) needs, so a power
    case says what it means rather than a magic constant."""
    out = []
    for w in writes or []:
        if "army" in w and w.get("meter") == "threshold":
            army = board.army(w["army"])
            cid = w.get("co", army.co_id)
            uses = w.get("uses", army.power_uses or 0)
            w = dict(w, meter=co_mod.power_threshold(cid, uses))
        out.append(w)
    return out


def py_writes(board, writes):
    """The board with the corpus writes applied, as the game will read it."""
    units = {u.slot: u for u in board.units}
    terrain = [list(r) for r in board.terrain]
    owner = [list(r) for r in board.owner]
    armies = {a.player: a for a in board.armies}
    kw = {}
    for w in writes or []:
        if "unit" in w:
            u = units[w["unit"]]
            if w.get("remove"):
                del units[w["unit"]]
                continue
            ch = {f: w[f] for f in ("type", "hp", "ammo", "capture", "fuel", "state")
                  if f in w}
            units[w["unit"]] = dataclasses.replace(u, **ch)
        elif "terrain" in w:
            x, y = w["terrain"]
            terrain[y][x] = w["id"]
            owner[y][x] = w.get("owner", 0)
        elif "army" in w:
            a = armies[w["army"]]
            ch = {}
            if "funds" in w:
                ch["funds"] = w["funds"]
            if "co" in w:
                ch["co_id"] = w["co"]
            if "meter" in w:
                ch["power"] = w["meter"]
            if "uses" in w:
                ch["power_uses"] = w["uses"]
            if "ready" in w:
                ch["power_ready"] = bool(w["ready"])
            if "active" in w:
                ch["power_active"] = bool(w["active"])
            armies[w["army"]] = dataclasses.replace(a, **ch)
        elif "fog" in w:
            kw["fog"] = bool(w["fog"])
        elif "weather" in w:
            kw["weather_index"] = int(w["weather"])
        elif "repair_free" in w:
            kw["repair_free"] = bool(w["repair_free"])
        elif "rate" in w:
            kw["funds_per_property"] = int(w["rate"])
        elif "rng" in w:
            pass
        elif "proplist" in w:
            pass                      # the terrain write beside it is the board's view
        elif "raw" in w:
            if int(w["raw"], 0) == 0x03004420:
                kw["day"] = int(w["value"])
            # any other raw cell is outside the board's model
        else:
            raise ValueError(f"unknown write {w}")
    return dataclasses.replace(board, units=list(units.values()),
                               armies=list(armies.values()), terrain=terrain,
                               owner=owner, vision=None, **kw)


# --------------------------------------------------------------------------
# from an action spec to a driver step
# --------------------------------------------------------------------------

def find_action(board, spec, warnings=None):
    """The one engine Action the spec names on this board (None for end_turn)."""
    warnings = warnings if warnings is not None else []
    k = spec["kind"]
    if k == "end_turn":
        return None, []
    player = board.active_player
    if k == "build":
        every = actions.build_actions(board, player, warnings=warnings)
        acts = [a for a in every if tuple(a.tile) == tuple(spec["factory"])
                and a.build_type == spec["type"]]
    elif k == "power":
        a = actions.power_action(board, player, warnings=warnings)
        every = [a] if a else []
        acts = list(every)
    else:
        unit = sim.unit_in(board, spec["slot"])
        if unit is None:
            raise ValueError(f"unit #{spec['slot']} is not on the board")
        every = actions.actions_for(board, unit, warnings=warnings)
        acts = [a for a in every if a.kind == k]
        if "tile" in spec:
            acts = [a for a in acts if tuple(a.tile) == tuple(spec["tile"])]
        if "target" in spec:
            acts = [a for a in acts
                    if a.target is not None and a.target.slot == spec["target"]]
        if "drop_tile" in spec:
            acts = [a for a in acts if a.drop_tile == tuple(spec["drop_tile"])]
    if len(acts) != 1:
        seen = [(a.kind, a.tile, a.target.slot if a.target else None, a.drop_tile,
                 a.build_type) for a in acts][:8]
        raise ValueError(f"{spec} matches {len(acts)} action(s): {seen}")
    return acts[0], every


def predicted_menu(unit, act, every) -> list:
    """The unit action menu as the game will offer it at the action's tile."""
    at = [a for a in every if a.tile == act.tile]
    items = []
    if any(a.kind == "attack" for a in at):
        items.append("Fire")
    if any(a.kind == "capture" for a in at):
        items.append("Capt")
    if act.kind == "load":
        items.append("Load")
    if unit.cargo and any(a.kind == "drop" and a.target.slot == unit.cargo for a in at):
        items.append("Drop")
    if unit.cargo2 and any(a.kind == "drop" and a.target.slot == unit.cargo2 for a in at):
        items.append("Drop2")
    if act.kind == "join":
        items.append("Join")
    if any(a.kind == "supply" for a in at):
        items.append("Supply")
    if act.kind not in ("load", "join"):
        items.append("Wait")
    if any(a.kind == "dive" for a in at):
        items.append("Dive")
    if any(a.kind == "rise" for a in at):
        items.append("Rise")
    return items


def _dir(a, b) -> str:
    dx, dy = b[0] - a[0], b[1] - a[1]
    return {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}[(dx, dy)]


def taps_along(route) -> list:
    return [_dir(route[i], route[i + 1]) for i in range(len(route) - 1)]


def drop_candidates(tile, land) -> list:
    """Tap lists for the drop selector: the landing direction first, the
    selector's recorded north default as the empty list."""
    d = _dir(tile, land)
    first = {"up": [], "right": ["right"], "down": ["down"], "left": ["left"]}[d]
    rest = [[], ["right"], ["down"], ["left"], ["up"], ["right", "right"],
            ["down", "down"], ["left", "left"]]
    return [first] + [r for r in rest if r != first]


def empty_tile(board) -> tuple:
    """A unit-free plain or road near the middle, for opening the map menu."""
    occupied = {(u.x, u.y) for u in board.units}
    cx, cy = board.width // 2, board.height // 2
    best = None
    for y in range(board.height):
        for x in range(board.width):
            if (x, y) in occupied or board.terrain[y][x] not in (1, 5):
                continue
            d = abs(x - cx) + abs(y - cy)
            if best is None or d < best[0]:
                best = (d, x, y)
    if best is None:
        raise ValueError("no empty plain or road to open the map menu on")
    return best[1], best[2]


def next_player(board) -> int:
    order = sim.players_in_order(board)
    later = [p for p in order if p > board.active_player]
    return later[0] if later else order[0]


def xy(t) -> dict:
    return {"x": int(t[0]), "y": int(t[1])}


def compile_step(board, spec, tag, warnings):
    """(driver step, the board sim says the step leaves) for one spec."""
    k = spec["kind"]
    if k == "write":
        writes = resolve_writes(board, spec["writes"])
        return ({"kind": "write", "tag": tag, "writes": lua_writes(writes)},
                py_writes(board, writes), None)
    if k == "end_turn":
        step = {"kind": "end_turn", "tag": tag, "empty": xy(empty_tile(board)),
                "checks": [{"what": "active", "player": next_player(board)}]}
        return step, sim.end_turn(board, warnings=warnings), None
    if k == "cpu_turn":
        # the game plays the next side (its control byte written to 2) and
        # hands the turn back; the model has no opinion on the board it
        # leaves, so the step returns the board unchanged (tools/cpu_trace.py)
        step = {"kind": "cpu_turn", "tag": tag, "empty": xy(empty_tile(board)),
                "limit": spec.get("limit", 3000), "cpu": spec.get("cpu"),
                "checks": []}
        return step, board, None
    act, every = find_action(board, spec, warnings)
    if k == "build":
        tid = board.terrain[act.tile[1]][act.tile[0]]
        shop = production.shop(tid)
        step = {"kind": "build", "tag": tag, "factory": xy(act.tile),
                "shop": shop, "shop_index": shop.index(act.build_type),
                "checks": [{"what": "unit", "slot": act.target.slot,
                            "x": act.tile[0], "y": act.tile[1], "acted": True}]}
    elif k == "power":
        step = {"kind": "power", "tag": tag, "empty": xy(empty_tile(board)),
                "checks": [{"what": "army", "player": act.power.player,
                            "active": True}]}
    else:
        unit = act.unit
        dest = act.drop_tile if k == "trap" else act.tile
        route = pathing.path(board, unit, dest)
        if not route:
            raise ValueError(f"{tag}: no route from ({unit.x},{unit.y}) to {dest}")
        taps = taps_along(route)
        step = {"kind": k, "tag": tag, "slot": unit.slot,
                "from": xy((unit.x, unit.y)), "taps": taps, "dest": xy(dest)}
        if k == "trap":
            taps.append(_dir(dest, act.tile))
            step["checks"] = [{"what": "unit", "slot": unit.slot, "x": dest[0],
                               "y": dest[1], "acted": True}]
        else:
            menu = predicted_menu(unit, act, every)
            label = KIND_LABEL[k]
            if k == "drop" and act.target.slot == unit.cargo2 and unit.cargo2 != unit.cargo:
                label = "Drop2"
            step["menu"] = menu
            step["menu_index"] = menu.index(label)
            checks = [{"what": "unit", "slot": unit.slot, "x": dest[0],
                       "y": dest[1], "acted": True}]
            if k == "attack":
                t = act.target
                step["target"] = {"slot": t.slot, "x": t.x, "y": t.y}
                checks = [{"what": "hit", "slot": t.slot}]
            elif k == "capture":
                checks.append({"what": "captured", "slot": unit.slot,
                               "x": dest[0], "y": dest[1], "player": unit.player})
            elif k == "supply":
                for fill in act.supplies:
                    checks.append({"what": "changed", "slot": fill.target.slot,
                                   "fields": ["fuel", "ammo"]})
            elif k == "load":
                checks = [{"what": "unit", "slot": unit.slot, "loaded": True}]
            elif k == "drop":
                step["drop_taps"] = drop_candidates(act.tile, act.drop_tile)
                checks.append({"what": "unit", "slot": act.target.slot,
                               "x": act.drop_tile[0], "y": act.drop_tile[1],
                               "loaded": False})
            elif k == "join":
                checks.append({"what": "unit", "slot": act.target.slot, "gone": True})
            elif k in ("dive", "rise"):
                checks[0]["dived"] = (k == "dive")
            step["checks"] = checks
    after = sim.apply(board, act, warnings=warnings)
    return step, after, act


def compile_case(case, warnings):
    name = case["name"]
    board = load(STATES_DIR / f"{case['state']}.json")
    writes = resolve_writes(board, case.get("writes"))
    board = py_writes(board, writes)
    steps = []
    for i, spec in enumerate(case.get("setup", [])):
        step, board, _ = compile_step(board, spec, f"{name}-s{i + 1}", warnings)
        steps.append(step)
    action, _, _ = compile_step(board, case["action"], name, warnings)
    return {"name": name, "state": case["state"],
            "writes": lua_writes(writes), "setup": steps,
            "action": action, "attempts": case.get("attempts", 3),
            "before": (RUNS / f"{name}.before.json").as_posix(),
            "after": (RUNS / f"{name}.after.json").as_posix()}


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------

def diff_boards(pred, game) -> list:
    """Every field on which the model's after-board and the game's differ."""
    out = []
    pu = {u.slot: u for u in pred.units}
    gu = {u.slot: u for u in game.units}
    for s in sorted(set(pu) | set(gu)):
        a, b = pu.get(s), gu.get(s)
        if a is None:
            out.append(f"unit #{s} ({b.type}): predicted absent, the game has it at ({b.x},{b.y})")
            continue
        if b is None:
            out.append(f"unit #{s} ({a.type}): predicted at ({a.x},{a.y}), the game removed it")
            continue
        for f in UNIT_FIELDS:
            va, vb = getattr(a, f), getattr(b, f)
            if va != vb:
                out.append(f"unit #{s} ({a.type}) {f}: predicted {va}, game {vb}")
    pa = {a.player: a for a in pred.armies}
    ga = {a.player: a for a in game.armies}
    for p in sorted(set(pa) | set(ga)):
        a, b = pa.get(p), ga.get(p)
        if a is None or b is None:
            out.append(f"P{p} army record: predicted {'absent' if a is None else 'present'}, "
                       f"game {'absent' if b is None else 'present'}")
            continue
        for f in ARMY_FIELDS:
            va, vb = getattr(a, f), getattr(b, f)
            if va != vb:
                out.append(f"P{p} {f}: predicted {va}, game {vb}")
    for f in BOARD_FIELDS:
        va, vb = getattr(pred, f), getattr(game, f)
        if va != vb:
            out.append(f"{f}: predicted {va}, game {vb}")
    if pred.width == game.width and pred.height == game.height:
        for y in range(pred.height):
            for x in range(pred.width):
                if pred.terrain[y][x] != game.terrain[y][x]:
                    out.append(f"terrain ({x},{y}): predicted {pred.terrain[y][x]}, "
                               f"game {game.terrain[y][x]}")
                if pred.owner[y][x] != game.owner[y][x]:
                    out.append(f"owner ({x},{y}): predicted P{pred.owner[y][x]}, "
                               f"game P{game.owner[y][x]}")
    return out


def analyse(case, before_path, after_path, rng_at_confirm=None) -> dict:
    """apply() on the before-dump against the after-dump: the verdict."""
    warnings = []
    before = load(before_path)
    game = load(after_path)
    spec = case["action"]
    if spec["kind"] == "end_turn":
        pred = sim.end_turn(before, warnings=warnings)
    else:
        act, _ = find_action(before, spec, warnings)
        kw = {}
        if act.kind in ("attack", "power") and rng_at_confirm is not None:
            kw["rng_state"] = rng_at_confirm
        pred = sim.apply(before, act, warnings=warnings, **kw)
    diffs = diff_boards(pred, game)
    return {"verdict": "agree" if not diffs else "differ", "diffs": diffs,
            "model_warnings": warnings}


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def load_corpus() -> list:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["drives"]


def load_results() -> dict:
    if RESULTS.exists():
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    return {"drives": []}


def mgba_check(state_name: str) -> list:
    """The Mesen dump against the mGBA dump of the same map, tile for tile."""
    st = STATES[state_name]
    ours = json.loads((STATES_DIR / f"{state_name}.json").read_text(encoding="utf-8"))
    theirs = json.loads((ROOT / "tests" / "fixtures" / st["mgba"]).read_text(encoding="utf-8"))
    problems = []
    if (ours["width"], ours["height"]) != (theirs["width"], theirs["height"]):
        problems.append(f"dims {ours['width']}x{ours['height']} vs mGBA "
                        f"{theirs['width']}x{theirs['height']}")
        return problems
    o_rows = {r["y"]: r["t"] for r in ours["terrain"]}
    t_rows = {r["y"]: r["t"] for r in theirs["terrain"]}
    for y in range(ours["height"]):
        for x in range(ours["width"]):
            if o_rows[y][x] != t_rows[y][x]:
                problems.append(f"terrain ({x},{y}): {o_rows[y][x]} vs mGBA {t_rows[y][x]}")
    op = sorted((p["t"], p["x"], p["y"]) for p in ours["properties"])
    tp = sorted((p["t"], p["x"], p["y"]) for p in theirs.get("properties", []))
    if tp and op != tp:
        problems.append(f"property list {op} vs mGBA {tp}")
    return problems


def cmd_dump(a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    STATES_DIR.mkdir(parents=True, exist_ok=True)
    names = a.states or list(STATES)
    targets = {n: STATES_DIR / f"{n}.json" for n in names}
    script = OUT / "dump.lua"
    script.write_text(make_dump_script(OUT, targets), encoding="utf-8")
    rc, took = run_mesen(script, 120)
    print(f"mesen exit {rc} in {took:.0f}s")
    err = OUT / "error.log"
    if err.exists():
        print("!! " + err.read_text(encoding="utf-8"))
        err.unlink()
        return 1
    for n, p in targets.items():
        if not p.exists():
            print(f"!! {n}: no dump written")
            return 1
        b = load(p)
        print(f"{n}: {b.width}x{b.height} day {b.day} P{b.active_player} to move, "
              f"{len(b.units)} units, fog {b.fog}, rate {b.funds_per_property}, "
              f"repair_free {b.repair_free}")
        for w in b.warnings:
            print(f"  !! {w}")
        probs = mgba_check(n)
        print(f"  against mGBA {STATES[n]['mgba']}: "
              + ("tile for tile" if not probs else f"{len(probs)} difference(s)"))
        for pr in probs[:10]:
            print(f"    {pr}")
    return 0


def cmd_compile(a) -> int:
    """Compile the corpus without running it -- the dry run."""
    ok = True
    for case in load_corpus():
        if a.cases and not fnmatch.fnmatch(case["name"], a.cases):
            continue
        warnings = []
        try:
            c = compile_case(case, warnings)
        except Exception as e:                                       # noqa: BLE001
            print(f"!! {case['name']}: {e}")
            ok = False
            continue
        act = c["action"]
        desc = act["kind"]
        if "taps" in act:
            desc += f" #{act['slot']} ({act['from']['x']},{act['from']['y']}) " \
                    f"{''.join(t[0] for t in act['taps']) or '.'} menu {act.get('menu')}[{act.get('menu_index')}]"
        if "shop" in act:
            desc += f" {act['shop'][act['shop_index']]} at ({act['factory']['x']},{act['factory']['y']})"
        print(f"{case['name']:32s} {len(c['setup'])} setup, {desc}")
    return 0 if ok else 1


def cmd_run(a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus()
    picked = [c for c in corpus if not a.cases or fnmatch.fnmatch(c["name"], a.cases)]
    if not picked:
        print("no cases match")
        return 1
    compiled, by_name = [], {}
    for case in picked:
        warnings = []
        compiled.append(compile_case(case, warnings))
        by_name[case["name"]] = case
    script = OUT / "run.lua"
    script.write_text(make_run_script(OUT, compiled), encoding="utf-8")
    jsonl = OUT / "results.jsonl"
    if jsonl.exists():
        jsonl.unlink()
    err = OUT / "error.log"
    if err.exists():
        err.unlink()
    budget = a.timeout or max(300, 90 * len(compiled))
    print(f"driving {len(compiled)} case(s), budget {budget}s ...")
    rc, took = run_mesen(script, budget)
    print(f"mesen exit {rc} in {took:.0f}s")
    if err.exists():
        print("!! " + err.read_text(encoding="utf-8"))
    driven = {}
    if jsonl.exists():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                driven[r["name"]] = r
    results = load_results()
    rows = {d["name"]: d for d in results["drives"]}
    for c in compiled:
        case = by_name[c["name"]]
        r = driven.get(c["name"])
        row = {"name": c["name"], "state": c["state"], "kind": case["action"]["kind"],
               "why": case.get("why", ""), "driven": bool(r and r.get("ok")),
               "attempts": r.get("attempts") if r else 0,
               "driver_note": (r.get("why") if r else "the run never reached this case")}
        if r and r.get("target_note"):
            row["target_note"] = r["target_note"]
        if r and r.get("drop_note"):
            row["drop_note"] = r["drop_note"]
        if r and r.get("rng_at_confirm") is not None:
            row["rng_at_confirm"] = r["rng_at_confirm"]
        if row["driven"]:
            row["before"] = f"runs/{c['name']}.before.json"
            row["after"] = f"runs/{c['name']}.after.json"
            row.update(analyse(case, RUNS / f"{c['name']}.before.json",
                               RUNS / f"{c['name']}.after.json",
                               row.get("rng_at_confirm")))
        else:
            row["verdict"] = "undriven"
            row["diffs"] = []
            for p in (RUNS / f"{c['name']}.before.json", RUNS / f"{c['name']}.after.json"):
                if p.exists():
                    p.unlink()
        rows[c["name"]] = row
    write_results(results, rows, corpus, took, len(compiled))
    print(report_text(results))
    return 0


def write_results(results, rows, corpus, took, n_cases):
    order = {c["name"]: i for i, c in enumerate(corpus)}
    results["drives"] = sorted((r for r in rows.values() if r["name"] in order),
                               key=lambda d: order[d["name"]])
    results["_comment"] = [
        "The differential test's result log (ROADMAP step 2): one parked",
        "state and ONE action per drive, dumped before and after by",
        "harness/mesen_state.lua, driven by harness/mesen_drive.lua, compared",
        "against engine/sim.py by tools/sim_diff.py. `diffs` lists every field",
        "the game contradicted; tests/test_sim_diff.py replays the comparison",
        "from the recorded dumps under runs/.",
    ]
    if took is not None:
        results["run"] = {"date": _dt.date.today().isoformat(),
                          "mesen": pathlib.Path(MESEN).name,
                          "wall_seconds": round(took), "cases_this_run": n_cases}
    results["summary"] = summarise(results["drives"])
    RESULTS.write_text(json.dumps(results, indent=1), encoding="utf-8")


def cmd_rescore(a) -> int:
    """Re-run the comparison for every driven case from its recorded dumps
    with today's model and rewrite the result log -- what to run after a
    sim.py change, no emulator needed."""
    corpus = load_corpus()
    by_name = {c["name"]: c for c in corpus}
    results = load_results()
    rows = {}
    for d in results["drives"]:
        case = by_name.get(d["name"])
        if case is None:
            continue
        d = dict(d)
        d["kind"] = case["action"]["kind"]
        d["why"] = case.get("why", "")
        if d["driven"]:
            d.update(analyse(case, FIX / d["before"], FIX / d["after"],
                             d.get("rng_at_confirm")))
        rows[d["name"]] = d
    write_results(results, rows, corpus, None, 0)
    print(report_text(results))
    return 0


def summarise(drives) -> dict:
    return {"drives": len(drives),
            "driven": sum(1 for d in drives if d["driven"]),
            "agree": sum(1 for d in drives if d.get("verdict") == "agree"),
            "differ": sum(1 for d in drives if d.get("verdict") == "differ"),
            "undriven": sum(1 for d in drives if not d["driven"])}


def report_text(results) -> str:
    lines = []
    for d in results["drives"]:
        mark = {"agree": "ok  ", "differ": "DIFF", "undriven": "----"}[d.get("verdict", "undriven")]
        lines.append(f"{mark} {d['name']:32s} {d['kind']:9s} "
                     + (d.get("driver_note") or "") if not d["driven"] else
                     f"{mark} {d['name']:32s} {d['kind']:9s} attempt {d['attempts']}")
        for x in d.get("diffs", []):
            lines.append(f"       {x}")
    s = results.get("summary") or summarise(results["drives"])
    lines.append(f"{s['driven']}/{s['drives']} driven, {s['agree']} agree, "
                 f"{s['differ']} differ, {s['undriven']} undriven")
    return "\n".join(lines)


def check_recorded(verbose=False) -> list:
    """Replay every driven case from its recorded dumps; the mismatches
    between the recorded verdict and today's model."""
    results = load_results()
    by_name = {c["name"]: c for c in load_corpus()}
    problems = []
    for d in results["drives"]:
        if not d["driven"]:
            continue
        case = by_name.get(d["name"])
        if case is None:
            problems.append(f"{d['name']}: in results but not in the corpus")
            continue
        now = analyse(case, FIX / d["before"], FIX / d["after"], d.get("rng_at_confirm"))
        if now["verdict"] != d["verdict"] or now["diffs"] != d["diffs"]:
            problems.append(f"{d['name']}: recorded {d['verdict']} {d['diffs']}, "
                            f"now {now['verdict']} {now['diffs']}")
        elif verbose:
            print(f"ok   {d['name']}: {now['verdict']}")
    return problems


def cmd_check(a) -> int:
    problems = check_recorded(verbose=True)
    for p in problems:
        print("!! " + p)
    print(f"{len(problems)} mismatch(es) between the recorded log and the model")
    return 1 if problems else 0


def cmd_report(a) -> int:
    print(report_text(load_results()))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="dump the parked states to tests/fixtures/sim_diff/states")
    d.add_argument("states", nargs="*", help=f"which of {', '.join(STATES)}")
    d.set_defaults(fn=cmd_dump)
    c = sub.add_parser("compile", help="compile the corpus into driver steps, no emulator")
    c.add_argument("--cases", help="fnmatch pattern on case names")
    c.set_defaults(fn=cmd_compile)
    r = sub.add_parser("run", help="drive the corpus on the game and diff")
    r.add_argument("--cases", help="fnmatch pattern on case names")
    r.add_argument("--timeout", type=int, help="Mesen's --timeout in seconds")
    r.set_defaults(fn=cmd_run)
    rs = sub.add_parser("rescore", help="re-diff every driven case from its dumps and rewrite the log")
    rs.set_defaults(fn=cmd_rescore)
    k = sub.add_parser("check", help="replay the recorded dumps against the model")
    k.set_defaults(fn=cmd_check)
    t = sub.add_parser("report", help="print the recorded verdicts")
    t.set_defaults(fn=cmd_report)
    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

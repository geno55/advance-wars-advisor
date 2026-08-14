"""What the enemy can do to you next turn, read off a live board.

    python tools/threat_report.py state.json
    python tools/threat_report.py state.json --unit 12
    python tools/threat_report.py state.json --map
    python tools/threat_report.py state.json --map --for Infantry

The default pass is cheap: one focus-fire evaluation per unit, on the tile it
already occupies. `--unit` runs the expensive question -- every tile that unit
could move to, re-running the enemy searches against each hypothetical
placement -- because that is the one a player actually acts on.

Everything printed here is composed from tables that were checked against the
running game. The judgement calls are not in the numbers, they are in what you
do about them, and this tool deliberately stops before that line.
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import threat                                      # noqa: E402
from engine.damage import screen_bars                          # noqa: E402
from engine.state import load, summarise                       # noqa: E402


def bars(hp):
    return f"{screen_bars(hp)} bars"


def describe(ff, hp):
    """One line for a focus-fire result, in the terms a player thinks in."""
    if not ff.delivered:
        return "untouched"
    span = (f"{ff.best_damage}-{ff.worst_damage}"
            if ff.best_damage != ff.worst_damage else f"{ff.worst_damage}")
    who = f"{ff.attackers} attacker{'s' if ff.attackers != 1 else ''}"
    out = f"{span} dmg from {who}"
    if ff.certain_death:
        out += "  DIES"
    elif ff.lethal:
        out += f"  CAN DIE (survives on {bars(ff.best_remaining)} at best)"
    else:
        out += f"  survives on {bars(ff.worst_remaining)}"
    if ff.crowded_out:
        out += f"  [+{len(ff.crowded_out)} crowded out]"
    if not ff.exact:
        out += "  [ordering not exhaustively searched]"
    return out


def report_units(board, player, warnings, weather=None):
    carried = {u.cargo for u in board.units if u.cargo}
    rows = []
    for u in sorted(board.units_of(player), key=lambda u: (u.y, u.x)):
        if u.slot in carried:
            continue
        ff = threat.focus_fire(board, u, weather=weather, warnings=warnings)
        rows.append((u, ff))
    return rows


def print_safety(board, unit, warnings, weather=None, limit=6):
    ranked = threat.safety(board, unit, weather=weather, warnings=warnings)
    here = (unit.x, unit.y)
    print(f"\n  every tile {unit.type} #{unit.slot} can reach, safest first:")
    for r in ranked[:limit]:
        mark = " <- here" if r.tile == here else ""
        star = f"{r.stars}*" if r.stars else "  "
        print(f"      ({r.tile[0]:2d},{r.tile[1]:2d}) {r.terrain:10s} {star} "
              f"cost {r.move_cost:2d}   {describe(r.focus, unit.hp)}{mark}")
    if len(ranked) > limit:
        print(f"      ... and {len(ranked) - limit} more")


def print_map(board, player, unit_type=None, weather=None):
    """Coverage grid: how many enemies can put a shot on each tile."""
    tmap = threat.threat_map(board, player, unit_type=unit_type,
                             weather=weather)
    label = f" for {unit_type}" if unit_type else ""
    print(f"\n  threat coverage{label} (digits = attackers able to reach, "
          f"'.' = clear):")
    header = "     " + "".join(str(x % 10) for x in range(board.width))
    print(header)
    for y in range(board.height):
        line = []
        for x in range(board.width):
            n = len(tmap.get((x, y), ()))
            line.append("." if n == 0 else ("+" if n > 9 else str(n)))
        print(f"  {y:2d} " + "".join(line))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("state", help="JSON from harness/mgba_state.lua")
    p.add_argument("--player", type=int,
                   help="whose point of view; defaults to the active player")
    p.add_argument("--unit", type=int,
                   help="slot number: rank every tile this unit can move to")
    p.add_argument("--map", action="store_true",
                   help="print the threat coverage grid")
    p.add_argument("--for", dest="for_type",
                   help="with --map, count only enemies that can hurt this type")
    p.add_argument("--weather", help="ask a hypothetical instead of the board's")
    p.add_argument("--limit", type=int, default=6,
                   help="tiles to list with --unit")
    a = p.parse_args()

    board = load(a.state)
    print(summarise(board).splitlines()[0])
    for w in board.warnings:
        print(f"  !! {w}")

    player = a.player or board.active_player
    if not player:
        print("!! no active player in this dump and --player not given")
        return 1

    warnings = []
    if a.map:
        print_map(board, player, a.for_type, a.weather)

    if a.unit is not None:
        unit = next((u for u in board.units if u.slot == a.unit), None)
        if unit is None:
            print(f"!! no unit in slot {a.unit}")
            return 1
        if unit.player != player:
            print(f"!! slot {a.unit} belongs to P{unit.player}, not P{player}")
            return 1
        print_safety(board, unit, warnings, a.weather, a.limit)
    elif not a.map:
        print(f"\nP{player} exposure -- what the enemy can do on their next turn:")
        rows = report_units(board, player, warnings, a.weather)
        if not rows:
            print("  (no units)")
        for u, ff in rows:
            terr = board.terrain_name(u.x, u.y)
            print(f"  {u.type:10s} #{u.slot:<3d} ({u.x:2d},{u.y:2d}) "
                  f"{bars(u.hp):>7s} on {terr:10s} {describe(ff, u.hp)}")
        at_risk = [u for u, ff in rows if ff.lethal]
        if at_risk:
            print(f"\n  CAN BE KILLED THIS TURN: "
                  + ", ".join(f"{u.type} #{u.slot}" for u in at_risk))

    for w in warnings:
        print(f"\n  !! {w}")
    if warnings:
        print("\n  Worst-case damage also ignores your counterattacks weakening")
        print("  the attackers, so it is an upper bound. See engine/threat.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

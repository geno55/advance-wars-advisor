"""Every legal action your units have this turn, read off a live board.

    python tools/action_report.py state.json
    python tools/action_report.py state.json --unit 12

The default pass is one line per unit -- what kinds of action it has and the
factual extremes (its hardest available hit, its least-exposed tile). `--unit`
lists everything: every attack with its counter and next-turn exposure, every
capture with the turn count, every load, and the safest tiles to wait on.

Facts only. Attacks are printed hardest-hitting first and waits least-exposed
first, which is a filing convention: nothing here says which action to take,
because every number is composed from measured tables and a recommendation
would not be. That line is drawn on purpose -- see engine/actions.py.
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import actions, fog                                # noqa: E402
from engine.damage import screen_bars                          # noqa: E402
from engine.state import load, summarise                       # noqa: E402


def span(lo, hi):
    return f"{lo}-{hi}" if lo != hi else f"{lo}"


def exposure_phrase(ff):
    """Next enemy turn, in the same terms threat_report uses."""
    if ff is None:
        return "no next turn to project (worst case is death this turn)"
    dark = f" [{ff.blind_spots} unlit tiles in reach]" if ff.blind_spots else ""
    if not ff.delivered:
        return ("untouched next turn" if not dark
                else "nothing VISIBLE reaches it next turn" + dark)
    out = (f"then {span(ff.best_damage, ff.worst_damage)} dmg from "
           f"{ff.attackers} next turn")
    if ff.certain_death:
        out += "  DIES"
    elif ff.lethal:
        out += f"  CAN DIE (best case {screen_bars(ff.best_remaining)} bars)"
    if not ff.exact:
        out += "  [ordering not exhaustively searched]"
    return out + dark


def strike_phrase(a):
    out = f"{span(a.strike.min_damage, a.strike.max_damage)} dmg"
    if a.strike.guaranteed_kill:
        out += " KILLS"
    elif a.strike.possible_kill:
        out += " (kill possible)"
    if a.counter is None:
        out += ", no counter"
    else:
        out += f", counter {span(a.counter.min_damage, a.counter.max_damage)}"
        if a.counter.guaranteed_kill:
            out += " KILLS YOU"
        elif a.counter.possible_kill:
            out += " (can kill you)"
    return out


def turn_start_phrase(a):
    """The next-morning facts on this tile, when there are any (DERIVATION 33
    via engine/supply.py: burn, the crash at 0, property service, APC
    auto-supply -- in the game's own order)."""
    ts = a.turn_start
    if ts is None:
        return ""
    bits = []
    if ts.crashes:
        return "  RUNS DRY: removed at turn start"
    if ts.serviced:
        heal = (f"repairs to {screen_bars(ts.hp_after)} bars"
                + (f" for {ts.repair_spent}" if ts.repair_spent else " free")
                if ts.hp_after > a.unit.hp else "resupplies")
        bits.append(heal)
    elif ts.auto_supplied:
        bits.append("APC tops it up")
    if not bits and ts.burn and ts.fuel_after <= 3 * ts.burn:
        bits.append(f"fuel {ts.fuel_after} after burn")
    return f"  [{', '.join(bits)} at turn start]" if bits else ""


def print_unit(board, unit, acts, limit=6):
    here = (unit.x, unit.y)
    print(f"\n{unit.type} #{unit.slot} ({unit.x},{unit.y}) "
          f"{screen_bars(unit.hp)} bars, ammo {unit.ammo}, fuel {unit.fuel}:")
    if not acts:
        return

    attacks = sorted((a for a in acts if a.kind == "attack"),
                     key=lambda a: (-a.strike.max_damage, a.tile))
    if attacks:
        print("  attacks (hardest-hitting first):")
        for a in attacks:
            star = f"{a.stars}*" if a.stars else "  "
            print(f"    -> {a.target.type} #{a.target.slot} "
                  f"({a.target.x},{a.target.y}): {strike_phrase(a)}, "
                  f"from ({a.tile[0]},{a.tile[1]}) {a.terrain} {star} "
                  f"{exposure_phrase(a.exposure)}")

    for a in (a for a in acts if a.kind == "capture"):
        mark = " <- continuing" if a.tile == here and unit.capture else ""
        done = ("falls THIS TURN" if a.captures_now
                else f"{a.progress_after}/20 after this turn, "
                     f"done in {a.capture_turns_left}")
        print(f"  capture {a.terrain} at ({a.tile[0]},{a.tile[1]}): {done}, "
              f"{exposure_phrase(a.exposure)}{mark}")

    for a in (a for a in acts if a.kind == "load"):
        print(f"  load into {a.target.type} #{a.target.slot} at "
              f"({a.tile[0]},{a.tile[1]}); the ride "
              f"{exposure_phrase(a.exposure)} (you go with it)")

    for a in (a for a in acts if a.kind == "drop"):
        print(f"  drop {a.target.type} #{a.target.slot} at "
              f"({a.drop_tile[0]},{a.drop_tile[1]}) from "
              f"({a.tile[0]},{a.tile[1]}); it lands acted, "
              f"{exposure_phrase(a.exposure)}{turn_start_phrase(a)}")

    for a in (a for a in acts if a.kind == "join"):
        m = a.merge
        extra = f", refund {m.refund}" if m.refund else ""
        print(f"  join {a.target.type} #{a.target.slot} at "
              f"({a.tile[0]},{a.tile[1]}): {screen_bars(a.hp_after)} bars, "
              f"fuel {a.fuel_after}, ammo {m.ammo_after}{extra}; "
              f"{exposure_phrase(a.exposure)}{turn_start_phrase(a)}")

    for a in (a for a in acts if a.kind == "supply"):
        who = ", ".join(
            f"{f.target.type} #{f.target.slot} (fuel {f.target.fuel}->"
            f"{f.fuel_to}" + (f", ammo {f.target.ammo}->{f.ammo_to})"
                              if f.ammo_to else ")")
            for f in a.supplies)
        print(f"  supply from ({a.tile[0]},{a.tile[1]}): fills {who} -- "
              f"free; {exposure_phrase(a.exposure)}")

    waits = sorted((a for a in acts if a.kind == "wait"),
                   key=lambda a: (a.exposure.worst_damage, a.move_cost, a.tile))
    print(f"  wait ({len(waits)} tiles, least exposed first):")
    for a in waits[:limit]:
        mark = " <- here" if a.tile == here else ""
        star = f"{a.stars}*" if a.stars else "  "
        print(f"    ({a.tile[0]:2d},{a.tile[1]:2d}) {a.terrain:10s} {star} "
              f"cost {a.move_cost:2d}  {exposure_phrase(a.exposure)}"
              f"{turn_start_phrase(a)}{mark}")
    if len(waits) > limit:
        print(f"    ... and {len(waits) - limit} more")


def summary_line(board, unit, acts):
    attacks = [a for a in acts if a.kind == "attack"]
    caps = [a for a in acts if a.kind == "capture"]
    loads = [a for a in acts if a.kind == "load"]
    waits = [a for a in acts if a.kind == "wait"]
    bits = []
    if attacks:
        hardest = max(attacks, key=lambda a: a.strike.max_damage)
        targets = len({a.target.slot for a in attacks})
        bits.append(f"{len(attacks)} attack(s) on {targets} target(s), "
                    f"hardest {span(hardest.strike.min_damage, hardest.strike.max_damage)}"
                    f" on {hardest.target.type} #{hardest.target.slot}")
    if caps:
        soonest = min(caps, key=lambda a: a.capture_turns_left)
        bits.append("capture available"
                    + (" (falls this turn)" if soonest.captures_now
                       else f" ({soonest.capture_turns_left} turns)"))
    if loads:
        bits.append(f"{len(loads)} transport(s) in reach")
    drops = [a for a in acts if a.kind == "drop"]
    if drops:
        bits.append(f"can drop onto {len({a.drop_tile for a in drops})} tile(s)")
    joins = [a for a in acts if a.kind == "join"]
    if joins:
        bits.append(f"can join {len({a.target.slot for a in joins})} unit(s)")
    sups = [a for a in acts if a.kind == "supply"]
    if sups:
        needy = len({f.target.slot for a in sups for f in a.supplies})
        bits.append(f"can supply {needy} unit(s)")
    if waits:
        calmest = min(a.exposure.worst_damage for a in waits)
        bits.append(f"{len(waits)} tiles"
                    + (", some untouched" if calmest == 0
                       else f", best still takes {calmest}"))
    return "; ".join(bits) if bits else "nothing to do"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("state", help="JSON from harness/mgba_state.lua")
    p.add_argument("--player", type=int,
                   help="whose units; defaults to the active player")
    p.add_argument("--unit", type=int,
                   help="slot number: list every action this unit has")
    p.add_argument("--weather", help="ask a hypothetical instead of the board's")
    p.add_argument("--limit", type=int, default=6,
                   help="wait tiles to list per unit with --unit")
    p.add_argument("--fog", dest="fog", action="store_true", default=None,
                   help="fog is on; only visible enemies are offered as targets")
    p.add_argument("--no-fog", dest="fog", action="store_false",
                   help="fog is off; silences the unknown warning")
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
    fog_on = board.fog if a.fog is None else a.fog
    if fog_on:
        print("\n" + fog.summarise(board, player))

    if a.unit is not None:
        unit = next((u for u in board.units if u.slot == a.unit), None)
        if unit is None:
            print(f"!! no unit in slot {a.unit}")
            return 1
        if unit.player != player:
            print(f"!! slot {a.unit} belongs to P{unit.player}, not P{player}")
            return 1
        acts = actions.actions_for(board, unit, weather=a.weather, fog=a.fog,
                                   warnings=warnings)
        if not acts:
            print(f"\n{unit.type} #{unit.slot} has no actions "
                  f"({'already acted' if unit.acted else 'riding a transport'})")
        print_unit(board, unit, acts, a.limit)
    else:
        per_unit = actions.all_actions(board, player, weather=a.weather,
                                       fog=a.fog, warnings=warnings)
        print(f"\nP{player} options -- what each unit can still do this turn:")
        if not per_unit:
            print("  (no unit can act)")
        for slot, acts in sorted(per_unit.items()):
            u = next(x for x in board.units if x.slot == slot)
            print(f"  {u.type:10s} #{slot:<3d} ({u.x:2d},{u.y:2d})  "
                  f"{summary_line(board, u, acts)}")
        print("\n  --unit N for the full list, with counters and exposure")

    # focus_fire warns about fog per hypothetical board, and the unlit count
    # differs on each, so content-dedupe lets dozens through. The count that
    # matters is the per-action blind_spots already printed on every line, and
    # the fog banner above said the rest -- collapse them to one line here.
    fog_notes = [w for w in warnings if w.startswith("fog of war is ON")]
    if fog_notes:
        warnings = [w for w in warnings if w not in fog_notes]
        warnings.append(
            "fog of war is ON: hidden units are absent from every number "
            "above; the per-action blind-spot counts are the tiles they "
            "could be in.")
    for w in warnings:
        print(f"\n  !! {w}")
    if warnings:
        print("\n  Exposure worst cases also ignore your counterattacks "
              "weakening\n  the attackers, so they are upper bounds. "
              "See engine/threat.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

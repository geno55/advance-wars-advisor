"""Interactive battle recorder. Run this instead of hand-editing the CSV.

You set a matchup once, then type one number per trial. It appends valid rows to
observations.csv, and after every entry it re-runs the hypothesis sweep and tells
you how many are left -- so you stop the moment it converges instead of grinding
out battles you did not need.

    python harness/record.py

Protocol reminders it enforces for you:
  * both units at FULL health unless you say otherwise (full health is the only
    internal HP you can know by looking)
  * a neutral CO with no power active (it asks, and records what you said)
  * the terrain is the DEFENDER's tile

ON SAVE STATES AND THE RNG -- read this before grinding out repeats.

A save state restores the whole machine, RNG included. If the game only advances
its RNG when it asks for a number, then reloading and repeating the SAME attack
reproduces the SAME luck roll, and ten "trials" are ten copies of one
observation. If instead the RNG ticks every frame, your variable timing between
load and confirm decorrelates them and repeats really do sample.

Which one AW1 does is unverified. Test it first: attack, note the result, reload,
repeat the identical attack, three times over.
  * results differ  -> repeats sample fine
  * results identical -> deterministic; repeats teach you nothing, so vary the
    matchup, terrain, or attacker HP instead. Each distinct combination is a
    fresh constraint, and you will converge on fewer total battles.

This tool watches for the identical-run case and tells you when to move on.
"""
from __future__ import annotations

import csv
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "tests"))

from calibrate import Obs, predict, survivors  # noqa: E402
from engine.damage import VARIANTS, select_weapon, tables  # noqa: E402

CSV_PATH = HERE / "observations.csv"
FIELDS = ["attacker", "defender", "att_hp", "def_hp", "terrain", "mode",
          "observed", "co_attack", "co_defense", "notes"]

TERRAINS = ["plains", "woods", "mountain", "road", "city", "river", "bridge",
            "shoal", "reef", "sea", "base", "airport", "port", "hq"]

UNITS = sorted(tables()["unit_ids"].values())


def load_existing():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh)
                if r.get("attacker") and not r["attacker"].lstrip().startswith("#")]
    return [Obs(r, i + 2) for i, r in enumerate(rows)]


def append_row(row):
    exists = CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow(row)


def drop_last_row():
    """Undo: remove the final data row, leaving comments and header intact."""
    lines = CSV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if s and not s.startswith("#") and not s.startswith("attacker,"):
            del lines[i]
            CSV_PATH.write_text("".join(lines), encoding="utf-8")
            return True
    return False


SHARED_LUCK = False
EXACT = False           # --exact: you can read internal HP (1..100) from RAM


def status(obs):
    """Re-run the sweep and describe where we stand."""
    if not obs:
        return "no observations yet"
    alive = survivors(obs, shared_luck=SHARED_LUCK)
    terrains = sorted({o.terrain for o in obs})
    total = len(VARIANTS) * 5 ** len(terrains) * (10 if SHARED_LUCK else 1)
    if not alive:
        return ("*** NOTHING survives -- a row is probably mis-recorded, a CO\n"
                "    modifier was active, or (in shared-luck mode) the roll is not\n"
                "    actually frozen. Use 'u' to undo the last entry.")
    if len(alive) == 1:
        v, sm = alive[0][0], alive[0][1]
        extra = f" luck={alive[0][2]}" if len(alive[0]) > 2 else ""
        return (f"*** CONVERGED. variant={v} stars={sm}{extra}\n"
                f"    You can stop recording.")
    vs = sorted({h[0] for h in alive})
    bits = [f"{len(alive)}/{total} hypotheses", f"{len(vs)} formula(s)"]
    for t in terrains:
        vals = sorted({h[1][t] for h in alive})
        bits.append(f"{t}={vals[0]}*" if len(vals) == 1
                    else f"{t}={'/'.join(map(str, vals))}")
    if SHARED_LUCK:
        lucks = sorted({h[2] for h in alive})
        bits.append(f"luck={lucks[0]}*" if len(lucks) == 1 else f"luck x{len(lucks)}")
    return "  " + ", ".join(bits)


def ask_matchup():
    print("\nMatchup. Format: <attacker> <defender> <terrain> [att_hp] [def_hp]")
    print(f"  units:    {', '.join(UNITS)}")
    print(f"  terrain:  {', '.join(TERRAINS[:8])} ...  (the DEFENDER's tile)")
    print("  hp is internal 1-100 and defaults to 100 (full). Blank line quits.")
    while True:
        raw = input("matchup> ").strip()
        if not raw or raw.lower() in ("q", "quit", "exit"):
            return None
        parts = raw.split()
        if len(parts) < 3:
            print("  need at least: attacker defender terrain")
            continue
        att, dfn, terr = parts[0], parts[1], parts[2].lower()
        att = next((u for u in UNITS if u.lower() == att.lower()), att)
        dfn = next((u for u in UNITS if u.lower() == dfn.lower()), dfn)
        if att not in UNITS or dfn not in UNITS:
            print(f"  unknown unit. valid: {', '.join(UNITS)}")
            continue
        if select_weapon(att, dfn) is None:
            print(f"  {att} cannot attack {dfn} at all -- pick another matchup.")
            continue
        try:
            ahp = int(parts[3]) if len(parts) > 3 else 100
            dhp = int(parts[4]) if len(parts) > 4 else 100
        except ValueError:
            print("  hp must be a number 1-100")
            continue
        if not (1 <= ahp <= 100 and 1 <= dhp <= 100):
            print("  hp must be 1-100")
            continue
        return att, dfn, terr, ahp, dhp


def main():
    global SHARED_LUCK, EXACT
    SHARED_LUCK = "--shared-luck" in sys.argv
    EXACT = "--exact" in sys.argv
    print(__doc__.split("Protocol")[0].strip())
    if EXACT:
        print("\nEXACT MODE: you type the defender's internal HP (0-100) from RAM.")
        print("Far more informative than bars -- expect to converge in a few battles.")
    if SHARED_LUCK:
        print("\nSHARED-LUCK MODE: assuming one frozen roll explains every battle.")
        print("Only valid if every battle is launched from the SAME save state and")
        print("your determinism test showed identical repeats.")
    print(f"\nappending to {CSV_PATH}")
    obs = load_existing()
    print(f"existing observations: {len(obs)}")
    if obs:
        print(status(obs))

    co = input("\nWhich CO are you playing? (must be neutral, no power active) > ")
    co = co.lstrip("﻿").strip().replace(",", " ") or "unknown"
    print(f"recording co_attack=100 co_defense=100, noting '{co}'.")
    print("If damage looks scaled versus the predictions, that CO is not neutral -- stop and say so.")

    while True:
        m = ask_matchup()
        if m is None:
            break
        att, dfn, terr, ahp, dhp = m
        hi = 100 if EXACT else 10
        what = ("INTERNAL HP (0-100, read from RAM)" if EXACT
                else "remaining HP bars (0-10)")
        print(f"\nRecording {att} ({ahp}) -> {dfn} ({dhp}) on {terr}.")
        print(f"Save state, attack, read the DEFENDER's {what}, type it.")
        print(f"  0-{hi} = record a trial   u = undo last   "
              "<blank> = new matchup   q = quit")
        trial = 0
        run = []          # results for this matchup, to spot a frozen RNG
        while True:
            raw = input(f"  [{terr}] {att}->{dfn} result> ").strip().lower()
            if raw == "q":
                print(f"\nfinal: {len(obs)} observations")
                print(status(obs))
                return
            if raw == "":
                break
            if raw == "u":
                if obs and drop_last_row():
                    obs.pop()
                    trial = max(0, trial - 1)
                    if run:
                        run.pop()
                    print(f"    undone. {len(obs)} left.\n{status(obs)}")
                else:
                    print("    nothing to undo")
                continue
            try:
                val = int(raw)
            except ValueError:
                print("    type a number 0-10, or u / blank / q")
                continue
            if not (0 <= val <= hi):
                print(f"    value must be 0-{hi}")
                continue

            # In exact mode the player types the DEFENDER'S REMAINING internal
            # HP; the schema wants damage dealt, so convert here rather than
            # making them do arithmetic at the keyboard.
            observed = (dhp - val) if EXACT else val
            row = {"attacker": att, "defender": dfn, "att_hp": ahp,
                   "def_hp": dhp, "terrain": terr,
                   "mode": "exact" if EXACT else "display_after",
                   "observed": observed, "co_attack": 100, "co_defense": 100,
                   "notes": f"co={co}"}
            o = Obs({k: str(v) for k, v in row.items()}, 0)

            # Sanity check before writing: is this even reachable under ANY
            # surviving hypothesis? If not, say so rather than poison the data.
            reachable = any(val in predict(o, v, s)
                            for v in VARIANTS for s in range(5))
            append_row(row)
            obs.append(o)
            run.append(val)
            trial += 1
            if not reachable:
                print(f"    !! {val} is impossible for this matchup under every "
                      "formula and terrain value.")
                print("       Check you read the DEFENDER's HP and both units were "
                      "at the stated health. 'u' to undo.")
            print(f"    trial {trial} (total {len(obs)})")
            print(status(obs))
            if len(run) >= 3 and len(set(run)) == 1:
                print(f"    note: {len(run)} identical results ({run[0]}). Your save "
                      "state is restoring the RNG,")
                print("       so further repeats of THIS attack add no information. "
                      "Change matchup,")
                print("       terrain, or attacker HP -- blank line for a new matchup.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\ninterrupted -- rows already written are kept")

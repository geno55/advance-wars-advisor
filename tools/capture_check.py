"""Score mgba_capture.lua's probes against what the model claims.

    python tools/capture_check.py cap_menu.json [cap_rate.json] [cap_move.json]

Three probes, three different questions:

  menu   A15a -- is `unit_class == "foot"` exactly the set of capturers?
         Scored per type. A row where the unit never ACTED is UNREADABLE (the
         drive sequence failed), not a "cannot capture". Naval types standing
         on a city are a board the game may never reach, so their rows are
         reported as SUSPECT rather than as findings either way.

  rate   a live cross-check on the ROM read at 0x08026180: progress moves by
         ceil(hp/10), clamps at 20, and the property falls when it gets there.
         The check assumes a NEUTRAL CO -- a Sami fixture reads +50% and this
         tool will loudly misscore it, so use anyone else.

  move   A15c -- does moving reset progress? Interpretable only if the `stay`
         row shows written progress SURVIVING an in-place capture (10 -> 10 +
         bars). If the game discards written progress even without moving, the
         move row answers nothing and the tool says so instead of scoring it.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def stats():
    return json.loads((ROOT / "data" / "aw1_unit_stats.json")
                      .read_text(encoding="utf-8"))["units"]


def by_ram_id():
    return {s["id"] + 1: (name, s) for name, s in stats().items()}


def bars(hp):
    return -(-hp // 10)


def check_menu(data):
    ids = by_ram_id()
    ctrl = data.get("control", {})
    print("menu sweep -- who may capture (model: unit_class == 'foot'):")
    if ctrl.get("capture_after", 0) <= 0:
        print("  !! the CONTROL row did not capture -- the drive sequence is"
              " broken and nothing below is a measurement. Re-park the fixture.")
        return False
    ok = True
    for c in data["cases"]:
        name, st = ids.get(c["type"], (f"id{c['type']}", None))
        if st is None:
            print(f"  {name:11s} unknown type id, skipped")
            continue
        expect = st["unit_class"] == "foot"
        got = c["capture_after"] > c["capture_before"]
        suspect = st["unit_class"] == "naval"
        if c["acted"] == 0 and not got:
            verdict = "UNREADABLE (never acted)"
        elif suspect:
            verdict = (f"suspect (naval on a city is not a reachable board) -- "
                       f"read {'captured' if got else 'no capture'}")
        elif got == expect:
            verdict = "confirmed"
        else:
            verdict = (f"REFUTED: model says {'may' if expect else 'may not'} "
                       f"capture, game says {'did' if got else 'did not'}")
            ok = False
        print(f"  {name:11s} class {st['unit_class']:6s} "
              f"{c['capture_before']:2d} -> {c['capture_after']:2d}  {verdict}")
    return ok


def check_rate(data):
    print("\nrate rows -- cross-check on the ROM arithmetic (NEUTRAL CO only):")
    ok = True
    for c in data["cases"]:
        want = min(20, c["capture_before"] + bars(c["hp"]))
        fell = c["owner_after"] != 0
        mark = "ok" if c["capture_after"] == want else "MISMATCH"
        if c["capture_after"] != want:
            ok = False
        fall = ""
        if want >= 20:
            fall = ("  property fell" if fell
                    else "  !! reached 20 but the owner did not change")
            if not fell:
                ok = False
        print(f"  hp {c['hp']:3d} ({bars(c['hp']):2d} bars) "
              f"{c['capture_before']:2d} -> {c['capture_after']:2d} "
              f"(expected {want:2d})  {mark}{fall}")
    if not ok:
        print("  a mismatch here on a non-neutral fixture is the CO capture "
              "modifier, not an error -- Sami adds bars >> 1. Re-run neutral.")
    return ok


def check_move(data):
    print("\nmove probe -- does moving reset progress (A15c)?")
    stay, move = data["cases"][0], data["cases"][1]
    stay_kept = stay["capture_after"] == min(20, 10 + bars(stay["hp"]))
    if not stay_kept:
        print(f"  stay row: 10 -> {stay['capture_after']} -- written progress "
              f"did NOT survive an in-place capture, so the move row cannot be "
              f"read. The reset happens at action time regardless of movement, "
              f"which itself refutes the 'moving resets' framing: record it.")
        return False
    print(f"  stay row: 10 -> {stay['capture_after']}  (written progress "
          f"survives; the probe is readable)")
    b = bars(move["hp"])
    if move["capture_after"] == b:
        print(f"  move row: 10 -> {move['capture_after']} = its bar count -- "
              f"moving RESET the progress. A15c CONFIRMED.")
    elif move["capture_after"] == min(20, 10 + b):
        print(f"  move row: 10 -> {move['capture_after']} = 10 + bars -- "
              f"moving KEPT the progress. A15c REFUTED; the engine resets it "
              f"and must stop.")
    else:
        print(f"  move row: 10 -> {move['capture_after']} -- neither branch "
              f"predicted this; do not record it, investigate.")
    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    ok = True
    for arg in sys.argv[1:]:
        data = json.loads(pathlib.Path(arg).read_text(encoding="utf-8"))
        probe = data.get("probe")
        if probe == "menu":
            ok = check_menu(data) and ok
        elif probe == "rate":
            ok = check_rate(data) and ok
        elif probe == "move":
            ok = check_move(data) and ok
        else:
            print(f"{arg}: unknown probe {probe!r}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

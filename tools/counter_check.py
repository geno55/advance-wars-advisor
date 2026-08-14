"""Test the two counterattack hypotheses against a seed sweep.

    python tools/counter_check.py C:/tmp/counter.json

H1 -- the shipped model. `engine/damage.py:counterattack()` runs the full strike
      formula on the survivor's DISPLAY hp, so it carries a luck roll and spans
      a range of values.

H2 -- the ROM at 0x080234DA-0x080234F6, which overwrites the value the strike
      path computed with `base * raw_internal_hp / 100` before applying the
      defence multiplier: no display quantisation and no luck, so it is one
      number for a given survivor.

The two differ in KIND, not degree. The opening attack is seeded across the
whole 32-bit space, so its damage varies; if the counter does not vary with it,
a luck-carrying counter is refuted outright and no statistics are needed.

Reads the JSON that `dmg_seedsweep` writes. Sweeps recorded before the harness
learned to read the attacker carry no `counter` field, and are rejected rather
than silently scored as zero.
"""
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.damage import Attack, resolve  # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def main(path):
    sweep = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    units = load("aw1_unit_stats.json")["units"]
    terrain = load("aw1_terrain.json")["terrain"]
    dmg_tbl = load("aw1_damage.json")

    # RAM type ids are 1-based; table rows are 0-based. See ASSUMPTIONS.
    by_row = {v["id"]: k for k, v in units.items()}
    att = by_row[sweep["attacker_type"] - 1]
    dfn = by_row[sweep["defender_type"] - 1]
    # Prefer the tile the attacker FOUGHT from. A fixture sits at target-select,
    # and if the unit record still holds the pre-move tile then
    # `attacker_terrain` names where it came from -- so the counterattack is
    # scored against a tile it never stood on. Sweeps taken before the harness
    # recorded both carry only the ambiguous one; say so rather than guessing.
    att_after = sweep.get("attacker_terrain_after")
    if att_after is None:
        att_stars = terrain[str(sweep["attacker_terrain"])]["stars"]
        stale_terrain = True
    else:
        att_stars = terrain[str(att_after)]["stars"]
        stale_terrain = False
        if att_after != sweep["attacker_terrain"]:
            print(f"!! attacker moved on confirm: fixture tile "
                  f"{sweep['attacker_terrain']}, fought from {att_after}. "
                  "Scoring against the second.")
    def_stars = terrain[str(sweep["defender_terrain"])]["stars"]
    att_hp0 = sweep["attacker_hp"]

    # The counterattacker is the DEFENDER, shooting back at the attacker.
    base = max(dmg_tbl["primary"].get(dfn, {}).get(att, 0),
               dmg_tbl["secondary"].get(dfn, {}).get(att, 0))
    if base == 0:
        print(f"{dfn} has no weapon that damages {att}; this sweep cannot "
              "test the counter formula.")
        return 1

    if stale_terrain:
        print("!! This sweep predates attacker_terrain_after, so the attacker's")
        print("!! terrain may be its PRE-MOVE tile. Counter results below are")
        print("!! attributed to a tile it may never have stood on.")
        print()
    print(f"{att} (hp {att_hp0}, {att_stars}-star terrain) attacks "
          f"{dfn} (hp {sweep['defender_hp']}, {def_stars}-star terrain)")
    print(f"counterattack is {dfn} -> {att}, base {base}, "
          f"landing on {att_stars} stars\n")

    ctl = sweep.get("controls")
    if ctl:
        same = ctl["seed"] == ctl["identity"]
        print(f"controls: none={ctl['none']}  seed={ctl['seed']}  "
              f"identity={ctl['identity']}")
        print(f"          write transparent: {'yes' if same else 'NO'}\n")
        if not same:
            print("!! The identity write changed the result. Nothing measured "
                  "with this write means anything.")
            return 1
    else:
        print("!! This sweep shipped no control case, so nothing separates a "
              "measurement\n!! from the machine agreeing with itself. Re-run "
              "with the current harness.\n")
    written = sweep.get("hp_written") or {}
    if any(written.values()):
        print(f"hp written each case: {written}  "
              f"(fixture held {sweep.get('hp_in_fixture')})\n")

    cases = sweep.get("cases", [])
    if not cases or "counter" not in cases[0]:
        print("!! This sweep has no 'counter' field. It predates the harness "
              "reading the attacker.\n!! Re-run dmg_seedsweep after updating "
              "harness/mgba_dmg.lua.")
        return 1

    # H2's defence multiplier. The target here is the original attacker, whose
    # HP does not change until the counter lands, so its display value is the
    # same under every candidate rule whenever att_hp0 is a multiple of 10.
    co_def = 100
    hp_d = -(-att_hp0 // 10)              # ceil; see A9a
    dfn_mult = 200 - (co_def + att_stars * hp_d)

    rows, h1_ok, h2_ok = [], 0, 0
    seen_counter = collections.Counter()
    seen_damage = collections.Counter()
    for c in cases:
        if c.get("attacker_destroyed"):
            continue
        opening, counter = c["damage"], c["counter"]
        survivor = sweep["defender_hp"] - opening
        if survivor <= 0:
            continue
        seen_damage[opening] += 1
        seen_counter[counter] += 1

        out = resolve(Attack(dfn, att, survivor, att_hp0, att_stars),
                      verified=True)
        h1_lo, h1_hi = (out.min_damage, out.max_damage) if out else (None, None)
        h2 = (base * survivor // 100) * dfn_mult // 100

        in_h1 = out is not None and h1_lo <= counter <= h1_hi
        h1_ok += in_h1
        h2_ok += (counter == h2)
        rows.append((opening, survivor, counter, h1_lo, h1_hi, h2, in_h1))

    n = len(rows)
    if not n:
        print("no usable cases (defender always died, or attacker destroyed)")
        return 1

    print(f"{'open':>5} {'survivor':>9} {'counter':>8} | {'H1 range':>9} "
          f"{'hit':>4} | {'H2':>4} {'hit':>4}")
    for opening, survivor, counter, lo, hi, h2, in_h1 in rows[:16]:
        print(f"{opening:5d} {survivor:9d} {counter:8d} | "
              f"{f'{lo}-{hi}':>9} {'ok' if in_h1 else 'MISS':>4} | "
              f"{h2:4d} {'ok' if counter == h2 else 'MISS':>4}")
    if n > 16:
        print(f"   ... {n - 16} more")

    print(f"\nopening damage values: "
          f"{'  '.join(f'{k}x{v}' for k, v in sorted(seen_damage.items()))}")
    print(f"counter damage values: "
          f"{'  '.join(f'{k}x{v}' for k, v in sorted(seen_counter.items()))}")
    print(f"\nH1 (shipped model, luck-carrying range): {h1_ok}/{n} cases inside")
    print(f"H2 (ROM, deterministic):                 {h2_ok}/{n} cases exact")

    print()
    if len(seen_damage) > 1 and len(seen_counter) == 1:
        print("VERDICT: the opening damage varied across seeds and the counter")
        print("did not. A counter carrying its own luck roll cannot do that.")
        print("H1 is refuted. counterattack() has the wrong formula shape.")
    elif h2_ok == n and h1_ok < n:
        print("VERDICT: H2 reproduces every case exactly and H1 does not.")
    elif h1_ok == n and h2_ok < n:
        print("VERDICT: H1 holds and H2 is refuted -- the ROM reading at")
        print("0x080234DA is not the live path. A9b is wrong; say so in "
              "ASSUMPTIONS.")
    elif h1_ok == n and h2_ok == n:
        print("VERDICT: both survive. This sweep does not separate them --")
        print("pick a fixture where H1's range is wider than one value.")
    else:
        print("VERDICT: neither hypothesis fits. Do not append anything; the")
        print("board state is not what this tool was told it was.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))

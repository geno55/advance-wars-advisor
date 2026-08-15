"""Measure a CO's luck range from a seed sweep, and score the ROM's prediction.

    python tools/luck_range_check.py C:/tmp/nell_sweep.json
    python tools/luck_range_check.py C:/tmp/sonja_sweep.json --expect-co 7

Ten of the twelve CO records carry 0/0 in the luck bytes at header +06/+07 and
roll the standard 0..9. Two do not, and `engine/co.py` reads them as

    luck = uniform(0, 9 + good) - bad        good = +06, bad = +07

giving Nell 0..19 (0..59 under her power) and Sonja -15..9. That is an
INTERPRETATION of two bytes. It is corroborated by the community's documented
ranges, which is worth something, but the two could easily be corroborating
each other rather than the game.

This settles it from a sweep. The generator is known exactly, so a seeded sweep
replays one attack across many rolls; inverting each observed damage back
through the shipped formula gives the roll that must have produced it. What
matters is not that the rolls land inside the predicted range -- 0..9 lands
inside 0..19 too -- but whether any roll appears OUTSIDE the standard band. One
roll above 9 confirms Nell. One below 0 confirms Sonja. Neither, after enough
seeds, is evidence the extension is not real.

So the tool reports three things and refuses to conflate them: what the ROM
predicts, what the sweep actually witnessed, and whether the sweep was capable
of telling the difference.

Take the sweep with the CO written, which harness/mgba_dmg.lua already
supports -- the sweep JSON records it as `co_written`.
"""
import argparse
import json
import pathlib
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine import co as co_mod                               # noqa: E402
from engine import damage as dmg                              # noqa: E402

# Wide enough to contain any range the records could encode, so a roll outside
# the prediction is FOUND rather than excluded by the search window.
SEARCH_LO, SEARCH_HI = -80, 80


def load_table(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def invert(attack, observed, lo=SEARCH_LO, hi=SEARCH_HI):
    """Every luck value that would produce `observed` damage for this attack."""
    return [lk for lk in range(lo, hi + 1)
            if dmg.damage_for_luck(attack, lk) == observed]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sweep_json")
    ap.add_argument("--expect-co", type=int,
                    help="CO id the sweep was taken under; defaults to the "
                         "co_written recorded in the sweep")
    ap.add_argument("--power", action="store_true",
                    help="the CO's power was active during the sweep")
    a = ap.parse_args()

    s = json.loads(pathlib.Path(a.sweep_json).read_text(encoding="utf-8"))
    if s.get("mode") != "seed":
        sys.exit("this is not a seed sweep -- luck cannot be inverted from a "
                 "frame-delay sweep, whose rolls are clustered rather than "
                 "controlled")

    co_id = a.expect_co
    if co_id is None:
        co_id = s.get("co_written")
    if co_id is None:
        co_id = s.get("co_in_fixture")
    if co_id is None:
        sys.exit("no CO recorded in the sweep and none given; pass --expect-co")

    names = {u["id"] + 1: n
             for n, u in load_table("aw1_unit_stats.json")["units"].items()}
    att, dfn = names.get(s["attacker_type"]), names.get(s["defender_type"])
    terrain = load_table("aw1_terrain.json")["terrain"][str(s["defender_terrain"])]
    stars = load_table("aw1_terrain.json")["stars"][terrain["name"].lower()]

    rec = co_mod.record(co_id, a.power)
    predicted = co_mod.luck(co_id, a.power)
    try:
        ca = co_mod.modifiers(co_id, att, a.power)[0]
    except KeyError:
        ca = 100

    attack = dmg.Attack(attacker=att, defender=dfn,
                        attacker_hp=s["attacker_hp"],
                        defender_hp=s["defender_hp"],
                        terrain_stars=stars, ammo=s["attacker_ammo"],
                        co_attack=ca, co_defense=100)

    live = [c for c in s["cases"] if not c["destroyed"]]
    print(f"{att} (hp {s['attacker_hp']}) -> {dfn} (hp {s['defender_hp']}) "
          f"on {terrain['name']} ({stars} stars)")
    print(f"CO {rec.name} (id {co_id}{', power' if a.power else ''}): "
          f"luck bytes +06={rec.luck_good} +07={rec.luck_bad}")
    print(f"  ROM-derived prediction: {predicted[0]}..{predicted[1]}")
    print(f"  {len(live)} usable case(s) of {len(s['cases'])}")

    if s.get("co_written") is None and a.expect_co is not None:
        print("  !! the sweep records no co_written -- if the fixture was not "
              "already\n  !! this CO, the rolls below belong to a different one")

    # A destroyed defender clips the damage and destroys the inversion with it.
    ambiguous, rolls = 0, []
    for c in live:
        cands = invert(attack, c["damage"])
        if not cands:
            print(f"  !! damage {c['damage']} is not reachable by ANY luck in "
                  f"{SEARCH_LO}..{SEARCH_HI} -- the formula or the CO "
                  f"modifiers are wrong for this sweep")
            return 1
        if len(cands) > 1:
            ambiguous += 1
        rolls.append(cands)

    # Rolls that pin to exactly one value are the only ones that can witness
    # anything; a damage consistent with several rolls proves nothing about
    # which occurred.
    pinned = sorted({c[0] for c in rolls if len(c) == 1})
    if not pinned:
        print("\n  every observed damage is consistent with more than one "
              "roll, so\n  this sweep cannot witness a range at all. Pick a "
              "matchup where the\n  damage changes with every roll.")
        return 1

    lo, hi = min(pinned), max(pinned)
    print(f"\n  rolls witnessed unambiguously: {lo}..{hi} "
          f"({len(pinned)} distinct)")
    if ambiguous:
        print(f"  {ambiguous} case(s) were consistent with several rolls and "
              f"are ignored")

    counts = Counter(c[0] for c in rolls if len(c) == 1)
    print("  " + "  ".join(f"{r}:{n}" for r, n in sorted(counts.items())))

    # -- the verdict, and what it is allowed to conclude --------------------
    print()
    above = [r for r in pinned if r > dmg.LUCK_MAX]
    below = [r for r in pinned if r < 0]
    extended = predicted != (0, dmg.LUCK_MAX)

    if not extended:
        if above or below:
            print(f"REFUTED. {rec.name}'s record says a standard 0..9 roll, but "
                  f"this sweep\nwitnessed {sorted(above + below)}. The reading "
                  f"of +06/+07 is wrong.")
            return 1
        print(f"Consistent. A standard 0..9 CO rolled inside 0..9, which is "
              f"what\nboth the record and the default predict. This is a "
              f"CONTROL, not a\nmeasurement of anything new.")
        return 0

    if lo < predicted[0] or hi > predicted[1]:
        print(f"REFUTED. Predicted {predicted[0]}..{predicted[1]}, witnessed "
              f"{lo}..{hi}.\nThe rule `uniform(0, 9 + good) - bad` does not "
              f"describe this CO.")
        return 1

    if above or below:
        witness = f"a roll of {max(above)}" if above else f"a roll of {min(below)}"
        print(f"CONFIRMED. {witness} is outside the standard 0..9 band, so the "
              f"extension\nis real and not an artefact of reading two bytes. "
              f"Witnessed {lo}..{hi}\nagainst a predicted "
              f"{predicted[0]}..{predicted[1]}.")
        if lo > predicted[0] or hi < predicted[1]:
            print(f"\nThe ends are not pinned: nothing was seen at "
                  f"{predicted[0]} or {predicted[1]}. More seeds would "
                  f"narrow\nthat, though a uniform draw makes the extremes the "
                  f"rarest thing to see.")
        return 0

    print(f"NOT SETTLED. Every witnessed roll ({lo}..{hi}) falls inside the "
          f"standard\n0..9 band, which is equally consistent with "
          f"{predicted[0]}..{predicted[1]} and with this CO\nbeing ordinary. "
          f"The sweep did not test the claim. More seeds, or a\nmatchup whose "
          f"damage separates the high rolls.")
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)

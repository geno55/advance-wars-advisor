"""Score counterattack hypotheses against a seed sweep.

    python tools/counter_check.py C:/tmp/counter.json
    python tools/counter_check.py --predict            # before recording anything

TWO QUESTIONS, and the file answers whichever the sweep can.

**Shape** -- settled, and kept as a regression. H1 said a counter runs the full
strike formula on the survivor's DISPLAY hp, so it carries a luck roll and spans
a range. H2 is the ROM at `0x080234DA-0x080234F6`, which overwrites the value
the strike path computed with `base * raw_internal_hp / 100`: no display
quantisation and no luck, so it is one number for a given survivor. A 64-seed
sweep refuted H1 outright -- the opening ranged 45..50 while the counter held at
2 -- and A9b is closed on H2.

**Where the CO modifiers enter** -- the open half, and the reason this file
grew. A9b measured the counter with a neutral CO on both sides, so every
modifier position is consistent with everything recorded. `counter_damage()`
takes the counter-TARGET's defence and no attack modifier at all for the unit
doing the countering, and `counterattack()` raises rather than quote a
non-neutral CO. Nine positions survive that evidence:

    A0  the counter-attacker's ATTACK modifier does not enter
    A1  it multiplies the base, before the survivor's hp     base*co_atk//100
    A2  it multiplies the value, after the survivor's hp     (base*hp//100)*co_atk//100
    D0  the target's DEFENCE modifier does not enter
    D1  it multiplies the value, after the survivor's hp
    D2  it is added inside the terrain bracket, 200 - (co_def + stars*hp)
    D3  it multiplies the base too, immediately after the attack modifier

D3 was not in the family to begin with, and leaving it out is what made the
defence sweep report that nothing fitted. D1 was written from A14, which located
the defence modifier on the VALUE for the strike path -- but the strike path's
"value" is the base after the attack modifier and before the HP term, and this
file's D1 put it after. On the strike that distinction is invisible, because the
HP term is 1 at full health. On a counter the HP term is the survivor's raw
internal HP, which is never 1, so the two orders separate and the observed
counter came out one point below D1 on three survivors in nine.

A1 and A2 differ only where the two truncations fall, so they agree on most
survivors and separate on a few; D0/D1/D2 separate everywhere. `--predict` says
in advance whether a given board separates them at all, which is the check that
stops a sweep being recorded that cannot answer anything.

THE CONTROL THAT MATTERS. A null result here -- "the counter did not move" --
looks identical to "the CO never reached the damage path", which is exactly what
A12 caught after four sweeps. So a sweep whose counter sits in the neutral band
proves nothing on its own. It needs a companion sweep, on the same fixture, in
which something DID move. This file refuses to call a null decisive without one,
and `--predict` marks which of the planned sweeps is carrying that weight.

Reads the JSON that `dmg_seedsweep` writes. Sweeps recorded before the harness
learned to read the attacker carry no `counter` field, and are rejected rather
than silently scored as zero.
"""
import argparse
import collections
import itertools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine import co as co_mod                                    # noqa: E402
from engine.damage import Attack, display_hp, resolve              # noqa: E402

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

ARMY_SLOTS = 64                  # slot -> army, the same split the harness uses
ATTACK_POS = ("A0", "A1", "A2")
DEFENCE_POS = ("D0", "D1", "D2", "D3")


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# the hypothesis family
# --------------------------------------------------------------------------

def counter_value(base, survivor, co_atk, co_def, stars, target_hp, akind,
                  dkind):
    """One counter, under one position for each modifier.

    Everything outside the two modifiers is A9b as measured: the survivor's RAW
    internal hp over 100, truncating, then the terrain bracket on the target's
    DISPLAY hp. `akind`/`dkind` move only the pieces still in question.
    """
    b = base
    if akind == "A1":
        b = b * co_atk // 100
    if dkind == "D3":
        b = b * co_def // 100
    v = b * survivor // 100
    if akind == "A2":
        v = v * co_atk // 100
    if dkind == "D1":
        v = v * co_def // 100
    hp_d = display_hp(target_hp)
    if dkind == "D2":
        return max(0, v * (200 - (co_def + stars * hp_d)) // 100)
    return max(0, v * (100 - stars * hp_d) // 100)


def band(base, survivors, co_atk, co_def, stars, target_hp, akind, dkind):
    vals = [counter_value(base, s, co_atk, co_def, stars, target_hp, akind,
                          dkind) for s in survivors]
    return min(vals), max(vals)


# --------------------------------------------------------------------------
# reading a sweep
# --------------------------------------------------------------------------

def sweep_cos(sweep):
    """(P1 co id, P2 co id) as the GAME saw them, and whether that was read or
    assumed.

    The damage path only consults army +0x1D when [0x03004318] is set; with it
    clear it substitutes record 1, Andy, for BOTH sides (DERIVATION 24, A12). So
    the CO that was written is not necessarily the CO that acted.

    Newer sweeps record `co_p1`/`co_p2`, read back out of RAM after the last
    case. Older ones record only the player they wrote, and the other side has
    to be assumed -- which on the wood fixture is wrong, because its P2 is Max.
    That assumption is flagged rather than made quietly.
    """
    if not sweep.get("co_abilities"):
        return 1, 1, "gate clear: the game used Andy for both sides"
    if sweep.get("co_p1") is not None and sweep.get("co_p2") is not None:
        return sweep["co_p1"], sweep["co_p2"], "read back from RAM"
    written, cid = sweep.get("co_player", 1), sweep.get("co_written")
    if cid is None:
        return (None, None,
                "!! gate forced but no CO recorded -- the fixture's own COs "
                "were live and this sweep did not write them down")
    other = "P2" if written == 1 else "P1"
    return ((cid if written == 1 else 1), (cid if written == 2 else 1),
            f"!! {other} assumed to be Andy; this sweep predates co_p1/co_p2")


def effective(co_id, unit_type):
    """(attack, defence) for one CO on one unit: per-unit folded with universal,
    the same fold `tests/test_corpus.py` uses."""
    a, d = co_mod.modifiers(co_id, unit_type)
    ua, ud = co_mod.universal(co_id)
    return a * ua // 100, d * ud // 100


def describe_sweep(path):
    sweep = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    units = load("aw1_unit_stats.json")["units"]
    terrain = load("aw1_terrain.json")["terrain"]
    dmg_tbl = load("aw1_damage.json")

    by_row = {v["id"]: k for k, v in units.items()}
    att = by_row[sweep["attacker_type"] - 1]
    dfn = by_row[sweep["defender_type"] - 1]

    att_after = sweep.get("attacker_terrain_after")
    stale = att_after is None
    att_stars = terrain[str(att_after if not stale
                            else sweep["attacker_terrain"])]["stars"]
    def_stars = terrain[str(sweep["defender_terrain"])]["stars"]

    # The counterattacker is the DEFENDER shooting back, so its weapon is picked
    # against the ATTACKER. Max base, per A1.
    base = max(dmg_tbl["primary"].get(dfn, {}).get(att, 0),
               dmg_tbl["secondary"].get(dfn, {}).get(att, 0))

    p1, p2, co_note = sweep_cos(sweep)
    # Which army each slot belongs to, rather than assuming attacker == P1.
    att_army = sweep["attacker_slot"] // ARMY_SLOTS + 1
    dfn_army = sweep["defender_slot"] // ARMY_SLOTS + 1
    co_att = p1 if att_army == 1 else p2
    co_dfn = p1 if dfn_army == 1 else p2

    return dict(sweep=sweep, att=att, dfn=dfn, base=base, stale=stale,
                att_stars=att_stars, def_stars=def_stars, co_note=co_note,
                co_att=co_att, co_dfn=co_dfn, p1=p1, p2=p2,
                att_army=att_army, dfn_army=dfn_army)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def main(path, quiet_shape=False):
    d = describe_sweep(path)
    sweep, att, dfn, base = d["sweep"], d["att"], d["dfn"], d["base"]
    att_stars, def_stars = d["att_stars"], d["def_stars"]
    att_hp0 = sweep["attacker_hp"]

    if base == 0:
        print(f"{dfn} has no weapon that damages {att}; this sweep cannot "
              "test the counter formula.")
        return 1

    if d["stale"]:
        print("!! This sweep predates attacker_terrain_after, so the attacker's")
        print("!! terrain may be its PRE-MOVE tile. Counter results below are")
        print("!! attributed to a tile it may never have stood on.")
        print()
    elif sweep.get("attacker_terrain_after") != sweep.get("attacker_terrain"):
        print(f"!! attacker moved on confirm: fixture tile "
              f"{sweep['attacker_terrain']}, fought from "
              f"{sweep['attacker_terrain_after']}. Scoring against the second.")

    print(f"{att} (hp {att_hp0}, {att_stars}-star terrain, P{d['att_army']}) "
          f"attacks {dfn} (hp {sweep['defender_hp']}, {def_stars}-star "
          f"terrain, P{d['dfn_army']})")
    print(f"counterattack is {dfn} -> {att}, base {base}, "
          f"landing on {att_stars} stars")

    # The two modifiers under test, named on the units they actually index.
    co_atk = effective(d["co_dfn"], dfn)[0]      # counter-attacker attacking
    co_def = effective(d["co_att"], att)[1]      # its target defending
    print(f"COs: P1 = {d['p1']} ({co_mod.record(d['p1']).name}), "
          f"P2 = {d['p2']} ({co_mod.record(d['p2']).name})  [{d['co_note']}]")
    print(f"  counter-attacker {dfn} under "
          f"{co_mod.record(d['co_dfn']).name}: attack {co_atk}")
    print(f"  counter target   {att} under "
          f"{co_mod.record(d['co_att']).name}: defence {co_def}\n")

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

    rows, h1_ok = [], 0
    hyp_ok = {k: 0 for k in itertools.product(ATTACK_POS, DEFENCE_POS)}
    # The full prediction vector per position, in case order. Two positions that
    # produce the same vector are indistinguishable BY THIS SWEEP, whatever the
    # modifiers were -- which is a different and stricter question than whether
    # a modifier happened to be 100.
    vec = {k: [] for k in hyp_ok}
    seen_counter, seen_damage, survivors = (collections.Counter(),
                                            collections.Counter(), set())
    killed = 0
    for c in cases:
        if c.get("attacker_destroyed"):
            killed += 1
            continue
        opening, counter = c["damage"], c["counter"]
        survivor = sweep["defender_hp"] - opening
        if survivor <= 0:
            continue
        seen_damage[opening] += 1
        seen_counter[counter] += 1
        survivors.add(survivor)

        out = resolve(Attack(dfn, att, survivor, att_hp0, att_stars),
                      verified=True)
        h1_lo, h1_hi = (out.min_damage, out.max_damage) if out else (None, None)
        in_h1 = out is not None and h1_lo <= counter <= h1_hi
        h1_ok += in_h1

        preds = {}
        for ak, dk in hyp_ok:
            v = counter_value(base, survivor, co_atk, co_def, att_stars,
                              att_hp0, ak, dk)
            preds[(ak, dk)] = v
            vec[(ak, dk)].append(v)
            hyp_ok[(ak, dk)] += (counter == v)
        rows.append((opening, survivor, counter, h1_lo, h1_hi, preds, in_h1))

    n = len(rows)
    if not n:
        print("no usable cases (defender always died, or attacker destroyed)")
        return 1
    if killed:
        print(f"!! {killed} of {len(cases)} cases killed the ATTACKER, so the "
              f"counter there is\n!! capped at its hp and carries no "
              f"information. They are excluded.\n")

    # Only positions that are still alive are worth a column.
    live = [k for k, v in sorted(hyp_ok.items(), key=lambda kv: -kv[1])
            if v > 0] or [("A0", "D1")]
    head = "  ".join(f"{a}/{b}" for a, b in live[:4])
    print(f"{'open':>5} {'survivor':>9} {'counter':>8} | {'H1 range':>9} "
          f"{'hit':>4} | {head}")
    for opening, survivor, counter, lo, hi, preds, in_h1 in rows[:16]:
        cells = "  ".join(
            f"{preds[k]:5d}" + ("*" if preds[k] == counter else " ")
            for k in live[:4])
        print(f"{opening:5d} {survivor:9d} {counter:8d} | "
              f"{f'{lo}-{hi}':>9} {'ok' if in_h1 else 'MISS':>4} | {cells}")
    if n > 16:
        print(f"   ... {n - 16} more")

    print(f"\nopening damage values: "
          f"{'  '.join(f'{k}x{v}' for k, v in sorted(seen_damage.items()))}")
    print(f"counter damage values: "
          f"{'  '.join(f'{k}x{v}' for k, v in sorted(seen_counter.items()))}")

    if not quiet_shape:
        print(f"\nH1 (shipped model, luck-carrying range): {h1_ok}/{n} inside")
        if len(seen_damage) > 1 and len(seen_counter) == 1:
            print("     the opening varied and the counter did not -- a counter "
                  "carrying its\n     own luck roll cannot do that. H1 stays "
                  "refuted (A9b).")

    print(f"\nwhere the CO modifiers enter ({n} cases):")
    srt = sorted(hyp_ok.items(), key=lambda kv: -kv[1])
    for (ak, dk), ok in srt:
        lo, hi = band(base, sorted(survivors), co_atk, co_def, att_stars,
                      att_hp0, ak, dk)
        mark = "  <-- fits" if ok == n else ""
        print(f"  {ak}/{dk}: {ok:3d}/{n} exact   predicts {lo}-{hi}{mark}")

    fits = [k for k, v in hyp_ok.items() if v == n]
    print()
    return verdict(fits, vec, n, co_atk, co_def)


def _groups(vec):
    """Positions that predict identically across every case, grouped."""
    g = {}
    for k, v in vec.items():
        g.setdefault(tuple(v), []).append(k)
    return list(g.values())


def verdict(fits, vec, n, co_atk, co_def):
    if not fits:
        print("VERDICT: no position reproduces every case. Do not append "
              "anything;\nthe board is not what this tool was told it was, or "
              "the counter takes a\nshape outside this family.")
        return 1

    groups = _groups(vec)
    if len(groups) == 1:
        print("VERDICT: this sweep separates nothing -- all nine positions "
              "predict the\nsame counter on every case it produced. It is a "
              "valid regression for the\nshape (A9b) and says nothing about "
              "where the modifiers enter.")
        print(f"\n  (counter-attacker's attack {co_atk}, target's defence "
              f"{co_def}; the\n  arithmetic collapses whether or not those are "
              "neutral.)")
        print("\nRun --predict and pick a board where the bands differ.")
        return 1

    # What this sweep actually pinned, axis by axis. A position can be excluded
    # on one axis and undetermined on the other, and reporting a single winner
    # would overstate both.
    fit_a = sorted({a for a, _ in fits})
    fit_d = sorted({d for _, d in fits})
    print(f"VERDICT: {len(fits)} of 9 positions reproduce all {n} cases.")
    print(f"  attack  modifier: {', '.join(fit_a)}"
          f"{'   <-- pinned' if len(fit_a) == 1 else '   (still open)'}")
    print(f"  defence modifier: {', '.join(fit_d)}"
          f"{'   <-- pinned' if len(fit_d) == 1 else '   (still open)'}")

    collapsed = [g for g in groups if len(g) > 1 and any(k in fits for k in g)]
    for g in collapsed:
        others = [k for k in g if k not in fits]
        if not others:
            print(f"  indistinguishable here: "
                  f"{', '.join('/'.join(k) for k in sorted(g))}")

    _null_warning(fit_a[0] if len(fit_a) == 1 else None,
                  fit_d[0] if len(fit_d) == 1 else None, co_atk, co_def)
    if len(fit_a) > 1 or len(fit_d) > 1:
        print("\nSeparating what is left needs a board where those positions "
              "disagree;\ntry --predict.")
    return 0 if len(fit_a) == 1 and len(fit_d) == 1 else 1


def _null_warning(ak, dk, co_atk, co_def):
    """A0/D0 is the answer that cannot be trusted alone."""
    nulls = []
    if ak == "A0" and co_atk != 100:
        nulls.append(f"the counter-attacker carried attack {co_atk} and the "
                     "counter did not move")
    if dk == "D0" and co_def != 100:
        nulls.append(f"the target carried defence {co_def} and the counter did "
                     "not move")
    if not nulls:
        return
    print()
    for s in nulls:
        print(f"  NULL RESULT: {s}.")
    print("  A CO that never reached the damage path looks exactly like this "
          "(A12).\n  Before recording it, point this tool at a companion sweep "
          "on the SAME\n  fixture where a modifier DID move the counter. "
          "Without that witness the\n  finding is 'nothing happened', which is "
          "not the same claim.")


# --------------------------------------------------------------------------
# --predict: does the board separate the hypotheses at all?
# --------------------------------------------------------------------------

def predict(base, opening_lo, opening_hi, def_hp, co_atk, co_def, att_stars,
            att_hp):
    survivors = [def_hp - x for x in range(opening_lo, opening_hi + 1)]
    survivors = [s for s in survivors if s > 0]
    out = {}
    for ak, dk in itertools.product(ATTACK_POS, DEFENCE_POS):
        out[(ak, dk)] = band(base, survivors, co_atk, co_def, att_stars,
                             att_hp, ak, dk)
    return survivors, out


def predict_cli(args):
    units = load("aw1_unit_stats.json")["units"]
    dmg_tbl = load("aw1_damage.json")
    att, dfn = args.attacker, args.defender
    for name in (att, dfn):
        if name not in units:
            print(f"unknown unit {name!r}")
            return 2
    base = max(dmg_tbl["primary"].get(dfn, {}).get(att, 0),
               dmg_tbl["secondary"].get(dfn, {}).get(att, 0))
    if base == 0:
        print(f"{dfn} cannot damage {att}: no counter to predict.")
        return 2

    print(f"counter: {dfn} -> {att}, base {base}, landing on "
          f"{args.attacker_stars} stars, target at {args.attacker_hp} hp\n")
    rows = []
    for label, p1, p2 in args.scenario:
        co_atk = effective(p2, dfn)[0]
        co_def = effective(p1, att)[1]
        a = Attack(att, dfn, args.attacker_hp, args.defender_hp,
                   args.defender_stars,
                   co_attack=effective(p1, att)[0],
                   co_defense=effective(p2, dfn)[1])
        o = resolve(a, verified=True)
        survivors, bands = predict(base, o.min_damage, o.max_damage,
                                   args.defender_hp, co_atk, co_def,
                                   args.attacker_stars, args.attacker_hp)
        rows.append((label, p1, p2, co_atk, co_def, o, survivors, bands))

    for label, p1, p2, co_atk, co_def, o, survivors, bands in rows:
        print(f"{label}  (P1 {co_mod.record(p1).name} / "
              f"P2 {co_mod.record(p2).name})")
        print(f"  opening {o.min_damage}-{o.max_damage} -> survivors "
              f"{survivors[-1]}-{survivors[0]}; counter-attack mod {co_atk}, "
              f"target defence {co_def}")
        seen = {}
        for k, (lo, hi) in bands.items():
            seen.setdefault((lo, hi), []).append("/".join(k))
        for (lo, hi), ks in sorted(seen.items()):
            print(f"    {lo:3d}-{hi:3d}   {', '.join(sorted(ks))}")
        print()

    # The whole point of predicting: does any pair of positions collapse?
    print("separation check")
    for label, p1, p2, co_atk, co_def, o, survivors, bands in rows:
        groups = {}
        for k, v in bands.items():
            groups.setdefault(v, []).append("/".join(k))
        merged = [g for g in groups.values() if len(g) > 1]
        if len(groups) == 1:
            print(f"  {label}: USELESS -- all nine positions predict the same "
                  f"band")
        else:
            print(f"  {label}: {len(groups)} distinct bands")
            for g in merged:
                print(f"      indistinguishable here: {', '.join(sorted(g))}")
    return 0


SCENARIOS = [
    ("1 baseline", 1, 1),
    ("2 attack   ", 1, 2),
    ("3 defence  ", 4, 1),
    ("4 both     ", 4, 2),
]


# --------------------------------------------------------------------------
# --selftest: does the scorer recover a position it was given?
# --------------------------------------------------------------------------

def synth(akind, dkind, p1=4, p2=2, def_stars=2, att_stars=1, att_hp=100,
          def_hp=100, att="Infantry", dfn="Tank"):
    """A sweep JSON as dmg_seedsweep would write it, with the counter column
    generated under one known position. Used only to check the scorer."""
    units = load("aw1_unit_stats.json")["units"]
    dmg_tbl = load("aw1_damage.json")
    base = max(dmg_tbl["primary"].get(dfn, {}).get(att, 0),
               dmg_tbl["secondary"].get(dfn, {}).get(att, 0))
    co_atk = effective(p2, dfn)[0]
    co_def = effective(p1, att)[1]
    cases = []
    for i, lk in enumerate(list(range(0, 10)) * 4):
        a = Attack(att, dfn, att_hp, def_hp, def_stars,
                   co_attack=effective(p1, att)[0],
                   co_defense=effective(p2, dfn)[1], luck_min=lk, luck_max=lk)
        opening = resolve(a, verified=True).min_damage
        survivor = def_hp - opening
        ctr = counter_value(base, survivor, co_atk, co_def, att_stars, att_hp,
                            akind, dkind)
        died = ctr >= att_hp
        cases.append({"seed": i, "damage": opening, "destroyed": False,
                      "attacker_hp_before": att_hp,
                      "counter": min(ctr, att_hp),
                      "attacker_ammo_after": 9, "attacker_destroyed": died,
                      "attacker_terrain_before": 1, "attacker_terrain_after": 1,
                      "attacker_moved_on_confirm": False})
    c0 = {"damage": cases[0]["damage"], "counter": cases[0]["counter"]}
    return {
        "mode": "seed", "co_abilities": 1, "co_written": None, "co_player": 1,
        "co_in_fixture": p1, "co_p1": p1, "co_p2": p2, "co_gate_after": 1,
        "attacker_slot": 7, "defender_slot": 66,
        "attacker_type": units[att]["id"] + 1, "attacker_hp": att_hp,
        "attacker_ammo": 9,
        "defender_type": units[dfn]["id"] + 1, "defender_hp": def_hp,
        "hp_written": {"attacker": None, "defender": None},
        "hp_in_fixture": {"attacker": att_hp, "defender": def_hp},
        "controls": {"none": c0, "seed": c0, "identity": c0, "passed": True},
        "attacker_terrain": 1, "attacker_terrain_after": att_stars and 1 or 1,
        "defender_terrain": 4 if def_stars == 2 else 1,
        "cases": cases,
    }


def selftest(tmpdir):
    """Generate a sweep under each position and check the scorer recovers it.

    The point is to fail HERE, on synthetic data, rather than after an emulator
    run -- a scorer that cannot recover a position it was handed would quietly
    turn a real measurement into the wrong answer.
    """
    tmp = pathlib.Path(tmpdir)
    tmp.mkdir(parents=True, exist_ok=True)
    bad = 0
    for ak, dk in itertools.product(ATTACK_POS, DEFENCE_POS):
        p = tmp / f"synth_{ak}_{dk}.json"
        p.write_text(json.dumps(synth(ak, dk)), encoding="utf-8")
        d = describe_sweep(p)
        s = d["sweep"]
        co_atk = effective(d["co_dfn"], d["dfn"])[0]
        co_def = effective(d["co_att"], d["att"])[1]
        hits, groups = {}, {}
        usable = 0
        for c in s["cases"]:
            if c["attacker_destroyed"]:
                continue
            usable += 1
            surv = s["defender_hp"] - c["damage"]
            for k in itertools.product(ATTACK_POS, DEFENCE_POS):
                v = counter_value(d["base"], surv, co_atk, co_def,
                                  d["att_stars"], s["attacker_hp"], *k)
                hits[k] = hits.get(k, 0) + (v == c["counter"])
                groups.setdefault(k, []).append(v)
        fits = [k for k, v in hits.items() if v == usable]
        same = [k for k in fits if groups[k] == groups[(ak, dk)]]
        ok = (ak, dk) in fits and len(same) == len(fits)
        note = "" if ok else "   <-- SCORER BUG"
        print(f"  {ak}/{dk}: {usable:2d} usable, recovered by "
              f"{', '.join('/'.join(k) for k in sorted(fits)) or 'nothing'}"
              f"{note}")
        bad += not ok
    print(f"\n{'all positions recovered' if not bad else f'{bad} FAILED'}")
    return 1 if bad else 0


def fits_for(path):
    """The positions that reproduce every usable case of one sweep."""
    d = describe_sweep(path)
    s = d["sweep"]
    if d["base"] == 0 or not s.get("cases") or "counter" not in s["cases"][0]:
        return None, 0
    co_atk = effective(d["co_dfn"], d["dfn"])[0]
    co_def = effective(d["co_att"], d["att"])[1]
    hits, n = collections.Counter(), 0
    for c in s["cases"]:
        if c.get("attacker_destroyed"):
            continue
        surv = s["defender_hp"] - c["damage"]
        if surv <= 0:
            continue
        n += 1
        for k in itertools.product(ATTACK_POS, DEFENCE_POS):
            v = counter_value(d["base"], surv, co_atk, co_def, d["att_stars"],
                              s["attacker_hp"], *k)
            hits[k] += (v == c["counter"])
    return {k for k, v in hits.items() if v == n}, n


def intersect(paths):
    """Positions surviving EVERY sweep.

    No one board separates all of them -- a sweep that moves only the attack
    modifier cannot place the defence one, and the board where both move at once
    cannot tell D1 from D3. The answer is the intersection, and stating it that
    way is also what makes it falsifiable: one more sweep either leaves the set
    at one member or empties it.
    """
    surviving, per = None, []
    for p in paths:
        fits, n = fits_for(p)
        if fits is None:
            print(f"!! {pathlib.Path(p).name}: unusable, skipped")
            continue
        per.append((p, fits, n))
        surviving = fits if surviving is None else (surviving & fits)

    print(f"{'sweep':34s} {'cases':>6}  positions reproducing every case")
    for p, fits, n in per:
        print(f"{pathlib.Path(p).name:34s} {n:6d}  "
              f"{', '.join('/'.join(k) for k in sorted(fits)) or 'NONE'}")

    if not surviving:
        print("\nVERDICT: nothing survives all of them. Either one board is not "
              "what it was\ndescribed as, or the counter takes a shape outside "
              "this family.")
        return 1
    print(f"\nsurviving every sweep: "
          f"{', '.join('/'.join(k) for k in sorted(surviving))}")
    if len(surviving) > 1:
        print("\nVERDICT: more than one position survives. These sweeps do not "
              "separate them;\n--predict for a board that does.")
        return 1
    ak, dk = sorted(surviving)[0]
    print(f"\nVERDICT: {ak}/{dk}, uniquely, across {len(per)} sweeps.")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sweep", nargs="*", help="one or more JSONs written by "
                   "dmg_seedsweep; several are intersected")
    p.add_argument("--predict", action="store_true",
                   help="print the bands each position predicts, and whether "
                        "the board separates them, without a sweep")
    p.add_argument("--attacker", default="Infantry")
    p.add_argument("--defender", default="Tank")
    p.add_argument("--attacker-hp", type=int, default=100)
    p.add_argument("--defender-hp", type=int, default=100)
    p.add_argument("--attacker-stars", type=int, default=1,
                   help="cover on the tile the counter LANDS on")
    p.add_argument("--defender-stars", type=int, default=2)
    p.add_argument("--selftest", metavar="DIR", nargs="?", const="_synth",
                   help="score synthetic sweeps generated under each position "
                        "and check each one is recovered")
    return p


if __name__ == "__main__":
    a = build_parser().parse_args()
    if a.selftest:
        sys.exit(selftest(a.selftest))
    if a.predict:
        a.scenario = SCENARIOS
        sys.exit(predict_cli(a))
    if not a.sweep:
        build_parser().print_help()
        sys.exit(2)
    if len(a.sweep) > 1:
        sys.exit(intersect(a.sweep))
    sys.exit(main(a.sweep[0]))

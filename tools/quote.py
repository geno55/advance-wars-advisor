"""Quote a single matchup. The first user-facing output of the advisor.

    python tools/quote.py Tank Infantry --stars 1
    python tools/quote.py MdTank Tank --att-hp 60 --def-hp 80 --stars 2
    python tools/quote.py Tank Infantry --stars 4 --att-co Max --def-co Andy

COs are named or given by id. Modifiers come from the ROM record, indexed by
the ATTACKING unit for attack and the DEFENDING unit for defence. Kanbei and
Sturm are refused rather than approximated: their strength lives in record
header fields the damage path has not been shown to read, so a quote would be
silently low. See engine/co.py.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.damage import (Attack, CounterModifiersUnknown,  # noqa: E402
                           DEFAULT_VARIANT, can_attack, counterattack,
                           display_hp, fights_at_contact, resolve,
                           screen_bars, tables)


def resolve_co(spec):
    """A CO by name or id. Defaults to Andy, who is neutral on every unit."""
    from engine import co as co_mod
    if spec is None:
        return 1                                    # Andy, 100/100 throughout
    if str(spec).isdigit():
        return int(spec)
    want = str(spec).strip().lower()
    import json
    names = json.loads((pathlib.Path(__file__).resolve().parent.parent
                        / "data" / "aw1_co.json").read_text(encoding="utf-8"))["confirmed"]
    hits = sorted(int(k) for k, v in names.items() if v.lower() == want)
    if not hits:
        raise KeyError(f"unknown CO {spec!r}; known: "
                       + ", ".join(sorted(set(names.values()))))
    if len(hits) > 1:
        raise KeyError(f"{spec!r} matches records {hits} -- Sturm has two, "
                       "so give the id you mean")
    return hits[0]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("attacker")
    p.add_argument("defender")
    p.add_argument("--att-hp", type=int, default=100, help="internal 1..100")
    p.add_argument("--def-hp", type=int, default=100, help="internal 1..100")
    p.add_argument("--stars", type=int, default=0, help="defender terrain stars")
    p.add_argument("--att-stars", type=int, default=0, help="attacker terrain stars")
    p.add_argument("--ammo", type=int, default=99, help="attacker's ammo")
    p.add_argument("--def-ammo", type=int, default=99,
                   help="defender's ammo, which decides what it counters with")
    p.add_argument("--variant", default=DEFAULT_VARIANT)
    p.add_argument("--att-co", help="attacking CO, by name or id 0..11")
    p.add_argument("--def-co", help="defending CO, by name or id 0..11")
    p.add_argument("--att-power", action="store_true",
                   help="attacking CO's power is active")
    p.add_argument("--def-power", action="store_true",
                   help="defending CO's power is active")
    a = p.parse_args()

    verified = tables()["provenance"].get("verified_against_emulator", False)
    if not verified:
        print("!! FORMULA NOT YET CALIBRATED against the emulator.")
        print(f"!! Showing variant '{a.variant}' as a hypothesis. Base damage is")
        print("!! ROM-exact; the arithmetic around it is not. See README.md.\n")

    # Kept in scope so the counterattack can look the records up again for the
    # return strike, which reads different fields of them.
    att_id = def_id = None
    if a.att_co or a.def_co:
        from engine import co as co_mod
        try:
            att_id = resolve_co(a.att_co)
            def_id = resolve_co(a.def_co)
            atk = Attack.between(a.attacker, a.defender, att_id, def_id,
                                 attacker_power=a.att_power,
                                 defender_power=a.def_power,
                                 attacker_hp=a.att_hp, defender_hp=a.def_hp,
                                 terrain_stars=a.stars, ammo=a.ammo)
        except co_mod.UnmodelledCO as e:
            print(f"!! {e}")
            return 1
        except KeyError as e:
            print(f"!! {e}")
            return 1
        print(f"COs: {co_mod.record(att_id).name} attacking "
              f"{co_mod.record(def_id).name} "
              f"(attack {atk.co_attack}%, defence {atk.co_defense}%)")
    else:
        atk = Attack(a.attacker, a.defender, a.att_hp, a.def_hp, a.stars,
                     a.ammo, 100, 100)
    out = resolve(atk, a.variant, verified=True)
    if out is None:
        print(f"{a.attacker} cannot attack {a.defender}"
              + (" with 0 ammo" if a.ammo == 0 else ""))
        return

    # Show the player what the SCREEN shows. display_hp() is the combat scaling
    # and is deliberately different -- a unit on 57 shows 6 bars but attacks
    # as 5. Printing the combat value would look like a bug to anyone reading
    # it off the game.
    def bars(hp):
        s = screen_bars(hp)
        c = display_hp(hp)
        return f"{s} bars" + (f", attacks as {c}" if c != s else "")

    print(f"{a.attacker} ({bars(a.att_hp)}) attacks "
          f"{a.defender} ({bars(a.def_hp)}) on {a.stars}-star terrain")
    print(f"  weapon        {out.weapon} (base {out.base})")
    print(f"  damage        {out.min_damage}-{out.max_damage} internal "
          f"({out.min_damage/10:.1f}-{out.max_damage/10:.1f} bars)")
    if not out.variants_agree:
        print(f"                (upper end is an envelope: two formula variants "
              f"survive calibration and differ at the top)")
    print(f"  defender left {screen_bars(out.min_remaining_hp)}-"
          f"{screen_bars(out.max_remaining_hp)} bars")
    if out.guaranteed_kill:
        print("  KILL          guaranteed, even on the worst luck roll")
    elif out.possible_kill:
        print("  kill          possible but NOT guaranteed -- plan for the survivor")
    else:
        print("  kill          no")

    # One call, and the terrain and COs go IN rather than being re-applied to a
    # second resolve afterwards. That re-resolve was dropping the CO modifiers,
    # so Grit, Andy and Max all printed the same counter.
    counter_kw = dict(attacker_stars=a.att_stars, defender_ammo=a.def_ammo)
    if att_id is not None:
        counter_kw.update(attacker_co=att_id, defender_co=def_id,
                          attacker_power=a.att_power,
                          defender_power=a.def_power)
    try:
        back = counterattack(atk, a.variant, verified=True, **counter_kw)
    except CounterModifiersUnknown as e:
        # Refused rather than approximated, the same way engine/co.py refuses
        # Kanbei. The opening quote above is still good; only the counter is not.
        print(f"  counter       REFUSED -- {e}")
        return
    if back is None:
        # Name the reason. "destroyed, or defender cannot return fire" read as a
        # shrug, and printed alongside "kill possible but NOT guaranteed" it was
        # a contradiction. Several of these can hold at once -- an APC is both
        # unarmed and out of range -- so they are ordered most specific first,
        # and every branch is true whenever it is reached.
        if not fights_at_contact(a.attacker):
            why = f"{a.attacker} strikes from range, which draws no counter"
        elif not can_attack(a.defender, a.attacker, a.def_ammo):
            why = (f"{a.defender} has no weapon that damages {a.attacker}"
                   + (" with 0 ammo" if a.def_ammo == 0 else ""))
        elif not fights_at_contact(a.defender):
            why = f"{a.defender} cannot fire at contact range"
        elif out.guaranteed_kill:
            why = "guaranteed kill, no survivor to fire back"
        else:
            why = "the engine declined to model one"     # no path reaches this
        print(f"  counter       none ({why})")
    else:
        print(f"  counter       {back.min_damage}-{back.max_damage} internal back at you"
              + ("  (LETHAL)" if back.guaranteed_kill else ""))
        if back.min_damage != back.max_damage:
            print("                (the counter carries no luck of its own; the "
                  "spread is which")
            print("                 survivor your opening roll leaves)")


if __name__ == "__main__":
    sys.exit(main() or 0)


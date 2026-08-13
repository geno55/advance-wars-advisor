"""Quote a single matchup. The first user-facing output of the advisor.

    python tools/quote.py Tank Infantry --stars 1
    python tools/quote.py MdTank Tank --att-hp 60 --def-hp 80 --stars 2
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.damage import (Attack, DEFAULT_VARIANT, counterattack,  # noqa: E402
                           display_hp, resolve, screen_bars, tables)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("attacker")
    p.add_argument("defender")
    p.add_argument("--att-hp", type=int, default=100, help="internal 1..100")
    p.add_argument("--def-hp", type=int, default=100, help="internal 1..100")
    p.add_argument("--stars", type=int, default=0, help="defender terrain stars")
    p.add_argument("--att-stars", type=int, default=0, help="attacker terrain stars")
    p.add_argument("--ammo", type=int, default=99)
    p.add_argument("--variant", default=DEFAULT_VARIANT)
    a = p.parse_args()

    verified = tables()["provenance"].get("verified_against_emulator", False)
    if not verified:
        print("!! FORMULA NOT YET CALIBRATED against the emulator.")
        print(f"!! Showing variant '{a.variant}' as a hypothesis. Base damage is")
        print("!! ROM-exact; the arithmetic around it is not. See README.md.\n")

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

    back = counterattack(Attack(a.attacker, a.defender, a.att_hp, a.def_hp,
                                a.stars, a.ammo), a.variant, verified=True)
    if back is None:
        print("  counter       none (destroyed, or defender cannot return fire)")
    else:
        # Re-apply the attacker's own terrain to the return strike.
        back = resolve(Attack(a.defender, a.attacker,
                              max(0, a.def_hp - out.max_damage), a.att_hp,
                              a.att_stars), a.variant, verified=True)
        print(f"  counter       {back.min_damage}-{back.max_damage} internal back at you"
              + ("  (LETHAL)" if back.guaranteed_kill else ""))


if __name__ == "__main__":
    main()


"""Joining: two damaged units of one type merging into one (DERIVATION 34).

The merge routine at 0x0802649C, read and then measured to the digit
(tests/fixtures/join_probes.json):

  * who may join whom -- the pair check at 0x08024664: same type byte, same
    army, NEITHER carrying cargo, and the TARGET (the unit already on the
    tile) displaying fewer than 10 bars. The mover's HP is not consulted;
    a full-HP mover may join a damaged target.
  * HP: DISPLAY bars are added -- bars_t + bars_m, capped at 10 -- and the
    survivor's internal HP is written as bars*10. So 45 + 45 internal is
    not 90 but 100: two five-bar units make a perfect one.
  * the excess bars over 10 are refunded as funds at the unit's VALUE per
    bar -- co.unit_value(): cost/10 * header[+0x2C]/100, Kanbei's 120 again
    (3 excess Tank bars: 2100 for anyone, 2520 for Kanbei, both measured).
  * fuel: the mover's POST-MOVE fuel plus the target's, capped at the stats
    max; ammo: the sum, capped at the stats max.
  * capture progress comes from the TARGET (the unit that was standing
    there), the mover's is discarded; the fuel byte's bit 7 is OR-ed in
    from the target.
  * the mover's record survives, acted; the target's type byte is zeroed.

Table data throughout: the pair rule is two record fields and a type-byte
equality, the refund is the CO header, the caps are unit stats -- no branch
on a unit-type name, same rule as everywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:
    from . import co as co_mod
    from . import pathing
except ImportError:
    import co as co_mod
    import pathing


def bars(hp: int) -> int:
    """Display bars, the ROM's own idiom (hp-1)/10+1, 0 for 0."""
    return 0 if hp <= 0 else (hp - 1) // 10 + 1


def can_join(mover, target) -> bool:
    """The pair check at 0x08024664 on two state.Unit records."""
    return (mover.type == target.type
            and mover.player == target.player
            and mover.slot != target.slot
            and not mover.cargo and not target.cargo
            and not mover.carrying and not target.carrying
            and bars(target.hp) < 10)


@dataclass(frozen=True)
class Merge:
    hp_after: int        # bars*10, a multiple of 10
    fuel_after: int
    ammo_after: int
    refund: int          # funds the army receives for bars over 10
    capture_after: int   # carried from the TARGET


def merge(unit_type: str, *, mover_hp: int, target_hp: int,
          mover_fuel_after_move: int, target_fuel: int,
          mover_ammo: int, target_ammo: int, target_capture: int = 0,
          co_id: Optional[int] = None, power: bool = False) -> Merge:
    """0x0802649C replayed. `mover_fuel_after_move` is the mover's fuel once
    the path cost is paid -- the game spends the move (0x0802417C) before
    adding the target's tank (fixture row J1: 30 - 3 + 30 = 57)."""
    total = bars(mover_hp) + bars(target_hp)
    refund = 0
    if total > 10:
        per_bar = (co_mod.unit_value(pathing.unit_stats(unit_type)["cost"],
                                     co_id, power)
                   if co_id is not None
                   else pathing.unit_stats(unit_type)["cost"] // 10)
        refund = per_bar * (total - 10)
        total = 10
    s = pathing.unit_stats(unit_type)
    return Merge(hp_after=total * 10,
                 fuel_after=min(s["max_fuel"],
                                mover_fuel_after_move + target_fuel),
                 ammo_after=min(s["max_ammo"], mover_ammo + target_ammo),
                 refund=refund, capture_after=target_capture)

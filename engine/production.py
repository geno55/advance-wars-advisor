"""Production: buying units at an own factory (DERIVATION 36).

The army's action, not a unit's, and the last mechanic engine/actions.py
had refused. Read off the shop builder (0x0802E818) and the purchase path,
then driven on written factories (tests/fixtures/build_probes.json). All of
it is table data:

  * WHICH FACTORY sells WHAT: the build menu is one 18-entry list in ROM
    (`shop_order` in aw1_unit_stats.json, read from 0x08080D14) filtered by
    the unit's class byte against a mask the factory terrain selects --
    Base 7 (foot|tires|treads), Airport 0x10 (air), Port 0x20 (naval). The
    masks are code constants at 0x0802E7EC..F8; they are carried here with
    that address, and the class bits come from the extractor's decoding of
    stats +0x14. HQ, cities and every other terrain open the map menu, not
    a shop (measured).
  * PRICE: (cost/10 * CO header[+0x2C] / 100) * 10 -- the same unit-value
    multiplier the meter charge and the join refund use; Kanbei pays 120%
    everywhere (Infantry 1200, Fighter 24000, both measured).
  * AFFORDABLE when funds >= price (0x0802E904); exact funds buy (measured
    down to 0).
  * the new unit is created ACTED with full hp, stats-max fuel and ammo,
    zero capture and cargo, in the LOWEST FREE SLOT of the army's block
    (slots base+1 .. base+50 scanned at 0x080240EC) -- so an army holds
    at most 50 units, and no free slot means no unit. The purchase also
    bumps a per-army per-type built counter at army+0x36+type (capped at
    255) and the army's lifetime spend at army+4 (capped 999999).
  * the factory must be the active player's and EMPTY: A on an occupied
    tile selects the unit instead.
"""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from typing import Optional

try:
    from . import co as co_mod
    from . import pathing
except ImportError:
    import co as co_mod
    import pathing

# the extractor's decoding of stats +0x14: 1 foot, 2 tires, 4 treads,
# 16 air, 32 naval
CLASS_BITS = {"foot": 1, "tires": 2, "treads": 4, "air": 16, "naval": 32}
# factory terrain id -> class mask, the dispatch at 0x0802E7A4/0x0802E7EC
FACTORY_MASKS = {14: 7, 10: 0x10, 11: 0x20}
ARMY_SLOTS = 50                         # base+1 .. base+50, 0x080240EC
SLOT_BLOCK = 64                         # armies own 64-aligned slot blocks


@functools.lru_cache(maxsize=None)
def _shop_order() -> tuple:
    d = json.loads((pathing.DATA / "aw1_unit_stats.json")
                   .read_text(encoding="utf-8"))
    return tuple(d["shop_order"])


def is_factory(terrain_id: int) -> bool:
    return terrain_id in FACTORY_MASKS


def shop(terrain_id: int) -> list:
    """Unit names a factory on this terrain sells, in the menu's order."""
    mask = FACTORY_MASKS.get(terrain_id)
    if mask is None:
        return []
    return [n for n in _shop_order()
            if CLASS_BITS.get(pathing.unit_stats(n)["unit_class"], 0) & mask]


def price(unit_type: str, co_id: Optional[int] = None,
          power: bool = False) -> int:
    """(cost/10 * header[+0x2C]/100) * 10, truncating at the division."""
    cost = pathing.unit_stats(unit_type)["cost"]
    if co_id is None:
        return (cost // 10) * 10
    return co_mod.unit_value(cost, co_id, power) * 10


def free_slot(board, player: int) -> Optional[int]:
    """The slot a purchase would land in: the lowest empty one in the
    army's block, or None when all 50 are taken. Slot blocks are 64 wide
    (base = 64 * (player - 1)); passengers keep their slots."""
    base = SLOT_BLOCK * (player - 1)
    used = {u.slot for u in board.units if u.player == player}
    for s in range(base + 1, base + 1 + ARMY_SLOTS):
        if s not in used:
            return s
    return None


@dataclass(frozen=True)
class Offer:
    unit_type: str
    price: int
    affordable: bool


def offers(terrain_id: int, funds: int, co_id: Optional[int] = None,
           power: bool = False) -> list:
    """The shop as the game draws it: every entry, priced, flagged."""
    out = []
    for n in shop(terrain_id):
        p = price(n, co_id, power)
        out.append(Offer(n, p, funds >= p))
    return out

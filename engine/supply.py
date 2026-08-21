"""Supply, property repair and daily fuel burn -- the turn-start rules.

Everything here replays code that was read at a named address and then
measured (DERIVATION 33; the measured rows are
tests/fixtures/supply_probes.json). The tables come from
data/aw1_supply.json, extracted by tools/extract_supply.py from the blocks
the game's own walkers index -- there is not a single branch on a unit-type
NAME in this file, same rule as pathing and threat: who supplies comes from
the supplier table at 0x08282EE5, who is serviced where from the per-terrain
block at stats +0x24, who burns what from the block at +0x38.

THE MEASURED TURN-START ORDER, which callers must not reorder:

    income (0x0802416A)
    daily burn, crashes applied inline   (walker calling 0x08023978;
                                          remover 0x080243D8)
    property repair + resupply           (walker at 0x0802A334)
    APC auto-supply of adjacent units    (walker at 0x0802A8A4)

The order is why a 0-fuel air unit next to an APC is removed rather than
refuelled (fixture row S5-crash-beats-supply), and why `turn_start()` below
applies burn before service.

REPAIR is the routine at 0x08029D9C, replayed exactly: per requested bar it
charges (cost/10) * co_repair_pct / 100 funds (Kanbei 120, everyone else
100 -- the CO header byte +0x2D, the "+09 twin" no code path had been seen
to read before this), adds 10 internal HP capped at 100, and on EVERY exit
snaps internal HP up to the display bar's ceiling: done, broke mid-way, or
already displaying 10 (91..99 becomes an exact free 100). Repair output is
always a multiple of 10. Whether it charges at all is the settings byte
0x03004357 (nonzero = free; the parked VS fixture reads 1, and which setup
option writes it is unread -- callers get `charge` as a parameter).

BURN is stats[+0x38] per terrain (uniform across terrains, asserted at
extraction), with four gates read at 0x08023978 and each measured: a loaded
unit burns nothing; a unit on its OWN no-burn terrain (air on Airport,
naval on Port) skips burn AND the crash check entirely; a dived sub burns
a flat 5 regardless of the table; Eagle's pool byte +0x0A takes 2 off every
air unit's burn, floored at 0. Fuel floors at 0, and an air or naval unit
at 0 after the burn step is removed -- ground units just stop moving.

RESUPPLY (property, APC menu Supply, APC turn-start auto-supply) is always
free and always to the stats maxima. The refill loops increment one point
per iteration until the field EQUALS the max, so a written over-max value
wraps mod 16 (ammo) rather than clamping -- fixture row R11.
"""
from __future__ import annotations

import functools
import json
import pathlib
from dataclasses import dataclass
from typing import Optional

try:
    from . import pathing
except ImportError:
    import pathing

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"


@functools.lru_cache(maxsize=None)
def _supply():
    return json.loads((DATA / "aw1_supply.json").read_text(encoding="utf-8"))


def _u(unit_type: str) -> dict:
    try:
        return _supply()["units"][unit_type]
    except KeyError:
        raise KeyError(f"unknown unit type {unit_type!r}") from None


# --------------------------------------------------------------------------
# who supplies, who is serviced where
# --------------------------------------------------------------------------

def is_supplier(unit_type: str) -> bool:
    """Table 0x08282EE5: True for the APC alone (a vestigial second entry
    sits on the cut unit id 8)."""
    return _u(unit_type)["is_supplier"]


def can_be_supplied(unit_type: str) -> bool:
    """Table 0x08282ECC. True for every real unit, kept as a lookup rather
    than a constant so the model breaks loudly if a future ROM differs."""
    return _u(unit_type)["can_be_supplied"]


def needs_supply(unit_type: str, fuel: int, ammo: int) -> bool:
    """The menu need-check at 0x0802588C: anything under either stats max.
    Gates both the Supply menu item and nothing else -- the turn-start
    walkers refill unconditionally (a no-op when already full)."""
    caps = resupply_caps(unit_type)
    return fuel != caps[0] or ammo != caps[1]


def serviced_at(unit_type: str, terrain_id: int, tile_owner: int,
                player: int) -> bool:
    """Does this tile repair/resupply this unit at turn start? The walker
    at 0x0802A334 requires the owner bits to match the active army and the
    stats block +0x24+terrain to be nonzero."""
    return tile_owner == player and terrain_id in _u(unit_type)["service_terrains"]


def resupply_caps(unit_type: str) -> tuple:
    """(max fuel, max ammo) -- what every refill path fills to."""
    s = pathing.unit_stats(unit_type)
    return s["max_fuel"], s["max_ammo"]


# --------------------------------------------------------------------------
# daily fuel burn, and the crash at zero
# --------------------------------------------------------------------------

def daily_burn(unit_type: str, *, terrain_id: Optional[int] = None,
               tile_owner: Optional[int] = None, player: Optional[int] = None,
               dived: bool = False, loaded: bool = False,
               co_id: Optional[int] = None, power: bool = False) -> int:
    """Fuel this unit loses at its owner's next turn start (0x08023978).

    Terrain enters twice: the burn table is indexed by it (uniform, so the
    id only matters for the own-service-terrain exemption), and an OWN
    Airport/Port skips the whole step. `power` is accepted for symmetry
    with co.py; Eagle's adjustment is identical in both blocks.
    """
    if loaded:
        return 0
    u = _u(unit_type)
    if (tile_owner is not None and player is not None
            and tile_owner == player
            and u["no_burn_on_own_terrain"] == terrain_id):
        return 0
    burn = 5 if dived else u["fuel_per_turn"]
    if co_id is not None:
        adj = _supply()["co_fuel_burn_adjust"].get(str(co_id))
        if adj:
            burn += adj["power" if power else "normal"].get(unit_type, 0)
    return max(0, burn)


def crashes_at_zero(unit_type: str) -> bool:
    """Air and naval die at fuel 0 after the burn step (class test at
    0x08023A92, remover 0x080243D8); ground units survive parked."""
    return pathing.unit_stats(unit_type)["unit_class"] in ("air", "naval")


def exempt_from_crash(unit_type: str, *, terrain_id: Optional[int] = None,
                      tile_owner: Optional[int] = None,
                      player: Optional[int] = None,
                      loaded: bool = False) -> bool:
    """The own-service-terrain and loaded gates return from 0x08023978
    BEFORE the crash check: a 0-fuel copter on an own Airport lives (and is
    then refuelled by the property phase); a loaded unit cannot crash."""
    if loaded:
        return True
    u = _u(unit_type)
    return (tile_owner is not None and player is not None
            and tile_owner == player
            and u["no_burn_on_own_terrain"] == terrain_id)


# --------------------------------------------------------------------------
# the repair routine, replayed exactly
# --------------------------------------------------------------------------

def repair_cost_per_bar(unit_type: str, co_id: Optional[int] = None) -> int:
    """(cost/10 + pool adj) * header[+0x2D] / 100, truncating -- the pool
    adjustment is zero on all 18 referenced entries, the multiplier is 120
    for Kanbei and 100 for everyone else (both asserted at extraction)."""
    cost10 = pathing.unit_stats(unit_type)["cost"] // 10
    pct = 100
    if co_id is not None:
        pct = _supply()["co_repair_cost_pct_by_id"][str(co_id)]
    return cost10 * pct // 100


def _snap(hp: int) -> int:
    """The exit write at 0x08029F20: internal HP up to the bar ceiling."""
    return ((hp - 1) // 10 + 1) * 10


@dataclass(frozen=True)
class Repair:
    hp_after: int      # always a multiple of 10 (or the input 0)
    spent: int         # funds actually charged


def repair(hp: int, unit_type: str, *, funds: Optional[int] = None,
           co_id: Optional[int] = None, charge: bool = True,
           bars: Optional[int] = None) -> Repair:
    """0x08029D9C replayed. `funds=None` with charge=True means "assume
    affordable" (quote the full outcome); pass real funds to get the broke
    behaviour: pay per bar until short, then snap and stop. `bars` defaults
    to the property amount 2 (= 2 + header[+0x2E] + pool[+4], both zero on
    every record)."""
    if hp <= 0:
        return Repair(hp_after=hp, spent=0)
    if bars is None:
        bars = _supply()["repair_bars"]
    per_bar = repair_cost_per_bar(unit_type, co_id)
    spent = 0
    for _ in range(bars):
        if (hp - 1) // 10 == 9:                       # already displaying 10
            return Repair(hp_after=100, spent=spent)  # free exact top-up
        if charge:
            if funds is not None and funds - spent < per_bar:
                return Repair(hp_after=_snap(hp), spent=spent)
            spent += per_bar
        hp = min(100, hp + 10)
    return Repair(hp_after=_snap(hp), spent=spent)


# --------------------------------------------------------------------------
# the whole turn start for one unit, in the measured order
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TurnStart:
    """What the owner's next turn start does to a unit parked on a tile.

    `crashes` True means the unit is removed at the burn step and nothing
    after it applies -- the other fields then describe a unit that no
    longer exists and are left at their post-burn values. `auto_supplied`
    reflects only the flag the caller passed for APC adjacency; the field
    exists so a consumer can see WHY fuel came back."""
    burn: int
    fuel_after: int
    crashes: bool
    serviced: bool
    hp_after: int
    repair_spent: int
    ammo_after: int
    auto_supplied: bool


def turn_start(unit_type: str, *, hp: int, fuel: int, ammo: int,
               terrain_id: int, tile_owner: int, player: int,
               funds: Optional[int] = None, charge: bool = True,
               co_id: Optional[int] = None, power: bool = False,
               dived: bool = False, loaded: bool = False,
               apc_adjacent: bool = False) -> TurnStart:
    """Burn -> crash -> property service -> auto-supply, the measured order.

    `apc_adjacent` is the caller's statement that an own supplier stands on
    one of the four neighbouring tiles at turn start (the walker reads the
    tile index, so only really-placed units count -- position writes are
    invisible to it)."""
    burn = daily_burn(unit_type, terrain_id=terrain_id, tile_owner=tile_owner,
                      player=player, dived=dived, loaded=loaded,
                      co_id=co_id, power=power)
    fuel_after = max(0, fuel - burn)
    if (fuel_after == 0 and crashes_at_zero(unit_type)
            and not exempt_from_crash(unit_type, terrain_id=terrain_id,
                                      tile_owner=tile_owner, player=player,
                                      loaded=loaded)):
        return TurnStart(burn=burn, fuel_after=0, crashes=True,
                         serviced=False, hp_after=hp, repair_spent=0,
                         ammo_after=ammo, auto_supplied=False)

    max_fuel, max_ammo = resupply_caps(unit_type)
    hp_after, spent = hp, 0
    serviced = (not loaded and serviced_at(unit_type, terrain_id, tile_owner,
                                           player))
    if serviced:
        fuel_after, ammo = max_fuel, max_ammo
        r = repair(hp, unit_type, funds=funds, co_id=co_id, charge=charge)
        hp_after, spent = r.hp_after, r.spent

    supplied = apc_adjacent and not loaded and can_be_supplied(unit_type)
    if supplied:
        fuel_after, ammo = max_fuel, max_ammo
    return TurnStart(burn=burn, fuel_after=fuel_after, crashes=False,
                     serviced=serviced, hp_after=hp_after, repair_spent=spent,
                     ammo_after=ammo, auto_supplied=supplied)

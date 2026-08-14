"""Fog of war: what a player can legally see.

This module exists because the state reader reports TRUE board state. Under fog
that is more information than the player has, and an advisor built on it would
quietly answer questions using units you cannot see. That is not a missing
feature, it is the confidently-wrong failure this project is organised against,
so it is modelled rather than ignored.

TWO WAYS TO BE WRONG, PULLING IN OPPOSITE DIRECTIONS

  * See too much and the advisor cheats: it warns you about an ambush you had
    no way of knowing about, and you learn to trust a number that will not be
    there in a real game.
  * See too little and the advisor is blind: it calls a tile safe because it
    cannot see the tank parked next to it.

There is no setting of one dial that is safe in both directions, so a fogged
answer is deliberately split in two: what is KNOWN from visible units, and an
explicit count of the unlit tiles an unseen attacker could be sitting in. A
threat report under fog that does not carry the second half is lying by
omission.

WHAT IS ESTABLISHED

`vision` is a per-unit ROM field at record +0x0E, extracted with the rest of
the stats table and 152 structural assertions. The values are strongly
self-consistent with it being a sight radius -- Recon, Missiles and Sub at 5,
the artillery family at 1 -- which is why the radius rule below is treated as
near-certain while everything layered on it is not.

WHAT IS ASSUMED, and none of it has been put in front of the game

Every rule below is a named entry in `RULES` with its own kill condition, so a
disagreement can be traced to one switch instead of to "the fog model". The
defaults are chosen for the CHEATING direction rather than the blind one: where
a rule would let the advisor see more, it is off until measured.

  radius          A tile is lit if some unit of yours is within `vision`
                  Manhattan steps. Kill: stand a Recon alone on an empty map
                  and count the lit ring; 5 steps means Manhattan, a 5x5 box
                  means Chebyshev.
  hiding_terrain  Units standing in Wood or Reef are invisible unless a viewer
                  is directly adjacent. Kill: park an infantry in woods three
                  tiles from a Recon and see whether it renders. ON by default
                  because leaving it off makes the advisor see more.
  property_vision An owned property lights its own tile even with nobody on it.
                  Kill: own a city far from any unit and see if it is lit. OFF
                  by default, which under-reports sight rather than over.
  mountain_bonus  Units on a mountain see further. This is documented behaviour
                  in later games in the series and may not be in this one at
                  all. Kill: same Recon, on a mountain, count the ring. OFF.

Because `Board.fog` is None until the flag is found in RAM (see
tools/fog_hunt.py), nothing here fires by accident: callers must say fog is on,
or pass a board that knows it is.
"""
from __future__ import annotations

import functools
from typing import Dict, List, Optional, Set, Tuple

try:                                    # imported as engine.fog by tools/
    from . import pathing
except ImportError:                     # imported as fog, engine/ on path
    import pathing

Coord = Tuple[int, int]

# Terrain that conceals whatever is standing in it. Names rather than ids, so
# this survives a renumbering, and data rather than a branch, so adding Reef
# was not a code change.
CONCEALING = ("wood", "woods", "reef")

RULES: Dict[str, bool] = {
    "radius": True,
    "hiding_terrain": True,
    "property_vision": False,
    "mountain_bonus": False,
}


def rules(**overrides) -> Dict[str, bool]:
    """The default rule set with named overrides applied."""
    unknown = set(overrides) - set(RULES)
    if unknown:
        raise KeyError(f"no such fog rule(s): {sorted(unknown)}")
    out = dict(RULES)
    out.update(overrides)
    return out


@functools.lru_cache(maxsize=None)
def strike_radius() -> int:
    """The furthest any unit could start from a tile and still hit it.

    max(movement + max_range) over the whole roster. Anything outside this of
    you cannot reach you this turn whatever it is, so it bounds how much unlit
    map is actually relevant -- table-driven, so a misread stat shows up here
    rather than as a magic number.
    """
    stats = pathing._stats()
    return max(s["move"] + s["max_range"] for s in stats.values() if s["armed"])


def _viewers(board, player: int) -> List:
    """Units that contribute sight. Passengers do not."""
    return [u for u in board.units if u.player == player and not u.loaded]


def _sight(board, unit, rule_set) -> int:
    st = pathing.unit_stats(unit.type)
    v = st["vision"]
    if rule_set["mountain_bonus"]:
        # Unverified and off by default; kept as one lookup so that turning it
        # on is a one-line experiment rather than a rewrite.
        if board.terrain_name(unit.x, unit.y).lower().startswith("mountain"):
            v += 1
    return v


def visible_tiles(board, player: int, rule_set: Optional[dict] = None) -> Set[Coord]:
    """Every tile this player has line of sight on."""
    rule_set = rule_set or RULES
    lit: Set[Coord] = set()
    if not rule_set["radius"]:
        return {(x, y) for y in range(board.height) for x in range(board.width)}
    for u in _viewers(board, player):
        r = _sight(board, u, rule_set)
        for dx in range(-r, r + 1):
            span = r - abs(dx)
            for dy in range(-span, span + 1):
                x, y = u.x + dx, u.y + dy
                if 0 <= x < board.width and 0 <= y < board.height:
                    lit.add((x, y))
    if rule_set["property_vision"]:
        for x, y, _ in board.properties_of(player):
            lit.add((x, y))
    return lit


def hidden_tiles(board, player: int, rule_set: Optional[dict] = None) -> Set[Coord]:
    """The unlit half of the board -- where an unseen unit could be standing."""
    lit = visible_tiles(board, player, rule_set)
    return {(x, y)
            for y in range(board.height) for x in range(board.width)
            if (x, y) not in lit}


def can_see(board, player: int, unit, rule_set: Optional[dict] = None) -> bool:
    """Whether `player` can see `unit` right now.

    Your own units are always visible, including passengers. Concealing terrain
    hides its occupant from anything that is not directly adjacent, which is a
    stricter test than the tile merely being lit.
    """
    rule_set = rule_set or RULES
    if unit.player == player:
        return True
    if unit.loaded:
        return False                    # inside a transport, not on the board
    here = (unit.x, unit.y)
    if here not in visible_tiles(board, player, rule_set):
        return False
    if rule_set["hiding_terrain"]:
        terrain = board.terrain_name(unit.x, unit.y).lower()
        if terrain in CONCEALING:
            return any(abs(v.x - unit.x) + abs(v.y - unit.y) <= 1
                       for v in _viewers(board, player))
    return True


def visible_units(board, player: int, rule_set: Optional[dict] = None) -> List:
    """Every unit `player` may legally act on knowledge of."""
    rule_set = rule_set or RULES
    return [u for u in board.units if can_see(board, player, u, rule_set)]


def blind_spots(board, player: int, tile: Coord,
                rule_set: Optional[dict] = None) -> Set[Coord]:
    """Unlit tiles close enough to `tile` that something there could reach it.

    This is the honest other half of a fogged threat report: not a prediction,
    a count of the places a prediction cannot see into.
    """
    r = strike_radius()
    hidden = hidden_tiles(board, player, rule_set)
    tx, ty = tile
    return {(x, y) for (x, y) in hidden if abs(x - tx) + abs(y - ty) <= r}


def summarise(board, player: int, rule_set: Optional[dict] = None) -> str:
    rule_set = rule_set or RULES
    lit = visible_tiles(board, player, rule_set)
    total = board.width * board.height
    seen = [u for u in visible_units(board, player, rule_set)
            if u.player != player]
    enemies = [u for u in board.units if u.player != player and not u.loaded]
    off = [k for k, v in sorted(rule_set.items()) if not v]
    out = [f"{len(lit)}/{total} tiles lit, "
           f"{len(seen)}/{len(enemies)} enemy units visible"]
    if len(seen) < len(enemies):
        out.append(f"  {len(enemies) - len(seen)} enemy unit(s) are hidden and "
                   f"absent from every number below")
    if off:
        out.append(f"  rules off: {', '.join(off)}")
    return "\n".join(out)

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

WHAT IS MEASURED

The game keeps its own answer in EWRAM: a byte per tile holding **how many of
your units can see it**, not a boolean. Reading it turned every rule below from
an assumption into a measurement, and `tools/fog_diff.py` reproduces that array
exactly -- 150 tiles out of 150 -- with these rules:

  radius           A unit sees every tile within `vision` MANHATTAN steps.
                   `vision` is the ROM stat at record +0x0E.
  mountain_bonus   A unit standing on a mountain sees +3 further. Not +1, which
                   is what this file guessed while it was guessing.
  property_vision  An owned property lights its OWN TILE and nothing more --
                   radius 0, not a ring.
  hiding_terrain   Wood and Reef are dark unless a viewer is within 1 step, and
                   this applies to the TILE, not merely to units standing in
                   it. That is the correction that mattered: the rule used to
                   live in `can_see` only, so the model lit a wood tile it
                   should not have and would have called it safe ground.

Three of the four defaults were wrong before the measurement -- two rules were
off that should be on, and the mountain bonus was a third of its real value.
The bias toward seeing less was the right instinct and still produced a model
that disagreed with the game on 13 tiles.

WHAT IS STILL NOT MEASURED

  * Whether adjacency actually REVEALS concealing terrain. On the capture that
    settled the rest, no unit stood within 1 of any wood tile, so "visible from
    adjacent" and "never visible" fit the data identically. `can_see` keeps the
    adjacency branch because it is the documented behaviour of the series, but
    it is the one clause here with nothing behind it.
  * Whether the mountain bonus is +3 for every unit class. One Mech on one
    mountain produced it.
  * Sonja's vision trait, and CO powers that reveal the map.
  * Whether the count array's address is stable across maps. It was found at
    0x0201763A on a 15x10 board, with an identical copy at 0x02017B42, and the
    reader does NOT yet read it -- one capture is not an address.

Because `Board.fog` is only known when the reader supplies it, nothing here
fires by accident: callers must say fog is on, or pass a board that knows.
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

# How far a viewer must be to see into concealing terrain, and how much a
# mountain adds. Both measured against the game's own count array.
CONCEAL_RANGE = 1
MOUNTAIN_BONUS = 3

RULES: Dict[str, bool] = {
    "radius": True,
    "hiding_terrain": True,
    "property_vision": True,
    "mountain_bonus": True,
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
        if board.terrain_name(unit.x, unit.y).lower().startswith("mountain"):
            v += MOUNTAIN_BONUS
    return v


def viewer_count(board, player: int,
                 rule_set: Optional[dict] = None) -> Dict[Coord, int]:
    """How many of this player's units can see each tile.

    This is the shape the GAME stores -- a byte per tile, a count and not a
    flag -- so it is the form the oracle can be compared against directly.
    Everything else here is derived from it, which keeps the thing we check
    and the thing we use the same computation.
    """
    rule_set = rule_set or RULES
    counts: Dict[Coord, int] = {(x, y): 0
                                for y in range(board.height)
                                for x in range(board.width)}
    if not rule_set["radius"]:
        return {t: 1 for t in counts}

    conceals = {t for t in counts
                if rule_set["hiding_terrain"]
                and board.terrain_name(*t).lower() in CONCEALING}
    for u in _viewers(board, player):
        r = _sight(board, u, rule_set)
        for dx in range(-r, r + 1):
            span = r - abs(dx)
            for dy in range(-span, span + 1):
                x, y = u.x + dx, u.y + dy
                if not (0 <= x < board.width and 0 <= y < board.height):
                    continue
                # Concealing terrain is dark to anything further than one step,
                # and that is a property of the TILE. Applying it only to units
                # standing there lights ground the game keeps dark.
                if (x, y) in conceals and abs(dx) + abs(dy) > CONCEAL_RANGE:
                    continue
                counts[(x, y)] += 1
    if rule_set["property_vision"]:
        for x, y, _ in board.properties_of(player):
            counts[(x, y)] += 1          # its own tile only, radius 0
    return counts


def visible_tiles(board, player: int, rule_set: Optional[dict] = None) -> Set[Coord]:
    """Every tile this player has line of sight on."""
    return {t for t, n in viewer_count(board, player, rule_set).items() if n}


def hidden_tiles(board, player: int, rule_set: Optional[dict] = None) -> Set[Coord]:
    """The unlit half of the board -- where an unseen unit could be standing."""
    lit = visible_tiles(board, player, rule_set)
    return {(x, y)
            for y in range(board.height) for x in range(board.width)
            if (x, y) not in lit}


def can_see(board, player: int, unit, rule_set: Optional[dict] = None) -> bool:
    """Whether `player` can see `unit` right now.

    Your own units are always visible, including passengers. Everything else
    reduces to whether its tile is lit, because concealing terrain is now
    handled where the game handles it -- on the tile.
    """
    rule_set = rule_set or RULES
    if unit.player == player:
        return True
    if unit.loaded:
        return False                    # inside a transport, not on the board
    return (unit.x, unit.y) in visible_tiles(board, player, rule_set)


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

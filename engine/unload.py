"""Unloading: the Drop command (DERIVATION 35).

Read off the menu predicate and the drop phase, then driven seven ways on the
real game (tests/fixtures/drop_probes.json). The rule has three table parts
and one occupancy check, and every part is data:

  * the transport may unload only while standing on a terrain its cargo
    struct flags at +0x1A+terrain -- `unload_from` in aw1_unit_stats.json
    (APC: plain/wood/road/city/HQ/airport/port/bridge/shoal/base; TCopter
    adds river and mountain; Lander: port and shoal only; Cruiser: sea,
    port, reef, plus a river and a shoal it can never stand on). Read at
    0x080259FC.
  * a tile takes a passenger when it is in bounds, EMPTY in the tile->unit
    index, and the PASSENGER's own stats +0x4C+terrain byte is >= 0 --
    `can_stand`, the passenger's passability, not the transport's. Read at
    0x0802564A; measured with a Tank-typed cargo refusing a mountain the
    Infantry cargo accepted.
  * the Drop item appears only when at least one of the four neighbours
    qualifies (0x0802566C returns a direction mask); no tile, no item.
  * the passenger lands ACTED with its loaded bit cleared, hp/fuel/ammo
    untouched; the transport's cargo slot is cleared, its carrying bit
    dropped, and it is acted -- so one drop per turn, and a two-slot
    transport chooses which passenger (two Drop records, one per slot).

The tile the transport is about to vacate counts as EMPTY: the APC moved one
tile west with sea written on its three other neighbours and dropped its
Infantry straight back onto its origin (fixture row D6). So `drop_tiles`
takes the transport's origin explicitly and ignores the transport's own
record when it asks who is standing where.
"""
from __future__ import annotations

try:
    from . import pathing
except ImportError:
    import pathing


def unload_from(transport_type: str, terrain_id: int) -> bool:
    """May this transport unload while standing on `terrain_id`?"""
    return terrain_id in pathing.unit_stats(transport_type).get("unload_from", ())


def can_stand(unit_type: str, terrain_id: int) -> bool:
    """The drop-tile passability check: stats +0x4C block, >= 0."""
    return terrain_id in pathing.unit_stats(unit_type)["can_stand"]


def drop_tiles(board, transport, transport_tile, passenger_type: str):
    """The neighbours of `transport_tile` the passenger could be dropped on,
    in the game's own order (W, E, N, S -- mask bits 4, 8, 1, 2 at
    0x0802566C): in bounds, nobody but the transport itself standing there,
    and the passenger able to stand on the terrain. `transport` is the
    moving unit's record; its origin tile counts as free (row D6)."""
    tx, ty = transport_tile
    if not unload_from(transport.type, board.terrain[ty][tx]):
        return []
    out = []
    for nx, ny in ((tx - 1, ty), (tx + 1, ty), (tx, ty - 1), (tx, ty + 1)):
        if not (0 <= nx < board.width and 0 <= ny < board.height):
            continue
        there = board.unit_at(nx, ny)
        if there is not None and there.slot != transport.slot:
            continue
        if can_stand(passenger_type, board.terrain[ny][nx]):
            out.append((nx, ny))
    return out

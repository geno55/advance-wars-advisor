"""Movement range and paths. One Dijkstra, every unit type.

There is not a single branch on unit type in this file, and that is the point.
Infantry, Md Tanks, Battleships and Bombers differ only by which row of
`moveCost[moveType][terrain]` they read and how many movement points they have,
both of which are data extracted from the ROM. If a unit-type test ever appears
below, something has been put in code that belongs in a table.

Pure: no emulator, no ROM, no I/O beyond loading the extracted JSON tables. It
takes a Board and returns dictionaries.

WHAT IS MODELLED, and where each rule comes from

  * Terrain cost per movement type, per weather -- aw1_movecost.json, and the
    board's own weather unless one is passed.
  * Movement allowance -- min(movement points, remaining fuel). A unit spends
    one fuel per movement point, so a Tank with 3 fuel moves 3, not 6.
  * Impassable terrain -- cost 255 in the table, exposed as None by Board.
  * Enemy units block passage entirely. You cannot move through them.
  * Friendly units can be moved THROUGH but not stopped on, which is why
    `reachable()` and `destinations()` are different functions and callers
    almost always want the second one. The game itself keeps the same two
    sets apart: its on-screen movement range is the pass-through set, and it
    paints tiles occupied by your own units and then rejects the move if you
    try to end there. `reachable()` has been matched tile-for-tile against
    that grid on a live board -- see docs/DERIVATION.md section 13.
  * Loading: a unit may end its move on a friendly transport whose cargo mask
    admits its type and which has room. Capacity comes from the ROM.

WHAT IS NOT MODELLED, deliberately and visibly

  * Fog of war, and therefore movement being interrupted by discovering a
    hidden unit. The state reader does not report fog.
  * Joining two damaged units of the same type on one tile.
  * A second passenger. The unit record exposes ONE cargo slot at +7, so a
    Lander at capacity 2 that is already carrying something reads as full. See
    `transport_has_room`. This under-reports destinations rather than inventing
    them, which is the safe direction, but it is a real limitation and it comes
    from the reader, not from here.
"""
from __future__ import annotations

import functools
import heapq
import json
import pathlib
from typing import Dict, List, Optional, Tuple

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
Coord = Tuple[int, int]


@functools.lru_cache(maxsize=None)
def _stats():
    """Static ROM data, read once -- see the same note in state.py. Read-only."""
    return json.loads((DATA / "aw1_unit_stats.json").read_text(encoding="utf-8"))["units"]


def unit_stats(unit_type: str) -> dict:
    s = _stats()
    if unit_type not in s:
        raise KeyError(f"no stats for unit type {unit_type!r}; the reader saw a "
                       "type the ROM table does not describe")
    return s[unit_type]


def allowance(unit) -> int:
    """Movement points this unit can actually spend right now.

    Fuel is the binding constraint far more often than people expect -- a
    Bomber with 4 fuel left moves 4, not 7.
    """
    return min(unit_stats(unit.type)["move"], unit.fuel)


def transport_has_room(board, transport, passenger_type: str) -> bool:
    """Whether `transport` can accept a unit of this type right now.

    Capacity and the permitted cargo types are both ROM data. The occupancy
    check is the weak part: the unit record exposes a single cargo slot, so a
    capacity-2 transport that is already carrying one unit is treated as full.
    """
    st = unit_stats(transport.type)
    if not st["capacity"] or passenger_type not in st["carries"]:
        return False
    return transport.cargo == 0


def _occupancy(board) -> Dict[Coord, object]:
    """Tile -> the unit standing on it, ignoring passengers.

    A loaded unit keeps its own record with coordinates tracking its transport,
    so two records legitimately share a tile; counting the passenger would make
    a transport's tile look doubly blocked.
    """
    carried = {u.cargo for u in board.units if u.cargo}
    return {(u.x, u.y): u for u in board.units if u.slot not in carried}


def reachable(board, unit, weather: Optional[str] = None) -> Dict[Coord, int]:
    """Every tile this unit can move THROUGH, mapped to the cost of getting there.

    Includes tiles occupied by friendly units, which can be crossed but not
    stopped on -- use `destinations()` for where the unit may actually end up.
    """
    if unit.loaded:
        return {}
    start = (unit.x, unit.y)
    budget = allowance(unit)
    occupied = _occupancy(board)
    move_type = unit_stats(unit.type)["move_type"]

    best: Dict[Coord, int] = {start: 0}
    queue: List[Tuple[int, Coord]] = [(0, start)]
    while queue:
        cost, (x, y) = heapq.heappop(queue)
        if cost > best.get((x, y), 1 << 30):
            continue
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (0 <= nx < board.width and 0 <= ny < board.height):
                continue
            step = board.move_cost(nx, ny, move_type, weather)
            if step is None:                       # impassable terrain
                continue
            blocker = occupied.get((nx, ny))
            if blocker is not None and blocker.player != unit.player:
                continue                           # enemies block passage
            total = cost + step
            if total > budget:
                continue
            if total < best.get((nx, ny), 1 << 30):
                best[(nx, ny)] = total
                heapq.heappush(queue, (total, (nx, ny)))
    return best


def destinations(board, unit, weather: Optional[str] = None) -> Dict[Coord, int]:
    """Tiles this unit may END its move on, mapped to the cost to get there.

    Its own tile always qualifies -- staying put is a legal move.
    """
    out = {}
    occupied = _occupancy(board)
    for tile, cost in reachable(board, unit, weather).items():
        blocker = occupied.get(tile)
        if blocker is None or blocker.slot == unit.slot:
            out[tile] = cost
        elif (blocker.player == unit.player
              and transport_has_room(board, blocker, unit.type)):
            out[tile] = cost                       # loading into a transport
    return out


def path(board, unit, dest: Coord, weather: Optional[str] = None) -> List[Coord]:
    """A cheapest route from the unit to `dest`, inclusive of both ends.

    Empty if unreachable. Ties are broken by the same order the search expands
    neighbours, so this is one cheapest path rather than a canonical one.
    """
    costs = reachable(board, unit, weather)
    if dest not in costs:
        return []
    move_type = unit_stats(unit.type)["move_type"]
    route = [dest]
    cur = dest
    while cur != (unit.x, unit.y):
        x, y = cur
        step_in = board.move_cost(x, y, move_type, weather)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if costs.get((nx, ny)) == costs[cur] - step_in:
                route.append((nx, ny))
                cur = (nx, ny)
                break
        else:
            return []                              # should be unreachable
    route.reverse()
    return route


def movable_units(board, player: int) -> List:
    """Units of `player` that could still move: not acted, not passengers."""
    return [u for u in board.units
            if u.player == player and not u.acted and not u.loaded]

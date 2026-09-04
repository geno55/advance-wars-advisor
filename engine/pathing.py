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
    hidden unit. Measured in DERIVATION 38: the game's grid marks a hidden
    enemy's tile reachable but never expands through it, and confirming
    onto it stops the mover one tile short with the action spent --
    `trap_tiles()` below; actions.py offers those tiles as "trap".
  * Joining two damaged units of the same type on one tile -- that is an
    ACTION, enumerated by engine/actions.py from reachable() and the pair rule
    (DERIVATION 34); destinations() keeps refusing the friendly's tile.
  * (Closed in DERIVATION 35: the second cargo slot is record +8, the reader
    dumps it as `cargo2`, and `transport_has_room` counts both.)
"""
from __future__ import annotations

import functools
import dataclasses
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


def allowance(unit, board=None) -> int:
    """Movement points this unit can actually spend right now.

    Fuel is the binding constraint far more often than people expect -- a
    Bomber with 4 fuel left moves 4, not 7. With `board`, the owner's CO
    adds its per-unit move adjustment (co.move_bonus: Sami's transports,
    Drake's navy, Max's direct units under power, Sami's foot under power)
    before the fuel cap, as the game's move-budget reader 0x0801D968 does.
    """
    move = unit_stats(unit.type)["move"]
    if board is not None:
        try:
            army = board.army(unit.player)
        except (StopIteration, AttributeError):
            army = None
        if army is not None and army.co_id is not None:
            try:                            # co imports nothing of ours, but
                from . import co as co_mod  # pathing is imported everywhere
            except ImportError:
                import co as co_mod
            move += co_mod.move_bonus(army.co_id, unit.type,
                                      bool(army.power_active))
    return min(move, unit.fuel)


def transport_has_room(board, transport, passenger_type: str) -> bool:
    """Whether `transport` can accept a unit of this type right now.

    Capacity and the permitted cargo types are both ROM data, and so are the
    two cargo slots: record +7 and +8 (DERIVATION 35), so a capacity-2
    transport with one passenger still has room.
    """
    st = unit_stats(transport.type)
    if not st["capacity"] or passenger_type not in st["carries"]:
        return False
    aboard = sum(1 for c in (transport.cargo, getattr(transport, "cargo2", 0))
                 if c)
    return aboard < st["capacity"]


def _occupancy(board) -> Dict[Coord, object]:
    """Tile -> the unit standing on it, ignoring passengers.

    A loaded unit keeps its own record with coordinates tracking its transport,
    so two records legitimately share a tile; counting the passenger would make
    a transport's tile look doubly blocked.
    """
    carried = {c for u in board.units
               for c in (u.cargo, getattr(u, "cargo2", 0)) if c}
    return {(u.x, u.y): u for u in board.units if u.slot not in carried}


def reachable(board, unit, weather: Optional[str] = None) -> Dict[Coord, int]:
    """Every tile this unit can move THROUGH, mapped to the cost of getting there.

    Includes tiles occupied by friendly units, which can be crossed but not
    stopped on -- use `destinations()` for where the unit may actually end up.
    """
    if unit.loaded:
        return {}
    start = (unit.x, unit.y)
    budget = allowance(unit, board)
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


def trap_tiles(board, unit, hidden_slots, weather: Optional[str] = None) -> Dict:
    """Hidden-enemy tiles the game's move grid offers this unit, mapped to
    (stop_tile, cost_paid) -- the fog ambush, DERIVATION 38.

    The game's fill marks a HIDDEN enemy's tile reachable at its honest path
    cost and never expands out of it (measured on the game's own grid: the
    tile 3, the tile beyond it 255 while the Mech was unlit; 255 itself
    when lit). Confirming onto it moves the unit to the tile before it,
    charges fuel for the tiles travelled, and spends the action.

    `hidden_slots` is the caller's statement of which enemies the player
    cannot see (fog.visible_units decides). The stop tile is the cheapest
    reachable approach; the game walks the arrow the player drew, so when
    two approaches tie the real stop may differ -- a stated caveat.
    """
    reach = reachable(board, unit, weather)
    st = unit_stats(unit.type)
    budget = allowance(unit, board)
    out = {}
    for enemy in board.units:
        if enemy.slot not in hidden_slots or enemy.loaded:
            continue
        h = (enemy.x, enemy.y)
        if h in reach:
            continue                           # cannot happen: enemies block
        step = board.move_cost(h[0], h[1], st["move_type"], weather)
        if step is None:
            continue
        best = None
        for n in ((h[0] - 1, h[1]), (h[0] + 1, h[1]), (h[0], h[1] - 1),
                  (h[0], h[1] + 1)):
            if n in reach and reach[n] + step <= budget:
                if best is None or (reach[n], n) < (reach[best], best):
                    best = n
        if best is not None:
            out[h] = (best, reach[best])
    return out


def conceal_traps(board, unit, concealed_slots,
                  weather: Optional[str] = None) -> Dict:
    """Tiles the game's move grid offers this unit only by way of a SUBMERGED
    sub it is not shown, mapped to (stop_tile, cost_paid, sub_tile) -- the
    clear-weather ambush, DERIVATION 41.

    Measured on the game's own grid: a concealed sub's tile reads as open
    water -- entered at its cost and EXPANDED through, unlike the fog rule
    of trap_tiles() -- and confirming onto it or onto anything beyond it
    stops the mover on the tile before the sub, charges fuel for the tiles
    walked, and spends the action with no menu. So the game offers every
    tile of the fill with the sub removed; the ones our own fill (which
    treats the sub as the blocker it really is) does not reach at that cost
    are traps, and the stop is where the cheapest route first meets a sub.

    Stated caveats: the game walks the arrow the player drew, and the
    player cannot see the sub, so a tile reachable at EQUAL cost by a route
    that avoids the sub is listed as an ordinary destination here although
    the game's own arrow may run through the sub; and a tile our fill
    reaches only at a HIGHER cost is a trap here (the auto-route takes the
    cheaper way, through the sub) although a hand-drawn detour would arrive.
    `concealed_slots` is the caller's statement (fog.concealed_units).
    """
    subs = {(u.x, u.y) for u in board.units
            if u.slot in concealed_slots and not u.loaded}
    if not subs or unit.loaded:
        return {}
    open_board = dataclasses.replace(
        board, units=[u for u in board.units if u.slot not in concealed_slots])
    full = reachable(open_board, unit, weather)
    real = reachable(board, unit, weather)
    occupied = _occupancy(open_board)
    out = {}
    for tile, cost in full.items():
        if tile in real and real[tile] <= cost:
            continue                               # honest route, same price
        blocker = occupied.get(tile)
        if blocker is not None and blocker.slot != unit.slot and not (
                blocker.player == unit.player
                and transport_has_room(open_board, blocker, unit.type)):
            continue                               # not a place the game offers
        route = path(open_board, unit, tile, weather)
        for i, t in enumerate(route):
            if t in subs:
                stop = route[i - 1]
                out[tile] = (stop, full[stop], t)
                break
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

"""Board state read out of a live game, plus the lookups an advisor needs.

Consumes the JSON emitted by harness/mgba_state.lua and joins it to the
ROM-derived tables: terrain defence stars, movement costs per weather, and the
damage model.

Nothing here guesses. If a terrain id or unit type is unknown it says so rather
than substituting a default -- a silently wrong terrain would produce confident
bad advice, which is the failure mode this whole project exists to avoid.
"""
from __future__ import annotations

import functools
import json
import pathlib
from dataclasses import dataclass, field
from typing import Optional

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
IMPASSABLE = 255


@functools.lru_cache(maxsize=None)
def _load(name):
    """The extracted tables, read once.

    These are static ROM data and every lookup below re-reads them -- terrain
    names, defence and movement cost are all called from inside a Dijkstra, so
    an uncached read here is a JSON parse per tile expansion. Threat projection
    runs a search per enemy per candidate tile and made that cost visible.

    Callers must treat the result as read-only; it is now shared.
    """
    return json.loads((DATA / name).read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Unit:
    slot: int
    player: int
    type: str
    x: int
    y: int
    hp: int          # internal 1..100
    ammo: int
    capture: int     # capture progress, 0..20; a property falls at 20
    fuel: int
    acted: bool
    carrying: bool
    loaded: bool     # sitting inside a transport
    state: int       # raw +1 byte, kept so unrecognised bits stay visible
    cargo: int       # slot of the carried unit, 0 = empty

    @property
    def bars(self) -> int:
        """What the screen shows. Combat scaling is different -- see damage.py."""
        return -(-self.hp // 10)

    @property
    def capture_turns_left(self) -> Optional[int]:
        """Turns to finish, at this unit's current HP. Capture rate is the
        DISPLAYED bar count, so a damaged unit captures slower."""
        if self.capture == 0:
            return None
        remaining = 20 - self.capture
        return -(-remaining // max(1, self.bars))


@dataclass(frozen=True)
class Army:
    player: int
    funds: int
    income: int
    # CO power charge, cumulative from 0. Both the dealer and the receiver of
    # damage gain. The activation threshold and the gain formula are UNKNOWN, so
    # this is a raw number, not a percentage -- do not render it as one.
    power: int = 0
    # CO identity, 0..11, from army +0x1D. None means the dump predates the
    # field, NOT that the army has no CO -- callers must treat the two
    # differently, because assuming a neutral CO where one is unknown is how a
    # prediction ends up 50% low against Max.
    co_id: Optional[int] = None


@dataclass
class Board:
    width: int
    height: int
    units: list
    armies: list
    terrain: list          # [y][x] -> terrain id
    owner: list            # [y][x] -> owning player, 0 neutral
    # Turn block at 0x03004420. day is 1-based and matches the on-screen Day;
    # active is 1-based and comes from a real field, not from the old "only the
    # current player has funds" inference. 0 means the reader did not supply
    # them -- an older state.json, not a game in progress with no active player.
    day: int = 0
    active_player: int = 0
    # Weather as the game stores it: an INDEX selecting one of the three
    # movement-cost tables, from 0x0300433C. None means the reader did not
    # supply it. The index is ROM-confirmed; the Clear/Snow/Rain names attached
    # to those tables are still inferred, so `weather_index` is the value to
    # trust and `weather` is a convenience over a label that may yet move.
    weather_index: Optional[int] = None
    # Fog of war. None means UNKNOWN, not off -- the flag's RAM address has not
    # been found yet (tools/fog_hunt.py is the way in), so the reader cannot
    # currently tell a clear board from a fogged one. Anything that would give
    # different advice under fog has to treat None as "ask" rather than "no",
    # because defaulting to off is exactly how the advisor would end up quoting
    # units the player cannot see. See engine/fog.py.
    fog: Optional[bool] = None
    warnings: list = field(default_factory=list)

    @property
    def weather(self) -> Optional[str]:
        if self.weather_index is None:
            return None
        tables = _load("aw1_movecost.json")["tables"]
        if not 0 <= self.weather_index < len(tables):
            return None
        return tables[self.weather_index]["weather"]

    # -- lookups ----------------------------------------------------------
    def terrain_name(self, x: int, y: int) -> str:
        tid = self.terrain[y][x]
        name = _load("aw1_terrain_ids.json")["ids"].get(str(tid))
        if name is None:
            raise KeyError(f"unknown terrain id {tid} at ({x},{y})")
        return name

    def defence(self, x: int, y: int) -> int:
        """Terrain defence stars, from the in-game Def display."""
        name = self.terrain_name(x, y).lower()
        stars = _load("aw1_terrain.json")["stars"]
        if name not in stars:
            raise KeyError(f"no defence value recorded for {name!r}; read it "
                           "off the in-game Def display and add it")
        return stars[name]

    def defence_for(self, x: int, y: int, move_type: str) -> int:
        """Defence stars the game applies to a unit of this move type here.

        Air units get no cover from the ground below them. The game does this
        by giving them terrain id 9 -- the sky, defence 0 -- rather than by
        special-casing air in the damage code, so this stays a table lookup
        driven by move type. Reading defence(x, y) for a Fighter parked over a
        mountain would hand it 4 stars and silently wreck every prediction
        involving aircraft.
        """
        t = _load("aw1_terrain.json")
        if move_type == "Air":
            return t["terrain"][str(t["sky_id"])]["stars"]
        return self.defence(x, y)

    def move_cost(self, x: int, y: int, move_type: str,
                  weather: Optional[str] = None) -> Optional[int]:
        """Movement cost, or None if impassable.

        Defaults to the board's own weather when the dump supplied one, so
        callers stop having to know it. Pass `weather` explicitly to ask a
        hypothetical ("what would this cost in snow?").
        """
        mc = _load("aw1_movecost.json")
        if weather is None:
            if self.weather_index is not None:
                table = mc["tables"][self.weather_index]
            else:
                table = mc["tables"][0]
        else:
            table = next(t for t in mc["tables"] if t["weather"] == weather)
        cost = table["costs"][move_type][self.terrain[y][x]]
        return None if cost == IMPASSABLE else cost

    def unit_at(self, x: int, y: int) -> Optional[Unit]:
        """The unit occupying a tile, ignoring anything being carried there.

        A loaded unit keeps its own record with coordinates tracking its
        transport, so two records legitimately share a tile. Returning the
        passenger would make a transport look like an infantryman.
        """
        carried = {u.cargo for u in self.units if u.cargo}
        return next((u for u in self.units
                     if u.x == x and u.y == y and u.slot not in carried), None)

    def cargo_of(self, unit: Unit) -> Optional[Unit]:
        if not unit.cargo:
            return None
        return next((u for u in self.units if u.slot == unit.cargo), None)

    def units_of(self, player: int) -> list:
        return [u for u in self.units if u.player == player]

    def army(self, player: int) -> Army:
        return next(a for a in self.armies if a.player == player)

    def properties_of(self, player: int) -> list:
        """Tiles this player owns, as (x, y, terrain name)."""
        out = []
        for y in range(self.height):
            for x in range(self.width):
                if self.owner[y][x] == player:
                    out.append((x, y, self.terrain_name(x, y)))
        return out


def load(path) -> Board:
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    rows = sorted(raw["terrain"], key=lambda r: r["y"])
    board = Board(
        width=raw["width"], height=raw["height"],
        units=[Unit(u["slot"], u["player"], u["type"], u["x"], u["y"],
                    u["hp"], u["ammo"], u.get("capture", 0), u["fuel"],
                    u.get("acted", False), u.get("carrying", False),
                    u.get("loaded", False),
                    u.get("state", 0), u.get("cargo", 0))
               for u in raw["units"]],
        armies=[Army(a["player"], a["funds"], a["income"], a.get("power", 0),
                     a.get("co_id"))
                for a in raw["armies"]],
        terrain=[r["t"] for r in rows],
        owner=[r["owner"] for r in rows],
        day=raw.get("day", 0),
        active_player=raw.get("active_player", 0),
        weather_index=raw.get("weather_index"),
        fog=raw.get("fog"),
    )
    chk = raw.get("check", {})
    if "day" not in raw:
        board.warnings.append(
            "no turn block in this dump -- reader predates it; day and active "
            "player are unknown rather than zero")
    if chk.get("turn_block_sane") is False:
        board.warnings.append(
            "the turn block failed its sanity check in the reader; day and "
            "active player are not trustworthy for this dump")
    if chk.get("funds_heuristic_agrees") is False:
        board.warnings.append(
            "the active-player field disagrees with which army holds funds -- "
            "one of the two is wrong, see aw1_army_struct.json")

    # The property list is the strongest check we have on map DIMENSIONS.
    # mapdims() derives the height by walking the movement row-pointer table,
    # which runs past the end of a small map into a buffer sized for a larger
    # one -- so the terrain array gets read too far and the extra rows are the
    # PREVIOUS map's data. That looks entirely plausible: coherent terrain,
    # every unit still standing somewhere legal. What gives it away is that the
    # stale rows contain properties the game does not list.
    # Width has two independent sources -- the map descriptor byte and the
    # row-pointer stride. Agreement is real corroboration; disagreement means
    # one of them is not what we think it is.
    dc = raw.get("dims_check", {})
    if dc and not dc.get("width_sources_agree", True):
        board.warnings.append(
            f"width {board.width} from the map descriptor disagrees with the "
            f"row-pointer stride {dc.get('stride_width')} -- one of the two "
            f"addresses is wrong for this build; do not trust this board")

    listed = {(p["x"], p["y"]) for p in raw.get("properties", [])}
    if listed:
        capturable = set(_load("aw1_terrain.json")["capturable_ids"])
        seen = {(x, y)
                for y in range(board.height) for x in range(board.width)
                if board.terrain[y][x] in capturable}
        phantom = sorted(seen - listed)
        missing = sorted(listed - seen)
        if phantom:
            rows_hit = sorted({y for _, y in phantom})
            board.warnings.append(
                f"{len(phantom)} propert(ies) in the terrain array are absent "
                f"from the game's own list, first at {phantom[0]} -- the map is "
                f"almost certainly SHORTER than {board.height} rows and "
                f"everything from row {rows_hit[0]} down is stale data from a "
                f"previously loaded map")
        if missing:
            board.warnings.append(
                f"the game lists {len(missing)} propert(ies) the terrain array "
                f"does not show, first at {missing[0]} -- the map address or "
                f"width is wrong")
    if chk.get("units_on_impossible_terrain"):
        board.warnings.append(
            f"{chk['units_on_impossible_terrain']} unit(s) stand on terrain they "
            "cannot occupy -- the map address is probably wrong for this build")
    if chk.get("unknown_terrain_ids"):
        board.warnings.append(
            f"unknown terrain ids present: {chk['unknown_terrain_ids']}")
    if any(u.type.startswith("id") for u in board.units):
        board.warnings.append("unrecognised unit type id in the unit array")
    return board


def summarise(b: Board) -> str:
    head = f"{b.width}x{b.height} board"
    if b.day:
        head += f", day {b.day}, P{b.active_player} to move"
    if b.weather_index is not None:
        head += f", weather {b.weather} (index {b.weather_index})"
    out = [head]
    for w in b.warnings:
        out.append(f"  !! {w}")
    for a in b.armies:
        us = b.units_of(a.player)
        props = b.properties_of(a.player)
        if not us and not props:
            continue
        out.append(f"  P{a.player}: {a.funds} funds (+{a.income}), "
                   f"power {a.power}, {len(us)} units, {len(props)} properties")
        carried = {x.cargo for x in b.units if x.cargo}
        for u in sorted(us, key=lambda u: (u.y, u.x)):
            if u.slot in carried:
                continue        # listed under its transport instead
            terr = b.terrain_name(u.x, u.y)
            extra = "  acted" if u.acted else ""
            if u.capture:
                extra += (f"  capturing {u.capture}/20 "
                          f"({u.capture_turns_left} more turn(s))")
            out.append(f"      {u.type:10s} ({u.x:2d},{u.y:2d}) {u.bars:2d} bars "
                       f"ammo {u.ammo} fuel {u.fuel} on {terr}{extra}")
            c = b.cargo_of(u)
            if c:
                out.append(f"        carrying {c.type} ({c.bars} bars, "
                           f"fuel {c.fuel})")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    b = load(sys.argv[1])
    print(summarise(b))

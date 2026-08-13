"""Board state read out of a live game, plus the lookups an advisor needs.

Consumes the JSON emitted by harness/mgba_state.lua and joins it to the
ROM-derived tables: terrain defence stars, movement costs per weather, and the
damage model.

Nothing here guesses. If a terrain id or unit type is unknown it says so rather
than substituting a default -- a silently wrong terrain would produce confident
bad advice, which is the failure mode this whole project exists to avoid.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Optional

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
IMPASSABLE = 255


def _load(name):
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


@dataclass
class Board:
    width: int
    height: int
    units: list
    armies: list
    terrain: list          # [y][x] -> terrain id
    owner: list            # [y][x] -> owning player, 0 neutral
    warnings: list = field(default_factory=list)

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

    def move_cost(self, x: int, y: int, move_type: str,
                  weather: str = "Clear") -> Optional[int]:
        """Movement cost, or None if impassable."""
        mc = _load("aw1_movecost.json")
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
        armies=[Army(a["player"], a["funds"], a["income"], a.get("power", 0))
                for a in raw["armies"]],
        terrain=[r["t"] for r in rows],
        owner=[r["owner"] for r in rows],
    )
    chk = raw.get("check", {})
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
    out = [f"{b.width}x{b.height} board"]
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

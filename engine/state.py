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
    fuel: int
    acted: bool

    @property
    def bars(self) -> int:
        """What the screen shows. Combat scaling is different -- see damage.py."""
        return -(-self.hp // 10)


@dataclass(frozen=True)
class Army:
    player: int
    funds: int
    income: int


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
        return next((u for u in self.units if u.x == x and u.y == y), None)

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
                    u["hp"], u["ammo"], u["fuel"], u["acted"])
               for u in raw["units"]],
        armies=[Army(a["player"], a["funds"], a["income"]) for a in raw["armies"]],
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
                   f"{len(us)} units, {len(props)} properties")
        for u in sorted(us, key=lambda u: (u.y, u.x)):
            terr = b.terrain_name(u.x, u.y)
            out.append(f"      {u.type:10s} ({u.x:2d},{u.y:2d}) {u.bars:2d} bars "
                       f"ammo {u.ammo} fuel {u.fuel} on {terr}"
                       f"{'  [acted]' if u.acted else ''}")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    b = load(sys.argv[1])
    print(summarise(b))

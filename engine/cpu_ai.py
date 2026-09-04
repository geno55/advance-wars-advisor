"""The CPU's turn, routine by routine from the ROM -- ROADMAP step 3.

This is the game's AI (0x0805E000..0x0806A000, DERIVATION 44/45) ported as
literally as it reads. Every method names the ROM routine it is, keeps its
scan order and its tie rule, and draws from the same RNG the game draws
from, because the AI's battle forecast rolls luck through the live RNG and
the traced command records carry the RNG state they were written at
(tools/cpu_trace.py): a prediction that matches the game matches it draw
for draw, which is how it is checked.

What the AI is, in one paragraph. The turn is nineteen sub-phases
(`subphases` in data/aw1_ai.json), each listing the not-yet-acted units of
one AI class in slot order and handing each to a behaviour: the foot
soldiers' capture pass, the indirects' fire-in-place pass, the direct
units' attack-or-move pass, the foot soldiers' second pass, the transports,
the indirects' move, the landers. A behaviour writes at most one command
record (0x080644D8) -- a Wait whose destination is the unit's own tile is
dropped, so a unit that decides to stay issues nothing. Movement is one
routine, `move_toward` (0x08060078): a cost map from the goal, the unit's
own reach, and the reachable tile closest to the goal that the unit may
stop on and that no enemy threatens. Attacks are `best_attack`
(0x0805F7E0/0x0805F948): every reachable shot forecast with luck, scored
as damage dealt times the target's worth minus damage taken times the
attacker's cost weight, the counter capped by a profile threshold. The
profile (data/aw1_ai.json `profiles`, one per mission row and CO) is where
the personality lives: per unit type a damage threshold, a threat-avoidance
percentage, fuel and hp retreat thresholds.

Per-unit state the game keeps in the unit record's bytes +9..+0xB (the
dumps carry them as `ai`): +9 condition and pickup flags, +0xA the unit's
random for this turn, +0xB its movement mode (0 foot: none; 4 vehicles:
hunt). `predict()` needs them, the sides' team/enemy/HQ bytes and the two
settings bytes, so it takes a `Context` built from a dump
(`Context.from_dump`).

What is ported and checked against the traces is listed at the bottom of
the module; what is not is raised as NotImplementedError with the ROM
address, never guessed silently.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    from . import actions, co as co_mod, damage, pathing, production
    from . import rng as rng_mod, sim
    from .cpu import Command
except ImportError:                     # engine/ on the path
    import actions
    import co as co_mod
    import damage
    import pathing
    import production
    import rng as rng_mod
    import sim
    from cpu import Command

Coord = Tuple[int, int]
DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "aw1_ai.json"
UNREACHABLE = -1
RANGE_MARK = 0x79          # 0x0801D2EC's ring marker: "one step past reach"
WHOLE_MAP = 0x78
NO_TILE = 0x270F
HQ, CITY, PORT, SHOAL, BASE = 8, 6, 11, 13, 14

_tables = None


def tables() -> dict:
    global _tables
    if _tables is None:
        _tables = json.loads(DATA.read_text(encoding="utf-8"))
    return _tables


def type_id(name: str) -> int:
    return tables()["unit_stats"][name]["type"]


def type_name(tid: int) -> str:
    for n, s in tables()["unit_stats"].items():
        if s["type"] == tid:
            return n
    raise KeyError(tid)


def stats(name: str) -> dict:
    return tables()["unit_stats"][name]


def move_type_of(name: str) -> str:
    return pathing.unit_stats(name)["move_type"]


def bars(hp: int) -> int:
    """(hp - 1) / 10 + 1: the 1..10 the forecast scales by (0x080232C8)."""
    return (hp - 1) // 10 + 1 if hp > 0 else 0


def div(a: int, b: int) -> int:
    """BIOS Div / __divsi3: truncation toward zero."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


@dataclass
class Side:
    team: int
    enemies: int            # army +0x28: bit k-1 set = side k is an enemy
    hq: Optional[Coord]     # army +0x29/+0x2A, None when bit 7 is set


@dataclass
class Context:
    """What the AI reads beyond the Board: the per-unit AI bytes, the sides'
    team/enemy/HQ bytes, the mission's profile, the settings bytes."""
    ai: Dict[int, List[int]]            # slot -> [+9, +0xA, +0xB]
    sides: Dict[int, Side]
    profile: dict                       # {"header": [...], "units": {name: [...]}}
    settings_6: int = 0                 # nonzero: the forecast rolls no luck
    settings_8: int = 1                 # nonzero: CO-specific stat blocks
    fog: bool = False
    # The game's own property list, [(terrain id, x, y)] in its y-then-x
    # order (0x03004500): the AI's factory list walks it, not the terrain
    # grid, so a written Base joins the AI's shopping only through it.
    properties: Optional[list] = None
    # Army record 0's bytes +0xC..+0xF, summed (+1) by 0x08068824 as the
    # divisor of the foot-share cap. Zero on every dump so far.
    army0: List[int] = field(default_factory=lambda: [0, 0, 0, 0])
    # 0x030050E4 as the dump read it -- informational only: the AI's
    # state 0 recomputes it from the map (Turn.side_flags).
    flags_e4: int = 0

    @classmethod
    def from_dump(cls, path, player: Optional[int] = None) -> "Context":
        """`player` is the side the CPU will play: its CO picks the
        profile row (0x0806826C reads the ACTIVE army's +0x1D at the
        CPU's own state 0). A dump taken on the human's turn names the
        human as active, so the caller says who is about to move."""
        d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        t = tables()
        ai = {u["slot"]: list(u.get("ai", [0, 0, 0])) for u in d["units"]}
        sides = {}
        for a in d["armies"]:
            hx, hy = a.get("hq", [128, 0])
            sides[a["player"]] = Side(team=a.get("team", a["player"] - 1),
                                      enemies=a.get("enemies", 0),
                                      hq=None if hx & 0x80 else (hx, hy))
        prof = None
        if d.get("ai_profile"):
            raw = bytes.fromhex(d["ai_profile"])
            prof = {"header": list(raw[:16]),
                    "units": {n: list(raw[4 + 12 * s["type"]: 16 + 12 * s["type"]])
                              for n, s in t["unit_stats"].items()}}
        if prof is None or all(b == 0 for b in prof["header"]):
            prof = profile_for(d.get("map_id", 0),
                               {a["player"]: a.get("co_id", 1) for a in d["armies"]},
                               player or d.get("active_player", 1))
        props = d.get("properties")
        return cls(ai=ai, sides=sides, profile=prof,
                   settings_6=d.get("settings_6", 0), settings_8=d.get("settings_8", 1),
                   fog=bool(d.get("fog", False)),
                   properties=[(p["t"], p["x"], p["y"]) for p in props] if props else None,
                   army0=list(d.get("army0", [0, 0, 0, 0])),
                   flags_e4=int(d.get("flags_e4", 0)))


def profile_for(map_id: int, co_ids: Dict[int, int], player: int) -> dict:
    """The profile the AI's state 0 copies (0x0806826C): a VS mission's row
    picks a column of 0x082872C8 by the CO."""
    t = tables()
    m = t["missions"][map_id]
    if not m["vs"]:
        raise NotImplementedError("campaign profiles (0x080683B0) are not read")
    k = t["profile_index"][m["row"]][co_ids[player]]
    return t["profiles"][k]


@dataclass
class Forecast:
    weapon: int             # atk +0x18: 0 none, 1 primary, 5 secondary
    dealt: int              # def +0x12
    taken: int              # atk +0x12
    atk_hp: int             # atk +8 after 0x08023508
    def_hp: int             # def +8


@dataclass
class Turn:
    """One AI turn in flight: the board as the commands so far leave it,
    the RNG, the per-unit bytes, the scratch tables the passes share."""
    board: object
    player: int
    rng: int
    ctx: Context
    commands: List[Command] = field(default_factory=list)
    builds: List[dict] = field(default_factory=list)            # state 4's purchases
    draws: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    targeted: Dict[int, int] = field(default_factory=dict)      # 0x03005160
    counters: Dict[int, List[int]] = field(default_factory=dict)  # 0x08282CC4 +3..
    flags_5008: int = 0
    _side_flags: Optional[int] = None                           # 0x030050E4, computed once
    behaviour: int = 0                                          # 0x030050DC
    subphase: int = 0                                           # 0x030051A0
    threat: Optional[Dict[Coord, int]] = None
    goal_grid: Dict[Coord, int] = field(default_factory=dict)   # +0x2D5A
    prop_grid: Dict[Coord, int] = field(default_factory=dict)   # +0x376A
    log: List[str] = field(default_factory=list)

    # -- RNG ---------------------------------------------------------------
    def draw(self, why: str) -> int:
        self.rng = rng_mod.next_state(self.rng)
        self.draws.append({"rng": self.rng, "why": why})
        return self.rng

    # -- sides -------------------------------------------------------------
    def side(self, p: int) -> Side:
        return self.ctx.sides[p]

    def allied(self, a: int, b: int) -> bool:               # 0x0802544C
        return self.side(a).team == self.side(b).team

    def enemy_sides(self) -> List[int]:
        m = self.side(self.player).enemies
        return [k for k in (1, 2, 3, 4) if m & (1 << (k - 1)) and k in self.ctx.sides]

    def alive(self, p: int) -> bool:                        # 0x08024CD0
        return p in self.ctx.sides and (self.board.units_of(p) or True)

    def owner_team_ok(self, x: int, y: int) -> bool:        # 0x08025484
        o = self.board.owner[y][x]
        return o != 0 and self.allied(self.player, o)

    def capturable(self, x: int, y: int) -> bool:           # 0x08025B68
        if self.owner_team_ok(x, y):
            return False
        return self.board.terrain[y][x] in (6, 8, 10, 11, 14, 17, 18)

    # -- units -------------------------------------------------------------
    def unit_at(self, x: int, y: int):
        return self.board.unit_at(x, y)

    def ai(self, unit) -> List[int]:
        return self.ctx.ai.setdefault(unit.slot, [0, 0, 0])

    def profile_unit(self, unit) -> List[int]:
        return self.ctx.profile["units"][unit.type]

    def co_block(self, player: int, name: str) -> dict:
        a = self.board.army(player)
        cid = a.co_id if self.ctx.settings_8 else 1
        return tables()["cos"][cid]["by_power_state"][str(int(bool(a.power_active)))][name]

    def eff_move(self, unit) -> int:                        # 0x0805F22C
        return min(stats(unit.type)["move"], unit.fuel)

    def move_budget(self, unit) -> int:                     # 0x0801D968
        m = stats(unit.type)["move"] + self.co_block(unit.player, unit.type)["move"]
        return min(unit.fuel, m)

    # -- grids -------------------------------------------------------------
    def fill(self, x: int, y: int, tid: int, budget: int, block: bool,
             side: Optional[int] = None) -> Dict[Coord, int]:
        """0x0801CD00: cost-so-far from (x, y) for a unit of type `tid`,
        within `budget`; with `block`, tiles holding units of a side in
        the moving side's enemy mask (0x03004870, the active player unless
        the threat builder is walking an enemy) are not entered."""
        b = self.board
        mt = move_type_of(type_name(tid))
        enemies = self.side(self.player if side is None else side).enemies
        best = {(x, y): 0}
        frontier = [(0, x, y)]
        import heapq
        while frontier:
            c, cx, cy = heapq.heappop(frontier)
            if c > best.get((cx, cy), 1 << 30):
                continue
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if not (0 <= nx < b.width and 0 <= ny < b.height):
                    continue
                step = b.move_cost(nx, ny, mt)
                if step is None:
                    continue
                n = c + step
                if n >= best.get((nx, ny), 1 << 30) or n > budget:
                    continue
                if block:
                    u = b.unit_at(nx, ny)
                    if u is not None and enemies & (1 << (u.player - 1)):
                        continue
                best[(nx, ny)] = n
                heapq.heappush(frontier, (n, nx, ny))
        return best

    def expand(self, grid: Dict[Coord, int], mark: int = RANGE_MARK) -> None:
        """0x0801D2EC: every unreached neighbour of a reached tile gets `mark`."""
        b = self.board
        for (x, y) in [t for t, v in grid.items() if v != mark]:
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < b.width and 0 <= ny < b.height and (nx, ny) not in grid:
                    grid[(nx, ny)] = mark

    def ring(self, x: int, y: int, lo: int, hi: int) -> Dict[Coord, int]:
        """0x0801DAE0: the tiles at distance lo..hi, valued 0."""
        b = self.board
        out = {}
        for ty in range(b.height):
            for tx in range(b.width):
                d = abs(tx - x) + abs(ty - y)
                if lo <= d <= hi:
                    out[(tx, ty)] = 0
        return out

    def own_reach(self, unit) -> Dict[Coord, int]:
        return self.fill(unit.x, unit.y, type_id(unit.type), self.move_budget(unit), True)

    def scan(self):
        """Every tile in the AI's scan order: rows outer, columns inner."""
        for y in range(self.board.height):
            for x in range(self.board.width):
                yield x, y

    # -- the threat grid (0x08068E78 / 0x08068F68) -------------------------
    def build_threat(self, unit) -> Dict[Coord, int]:
        if self.flags_5008 & 1:
            return self.threat
        self.flags_5008 |= 1
        grid: Dict[Coord, int] = {}
        mask = stats(unit.type)["hit_by"]
        for k in self.enemy_sides():
            for e in self.board.units_of(k):
                if e.loaded:
                    continue
                es = stats(e.type)
                if not (es["hits"] & mask):
                    continue
                if self.ai(e)[0] & 0 or (e.state & 8):
                    continue
                if not self.within_reach(unit, e):
                    continue
                if e.type == "Sub" and e.dived:
                    pass                                     # 0x08023DD0 unread
                if es["max_range"] == 1:
                    g = self.fill(e.x, e.y, type_id(e.type), es["move"], True, side=k)
                    self.expand(g)
                else:
                    g = self.fill(e.x, e.y, 16, es["max_range"], False)
                for t in g:
                    grid[t] = grid.get(t, 0) | es["hits"]
        self.threat = grid
        return grid

    def within_reach(self, a, b) -> bool:                   # 0x08069134
        d = abs(a.x - b.x) + abs(a.y - b.y)
        if stats(b.type)["max_range"] == 1:
            return d <= self.eff_move(a) + self.eff_move(b) + 1
        return d <= self.eff_move(a) + stats(b.type)["max_range"]

    def threatened(self, unit, x: int, y: int) -> bool:
        if self.threat is None:
            return False
        return bool(self.threat.get((x, y), 0) & stats(unit.type)["hit_by"])

    # -- the forecast (0x08023550) -----------------------------------------
    def weapon(self, atk, dfn, dist: int, attacking: bool) -> Tuple[int, int]:
        """0x08022BFC: (base * CO attack / 100 * weapon multiplier / 100,
        weapon flag). The defender only answers at contact."""
        if not attacking and dist != 1:
            return 0, 0
        s = stats(atk.type)
        cb = self.co_block(atk.player, atk.type)
        t = damage.tables()
        prim = t["primary"].get(atk.type, {}).get(dfn.type, 0)
        sec = t["secondary"].get(atk.type, {}).get(dfn.type, 0)
        if dist == 1:
            if s["min_range"] != 1 or atk.ammo == 0:
                prim = 0
        else:
            hi = s["max_range"] + (cb["range"] if s["max_range"] > 1 else 0)
            if not (s["min_range"] <= dist <= hi) or atk.ammo == 0:
                return 0, 0
            sec = 0
        a_army, d_army = self.board.army(atk.player), self.board.army(dfn.player)
        a_pow, d_pow = bool(a_army.power_active), bool(d_army.power_active)
        atk_pct = co_mod.modifiers(a_army.co_id, atk.type, a_pow)[0]
        atk_uni = co_mod.universal(a_army.co_id, a_pow)[0]
        def_pct = co_mod.modifiers(d_army.co_id, dfn.type, d_pow)[1]
        def_uni = co_mod.universal(d_army.co_id, d_pow)[1]
        dfac = div(def_pct * def_uni, 100)

        def value(base):
            v = div(div(base * atk_pct, 100) * atk_uni, 100)
            return div(v * dfac, 100)
        p = value(prim) if prim else 0
        q = value(sec) if sec else 0
        if p > q:
            return p, 1
        if sec:
            return q, 5
        return 0, 0

    def defence_value(self, unit, x: int, y: int) -> int:  # 0x08022A58
        if stats(unit.type)["move_class"] == 0x10:
            return 0
        v = self.board.defence(x, y) * 10
        return 50 if v > 49 else v

    def forecast(self, atk, ax: int, ay: int, dfn) -> Forecast:
        dist = abs(ax - dfn.x) + abs(ay - dfn.y)
        a10, aw = self.weapon(atk, dfn, dist, True)
        d10, _ = self.weapon(dfn, atk, dist, False)
        a6 = self.defence_value(atk, ax, ay)
        d6 = self.defence_value(dfn, dfn.x, dfn.y)
        ab, db = bars(atk.hp), bars(dfn.hp)
        aC = div(a10 * ab, 10)
        aE = div(a6 * ab, 10)
        dC = div(d10 * db, 10)
        dE = div(d6 * db, 10)
        for who, unit, c in (("atk", atk, aC), ("def", dfn, dC)):
            if c == 0:
                continue
            if self.ctx.settings_6 == 0:
                lo, hi = co_mod.luck(self.board.army(unit.player).co_id,
                                     bool(self.board.army(unit.player).power_active))
                good, bad = hi - lo - 9, -lo
                roll = rng_mod.luck_reduce(self.draw(f"forecast {who} {unit.type}#{unit.slot}"),
                                           good, bad)
                c = max(0, c + roll)
            else:
                c += 5
            if who == "atk":
                aC = c
            else:
                dC = c
        # 0x080234CC
        d12 = div(aC * (100 - dE), 100)
        d8 = dfn.hp - d12
        dC = div(d10 * max(d8, 0), 100)
        a12 = div(dC * (100 - aE), 100)
        a8 = atk.hp - a12
        # 0x08023508
        if a8 <= 0:
            if d8 <= 0:
                if a8 < d8:
                    d8, a8 = 1, 0
                else:
                    a8, d8 = 1, 0
            elif a8 < 0:
                a8 = 0
        if d8 < 0:
            d8 = 0
        return Forecast(weapon=aw, dealt=d12, taken=a12, atk_hp=a8, def_hp=d8)

    # -- attacks (0x0805F71C, 0x0805F7E0, 0x0805F948, 0x0805FB08) ---------
    def attack_coverage(self, unit) -> Tuple[Dict[Coord, int], bool]:
        """0x0805F71C: the tiles a shot can reach. Direct: the move grid
        ringed by one; indirect: the range ring from where it stands."""
        s = stats(unit.type)
        if s["max_range"] == 1:
            g = self.own_reach(unit)
            self.expand(g)
            return g, False
        cb = self.co_block(unit.player, unit.type)
        return self.ring(unit.x, unit.y, s["min_range"], s["max_range"] + cb["range"]), True

    def from_tile(self, unit, target, grid) -> Optional[Coord]:
        """0x0805FB08: the neighbour of the target to shoot from -- terrain
        value plus 100 if unthreatened, later neighbours winning ties."""
        best, tile = NO_TILE, None
        tv = tables()["terrain_value"]
        air = stats(unit.type)["move_class"] == 0x10
        for nx, ny in ((target.x - 1, target.y), (target.x + 1, target.y),
                       (target.x, target.y - 1), (target.x, target.y + 1)):
            if not (0 <= nx < self.board.width and 0 <= ny < self.board.height):
                continue
            occ = self.unit_at(nx, ny)
            if occ is not None and occ.slot != unit.slot:
                continue
            c = grid.get((nx, ny), UNREACHABLE)
            if c < 0 or c > WHOLE_MAP:
                continue
            score = 0 if air else tv[self.board.terrain[ny][nx]]
            if not self.threatened(unit, nx, ny):
                score += 100
            self.log.append(f"    from ({nx},{ny}) terrain {self.board.terrain[ny][nx]} score {score}")
            if tile is None or score >= best:
                best, tile = score, (nx, ny)
        return tile

    def attack_score(self, unit, target, fc: Forecast) -> int:   # 0x0805F948
        weights = tables()["counter_weight"]
        taken = fc.taken + (50 if fc.atk_hp == 0 else 0)
        if taken >= self.profile_unit(unit)[0]:
            return -1
        ts = stats(target.type)
        value = 1
        capturer = (ts["type"] <= 2
                    and tables()["property_terrain"][self.board.terrain[target.y][target.x]]
                    and not self.owner_team_ok(target.x, target.y))
        if capturer:
            terr = self.board.terrain[target.y][target.x]
            if terr == HQ:
                value = 0x20
            if terr in (BASE, 10, PORT):
                value <<= 3
            prog = target.capture
            n = (div(target.hp - 1, 10) + prog + 1) if target.hp else prog
            value = value * (div(n, 5) + 1) * 100
        else:
            value = 10 * (ts["cost_class"] + 9)
            value *= 10 if ts["max_range"] == 1 else 15
        dealt = fc.dealt
        if fc.def_hp == 0 and dealt <= 0x31:
            dealt = 0x32
        if ts["ai_class"] == 2:
            if target.cargo2 == 0:
                value <<= 1
            elif target.cargo == 0:
                value >>= 3
        score = dealt * (value >> 4) - taken * weights[stats(unit.type)["cost_class"]]
        return score if score >= 0 else -1

    def best_attack(self, unit) -> Optional[Tuple[object, Coord, int]]:
        """0x08065300 without the command: (target, from, score) or None."""
        self.build_threat(unit)
        grid, indirect = self.attack_coverage(unit)
        cands = []
        for x, y in self.scan():
            if grid.get((x, y), UNREACHABLE) < 0:
                continue
            t = self.unit_at(x, y)
            if t is None or self.allied(self.player, t.player):
                continue
            if t.type == "Sub" and t.dived:
                continue
            if indirect:
                frm = (unit.x, unit.y)
            else:
                frm = self.from_tile(unit, t, grid)
                if frm is None:
                    continue
            fc = self.forecast(unit, frm[0], frm[1], t)
            if fc.weapon == 0:
                continue
            sc = self.attack_score(unit, t, fc)
            cands.append((t, frm, max(sc, 0)))
            self.log.append(f"  cand {unit.type}#{unit.slot} -> {t.type}#{t.slot} from {frm}: "
                            f"dealt {fc.dealt} taken {fc.taken} hp {fc.atk_hp}/{fc.def_hp} score {sc}")
        best = None
        for c in cands:                                          # 0x0805F778
            if c[2] > (best[2] if best else 0):
                best = c
        return best

    # -- the mover (0x08060078) ---------------------------------------------
    def can_stop(self, unit, x: int, y: int) -> bool:          # 0x080604D0
        b = self.board
        occ = self.unit_at(x, y)
        if occ is not None and occ.slot != unit.slot:
            return False
        t = tables()
        terr = b.terrain[y][x]
        if not t["property_terrain"][terr]:
            return True
        owner = b.owner[y][x]
        if owner == self.player:
            f = t["factory_terrain"][terr]
            if f == 0 or f == self.behaviour or (self.flags_5008 & 2):
                return True
            return False
        if owner == 0:
            return False
        if type_id(unit.type) <= 2:
            return True
        return self.near_enemy_hq(x, y)

    def near_enemy_hq(self, x: int, y: int) -> bool:           # 0x080619F8
        for k in self.enemy_sides():
            hq = self.side(k).hq
            if hq and 0 <= x - hq[0] + 2 <= 4 and 0 <= y - hq[1] + 2 <= 4:
                return True
        return False

    def move_toward(self, unit, goal: Coord, *, goal_grid: Optional[Dict[Coord, int]] = None):
        """0x08060078 (with `goal_grid`, 0x080602D8): the reachable tile
        closest to the goal, later tiles winning ties. Returns the tile."""
        if goal_grid is None:
            goal_grid = self.fill(goal[0], goal[1], type_id(unit.type), WHOLE_MAP, False)
        self.goal_grid = goal_grid
        prof = self.profile_unit(unit)
        avoid = prof[1] >= self.ai(unit)[1] % 100
        if avoid:
            self.build_threat(unit)
        reach = self.own_reach(unit)
        best = 0x7FFF if prof[1] == 100 else goal_grid.get((unit.x, unit.y), 255)
        tile = None
        sea = stats(unit.type)["move_class"] == 0x20
        for x, y in self.scan():
            if (x, y) not in reach:
                continue
            d = goal_grid.get((x, y), 255)
            if d > best:
                continue
            if avoid and self.threatened(unit, x, y):
                continue
            if not self.can_stop(unit, x, y):
                continue
            if not sea and self.board.terrain[y][x] == PORT:
                continue
            best, tile = d, (x, y)
        if tile is not None:
            self.emit(unit, 2, tile)
        if self.flags_5008 & 2 and prof[1] > self.ai(unit)[1] % 100:
            raise NotImplementedError("0x0806636C (drop after moving)")
        self.settle(unit)
        return tile

    def settle(self, unit):
        """0x08066248: a unit that may not stay where it stands moves to
        the best-valued tile it can reach."""
        if self.command_issued(unit) or self.can_stop(unit, unit.x, unit.y):
            return
        reach = self.own_reach(unit)
        tv = tables()["terrain_value"]
        air = stats(unit.type)["move_class"] == 0x10
        best, tile = 0, None
        for x, y in self.scan():
            c = reach.get((x, y))
            if c is None:
                continue
            v = (20 - c) if air else tv[self.board.terrain[y][x]] - (c - 20)
            if v > best and self.can_stop(unit, x, y):
                best, tile = v, (x, y)
        if tile is not None:
            self.emit(unit, 2, tile)

    # -- commands ------------------------------------------------------------
    def command_issued(self, unit) -> bool:
        return bool(self.commands) and self.commands[-1].slot == unit.slot \
            and getattr(self, "_issued_for", None) == unit.slot

    def path_draws(self, unit, dest: Coord) -> None:
        """0x0801DC38: the writer's path from the unit to `dest`, walked
        back over the unit's move grid; equal-cost predecessors are chosen
        by a draw (two: bit 14; three: mod 3; four: low two bits)."""
        grid = self.own_reach(unit)
        x, y = dest
        if grid.get(dest, 0) == 0:
            return
        w, h = self.board.width, self.board.height
        while True:
            vals = []
            for i, (nx, ny) in enumerate(((x + 1, y), (x - 1, y), (x, y - 1), (x, y + 1))):
                if 0 <= nx < w and 0 <= ny < h:
                    v = grid.get((nx, ny), UNREACHABLE) & 0xFF
                else:
                    v = 0xFF
                vals.append(v)
            m = min(vals)
            ties = [i for i, v in enumerate(vals) if v == m]
            if len(ties) == 1:
                pick = ties[0]
            elif len(ties) == 2:
                pick = ties[(self.draw("path tie 2") >> 14) & 1]
            elif len(ties) == 3:
                pick = ties[self.draw("path tie 3") % 3]
            else:
                pick = ties[self.draw("path tie 4") & 3]
            x, y = ((x + 1, y), (x - 1, y), (x, y - 1), (x, y + 1))[pick]
            if grid.get((x, y), 0) == 0:
                return

    def emit(self, unit, cid: int, tile: Coord, arg: int = 0, arg2: int = 0):
        """0x080644D8: a Wait onto the unit's own tile is dropped."""
        if self.command_issued(unit):
            return
        if cid == 2 and tile == (unit.x, unit.y):
            self._issued_for = unit.slot
            self.log.append(f"  {unit.type}#{unit.slot}: stays")
            return
        self.path_draws(unit, tile)
        cmd = Command(id=cid, slot=unit.slot, tile=tile, origin=(unit.x, unit.y),
                      arg=arg, arg2=arg2, rng=self.rng, fuel=0)
        self.commands.append(cmd)
        self._issued_for = unit.slot
        self.log.append(f"  {unit.type}#{unit.slot}: {cmd.name} -> {tile} arg {arg}")

    # -- the passes ----------------------------------------------------------
    def guard(self, unit) -> bool:
        """0x080651AC: on an own non-factory property with an enemy
        Infantry within an Infantry's walk, stay put."""
        b = self.board
        x, y = unit.x, unit.y
        if b.owner[y][x] != self.player:
            return False
        t = tables()
        if t["factory_terrain"][b.terrain[y][x]]:
            return False
        g = self.fill(x, y, 1, 3, False)
        for tx, ty in self.scan():
            if (tx, ty) not in g:
                continue
            u = self.unit_at(tx, ty)
            if u is None or self.allied(self.player, u.player):
                continue
            if u.type == "Infantry":
                self.emit(unit, 2, (x, y))
                return True
        return False

    def capture_pass(self, unit) -> bool:                       # 0x080646B0
        self.guard(unit)
        if self.command_issued(unit):
            return True
        if self.capturable(unit.x, unit.y):
            self.emit(unit, 3, (unit.x, unit.y))
            return True
        reach = self.own_reach(unit)
        bonus = tables()["capture_bonus"]
        best, tile = 0, None
        for x, y in self.scan():
            c = reach.get((x, y))
            if c is None or not self.capturable(x, y):
                continue
            if self.unit_at(x, y) is not None:
                continue
            terr = self.board.terrain[y][x]
            if terr == HQ:
                score = c + 8
            elif bonus[terr]:
                score = c + 4
            else:
                score = c
            if score > best:
                best, tile = score, (x, y)
        if tile is not None:
            self.emit(unit, 3, tile)
            return True
        return False

    def direct_pass(self, unit):                                 # 0x080648EC
        self.guard(unit)
        if self.command_issued(unit):
            return
        self.attack_pass(unit)
        if self.command_issued(unit):
            return
        self.mode_dispatch(unit)

    def attack_pass(self, unit):                                 # 0x08065300
        best = self.best_attack(unit)
        if best is not None:
            t, frm, _ = best
            self.emit(unit, 4, frm, t.slot)

    def indirect_pass(self, unit):                               # 0x080648A8
        self.guard(unit)
        if self.command_issued(unit):
            return
        prof = self.profile_unit(unit)
        if prof[0] > self.ai(unit)[1] % 100 or self.board.army(self.player).power_active:
            self.attack_pass(unit)

    def mode_dispatch(self, unit):                               # 0x08066040
        mode = self.ai(unit)[2]
        if mode > 7:
            mode = self.ai(unit)[2] = 1
        fn = {0: lambda u: None, 1: self.mode_hq, 4: self.mode_hunt}.get(mode)
        if fn is None:
            raise NotImplementedError(f"movement mode {mode} ({tables()['mode_fns'][mode]})")
        fn(unit)

    def far_range(self, unit) -> int:                            # 0x0805FDDC
        return WHOLE_MAP

    def mode_hq(self, unit):                                     # 0x08065870
        g = self.fill(unit.x, unit.y, type_id(unit.type), self.far_range(unit), False)
        best, goal = 0x7FFF, None
        for k in self.enemy_sides():
            hq = self.side(k).hq
            if hq is None:
                continue
            d = g.get(hq, -1)
            if 0 <= d < best:
                best, goal = d, hq
        if goal is None:
            raise NotImplementedError("0x0806606C (no enemy HQ reachable)")
        self.move_toward(unit, goal)

    def hunt_list(self, unit, g: Dict[Coord, int]) -> List[Tuple[Coord, int]]:
        """0x08060A90: every enemy this unit can damage, valued by the
        forecast base against it and its fuel band."""
        mult = tables()["hunt_multiplier"]
        bands = tables()["fuel_bands"]
        fuel = unit.fuel
        band = 0
        while band < len(bands) and fuel > bands[band]:
            band += 1
        out = []
        s = stats(unit.type)
        for k in self.enemy_sides():
            for e in self.board.units_of(k):
                if e.loaded or g.get((e.x, e.y), -1) <= 0:
                    continue
                if e.type == "Sub" and e.dived:
                    continue
                # 0x08060A90: the attacker's value only -- base through the
                # CO's per-type attack and all-units multiplier, the larger
                # weapon (a tie keeps the primary, 0x08060D00's blt)
                t = damage.tables()
                army = self.board.army(unit.player)
                pow_ = bool(army.power_active)
                atk_pct = co_mod.modifiers(army.co_id, unit.type, pow_)[0]
                atk_uni = co_mod.universal(army.co_id, pow_)[0]
                prim = t["primary"].get(unit.type, {}).get(e.type, 0)
                sec = t["secondary"].get(unit.type, {}).get(e.type, 0)
                p = div(div(prim * atk_pct, 100) * atk_uni, 100) if prim else 0
                q = div(div(sec * atk_pct, 100) * atk_uni, 100) if sec else 0
                base = p if p >= q else q
                val = base * mult[min(band, len(mult) - 1)]
                out.append(((e.x, e.y), val, e))
        return out

    def mode_hunt(self, unit):                                   # 0x08065B30
        g = self.fill(unit.x, unit.y, type_id(unit.type), self.far_range(unit), False)
        self.expand_by_range(unit, g)
        lst = self.hunt_list(unit, g)
        # 0x08060A34: the lowest score, later entries winning ties
        goal, best = None, 0x7FFF
        for tile, val, e in lst:
            if val <= best:
                goal, best = tile, val
        if goal is None:
            raise NotImplementedError("0x0806606C (nothing to hunt)")
        if g.get(goal, 0) <= RANGE_MARK:
            self.move_toward(unit, goal)
        else:
            goal_grid = self.fill(goal[0], goal[1], 16, WHOLE_MAP, False)
            self.move_toward(unit, goal, goal_grid=goal_grid)

    def expand_by_range(self, unit, g: Dict[Coord, int]):       # 0x08060938
        s = stats(unit.type)
        n = s["max_range"] if s["max_range"] > 1 else s["min_range"]
        for i in range(n):
            self.expand(g, RANGE_MARK + i)

    def foot_pass(self, unit):                                   # 0x08064C94
        self.join_pass(unit)
        if self.command_issued(unit):
            return
        self.capture_pass(unit)
        if self.command_issued(unit):
            return
        prof = self.profile_unit(unit)
        # 0x08064CB6: a foot soldier shoots only when its random beats the
        # profile's threshold (blo: threshold < random), or under a power
        if prof[0] < self.ai(unit)[1] % 100 or self.board.army(self.player).power_active:
            self.attack_pass(unit)
            if self.command_issued(unit):
                return
        reach = self.fill(unit.x, unit.y, type_id(unit.type), self.far_range(unit), False)
        props = [(x, y) for x, y in self.scan()
                 if (x, y) in reach and self.capturable(x, y)]
        limit = div(self.count_class(1), self.ctx.profile["header"][9] or 1)
        goal = self.pick_property(unit, props, reach, limit)
        self.log.append(f"  props {props[:6]} limit {limit} -> goal {goal} "
                        f"d {reach.get(goal) if goal else None}")
        if goal is not None:
            self.ai(unit)[0] &= ~0x39
            far = self.ride_check(unit, goal, reach)
            if self.command_issued(unit):
                return
            self.move_toward(unit, goal)
            return
        raise NotImplementedError("0x08064D6A (foot unit with no property to walk to)")

    def count_class(self, cls: int) -> int:                     # 0x0805F000
        return sum(1 for u in self.board.units_of(self.player)
                   if stats(u.type)["ai_class"] == cls)

    def pick_property(self, unit, props, reach, limit):
        """0x0805F150: the nearest property whose target counter is under
        the limit; the counter is bumped for the one taken."""
        cands = [(reach.get(t, 0x7FFF), t) for t in props]
        while cands:
            d, t = min(cands, key=lambda c: c[0])
            cnt = self.counters.setdefault(t, [0] * 8)
            if cnt[0] > limit:
                cands = [c for c in cands if c[1] != t]
                continue
            cnt[0] += 1
            return t
        return None

    def ride_check(self, unit, goal, reach) -> bool:           # 0x080630B8
        d = reach.get(goal, 0)
        m = stats(unit.type)["move"]
        if d <= 2 * m:
            return False
        tt = "TCopter" if self.ctx_flag_air() else "APC"
        state = 2 if tt == "TCopter" else 1
        tg = self.fill(unit.x, unit.y, type_id(tt), WHOLE_MAP, False)
        self.expand(tg)
        td = tg.get(goal, 0)
        by_ride = div(td * 3, stats(tt)["move"])
        by_foot = div(d * 4, m)
        if by_foot > by_ride:
            self.ai(unit)[0] = (self.ai(unit)[0] & ~0x39) | (state << 3)
            self.board_transport(unit)
            return True
        return False

    def ctx_flag_air(self) -> bool:
        return bool(self.side_flags() & 1)                       # 0x030050E4 bit 0

    def side_flags(self) -> int:
        """0x030050E4, as state 0 computes it (0x080689A8): the OR over
        EVERY tile of the map, whoever owns it, of the 5-byte table at
        0x0811A92C indexed by the terrain's factory class (0x083B7DDC)
        >> 1 -- so any Airport on the map sets bit 0 (foot soldiers then
        ask for TCopters) and any Port bit 1 (the Lander buy)."""
        if self._side_flags is None:
            t = tables()
            f = 0
            for x, y in self.scan():
                cls = t["capture_bonus"][self.board.terrain[y][x] & 0x1F]
                f |= t["side_flags"][cls >> 1]
            self._side_flags = f
        return self._side_flags

    def board_transport(self, unit):                            # 0x080665B8 / 0x08066664
        reach = self.own_reach(unit)
        want = (self.ai(unit)[0] >> 3) & 7
        for t in self.board.units_of(self.player):
            if t.loaded or (t.state & 8) or (t.x, t.y) not in reach:
                continue
            ok = {1: ("APC", "TCopter"), 2: ("TCopter",), 3: ("Lander",)}.get(want, ())
            if t.type not in ok:
                continue
            if t.cargo and (t.type != "Lander" or t.cargo2):
                continue
            self.ai(t)[0] += 0x40
            self.emit(unit, 6, (t.x, t.y))
            return

    def join_pass(self, unit):                                   # 0x080650B8
        if unit.hp > 0x32:
            return
        raise NotImplementedError("0x080650B8 join for a damaged foot unit")

    def transport_pass(self, unit):                              # 0x08064980
        if self.ai(unit)[0] & 0xC0:
            return
        if unit.type == "APC":
            self.apc_pass(unit)
        elif unit.type == "TCopter":
            raise NotImplementedError("0x08060670 TCopter")

    def apc_pass(self, unit):                                    # 0x080605AC
        g = self.fill(unit.x, unit.y, type_id(unit.type), WHOLE_MAP, True)
        self.expand(g)
        self.goal_grid = g
        goal = self.pickup_target(unit, 0)
        if goal is not None:
            self.move_toward(unit, goal)
            return
        prof = self.profile_unit(unit)
        if prof[0] > self.ai(unit)[1] % 100 or self.board.army(self.player).power_active:
            self.attack_pass(unit)
        self.settle(unit)

    def drop_pass(self, unit):                                   # 0x080649B0
        """A loaded APC or TCopter sets its passenger down: onto the
        capturable property with the highest move-grid value it can reach
        or stand beside (the ring marker 0x79 beats every cost, so a
        property one step past its reach wins), else onto the tile with
        the most properties within an Infantry's walk."""
        if not (self.ai(unit)[0] & 0xC0) or unit.type not in ("APC", "TCopter"):
            return
        g = self.own_reach(unit)
        self.expand(g)
        best, pick = 0, None
        for x, y in self.scan():
            v = g.get((x, y), UNREACHABLE)
            if v < 0 or not self.capturable(x, y) or self.unit_at(x, y) is not None:
                continue
            if v <= best:
                continue
            frm = self.drop_from(unit, x, y, g)
            if frm is None:
                continue
            best, pick = v, ((x, y), frm)
        if pick is None:
            best = 0
            for x, y in self.scan():
                v = g.get((x, y), UNREACHABLE)
                if v < 0:
                    continue
                pv = self.prop_grid.get((x, y), 0)
                if pv <= best or self.unit_at(x, y) is not None:
                    continue
                frm = self.drop_from(unit, x, y, g)
                if frm is None:
                    continue
                best, pick = pv, ((x, y), frm)
        if pick is None:
            return
        (px, py), (fx, fy) = pick
        self.ai(unit)[0] -= 0x40
        if px != fx:
            direction = (fx - px) + 3           # 2 east, 4 west
        else:
            direction = (py - fy) + 2           # 1 north, 3 south
        self.emit(unit, 7, (fx, fy), direction, 0)

    def drop_from(self, unit, x: int, y: int, g: Dict[Coord, int]) -> Optional[Coord]:
        """0x0805FC94: a neighbour of (x, y) the transport can reach this
        turn and stand on; the last of W, E, N, S wins."""
        mt = move_type_of(unit.type)
        if self.board.move_cost(x, y, mt) is None:
            return None
        out = None
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= nx < self.board.width and 0 <= ny < self.board.height):
                continue
            occ = self.unit_at(nx, ny)
            if occ is not None and occ.slot != unit.slot:
                continue
            v = g.get((nx, ny), UNREACHABLE)
            if v < 0 or v == RANGE_MARK:
                continue
            if self.board.move_cost(nx, ny, mt) is None:
                continue
            out = (nx, ny)
        return out

    def loaded_pass(self, unit):                                 # 0x08064C58
        if not (self.ai(unit)[0] & 0xC0):
            return
        raise NotImplementedError("0x08060708 loaded APC / 0x080607C4 loaded TCopter")

    def supply_pass(self, unit):                                 # 0x08065034
        """An APC supplies a low-fuel neighbour it can reach (0x08064820),
        else drives toward the neediest low-fuel unit (0x08061710)."""
        if unit.type != "APC":
            return
        g = self.own_reach(unit)
        self.expand(g)
        slot = self.needy_unit(unit, g)
        if slot is not None:
            target = sim.unit_in(self.board, slot)
            frm = self.from_tile(unit, target, g)
            if frm is not None:
                self.ai(target)[0] &= ~8
                self.emit(unit, 5, frm, slot)
                return
        g = self.fill(unit.x, unit.y, type_id(unit.type), WHOLE_MAP, True)
        self.expand(g)
        slot = self.needy_unit(unit, g)
        if slot is None:
            self.settle(unit)
            return
        target = sim.unit_in(self.board, slot)
        self.move_toward(unit, (target.x, target.y))

    def needy_unit(self, unit, g: Dict[Coord, int]) -> Optional[int]:  # 0x08061710
        best, pick = 0x7FFF, None
        t = tables()
        for u in self.board.units_of(self.player):
            if u.slot == unit.slot or u.loaded or (u.state & 8):
                continue
            if (self.ai(u)[0] & 7) != 1:
                continue
            if g.get((u.x, u.y), UNREACHABLE) < 0:
                continue
            score = (5 - t["t7E09"][type_id(u.type)]) * 16 + u.fuel
            if score < best:
                best, pick = score, u.slot
        return pick

    def build_prop_grid(self) -> None:                           # 0x080685D0
        """How many capturable properties lie within an Infantry's walk
        of each tile, skipping properties an allied foot unit stands on."""
        grid: Dict[Coord, int] = {}
        for x, y in self.scan():
            if not self.capturable(x, y):
                continue
            u = self.unit_at(x, y)
            if u is not None and self.allied(self.player, u.player) and type_id(u.type) <= 2:
                continue
            for t in self.fill(x, y, 1, 3, False):
                grid[t] = grid.get(t, 0) + 1
        self.prop_grid = grid

    def pickup_target(self, unit, kind: int) -> Optional[Coord]:  # 0x08061AB0
        classes = tables()["load_classes"][4 * kind: 4 * kind + 4]
        best, pick = 0x7FFF, None
        for u in self.board.units_of(self.player):
            if type_id(u.type) > 2 or self.targeted.get(u.slot):
                continue
            want = (self.ai(u)[0] >> 3) & 7
            if not classes[want & 3] if want < 4 else False:
                continue
            d = self.goal_grid.get((u.x, u.y), -1)
            if d > best or d < 0:
                continue
            reach = self.fill(u.x, u.y, type_id(u.type), stats(u.type)["move"], True)
            tile, bd = None, 0x7FFF
            for x, y in self.scan():
                if (x, y) not in reach:
                    continue
                gd = self.goal_grid.get((x, y), -1)
                if gd < 0 or gd > bd or self.unit_at(x, y) is not None:
                    continue
                mt = move_type_of(unit.type)
                if self.board.move_cost(x, y, mt) is None:
                    continue
                if self.owner_team_ok(x, y) and tables()["factory_terrain"][self.board.terrain[y][x]]:
                    continue
                tile, bd = (x, y), gd
            if tile is not None:
                pick, best = tile, bd
                target_slot = u.slot
        if pick is not None:
            self.targeted[target_slot] = self.targeted.get(target_slot, 0) + 1
        return pick

    # -- the power sub-phase (0x0806490C) ------------------------------------
    def power_pass(self) -> None:
        """Fire the CO power when the meter is at its threshold
        (0x0801C07C) and the CO's predicate says so. The predicates read
        (data/aw1_ai.json `cos[].power_fn`): 0x08063298, most COs -- at
        the turn's first power sub-phase only; 0x080632C4, Andy -- only
        with a unit at 90 hp or less to heal; 0x080632AC, co 8 -- at the
        second (end-of-turn) pass; 0x08063324, co 3 -- only under a
        settings weather (bytes +0x2C/+0x2D) the traces never set."""
        army = self.board.army(self.player)
        if army.power_active or army.co_id is None:
            return
        uses = army.power_uses or 0
        if army.power < co_mod.power_threshold(army.co_id, uses):
            return
        fn = tables()["cos"][army.co_id]["power_fn"]
        first = self.subphase <= 1
        if fn == "0x08063298":
            fire = first
        elif fn == "0x080632C4":
            fire = first and any(u.hp <= 0x5A for u in self.board.units_of(self.player))
        elif fn == "0x080632AC":
            fire = self.subphase > 15
        elif fn == "0x08063324":
            raise NotImplementedError("0x08063324: a weather-conditioned AI power predicate")
        else:
            raise NotImplementedError(f"AI power predicate {fn}")
        if not fire:
            return
        raise NotImplementedError(
            "the CPU would fire its power here (0x0801C120): no trace has seen "
            "one, so the RNG draws and the command it leaves are unread")

    # -- the turn ------------------------------------------------------------
    def units_of_class(self, cls: int) -> list:
        """The sub-phase's order list (0x080641CC): the class's unacted
        units, by the type's move descending, slot order on ties."""
        us = [u for u in sorted(self.board.units_of(self.player), key=lambda u: u.slot)
              if not u.acted and not u.loaded and stats(u.type)["ai_class"] == cls]
        return sorted(us, key=lambda u: -stats(u.type)["move"])

    def decide(self, unit, behaviour) -> None:
        """State 2 (0x080642C8): the unit's random, the classifier, the
        behaviour, then the command executed through the forward model."""
        self.flags_5008 &= ~3
        self.threat = None
        self._issued_for = None
        self.ai(unit)[1] = self.draw(f"unit random {unit.type}#{unit.slot}") % 100
        beh = tables()["behaviour_by_type"][type_id(unit.type)]
        if beh == 4 and (self.ai(unit)[0] & 7) == 0:
            beh = 0
        self.behaviour = beh
        self.log.append(f"{unit.type}#{unit.slot} at ({unit.x},{unit.y}) random {self.ai(unit)[1]}")
        cond = self.ai(unit)[0] & 7
        if cond in (1, 2) and unit.capture == 0:
            raise NotImplementedError(f"0x08065590 pre-step for condition {cond}")
        behaviour(unit)
        if self._issued_for == unit.slot and self.commands and self.commands[-1].slot == unit.slot:
            self.execute(self.commands[-1])

    def execute(self, cmd: Command):
        from . import cpu as cpu_mod
        act = cpu_mod.to_action(self.board, cmd, self.warnings, fog=False)
        if act is None:
            raise RuntimeError(f"predicted {cmd} names no engine Action")
        kw = {}
        if act.kind == "attack":
            kw = {"rng_state": cmd.rng, "luck_draw": cpu_mod.AI_STRIKE_DRAW}
        self.board = sim.apply(self.board, act, warnings=self.warnings, **kw)
        if act.kind == "attack":
            for _ in range(rng_mod.BATTLE_DRAWS_NO_COUNTER):
                self.draw("battle")

    def run(self):
        self.build_prop_grid()
        phases = tables()["subphases"]["fog" if self.ctx.fog else "clear"]
        for i, name in enumerate(phases):
            self.subphase = i + 1
            self.log.append(f"-- sub-phase {i} {name}")
            if name == "power":
                self.power_pass()
                continue
            if name == "clear_targeted":
                self.targeted.clear()
                continue
            if name == "end":
                self.build_pass()
                break
            plan = {"foot_capture": (1, self.capture_pass),
                    "indirect_fire": (4, self.indirect_pass),
                    "direct": (5, self.direct_pass),
                    "foot": (1, self.foot_pass),
                    "transport_empty": (2, self.transport_pass),
                    "transport": (2, self.drop_pass),
                    "transport_loaded": (2, self.loaded_pass),
                    "apc_supply": (2, self.supply_pass),
                    "indirect_move": (4, self.mode_dispatch)}
            if name not in plan:
                units = self.units_of_class({"air_strike": 5, "transport": 2,
                                             "transport_loaded": 2, "lander": 6,
                                             "apc_supply": 2, "class3": 3}.get(name, -1))
                if name == "air_strike":
                    units = [u for u in units if u.type in ("Fighter", "Bomber")]
                if name == "transport" or name == "transport_loaded":
                    units = [u for u in units if bool(self.ai(u)[0] & 0xC0) == (name == "transport_loaded")]
                if units:
                    raise NotImplementedError(f"sub-phase {name} with {len(units)} unit(s)")
                continue
            cls, fn = plan[name]
            for unit in self.units_of_class(cls):
                unit = sim.unit_in(self.board, unit.slot)
                if unit is None or unit.acted:
                    continue
                self.decide(unit, fn)
        return self.commands


    # -- building: driver state 4 (0x08066EC8), once, after the last sub-phase --
    #
    # The AI buys at the END of its turn: the "end" sub-phase writes driver
    # state 5, and the end-turn path runs 0x08066EC8 once before the
    # human's turn (DERIVATION 47; the purchase is made directly by the
    # writer 0x08067A48 -> 0x080243DC, never through the command
    # dispatcher, which is why no command trace ever showed one). The
    # parameters are the AI PROFILE itself: 0x08068346 copies the profile
    # block to 0x0202370C and every chooser reads it through 0x083B7CE4 --
    # header bytes 0..8 and, per unit type, the row's bytes 4..10 (the mode
    # roll's cumulative table) and 11 (the weight).

    def profile_byte(self, off: int) -> int:
        """The profile block by flat offset: 16 header bytes, then a
        12-byte row per RAM type at 4 + 12 * type."""
        p = self.ctx.profile
        if off < 16:
            return p["header"][off]
        t, j = divmod(off - 4, 12)
        name = tables()["unit_stats"]
        for n, s in name.items():
            if s["type"] == t:
                return p["units"][n][j]
        return 0

    def own_records(self) -> list:
        return [u for u in self.board.units if u.player == self.player]

    @staticmethod
    def named(tid: int) -> Optional[str]:
        """The unit name of a RAM type, None for the six vestigial ids
        (4, 8, 9, 12, 13, 18) -- their damage rows and profile rows are
        zero, so the choosers skip them where the ROM reads zeros."""
        for n, s in tables()["unit_stats"].items():
            if s["type"] == tid:
                return n
        return None

    def count_type(self, tid: int) -> int:                   # 0x0805F0A4
        return sum(1 for u in self.own_records() if type_id(u.type) == tid)

    def count_class(self, cls: int) -> int:                  # 0x0805F000
        return sum(1 for u in self.own_records() if stats(u.type)["ai_class"] == cls)

    def count_move_mask(self, mask: int) -> int:             # 0x0805F050
        return sum(1 for u in self.own_records() if stats(u.type)["move_class"] & mask)

    def enemy_count_type(self, tid: int) -> int:             # 0x0805F0E4
        sides = set(self.enemy_sides())
        return sum(1 for u in self.board.units
                   if u.player in sides and type_id(u.type) == tid)

    def price(self, tid: int) -> int:                        # 0x080243DC's arithmetic
        a = self.board.army(self.player)
        cid = a.co_id if self.ctx.settings_8 else 1
        return production.price(type_name(tid), cid, bool(a.power_active))

    def factory_records(self) -> list:                       # 0x08067684
        """The AI's factory list: every record of the game's property list
        (0x03004500, y-then-x order) that is a Base/Airport/Port of the
        active side with no unit on it -- as {x, y, cls, mark}, cls from
        0x083B7DDC (Base 2, Airport 4, Port 6), mark 0x7F until ranked."""
        b = self.board
        props = self.ctx.properties
        if props is None:
            props = [(b.terrain[y][x], x, y) for x, y in self.scan()]
        out = []
        for t, x, y in props:
            tid = b.terrain[y][x]
            if tid not in (10, 11, 14) or b.owner[y][x] != self.player:
                continue
            if b.unit_at(x, y) is not None:
                continue
            out.append({"x": x, "y": y, "cls": tables()["capture_bonus"][tid], "mark": 0x7F})
        return out

    def factories_of(self, cls: int) -> int:                 # 0x080677B0
        return sum(1 for r in self._factories if r["cls"] == cls and r["mark"] != 0xFE)

    def build_pass(self) -> None:                            # 0x08066EC8
        hdr = self.ctx.profile["header"]
        param = 0
        if self.board.day > 3:                               # 0x08067650
            param = min(0x50, self.board.day * hdr[3])
        self._factories = self.factory_records()
        self._enemy_battleships = self.enemy_count_type(21)  # 0x03005014
        self.log.append(f"-- build: {len(self._factories)} factory(ies), mech chance {param}")
        for _ in range(len(self._factories)):
            self.build_one(param)

    def build_one(self, param: int) -> None:                 # 0x08066F10
        total = len(self.own_records())                      # 0x030050A4
        foot = self.count_class(1)                           # 0x03005104
        chosen = self.choose_foot(param, total, foot)        # 0x08066FCC
        if chosen == 0:
            chosen = self.choose_transport(foot)             # 0x08067064
        if chosen == 0:
            chosen = self.choose_counter(total)              # 0x080671E0
        if chosen == 0:
            chosen = self.choose_sub()                       # 0x080671AC
        if chosen == 0:
            chosen = self.choose_fallback(total)             # 0x08067624
        if chosen:
            self.write_build(chosen, total)                  # 0x08067A48

    def choose_foot(self, param: int, total: int, foot: int) -> int:
        """0x08066FCC: a foot soldier unless the foot share is over the
        profile's caps; Mech with `param` percent, else Infantry."""
        if self.factories_of(2) == 0:
            return 0
        hdr = self.ctx.profile["header"]
        pct = div(foot * 100, total) if total else 0
        if foot >= hdr[0] and pct > hdr[2]:
            r2 = sum(self.ctx.army0[:4]) + 1                 # 0x08068824(0)
            if r2 == 0:
                return 0
            if div(foot * 100, r2) > hdr[1]:
                return 0
        roll = self.draw("build foot roll") % 100
        return 2 if roll < param else 1

    def choose_transport(self, foot: int) -> int:            # 0x08067064
        hdr = self.ctx.profile["header"]
        flags = self.side_flags()
        if flags & 1 and self.factories_of(4):               # 0x080670A0 TCopter
            c = self.count_type(20)
            ratio = 100 if foot == 0 else div(c * 100, foot)
            if ratio < hdr[4]:
                return 20
        if self.factories_of(2):                             # 0x080670EC APC
            c = self.count_type(7)
            ratio = 100 if foot == 0 else div(c * 100, foot)
            if ratio < (hdr[5] if flags & 1 else hdr[6]):
                return 7
        if flags & 2 and self.factories_of(6):               # 0x08067154 Lander
            c = self.count_type(23)
            ground = self.count_move_mask(7)
            ratio = 100 if ground == 0 else div(c * 100, ground)
            if ground > self.profile_byte(0x22) and ratio < hdr[7]:
                return 23
        return 0

    def attack_mods(self, tid: int) -> tuple:
        """(pool attack, universal attack) of the active side's CO for a
        type -- record 1's when the CO gate (settings +0x08) is off."""
        a = self.board.army(self.player)
        cid = a.co_id if self.ctx.settings_8 else 1
        power = bool(a.power_active)
        try:
            pool = co_mod.modifiers(cid, type_name(tid), power)[0]
        except KeyError:
            pool = 100
        return pool, co_mod.universal(cid, power)[0]

    def desire_table(self) -> Dict[int, int]:                # 0x08069748
        """Per enemy type: its total hp less what our army's primary
        weapons deal it, CO-modified -- the enemy type we are least able
        to answer scores highest."""
        enemy_hp: Dict[int, int] = {}
        own_hp: Dict[int, int] = {}
        sides = set(self.enemy_sides())
        for u in self.board.units:
            if u.player not in self.ctx.sides:
                continue
            d = enemy_hp if u.player in sides else own_hp
            d[type_id(u.type)] = d.get(type_id(u.type), 0) + u.hp
        prim = damage.tables()["primary"]
        out: Dict[int, int] = {}
        for k in range(1, 25):
            v = enemy_hp.get(k, 0)
            if v == 0:
                out[k] = 0
                continue
            for j in range(1, 25):
                nj, nk = self.named(j), self.named(k)
                base = prim.get(nj, {}).get(nk, 0) if nj and nk else 0
                if base and own_hp.get(j):
                    pool, uni = self.attack_mods(j)
                    m = div(div(base * pool, 100) * uni, 100)
                    v -= own_hp[j] * m
            out[k] = ((v + 0x8000) & 0xFFFF) - 0x8000       # stored as u16, read as s16
        return out

    def choose_counter(self, total: int) -> int:             # 0x080671E0
        """The buildable, affordable type with the best CO-modified base
        damage against the most under-answered enemy type; indirects only
        under their share cap, nothing over profile[0x23]% of the funds."""
        ind_pct = 100 if total == 0 else div(self.count_class(4) * 100, total)
        desire = self.desire_table()
        prim = damage.tables()["primary"]
        funds = self.board.army(self.player).funds
        while True:
            best, target = 0, 0
            for k in range(1, 25):                           # strict >, first wins
                if desire[k] > best:
                    best, target = desire[k], k
            if target == 0:
                return 0
            thr = 0x28 if best > 100 else div(best, 3)
            score = {}
            for i in range(24):
                t = i + 1
                if self.profile_byte(0x1B + 12 * i) == 0 or not self.named(t):
                    score[t] = 0
                    continue
                base = prim.get(self.named(t), {}).get(self.named(target), 0)
                if base == 0:
                    score[t] = 0
                    continue
                pool, uni = self.attack_mods(t)
                v = div(div(base * pool, 100) * uni, 100)
                score[t] = (v & 0xFF) if v >= thr else 0
            while True:
                pick, top = 0, 0
                for t in range(1, 25):                       # strict >, first wins
                    if score[t] > top:
                        top, pick = score[t], t
                if pick == 0:
                    break
                score[pick] = 0
                mc = stats(type_name(pick))["move_class"]
                cls = 4 if mc == 0x10 else 6 if mc == 0x20 else 2
                price = self.price(pick)
                if self.factories_of(cls) == 0 or price > funds:
                    continue
                if stats(type_name(pick))["ai_class"] == 4 and ind_pct > self.profile_byte(0x21):
                    pick = 0
                    break
                if (price * 100) // funds > self.profile_byte(0x23):
                    pick = 0
                    break
                self.log.append(f"  build counter: {type_name(pick)} vs {type_name(target)}")
                return pick
            desire[target] = 0

    def choose_sub(self) -> int:                             # 0x080671AC
        if self._enemy_battleships > self.count_type(24) and self.factories_of(6):
            return 24
        return 0

    def choose_fallback(self, total: int) -> int:            # 0x08067624
        """0x080677EC / 0x08067850 / 0x08067978: the type furthest under
        its profile weight -- share (per mille of the army) over weight --
        among the buildable and affordable, if that ratio is at most
        header[8]; ties go to the heaviest weight."""
        hdr = self.ctx.profile["header"]
        funds = self.board.army(self.player).funds
        ratio = {}
        for i in range(24):
            t = i + 1
            w = self.profile_byte(0x1B + 12 * i)
            if w == 0:
                ratio[t] = 0xFF
                continue
            share = div(1000 * self.count_type(t), total) if total else 0
            ratio[t] = div(10 * share, w) & 0xFFFF
        for t in range(1, 25):                               # 0x08067850
            cls = tables()["behaviour_by_type"][t]
            if not self.named(t) or self.factories_of(cls) == 0 or self.price(t) > funds:
                ratio[t] = 0xFF
        low, pick = 0xFF, 0                                  # 0x08067978
        for t in range(1, 25):
            v = ((ratio[t] + 0x8000) & 0xFFFF) - 0x8000
            if v < low:
                low, pick = v, t
        if low > hdr[8]:
            return 0
        ties = [t for t in range(1, 25) if ((ratio[t] + 0x8000) & 0xFFFF) - 0x8000 == low]
        if len(ties) <= 1:
            return ties[0] if ties else pick
        best_w, out = 0, pick
        for t in ties:
            w = self.profile_byte(0x1B + 12 * (t - 1))
            if w > best_w:
                best_w, out = w, t
        self.log.append(f"  build fallback: {type_name(out)} (ratio {low}, ties {[type_name(t) for t in ties]})")
        return out

    def roll_mode(self, tid: int) -> int:                    # 0x08067BD0
        """One draw, then the row's cumulative bytes 4..10: the first
        entry above the roll names the mode; no table (0xFF) gives foot
        soldiers 0 and everyone else 1."""
        roll = self.draw(f"build mode roll {type_name(tid)}") % 100
        row = [self.profile_byte(0x14 + 12 * (tid - 1) + j) for j in range(7)]
        if row[0] == 0xFF:
            return 0 if tid <= 2 else 1
        for j, b in enumerate(row):
            if b == 0xFF:
                continue
            if b > roll:
                return j + 1
        return 1

    def rank_factories(self, tid: int, mode: int) -> bool:   # 0x08067D70
        """Fill from every free factory of the type's class and write the
        distance to its nearest candidate of `mode` into the record; False
        as soon as one free factory has no candidate at all."""
        b = self.board
        want = tables()["behaviour_by_type"][tid]
        base = 64 * (self.player - 1)
        for r in self._factories:
            if r["cls"] != want or b.unit_at(r["x"], r["y"]) is not None:
                continue
            grid = self.fill(r["x"], r["y"], 20 if mode == 4 else tid, 0x78, False)
            best = None
            for x, y in self.scan():
                d = grid.get((x, y))
                if d is None:
                    continue
                if mode == 0:
                    tid_here = b.terrain[y][x]
                    ok = tables()["property_terrain"][tid_here] == 1 and b.owner[y][x] != self.player
                elif mode == 2:
                    ok = b.terrain[y][x] in (0xB, 0xD)
                else:
                    u = b.unit_at(x, y)
                    ok = u is not None and base <= u.slot < base + 64
                    if ok and mode == 1:
                        ok = u.type == "TCopter" and u.cargo == 0
                    elif ok and mode == 3:
                        ok = type_id(u.type) <= 2 and not (self.ai(u)[0] & 8)
                    elif ok and mode == 4:
                        ok = bool(tables()["t7D9C"][type_id(u.type)]) and not (self.ai(u)[0] & 8)
                if ok and (best is None or d <= best):
                    best = d
            if best is None:
                return False
            r["mark"] = best & 0xFF
        return True

    def finish_factory(self, tid: int):                      # 0x080680D0
        want = tables()["behaviour_by_type"][tid]
        low, pick = 0xFE, None
        for r in self._factories:
            if r["mark"] < low and r["cls"] == want:
                low, pick = r["mark"], r
        if pick is None:
            return None
        pick["mark"] = 0xFE
        return (pick["x"], pick["y"])

    def pick_factory(self, tid: int):                        # 0x08067C38
        modes = []
        if tid == 20:
            modes.append(3)
        if tid == 23:
            modes.append(4)
        modes.append(0)
        if tid <= 2:
            modes.append(1)
        if tables()["behaviour_by_type"][tid] != 2:
            modes.append(2)
        for m in modes:
            if self.rank_factories(tid, m):
                return self.finish_factory(tid)
        want = tables()["behaviour_by_type"][tid]              # 0x08067CD6
        for r in self._factories:
            if r["mark"] <= 0xFD and r["cls"] == want:
                r["mark"] = 0xFE
                return (r["x"], r["y"])
        return None

    def write_build(self, tid: int, total: int) -> None:     # 0x08067A48
        funds = self.board.army(self.player).funds
        price = self.price(tid)
        if price > funds or total > 0x3F:
            return
        mode = self.roll_mode(tid)
        tile = self.pick_factory(tid)
        if tile is None:
            return
        name = type_name(tid)
        acts = [a for a in actions.build_actions(self.board, self.player, warnings=self.warnings)
                if tuple(a.tile) == tile and a.build_type == name]
        if not acts:
            raise RuntimeError(f"predicted build of {name} at {tile} names no engine Action")
        self.board = sim.apply(self.board, acts[0], warnings=self.warnings)
        slot = acts[0].target.slot
        self.ctx.ai[slot] = [0, 0, mode]
        self.builds.append({"x": tile[0], "y": tile[1], "type": tid, "name": name,
                            "mode": mode, "price": price, "slot": slot, "rng": self.rng})
        self.log.append(f"  build: {name} at {tile} for {price}, mode {mode}, slot {slot}")


# What the seven traces exercised and this port reproduces draw for draw
# (tests/test_cpu.py): the foot passes (0x080646B0, 0x08064C94) with the
# guard (0x080651AC), the property choice (0x08025DFC, 0x0805F150), the
# ride check (0x080630B8) and boarding (0x080665B8); the direct and indirect
# attack passes (0x080648EC, 0x080648A8) over 0x08065300's candidates,
# forecast (0x08023550) and score (0x0805F948); movement mode 4
# (0x08065B30) through the mover (0x08060078) and the threat grid
# (0x08068F68); the empty APC (0x080605AC, 0x08061AB0), the drop
# (0x080649B0, 0x0805FC94), the supply pass (0x08065034, 0x08061710); the
# path builder's tie draws (0x0801DC38); the power predicates (0x0806490C).
#
# What raises NotImplementedError with its address: movement modes 2, 3,
# 5, 6, 7 and the sea variant of mode 1; the Lander pass (0x08064DF4); the
# TCopter (0x08060670); the loaded transport's move (0x08060708,
# 0x080607C4); the join and retreat pre-steps (0x08065590, 0x080650B8);
# the "nothing to do" fallbacks (0x0806606C); firing a power (0x0801C120);
# campaign profiles (0x080683B0).
#
# Building (driver state 4, 0x08066EC8, run once at the turn's end) is
# ported above and reproduces the eleven build traces (DERIVATION 47):
# the factory list off the property list, the foot / transport / counter /
# Sub / fallback choosers, the mode roll, the factory ranking, the
# purchase. Untraced within it: the TCopter and Lander branches (flags
# byte 0x030050E4 was 0 on every trace) and a nonzero army-0 divisor.


def predict(board, player: int, ctx: Context, *, rng: Optional[int] = None) -> Turn:
    """The turn the CPU will take from `board` for `player`: the Turn holds
    the commands in order, the board they leave, the RNG draws made."""
    t = Turn(board=board, player=player, rng=board.rng if rng is None else rng, ctx=ctx)
    t.run()
    return t

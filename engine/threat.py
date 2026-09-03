"""What the enemy can do to you next turn.

Composition, not new reverse engineering. Every input is already extracted and
already checked against the running game:

  * where an enemy can go            -- pathing.py, matched tile-for-tile
                                        against the game's own flood fill
  * whether it can shoot you at all  -- the damage matrices, ROM-exact
  * for how much                     -- the calibrated formula, luck_after_hp
  * how much cover you have          -- terrain stars, and the sky substitution
                                        that stops aircraft inheriting them

There is not a single branch on unit type in this file, for the same reason
there is none in pathing.py. Whether a unit can shoot after moving, how far it
reaches, and whether it is armed at all are three ROM fields -- `armed`,
`can_move_and_fire`, `min_range`/`max_range`. An Artillery threatens a ring
around where it stands rather than around everywhere it could drive, and that
falls out of `can_move_and_fire` being false, not out of anyone typing its name.

WHAT IS MODELLED, and where each rule comes from

  * Direct units threaten every tile adjacent to any tile they may END on.
    `destinations()`, not `reachable()` -- a unit cannot shoot from a tile it
    is only passing through.
  * Indirect units threaten the range ring around their CURRENT tile, because
    `can_move_and_fire` is false and firing is therefore this turn's whole
    action. Range is Manhattan distance.
  * Unarmed units threaten nothing. `armed` is a ROM field, so transports and
    the like drop out without being enumerated.
  * Your unit blocks enemy movement. Asking "what if I stood at X" moves it
    there first and re-runs the enemy searches against that board, so a tile
    you would plug is scored as plugged, and the tile you vacate opens up.
  * A tile can only be shot from by one unit at a time. Attackers are matched
    to DISTINCT firing tiles, so a chokepoint that only one enemy can occupy
    is reported as one attacker and not as six.
  * Focus fire is sequenced, not summed. The defence term reads the defender's
    CURRENT displayed HP, so each hit lands into a weaker unit and is worth
    more than the last. Summing independent quotes understates a gang-up on
    starred terrain; the order is searched rather than assumed.
  * Enemy `acted` flags are IGNORED by default. The question is what happens
    over the opponent's next whole turn, by which point every one of their
    units has refreshed. Pass ignore_acted=False to ask what can hit you
    before the current turn ends.

WHAT IS NOT MODELLED, deliberately and visibly

  * Counterattacks weakening the attackers. A gang-up is scored as though your
    unit never hits back, so `worst_damage` is an UPPER bound. That is the
    direction that fails safe for "can I die here", and it is a real
    overstatement when your unit counters hard.
  * Whether fog is even on. `Board.fog` is None until the flag is located in
    RAM, and None is carried as UNKNOWN rather than collapsed to off -- an
    unknown board gets a warning saying the numbers assume clear. Pass
    fog=True and the enemy list drops to what you can legally see, with the
    unlit tiles that could hide the rest reported as `blind_spots`. The
    visibility rules themselves are engine/fog.py's assumptions, none of them
    measured yet.
  * CO powers firing. The charge is readable but the activation threshold is
    not known (see aw1_army_struct.json), so a power landing mid-turn is
    outside the model entirely.
  * Enemy production. Anything bought on their turn appears from nowhere.
  * Alliances. Every player that is not you is treated as hostile; the dump
    carries no team data.

Threat numbers inherit the CO caveats from co.py. If a CO's strength lives in
header fields the damage path has never been shown to read -- Kanbei, Sturm --
the numbers come out LOW, which is the dangerous direction, so it is reported
as a warning rather than swallowed.
"""
from __future__ import annotations

import dataclasses
import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

try:                                    # imported as engine.threat by tools/
    from . import co as co_mod
    from . import damage, fog as fog_mod, pathing
except ImportError:                     # imported as threat, engine/ on path
    import co as co_mod
    import damage
    import fog as fog_mod
    import pathing

Coord = Tuple[int, int]

# Above this many simultaneous attackers the ordering search stops being
# exhaustive. 6! is 720 orderings, and the per-attack damages are memoised
# across them, so this costs far less than it looks like it should.
EXHAUSTIVE_ORDERINGS = 6


# --------------------------------------------------------------------------
# geometry -- all of it driven by the three ROM range fields
# --------------------------------------------------------------------------

def manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def in_range(board, origin: Coord, lo: int, hi: int) -> Set[Coord]:
    """On-board tiles at Manhattan distance lo..hi from `origin`."""
    ox, oy = origin
    out = set()
    for dx in range(-hi, hi + 1):
        span = hi - abs(dx)
        for dy in range(-span, span + 1):
            if lo <= abs(dx) + abs(dy) <= hi:
                x, y = ox + dx, oy + dy
                if 0 <= x < board.width and 0 <= y < board.height:
                    out.add((x, y))
    return out


def firing_positions(board, unit, weather: Optional[str] = None) -> Dict[Coord, int]:
    """Tiles this unit could shoot FROM, mapped to what it costs to get there.

    Three cases, and all three come out of the stats table rather than out of
    a type test: unarmed units shoot from nowhere, units that may move and fire
    shoot from anywhere they can stop, and the rest shoot from where they are.

    Tiles occupied by another unit are dropped. They appear in `destinations()`
    because loading into a transport is a legal way to end a move, but a unit
    that loads has spent its turn and is not shooting anybody.
    """
    st = pathing.unit_stats(unit.type)
    if unit.loaded or not st["armed"]:
        return {}
    if not st["can_move_and_fire"]:
        return {(unit.x, unit.y): 0}
    here = (unit.x, unit.y)
    out = {}
    for tile, cost in pathing.destinations(board, unit, weather).items():
        blocker = board.unit_at(*tile)
        if tile == here or blocker is None:
            out[tile] = cost
    return out


def covered_tiles(board, unit, weather: Optional[str] = None) -> Dict[Coord, Set[Coord]]:
    """Every tile this unit could put a shot on -> the tiles it could shoot from.

    Geometry and reach only. Whether the shot is LEGAL depends on what is
    standing there, which is the damage matrices' business, not this one's.
    """
    st = pathing.unit_stats(unit.type)
    lo, hi = st["min_range"], st["max_range"]
    out: Dict[Coord, Set[Coord]] = {}
    for src in firing_positions(board, unit, weather):
        for tgt in in_range(board, src, lo, hi):
            out.setdefault(tgt, set()).add(src)
    return out


def hostiles(board, player: int, ignore_acted: bool = True,
             fog: bool = False, rule_set: Optional[dict] = None) -> List:
    """Enemy units that could still shoot. Every non-`player` army is hostile.

    Under fog this drops the ones the player cannot legally see. That makes the
    answer WEAKER on purpose -- a projection built on hidden units is the
    advisor telling you about an ambush you had no way of knowing about. What
    is lost has to be reported separately; see `FocusFire.blind_spots`.
    """
    out = [u for u in board.units
           if u.player != player and u.player != 0 and not u.loaded
           and (ignore_acted or not u.acted)]
    if fog:
        visible = {u.slot for u in fog_mod.visible_units(board, player, rule_set)}
        out = [u for u in out if u.slot in visible]
    # A submerged sub the player is not shown is dropped for the same
    # reason, fog or no fog (DERIVATION 41): the game will not offer it as
    # a target, and projecting its torpedoes would be advice built on a unit
    # the player cannot know is there. threats_to() warns when it happens.
    return [u for u in out if not fog_mod.concealed(board, u, player)]


def fog_active(board, fog: Optional[bool]) -> Optional[bool]:
    """Resolve the fog setting: explicit argument, else whatever the board knows.

    None propagates as None rather than collapsing to False, because the reader
    cannot yet tell fog from clear and "we do not know" must not silently
    become "there is none".
    """
    return board.fog if fog is None else fog


# --------------------------------------------------------------------------
# hypothetical placement
# --------------------------------------------------------------------------

def _relocate(board, unit, tile: Coord):
    """A copy of the board with `unit` standing at `tile`, cargo carried along.

    Enemy movement is computed against this, not against the real board: a
    tile is only genuinely safe if it is safe once your unit is standing in it
    plugging the gap, and the tile you left has to open back up.
    """
    if (unit.x, unit.y) == tile:
        return board, unit
    x, y = tile
    moved = dataclasses.replace(unit, x=x, y=y)
    # The game's observed visibility array describes the board as it stands.
    # Move a unit and it is a photograph of a position that no longer exists,
    # so the hypothetical board drops it and falls back to the rules. Carrying
    # it forward would make every "what if I stood here" answer use the sight
    # lines of where the unit actually is.
    riders = {unit.cargo} if unit.cargo else set()
    units = []
    for u in board.units:
        if u.slot == unit.slot:
            units.append(moved)
        elif u.slot in riders:
            units.append(dataclasses.replace(u, x=x, y=y))
        else:
            units.append(u)
    return dataclasses.replace(board, units=units, vision=None), moved


# --------------------------------------------------------------------------
# CO resolution
# --------------------------------------------------------------------------

def _co_of(board, player: int, co_ids: Optional[dict]) -> Optional[int]:
    if co_ids and player in co_ids:
        return co_ids[player]
    try:
        return board.army(player).co_id
    except (StopIteration, AttributeError):
        return None


def _modifier(board, unit, role: int, co_ids, warnings: list) -> int:
    """This unit's CO attack (role 0) or defence (role 1) percentage.

    Falls back to a neutral 100 when the CO is unknown, and says so once --
    silently assuming neutral is how a Max prediction comes out a third low.
    """
    cid = _co_of(board, unit.player, co_ids)
    if cid is None:
        note = (f"P{unit.player}'s CO is unknown -- this dump predates the "
                f"co_id field, so their modifiers are assumed neutral. Re-dump "
                f"with the current mgba_state.lua to fix it.")
        if note not in warnings:
            warnings.append(note)
        return 100
    gaps = co_mod.unmodelled(cid)
    if gaps:
        rec = co_mod.record(cid)
        note = (f"P{unit.player} is {rec.name}, whose strength lives in header "
                f"fields {sorted(gaps)} the damage path has never been shown "
                f"to read. Numbers involving them are UNDERSTATED.")
        if note not in warnings:
            warnings.append(note)
    try:
        return co_mod.modifiers(cid, unit.type)[role]
    except KeyError:
        return 100


# --------------------------------------------------------------------------
# threats
# --------------------------------------------------------------------------

def _note_fog(board, player: int, fog: Optional[bool], rule_set, warnings: list):
    """Say out loud which of the three fog states this answer was computed in.

    Unknown is its own state and gets its own warning. Treating it as "off"
    would produce a confident clear-board answer for a fogged game, which is
    the whole reason this module grew a fog parameter.
    """
    state = fog_active(board, fog)
    if state is None:
        note = ("fog of war is UNKNOWN for this board. The reader cannot detect "
                "it yet -- see tools/fog_hunt.py -- so these numbers assume a "
                "clear board, and under fog they would quote you units you have "
                "no way of seeing. Pass fog=True or fog=False to settle it.")
    elif state:
        hidden = len(fog_mod.hidden_tiles(board, player, rule_set))
        note = (f"fog of war is ON: only units you can currently see are "
                f"counted, and {hidden} tile(s) are unlit. Anything standing in "
                f"them is absent from every number here.")
    else:
        return
    if note not in warnings:
        warnings.append(note)


@dataclass(frozen=True)
class Threat:
    """One enemy unit's attack on one tile, already resolved to damage."""
    attacker: object
    firing_tiles: tuple          # every tile it could deliver this from
    target_tile: Coord
    outcome: object              # damage.Outcome
    # For a SUBMERGED defender: which measured branch of the game's reveal
    # check lets this attacker be shown the target when its menu opens --
    # "adjacent" (a unit of theirs already beside it), "property" (it sits
    # on their property) or "revealer" (another of their units can park
    # beside it first, DERIVATION 41 W3). None for a surfaced defender.
    revealed_by: Optional[str] = None

    @property
    def min_damage(self) -> int:
        return self.outcome.min_damage

    @property
    def max_damage(self) -> int:
        return self.outcome.max_damage


class _Damage:
    """Resolved damage against one defender on one tile, memoised over its HP.

    The ordering search re-asks the same questions hundreds of times, and
    display HP quantises to tens, so the cache collapses 720 orderings down to
    a handful of real evaluations.
    """

    def __init__(self, board, defender, tile: Coord, co_ids, warnings):
        self.board, self.defender, self.tile = board, defender, tile
        self.co_ids, self.warnings = co_ids, warnings
        move_type = pathing.unit_stats(defender.type)["move_type"]
        self.stars = board.defence_for(tile[0], tile[1], move_type)
        self.co_def = _modifier(board, defender, 1, co_ids, warnings)
        self._cache: dict = {}

    def outcome(self, attacker, defender_hp: int):
        key = (attacker.slot, defender_hp)
        if key not in self._cache:
            atk = damage.Attack(
                attacker=attacker.type, defender=self.defender.type,
                attacker_hp=attacker.hp, defender_hp=defender_hp,
                terrain_stars=self.stars, ammo=attacker.ammo,
                co_attack=_modifier(self.board, attacker, 0,
                                    self.co_ids, self.warnings),
                co_defense=self.co_def,
                # Dive states ride along (DERIVATION 31): a submerged
                # defender is reachable only through the dived table, and a
                # submerged attacker's counter is routed through it too.
                defender_dived=self.defender.dived,
                attacker_dived=attacker.dived,
            )
            self._cache[key] = damage.resolve(atk)
        return self._cache[key]


def threats_to(board, defender, tile: Optional[Coord] = None, *,
               co_ids: Optional[dict] = None, weather: Optional[str] = None,
               ignore_acted: bool = True, fog: Optional[bool] = None,
               fog_rules: Optional[dict] = None,
               warnings: Optional[list] = None,
               unseen: Optional[list] = None) -> List[Threat]:
    """Every enemy attack that could land on `defender` if it stood at `tile`.

    Defaults to where it actually stands. Enemy movement is recomputed against
    the hypothetical board, so blocking effects are real -- and so is the
    change in what you can SEE from there, since visibility is evaluated on the
    same hypothetical board.

    A SUBMERGED defender is further filtered by the game's reveal rule
    (`_reveal_filter`, DERIVATION 41); hunters that could reach it but would
    not be shown it are appended to `unseen` when a list is passed.
    """
    tile = tile or (defender.x, defender.y)
    warnings = warnings if warnings is not None else []
    hypo, moved = _relocate(board, defender, tile)
    dmg = _Damage(hypo, moved, tile, co_ids, warnings)
    _note_fog(hypo, defender.player, fog, fog_rules, warnings)
    _note_concealed(hypo, defender.player, warnings)

    out = []
    enemies = hostiles(hypo, defender.player, ignore_acted,
                       fog=bool(fog_active(hypo, fog)), rule_set=fog_rules)
    for enemy in enemies:
        sources = covered_tiles(hypo, enemy, weather).get(tile)
        if not sources:
            continue
        outcome = dmg.outcome(enemy, moved.hp)
        if outcome is None:              # no weapon can touch this target
            continue
        out.append(Threat(attacker=enemy, firing_tiles=tuple(sorted(sources)),
                          target_tile=tile, outcome=outcome))
    if moved.dived:
        out = _reveal_filter(hypo, moved, tile, out, enemies, weather, unseen)
    out.sort(key=lambda t: (-t.max_damage, t.attacker.slot))
    return out


def _note_concealed(board, player: int, warnings: list) -> None:
    n = len(fog_mod.concealed_units(board, player))
    if n:
        note = (f"{n} enemy sub(s) are submerged where you are not shown them "
                f"-- not drawn, not targetable, and their torpedoes are absent "
                f"from every number here (DERIVATION 41)")
        if note not in warnings:
            warnings.append(note)


def _reveal_filter(board, sub, tile: Coord, threats: List[Threat], enemies,
                   weather, unseen: Optional[list]) -> List[Threat]:
    """Which hunters get to fire at a SUBMERGED defender -- the game's reveal
    check (fog.concealed, DERIVATION 41) applied at each hunter's menu time.

    Measured: a hunter parked adjacent when its action starts fires (V2,
    W1); one that only becomes adjacent by moving does not (H2), because the
    check reads units where they stand; the sub on the hunter's side's
    property is shown to all of them (W2); and any unit of theirs that parks
    beside the sub first reveals it for a hunter that then moves in (W3).
    So a threat survives if the sub is already revealed on this board, or
    some OTHER enemy unit can end its move on a tile adjacent to the sub
    while leaving the hunter a firing tile of its own. Hunters this drops
    are appended to `unseen` when a list is given.

    Approximations, stated: the revealer is any hostile unit that can END on
    an adjacent tile (pathing.destinations), spending its own action to do
    it; only the two-tile distinctness between one revealer and the hunter
    is checked, not a full assignment across several hunters; alliances are
    `player != sub.player`, as everywhere.
    """
    x, y = tile
    adj = {(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)}
    adj = {t for t in adj if 0 <= t[0] < board.width and 0 <= t[1] < board.height}
    owner_here = board.owner[y][x]
    parked = {(e.x, e.y) for e in enemies} & adj
    reach_adj = {}
    for e in enemies:
        got = set(pathing.destinations(board, e, weather)) & adj
        if got:
            reach_adj[e.slot] = got
    kept = []
    for th in threats:
        h = th.attacker
        why = None
        if parked:
            why = "adjacent"
        elif owner_here == h.player:
            why = "property"
        else:
            mine = set(th.firing_tiles)
            for slot, tiles in reach_adj.items():
                if slot == h.slot:
                    continue
                if len(tiles | mine) >= 2:
                    why = "revealer"
                    break
        if why is None:
            if unseen is not None:
                unseen.append(th)
            continue
        kept.append(dataclasses.replace(th, revealed_by=why))
    return kept


# --------------------------------------------------------------------------
# focus fire
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FocusFire:
    tile: Coord
    delivered: tuple        # Threats that can all be delivered at once
    crowded_out: tuple      # Threats dropped for want of a distinct firing tile
    worst_damage: int       # every attacker rolls high, in the worst order
    best_damage: int        # every attacker rolls low, in the kindest order
    worst_remaining: int
    best_remaining: int
    lethal: bool            # the worst case kills
    certain_death: bool     # even the best case kills
    exact: bool             # whether the ordering search was exhaustive
    # Under fog, unlit tiles close enough that something parked there could
    # reach this one. NOT a prediction -- a count of the places the prediction
    # cannot see into, so a quiet report and a blind one look different.
    blind_spots: int = 0
    # For a SUBMERGED defender: hunters that could reach a firing tile but
    # would not be shown the sub when their menu opens (DERIVATION 41) --
    # dropped from the damage, listed so the reader can see what the dive
    # is buying and what would undo it (an enemy unit parking alongside).
    unseen: tuple = ()

    @property
    def attackers(self) -> int:
        return len(self.delivered)


def _match_distinct_tiles(threats: Sequence[Threat]):
    """Assign each attacker its own firing tile; drop those that cannot get one.

    Kuhn's algorithm, heaviest hitters first. Two enemies wanting the single
    tile beside a chokepoint means one of them does not get to shoot, and
    scoring both is how a tool talks a player out of the best square on the map.
    """
    ordered = sorted(threats, key=lambda t: (-t.max_damage, t.attacker.slot))
    owner: Dict[Coord, int] = {}
    by_index = {i: t.firing_tiles for i, t in enumerate(ordered)}

    def assign(i: int, seen: set) -> bool:
        for tile in by_index[i]:
            if tile in seen:
                continue
            seen.add(tile)
            if tile not in owner or assign(owner[tile], seen):
                owner[tile] = i
                return True
        return False

    placed = {i for i in range(len(ordered)) if assign(i, set())}
    return ([t for i, t in enumerate(ordered) if i in placed],
            [t for i, t in enumerate(ordered) if i not in placed])


def _chain(dmg: _Damage, start_hp: int, order: Sequence[Threat], high: bool) -> int:
    """Total damage from attacking in this order. Sequenced, because the
    defence term reads current HP -- each hit lands into a weaker unit."""
    hp, total = start_hp, 0
    for th in order:
        if hp <= 0:
            break
        outcome = dmg.outcome(th.attacker, hp)
        if outcome is None:
            continue
        step = min(outcome.max_damage if high else outcome.min_damage, hp)
        hp -= step
        total += step
    return total


def focus_fire(board, defender, tile: Optional[Coord] = None, *,
               co_ids: Optional[dict] = None, weather: Optional[str] = None,
               ignore_acted: bool = True, fog: Optional[bool] = None,
               fog_rules: Optional[dict] = None,
               warnings: Optional[list] = None) -> FocusFire:
    """Worst and best case for `defender` standing at `tile` over one enemy turn.

    `worst_damage` is an upper bound: attackers are never weakened by the
    counterattacks they would eat. See the module docstring.
    """
    tile = tile or (defender.x, defender.y)
    warnings = warnings if warnings is not None else []
    unseen: list = []
    threats = threats_to(board, defender, tile, co_ids=co_ids, weather=weather,
                         ignore_acted=ignore_acted, fog=fog,
                         fog_rules=fog_rules, warnings=warnings, unseen=unseen)
    delivered, crowded = _match_distinct_tiles(threats)

    hypo, moved = _relocate(board, defender, tile)
    dmg = _Damage(hypo, moved, tile, co_ids, warnings)
    blind = (len(fog_mod.blind_spots(hypo, defender.player, tile, fog_rules))
             if fog_active(hypo, fog) else 0)

    if not delivered:
        return FocusFire(tile=tile, delivered=(), crowded_out=tuple(crowded),
                         worst_damage=0, best_damage=0,
                         worst_remaining=moved.hp, best_remaining=moved.hp,
                         lethal=False, certain_death=False, exact=True,
                         blind_spots=blind, unseen=tuple(unseen))

    exact = len(delivered) <= EXHAUSTIVE_ORDERINGS
    if exact:
        orders = list(itertools.permutations(delivered))
    else:
        # Big attacks want to land last, into the highest defence multiplier,
        # so ascending is the shape of the worst case. Kept honest by reporting
        # exact=False rather than by claiming the search was complete.
        by_size = sorted(delivered, key=lambda t: t.max_damage)
        orders = [tuple(by_size), tuple(reversed(by_size)), tuple(delivered)]

    worst = max(_chain(dmg, moved.hp, o, True) for o in orders)
    best = min(_chain(dmg, moved.hp, o, False) for o in orders)
    return FocusFire(
        tile=tile, delivered=tuple(delivered), crowded_out=tuple(crowded),
        worst_damage=worst, best_damage=best,
        worst_remaining=max(0, moved.hp - worst),
        best_remaining=max(0, moved.hp - best),
        lethal=worst >= moved.hp, certain_death=best >= moved.hp,
        exact=exact, blind_spots=blind, unseen=tuple(unseen),
    )


# --------------------------------------------------------------------------
# the advisor-facing questions
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TileRisk:
    tile: Coord
    move_cost: int
    terrain: str
    stars: int
    focus: FocusFire


def safety(board, unit, *, co_ids: Optional[dict] = None,
           weather: Optional[str] = None, ignore_acted: bool = True,
           fog: Optional[bool] = None, fog_rules: Optional[dict] = None,
           warnings: Optional[list] = None) -> List[TileRisk]:
    """Every tile this unit can end on, worst case first cheapest.

    This is the question a player actually asks -- not "how much does that
    trade cost" but "where can I put this thing down without losing it".
    """
    warnings = warnings if warnings is not None else []
    move_type = pathing.unit_stats(unit.type)["move_type"]
    out = []
    for tile, cost in pathing.destinations(board, unit, weather).items():
        ff = focus_fire(board, unit, tile, co_ids=co_ids, weather=weather,
                        ignore_acted=ignore_acted, fog=fog,
                        fog_rules=fog_rules, warnings=warnings)
        out.append(TileRisk(
            tile=tile, move_cost=cost,
            terrain=board.terrain_name(*tile),
            stars=board.defence_for(tile[0], tile[1], move_type),
            focus=ff))
    out.sort(key=lambda r: (r.focus.worst_damage, r.move_cost, r.tile))
    return out


def threat_map(board, player: int, *, unit_type: Optional[str] = None,
               weather: Optional[str] = None, ignore_acted: bool = True,
               fog: Optional[bool] = None,
               fog_rules: Optional[dict] = None,
               dived: bool = False) -> Dict[Coord, List]:
    """Tile -> the enemy units that could put a shot on it, as the board stands.

    Coverage, not damage: what a tile costs you depends on what you park there.
    Pass `unit_type` to count only enemies whose weapons can actually hurt that
    kind of unit -- without it a Lander looks threatened by an Anti-Air.
    `dived=True` asks the question for a SUBMERGED sub: only Cruisers and
    Subs pass the filter, per the dived table (DERIVATION 31).

    Your own units are where they are, so this is the map for the board as it
    is now. `safety()` is the one that re-runs the enemy searches against a
    hypothetical placement.
    """
    out: Dict[Coord, List] = {}
    for enemy in hostiles(board, player, ignore_acted,
                          fog=bool(fog_active(board, fog)), rule_set=fog_rules):
        if unit_type is not None and not damage.can_attack(
                enemy.type, unit_type, enemy.ammo, defender_dived=dived):
            continue
        for tile in covered_tiles(board, enemy, weather):
            out.setdefault(tile, []).append(enemy)
    return out

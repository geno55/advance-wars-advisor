"""The first opinion: a one-turn greedy planner with sequential commit.

Everything below engine/actions.py and engine/sim.py is a fact the game can
be asked to check. Nothing here is. This module is the opinion layer the
handoff drew the line for: it consumes Action records, puts a NUMBER on each,
commits the best, advances the board through sim.apply, and repeats until
the turn is spent. Every number it prints is either a fact it is quoting
(and each Term says which) or a weight from the one table at the top of this
file, labelled heuristic. Do not let the two blur: no module below this one
grows a score field, and no weight here is ever called measured.

THE LOOP (ROADMAP step 1)

  1. enumerate: every action of every unit that can still act, every
     affordable build at every empty own factory, and the CO power when the
     meter is there -- all facts, from actions.all_actions / build_actions /
     power_action on the board AS IT NOW STANDS;
  2. score each candidate as a sum of named Terms, each Term = weight x a
     quantity read off the action's facts, all in funds so they add;
  3. commit the best, advance the board with sim.apply(luck="min") -- the
     same worst-case world the action layer scores exposure in -- and go
     back to 1 with the units that remain;
  4. stop when no unit can act and no build or power scores above zero.
     The plan is the committed sequence, in order, ending with End Turn.

THE REPLY (ROADMAP step 4)

  The loop above scores each action against the action layer's worst case:
  every enemy converges on the ending tile and none is weakened on the
  way. With `reply` set, the loop becomes the PROPOSER and a modelled
  opponent the ARBITER:

  1. propose: the greedy plan, and then `branches` variants -- at each of
     the closest calls (the steps where the same actor's next-best action
     scored nearest the winner; those where the worst case had a say -- a
     damage, kill, loss or capture term differs between the two -- first)
     that alternative is committed instead and the rest of the turn
     re-planned greedily from there. The SAME actor's, not the overall
     runner-up: that is usually another unit's best move, and forcing it
     first only reorders the plan onto the same board;
  2. reply: for each candidate, End Turn (sim.end_turn), then the
     opponent's whole turn -- engine/cpu_ai.predict, the game's own AI
     ported routine by routine, when the opponent is the CPU
     (reply="cpu", needs a cpu_ai.Context from the dump); this planner,
     one ply and worst case for its own actor, when it is not
     (reply="planner"); the planner stands in whenever the port meets a
     branch it has not read (NotImplementedError), and says so -- then
     End Turn again, so the board is the one at OUR next turn start;
  3. evaluate: that board from our side, as named terms in funds
     (`evaluate`): material, treasury, income over the horizon, captures
     in hand, the HQ and the rout;
  4. choose the candidate whose reply scores best; ties keep the greedy
     plan. The Plan carries the reply (what the opponent did, the board,
     the terms) and every candidate with its reply score.

  What this does and does not fix: the exposure a candidate is charged is
  now what the modelled opponent DOES, not what every enemy could do at
  once, and the opponent's captures and builds count -- but only the
  runner-up at a close call is ever tried, the lookahead is two plies, and
  the reply is only as right as the model: the CPU port where it has been
  traced, this planner's own worst-case greed elsewhere.

WHAT THE TERMS READ (facts) AND WHAT THEY ASSUME (weights)

  damage_dealt    the worst-case strike (Outcome.min_damage) in display bars
                  taken off the target, times the target's bar value
                  (co.unit_value: cost/10 x the CO's value multiplier)
  kill            a GUARANTEED kill (Outcome.guaranteed_kill) is worth a
                  fraction of the target's price on top -- weight `kill`
  damage_taken    the counter at its worst (Outcome.max_damage) plus next
                  enemy turn's focus fire on the ending tile
                  (FocusFire.worst_damage), in the actor's bars and bar
                  value, multiplied by 1 + army_share x (this unit's value /
                  the army's), so the last Tank is guarded harder than the
                  fifth
  loss            when that worst case kills (FocusFire.lethal, or a counter
                  that guarantees it), a fraction of the actor's price --
                  the mirror of `kill`
  capture         capture points gained this action (Action.progress_after
                  minus the unit's progress) as a fraction of 20, times the
                  property's worth: the funds rate x capture_horizon days,
                  doubled when the tile is an enemy's (they lose it too);
                  points ABANDONED by stepping off a half-taken property
                  (A15: moving resets progress) count against; an HQ that
                  falls this turn scores `win`
  objective       a small pull: movement points closer to the unit's
                  objective along terrain costs (pathing over the movement
                  table, units ignored), x objective_pull funds each.
                  Foot units head for the nearest property not theirs,
                  armed units for the nearest visible enemy, transports for
                  the enemy's properties
  property_exposure
                  the mirror of `capture`, read before and after the action:
                  every own property an enemy FOOT unit can end its next
                  move on (pathing.destinations, the game's own fill, on
                  the board the action leaves -- a tile we stand on is not
                  reachable, a pass we plug is closed, a target we kill is
                  gone) x the property's worth to that enemy x the share
                  it can hold there by the end of that turn (points in
                  hand plus actions._capture_gain, the ROM's bars-plus-CO-
                  shift) / 20. The term is the CHANGE the action makes,
                  so a wait scores nothing and only stepping off, plugging
                  or shooting moves it
  hq_exposure     the same for an HQ, but at the win's value: `win` x the
                  share of the HQ the enemy can hold by the end of its
                  next turn, x hq_exposure. That is what makes a lone
                  enemy Infantry beside an empty HQ an emergency and not
                  a 6,000-funds city
  turn_start      what the next morning does on that tile (supply.TurnStart):
                  bars repaired x bar value, the repair's funds charge
                  against, a resupply as a fraction of the unit's price when
                  it was short, and a crash as the whole unit
  resupply        each unit a Supply action refills, as the fraction of its
                  fuel and ammo it was missing x its price
  refund          a join's refund (join.Merge.refund), funds, verbatim
  build           per offer: the average over visible enemies of (best base
                  damage this type deals them / 100) x their price
                  (build_matchup); for foot units the property worth x
                  min(1, unowned properties / (own foot units + 1))
                  (build_capture); the price against (build_spend); and a
                  fixed early bias per type (build_bias, BUILD_BIAS)
  power           Andy's heals as repair, Eagle's refreshes as a fraction of
                  each unit's price, Drake's damage as damage_dealt, Sturm's
                  meteor at its WORST strategy for us (the RNG is unread),
                  and the power stat block as (attack% - 100) of the army's
                  value for the turn; Olaf's snow, Sami's roads and Grit's
                  range are noted and score nothing (unmodelled here)

Ties break on the filing order (kind, the actor's tile, the ending tile,
the target's tile) -- never on slot numbers, so the plan is the same under
slot renumbering and board translation; tests/test_advisor.py holds it to
that and to a neutral-CO board planning exactly as Andy.

WHERE THIS IS NAIVE, on purpose (docs/ADVISOR.md says more)

  * one enemy unit's exposure is scored per action, so two of your units
    parked in the same enemy's reach are each charged for it -- the sum
    over a plan overcounts focus fire, in the safe direction;
  * greedy: the best first action, then the best second -- never the pair
    that is best together. A unit that could kill after a softener commits
    the softener only if it scores best on its own;
  * one turn, unless `reply` is set: then two plies, the second the
    modelled opponent's, and only one alternative per close call is
    tried against it;
  * traps (kind "trap") are never chosen: walking into a known ambush is
    not a plan, and the stop tile is available as a plain wait.

The shared-delusion caveat for anyone tuning these weights by self-play: a
bug in sim.apply is a belief both planners hold, and self-play will
converge on exploiting it. That is why the emulator budget goes to the
differential test of apply() (ROADMAP step 2), not to this file.
"""
from __future__ import annotations

import dataclasses
import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:                                    # imported as engine.advisor
    from . import actions as actions_mod
    from . import co as co_mod
    from . import cpu_ai
    from . import damage, economy, pathing, sim as sim_mod
    from . import supply as supply_mod, threat
except ImportError:                     # imported as advisor, engine/ on path
    import actions as actions_mod
    import co as co_mod
    import cpu_ai
    import damage
    import economy
    import pathing
    import sim as sim_mod
    import supply as supply_mod
    import threat

Coord = Tuple[int, int]

# --------------------------------------------------------------------------
# THE WEIGHTS. Heuristic, every one. Nothing in this table was measured on
# the game and nothing can be; they are the opinion. Units: a weight of 1.0
# means "one funds-worth of the quantity is worth one funds of score".
# --------------------------------------------------------------------------
WEIGHTS: Dict[str, float] = {
    "damage_dealt": 1.0,      # x funds of enemy bars taken (worst case)
    "kill": 0.5,              # x target price, on a guaranteed kill
    "damage_taken": 1.0,      # x funds of own bars lost (counter + exposure)
    "army_share": 1.0,        # damage_taken x (1 + this x unit's share of army)
    "loss": 0.5,              # x own price, when the worst case kills
    "capture": 1.0,           # x property worth x points gained / 20
    "capture_horizon": 6,     # DAYS of income a property is worth
    "enemy_property": 2.0,    # a property taken FROM an enemy is worth this x
    "win": 1_000_000,         # an HQ that falls this turn
    "objective_pull": 40,     # funds per movement point closer
    "property_exposure": 1.0, # x a property's worth x the share of it an
                              #   enemy foot unit can hold by the end of
                              #   its next turn (delta per action)
    "hq_exposure": 0.1,       # x win x the share of an HQ the enemy can
                              #   hold by the end of its next turn
    "repair": 1.0,            # x funds of bars the morning repairs
    "repair_spend": 0.5,      # x funds the repair charges
    "resupply": 0.3,          # x price x fraction of fuel+ammo restored
    "crash": 1.0,             # x own unit value, when the morning removes it
    "refund": 1.0,            # x a join's refund
    "build_matchup": 0.5,     # x avg (base/100 x enemy price) over enemies
    "build_capture": 1.0,     # x property worth x unowned/(foot+1), foot only
    "build_spend": 0.3,       # x the price, against
    "build_bias": 1.0,        # x BUILD_BIAS[type]
    "power_refresh": 0.3,     # x price per unit Eagle refreshes
    "power_block": 0.5,       # x army value x (attack% - 100)/100
    # -- the reply's evaluation (ROADMAP step 4): the board at our next
    # -- turn start, from our side, after the modelled opponent has moved
    "material": 1.0,          # x (our units' value - the enemy's)
    "treasury": 0.7,          # x (our funds - the enemy's)
    "income": 1.0,            # x (our income - the enemy's) x capture_horizon
}

# The fixed early table the roadmap asked for: funds added to a build's
# score per type, before the matchup terms. Transports need cargo the
# planner cannot yet plan for, so they are held back rather than bought
# for their (zero) matchup value alone.
BUILD_BIAS: Dict[str, int] = {
    "Infantry": 500,
    "APC": -1500,
    "TCopter": -2000,
    "Lander": -4000,
}

TERRAIN_HQ = 8


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Term:
    """One named contribution to a score: weight x quantity, and the fact
    the quantity was read from (a sentence quoting the action's numbers)."""
    name: str
    weight: float
    quantity: float           # in funds, before the weight
    fact: str

    @property
    def value(self) -> float:
        return self.weight * self.quantity


@dataclass(frozen=True)
class Scored:
    action: object            # actions.Action
    terms: Tuple[Term, ...]

    @property
    def score(self) -> float:
        return sum(t.value for t in self.terms)


@dataclass(frozen=True)
class Step:
    """One committed action: the board it was scored on, the score, and the
    board sim.apply left behind (worst case for the actor)."""
    index: int
    scored: Scored
    board_before: object
    board_after: object
    runner_up: Optional[Scored] = None    # the next best candidate, if any
    # the same actor's next-best action on that board (another build for a
    # build, nothing for the power): what a variant proposal commits instead
    alternative: Optional[Scored] = None

    @property
    def action(self):
        return self.scored.action


@dataclass(frozen=True)
class Reply:
    """The opponents' modelled turns after a candidate plan, and the board
    they leave -- at the planner's NEXT turn start -- evaluated from the
    planner's side (ROADMAP step 4)."""
    opponents: Tuple[int, ...]
    model: str                  # "cpu" (engine/cpu_ai), "planner" (this
                                # module, one ply), or "mixed"
    described: Tuple[str, ...]  # what the opponents did, one line each
    board_after: object
    terms: Tuple[Term, ...]     # evaluate() on board_after
    note: str = ""              # why the asked-for model was not used

    @property
    def score(self) -> float:
        return sum(t.value for t in self.terms)


@dataclass
class Candidate:
    """One proposed turn and the reply it drew."""
    label: str
    steps: List[Step]
    board_after: object
    reply: Optional[Reply] = None
    chosen: bool = False

    @property
    def score(self) -> float:          # the proposal's own total
        return sum(s.scored.score for s in self.steps)


@dataclass
class Plan:
    player: int
    steps: List[Step]
    board_after: object       # the board once every step is committed
    warnings: List[str] = field(default_factory=list)
    weights: Dict[str, float] = field(default_factory=dict)
    # with `reply` set: the modelled reply to the chosen plan, every
    # candidate that was proposed with its own reply, and the start board
    # evaluated the same way so the reply's score can be read as a change
    reply: Optional[Reply] = None
    candidates: List[Candidate] = field(default_factory=list)
    baseline: Tuple[Term, ...] = ()

    @property
    def baseline_score(self) -> float:
        return sum(t.value for t in self.baseline)

    @property
    def score(self) -> float:
        return sum(s.scored.score for s in self.steps)


# --------------------------------------------------------------------------
# values in funds
# --------------------------------------------------------------------------

def _co_of(board, player: int, co_ids: Optional[dict]) -> Optional[int]:
    if co_ids and player in co_ids:
        return co_ids[player]
    try:
        return board.army(player).co_id
    except (StopIteration, AttributeError):
        return None


def _power_of(board, player: int) -> bool:
    try:
        return bool(board.army(player).power_active)
    except (StopIteration, AttributeError):
        return False


def price(unit_type: str) -> int:
    return pathing.unit_stats(unit_type)["cost"]


def bar_value(board, unit, co_ids: Optional[dict] = None) -> int:
    """Funds per display bar of this unit: the game's own unit-value
    arithmetic (co.unit_value, cost/10 x the CO's header multiplier), or
    cost/10 when the CO is unknown -- the fallback sim.battle makes."""
    cid = _co_of(board, unit.player, co_ids)
    cost = price(unit.type)
    if cid is None:
        return cost // 10
    return co_mod.unit_value(cost, cid, _power_of(board, unit.player))


def unit_worth(board, unit, co_ids=None) -> int:
    """This unit's remaining value: bars x bar value."""
    return bar_value(board, unit, co_ids) * damage.screen_bars(unit.hp)


def army_worth(board, player: int, co_ids=None) -> int:
    return sum(unit_worth(board, u, co_ids) for u in board.units
               if u.player == player)


def bars_lost(hp_before: int, hp_after: int) -> int:
    return damage.screen_bars(hp_before) - damage.screen_bars(max(0, hp_after))


def property_worth(board, x: int, y: int, player: int, w: dict) -> float:
    """What owning the tile at (x, y) is worth to `player`: the funds rate
    over the horizon, doubled when an enemy holds it now."""
    rate, _ = economy.funds_rate(board)
    worth = rate * w["capture_horizon"]
    owner = board.owner[y][x]
    if owner and owner != player:
        worth *= w["enemy_property"]
    return worth


# --------------------------------------------------------------------------
# the objective pull: distance fields over terrain costs, units ignored
# --------------------------------------------------------------------------

_FIELD_CACHE: Dict[tuple, dict] = {}
_FIELD_CACHE_MAX = 64


def distance_field(board, targets, move_type: str,
                   weather: Optional[str] = None) -> Dict[Coord, int]:
    """Tile -> movement points from there to the nearest target, for this
    move type over the board's terrain (the weather's table), units
    ignored. Unreachable tiles are absent. Cost of a route is the sum of
    the entry costs of the tiles after the first, so the field is a
    multi-source Dijkstra relaxed with the cost of the tile being LEFT."""
    key = (tuple(map(tuple, board.terrain)), frozenset(targets), move_type,
           weather, board.weather_index)
    hit = _FIELD_CACHE.get(key)
    if hit is not None:
        return hit
    best: Dict[Coord, int] = {t: 0 for t in targets}
    queue = [(0, t) for t in targets]
    heapq.heapify(queue)
    while queue:
        d, (x, y) = heapq.heappop(queue)
        if d > best.get((x, y), 1 << 30):
            continue
        leave = board.move_cost(x, y, move_type, weather)
        if leave is None:
            leave = 0                      # a target on impassable ground
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (0 <= nx < board.width and 0 <= ny < board.height):
                continue
            if board.move_cost(nx, ny, move_type, weather) is None:
                continue
            nd = d + leave
            if nd < best.get((nx, ny), 1 << 30):
                best[(nx, ny)] = nd
                heapq.heappush(queue, (nd, (nx, ny)))
    if len(_FIELD_CACHE) >= _FIELD_CACHE_MAX:
        _FIELD_CACHE.clear()
    _FIELD_CACHE[key] = best
    return best


def objective_tiles(board, unit, fog_on: bool, fog_rules=None) -> tuple:
    """Where this unit is pulled toward, and why -- (tiles, label).

    Foot units: every capturable tile not the owner's. Armed units: the
    visible enemies. Transports: the enemy's properties. Each falls back
    down the list when its own set is empty."""
    st = pathing.unit_stats(unit.type)
    props_not_mine = {(x, y) for y in range(board.height)
                      for x in range(board.width)
                      if board.terrain[y][x] in actions_mod._capturable()
                      and board.owner[y][x] != unit.player}
    enemy_props = {(x, y) for (x, y) in props_not_mine
                   if board.owner[y][x] != 0}
    enemies = {(e.x, e.y) for e in threat.hostiles(
        board, unit.player, ignore_acted=True, fog=fog_on, rule_set=fog_rules)}
    if st["unit_class"] == "foot":
        order = ((props_not_mine, "nearest property to take"),
                 (enemies, "nearest enemy"))
    elif st["armed"]:
        order = ((enemies, "nearest visible enemy"),
                 (enemy_props, "nearest enemy property"))
    else:
        order = ((enemy_props, "nearest enemy property"),
                 (enemies, "nearest visible enemy"))
    for tiles, label in order:
        if tiles:
            return frozenset(tiles), label
    return frozenset(), "no objective on this board"


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

class Context:
    """Per-board things the terms share: the weights, the CO map, the fog
    setting and the army's value -- computed once per round, not per
    action."""

    def __init__(self, board, player: int, *, weights=None, co_ids=None,
                 weather=None, fog=None, fog_rules=None, warnings=None):
        self.board = board
        self.player = player
        self.w = dict(WEIGHTS)
        if weights:
            unknown = set(weights) - set(WEIGHTS)
            if unknown:
                raise KeyError(f"unknown weight(s) {sorted(unknown)}; the "
                               f"table is advisor.WEIGHTS")
            self.w.update(weights)
        self.co_ids = co_ids
        self.weather = weather
        self.fog = fog
        self.fog_on = bool(threat.fog_active(board, fog))
        self.fog_rules = fog_rules
        self.warnings = warnings if warnings is not None else []
        self.army_value = army_worth(board, player, co_ids) or 1
        self._exposure: Optional[Exposure] = None
        self._exposure_memo: Dict[tuple, Exposure] = {}
        self._foot_slots: Optional[frozenset] = None

    def share(self, unit) -> float:
        return unit_worth(self.board, unit, self.co_ids) / self.army_value

    # -- property exposure, resolved once per board: the enemy foot units
    # -- this side can see are decided here, on the board as it stands,
    # -- and every hypothetical is asked about the same units
    @property
    def foot_slots(self) -> frozenset:
        if self._foot_slots is None:
            self._foot_slots = frozenset(
                e.slot for e in threat.hostiles(self.board, self.player,
                                                ignore_acted=True, fog=self.fog_on,
                                                rule_set=self.fog_rules)
                if pathing.unit_stats(e.type)["unit_class"] == "foot")
        return self._foot_slots

    def exposure_of(self, board) -> Exposure:
        key = tuple(sorted((u.slot, u.x, u.y, u.hp, u.loaded) for u in board.units))
        hit = self._exposure_memo.get(key)
        if hit is None:
            hit = Exposure.of(board, self.player, self.w, self.co_ids,
                              weather=self.weather, warnings=self.warnings,
                              slots=self.foot_slots)
            self._exposure_memo[key] = hit
        return hit

    @property
    def exposure(self) -> Exposure:
        if self._exposure is None:
            self._exposure = self.exposure_of(self.board)
        return self._exposure


def _damage_terms(ctx: Context, a, actor, hp_start: int, exposure,
                  counter=None, lethal_now: bool = False) -> List[Term]:
    """damage_taken and loss for `actor` ending on a's tile: the counter
    (when there is one), then next turn's focus fire on the survivor."""
    w = ctx.w
    bv = bar_value(ctx.board, actor, ctx.co_ids)
    mult = 1 + w["army_share"] * ctx.share(actor)
    out = []
    lost, facts = 0, []
    hp = hp_start
    if counter is not None:
        got = bars_lost(hp, counter.min_remaining_hp)
        hp = counter.min_remaining_hp
        lost += got
        facts.append(f"counter {counter.min_damage}-{counter.max_damage} "
                     f"({got} bar{'s' if got != 1 else ''} at worst)")
    dead = lethal_now or hp <= 0
    if not dead and exposure is not None:
        got = bars_lost(hp, exposure.worst_remaining)
        lost += got
        if exposure.delivered:
            facts.append(f"next turn {exposure.best_damage}-"
                         f"{exposure.worst_damage} from {exposure.attackers} "
                         f"attacker{'s' if exposure.attackers != 1 else ''} "
                         f"({got} bar{'s' if got != 1 else ''} at worst)")
        else:
            facts.append("untouched next turn")
        if exposure.blind_spots:
            facts.append(f"{exposure.blind_spots} unlit tiles in reach")
        dead = exposure.lethal
    elif not dead and exposure is None:
        facts.append("no exposure quoted")
    if lost:
        out.append(Term("damage_taken", w["damage_taken"],
                        -lost * bv * mult,
                        f"{'; '.join(facts)} x {bv}/bar x {mult:.2f} "
                        f"army share"))
    if dead:
        out.append(Term("loss", w["loss"], -price(actor.type),
                        f"worst case kills this {actor.type} "
                        f"(price {price(actor.type)})"))
    return out


def _turn_start_terms(ctx: Context, actor, hp_now: int, ts) -> List[Term]:
    if ts is None:
        return []
    w = ctx.w
    out = []
    if ts.crashes:
        out.append(Term("crash", w["crash"],
                        -unit_worth(ctx.board, actor, ctx.co_ids),
                        f"fuel {ts.fuel_after} after the burn of {ts.burn}: "
                        f"removed at turn start"))
        return out
    bv = bar_value(ctx.board, actor, ctx.co_ids)
    healed = bars_lost(ts.hp_after, hp_now) if ts.hp_after > hp_now else 0
    if healed:
        out.append(Term("repair", w["repair"], healed * bv,
                        f"repairs {damage.screen_bars(hp_now)} -> "
                        f"{damage.screen_bars(ts.hp_after)} bars at turn start"))
    if ts.repair_spent:
        out.append(Term("repair_spend", w["repair_spend"], -ts.repair_spent,
                        f"the repair charges {ts.repair_spent}"))
    if ts.serviced or ts.auto_supplied:
        frac = _short_fraction(actor.type, actor.fuel, actor.ammo)
        if frac > 0:
            out.append(Term("resupply", w["resupply"],
                            frac * price(actor.type),
                            f"{'APC' if ts.auto_supplied and not ts.serviced else 'property'} "
                            f"refills fuel {actor.fuel} -> {ts.fuel_after}, "
                            f"ammo {actor.ammo} -> {ts.ammo_after} "
                            f"({frac:.0%} of the tanks was empty)"))
    return out


def _short_fraction(unit_type: str, fuel: int, ammo: int) -> float:
    """How empty a unit's fuel and ammo are, 0..1, averaged over the gauges
    the unit has."""
    max_fuel, max_ammo = supply_mod.resupply_caps(unit_type)
    parts = []
    if max_fuel:
        parts.append(1 - min(fuel, max_fuel) / max_fuel)
    if max_ammo:
        parts.append(1 - min(ammo, max_ammo) / max_ammo)
    return sum(parts) / len(parts) if parts else 0.0


def _objective_term(ctx: Context, unit, end_tile: Coord) -> List[Term]:
    if end_tile == (unit.x, unit.y):
        return []
    tiles, label = objective_tiles(ctx.board, unit, ctx.fog_on, ctx.fog_rules)
    if not tiles:
        return []
    move_type = pathing.unit_stats(unit.type)["move_type"]
    fieldd = distance_field(ctx.board, tiles, move_type, ctx.weather)
    before = fieldd.get((unit.x, unit.y))
    after = fieldd.get(end_tile)
    if before is None or after is None or before == after:
        return []
    closer = before - after
    return [Term("objective", ctx.w["objective_pull"], closer,
                 f"{label}: {before} -> {after} movement points away")]


# --------------------------------------------------------------------------
# property exposure: which of a side's properties a foot unit of the other
# side can step onto next turn, and how much of each it can hold by then
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PropertyThreat:
    """One property an enemy foot unit can end its next move on: the tile,
    its terrain, the unit, and the capture it can hold there by the end
    of that turn -- points already in hand plus one turn's gain."""
    tile: Coord
    terrain: int
    unit: object
    held_after: int


def property_threats(board, player: int, *, co_ids=None, weather=None,
                     fog: bool = False, fog_rules=None, warnings=None,
                     slots=None) -> List[PropertyThreat]:
    """For every property `player` owns, the foot unit of another army that
    can end its next move on it and the capture it can hold there by the end
    of that turn. Facts only: the reach is pathing.destinations, the game's
    own fill, on this board as it stands -- so a tile one of `player`'s units
    occupies is not reachable, nor one behind a plugged pass; the gain is
    actions._capture_gain, the ROM's bars plus the CO's shift. Enemy `acted`
    flags are ignored (next turn they refresh). Under fog only the units
    `player` can legally see count, as everywhere else; `slots` restricts
    the enemies further (a caller that resolved visibility on another
    board). A foot unit riding a transport is not seen. One unit per tile:
    the one that would hold the most."""
    warnings = warnings if warnings is not None else []
    capturable = actions_mod._capturable()
    mine = [(x, y) for y in range(board.height) for x in range(board.width)
            if board.owner[y][x] == player and board.terrain[y][x] in capturable]
    if not mine:
        return []
    feet = [e for e in threat.hostiles(board, player, ignore_acted=True,
                                       fog=fog, rule_set=fog_rules)
            if pathing.unit_stats(e.type)["unit_class"] == "foot"
            and (slots is None or e.slot in slots)]
    best: Dict[Coord, PropertyThreat] = {}
    for e in feet:
        reach = pathing.allowance(e)
        near = [t for t in mine if abs(t[0] - e.x) + abs(t[1] - e.y) <= reach]
        if not near:
            continue
        dests = pathing.destinations(board, e, weather)
        gain = actions_mod._capture_gain(board, e, co_ids, warnings)
        for t in near:
            if t not in dests:
                continue
            held = e.capture if (e.x, e.y) == t else 0
            after = min(actions_mod.CAPTURE_GOAL, held + gain)
            cur = best.get(t)
            if cur is None or after > cur.held_after:
                best[t] = PropertyThreat(t, board.terrain[t[1]][t[0]], e, after)
    return [best[t] for t in sorted(best)]


@dataclass(frozen=True)
class Exposure:
    """A side's property exposure in funds, before any weight: the
    properties' worth x the share the enemy can hold, and the HQs' at the
    win's value, with one line per threatened tile."""
    properties: float
    hq: float
    facts: Tuple[str, ...]

    @staticmethod
    def of(board, player: int, w: dict, co_ids=None, **kw) -> "Exposure":
        props, hq, facts = 0.0, 0.0, []
        goal = actions_mod.CAPTURE_GOAL
        for t in property_threats(board, player, co_ids=co_ids, **kw):
            x, y = t.tile
            e = t.unit
            if t.terrain == TERRAIN_HQ:
                worth = w["win"]
                hq += worth * t.held_after / goal
            else:
                worth = property_worth(board, x, y, e.player, w)
                props += worth * t.held_after / goal
            facts.append(f"{board.terrain_name(x, y)} at ({x},{y}): P{e.player} "
                         f"{e.type} #{e.slot} can hold {t.held_after}/{goal} "
                         f"by the end of its turn (worth {worth:.0f})")
        return Exposure(props, hq, tuple(facts))

    @property
    def summary(self) -> str:
        return "; ".join(self.facts) if self.facts else "none"


def _hypothetical(ctx: "Context", a):
    """The board as it stands once `a` is taken, for the enemy's reach only:
    the actor on its ending tile, a join's or load's actor gone, a drop's
    passenger set down, a built unit on its factory, and an attack's target
    at its worst-case remaining hp (gone when that is zero). None for the
    power, which moves nothing."""
    b = ctx.board
    if a.kind == "power":
        return None
    if a.kind == "build":
        return dataclasses.replace(b, units=b.units + [a.target], vision=None)
    u = a.unit
    if a.kind in ("join", "load"):
        units = [x for x in b.units if x.slot != u.slot]
        if a.kind == "join":
            units = [dataclasses.replace(x, hp=a.hp_after)
                     if x.slot == a.target.slot else x for x in units]
        return dataclasses.replace(b, units=units, vision=None)
    end = a.drop_tile if a.kind == "trap" else a.tile
    hyp, _ = threat._relocate(b, u, end)
    if a.kind == "drop":
        dx, dy = a.drop_tile
        hyp = dataclasses.replace(hyp, units=[
            dataclasses.replace(x, x=dx, y=dy, loaded=False)
            if x.slot == a.target.slot else x for x in hyp.units])
    elif a.kind == "attack":
        left = a.strike.max_remaining_hp
        units = [x for x in hyp.units if x.slot != a.target.slot]
        if left > 0:
            units.append(dataclasses.replace(a.target, hp=left))
        hyp = dataclasses.replace(hyp, units=units)
    return hyp


def _exposure_terms(ctx: "Context", a) -> List[Term]:
    """property_exposure and hq_exposure as the CHANGE `a` makes: the
    side's exposure on the board it leaves against the board as it stands.
    Positive when the action plugs, blocks or shoots a threat away,
    negative when it opens a property up."""
    hyp = _hypothetical(ctx, a)
    if hyp is None:
        return []
    before = ctx.exposure
    after = ctx.exposure_of(hyp)
    out = []
    for name, was, now in (("property_exposure", before.properties, after.properties),
                           ("hq_exposure", before.hq, after.hq)):
        if was == now:
            continue
        what = "the HQ" if name == "hq_exposure" else "properties"
        out.append(Term(name, ctx.w[name], was - now,
                        f"{what} an enemy foot unit can step onto next turn: "
                        f"{before.summary} -> {after.summary}"))
    return out


def _capture_terms(ctx: Context, a) -> List[Term]:
    w = ctx.w
    u = a.unit
    out = []
    x, y = a.tile
    if a.kind == "capture":
        progress = u.capture if a.tile == (u.x, u.y) else 0
        gained = a.progress_after - progress
        worth = property_worth(ctx.board, x, y, u.player, w)
        out.append(Term("capture", w["capture"], worth * gained / 20,
                        f"{a.terrain} at ({x},{y}): {progress} -> "
                        f"{a.progress_after}/20"
                        + (", falls THIS TURN" if a.captures_now
                           else f", done in {a.capture_turns_left}")
                        + f"; the property is worth {worth:.0f} "
                        f"({w['capture_horizon']} days of income"
                        + (", enemy-held" if ctx.board.owner[y][x] else "")
                        + ")"))
        if a.captures_now and ctx.board.terrain[y][x] == TERRAIN_HQ:
            out.append(Term("win", w["win"], 1,
                            f"the enemy HQ at ({x},{y}) falls this turn"))
    if u.capture and (a.kind != "capture" or a.tile != (u.x, u.y)):
        end = a.drop_tile if a.kind == "trap" else a.tile
        if end != (u.x, u.y):
            worth = property_worth(ctx.board, u.x, u.y, u.player, w)
            out.append(Term("capture", w["capture"], -worth * u.capture / 20,
                            f"leaving ({u.x},{u.y}) abandons {u.capture}/20 "
                            f"capture points (moving resets progress, A15)"))
    return out


def score_action(ctx: Context, a) -> Scored:
    """The opinion on one action, as named terms over its facts."""
    if a.kind == "build":
        return _score_build(ctx, a)
    if a.kind == "power":
        return _score_power(ctx, a)
    w = ctx.w
    u = a.unit
    terms: List[Term] = []

    if a.kind == "attack":
        t = a.target
        tv = bar_value(ctx.board, t, ctx.co_ids)
        got = bars_lost(t.hp, a.strike.max_remaining_hp)
        terms.append(Term("damage_dealt", w["damage_dealt"], got * tv,
                          f"strike {a.strike.min_damage}-{a.strike.max_damage} "
                          f"on {t.type} #{t.slot} ({got} bar"
                          f"{'s' if got != 1 else ''} at worst) x {tv}/bar"))
        if a.strike.guaranteed_kill:
            terms.append(Term("kill", w["kill"], price(t.type),
                              f"guaranteed kill of a {t.type} "
                              f"(price {price(t.type)})"))
        terms += _damage_terms(ctx, a, u, u.hp, a.exposure, counter=a.counter,
                               lethal_now=(a.counter is not None
                                           and a.counter.guaranteed_kill))
        terms += _turn_start_terms(ctx, dataclasses.replace(u, hp=a.hp_after),
                                   a.hp_after, a.turn_start)
        terms += _capture_terms(ctx, a)
        terms += _objective_term(ctx, u, a.tile)
        terms += _exposure_terms(ctx, a)
        return Scored(a, tuple(terms))

    if a.kind == "load":
        ride = a.target
        ff = a.exposure
        if ff is not None and ff.lethal:
            terms.append(Term("damage_taken", w["damage_taken"],
                              -unit_worth(ctx.board, u, ctx.co_ids),
                              f"the {ride.type} #{ride.slot} it boards can "
                              f"die next turn ({ff.best_damage}-"
                              f"{ff.worst_damage} from {ff.attackers}); "
                              f"a passenger dies with its ride"))
            terms.append(Term("loss", w["loss"], -price(u.type),
                              f"worst case loses this {u.type} with its ride"))
        terms += _capture_terms(ctx, a)
        terms += _objective_term(ctx, u, a.tile)
        terms += _exposure_terms(ctx, a)
        return Scored(a, tuple(terms))

    if a.kind == "drop":
        p = a.target
        terms += _damage_terms(ctx, a, p, p.hp, a.exposure)
        terms += _turn_start_terms(ctx, p, p.hp, a.turn_start)
        terms += _objective_term(ctx, p, a.drop_tile)
        terms += _objective_term(ctx, u, a.tile)
        terms += _exposure_terms(ctx, a)
        return Scored(a, tuple(terms))

    if a.kind == "join":
        m = a.merge
        merged = dataclasses.replace(u, hp=a.hp_after, x=a.tile[0],
                                     y=a.tile[1])
        terms += _damage_terms(ctx, a, merged, a.hp_after, a.exposure)
        terms += _turn_start_terms(ctx, merged, a.hp_after, a.turn_start)
        if m.refund:
            terms.append(Term("refund", w["refund"], m.refund,
                              f"the join refunds {m.refund}"))
        terms += _capture_terms(ctx, a)
        terms += _objective_term(ctx, u, a.tile)
        terms += _exposure_terms(ctx, a)
        return Scored(a, tuple(terms))

    if a.kind == "supply":
        for f in a.supplies:
            frac = _short_fraction(f.target.type, f.target.fuel, f.target.ammo)
            terms.append(Term("resupply", w["resupply"],
                              frac * price(f.target.type),
                              f"refills {f.target.type} #{f.target.slot} "
                              f"fuel {f.target.fuel} -> {f.fuel_to}, ammo "
                              f"{f.target.ammo} -> {f.ammo_to} "
                              f"({frac:.0%} of its tanks was empty)"))
        terms += _damage_terms(ctx, a, u, u.hp, a.exposure)
        terms += _turn_start_terms(ctx, u, u.hp, a.turn_start)
        terms += _objective_term(ctx, u, a.tile)
        terms += _exposure_terms(ctx, a)
        return Scored(a, tuple(terms))

    # wait, capture, dive, rise, trap
    end = a.drop_tile if a.kind == "trap" else a.tile
    actor = u
    if a.kind == "dive":
        actor = dataclasses.replace(u, state=u.state | 0x20)
    elif a.kind == "rise":
        actor = dataclasses.replace(u, state=u.state & ~0x20)
    terms += _damage_terms(ctx, a, actor, u.hp, a.exposure)
    terms += _turn_start_terms(ctx, actor, u.hp, a.turn_start)
    terms += _capture_terms(ctx, a)
    terms += _objective_term(ctx, u, end)
    terms += _exposure_terms(ctx, a)
    return Scored(a, tuple(terms))


def _score_build(ctx: Context, a) -> Scored:
    w = ctx.w
    fresh = a.target
    terms: List[Term] = []
    enemies = threat.hostiles(ctx.board, ctx.player, ignore_acted=True,
                              fog=ctx.fog_on, rule_set=ctx.fog_rules)
    if enemies:
        total, hits = 0.0, []
        for e in enemies:
            wpn = damage.select_weapon(fresh.type, e.type, fresh.ammo,
                                       defender_dived=e.dived)
            base = wpn.base if wpn else 0
            total += base / 100 * price(e.type)
            hits.append(f"{base} vs {e.type}")
        avg = total / len(enemies)
        terms.append(Term("build_matchup", w["build_matchup"], avg,
                          f"base damage {', '.join(hits[:6])}"
                          + (", ..." if len(hits) > 6 else "")
                          + f" -> {avg:.0f} of enemy price on average"))
    st = pathing.unit_stats(fresh.type)
    if st["unit_class"] == "foot":
        unowned = sum(1 for y in range(ctx.board.height)
                      for x in range(ctx.board.width)
                      if ctx.board.terrain[y][x] in actions_mod._capturable()
                      and ctx.board.owner[y][x] != ctx.player)
        foot = sum(1 for u in ctx.board.units
                   if u.player == ctx.player
                   and pathing.unit_stats(u.type)["unit_class"] == "foot")
        if unowned:
            rate, _ = economy.funds_rate(ctx.board)
            worth = rate * w["capture_horizon"]
            need = min(1.0, unowned / (foot + 1))
            terms.append(Term("build_capture", w["build_capture"],
                              worth * need,
                              f"{unowned} propert{'y' if unowned == 1 else 'ies'} "
                              f"not ours, {foot} foot unit{'s' if foot != 1 else ''} "
                              f"already -> {need:.0%} of a property's worth"))
    bias = BUILD_BIAS.get(fresh.type, 0)
    if bias:
        terms.append(Term("build_bias", w["build_bias"], bias,
                          f"fixed early-table bias for {fresh.type}"))
    terms += _exposure_terms(ctx, a)
    terms.append(Term("build_spend", w["build_spend"], -a.cost,
                      f"{fresh.type} costs {a.cost} at {a.terrain} "
                      f"({a.tile[0]},{a.tile[1]})"))
    terms += _damage_terms(ctx, a, fresh, fresh.hp, a.exposure)
    return Scored(a, tuple(terms))


def _score_power(ctx: Context, a) -> Scored:
    w = ctx.w
    f = a.power
    terms: List[Term] = []
    for u, hp_after in f.heals:
        got = bars_lost(hp_after, u.hp) if hp_after > u.hp else 0
        if got:
            bv = bar_value(ctx.board, u, ctx.co_ids)
            terms.append(Term("repair", w["repair"], got * bv,
                              f"heals {u.type} #{u.slot} "
                              f"{damage.screen_bars(u.hp)} -> "
                              f"{damage.screen_bars(hp_after)} bars"))
    for u in f.refreshes:
        terms.append(Term("power_refresh", w["power_refresh"], price(u.type),
                          f"refreshes {u.type} #{u.slot} (acted -> ready)"))
    for u, hp_after in f.damages:
        got = bars_lost(u.hp, hp_after)
        if got:
            bv = bar_value(ctx.board, u, ctx.co_ids)
            terms.append(Term("damage_dealt", w["damage_dealt"], got * bv,
                              f"hits {u.type} #{u.slot} for {got} bar"
                              f"{'s' if got != 1 else ''}"))
    if f.meteors:
        worst = None
        for strategy, center, victims in f.meteors:
            val = 0
            for u, hp_after in victims:
                got = bars_lost(u.hp, hp_after)
                sign = 1 if u.player != ctx.player else -1
                val += sign * got * bar_value(ctx.board, u, ctx.co_ids)
            if worst is None or val < worst[0]:
                worst = (val, strategy, center)
        val, strategy, center = worst
        terms.append(Term("damage_dealt", w["damage_dealt"], val,
                          f"meteor at its worst strategy for us ({strategy}, "
                          f"centre {center}): {val:+.0f} of unit value; the "
                          f"RNG draw that picks it is unread"))
    atk, _ = f.universal
    if atk != 100:
        terms.append(Term("power_block", w["power_block"],
                          ctx.army_value * (atk - 100) / 100,
                          f"the power block's {atk}% attack over an army "
                          f"worth {ctx.army_value} for the rest of the turn"))
    unscored = [k for k in ("weather", "move_tables", "range_bonus")
                if f.effects.get(k)]
    if unscored:
        terms.append(Term("power_block", w["power_block"], 0,
                          f"effect(s) {unscored} score nothing here -- "
                          f"unmodelled by the planner"))
    return Scored(a, tuple(terms))


# --------------------------------------------------------------------------
# the plan
# --------------------------------------------------------------------------

_KIND_ORDER = actions_mod._KIND_ORDER


def _tiebreak(s: Scored) -> tuple:
    a = s.action
    actor = (a.unit.x, a.unit.y) if a.unit is not None else (-1, -1)
    tgt = ((a.target.x, a.target.y) if a.target is not None
           and a.kind != "build" else (-1, -1))
    return (-s.score, _KIND_ORDER[a.kind], actor, a.tile, tgt,
            a.build_type or "")


def candidates(board, player: int, ctx: Context) -> List[Scored]:
    """Every scorable candidate on this board, best first."""
    out: List[Scored] = []
    for slot, acts in actions_mod.all_actions(
            board, player, co_ids=ctx.co_ids, weather=ctx.weather,
            fog=ctx.fog, fog_rules=ctx.fog_rules,
            warnings=ctx.warnings).items():
        for a in acts:
            if a.kind == "trap":
                continue
            out.append(score_action(ctx, a))
    for a in actions_mod.build_actions(board, player, co_ids=ctx.co_ids,
                                       weather=ctx.weather, fog=ctx.fog,
                                       fog_rules=ctx.fog_rules,
                                       warnings=ctx.warnings):
        if a.affordable:
            out.append(score_action(ctx, a))
    pw = actions_mod.power_action(board, player, co_ids=ctx.co_ids,
                                  warnings=ctx.warnings)
    if pw is not None:
        out.append(score_action(ctx, pw))
    out.sort(key=_tiebreak)
    return out


def _greedy(board, player: int, *, weights, co_ids, weather, fog, fog_rules,
            luck, warnings, max_steps: int, forced: Optional[Scored] = None,
            index_from: int = 1) -> Tuple[List[Step], object]:
    """The greedy loop: steps from `board` until the turn is spent, and the
    board they leave. `forced` commits that candidate (scored on `board`)
    as the first step instead of the best one -- how a variant plan is
    proposed -- and the greedy choice it displaces is filed as its
    runner-up."""
    steps: List[Step] = []
    cur = board
    for _ in range(max_steps):
        ctx = Context(cur, player, weights=weights, co_ids=co_ids,
                      weather=weather, fog=fog, fog_rules=fog_rules,
                      warnings=warnings)
        ranked = candidates(cur, player, ctx)
        if not ranked:
            break
        if forced is not None:
            best, forced = forced, None
            runner = alt = ranked[0]
        else:
            best = ranked[0]
            if best.action.unit is None and best.score <= 0:
                # an army action that does not pay: is any unit still waiting?
                unit_acts = [s for s in ranked if s.action.unit is not None]
                if not unit_acts:
                    break
                best = unit_acts[0]
            runner = next((s for s in ranked[1:] if s is not best), None)
            alt = next((s for s in ranked if s is not best
                        and _same_actor(s.action, best.action)), None)
        nxt = sim_mod.apply(cur, best.action, luck=luck, co_ids=co_ids,
                            warnings=warnings)
        steps.append(Step(index=index_from + len(steps), scored=best,
                          board_before=cur, board_after=nxt,
                          runner_up=runner, alternative=alt))
        cur = nxt
    else:
        warnings.append(f"the planner stopped after {max_steps} steps")
    return steps, cur


def _same_actor(a, b) -> bool:
    """Two candidates for the same decision: the same unit's actions, or
    two army actions of the same kind (one build against another)."""
    if a.unit is not None and b.unit is not None:
        return a.unit.slot == b.unit.slot
    return a.unit is None and b.unit is None and a.kind == b.kind


REPLY_MODELS = ("cpu", "planner")

# The terms the worst case has a say in: a step whose winner and runner-up
# differ in one of these is a call the modelled reply may overturn.
STAKE_TERMS = ("damage_dealt", "kill", "damage_taken", "loss", "capture", "win",
               "property_exposure", "hq_exposure")


def _stake(s: Step) -> float:
    """How much of the gap between a step's winner and its alternative the
    worst case decided: the summed difference of their STAKE_TERMS."""
    def by_name(sc: Scored) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for t in sc.terms:
            out[t.name] = out.get(t.name, 0.0) + t.value
        return out
    a, b = by_name(s.scored), by_name(s.alternative)
    return sum(abs(a.get(n, 0.0) - b.get(n, 0.0)) for n in STAKE_TERMS)


def plan(board, player: Optional[int] = None, *, weights=None,
         co_ids=None, weather=None, fog=None, fog_rules=None,
         luck="min", max_steps: int = 200,
         warnings: Optional[list] = None,
         reply: Optional[str] = None, cpu_ctx=None, branches: int = 3,
         reply_luck="max") -> Plan:
    """A turn for `player` (default: the active player), greedy with
    sequential commit. Each step is scored on the board the previous step
    left behind (sim.apply at `luck`, "min" being the worst case for the
    actor). A unit that can act always gets an action -- a wait in place
    is a legal one -- while builds and the power are taken only when they
    score above zero.

    With `reply` the greedy plan is a proposal: it and up to `branches`
    variants (the runner-up committed at the closest calls) are each
    followed by the opponent's modelled turn -- "cpu" for engine/cpu_ai
    over `cpu_ctx` (cpu_ai.Context.from_dump), "planner" for this planner
    one ply deep, its battles rolled at `reply_luck` ("max": high against
    us) -- and the candidate whose board at our next turn start evaluates
    best is the plan. Ties keep the greedy proposal."""
    warnings = warnings if warnings is not None else []
    player = player or board.active_player
    if not player:
        raise ValueError("no active player on this board and none given")
    if reply is not None and reply not in REPLY_MODELS:
        raise ValueError(f"reply must be one of {REPLY_MODELS} or None, "
                         f"not {reply!r}")
    if reply == "cpu" and cpu_ctx is None:
        raise ValueError("reply='cpu' needs cpu_ctx, a cpu_ai.Context "
                         "(cpu_ai.Context.from_dump(path, player=opponent))")
    kw = dict(weights=weights, co_ids=co_ids, weather=weather, fog=fog,
              fog_rules=fog_rules, luck=luck, warnings=warnings)
    steps, cur = _greedy(board, player, max_steps=max_steps, **kw)
    out = Plan(player=player, steps=steps, board_after=cur,
               warnings=warnings, weights=dict(WEIGHTS))
    if weights:
        out.weights.update(weights)
    if reply is None:
        return out

    # -- the proposals: the greedy plan, then the same actor's alternative
    # -- at each of the closest calls, the rest of the turn re-planned
    cands = [Candidate("the greedy plan", steps, cur)]
    close = sorted((s for s in steps if s.alternative is not None),
                   key=lambda s: (_stake(s) == 0,
                                  s.scored.score - s.alternative.score, s.index))
    for s in close[:max(0, branches)]:
        rest, after = _greedy(s.board_before, player, forced=s.alternative,
                              index_from=s.index,
                              max_steps=max_steps - (s.index - 1), **kw)
        cands.append(Candidate(
            f"step {s.index}: {describe_action(s.alternative.action)} instead",
            steps[:s.index - 1] + rest, after))
    # -- the arbiter; two proposals that leave the same board share a reply
    for c in cands:
        twin = next((d for d in cands if d.reply is not None
                     and d.board_after == c.board_after), None)
        if twin is not None:
            c.reply = twin.reply
            continue
        c.reply = _reply(c.board_after, player, reply, cpu_ctx,
                         weights=out.weights, co_ids=co_ids, weather=weather,
                         fog=fog, fog_rules=fog_rules, luck=reply_luck,
                         warnings=warnings, start=board)
    best = max(cands, key=lambda c: c.reply.score)     # max keeps the first tie
    best.chosen = True
    out.steps, out.board_after = best.steps, best.board_after
    out.reply, out.candidates = best.reply, cands
    out.baseline = evaluate(board, player, out.weights, co_ids, start=board)
    return out


# --------------------------------------------------------------------------
# the reply: the opponents' modelled turns, then the board from our side
# --------------------------------------------------------------------------

def _fresh_ctx(ctx):
    """A cpu_ai.Context the predictor may write into (it keeps per-unit
    bytes and adds the units it builds) without touching the caller's."""
    return dataclasses.replace(ctx, ai={k: list(v) for k, v in ctx.ai.items()})


def _describe_command(cmd, board) -> str:
    u = sim_mod.unit_in(board, cmd.slot)
    who = f"{u.type} #{u.slot} ({u.x},{u.y})" if u is not None else f"#{cmd.slot}"
    at = f"({cmd.tile[0]},{cmd.tile[1]})"
    if cmd.name == "fire":
        t = sim_mod.unit_in(board, cmd.arg)
        tgt = (f"{t.type} #{t.slot} ({t.x},{t.y})" if t is not None
               else f"#{cmd.arg}")
        return f"{who} -> {at} FIRE at {tgt}"
    return f"{who} -> {at} {cmd.name.upper()}"


def _reply(board, player: int, model: str, cpu_ctx, *, weights, co_ids,
           weather, fog, fog_rules, luck, warnings, start) -> Reply:
    """End Turn after `board`, then every opponent's turn as `model` plays
    it until the turn comes back to `player`, then that turn's start; the
    board is evaluated from `player`'s side against `start`."""
    described: List[str] = []
    notes: List[str] = []
    used: List[str] = []
    opponents: List[int] = []
    cur = sim_mod.end_turn(board, warnings=warnings)
    while cur.active_player != player and len(opponents) < 4:
        opp = cur.active_player
        opponents.append(opp)
        played = False
        if model == "cpu":
            rng = cur.rng
            if rng is None:
                rng = 0
                note = ("no RNG state on this board: the CPU's luck draws "
                        "are taken from state 0")
                if note not in warnings:
                    warnings.append(note)
            try:
                turn = cpu_ai.predict(cur, opp, _fresh_ctx(cpu_ctx), rng=rng)
            except (NotImplementedError, RuntimeError) as e:
                notes.append(f"the CPU port could not play P{opp}'s turn "
                             f"({e}); this planner stood in for it")
            else:
                lines = [f"P{opp}: {_describe_command(c, cur)}" for c in turn.commands]
                lines += [f"P{opp} buys {b['name']} at ({b['x']},{b['y']}) "
                          f"for {b['price']}" for b in turn.builds]
                described += lines or [f"P{opp} does nothing"]
                cur = turn.board
                used.append("cpu")
                played = True
        if not played:
            p = plan(cur, opp, weights=weights, co_ids=co_ids, weather=weather,
                     fog=fog, fog_rules=fog_rules, luck=luck, warnings=warnings)
            described += ([f"P{opp}: {describe_action(s.action)}" for s in p.steps]
                          or [f"P{opp} does nothing"])
            cur = p.board_after
            used.append("planner")
        cur = sim_mod.end_turn(cur, warnings=warnings)
    kinds = set(used)
    return Reply(opponents=tuple(opponents),
                 model=(kinds.pop() if len(kinds) == 1 else "mixed"),
                 described=tuple(described), board_after=cur,
                 terms=evaluate(cur, player, weights, co_ids, start=start),
                 note="; ".join(notes))


def evaluate(board, player: int, weights=None, co_ids=None, *,
             start=None) -> Tuple[Term, ...]:
    """The static opinion on `board` from `player`'s side, in funds, as
    named terms: material (unit value, ours less theirs), treasury, income
    over the horizon, captures in hand, and -- against `start` -- an HQ
    that changed hands or a side that lost its last unit, at `win`."""
    w = dict(WEIGHTS)
    if weights:
        w.update(weights)
    start = start if start is not None else board
    others = sorted({p for b in (start, board)
                     for p in sim_mod.players_in_order(b) if p != player})
    out: List[Term] = []

    def funds_of(b, p):
        try:
            return b.army(p).funds
        except (StopIteration, AttributeError):
            return 0

    own = army_worth(board, player, co_ids)
    foe = sum(army_worth(board, p, co_ids) for p in others)
    out.append(Term("material", w["material"], own - foe,
                    f"P{player}'s units are worth {own}, the enemy's {foe}"))
    own_f = funds_of(board, player)
    foe_f = sum(funds_of(board, p) for p in others)
    out.append(Term("treasury", w["treasury"], own_f - foe_f,
                    f"funds {own_f} against the enemy's {foe_f}"))
    own_i = economy.income(board, player).amount
    foe_i = sum(economy.income(board, p).amount for p in others)
    out.append(Term("income", w["income"],
                    (own_i - foe_i) * w["capture_horizon"],
                    f"income {own_i} a day against the enemy's {foe_i}, over "
                    f"{w['capture_horizon']} days"))
    held, facts = 0.0, []
    for u in sorted(board.units, key=lambda u: (u.player != player, u.slot)):
        if not u.capture:
            continue
        worth = property_worth(board, u.x, u.y, u.player, w) * u.capture / 20
        sign = 1 if u.player == player else -1
        held += sign * worth
        facts.append(f"P{u.player} {u.type} #{u.slot} at ({u.x},{u.y}) "
                     f"{u.capture}/20 ({sign * worth:+.0f})")
    if facts:
        out.append(Term("capture", w["capture"], held,
                        "captures in hand: " + "; ".join(facts)))
    # what each side's foot units can step onto next: ours exposed against,
    # theirs for -- the same facts the per-action term reads, with full
    # information, as every term here has
    for side, sign in [(player, -1)] + [(p, 1) for p in others]:
        ex = Exposure.of(board, side, w, co_ids, warnings=[])
        for name, qty, what in (("property_exposure", ex.properties, "properties"),
                                ("hq_exposure", ex.hq, "HQ")):
            if qty:
                out.append(Term(name, w[name], sign * qty,
                                f"P{side}'s {what} a foot unit of the other side "
                                f"can step onto next turn: {ex.summary}"))
    for y in range(board.height):
        for x in range(board.width):
            if board.terrain[y][x] != TERRAIN_HQ:
                continue
            was, now = start.owner[y][x], board.owner[y][x]
            if was == now:
                continue
            if now == player and was in others:
                out.append(Term("win", w["win"], 1,
                                f"the enemy HQ at ({x},{y}) is ours"))
            elif was == player and now in others:
                out.append(Term("win", w["win"], -1,
                                f"our HQ at ({x},{y}) is the enemy's"))
    for p in [player] + others:
        if start.units_of(p) and not board.units_of(p):
            out.append(Term("win", w["win"], -1 if p == player else 1,
                            f"P{p} has lost its last unit (the rout -- "
                            f"stated, not measured)"))
    return tuple(out)


# --------------------------------------------------------------------------
# rendering, shared by tools/advise.py and the tests' readability check
# --------------------------------------------------------------------------

def describe_action(a) -> str:
    u = a.unit
    at = f"({a.tile[0]},{a.tile[1]})"
    if a.kind == "build":
        return f"BUILD {a.build_type} at {a.terrain} {at} for {a.cost}"
    if a.kind == "power":
        return f"CO POWER -- {a.power.co_name} (meter {a.power.meter}/{a.power.threshold})"
    who = f"{u.type} #{u.slot} ({u.x},{u.y})"
    if a.kind == "attack":
        t = a.target
        return (f"{who} -> {at} FIRE at {t.type} #{t.slot} ({t.x},{t.y})")
    if a.kind == "capture":
        return (f"{who} CAPTURE {a.terrain} here" if a.tile == (u.x, u.y)
                else f"{who} -> {at} CAPTURE {a.terrain}")
    if a.kind == "load":
        t = a.target
        return f"{who} -> LOAD into {t.type} #{t.slot} {at}"
    if a.kind == "drop":
        t = a.target
        return (f"{who} -> {at} DROP {t.type} #{t.slot} at "
                f"({a.drop_tile[0]},{a.drop_tile[1]})")
    if a.kind == "join":
        t = a.target
        return f"{who} -> JOIN {t.type} #{t.slot} at {at}"
    if a.kind == "supply":
        who2 = ", ".join(f"#{f.target.slot}" for f in a.supplies)
        return f"{who} -> {at} SUPPLY {who2}"
    if a.kind == "wait":
        return (f"{who} WAIT here on {a.terrain}" if a.tile == (u.x, u.y)
                else f"{who} -> {at} WAIT on {a.terrain}")
    return f"{who} -> {at} {a.kind.upper()}"


def render(p: Plan, *, terms: bool = True) -> str:
    """The plan as text: one line per step with its score, and under it
    the terms -- each a weight, a quantity, and the fact it was read from.
    Weights are marked heuristic on every line they appear on."""
    n = f"{len(p.steps)} step{'s' if len(p.steps) != 1 else ''}"
    if p.reply is None:
        out = [f"P{p.player}'s turn -- {n}, greedy, worst case throughout "
               f"(heuristic total {p.score:+.0f})"]
    else:
        out = [f"P{p.player}'s turn -- {n}, the proposal of "
               f"{len(p.candidates)} that the modelled reply scores best "
               f"(heuristic total {p.score:+.0f}; the board after the reply "
               f"{p.reply.score:+.0f}, from {p.baseline_score:+.0f} now)"]
    for s in p.steps:
        out.append(f"{s.index:2d}. {describe_action(s.action)}   "
                   f"[{s.scored.score:+.0f}]")
        if terms:
            if not s.scored.terms:
                out.append("      (no term applies: nothing gained, nothing "
                           "at risk)")
            for t in s.scored.terms:
                out.append(f"      {t.name:14s} {t.value:+9.0f}  = "
                           f"{t.weight:g} (heuristic) x {t.quantity:+.0f}  "
                           f"<- {t.fact}")
            if s.runner_up is not None:
                out.append(f"      next best: {describe_action(s.runner_up.action)} "
                           f"[{s.runner_up.score:+.0f}]")
    out.append(f"{len(p.steps) + 1:2d}. END TURN")
    if p.reply is not None:
        r = p.reply
        who = " and ".join(f"P{o}" for o in r.opponents)
        by = {"cpu": "the CPU port (engine/cpu_ai)",
              "planner": "this planner, one ply, its battles rolled against us",
              "mixed": "the CPU port where it could play and this planner where not"}[r.model]
        out.append(f"    then {who}'s reply, modelled by {by}:")
        for line in r.described:
            out.append(f"      {line}")
        if r.note:
            out.append(f"      !! {r.note}")
        out.append(f"    the board at P{p.player}'s next turn start, from "
                   f"P{p.player}'s side   [{r.score:+.0f}, from "
                   f"{p.baseline_score:+.0f} now]")
        if terms:
            for t in r.terms:
                out.append(f"      {t.name:14s} {t.value:+9.0f}  = "
                           f"{t.weight:g} (heuristic) x {t.quantity:+.0f}  "
                           f"<- {t.fact}")
        out.append("    proposals, by the reply's score:")
        for c in p.candidates:
            out.append(f"      {c.reply.score:+9.0f}  {c.label} "
                       f"(proposal {c.score:+.0f})"
                       + ("   [chosen]" if c.chosen else ""))
    return "\n".join(out)

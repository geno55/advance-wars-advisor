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
  * one turn: nothing past the enemy's reply, which is itself the worst
    case rather than a modelled opponent (ROADMAP steps 3-4);
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
    from . import damage, economy, pathing, sim as sim_mod
    from . import supply as supply_mod, threat
except ImportError:                     # imported as advisor, engine/ on path
    import actions as actions_mod
    import co as co_mod
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

    @property
    def action(self):
        return self.scored.action


@dataclass
class Plan:
    player: int
    steps: List[Step]
    board_after: object       # the board once every step is committed
    warnings: List[str] = field(default_factory=list)
    weights: Dict[str, float] = field(default_factory=dict)

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

    def share(self, unit) -> float:
        return unit_worth(self.board, unit, self.co_ids) / self.army_value


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
        return Scored(a, tuple(terms))

    if a.kind == "drop":
        p = a.target
        terms += _damage_terms(ctx, a, p, p.hp, a.exposure)
        terms += _turn_start_terms(ctx, p, p.hp, a.turn_start)
        terms += _objective_term(ctx, p, a.drop_tile)
        terms += _objective_term(ctx, u, a.tile)
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


def plan(board, player: Optional[int] = None, *, weights=None,
         co_ids=None, weather=None, fog=None, fog_rules=None,
         luck="min", max_steps: int = 200,
         warnings: Optional[list] = None) -> Plan:
    """A turn for `player` (default: the active player), greedy with
    sequential commit. Each step is scored on the board the previous step
    left behind (sim.apply at `luck`, "min" being the worst case for the
    actor). A unit that can act always gets an action -- a wait in place
    is a legal one -- while builds and the power are taken only when they
    score above zero."""
    warnings = warnings if warnings is not None else []
    player = player or board.active_player
    if not player:
        raise ValueError("no active player on this board and none given")
    steps: List[Step] = []
    cur = board
    for i in range(max_steps):
        ctx = Context(cur, player, weights=weights, co_ids=co_ids,
                      weather=weather, fog=fog, fog_rules=fog_rules,
                      warnings=warnings)
        ranked = candidates(cur, player, ctx)
        if not ranked:
            break
        best = ranked[0]
        if best.action.unit is None and best.score <= 0:
            # an army action that does not pay: is any unit still waiting?
            unit_acts = [s for s in ranked if s.action.unit is not None]
            if not unit_acts:
                break
            best = unit_acts[0]
        runner = next((s for s in ranked[1:] if s is not best), None)
        nxt = sim_mod.apply(cur, best.action, luck=luck, co_ids=co_ids,
                            warnings=warnings)
        steps.append(Step(index=len(steps) + 1, scored=best,
                          board_before=cur, board_after=nxt,
                          runner_up=runner))
        cur = nxt
    else:
        warnings.append(f"the planner stopped after {max_steps} steps")
    out = Plan(player=player, steps=steps, board_after=cur,
               warnings=warnings, weights=dict(WEIGHTS))
    if weights:
        out.weights.update(weights)
    return out


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
    out = [f"P{p.player}'s turn -- {len(p.steps)} step"
           f"{'s' if len(p.steps) != 1 else ''}, greedy, worst case "
           f"throughout (heuristic total {p.score:+.0f})"]
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
    return "\n".join(out)

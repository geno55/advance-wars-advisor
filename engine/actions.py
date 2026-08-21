"""Every legal action one unit has this turn, resolved to facts. No opinions.

This is the advisor's first layer that looks at YOUR units instead of the
enemy's, and it deliberately stops at enumeration: each action comes back with
the numbers the engine can already state -- damage envelopes, the counter, the
exposure at the ending tile -- and no ranking. Ranking is a judgment call, the
first number in this project the game cannot be asked to check, and it does not
belong in the same module as the facts. A caller that wants "best" builds it on
top of this and says so.

Composition, not new reverse engineering, same as threat.py. Where each fact
comes from:

  * where the unit may end its move        -- pathing.destinations(), matched
                                              against the game's own flood fill
  * what it could shoot from there          -- threat.firing_positions(), the
                                              same three ROM fields (armed,
                                              can_move_and_fire, min/max range)
                                              that drive the enemy projection
  * whether a shot is legal, and for what   -- the ROM damage matrices and the
                                              calibrated formula, exact ranges
  * what comes back at it                   -- damage.counterattack(), measured
                                              (ASSUMPTIONS A9b), with the cover
                                              on the ENDING tile, not where the
                                              unit started
  * what the enemy does about it next turn  -- threat.focus_fire() at the
                                              ending tile, on a board where the
                                              attack has already happened

There is not a single branch on unit type in this file, and tests/test_actions.py
enforces it the same way pathing and threat do. Who may capture comes from the
ROM's `unit_class` field, who may load from the cargo mask, who may shoot from
`armed` -- never from a name.

WHAT IS MODELLED

  * WAIT on any tile `destinations()` allows, with the exposure there -- and
    the measured turn-start facts for that tile (`Action.turn_start`): the
    daily fuel burn, the crash at fuel 0, property repair/resupply with the
    exact funds charge, and APC auto-supply, in the game's own order
    (income, burn+crash, property service, auto-supply -- DERIVATION 33).
    All of it composes engine/supply.py; the caveats are in _turn_start_facts.
  * SUPPLY for any unit the ROM's supplier table names (the APC): offered on
    every destination with at least one adjacent friendly below its fuel or
    ammo maximum -- the same need-gate the game's menu applies -- listing
    exactly who gets filled to what. Free, and it spends the action.
  * ATTACK any hostile the damage matrix permits, from any legal firing tile.
    Direct units attack from anywhere they may stop; indirects only from where
    they stand, and never inside min_range -- all of it falling out of the same
    table fields threat.py reads. The strike and counter are exact ranges.
  * CAPTURE a property not already yours, either continuing where the unit
    stands or starting fresh on any destination. See A15 for what in the
    capture rules is measured and what is assumed.
  * LOAD into a friendly transport `destinations()` already offers. The
    exposure reported is the TRANSPORT's, because that is what a shot would
    hit -- a passenger dies with its ride.

WHAT IS NOT MODELLED, deliberately and visibly

  * Unloading, and joining two damaged units. The reader's single cargo slot
    under-reports a transport's state, and the join rule has never been put in
    front of the game, so neither is offered rather than offered wrongly.
  * Production. Buying units is the army's action, not a unit's, and the
    factory rules have not been measured. A later module.
  * CO power activation. The whole system is modelled in engine/co.py --
    charge formula, threshold (`power_threshold`), per-CO effects
    (DERIVATION 27) -- but activating is an ARMY action like production, and
    this module enumerates unit actions. When an army module exists it
    should offer activation from co.py's numbers; nothing here models a
    power firing mid-turn.
  * Hidden units interrupting movement under fog. Pathing does not model the
    ambush rule, so under fog a long move may be more optimistic than the game.

Exposure on an ATTACK action is computed on the board AFTER the trade, worst
case for you throughout one consistent world -- the low opening roll: the
target survives at its strongest (it is removed only on a guaranteed kill) and
your unit stands at its weakest post-counter HP. When even that worst case is
death, exposure is None: what the enemy could do next turn to a unit that may
already be gone is not a number worth printing, and `counter.possible_kill` /
`counter.guaranteed_kill` carry the warning instead.

Facts here inherit every caveat of what they compose: `worst_damage` in the
exposure is an upper bound (attackers are never weakened by counters), fog
UNKNOWN is carried as a warning rather than collapsed to off, and an unknown CO
falls back to neutral out loud.
"""
from __future__ import annotations

import dataclasses
import functools
import json
import pathlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:                                    # imported as engine.actions by tools/
    from . import co as co_mod
    from . import damage, pathing, supply as supply_mod, threat
except ImportError:                     # imported as actions, engine/ on path
    import co as co_mod
    import damage
    import pathing
    import supply as supply_mod
    import threat

Coord = Tuple[int, int]
DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

# A property falls at 20 capture points. The whole arithmetic is READ off the
# ROM at 0x08026180-0x080262E4: progress += ceil(hp/10) plus the CO bonus (see
# _capture_gain), clamped to 20, transfer when the re-read value exceeds 19.
CAPTURE_GOAL = 20


@functools.lru_cache(maxsize=None)
def _capturable() -> frozenset:
    """Terrain ids a unit can capture, from the extracted terrain table."""
    t = json.loads((DATA / "aw1_terrain.json").read_text(encoding="utf-8"))
    return frozenset(t["capturable_ids"])


# --------------------------------------------------------------------------
# the action record
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One legal thing one unit can do this turn, with the facts attached.

    `kind` is one of "attack", "capture", "load", "supply", "wait". The
    optional fields are populated per kind and None/zero otherwise:

      attack   target (the enemy unit), strike, counter, hp_after
      capture  target=None; progress_after, captures_now, capture_turns_left
      load     target (the transport); exposure describes the TRANSPORT
      supply   supplies: one SupplyFill per adjacent friendly this refills
      wait     nothing extra

    `turn_start` (attack/capture/supply/wait) is what the owner's NEXT turn
    start does to the unit parked on this tile -- supply.TurnStart, with the
    caveats stated on _turn_start_facts. None on loads: a passenger neither
    burns nor crashes, and is serviced by nothing but a Cruiser's cargo
    supply, which needs the ride's type, not this tile.

    `hp_after` is your unit's worst-case internal HP once the counter has
    landed -- unit.hp when nothing counters. `exposure` is next enemy turn's
    focus fire on the ending tile, None when the worst case of this action is
    the unit not being alive to have a next turn.
    """
    kind: str
    unit: object
    tile: Coord
    move_cost: int
    terrain: str
    stars: int
    fuel_after: int
    exposure: Optional[object]                 # threat.FocusFire
    target: object = None
    strike: Optional[object] = None            # damage.Outcome
    counter: Optional[object] = None           # damage.Outcome
    hp_after: int = 0
    progress_after: int = 0
    captures_now: bool = False
    capture_turns_left: Optional[int] = None
    turn_start: Optional[object] = None        # supply.TurnStart
    supplies: Tuple = ()                       # SupplyFill per refilled unit


@dataclass(frozen=True)
class SupplyFill:
    """One adjacent friendly a Supply action refills -- to the stats maxima,
    free (measured: funds unchanged, fixture rows supply-in-place and
    S4-auto-supply-two-sides)."""
    target: object
    fuel_to: int
    ammo_to: int


# --------------------------------------------------------------------------
# CO resolution -- Attack.between wants ids, boards may not carry them
# --------------------------------------------------------------------------

def _co_pair(board, attacker, defender, co_ids, warnings) -> tuple:
    """(attacking CO id, defending CO id), or (None, None) with a warning.

    Falls back to neutral OUT LOUD when either side's CO is unknown, for the
    same reason threat.py does: silently assuming neutral is how a Max quote
    comes out a third low.
    """
    ids = []
    for u in (attacker, defender):
        cid = None
        if co_ids and u.player in co_ids:
            cid = co_ids[u.player]
        else:
            try:
                cid = board.army(u.player).co_id
            except (StopIteration, AttributeError):
                cid = None
        ids.append(cid)
    if ids[0] is None or ids[1] is None:
        missing = sorted({u.player for u, c in zip((attacker, defender), ids)
                          if c is None})
        note = (f"CO unknown for player(s) {missing} -- this dump predates the "
                f"co_id field, so attack quotes assume neutral COs and the "
                f"standard 0..9 luck roll. Re-dump with the current "
                f"mgba_state.lua to fix it.")
        if note not in warnings:
            warnings.append(note)
        return None, None
    return ids[0], ids[1]


def _build_attack(board, attacker, defender, co_ids, warnings) -> Optional[object]:
    """The damage.Attack for `attacker` shooting `defender` where it stands.

    Terrain stars are the DEFENDER's, move-type aware (the sky substitution).
    With both COs known this goes through Attack.between, which fills the
    per-unit pool, the universal pair and the per-CO luck range; otherwise
    neutral, with the warning already raised by _co_pair.
    """
    d_move = pathing.unit_stats(defender.type)["move_type"]
    stars = board.defence_for(defender.x, defender.y, d_move)
    a_co, d_co = _co_pair(board, attacker, defender, co_ids, warnings)
    kw = dict(attacker_hp=attacker.hp, defender_hp=defender.hp,
              terrain_stars=stars, ammo=attacker.ammo,
              defender_dived=defender.dived, attacker_dived=attacker.dived)
    if a_co is None:
        return damage.Attack(attacker=attacker.type, defender=defender.type, **kw)
    return damage.Attack.between(attacker.type, defender.type, a_co, d_co, **kw)


# --------------------------------------------------------------------------
# the post-trade board
# --------------------------------------------------------------------------

def _after_trade(board, unit, enemy, strike, counter):
    """The board once the attack has happened, worst case for `unit` in one
    consistent world -- the low opening roll.

    The target is removed only on a guaranteed kill; otherwise it stands at
    its STRONGEST surviving HP, which is the same roll that produces the
    biggest counter, so pairing max_remaining_hp with the counter's
    min_remaining_hp is one scenario and not two pessimisms multiplied.

    Returns (board, your unit on it), or (None, None) when the worst case is
    your unit dead -- there is no next turn to project for it.
    """
    my_hp = counter.min_remaining_hp if counter else unit.hp
    if my_hp <= 0:
        return None, None
    me = dataclasses.replace(unit, hp=my_hp)
    units = []
    for u in board.units:
        if u.slot == enemy.slot:
            if strike.guaranteed_kill:
                continue
            units.append(dataclasses.replace(u, hp=strike.max_remaining_hp))
        elif u.slot == unit.slot:
            units.append(me)
        else:
            units.append(u)
    # The visibility array is a photograph of the pre-trade board; a board
    # with a unit removed or weakened keeps it, because sight lines do not
    # depend on HP -- but focus_fire will drop it anyway once it relocates.
    return dataclasses.replace(board, units=units), me


def _loaded_board(board, unit, transport):
    """The board with `unit` inside `transport`, for scoring the ride."""
    units = []
    for u in board.units:
        if u.slot == unit.slot:
            units.append(dataclasses.replace(u, x=transport.x, y=transport.y,
                                             loaded=True))
        elif u.slot == transport.slot:
            units.append(dataclasses.replace(u, cargo=unit.slot, carrying=True))
        else:
            units.append(u)
    hypo = dataclasses.replace(board, units=units, vision=None)
    rider = next(u for u in hypo.units if u.slot == transport.slot)
    return hypo, rider


# --------------------------------------------------------------------------
# enumeration
# --------------------------------------------------------------------------

def _capture_gain(board, unit, co_ids, warnings) -> int:
    """Capture points this unit adds per action -- the ROM's own arithmetic,
    read at 0x08026180-0x080261FC:

        gain = bars + (bars >> (8 - shift))      bars  = ceil(hp / 10)
                                                 shift = CO record +0x0D

    The shift is 0 for every CO but Sami, whose 7 makes the bonus bars >> 1 --
    the documented 1.5x, truncated, and a real difference in this layer: a
    7-bar Sami infantry gains 10 a turn where anyone else's gains 7. An
    unknown CO falls back to the neutral bar count out loud, like every other
    unknown here. Assumes CO abilities are on ([0x03004318] set), the same
    assumption every quote in this module already makes.
    """
    bars = unit.bars
    cid = _co_of(board, unit.player, co_ids)
    if cid is None:
        note = (f"P{unit.player}'s CO is unknown -- capture rate assumes no CO "
                f"bonus. Re-dump with the current mgba_state.lua to fix it.")
        if note not in warnings:
            warnings.append(note)
        return bars
    return bars + (bars >> (8 - co_mod.capture_shift(cid)))


def _co_of(board, player, co_ids):
    if co_ids and player in co_ids:
        return co_ids[player]
    try:
        return board.army(player).co_id
    except (StopIteration, AttributeError):
        return None


_KIND_ORDER = {"attack": 0, "capture": 1, "supply": 2, "load": 3, "wait": 4}


def _neighbours(board, tile):
    x, y = tile
    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if 0 <= nx < board.width and 0 <= ny < board.height:
            u = board.unit_at(nx, ny)
            if u is not None:
                yield u


def _turn_start_facts(board, unit, tile, fuel_after, co_ids, warnings,
                      owner_override=None):
    """supply.TurnStart for `unit` ending its move on `tile`.

    Facts with their caveats stated, per the module rule:

      * funds are TODAY's -- the game pays income before repairing
        (DERIVATION 33's measured order), so a repair this quotes as broke
        may in fact be paid; income is production-module territory.
      * whether repair charges at all is the settings byte 0x03004357,
        which old dumps do not carry -- when unknown this assumes it
        CHARGES, out loud, because a silently free repair is how a broke
        army gets promised HP it will not receive.
      * APC adjacency is a photograph of the current board: friendlies that
        move away before the turn ends take the top-up with them.

    `owner_override` is for a capture that completes this turn: the tile is
    the capturer's at turn start, whatever the board says today.
    """
    x, y = tile
    terrain_id = board.terrain[y][x]
    owner = board.owner[y][x] if owner_override is None else owner_override
    co = _co_of(board, unit.player, co_ids)
    try:
        army = board.army(unit.player)
        funds, power = army.funds, army.power_active
    except (StopIteration, AttributeError):
        funds, power = None, False
    free = getattr(board, "repair_free", None)
    if (free is None
            and supply_mod.serviced_at(unit.type, terrain_id, owner,
                                       unit.player)):
        note = ("this dump predates the repair-free byte (0x03004357), so "
                "repair quotes assume repairs CHARGE funds; a VS game with "
                "the free-repair rule will repair more than quoted. Re-dump "
                "with the current mgba_state.lua to fix it.")
        if note not in warnings:
            warnings.append(note)
    adjacent = any(u.player == unit.player and u.slot != unit.slot
                   and supply_mod.is_supplier(u.type)
                   for u in _neighbours(board, tile))
    return supply_mod.turn_start(
        unit.type, hp=unit.hp, fuel=max(0, fuel_after),
        ammo=unit.ammo, terrain_id=terrain_id, tile_owner=owner,
        player=unit.player, funds=funds, charge=not free, co_id=co,
        power=power, dived=unit.dived, loaded=False, apc_adjacent=adjacent)


def actions_for(board, unit, *, co_ids: Optional[dict] = None,
                weather: Optional[str] = None, fog: Optional[bool] = None,
                fog_rules: Optional[dict] = None,
                warnings: Optional[list] = None) -> List[Action]:
    """Every legal action `unit` has this turn, resolved. Deterministic order
    (attacks, captures, loads, waits; then by tile and target), which is a
    filing convention and not a recommendation.

    A unit that has acted or is riding a transport has no actions. Under fog
    only enemies the player can legally see are offered as targets, and the
    exposure numbers carry the blind-spot counts; fog UNKNOWN is warned about
    rather than treated as off, same as everywhere else.
    """
    warnings = warnings if warnings is not None else []
    if unit.acted or unit.loaded:
        return []

    my_stats = pathing.unit_stats(unit.type)
    my_move = my_stats["move_type"]
    fog_on = bool(threat.fog_active(board, fog))
    dests = pathing.destinations(board, unit, weather)
    fire_from = threat.firing_positions(board, unit, weather)
    lo, hi = my_stats["min_range"], my_stats["max_range"]
    enemies = threat.hostiles(board, unit.player, ignore_acted=True,
                              fog=fog_on, rule_set=fog_rules)

    out: List[Action] = []

    def facts(tile):
        return dict(terrain=board.terrain_name(*tile),
                    stars=board.defence_for(tile[0], tile[1], my_move),
                    fuel_after=unit.fuel - dests[tile])

    for tile, cost in dests.items():
        blocker = board.unit_at(*tile)

        # -- load: the only destination another unit may share ---------------
        if blocker is not None and blocker.slot != unit.slot:
            hypo, rider = _loaded_board(board, unit, blocker)
            ff = threat.focus_fire(hypo, rider, tile, co_ids=co_ids,
                                   weather=weather, fog=fog,
                                   fog_rules=fog_rules, warnings=warnings)
            out.append(Action(kind="load", unit=unit, tile=tile, move_cost=cost,
                              exposure=ff, target=blocker, hp_after=unit.hp,
                              **facts(tile)))
            continue

        # -- wait ------------------------------------------------------------
        ff = threat.focus_fire(board, unit, tile, co_ids=co_ids,
                               weather=weather, fog=fog,
                               fog_rules=fog_rules, warnings=warnings)
        ts = _turn_start_facts(board, unit, tile, unit.fuel - cost,
                               co_ids, warnings)
        out.append(Action(kind="wait", unit=unit, tile=tile, move_cost=cost,
                          exposure=ff, hp_after=unit.hp, turn_start=ts,
                          **facts(tile)))

        # -- supply -----------------------------------------------------------
        if supply_mod.is_supplier(unit.type):
            fills = tuple(
                SupplyFill(target=u,
                           fuel_to=supply_mod.resupply_caps(u.type)[0],
                           ammo_to=supply_mod.resupply_caps(u.type)[1])
                for u in _neighbours(board, tile)
                if u.player == unit.player and u.slot != unit.slot
                and supply_mod.can_be_supplied(u.type)
                and supply_mod.needs_supply(u.type, u.fuel, u.ammo))
            if fills:
                out.append(Action(kind="supply", unit=unit, tile=tile,
                                  move_cost=cost, exposure=ff,
                                  hp_after=unit.hp, turn_start=ts,
                                  supplies=fills, **facts(tile)))

        # -- capture ----------------------------------------------------------
        x, y = tile
        if (my_stats["unit_class"] == "foot"
                and board.terrain[y][x] in _capturable()
                and board.owner[y][x] != unit.player):
            progress = unit.capture if tile == (unit.x, unit.y) else 0
            gained = _capture_gain(board, unit, co_ids, warnings)
            remaining = CAPTURE_GOAL - progress
            # a capture that finishes makes the tile OURS at turn start --
            # a captured city starts repairing its capturer the same night
            ts_cap = ts if gained < remaining else _turn_start_facts(
                board, unit, tile, unit.fuel - cost, co_ids, warnings,
                owner_override=unit.player)
            out.append(Action(
                kind="capture", unit=unit, tile=tile, move_cost=cost,
                exposure=ff, hp_after=unit.hp, turn_start=ts_cap,
                progress_after=min(CAPTURE_GOAL, progress + gained),
                captures_now=gained >= remaining,
                capture_turns_left=-(-remaining // max(1, gained)),
                **facts(tile)))

    # -- attacks ---------------------------------------------------------------
    for enemy in enemies:
        if not damage.can_attack(unit.type, enemy.type, unit.ammo,
                                 defender_dived=enemy.dived):
            continue
        atk = _build_attack(board, unit, enemy, co_ids, warnings)
        strike = damage.resolve(atk)
        if strike is None:
            continue
        a_co, d_co = _co_pair(board, unit, enemy, co_ids, warnings)
        for tile in fire_from:
            dist = threat.manhattan(tile, (enemy.x, enemy.y))
            if not lo <= dist <= hi:
                continue
            my_stars = board.defence_for(tile[0], tile[1], my_move)
            counter = damage.counterattack(
                atk, attacker_stars=my_stars, defender_ammo=enemy.ammo,
                attacker_co=a_co, defender_co=d_co)
            hypo, me = _after_trade(board, unit, enemy, strike, counter)
            ff = None
            if hypo is not None:
                ff = threat.focus_fire(hypo, me, tile, co_ids=co_ids,
                                       weather=weather, fog=fog,
                                       fog_rules=fog_rules, warnings=warnings)
            my_hp = counter.min_remaining_hp if counter else unit.hp
            # turn-start facts for the survivor at its worst-case HP -- a
            # unit that may already be dead gets no next morning to quote
            ts_atk = None
            if my_hp > 0:
                ts_atk = _turn_start_facts(
                    board, dataclasses.replace(unit, hp=my_hp), tile,
                    unit.fuel - fire_from[tile], co_ids, warnings)
            out.append(Action(
                kind="attack", unit=unit, tile=tile, move_cost=fire_from[tile],
                terrain=board.terrain_name(*tile), stars=my_stars,
                fuel_after=unit.fuel - fire_from[tile],
                exposure=ff, target=enemy, strike=strike, counter=counter,
                hp_after=my_hp, turn_start=ts_atk))

    out.sort(key=lambda a: (_KIND_ORDER[a.kind], a.tile,
                            a.target.slot if a.target else -1))
    return out


def all_actions(board, player: int, *, co_ids: Optional[dict] = None,
                weather: Optional[str] = None, fog: Optional[bool] = None,
                fog_rules: Optional[dict] = None,
                warnings: Optional[list] = None) -> Dict[int, List[Action]]:
    """slot -> actions, for every unit of `player` that could still act."""
    warnings = warnings if warnings is not None else []
    return {u.slot: actions_for(board, u, co_ids=co_ids, weather=weather,
                                fog=fog, fog_rules=fog_rules, warnings=warnings)
            for u in pathing.movable_units(board, player)}

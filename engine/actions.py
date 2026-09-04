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
                                              ending tile, on the board
                                              sim.apply() says the action
                                              leaves behind (the forward
                                              model, engine/sim.py)

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
  * JOIN onto a damaged friendly of the same type anywhere `reachable()`
    goes: the pair rule, the display-bar sum, the refund for bars over 10,
    the capped fuel and ammo, the capture progress inherited from the
    target -- all measured (DERIVATION 34, engine/join.py). The exposure is
    the MERGED unit's on that tile, on the board with the target gone.

  * DROP a passenger from any destination the transport's unload-from table
    allows, onto any neighbour the PASSENGER can stand on and nobody
    occupies -- the three table parts and the occupancy check measured in
    DERIVATION 35 (engine/unload.py); the tile the transport just left is
    free. One action per (passenger, tile); both cargo slots are read now.
    The exposure is the dropped unit's, on its tile, with the transport
    parked beside it.

  * BUILD -- the army's action, kept apart from the per-unit enumeration in
    `build_actions(board, player)`: every own EMPTY factory, every unit its
    shop lists (the ROM's shop order filtered by class mask), priced with
    the CO's value multiplier, flagged affordable, with the new unit's
    exposure on the factory tile (it is created acted, full, and sits there
    until morning) and its turn-start facts. DERIVATION 36,
    engine/production.py. Unaffordable offers are listed, not hidden --
    the game greys them, and a quote that omits them would hide the one
    number a saving plan needs.

  * POWER -- the other army action, `power_action(board, player)`: offered
    when the meter is at the uses-scaled threshold (the map-menu gate,
    measured in DERIVATION 37), with what activating does to THIS board
    from engine/power.py -- Andy's heals per unit, Eagle's refreshed units,
    Drake's damage per enemy, Sturm's three candidate meteors, Olaf's snow,
    the power stat block everyone gets. The facts do not re-enumerate the
    turn after activation; a caller that wants "Eagle refreshes, then the
    Tank attacks" composes it from actions_for on the refreshed board.

  * TRAP -- under fog, the tiles the game's own move grid would offer that
    hold a HIDDEN enemy (DERIVATION 38: enterable, never expanded through).
    Picking one moves the unit to the tile before it, charges fuel for the
    tiles travelled, and spends the action with no menu. Offered as kind
    "trap" with `drop_tile` = where the unit actually stops and the
    exposure and turn-start facts THERE, so a board that knows where the
    hidden units stand says so instead of pretending the tile is a
    destination. reachable()/destinations() are already the game's fill
    for everything else: hidden enemies block passage exactly like visible
    ones, so no move is more optimistic than the game.
    The same kind covers the clear-weather ambush on a SUBMERGED enemy sub
    the player is not shown (DERIVATION 41, pathing.conceal_traps): there
    the game's grid enters and expands THROUGH the sub, so every tile it
    offers only by way of the sub -- the sub's own tile and everything
    beyond it -- is a trap that stops the mover where its route meets the
    sub. Under fog a submerged sub on an unlit tile is handled by the fog
    rule; which of the two grids the game uses when both apply is unread.

  * DIVE and RISE -- the menu's own gates, read off the predicate table
    (DERIVATION 40): Dive is offered to the table's one diver (`can_dive`,
    a code constant the extractor reads off the predicate and labels as
    such) while its dive bit is clear; Rise to ANY unit whose bit is set,
    type untested, exactly as the ROM has it. Both on every destination
    that is neither a load nor a join, which is every tile the wait loop
    reaches. Either spends the action and changes nothing but the bit.
    The exposure is at the tile with the bit toggled -- submerged, only
    the dived table's hunters reach it -- and `turn_start` carries the
    flat 5 burn a dived sub pays (engine/supply.py).

WHAT IS NOT MODELLED, deliberately and visibly

  * Nothing, at present. The last one -- a dived sub's concealment -- is
    measured (DERIVATION 41) and composed in: an enemy sub the player is
    not shown is neither a target nor a projected attacker (threat.hostiles,
    with a warning), your own dived sub's exposure keeps only the hunters
    that would be SHOWN it when their menu opens (threat._reveal_filter:
    parked adjacent, on their property, or revealed by another of their
    units parking alongside first), and the tiles the game offers you only
    through a concealed sub are traps. The reveal filter's approximations
    are stated where it lives.

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
    from . import damage, join as join_mod, pathing, supply as supply_mod, threat
    from . import power as power_mod
    from . import production as prod_mod
    from . import sim as sim_mod
    from . import unload as unload_mod
    from .state import Unit as _Unit, DIVE_FLAG
except ImportError:                     # imported as actions, engine/ on path
    import co as co_mod
    import damage
    import join as join_mod
    import pathing
    import supply as supply_mod
    import threat
    import power as power_mod
    import production as prod_mod
    import sim as sim_mod
    import unload as unload_mod
    from state import Unit as _Unit, DIVE_FLAG

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

    `kind` is one of "attack", "build", "capture", "dive", "drop", "join",
    "load", "power", "rise", "supply", "trap", "wait". The optional fields
    are populated per kind and None/zero otherwise:

      attack   target (the enemy unit), strike, counter, hp_after
      build    unit=None (an army action); build_type, cost, affordable,
               target = the unit as it will stand on the tile (acted)
      power    unit=None (an army action); power = power.Activation
      capture  target=None; progress_after, captures_now, capture_turns_left
      dive     nothing extra; exposure and turn_start describe the unit
      rise     SUBMERGED (dive) or SURFACED (rise) on this tile
      drop     target (the passenger), drop_tile (where it lands, acted);
               exposure and turn_start describe the PASSENGER there
      trap     target (the hidden enemy), tile = what the player would pick,
               drop_tile = where the unit really stops; facts are there
      join     target (the friendly merged into); hp_after, fuel_after and
               merge (join.Merge: ammo, refund, inherited capture progress)
      load     target (the transport); exposure describes the TRANSPORT
      supply   supplies: one SupplyFill per adjacent friendly this refills
      wait     nothing extra

    `turn_start` (attack/capture/dive/rise/supply/wait) is what the owner's NEXT turn
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
    merge: Optional[object] = None             # join.Merge on a join
    drop_tile: Optional[Coord] = None          # where a drop lands
    build_type: Optional[str] = None           # what a build buys
    power: Optional[object] = None             # power.Activation on a power
    cost: int = 0                              # its price
    affordable: bool = True                    # funds >= cost


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
    # the power stat block is live while +0x1E is up, the opponent's turn
    # included (DERIVATION 27) -- both sides' flags reach the quote
    return damage.Attack.between(attacker.type, defender.type, a_co, d_co,
                                 attacker_power=_power_active(board, attacker.player),
                                 defender_power=_power_active(board, defender.player),
                                 **kw)


def _power_active(board, player) -> bool:
    try:
        return bool(board.army(player).power_active)
    except (StopIteration, AttributeError):
        return False


# --------------------------------------------------------------------------
# the board an action leaves behind comes from engine/sim.py: the Action is
# built first with no exposure, sim.apply() advances the board, and the
# exposure is scored on that. One forward model, shared with any planner.
# --------------------------------------------------------------------------

def _after(board, act, *, co_ids=None, warnings=None):
    """(board after `act` in the worst case for its actor, the actor on it or
    None if that worst case is its death)."""
    hypo = sim_mod.apply(board, act, luck="min", co_ids=co_ids,
                         warnings=warnings)
    return hypo, sim_mod.unit_in(hypo, act.unit.slot) if act.unit else None


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


_KIND_ORDER = {"attack": 0, "capture": 1, "join": 2, "drop": 3, "supply": 4,
               "load": 5, "wait": 6, "dive": 7, "rise": 8, "trap": 9,
               "build": 10, "power": 11}


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
    (attacks, captures, joins, drops, supplies, loads, waits, dives/rises,
    traps; then by tile and target), which is a filing convention and not a
    recommendation.

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
    cid = _co_of(board, unit.player, co_ids)
    if cid is not None and hi > 1:          # the CO's range adjustment on an
        hi += co_mod.range_bonus(cid, unit.type,      # indirect's maximum
                                 _power_active(board, unit.player))
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
            act = Action(kind="load", unit=unit, tile=tile, move_cost=cost,
                         exposure=None, target=blocker, hp_after=unit.hp,
                         **facts(tile))
            hypo, _ = _after(board, act, co_ids=co_ids, warnings=warnings)
            rider = sim_mod.unit_in(hypo, blocker.slot)
            ff = threat.focus_fire(hypo, rider, tile, co_ids=co_ids,
                                   weather=weather, fog=fog,
                                   fog_rules=fog_rules, warnings=warnings)
            out.append(dataclasses.replace(act, exposure=ff))
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

        # -- dive / rise -----------------------------------------------------
        # the menu's gates (DERIVATION 40): Dive to the table's diver while
        # the bit is clear, Rise to anything with the bit set, on every
        # destination that is not a load or a join -- every tile this loop
        # reaches. The action flips the bit and ends the turn; the facts are
        # for the unit as it will then stand: submerged (only the dived
        # table's hunters reach it, burning a flat 5) or surfaced again.
        toggle = None
        if my_stats["can_dive"] and not unit.dived:
            toggle = "dive"
        elif unit.dived:
            toggle = "rise"
        if toggle is not None:
            act = Action(kind=toggle, unit=unit, tile=tile, move_cost=cost,
                         exposure=None, hp_after=unit.hp, **facts(tile))
            hypo, me = _after(board, act, co_ids=co_ids, warnings=warnings)
            ff_t = threat.focus_fire(hypo, me, tile, co_ids=co_ids,
                                     weather=weather, fog=fog,
                                     fog_rules=fog_rules, warnings=warnings)
            ts_t = _turn_start_facts(hypo, me, tile, unit.fuel - cost,
                                     co_ids, warnings)
            out.append(dataclasses.replace(act, exposure=ff_t, turn_start=ts_t))

    # -- traps ------------------------------------------------------------------
    # under fog, the hidden enemies' tiles the game's grid offers: picking
    # one ends the move one tile short, acted, no menu (DERIVATION 38); in
    # the clear, the tiles offered only through a submerged sub the player
    # is not shown -- the sub's tile and everything beyond it (DERIVATION 41)
    def trap_action(picked, stop, paid, enemy):
        act = Action(kind="trap", unit=unit, tile=picked, move_cost=paid,
                     terrain=board.terrain_name(*stop),
                     stars=board.defence_for(stop[0], stop[1], my_move),
                     fuel_after=max(0, unit.fuel - paid), exposure=None,
                     target=enemy, drop_tile=stop, hp_after=unit.hp)
        hypo, me = _after(board, act, co_ids=co_ids, warnings=warnings)
        ff = threat.focus_fire(hypo, me, stop, co_ids=co_ids,
                               weather=weather, fog=fog,
                               fog_rules=fog_rules, warnings=warnings)
        ts = _turn_start_facts(hypo, me, stop, me.fuel, co_ids, warnings)
        return dataclasses.replace(act, exposure=ff, turn_start=ts)

    hidden = set()
    if fog_on:
        seen = {u.slot for u in threat.fog_mod.visible_units(board, unit.player,
                                                             fog_rules)}
        hidden = {u.slot for u in board.units
                  if u.player != unit.player and u.player != 0
                  and u.slot not in seen}
        for h, (stop, paid) in pathing.trap_tiles(board, unit, hidden,
                                                   weather).items():
            out.append(trap_action(h, stop, paid, board.unit_at(*h)))
    concealed = {u.slot for u in threat.fog_mod.concealed_units(board, unit.player)
                 if u.slot not in hidden}
    if concealed:
        for picked, (stop, paid, sub_tile) in pathing.conceal_traps(
                board, unit, concealed, weather).items():
            out.append(trap_action(picked, stop, paid, board.unit_at(*sub_tile)))

    # -- drops ------------------------------------------------------------------
    # one action per (passenger, landing tile), on every destination the
    # transport's unload-from table allows; the transport's own origin
    # counts as free (the game dropped onto it -- fixture row D6).
    passengers = [board.cargo_of(unit)] if unit.cargo else []
    if getattr(unit, "cargo2", 0):
        p2 = next((u for u in board.units if u.slot == unit.cargo2), None)
        if p2 is not None:
            passengers.append(p2)
    for tile, cost in dests.items():
        blocker = board.unit_at(*tile)
        if blocker is not None and blocker.slot != unit.slot:
            continue                            # a load destination, not ours
        for p in passengers:
            if p is None:
                continue
            for land in unload_mod.drop_tiles(board, unit, tile, p.type):
                act = Action(kind="drop", unit=unit, tile=tile,
                             move_cost=cost, exposure=None, target=p,
                             drop_tile=land, hp_after=p.hp, **facts(tile))
                hypo, _ = _after(board, act, co_ids=co_ids, warnings=warnings)
                walker = sim_mod.unit_in(hypo, p.slot)
                ff = threat.focus_fire(hypo, walker, land, co_ids=co_ids,
                                       weather=weather, fog=fog,
                                       fog_rules=fog_rules, warnings=warnings)
                ts = _turn_start_facts(hypo, walker, land, walker.fuel,
                                       co_ids, warnings)
                out.append(dataclasses.replace(act, exposure=ff, turn_start=ts))

    # -- joins ------------------------------------------------------------------
    # destinations() refuses tiles a friendly stands on unless it is a
    # transport with room; a join ENDS on a friendly, so it comes from the
    # pass-through reachable set instead, filtered by the pair rule.
    if not unit.cargo and not unit.carrying:
        for tile, cost in pathing.reachable(board, unit, weather).items():
            if tile in dests:
                continue
            partner = board.unit_at(*tile)
            if partner is None or not join_mod.can_join(unit, partner):
                continue
            co = _co_of(board, unit.player, co_ids)
            try:
                power = board.army(unit.player).power_active
            except (StopIteration, AttributeError):
                power = False
            m = join_mod.merge(unit.type, mover_hp=unit.hp,
                               target_hp=partner.hp,
                               mover_fuel_after_move=max(0, unit.fuel - cost),
                               target_fuel=partner.fuel, mover_ammo=unit.ammo,
                               target_ammo=partner.ammo,
                               target_capture=partner.capture,
                               co_id=co, power=power)
            act = Action(kind="join", unit=unit, tile=tile,
                         move_cost=cost, exposure=None, target=partner,
                         hp_after=m.hp_after, merge=m,
                         terrain=board.terrain_name(*tile),
                         stars=board.defence_for(tile[0], tile[1], my_move),
                         fuel_after=m.fuel_after)
            hypo, me = _after(board, act, co_ids=co_ids, warnings=warnings)
            ff = threat.focus_fire(hypo, me, tile, co_ids=co_ids,
                                   weather=weather, fog=fog,
                                   fog_rules=fog_rules, warnings=warnings)
            ts = _turn_start_facts(hypo, me, tile, m.fuel_after, co_ids,
                                   warnings)
            out.append(dataclasses.replace(act, exposure=ff, turn_start=ts))

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
                attacker_co=a_co, defender_co=d_co,
                attacker_power=_power_active(board, unit.player),
                defender_power=_power_active(board, enemy.player))
            my_hp = counter.min_remaining_hp if counter else unit.hp
            act = Action(
                kind="attack", unit=unit, tile=tile, move_cost=fire_from[tile],
                terrain=board.terrain_name(*tile), stars=my_stars,
                fuel_after=unit.fuel - fire_from[tile],
                exposure=None, target=enemy, strike=strike, counter=counter,
                hp_after=my_hp)
            # the worst case for you in one consistent world -- the low
            # opening roll: sim.apply(luck="min") leaves the target at its
            # strongest and you at your weakest, or gone
            hypo, me = _after(board, act, co_ids=co_ids, warnings=warnings)
            ff = None
            if me is not None:
                ff = threat.focus_fire(hypo, me, tile, co_ids=co_ids,
                                       weather=weather, fog=fog,
                                       fog_rules=fog_rules, warnings=warnings)
            # turn-start facts for the survivor at its worst-case HP -- a
            # unit that may already be dead gets no next morning to quote
            ts_atk = None
            if my_hp > 0:
                ts_atk = _turn_start_facts(
                    board, dataclasses.replace(unit, hp=my_hp), tile,
                    unit.fuel - fire_from[tile], co_ids, warnings)
            out.append(dataclasses.replace(act, exposure=ff, turn_start=ts_atk))

    out.sort(key=lambda a: (_KIND_ORDER[a.kind], a.tile,
                            a.target.slot if a.target else -1))
    return out


def build_actions(board, player: int, *, co_ids: Optional[dict] = None,
                  weather: Optional[str] = None, fog: Optional[bool] = None,
                  fog_rules: Optional[dict] = None,
                  warnings: Optional[list] = None) -> List[Action]:
    """Every purchase the army could make this turn, one Action per
    (factory, unit), affordable or not -- DERIVATION 36.

    A factory is an own Base/Airport/Port with no unit on it. The new unit
    is acted, full, and stands there until morning, so its exposure is
    focus fire on that tile as the board stands, and its turn_start facts
    are the factory's own service (free resupply of a full unit: nothing).
    When the army's 50 slots are full no action is offered and a warning
    says why; when funds are unknown every offer is flagged unaffordable
    out loud.
    """
    warnings = warnings if warnings is not None else []
    out: List[Action] = []
    try:
        army = board.army(player)
        funds, power = army.funds, army.power_active
    except (StopIteration, AttributeError):
        funds, power = None, False
        note = "no army record for this player -- builds are listed unpriced as unaffordable"
        if note not in warnings:
            warnings.append(note)
    co = _co_of(board, player, co_ids)
    slot = prod_mod.free_slot(board, player)
    if slot is None:
        note = (f"P{player} holds all {prod_mod.ARMY_SLOTS} unit slots -- the "
                f"game builds nothing until one frees up")
        if note not in warnings:
            warnings.append(note)
        return out
    for y in range(board.height):
        for x in range(board.width):
            tid = board.terrain[y][x]
            if not prod_mod.is_factory(tid) or board.owner[y][x] != player:
                continue
            if board.unit_at(x, y) is not None:
                continue
            for off in prod_mod.offers(tid, funds if funds is not None else -1,
                                       co, power):
                st = pathing.unit_stats(off.unit_type)
                fresh = _Unit(
                    slot=slot, player=player, type=off.unit_type, x=x, y=y,
                    hp=100, ammo=st["max_ammo"], capture=0,
                    fuel=st["max_fuel"], acted=True, carrying=False,
                    loaded=False, state=1, cargo=0)
                act = Action(
                    kind="build", unit=None, tile=(x, y), move_cost=0,
                    terrain=board.terrain_name(x, y),
                    stars=board.defence_for(x, y, st["move_type"]),
                    fuel_after=fresh.fuel, exposure=None, target=fresh,
                    hp_after=100, build_type=off.unit_type,
                    cost=off.price, affordable=off.affordable)
                hypo = sim_mod.apply(board, act)
                ff = threat.focus_fire(hypo, fresh, (x, y), co_ids=co_ids,
                                       weather=weather, fog=fog,
                                       fog_rules=fog_rules, warnings=warnings)
                ts = _turn_start_facts(hypo, fresh, (x, y), fresh.fuel,
                                       co_ids, warnings)
                out.append(dataclasses.replace(act, exposure=ff, turn_start=ts))
    return out


def power_action(board, player: int, *, co_ids: Optional[dict] = None,
                 warnings: Optional[list] = None) -> Optional[Action]:
    """The CO power as an action, when the meter is there -- DERIVATION 37.

    None when the CO is unknown or the meter is short; the Activation
    facts (meter, threshold, what the power does to this board) ride on
    `Action.power`. The Action has no tile: it is the army's, not a
    unit's, and costs no move.
    """
    warnings = warnings if warnings is not None else []
    co = _co_of(board, player, co_ids)
    facts = power_mod.activation(board, player, co_id=co, warnings=warnings)
    if facts is None or not facts.available:
        return None
    return Action(kind="power", unit=None, tile=(-1, -1), move_cost=0,
                  terrain="", stars=0, fuel_after=0, exposure=None,
                  power=facts)


def all_actions(board, player: int, *, co_ids: Optional[dict] = None,
                weather: Optional[str] = None, fog: Optional[bool] = None,
                fog_rules: Optional[dict] = None,
                warnings: Optional[list] = None) -> Dict[int, List[Action]]:
    """slot -> actions, for every unit of `player` that could still act."""
    warnings = warnings if warnings is not None else []
    return {u.slot: actions_for(board, u, co_ids=co_ids, weather=weather,
                                fog=fog, fog_rules=fog_rules, warnings=warnings)
            for u in pathing.movable_units(board, player)}

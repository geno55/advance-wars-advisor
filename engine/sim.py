"""The forward model: `apply(board, action) -> board`, and the turn boundary.

This is the piece the handoff called load-bearing. Every fact the action
layer states is per-action on the CURRENT board; a plan needs the board each
committed action leaves behind, and the only other way to get one is the
emulator. Everything here composes measured modules -- damage (the formula,
the counter, the dived table), join, supply (burn, crash, repair, resupply,
auto-supply in the game's own order), economy (income), co (meter charge,
thresholds, power effects), pathing (stats) -- and it is game-checkable in
the same way they are: one parked state, one action, dump-apply-drive-dump
and diff. That differential test is the next deliverable; until it has run,
the composition here is tested against the modules it composes and against
the action layer, which now builds its hypothetical boards through this
module rather than by hand.

WHAT IS EXACT (given the modules)

  * movement: the unit ends on the action's tile (a trap's stop tile), pays
    the path cost in fuel, and is acted. Moving off a property resets the
    capture progress the record carries (measured, A15).
  * attack: the strike is a POINT once the luck is chosen -- `luck` is the
    reduced roll the attacking CO's range allows ("min", "max", an int, or
    an RNG state via `rng_state`, see rng.strike_luck); the counter is the
    survivor's raw HP through the measured counter formula, no luck of its
    own; a primary shot spends one round on whichever side fired it; a unit
    at 0 HP is removed with anything it carried; both meters gain
    co.charge_gains unless that army's power is up, clamped at the
    uses-scaled threshold.
  * capture: progress += the CO-aware gain; at 20 the tile is the
    capturer's and the record's progress is 0. A Wait IN PLACE keeps the
    progress and the next Capt continues from it (measured, DERIVATION 42:
    10 kept across a Wait, then 10 -> 20 and the city fell); an attack from
    the tile is assumed to behave the same, being the same "did not move".
  * load / drop / join / supply / dive / rise / build: the record edits the
    game makes, per DERIVATION 33-40, and nothing else.
  * power: meter 0, uses+1, the block up; Andy's heals, Eagle's refreshes,
    Drake's damage, Olaf's snow, Sturm's meteor for the strategy given
    (or the RNG state's), all from engine/power.py.
  * end_turn: the next army in player order becomes active (the day turns
    over when the order wraps); its power block and Olaf's snow expire;
    income; then its units in slot order through supply.turn_start with the
    treasury threaded from one repair to the next, crashed units removed;
    its acted bits cleared; the vision photograph dropped.

WHAT IS STATED RATHER THAN MEASURED -- each a line in ASSUMPTIONS

  * the weather Olaf's snow reverts to at expiry: Clear, whatever it was.
  * Sturm's strategy when neither `meteor_strategy` nor `rng_state` is
    given: 0, with a warning -- a planner that wants the worst case asks
    for each in turn.
  * mass damage (Drake, Sturm) and the meters: no charge assumed.
  * a passenger riding a Cruiser is not resupplied by the ride here.
  * capturing the HQ ends the match; here the tile merely changes hands.
  * the funds cap 999,999 is the shop's and the join refund's (measured);
    income is assumed to obey the same clamp.

Nothing in this file names a unit type; tests/test_sim.py enforces it.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:                                    # imported as engine.sim
    from . import co as co_mod
    from . import damage, economy, join as join_mod, pathing, power as power_mod
    from . import rng as rng_mod, supply as supply_mod
    from .state import DIVE_FLAG
except ImportError:                     # imported as sim, engine/ on path
    import co as co_mod
    import damage
    import economy
    import join as join_mod
    import pathing
    import power as power_mod
    import rng as rng_mod
    import supply as supply_mod
    from state import DIVE_FLAG

Coord = Tuple[int, int]

FUNDS_CAP = 999_999                     # 0x000F423F, the funds writer's clamp
CAPTURE_GOAL = 20
CLEAR_INDEX, SNOW_INDEX = 0, 1          # aw1_movecost tables: Clear, Snow, Rain
# A Wait taken on the property being captured keeps the record's progress
# (harness/mesen_capwait.lua, DERIVATION 42); only changing tile clears it.
CAPTURE_KEPT_ON_STAY = True


# --------------------------------------------------------------------------
# small board surgery
# --------------------------------------------------------------------------

def unit_in(board, slot: int):
    """The unit with this slot on this board, or None once it is gone."""
    return next((u for u in board.units if u.slot == slot), None)


def _with_units(board, units):
    return dataclasses.replace(board, units=list(units), vision=None)


def _replace(board, by_slot: dict):
    """A board with the given slots' units swapped for new records."""
    return _with_units(board, [by_slot.get(u.slot, u) for u in board.units])


def _remove(board, slots):
    """A board without these units -- and without whatever they carried."""
    gone = set(slots)
    for u in board.units:
        if u.slot in gone:
            gone |= {c for c in (u.cargo, getattr(u, "cargo2", 0)) if c}
    return _with_units(board, [u for u in board.units if u.slot not in gone])


def _army(board, player: int):
    return next((a for a in board.armies if a.player == player), None)


def _set_army(board, army):
    return dataclasses.replace(
        board, armies=[army if a.player == army.player else a
                       for a in board.armies])


def _add_funds(board, player: int, delta: int):
    army = _army(board, player)
    if army is None:
        return board
    funds = max(0, min(FUNDS_CAP, army.funds + delta))
    return _set_army(board, dataclasses.replace(army, funds=funds))


def _co_of(board, player: int, co_ids: Optional[dict]):
    if co_ids and player in co_ids:
        return co_ids[player]
    army = _army(board, player)
    return army.co_id if army is not None else None


def _power_of(board, player: int) -> bool:
    army = _army(board, player)
    return bool(army.power_active) if army is not None else False


def _moved(unit, tile: Coord, cost: int, **extra):
    """The unit as the move applier leaves it: on the tile, the path cost
    paid, acted, its capture progress gone if it changed tile (A15)."""
    fuel = max(0, unit.fuel - cost)
    capture = unit.capture if tile == (unit.x, unit.y) else 0
    return dataclasses.replace(unit, x=tile[0], y=tile[1], fuel=fuel,
                               acted=True, capture=capture, **extra)


# --------------------------------------------------------------------------
# battle
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Battle:
    """One resolved exchange: what each record reads afterwards."""
    luck: int
    strike: int                 # internal HP taken off the defender
    counter: int                # internal HP taken off the attacker (0: none)
    attacker_hp: int            # 0 = removed
    defender_hp: int
    attacker_ammo: int
    defender_ammo: int
    attacker_gain: int          # meter charge, before the power skip / clamp
    defender_gain: int


def _luck_value(atk, luck, rng_state: Optional[int]) -> int:
    if rng_state is not None:
        # range = -bad .. 9+good (rng.luck_reduce), so the CO's bounds give
        # the two reducer parameters back
        return rng_mod.strike_luck(rng_state, good=atk.luck_max - 9,
                                   bad=-atk.luck_min)
    if luck == "min":
        return atk.luck_min
    if luck == "max":
        return atk.luck_max
    if not isinstance(luck, int):
        raise ValueError(f"luck must be 'min', 'max' or an int, not {luck!r}")
    if not atk.luck_min <= luck <= atk.luck_max:
        raise ValueError(f"luck {luck} outside this CO's {atk.luck_min}.."
                         f"{atk.luck_max}")
    return luck


def _attack_between(board, attacker, defender, co_ids, warnings):
    """The damage.Attack for this pair where they stand, COs and power
    blocks from the armies -- neutral out loud when a CO is unknown, the
    same fallback the action layer makes."""
    d_move = pathing.unit_stats(defender.type)["move_type"]
    stars = board.defence_for(defender.x, defender.y, d_move)
    a_co = _co_of(board, attacker.player, co_ids)
    d_co = _co_of(board, defender.player, co_ids)
    kw = dict(attacker_hp=attacker.hp, defender_hp=defender.hp,
              terrain_stars=stars, ammo=attacker.ammo,
              defender_dived=defender.dived, attacker_dived=attacker.dived)
    if a_co is None or d_co is None:
        note = ("CO unknown for one side -- sim.battle resolves this attack "
                "with neutral COs and the standard 0..9 roll")
        if warnings is not None and note not in warnings:
            warnings.append(note)
        return damage.Attack(attacker=attacker.type, defender=defender.type,
                             **kw), None, None
    atk = damage.Attack.between(attacker.type, defender.type, a_co, d_co,
                                attacker_power=_power_of(board, attacker.player),
                                defender_power=_power_of(board, defender.player),
                                **kw)
    return atk, a_co, d_co


def battle(board, attacker, defender, *, luck="min",
           rng_state: Optional[int] = None, co_ids: Optional[dict] = None,
           warnings: Optional[list] = None) -> Battle:
    """Resolve `attacker` (standing where its record says) shooting
    `defender`, to a point.

    The strike is damage.damage_for_luck at the chosen roll; the counter is
    damage.counter_damage on the survivor -- both sides' CO modifiers on the
    base, the survivor's raw HP, the attacker's cover -- only at contact and
    only if the defender's weapon can answer (the dived table included).
    Primary shots spend a round. Meter gains are co.charge_gains on the
    display HP each side lost, a dead unit losing all of it.
    """
    atk, a_co, d_co = _attack_between(board, attacker, defender, co_ids,
                                      warnings)
    w = damage.select_weapon(attacker.type, defender.type, attacker.ammo,
                             defender_dived=defender.dived)
    if w is None:
        raise ValueError(f"{attacker.type} cannot attack {defender.type} here")
    lk = _luck_value(atk, luck, rng_state)
    strike = damage.damage_for_luck(atk, lk)
    d_hp = max(0, defender.hp - strike)
    a_ammo = attacker.ammo - 1 if w.slot == "primary" and attacker.ammo else attacker.ammo
    d_ammo = defender.ammo

    counter, a_hp = 0, attacker.hp
    contact = abs(attacker.x - defender.x) + abs(attacker.y - defender.y) == 1
    if (d_hp > 0 and contact and damage.fights_at_contact(attacker.type)
            and damage.fights_at_contact(defender.type)):
        wc = damage.select_weapon(defender.type, attacker.type, defender.ammo,
                                  defender_dived=attacker.dived)
        if wc is not None:
            if a_co is not None:
                back = damage.Attack.between(
                    defender.type, attacker.type, d_co, a_co,
                    attacker_power=_power_of(board, defender.player),
                    defender_power=_power_of(board, attacker.player))
                co_a, co_d = back.co_attack, back.co_defense
            else:
                co_a, co_d = 100, 100
            a_move = pathing.unit_stats(attacker.type)["move_type"]
            my_stars = board.defence_for(attacker.x, attacker.y, a_move)
            counter = damage.counter_damage(wc.base, d_hp, co_d, my_stars,
                                            attacker.hp, co_attack=co_a)
            a_hp = max(0, attacker.hp - counter)
            if wc.slot == "primary" and defender.ammo:
                d_ammo = defender.ammo - 1

    def lost(before, after):
        return damage.display_hp(before) - (damage.display_hp(after) if after else 0)

    def value(u, cid):
        cost = pathing.unit_stats(u.type)["cost"]
        if cid is None:
            return cost // 10
        return co_mod.unit_value(cost, cid, _power_of(board, u.player))

    a_gain, d_gain = co_mod.charge_gains(value(attacker, a_co),
                                         value(defender, d_co),
                                         lost(attacker.hp, a_hp),
                                         lost(defender.hp, d_hp))
    return Battle(luck=lk, strike=strike, counter=counter, attacker_hp=a_hp,
                  defender_hp=d_hp, attacker_ammo=a_ammo, defender_ammo=d_ammo,
                  attacker_gain=a_gain, defender_gain=d_gain)


def _charge(board, player: int, gain: int, co_ids):
    """Add meter charge the way 0x0801BF7C does: skipped while the power is
    up, clamped at the uses-scaled threshold."""
    army = _army(board, player)
    if army is None or army.power_active or gain <= 0:
        return board
    cid = _co_of(board, player, co_ids)
    meter = army.power + gain
    if cid is not None:
        meter = min(meter, co_mod.power_threshold(cid, army.power_uses or 0))
    return _set_army(board, dataclasses.replace(army, power=meter))


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------

def apply(board, action, *, luck="min", rng_state: Optional[int] = None,
          meteor_strategy: Optional[int] = None,
          co_ids: Optional[dict] = None,
          warnings: Optional[list] = None):
    """The board after `action` -- an actions.Action of any kind.

    Units are found by slot on THIS board, so an Action enumerated on an
    earlier board still applies as long as its unit and target are present;
    the tile and path cost are the Action's. `luck` (or `rng_state`) picks
    the strike's roll on an attack -- "min" is the worst case for the actor
    and the default, the same world the action layer scores exposure in.
    The input board is never modified.
    """
    k = action.kind
    if k == "build":
        return _apply_build(board, action)
    if k == "power":
        return _apply_power(board, action, meteor_strategy, rng_state,
                            co_ids, warnings)
    unit = unit_in(board, action.unit.slot)
    if unit is None:
        raise ValueError(f"unit #{action.unit.slot} is not on this board")
    if k == "wait":
        return _replace(board, {unit.slot: _moved(unit, action.tile,
                                                     action.move_cost)})
    if k == "trap":
        return _replace(board, {unit.slot: _moved(unit, action.drop_tile,
                                                     action.move_cost)})
    if k in ("dive", "rise"):
        state = (unit.state | DIVE_FLAG) if k == "dive" else (unit.state & ~DIVE_FLAG)
        return _replace(board, {unit.slot: _moved(unit, action.tile,
                                                     action.move_cost,
                                                     state=state)})
    if k == "capture":
        return _apply_capture(board, unit, action, co_ids, warnings)
    if k == "supply":
        return _apply_supply(board, unit, action)
    if k == "load":
        return _apply_load(board, unit, action)
    if k == "drop":
        return _apply_drop(board, unit, action)
    if k == "join":
        return _apply_join(board, unit, action, co_ids)
    if k == "attack":
        return _apply_attack(board, unit, action, luck, rng_state, co_ids,
                             warnings)
    raise ValueError(f"unknown action kind {k!r}")


def _apply_attack(board, unit, action, luck, rng_state, co_ids, warnings):
    enemy = unit_in(board, action.target.slot)
    if enemy is None:
        raise ValueError(f"target #{action.target.slot} is not on this board")
    me = _moved(unit, action.tile, action.move_cost)
    staged = _replace(board, {unit.slot: me})
    b = battle(staged, me, enemy, luck=luck, rng_state=rng_state,
               co_ids=co_ids, warnings=warnings)
    after = staged
    after = _charge(after, me.player, b.attacker_gain, co_ids)
    after = _charge(after, enemy.player, b.defender_gain, co_ids)
    swaps, dead = {}, []
    if b.attacker_hp > 0:
        swaps[me.slot] = dataclasses.replace(me, hp=b.attacker_hp,
                                             ammo=b.attacker_ammo)
    else:
        dead.append(me.slot)
    if b.defender_hp > 0:
        swaps[enemy.slot] = dataclasses.replace(enemy, hp=b.defender_hp,
                                                ammo=b.defender_ammo)
    else:
        dead.append(enemy.slot)
    after = _replace(after, swaps)
    return _remove(after, dead) if dead else after


def _capture_gain(board, unit, co_ids) -> int:
    """bars + (bars >> (8 - shift)), the ROM's own arithmetic (actions.py)."""
    bars = unit.bars
    cid = _co_of(board, unit.player, co_ids)
    if cid is None:
        return bars
    return bars + (bars >> (8 - co_mod.capture_shift(cid)))


def _apply_capture(board, unit, action, co_ids, warnings):
    x, y = action.tile
    me = _moved(unit, action.tile, action.move_cost)
    progress = me.capture + _capture_gain(board, unit, co_ids)
    if progress >= CAPTURE_GOAL:
        owner = [list(r) for r in board.owner]
        owner[y][x] = unit.player
        board = dataclasses.replace(board, owner=owner)
        progress = 0
    return _replace(board, {unit.slot: dataclasses.replace(me, capture=progress)})


def _apply_supply(board, unit, action):
    me = _moved(unit, action.tile, action.move_cost)
    swaps = {unit.slot: me}
    for fill in action.supplies:
        t = unit_in(board, fill.target.slot)
        if t is not None:
            swaps[t.slot] = dataclasses.replace(t, fuel=fill.fuel_to,
                                                ammo=fill.ammo_to)
    return _replace(board, swaps)


def _apply_load(board, unit, action):
    ride = unit_in(board, action.target.slot)
    if ride is None:
        raise ValueError(f"transport #{action.target.slot} is not on this board")
    rider = dataclasses.replace(_moved(unit, action.tile, action.move_cost,
                                       loaded=True), capture=0)
    if not ride.cargo:
        ride2 = dataclasses.replace(ride, cargo=unit.slot, carrying=True)
    else:
        ride2 = dataclasses.replace(ride, cargo2=unit.slot, carrying=True)
    return _replace(board, {unit.slot: rider, ride.slot: ride2})


def _apply_drop(board, unit, action):
    p = unit_in(board, action.target.slot)
    if p is None:
        raise ValueError(f"passenger #{action.target.slot} is not on this board")
    me = _moved(unit, action.tile, action.move_cost)
    kw = {}
    if me.cargo == p.slot:
        kw["cargo"] = 0
    elif getattr(me, "cargo2", 0) == p.slot:
        kw["cargo2"] = 0
    left = kw.get("cargo", me.cargo) or kw.get("cargo2", getattr(me, "cargo2", 0))
    me = dataclasses.replace(me, carrying=bool(left), **kw)
    walker = dataclasses.replace(p, x=action.drop_tile[0], y=action.drop_tile[1],
                                 loaded=False, acted=True)
    return _replace(board, {unit.slot: me, p.slot: walker})


def _apply_join(board, unit, action, co_ids):
    partner = unit_in(board, action.target.slot)
    if partner is None:
        raise ValueError(f"join target #{action.target.slot} is not on this board")
    m = action.merge
    if m is None:
        m = join_mod.merge(unit.type, mover_hp=unit.hp, target_hp=partner.hp,
                           mover_fuel_after_move=max(0, unit.fuel - action.move_cost),
                           target_fuel=partner.fuel, mover_ammo=unit.ammo,
                           target_ammo=partner.ammo, target_capture=partner.capture,
                           co_id=_co_of(board, unit.player, co_ids),
                           power=_power_of(board, unit.player))
    me = dataclasses.replace(unit, x=partner.x, y=partner.y, hp=m.hp_after,
                             fuel=m.fuel_after, ammo=m.ammo_after,
                             capture=m.capture_after, acted=True)
    after = _remove(board, [partner.slot])
    after = _replace(after, {unit.slot: me})
    return _add_funds(after, unit.player, m.refund) if m.refund else after


def _apply_build(board, action):
    fresh = action.target
    if unit_in(board, fresh.slot) is not None:
        raise ValueError(f"slot {fresh.slot} is already taken on this board")
    if board.unit_at(*action.tile) is not None:
        raise ValueError(f"the factory at {action.tile} is occupied")
    after = _with_units(board, board.units + [fresh])
    return _add_funds(after, fresh.player, -action.cost)


def _apply_power(board, action, meteor_strategy, rng_state, co_ids, warnings):
    facts = action.power
    player = facts.player
    army = _army(board, player)
    if army is None:
        raise ValueError(f"no army record for P{player}")
    after = _set_army(board, dataclasses.replace(
        army, power=0, power_uses=(army.power_uses or 0) + 1,
        power_active=True, power_ready=False))
    after = dataclasses.replace(after, vision=None)
    swaps: Dict[int, object] = {}
    for u, hp_after in facts.heals:
        cur = unit_in(after, u.slot)
        if cur is not None:
            swaps[u.slot] = dataclasses.replace(cur, hp=hp_after)
    for u in facts.refreshes:
        cur = swaps.get(u.slot) or unit_in(after, u.slot)
        if cur is not None:
            swaps[u.slot] = dataclasses.replace(cur, acted=False)
    for u, hp_after in facts.damages:
        cur = swaps.get(u.slot) or unit_in(after, u.slot)
        if cur is not None:
            swaps[u.slot] = dataclasses.replace(cur, hp=hp_after)
    dead: List[int] = []
    if facts.meteors:
        if meteor_strategy is None and rng_state is not None:
            meteor_strategy = co_mod.meteor_strategy(rng_state)
        if meteor_strategy is None:
            meteor_strategy = 0
            note = ("Sturm's meteor strategy is one RNG draw mod 3; none was "
                    "given, so sim.apply took strategy 0 -- an assumption")
            if warnings is not None and note not in warnings:
                warnings.append(note)
        chosen = next((m for m in facts.meteors if m[0] == meteor_strategy), None)
        if chosen is None:
            raise ValueError(f"no meteor candidate for strategy {meteor_strategy}")
        for u, hp_after in chosen[2]:
            cur = swaps.get(u.slot) or unit_in(after, u.slot)
            if cur is None:
                continue
            if hp_after <= 0:
                dead.append(u.slot)
            else:
                swaps[u.slot] = dataclasses.replace(cur, hp=hp_after)
    if swaps:
        after = _replace(after, swaps)
    if dead:
        after = _remove(after, dead)
    if facts.weather == "snow":
        after = dataclasses.replace(after, weather_index=SNOW_INDEX)
    return after


# --------------------------------------------------------------------------
# the turn boundary
# --------------------------------------------------------------------------

def players_in_order(board) -> List[int]:
    """Who is in this match, in turn order (player number order)."""
    present = {a.player for a in board.armies} | {u.player for u in board.units}
    return sorted(p for p in present if p)


def end_turn(board, *, warnings: Optional[list] = None):
    """The active player ends its turn: the next army in order becomes
    active (the day advances when the order wraps) and its turn start is
    applied -- see turn_start()."""
    order = players_in_order(board)
    if not order:
        raise ValueError("no players on this board")
    cur = board.active_player
    later = [p for p in order if p > cur]
    nxt = later[0] if later else order[0]
    day = board.day + (0 if later else 1)
    after = dataclasses.replace(board, active_player=nxt, day=day, vision=None)
    return turn_start(after, nxt, warnings=warnings)


def turn_start(board, player: int, *, warnings: Optional[list] = None):
    """What the game does as `player`'s turn begins, in the measured order
    (DERIVATION 27, 33, 39): the power block and Olaf's snow expire; income;
    the burn walker, the property walker and auto-supply -- units in slot
    order, the treasury threaded through the repairs, crashed units removed;
    and the player's acted bits cleared.
    """
    warnings = warnings if warnings is not None else []
    after = dataclasses.replace(board, vision=None)
    army = _army(after, player)
    cid = army.co_id if army is not None else None
    # -- expiry: the block clears at the caster's next turn start, and the
    # -- snow at the same boundary (reverting to Clear: stated, not measured)
    if army is not None and army.power_active:
        after = _set_army(after, dataclasses.replace(army, power_active=False))
        if cid is not None and co_mod.POWER_EFFECTS.get(cid, {}).get("weather"):
            after = dataclasses.replace(after, weather_index=CLEAR_INDEX)
        army = _army(after, player)
    # -- income, before anything is repaired
    if army is not None:
        after = _add_funds(after, player, economy.income(after, player).amount)
        army = _army(after, player)
    funds = army.funds if army is not None else None
    power = bool(army.power_active) if army is not None else False
    free = getattr(after, "repair_free", None)
    if free is None:
        note = ("repair-free byte unknown on this board -- turn_start assumes "
                "repairs CHARGE funds")
        if note not in warnings:
            warnings.append(note)
    # -- the walkers, slot-ascending; adjacency is read off the board as it
    # -- stands at turn start (position writes are invisible to it)
    swaps, dead = {}, []
    for u in sorted((u for u in after.units if u.player == player),
                    key=lambda u: u.slot):
        terrain_id = after.terrain[u.y][u.x]
        owner = after.owner[u.y][u.x]
        adjacent = any(
            n is not None and n.player == player and n.slot != u.slot
            and supply_mod.is_supplier(n.type)
            for n in (after.unit_at(u.x + dx, u.y + dy)
                      for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                      if 0 <= u.x + dx < after.width and 0 <= u.y + dy < after.height))
        ts = supply_mod.turn_start(
            u.type, hp=u.hp, fuel=u.fuel, ammo=u.ammo, terrain_id=terrain_id,
            tile_owner=owner, player=player, funds=funds, charge=not free,
            co_id=cid, power=power, dived=u.dived, loaded=u.loaded,
            apc_adjacent=adjacent)
        if ts.crashes:
            dead.append(u.slot)
            continue
        if funds is not None:
            funds -= ts.repair_spent
        swaps[u.slot] = dataclasses.replace(u, hp=ts.hp_after,
                                            fuel=ts.fuel_after,
                                            ammo=ts.ammo_after, acted=False)
    after = _replace(after, swaps)
    if dead:
        after = _remove(after, dead)
    if army is not None and funds is not None and funds != army.funds:
        after = _set_army(after, dataclasses.replace(_army(after, player),
                                                     funds=funds))
    return after

"""CO power activation as an army action (DERIVATION 37).

The system itself is DERIVATION 27 (charge, threshold, effects, lifetime)
and lives in engine/co.py; this module turns it into facts a caller can
offer beside build_actions(): is the power available NOW, what it costs,
and what activating would do to THIS board.

Availability, measured at the map menu (tests/fixtures/power_menu.json):
the Power item appears when the METER is at or above the uses-scaled
threshold -- cost * (100 + 20*uses) / 100, capped at 200% -- and not
otherwise. The one-shot ready latch at army +0x24 plays no part in the
menu (latch set with an empty meter: no item; meter full with the latch
clear: item), so a dump without the latch loses nothing. The item also
shows while a power is already running if the meter is full, which normal
play cannot reach (activation zeroes the meter and charging pauses while
the block is up) -- so `available` is meter >= threshold and not active,
with the active case reported separately.

Effects replay what DERIVATION 27/30 measured, per CO, on the given board:

  * every CO: meter -> 0, uses+1, the power stat block up until the
    caster's next turn start (universal pair, per-unit pool, luck, vision)
  * Andy   heal: every own unit through the repair routine for 2 bars,
           FREE -- the same snap-to-bar-ceiling routine property repair uses
  * Olaf   weather -> snow
  * Drake  every enemy unit -10 internal, floored at 1
  * Eagle  every own non-foot unit that has acted gets its action back
  * Sturm  the meteor: three candidate centres, one per strategy the RNG
           draw selects (co.meteor_target), each with its victims
  * Sami   movement tables 3/4/5 (foot terrain costs all 1) -- data, stated
  * Grit   indirect max range +2 -- stated; the threat model does not yet
           apply it
  * Max, Nell, Kanbei, Sonja: stat block only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

try:
    from . import co as co_mod
    from . import pathing, supply
except ImportError:
    import co as co_mod
    import pathing
    import supply


@dataclass(frozen=True)
class Activation:
    player: int
    co_id: int
    co_name: str
    meter: int
    uses: int
    threshold: int
    available: bool          # meter >= threshold and no power running
    active: bool             # a power is running already
    next_threshold: int      # what the meter will need after this use
    universal: tuple         # (attack%, defence%) of the power block
    luck: tuple              # the power block's luck range
    effects: dict            # co.POWER_EFFECTS entry
    heals: List[tuple] = field(default_factory=list)     # (unit, hp_after)
    refreshes: List[object] = field(default_factory=list)
    damages: List[tuple] = field(default_factory=list)   # (unit, hp_after)
    meteors: List[tuple] = field(default_factory=list)   # (strategy, center, [(unit, hp_after)])
    weather: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def activation(board, player: int, co_id: Optional[int] = None,
               warnings: Optional[list] = None) -> Optional[Activation]:
    """The activation facts for `player`, or None when the CO is unknown."""
    warnings = warnings if warnings is not None else []
    try:
        army = board.army(player)
    except (StopIteration, AttributeError):
        return None
    co = co_id if co_id is not None else army.co_id
    if co is None:
        note = (f"P{player}'s CO is unknown -- no power facts; re-dump with "
                f"the current mgba_state.lua")
        if note not in warnings:
            warnings.append(note)
        return None
    uses = army.power_uses
    if uses is None:
        uses = 0
        note = (f"P{player}'s activation count is not in this dump -- the "
                f"threshold assumes no prior use (re-dump to fix)")
        if note not in warnings:
            warnings.append(note)
    threshold = co_mod.power_threshold(co, uses)
    meter = army.power
    active = bool(army.power_active)
    effects = co_mod.POWER_EFFECTS.get(co, {})
    rec = co_mod.record(co, power=True)
    a = Activation(
        player=player, co_id=co, co_name=rec.name, meter=meter, uses=uses,
        threshold=threshold, available=(meter >= threshold and not active),
        active=active, next_threshold=co_mod.power_threshold(co, uses + 1),
        universal=co_mod.universal(co, power=True),
        luck=co_mod.luck(co, power=True), effects=dict(effects))
    notes = a.notes
    if "heal_display" in effects:
        for u in board.units:
            if u.player == player:
                r = supply.repair(u.hp, u.type, charge=False,
                                  bars=effects["heal_display"])
                a.heals.append((u, r.hp_after))
    if effects.get("refresh") == "nonfoot_acted":
        for u in board.units:
            if (u.player == player and u.acted and not u.loaded
                    and pathing.unit_stats(u.type)["unit_class"] != "foot"):
                a.refreshes.append(u)
    if "mass_damage" in effects:
        for u in board.units:
            if u.player != player and not u.loaded:
                a.damages.append((u, max(1, u.hp - effects["mass_damage"])))
    if "meteor_internal" in effects:
        dmg = effects["meteor_internal"]
        for strategy in range(co_mod.METEOR_STRATEGIES):
            center = co_mod.meteor_target(board, player, strategy)
            victims = co_mod.meteor_victims(board, center, dmg) if center else []
            a.meteors.append((strategy, center, victims))
        notes.append("the strategy is one RNG draw mod 3 (rng.next_state "
                     "of 0x03001D30); without a state read all three "
                     "candidates are listed")
    if "weather" in effects:
        object.__setattr__(a, "weather", effects["weather"])
    if "move_tables" in effects:
        notes.append("Sami: foot units pay 1 on every passable terrain "
                     "(movement tables 3/4/5) for the power's lifetime")
    if "range_bonus" in effects:
        notes.append(f"Grit: indirect max range +{effects['range_bonus']} "
                     f"while the power runs -- not yet applied by threat.py")
    return a

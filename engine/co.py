"""CO modifiers: what the damage path reads, and what it demonstrably does not.

`engine/damage.py` has always taken `co_attack` and `co_defense` as parameters
and nothing filled them. This fills them -- but only from the source the
disassembly actually shows the damage path using, and it refuses to guess about
the rest.

WHAT IS ESTABLISHED

The damage path indexes a per-unit modifier and applies it as `(value * mod)
/ 100`, truncating, twice in sequence -- attack then defence. That was read off
the code (DERIVATION.md 7), not inferred, and the pointer array it indexes is
the one `tools/extract_co.py` decodes. So per-unit modifiers ARE damage inputs:

    Max      150/100 on direct units,  90/110 on the four indirects
    Sami     120/90 on Infantry and Mech
    Grit      80/100 on everything EXCEPT the indirects
    Eagle    115/90 on air, 80/100 on the surface navy
    Drake     80/100 on air

WHAT IS NOT, AND WHY THIS MODULE WILL NOT PRETEND OTHERWISE

Kanbei has **no per-unit modifiers at all** -- all 24 entries are 100/100 -- and
is plainly a stronger CO. His strength lives in the record header, at +08/+09
(120/120) and +11/+12 (120/80). Neither pair has been observed in the damage
path. Sturm is the same story: per-unit all 100/100, header +11/+12 reading
130/120 and 80/80 for his two records.

So for those COs the per-unit pool alone predicts *no bonus*, which is
certainly wrong. Rather than invent a combination rule, `modifiers()` returns
what the pool says and `unmodelled()` reports the header pairs that are not
being applied. `damage.resolve(co=...)` refuses non-neutral-header COs unless
the caller opts in, so a Kanbei prediction cannot be produced silently.

HOW NOT TO SETTLE IT. This file used to say: write army +0x1D to Kanbei and to
Andy on the same fixture, seed the RNG, and compare the damage -- identical
means the header pairs do not reach the damage path. **That test is broken and
would have produced exactly the wrong answer.**

Writing +0x1D alone does not change damage, not even for a CO whose effect is
not in doubt. Max is 150/100 on Tank and should move a Tank -> Infantry-in-
woods attack from 60-67 to 90-97; written mid-fixture he lands on 60-67, the
same as Andy.

The reason is a gate, not a cache. DERIVATION 24 traces the fetch: both the
attacker's and the defender's modifier lookups do read [army+0x1D] and index
the record at a 292-byte stride -- but each first tests [0x03004318], and when
that byte is clear they branch to a hardcoded `292 * 1`, record 1, ANDY, for
both sides. It reads 0 in every VS capture taken so far, so those matches
compute every unit as Andy whatever +0x1D says.

So the old test could only ever return "identical", and the conclusion drawn
from that would have been "Kanbei's header fields do not reach the damage
path". A test that cannot fail is worse than no test.

HOW TO SETTLE IT: write 0x03004318 = 1 alongside +0x1D, or build the fixture in
a match where CO abilities are already on. Either way, check it with Max first
-- his 90-97 is the control that says the CO reached the damage path at all.
"""
from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Optional

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"

NEUTRAL = (100, 100)

# The roll every CO gets before its record modifies it.
DEFAULT_LUCK_MAX = 9


def _co_data():
    return json.loads((DATA / "aw1_co.json").read_text(encoding="utf-8"))


class UnmodelledCO(RuntimeError):
    """Raised when a CO's strength lives somewhere the damage model cannot see."""


@dataclass(frozen=True)
class CoRecord:
    id: int
    name: str
    per_unit: dict          # unit name -> (attack, defence), from the pool
    # +08/+09: the unit-VALUE pair (meter charge / deploy cost), NOT damage.
    # +08 is what unit_value() applies; +09 has no observed reader.
    header_global: tuple
    header_pair: tuple      # +11/+12, the universal pair the damage path uses
    weather_tables: list
    # +06 widens the roll upward, +07 shifts it down. Zero on ten of the twelve
    # records; see `luck` for the rule and what it rests on.
    luck_good: int = 0
    luck_bad: int = 0

    @property
    def luck(self) -> tuple:
        """(min, max) of this CO's luck roll."""
        return (-self.luck_bad, DEFAULT_LUCK_MAX + self.luck_good - self.luck_bad)


def record(co_id: int, power: bool = False) -> CoRecord:
    d = _co_data()
    if not 0 <= co_id < len(d["records"]):
        raise KeyError(f"CO id {co_id} outside 0..{len(d['records']) - 1}")
    r = d["records"][co_id]
    block = r["power" if power else "normal"]
    h = block["header"]
    return CoRecord(
        id=co_id,
        name=d["confirmed"].get(str(co_id), f"co{co_id}"),
        per_unit={u: tuple(m) for u, m in block["modifiers"].items()},
        header_global=(h[8], h[9]),
        header_pair=(h[11], h[12]),
        weather_tables=block["weather_tables"],
        luck_good=h[6],
        luck_bad=h[7],
    )


def luck(co_id: int, power: bool = False) -> tuple:
    """(min, max) of this CO's luck roll, from the ROM record.

        luck = uniform(0, 9 + good) - bad      good = +06, bad = +07

    Ten of the twelve records carry 0/0 and roll the standard 0..9. The two
    that do not are the whole reason this function exists:

        Nell        good=10          ->    0..19
        Nell power  good=50          ->    0..59
        Sonja       good=15 bad=15   ->  -15..9

    One rule, no per-CO special cases. Sonja's SYMMETRIC pair is what forces
    it: read +06 alone as "wider roll" and she would swing 0..24, read +07
    alone as "worse roll" and she would sit at -15..-6. Only the pair together
    gives a range the same width as everyone else's, slid downward, which is
    what a luck penalty should be.

    ROM-derived and community-corroborated, but NOT confirmed against the game
    -- no sweep has yet produced a roll outside 0..9. `engine/damage.py`
    applies it anyway, because for Sonja the alternative is claiming kills are
    guaranteed when a -15 roll would leave the target standing. See
    ASSUMPTIONS A11 and `tools/luck_range_check.py`.
    """
    return record(co_id, power).luck


def universal(co_id: int, power: bool = False) -> tuple:
    """The CO's all-units pair, from header +11/+12, as PERCENTAGES TO APPLY.

    Both are used raw. `+11` multiplies the attacker's value, `+12` the
    defender's, each as `value * x / 100`, so 100 is neutral, a higher attack
    number hits harder and a LOWER defence number takes less. That inversion is
    not a mistake: the defence byte is stored already subtracted, which is why
    Kanbei reads 80 rather than 120.

        Andy        100 / 100      neutral, and so are nine others
        Andy power  110 /  90      the standard power bonus, both directions
        Kanbei      120 /  80
        Eagle power  80 /  70      Lightning Strike

    MEASURED, on one board, both directions:

        Kanbei attacking   value 75 -> 90    damage 72-79, predicted 72-79
        Kanbei defending   value 75 -> 60    damage 48-55, predicted 48-55

    +08/+09 is NOT this pair's innate form, as this docstring once guessed:
    +08 is the unit-VALUE multiplier the power charge path reads (Kanbei's
    120 makes his units worth more meter, same as their deploy cost), and
    +09 has no observed reader. +11/+12 alone is what the damage path wants.
    """
    d = _co_data()
    block = d["records"][co_id]["power" if power else "normal"]
    h = block["header"]
    return h[11], h[12]


def capture_shift(co_id: int, power: bool = False) -> int:
    """Header +0x0D: the capture-rate bonus, as a SHIFT. Read off the ROM at
    0x080261E4-0x080261FC:

        increment = bars + (bars >> (8 - byte))      bars = ceil(hp / 10)

    Zero on eleven of twelve records -- bars >> 8 is 0 for any bar count the
    game can produce -- and 7 on Sami's, both blocks, so her units gain
    bars + (bars >> 1): the documented 1.5x capture rate, truncated. The fetch
    is gated on [0x03004318] exactly like the damage modifiers (A12): with CO
    abilities off, everyone captures as Andy, at +0.
    """
    d = _co_data()
    return d["records"][co_id]["power" if power else "normal"]["header"][13]


def modifiers(co_id: int, unit_type: str, power: bool = False) -> tuple:
    """(attack, defence) for this CO's units of this type, from the pool.

    This is the pair the damage path multiplies by. It is NOT the whole of a
    CO's strength -- see `unmodelled`.
    """
    r = record(co_id, power)
    if unit_type not in r.per_unit:
        raise KeyError(f"{unit_type!r} has no modifier entry for {r.name}")
    return r.per_unit[unit_type]


def unmodelled(co_id: int, power: bool = False) -> dict:
    """The parts of this CO's record that the damage model does not apply.

    Empty for every CO now. `+11/+12` used to be listed here and is the reason
    Kanbei and Sturm were refused; it is `universal()`, measured in both
    directions, and applied. `+08/+09` turned out not to be damage at all --
    +08 is the meter-value multiplier (DERIVATION 27), so nothing is missing.

    Kept, rather than deleted, because it is the hook the refusal hangs on. If
    a later record turns out to carry strength somewhere else, this is where it
    gets reported and `Attack.between` starts declining again.
    """
    return {}


# ---------------------------------------------------------------------------
# The CO power system, DERIVATION 27. Everything below is either read off the
# ROM at a named address or measured live through the headless rig; nothing is
# community lore.

# Charging is gated on [0x03004317] (a second settings byte, distinct from the
# CO-modifier gate at +0x08): with the VS "CO Power" rule off nothing charges,
# which is why every earlier capture read meter 0.
CHARGE_GATE = 0x03004317


def power_cost(co_id: int) -> int:
    """The meter's base activation cost, u32 at record TRUE base +0x08.

    Measured by activation: writing exactly this much charge makes the Power
    menu item appear, and activation resets the meter to 0. Sami 25000,
    Drake 40000, Kanbei/Eagle/both Sturms 50000, everyone else 30000 --
    the community's star counts times 10000.
    """
    return _co_data()["records"][co_id]["power_meta"]["cost"]


def power_threshold(co_id: int, uses: int = 0) -> int:
    """What a full meter costs after `uses` activations.

    Read off 0x0801C018: percent = 200 if uses > 9 else 100 + 20*uses, then
    cost * percent / 100, truncating. The use count is army +0x25, incremented
    on every activation (0x0801C104) and saturating at 255.
    """
    percent = 200 if uses > 9 else 100 + 20 * uses
    return power_cost(co_id) * percent // 100


def unit_value(unit_cost: int, co_id: int, power: bool = False) -> int:
    """A unit's meter-charge value: (cost/10) * header[+08] / 100.

    Read off 0x0802D344. Header +08 is a VALUE multiplier, not attack --
    Kanbei's 120 makes his units worth 20% more charge, the same 20% his
    deployments cost. The pool's per-unit s16 adjustment at entry +0 is zero
    on all 18 referenced entries, so it is folded out here.
    """
    d = _co_data()
    h = d["records"][co_id]["power" if power else "normal"]["header"]
    return (unit_cost // 10) * h[8] // 100


def charge_gains(att_value: int, def_value: int,
                 att_display_lost: int, def_display_lost: int) -> tuple:
    """(attacker_gain, defender_gain) for one battle, in meter units.

    Read off 0x0802D2A0-0x0802D5A6 and confirmed live twice:

        own  = unit_value * display_HP_lost      (dead: full display HP)
        gain = own + other_side_own / 4          (truncating)

    Tank->Infantry, 8 display dealt, 0 taken: predicted (200, 800), watched
    the writes land exactly that at 0x0801BFC4. The old A-Air/Tank capture
    (+3725/+2900) solves to 3 display dealt, 4 taken -- consistent.

    The add is skipped entirely while that army's power is ACTIVE (+0x1E
    nonzero, tested at 0x0801BF7C), and the result clamps at the threshold.
    """
    att_own = att_value * att_display_lost
    def_own = def_value * def_display_lost
    return (att_own + def_own // 4, def_own + att_own // 4)


def power_meta(co_id: int) -> dict:
    """The extracted power descriptor: cost, walker eligibility, effect."""
    return dict(_co_data()["records"][co_id]["power_meta"])


# One-shot effects on activation, MEASURED per record through the headless
# activation probe (harness/mesen_power_activate.lua, _fx*.lua). Activation
# itself always does: uses+=1 (army +0x25), ready flag cleared (+0x24), meter
# reset to 0, statblock +0x1E = 1. The block reverts to 0 at the start of the
# caster's NEXT turn (0x0801BFFC), so the power's stat block -- including the
# universal 110/90 -- covers the opponent's whole turn.
POWER_EFFECTS = {
    0: {},                                  # Nell: luck 0..59, stat block only
    1: {"heal_display": 2},                 # Andy: +2 HP, free, via repair path
    2: {},                                  # Max: pool 170/100 on directs
    3: {"weather": "snow"},                 # Olaf: snow until his next turn
    4: {"move_tables": [3, 4, 5]},          # Sami: foot terrain costs all 1
    5: {"range_bonus": 2},                  # Grit: indirect max range +2 (2..5)
    6: {},                                  # Kanbei: universal 140/70
    7: {},                                  # Sonja: luck stays -15..9; rest unread
    8: {"refresh": "nonfoot_acted"},        # Eagle: acted bit cleared, non-foot
    9: {"mass_damage": 10},                 # Drake: -10 internal all enemies, min 1
    10: {"meteor_internal": 80},            # Sturm: r<=2 cluster, code 0x0801CC88
    11: {"meteor_internal": 40},            # Sturm (VS record): same, weaker
}


def describe(co_id: int, unit_type: Optional[str] = None,
             power: bool = False) -> str:
    r = record(co_id, power)
    bits = [f"{r.name} (id {co_id}{', power' if power else ''})"]
    if unit_type:
        a, d = modifiers(co_id, unit_type, power)
        bits.append(f"{unit_type}: attack {a}%, defence {d}%")
    groups = {}
    for u, m in sorted(r.per_unit.items()):
        groups.setdefault(m, []).append(u)
    for m, us in sorted(groups.items()):
        if m != NEUTRAL:
            bits.append(f"  {m[0]}/{m[1]}: {', '.join(us)}")
    gaps = unmodelled(co_id, power)
    if gaps:
        bits.append("  NOT MODELLED: " + ", ".join(f"{k}={v[0]}/{v[1]}"
                                                   for k, v in gaps.items()))
    return "\n".join(bits)

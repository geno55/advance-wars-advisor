"""Advance Wars 1 (GBA) damage model.

Two very different confidence levels live in this file, and conflating them is
how you end up with a tool that gives confidently wrong advice:

  * The base damage TABLES are extracted byte-for-byte from the ROM
    (data/aw1_damage.json). Trustworthy.
  * The FORMULA wrapped around them is not yet verified. Several plausible
    variants agree on most inputs and disagree only on where they truncate.
    Rather than pick one and hope, all of them live here as named variants;
    tests/calibrate.py eliminates the wrong ones using observations from a
    real emulator run.

Nothing here picks a variant for you. `DEFAULT_VARIANT` is a *hypothesis*
until calibration says otherwise, and `damage_range()` refuses to pretend
otherwise unless you pass verified=True.

HP convention: internal HP is 1..100. Displayed HP is 1..10. Damage is applied
to internal HP, but attack strength scales with *displayed* HP. Getting that
backwards is the single most common bug in third-party AW calculators.
"""
from __future__ import annotations

import functools
import json
import math
import pathlib
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "aw1_damage.json"
STATS = pathlib.Path(__file__).resolve().parent.parent / "data" / "aw1_unit_stats.json"

# The CO modifier a record carries when it does nothing. Both halves of a
# neutral matchup read this, which is what makes the neutral case answerable
# without knowing which CO it was.
NEUTRAL_CO = 100

LUCK_MIN, LUCK_MAX = 0, 9          # the DEFAULT roll; per-CO ranges below

# Per-CO luck comes out of the CO record header, not out of a table of names.
#
#     luck = uniform(0, 9 + good) - bad     good = header +06, bad = header +07
#
# Across all twelve records only two are nonzero: Nell carries good=10 normally
# and 50 under her power, Sonja carries good=15 AND bad=15, equal. That
# symmetry is the tell -- it is what makes one rule fit both COs instead of
# needing a special case each:
#
#     standard    uniform(0, 9)  - 0   ->    0..9
#     Nell        uniform(0,19)  - 0   ->    0..19
#     Nell power  uniform(0,59)  - 0   ->    0..59
#     Sonja       uniform(0,24)  - 15  ->  -15..9
#
# ROM-derived, and independently corroborated: those are exactly the ranges the
# community documents. Two sources agreeing on a rule neither states outright
# is worth a lot -- but it is still an INTERPRETATION of two bytes, and no
# emulator sweep has yet produced a roll outside 0..9. See ASSUMPTIONS A11.
#
# Sonja matters for safety in a way Nell does not. A negative roll lowers the
# MINIMUM, so treating her as a standard CO overstates min_damage and reports
# kills as guaranteed that are not. Nell's error runs the safe way: it
# understates the maximum.


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------

def load_tables(path: pathlib.Path = DATA) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


_TABLES: Optional[dict] = None


def tables() -> dict:
    global _TABLES
    if _TABLES is None:
        _TABLES = load_tables()
    return _TABLES


# How internal HP (1..100) maps to the value the damage formula scales by.
#
# This was assumed to be ceil() and that was WRONG -- refuted by emulator data.
# A Mech at 57 internal HP displays 6 bars but attacks as 5, so the bar count on
# screen is not what drives damage. No candidate rule is hard-coded now; the
# calibration decides, exactly like the formula variants.
#
# floor vs floor_min1 differ only below 10 internal HP: plain floor says a unit
# on its last bar attacks at strength 0 and deals nothing. Tested and settled --
# see DEFAULT_DISPLAY below.
DISPLAY_VARIANTS = {
    "floor":      lambda h: h // 10 if h > 0 else 0,
    "floor_min1": lambda h: max(1, h // 10) if h > 0 else 0,
    "ceil":       lambda h: -(-h // 10) if h > 0 else 0,
    "round":      lambda h: (h + 5) // 10 if h > 0 else 0,
}

# MEASURED: it is `ceil`, on both operands. See ASSUMPTIONS A9a.
#
# This was `floor_min1` for months on the strength of two observations that
# turned out to be counterattacks, which the game computes with an entirely
# different formula (A9b) -- so they never constrained this at all. Fitting them
# with the strike formula produced a rule that agreed with every other row in
# the corpus, because every other row had the attacker at 100 or 9 internal HP,
# the values where all four candidate rules return the same number.
#
# Settled by writing HP directly and sweeping 64 luck seeds per board:
#   attacker at 57  -> damage 27..32   (floor_min1 predicts 22..27)
#   defender at 81  -> damage 48..53   (floor_min1 predicts 51..57; and 81 is
#                                       the board that also excludes `round`)
#   defender at 85  -> damage 48..53
# The observed multiplicities match ceil's 2:2:1:2:1:2 collapse of ten rolls,
# not merely its endpoints. The ROM says the same thing at 0x080232C8:
# `(hp-1)/10 + 1`, which is ceil for every hp in 1..100.
DEFAULT_DISPLAY = "ceil"


def display_hp(internal: int, rule: Optional[str] = None) -> int:
    """Internal HP -> the value the damage formula scales by. Zero stays zero."""
    return DISPLAY_VARIANTS[rule or DEFAULT_DISPLAY](internal)


def screen_bars(internal: int) -> int:
    """What the PLAYER sees, which is a different question. The HP shown on
    screen rounds up; the combat maths does not. Keeping these separate is the
    whole point -- conflating them is what produced a refuted model."""
    if internal <= 0:
        return 0
    return -(-internal // 10)


# --------------------------------------------------------------------------
# weapon selection
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Weapon:
    slot: str          # "primary" | "secondary"
    base: int          # base damage, 0 means cannot target


def select_weapon(attacker: str, defender: str, ammo: int = 99,
                  tbl: Optional[dict] = None) -> Optional[Weapon]:
    """Pick the weapon the game would use, or None if the attack is illegal.

    Rule: use whichever available weapon deals more base damage. This is what
    reproduces the known behaviour that a Md Tank hits Infantry for 105 (its
    machine gun) rather than 50 (its cannon), while still using the cannon
    against armour. Marked as an assumption in docs/ASSUMPTIONS.md.
    """
    t = tbl or tables()
    prim = t["primary"].get(attacker, {}).get(defender, 0) if ammo > 0 else 0
    sec = t["secondary"].get(attacker, {}).get(defender, 0)
    if prim == 0 and sec == 0:
        return None
    return Weapon("primary", prim) if prim >= sec else Weapon("secondary", sec)


def can_attack(attacker: str, defender: str, ammo: int = 99) -> bool:
    return select_weapon(attacker, defender, ammo) is not None


# --------------------------------------------------------------------------
# formula variants
#
# Shared shape:
#     atk_term = base * co_attack/100  (+ luck, placement varies)
#     hp_term  = displayed attacker HP / 10
#     def_term = (200 - (co_defense + terrain_stars * displayed defender HP))/100
# The variants differ only in truncation points and where luck enters.
# --------------------------------------------------------------------------

def _terms(base, hp_a, co_atk, co_def, stars, hp_d):
    # The CO attack modifier TRUNCATES here, before anything else touches the
    # value. `muls` then `__divsi3` in the damage path (DERIVATION 7), and
    # measured: Max on Tank -> Infantry in woods lands 89..96, not the 90..97
    # an exact 75 * 150/100 = 112.5 gives. floor(112.5) = 112 reproduces both
    # the range and the histogram's shape -- 92 and 96 doubled, which is what
    # the sweep saw at 11 and 14 against 5-8 elsewhere.
    #
    # This was a Fraction for the whole life of the project and nothing caught
    # it, because 100/100 divides exactly and every one of the 75 corpus rows
    # and every earlier sweep was neutral. DERIVATION 7 said as much at the
    # time -- "with a neutral CO the truncation is a no-op" -- and it took the
    # first non-neutral measurement to make the difference visible.
    #
    # The DEFENCE modifier is the second of the two divides the disassembly
    # does in sequence -- on the VALUE, before luck, not inside the terrain
    # bracket where this used to put it.
    #
    # Measured with Kanbei defending, whose +12 reads 80: value 75 -> 60, and
    # damage 48..55. The old additive form predicted 45..50 and a product of
    # separate factors outside the luck term predicted 48..53. Neither. What
    # fits is the multiply landing on the value alongside the attacker's, so
    # luck is added AFTER it and then scaled by terrain alone.
    #
    # `co_def` is therefore a percentage to apply, not a defence stat: 100 is
    # neutral and LOWER takes less damage, because the byte is stored already
    # subtracted. At 100 this is identical to the old bracket, which is why 75
    # corpus rows and twelve sweeps could not tell the two apart.
    atk = base * co_atk // 100
    atk = atk * co_def // 100
    hp = Fraction(hp_a, 10)
    dfn = Fraction(100 - stars * hp_d, 100)
    return atk, hp, dfn


def v_floor_end(base, hp_a, co_atk, co_def, stars, hp_d, luck):
    atk, hp, dfn = _terms(base, hp_a, co_atk, co_def, stars, hp_d)
    return math.floor((atk + luck) * hp * dfn)


def v_floor_attack_then_end(base, hp_a, co_atk, co_def, stars, hp_d, luck):
    atk, hp, dfn = _terms(base, hp_a, co_atk, co_def, stars, hp_d)
    return math.floor((math.floor(atk) + luck) * hp * dfn)


def v_floor_each_step(base, hp_a, co_atk, co_def, stars, hp_d, luck):
    atk, hp, dfn = _terms(base, hp_a, co_atk, co_def, stars, hp_d)
    x = math.floor(atk) + luck
    x = math.floor(x * hp)
    return math.floor(x * dfn)


def v_round_end(base, hp_a, co_atk, co_def, stars, hp_d, luck):
    atk, hp, dfn = _terms(base, hp_a, co_atk, co_def, stars, hp_d)
    return math.floor((atk + luck) * hp * dfn + Fraction(1, 2))


def v_luck_after_hp(base, hp_a, co_atk, co_def, stars, hp_d, luck):
    atk, hp, dfn = _terms(base, hp_a, co_atk, co_def, stars, hp_d)
    return math.floor((atk * hp + luck) * dfn)


def v_luck_last(base, hp_a, co_atk, co_def, stars, hp_d, luck):
    atk, hp, dfn = _terms(base, hp_a, co_atk, co_def, stars, hp_d)
    return math.floor(atk * hp * dfn) + luck


VARIANTS = {
    "floor_end": v_floor_end,
    "floor_attack_then_end": v_floor_attack_then_end,
    "floor_each_step": v_floor_each_step,
    "round_end": v_round_end,
    "luck_after_hp": v_luck_after_hp,
    "luck_last": v_luck_last,
}

# Narrowed by emulator observations, not chosen.
#
# REFUTED: floor_end, floor_attack_then_end, floor_each_step (an Infantry at 9
# internal HP dealt 11 damage on road, which all three cap below), and round_end.
#
# SURVIVING: luck_after_hp and luck_last. They differ only in whether the luck
# roll is scaled by the terrain multiplier, so they agree exactly whenever
# defence is 0 stars, and their MINIMUM damage is always identical -- which is
# why guaranteed-kill advice is already safe. Evidence favours luck_after_hp at
# roughly 357:1, but that rests on the roll being uniform over 0..9, which is
# itself unverified and currently looks doubtful.
DEFAULT_VARIANT = "luck_after_hp"

# Calibration ended with two variants standing. Rather than pick one and pretend,
# the engine evaluates BOTH and reports the envelope:
#   * they agree exactly on minimum damage, so guaranteed_kill is exact
#   * they differ on maximum damage by up to 4 on 4-star terrain, so
#     possible_kill takes the wider view and never says "cannot kill" when one
#     of them says it might
# Erring outward on the uncertain end and exactly on the certain end is the only
# safe asymmetry for a tool that tells you whether something dies.
#
# NOW A SET OF ONE. `luck_last` was refuted by a seeded sweep: the combat luck
# state (a u32 at 0x03001D30, found by bisection -- see DERIVATION.md 16) was
# written to 128 values spread across the 32-bit space, and the resulting damage
# histogram matched luck_after_hp's shape at chi2/df 0.74 while luck_last scored
# 12.29 and predicted 51..54, which never appeared in 128 attacks.
#
# That is a POSITIVE identification, not the argument from absence that had to
# be withdrawn before (see ASSUMPTIONS A4). The variants predict different
# histogram SHAPES -- luck_after_hp collapses ten rolls onto six damages so some
# occur twice as often, luck_last maps ten onto ten and is flat -- and the
# uniformity it rests on is a property of the seeds we chose, not an assumption
# about how the game samples.
#
# Kept as a tuple, and resolve() still takes the envelope over it, so that
# reopening the question means adding a name here rather than restructuring.
SURVIVING_VARIANTS = ("luck_after_hp",)


class Unverified(RuntimeError):
    pass


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Attack:
    attacker: str
    defender: str
    attacker_hp: int = 100        # internal 1..100
    defender_hp: int = 100        # internal 1..100
    terrain_stars: int = 0
    ammo: int = 99
    co_attack: int = 100
    co_defense: int = 100
    # The ATTACKING CO's roll. Defaults to the standard 0..9 so every existing
    # caller is unchanged; `Attack.between()` fills it from the CO record.
    luck_min: int = LUCK_MIN
    luck_max: int = LUCK_MAX

    @property
    def luck_range(self) -> range:
        return range(self.luck_min, self.luck_max + 1)

    @classmethod
    def between(cls, attacker: str, defender: str, attacker_co: int,
                defender_co: int, attacker_power: bool = False,
                defender_power: bool = False, **kw) -> "Attack":
        """Build an Attack with both COs' modifiers filled from their records.

        `co_attack` comes from the ATTACKER's record indexed by the attacking
        unit type; `co_defense` from the DEFENDER's record indexed by the
        DEFENDING unit type. Getting those two crossed is the easy mistake, so
        this exists rather than leaving callers to wire it by hand.

        Raises UnmodelledCO if either CO's strength lives in header fields the
        damage path has not been shown to read -- Kanbei and Sturm. Pass
        co_attack/co_defense directly to override.
        """
        # This module is imported two ways: as `engine.damage` by the tests and
        # as top-level `damage` by the tools, which put engine/ on sys.path.
        try:
            from . import co as _co
        except ImportError:
            import co as _co
        modifiers, unmodelled = _co.modifiers, _co.unmodelled
        UnmodelledCO, record = _co.UnmodelledCO, _co.record
        for cid, power, role in ((attacker_co, attacker_power, "attacker"),
                                 (defender_co, defender_power, "defender")):
            gaps = unmodelled(cid, power)
            if gaps:
                r = record(cid, power)
                raise UnmodelledCO(
                    f"{role} CO {r.name} carries {gaps}, which the damage path "
                    "has not been shown to read -- a prediction would omit it. "
                    "See engine/co.py for how to settle this, or pass "
                    "co_attack/co_defense explicitly to override.")
        # Luck is the ATTACKER's roll only. Taking it from the defender would
        # hand Sonja's penalty to whoever shoots at her.
        lo, hi = _co.luck(attacker_co, attacker_power)
        kw.setdefault("luck_min", lo)
        kw.setdefault("luck_max", hi)
        # Each side is its per-unit modifier folded with its all-units pair.
        # Kanbei is the reason: his per-unit entries are all 100/100 and his
        # strength is entirely in +11/+12, so a quote built from the pool alone
        # was neutral and this class used to refuse rather than say so.
        #
        # Measured for the universal half in both directions. The PER-UNIT half
        # being a multiplier in the same convention -- so Max's 110 on indirect
        # defence means taking 10% MORE, not less -- follows the same table and
        # the same application point, but no sweep has yet used a CO with a
        # non-100 per-unit DEFENCE value. See A14.
        a_uni, d_uni = _co.universal(attacker_co, attacker_power)[0], \
            _co.universal(defender_co, defender_power)[1]
        return cls(attacker=attacker, defender=defender,
                   co_attack=modifiers(attacker_co, attacker,
                                       attacker_power)[0] * a_uni // 100,
                   co_defense=modifiers(defender_co, defender,
                                        defender_power)[1] * d_uni // 100,
                   **kw)


@dataclass(frozen=True)
class Outcome:
    min_damage: int
    max_damage: int
    min_remaining_hp: int
    max_remaining_hp: int
    weapon: str
    base: int
    guaranteed_kill: bool
    possible_kill: bool
    variant: str
    # False when the two surviving formula variants disagree about the top of
    # the damage range here, so max_damage is an envelope rather than a figure.
    variants_agree: bool = True


def damage_for_luck(a: Attack, luck: int, variant: str = DEFAULT_VARIANT,
                    display: Optional[str] = None) -> Optional[int]:
    w = select_weapon(a.attacker, a.defender, a.ammo)
    if w is None:
        return None
    fn = VARIANTS[variant]
    raw = fn(w.base, display_hp(a.attacker_hp, display), a.co_attack,
             a.co_defense, a.terrain_stars, display_hp(a.defender_hp, display),
             luck)
    return max(0, min(raw, a.defender_hp))


def resolve(a: Attack, variant: str = DEFAULT_VARIANT,
            verified: bool = False) -> Optional[Outcome]:
    """Full outcome across the luck range. Returns None if the attack is illegal.

    `verified=False` is fine for exploration, but callers that turn this into
    advice for a human should pass verified=True and be forced to confront the
    fact that the formula has not been checked against the game yet.
    """
    if not verified and not tables()["provenance"].get("verified_against_emulator"):
        raise Unverified(
            "The damage FORMULA has not been calibrated against the emulator yet. "
            "Run tests/calibrate.py with real observations, or pass verified=True "
            "to acknowledge you are using an unvalidated model."
        )
    w = select_weapon(a.attacker, a.defender, a.ammo)
    if w is None:
        return None

    # Evaluate every surviving variant and take the envelope. `variant` still
    # forces a single one, for calibration work that needs to isolate them.
    variants = (variant,) if variant != DEFAULT_VARIANT else SURVIVING_VARIANTS
    los, his = [], []
    for v in variants:
        vals = [damage_for_luck(a, lk, v) for lk in (a.luck_min, a.luck_max)]
        los.append(min(vals))
        his.append(max(vals))
    lo, hi = min(los), max(his)
    return Outcome(
        min_damage=lo, max_damage=hi,
        min_remaining_hp=max(0, a.defender_hp - hi),
        max_remaining_hp=max(0, a.defender_hp - lo),
        weapon=w.slot, base=w.base,
        guaranteed_kill=lo >= a.defender_hp,
        possible_kill=hi >= a.defender_hp,
        variant="+".join(variants),
        variants_agree=len(set(his)) == 1,
    )


# --------------------------------------------------------------------------
# counterattack
# --------------------------------------------------------------------------

class CounterModifiersUnknown(RuntimeError):
    pass


@functools.lru_cache(maxsize=None)
def _unit_stats() -> dict:
    """Range data, read once. Loaded here rather than imported from pathing:
    pathing depends on nothing in this module and the damage model is the
    bottom of the stack, so inverting that to borrow one dict would be the
    wrong trade for four lines."""
    return json.loads(STATS.read_text(encoding="utf-8"))["units"]


def fights_at_contact(unit_type: str) -> bool:
    """Whether this unit's range ring includes 1 -- can it fight at touching
    distance, in either direction.

    One predicate read off min_range/max_range, applied to both roles, and no
    unit is named. An Artillery (2..3) is neither countered nor countering: it
    never stood adjacent, and it cannot fire at its own feet. Unarmed units read
    0..0 and fall out for free, without being enumerated.
    """
    st = _unit_stats().get(unit_type)
    if st is None:
        raise KeyError(f"no stats for unit type {unit_type!r}; the reader saw a "
                       "type the ROM table does not describe")
    return st["min_range"] <= 1 <= st["max_range"]


def counter_damage(base: int, survivor_hp: int, co_defense: int,
                   target_stars: int, target_hp: int, *,
                   co_attack: int = 100) -> int:
    """The counterattack's own formula. MEASURED -- see ASSUMPTIONS A9b.

    `co_attack` is keyword-only, and late, because it arrived after the rest:
    every call written before it was measured is neutral and stays correct
    where it stands, with no chance of a positional argument sliding into the
    wrong slot.

    A counter is not a second strike. The game overwrites the scaled value the
    strike path computed (`0x080234DA-0x080234F6`) with the survivor's **raw
    internal HP over 100** -- no display quantisation and no luck roll -- before
    applying the defence multiplier.

    Three independent lines pin this, and each kills something the others do
    not. A 64-seed sweep on a fixed board: the opening damage ranged 45..50 and
    the counter read 2 every single time, which no luck-carrying formula can do,
    and which also refutes a `ceil` or `round` display rule (survivors 51..55
    would have countered for 3). The recorded Mech at 57 HP: raw internal gives
    (55*57/100)*90/100 = 27 against the observed 27, where a `floor` display
    rule gives 24. And the ROM instruction sequence says raw internal directly.

    BOTH CO modifiers land on the BASE, before the survivor's HP, in the same
    order and with the same two truncations the strike path uses -- MEASURED,
    four 64-seed sweeps on an Infantry-v-Tank board, 256 cases, see A9b.

    So the counter is not a different formula for the CO pair. It is `_terms()`
    with the strike's display-HP term swapped for the survivor's raw internal HP
    and the luck roll dropped, which is the whole of the difference.

    The defence modifier moving here from the value to the base is the part that
    took a measurement rather than an argument. A14 located it "on the value"
    for the strike, and the strike's value IS the base -- its HP term is 1 at
    full health, so the two orders are indistinguishable there. A counter's HP
    term is the survivor's raw HP and never 1, which separates them: with Sami
    countered at 90 defence, base-first reproduces all 64 cases and value-first
    reads one point high on three survivors in nine.
    """
    atk = (base * co_attack // 100) * co_defense // 100
    v = atk * survivor_hp // 100
    return max(0, min(v * (100 - target_stars * display_hp(target_hp)) // 100,
                      target_hp))


def counterattack(a: Attack, variant: str = DEFAULT_VARIANT,
                  verified: bool = False, *,
                  attacker_stars: int = 0,
                  defender_ammo: int = 99,
                  attacker_co: Optional[int] = None,
                  defender_co: Optional[int] = None,
                  attacker_power: bool = False,
                  defender_power: bool = False) -> Optional[Outcome]:
    """The defender's return strike. None when there is no counter to model.

    Three distinct reasons for None, all of them real:
      * the opening attack is illegal, so nothing happened at all;
      * the battle was not fought at contact -- an Artillery shelling from two
        tiles is neither countered nor countering (see `fights_at_contact`);
      * the kill is GUARANTEED, so no survivor exists on any luck roll.

    A merely POSSIBLE kill still returns a counter. The attacker has to live
    with the roll where the defender survives, and saying "no counter" there is
    how the same output claimed a kill was not guaranteed and that nothing
    would come back, on adjacent lines.

    The survivor is the STRONGEST one -- `max_remaining_hp`, the defender that
    took the smallest damage roll. That is the pessimistic direction for the
    attacker, which is the one that matters here: this number answers "what is
    the worst that comes back at me", and understating it is how a tool talks a
    player into a bad trade. It used to read `min_remaining_hp` under a comment
    claiming that was pessimistic; it is the opposite, because the weakest
    survivor hits weakest.

    `attacker_stars` is the cover on the ORIGINAL attacker's tile, which is
    where the return strike lands. It defaults to 0, the same open-ground
    default the rest of the module uses.

    CO modifiers cannot be swapped out of `a`. `a.co_attack` is the attacking
    CO's ATTACK modifier indexed by the attacking unit; the return strike needs
    the defending CO's ATTACK modifier indexed by the DEFENDING unit, which is a
    different field of a different record -- Max reads 150/100, so reusing his
    defence figure quotes his counter 50 points light. Pass the two CO ids and
    both are looked up properly, with roles and power flags swapped here rather
    than at every call site. Omit them only for a neutral opening attack, where
    the question does not arise; anything else raises rather than reusing a
    number that cannot be right.
    """
    first = resolve(a, variant, verified)
    if first is None:
        return None
    if not (fights_at_contact(a.attacker) and fights_at_contact(a.defender)):
        return None
    w = select_weapon(a.defender, a.attacker, defender_ammo)
    if w is None:
        return None

    if (attacker_co is None) != (defender_co is None):
        raise ValueError("pass both attacker_co and defender_co, or neither -- "
                         "one alone cannot fill in the return strike")
    if attacker_co is not None:
        back = Attack.between(a.defender, a.attacker, defender_co, attacker_co,
                              attacker_power=defender_power,
                              defender_power=attacker_power)
        co_a, co_d = back.co_attack, back.co_defense
    else:
        # Only `a` to go on, and its two fields are the OPENING's: the
        # defending CO's DEFENCE modifier where the counter needs its ATTACK
        # modifier, indexed by a different unit. Neutral is the one case where
        # that does not matter, because everything is 100 either way.
        co_a, co_d = a.co_defense, a.co_attack
        if (co_a, co_d) != (NEUTRAL_CO, NEUTRAL_CO):
            raise CounterModifiersUnknown(
                f"this counter carries CO modifiers ({co_a}/{co_d}) and cannot "
                "be derived from the opening attack alone: `a.co_attack` and "
                "`a.co_defense` are the attacking CO's attack and the "
                "defending CO's defence, and the return strike needs the other "
                "two fields, each indexed by the other unit. Pass attacker_co "
                "and defender_co and both are looked up properly. Where the "
                "modifiers enter is no longer the problem -- that is measured, "
                "see ASSUMPTIONS A9b.")

    # The counter carries no luck of its own, but the SURVIVOR does: which
    # defender is left depends on the opening roll. So walk the opening's luck
    # range and map each surviving defender through the counter formula. This is
    # a real envelope over one variable, not a range over a variable the game
    # does not have -- and it is why the old lower bound was too high.
    dmgs = []
    for lk in range(LUCK_MIN, LUCK_MAX + 1):
        d = damage_for_luck(a, lk, variant)
        if d is None:
            continue
        survivor = a.defender_hp - d
        if survivor <= 0:
            continue                      # dead on this roll: no counter at all
        dmgs.append(counter_damage(w.base, survivor, co_d,
                                   attacker_stars, a.attacker_hp,
                                   co_attack=co_a))
    if not dmgs:
        return None                       # guaranteed kill on every roll

    lo, hi = min(dmgs), max(dmgs)
    return Outcome(
        min_damage=lo, max_damage=hi,
        min_remaining_hp=max(0, a.attacker_hp - hi),
        max_remaining_hp=max(0, a.attacker_hp - lo),
        weapon=w.slot, base=w.base,
        guaranteed_kill=lo >= a.attacker_hp,
        possible_kill=hi >= a.attacker_hp,
        variant="rom_counter",
        variants_agree=True,
    )

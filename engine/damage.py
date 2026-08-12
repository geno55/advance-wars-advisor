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

import json
import math
import pathlib
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "aw1_damage.json"

LUCK_MIN, LUCK_MAX = 0, 9          # standard CO luck roll; Nell/Flak differ


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


def display_hp(internal: int) -> int:
    """Internal HP (1..100) -> displayed HP (1..10). Zero stays zero."""
    if internal <= 0:
        return 0
    return -(-internal // 10)          # ceil division


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
    atk = Fraction(base * co_atk, 100)
    hp = Fraction(hp_a, 10)
    dfn = Fraction(200 - (co_def + stars * hp_d), 100)
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

# Hypothesis only. calibrate.py is what makes this a fact.
DEFAULT_VARIANT = "floor_end"


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


def damage_for_luck(a: Attack, luck: int, variant: str = DEFAULT_VARIANT) -> Optional[int]:
    w = select_weapon(a.attacker, a.defender, a.ammo)
    if w is None:
        return None
    fn = VARIANTS[variant]
    raw = fn(w.base, display_hp(a.attacker_hp), a.co_attack,
             a.co_defense, a.terrain_stars, display_hp(a.defender_hp), luck)
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
    lo = damage_for_luck(a, LUCK_MIN, variant)
    hi = damage_for_luck(a, LUCK_MAX, variant)
    lo, hi = min(lo, hi), max(lo, hi)
    return Outcome(
        min_damage=lo, max_damage=hi,
        min_remaining_hp=max(0, a.defender_hp - hi),
        max_remaining_hp=max(0, a.defender_hp - lo),
        weapon=w.slot, base=w.base,
        guaranteed_kill=lo >= a.defender_hp,
        possible_kill=hi >= a.defender_hp,
        variant=variant,
    )


def counterattack(a: Attack, variant: str = DEFAULT_VARIANT,
                  verified: bool = False) -> Optional[Outcome]:
    """The defender's return strike, at whatever HP it has left after taking the
    worst case. Counters use the survivor's reduced HP -- that is the whole
    reason alpha-striking works, so it must not be modelled as full strength."""
    first = resolve(a, variant, verified)
    if first is None:
        return None
    survivor_hp = first.min_remaining_hp        # pessimistic for the attacker
    if survivor_hp <= 0:
        return None
    back = Attack(
        attacker=a.defender, defender=a.attacker,
        attacker_hp=survivor_hp, defender_hp=a.attacker_hp,
        terrain_stars=0,      # caller supplies the attacker's tile
        co_attack=a.co_defense, co_defense=a.co_attack,
    )
    return resolve(back, variant, verified)

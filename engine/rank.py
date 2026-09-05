"""The debrief's rank, as the game computes it (DERIVATION 58).

Read from the ROM and checked against the debrief of the m01 acceptance
run's win (Speed 25, Power 100, Technique 54, total 487, rank C):

  * Speed (0x080248E8): 100 up to the mission's par in days; past it,
    100 - 100 * (days - par) / (3 * par), integer division, and 0 from
    four times par on. The par is the mission record's +0x20 (8 on
    mission one); a campaign table at 0x082EA3D0 replaces it when
    settings +1 is 1 and the byte at 0x0201228D is set (not met yet).
    `days` is the day counter at the win.
  * Power (0x08024970): min(100, 1000 * best_day / enemy_fielded) --
    the most enemy units destroyed in one day against every unit the
    enemy sides ever fielded. 100 whenever the best day took a tenth.
  * Technique (0x08024A48): min(100, floor + 100 * (1 - lost / fielded))
    with the ratio in integer percent; the floor is 20 with settings +1
    at 1 (the campaign), 10 otherwise. 100 while a fifth or less is lost.
  * Total (0x080248AC): 5 * Speed + 2 * Power + 3 * Technique, capped
    at 999. Letter (0x080397B8): up to 249, 449, 649, 849 and 949 are the
    codes 0..4; above 949 is 5, S, unless settings +1 is 0, where A is
    the ceiling.

The per-side counters live in the army record: +0x16 kills today, +0x18
the best day's, +0x37..+0x4E units fielded per type, +0x50..+0x67 units
lost per type. Only a side with a controller that was not eliminated is
scored (0x08024CD0).
"""
from __future__ import annotations

import functools
import json
import pathlib
from dataclasses import dataclass

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "aw1_ai.json"
LETTERS = ("E", "D", "C", "B", "A", "S")
S_TOTAL = 950
A_TOTAL = 850


@functools.lru_cache(maxsize=None)
def _tables() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def par_for(map_id: int, campaign_table: bool = False) -> int:
    """The mission's par in days. `campaign_table` selects the table at
    0x082EA3D0 (maps 0x82 on), the branch taken with settings +1 at 1 and
    0x0201228D set."""
    t = _tables()
    if campaign_table and map_id >= 0x82 and map_id - 0x82 < len(t["campaign_par"]):
        return t["campaign_par"][map_id - 0x82]
    return t["missions"][map_id]["par"]


def speed(days: int, par: int) -> int:
    if days <= par:
        return 100
    if days >= 4 * par:
        return 0
    return 100 - (days - par) * 100 // (3 * par)


def power(best_day: int, enemy_fielded: int) -> int:
    if enemy_fielded <= 0:
        return 0
    return min(100, 1000 * best_day // enemy_fielded)


def technique(fielded: int, lost: int, campaign: bool = True) -> int:
    if fielded <= 0 or lost > fielded:
        return 0
    floor = 20 if campaign else 10
    v = floor + 100 - (100 * lost // fielded)
    return max(0, min(100, v))


def total(sp: int, pw: int, tq: int) -> int:
    return min(999, 5 * sp + 2 * pw + 3 * tq)


def letter(tot: int, campaign: bool = True) -> str:
    for code, bound in enumerate((249, 449, 649, 849, 949)):
        if tot <= bound:
            return LETTERS[code]
    return "S" if campaign else "A"


@dataclass(frozen=True)
class Rank:
    speed: int
    power: int
    technique: int
    total: int
    letter: str


def score(*, days: int, par: int, best_day: int, enemy_fielded: int,
          fielded: int, lost: int, campaign: bool = True) -> Rank:
    sp = speed(days, par)
    pw = power(best_day, enemy_fielded)
    tq = technique(fielded, lost, campaign)
    tot = total(sp, pw, tq)
    return Rank(sp, pw, tq, tot, letter(tot, campaign))


def days_for(target: str, par: int, power_score: int = 100, technique_score: int = 100) -> int:
    """The last day a win still earns `target` ("S" or "A") with the other
    two scores as given -- what a speed objective has to hit."""
    need = {"S": S_TOTAL, "A": A_TOTAL}[target]
    last = 0
    for d in range(1, 4 * par + 1):
        if total(speed(d, par), power_score, technique_score) >= need:
            last = d
    return last

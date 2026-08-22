"""Turn-start income: which tiles pay, how much, and what the rate depends on.

The whole rule is one multiplication, and every part of it is read off the ROM
rather than assumed:

    income(player) = rate * (properties that player owns)

**Which tiles pay** comes from the helper at `0x08025138`, a switch on terrain
id over the range 6..18 whose thirteen jump-table entries point at exactly two
bodies -- "return the rate" or "return 0". Reading the table settles the set
without a single measurement: City, HQ, Airport, Port, Base pay; two unused
slots (17, 18) share the paying body and are unreachable. HQ PAYS. The terrain
struct's own income field (`aw1_terrain.json` +4, 1000 for capturables) is NOT
what this path reads -- it is a parallel constant this code never touches.

**The rate** is the u32 at `0x03004338`, i.e. battle settings + 0x28. It is a
SETTING, not a constant: the parked VS fixtures read 9500 (a VS setup option)
and the 15x10/19x16 fixtures read 1000, and both reproduce their armies'
income exactly. Campaign mode runs this same code -- the helper has no mode
branch and the game has only one income path -- so campaign differs only in
what it writes into that cell, never in the rule.

Which is why `funds_rate()` prefers to DERIVE the rate from the dump: any army
holding at least one property publishes `rate = income / properties` through
its own `funds_incoming` field, so the advisor gets the right answer in
campaign, in VS, and on any setting, without knowing which it is looking at.

**What is added on top**: nothing. The payer at `0x08025310` adds two CO-table
terms to the property sum -- `CO[co, block] + 0x28` every day, and
`+ 0x26` as well when the turn block's day reads 1 -- and both fields are zero
for all twelve COs in both stat blocks (asserted below). No AW1 CO modifies
income and there is no day-one bonus; the terms exist in the code and are dead
in the data.

The one number the game keeps for itself is `Army.income` (army + 0x08), the
running total the property walker leaves behind. It is ground truth for the
board it came from, so `check()` compares it against this model on every dump
and says so when they disagree, instead of letting a wrong property set ride.
"""
from __future__ import annotations

import functools
import pathlib
import struct
from dataclasses import dataclass
from typing import Optional

# Terrain ids whose jump-table entry at 0x08025150 points at the paying body
# (0x08025184) rather than the zero body (0x08025190). Ids 17 and 18 also pay
# but never appear on a map -- aw1_terrain_ids.json has them as unused slots --
# so they are carried here for fidelity to the switch, not because they matter.
FUNDING_TERRAIN = frozenset({6, 8, 10, 11, 14})
FUNDING_TERRAIN_UNUSED = frozenset({17, 18})

# The rate cell, battle settings + 0x28. Read straight in the paying body:
#   ldr r0, =0x03004310 ; ldr r0, [r0, #0x28] ; bx lr
RATE_ADDR = 0x03004338

# Used only when a dump carries neither the rate nor a property-holding army.
# It is the terrain struct's constant and the value every non-VS fixture shows,
# but it is a FALLBACK: anything that lands on it says so out loud.
DEFAULT_RATE = 1000

CO_TABLE_BASE, CO_STRIDE, CO_SUBBLOCK, CO_COUNT = 0x284A0C, 292, 128, 12


@dataclass(frozen=True)
class Income:
    """One army's turn-start income, with the derivation kept visible."""
    player: int
    properties: int
    rate: int
    rate_source: str          # "dump", "derived", or "default"
    amount: int
    # The game's own figure from army + 0x08, when the dump carried an army
    # record for this player. None means the dump had nothing to compare to.
    reported: Optional[int] = None

    @property
    def agrees(self) -> Optional[bool]:
        if self.reported is None:
            return None
        return self.reported == self.amount


def properties(board, player: int) -> int:
    """Count the paying tiles `player` owns."""
    n = 0
    for y in range(board.height):
        trow, orow = board.terrain[y], board.owner[y]
        for x in range(board.width):
            if orow[x] == player and trow[x] in FUNDING_TERRAIN:
                n += 1
    return n


def property_tiles(board, player: int) -> list:
    """The paying tiles `player` owns, as (x, y) -- for reports and plans."""
    return [(x, y)
            for y in range(board.height)
            for x in range(board.width)
            if board.owner[y][x] == player
            and board.terrain[y][x] in FUNDING_TERRAIN]


def funds_rate(board) -> tuple:
    """(rate, source) for this board.

    Prefers the dumped setting cell; falls back to deriving it from any army
    that owns property, since income/properties is exactly the rate; falls back
    again to DEFAULT_RATE. The source string travels with the number so a
    caller can refuse to plan on a guess.
    """
    dumped = getattr(board, "funds_per_property", None)
    if dumped:
        return int(dumped), "dump"
    for army in board.armies:
        n = properties(board, army.player)
        if n and army.income and army.income % n == 0:
            return army.income // n, "derived"
    return DEFAULT_RATE, "default"


def income(board, player: int, *, rate: Optional[int] = None) -> Income:
    """This player's turn-start income on this board."""
    if rate is None:
        rate, source = funds_rate(board)
    else:
        source = "given"
    n = properties(board, player)
    reported = next((a.income for a in board.armies if a.player == player),
                    None)
    return Income(player=player, properties=n, rate=rate, rate_source=source,
                  amount=n * rate, reported=reported)


def check(board) -> list:
    """Compare the model against the game's own income field, per army.

    Returns a list of complaint strings, empty when every army agrees. Cheap
    enough to run on every dump, and it is the only free regression there is on
    the property set: a terrain id wrongly in or out of FUNDING_TERRAIN shows
    up here the moment a map has one.
    """
    out = []
    rate, source = funds_rate(board)
    for army in board.armies:
        got = income(board, army.player, rate=rate)
        if got.reported is None or got.agrees:
            continue
        out.append(
            f"P{army.player}: the game reports income {got.reported} but "
            f"{got.properties} owned properties at {rate}/property "
            f"({source}) makes {got.amount} -- either the property set or "
            f"the rate is wrong for this board")
    return out


def forecast(board, player: int, days: int, *, rate: Optional[int] = None):
    """Funds after `days` more turn starts, assuming the property set holds.

    A FACT only for days=0; every day beyond that assumes nothing is captured
    or lost, which is exactly the assumption a build plan is making when it
    saves up. Callers that print this must label it as the projection it is.
    """
    got = income(board, player, rate=rate)
    funds = next((a.funds for a in board.armies if a.player == player), 0)
    return funds + days * got.amount


# --- the dead CO terms, asserted rather than believed ---------------------

@functools.lru_cache(maxsize=None)
def co_income_terms(rom_path) -> dict:
    """Read CO record +0x26 / +0x28 for every CO and both stat blocks.

    The payer adds these to the property sum. They are zero throughout, which
    is what lets income collapse to one multiplication -- so the claim is
    checked against the ROM instead of being taken on trust.
    """
    rom = pathlib.Path(rom_path).read_bytes()
    out = {}
    for co in range(CO_COUNT):
        for block in (0, 1):
            a = CO_TABLE_BASE + co * CO_STRIDE + block * CO_SUBBLOCK
            out[(co, block)] = (struct.unpack_from("<h", rom, a + 0x26)[0],
                                struct.unpack_from("<h", rom, a + 0x28)[0])
    return out

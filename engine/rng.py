"""The game's RNG: the generator, and the luck consumption.

The generator, read off the ROM at 0x08010A84 and confirmed live twelve
times out of twelve through the meteor probe (DERIVATION 30):

    state' = ((4*state + 2) * (4*state + 3)  mod 2**32) >> 2

on the u32 at 0x03001D30. Callers get the NEW state back as the draw, and
reduce it themselves -- the meteor picks its strategy as state' % 3 through
the modulo helper at 0x0807B6AC.

THE LUCK CONSUMPTION IS READ AND MEASURED NOW (DERIVATION 32). The combat
luck block at 0x0802333A draws once and reduces through the CO record:

    roll = draw % (10 + good) - bad      good/bad = record +0x2A/+0x2B

then damage + roll floors at 0 (0x0802341E). A battle resolution makes
EXACTLY FOUR draws, all from this same block (with the fixed-luck byte set,
the whole battle draws nothing), and the STRIKE's roll is the THIRD --
measured 20/20 across Andy (%10), Nell (%20, a live 16), and Sonja
(%25-15, a live -14), tests/fixtures/luck_probes.json. The counter's draw
is made and discarded, which is why A9b measured no counter luck.

So a read of 0x03001D30 at target-confirm makes the strike's damage a
POINT, not an envelope: strike_luck() below, fed to damage_for_luck().
The draw index is measured on the standard drive (confirm from the
forecast); a different UI path reaching the resolver has not been swept.

One byte found in passing: settings +0x06 (0x03004316) nonzero skips the
RNG entirely and adds a flat +5 -- a fixed-average-luck mode. Which setup
option sets it is unknown; every capture so far reads 0.
"""

MASK = 0xFFFFFFFF
ADDR = 0x03001D30

# Draws per battle resolution, and which of them lands on the strike. Four
# draws with the strike third when the defender's weapon can answer at
# contact (DERIVATION 32); when it cannot -- an indirect shot, or a target
# with nothing to fire back -- the resolution makes only TWO draws and the
# strike is the SECOND (the differential corpus, DERIVATION 43:
# attack-artillery-in-place drew 2 and landed on draw 2; every countered
# battle drew 4 and landed on draw 3, a target that died to the strike
# included, so what matters is whether a counter is POSSIBLE, not whether
# one happened).
BATTLE_DRAWS = 4
STRIKE_DRAW = 3
BATTLE_DRAWS_NO_COUNTER = 2
STRIKE_DRAW_NO_COUNTER = 2


def next_state(state: int) -> int:
    """One draw: the new state, which is also the value consumers reduce.

    Every add and multiply wraps at 32 bits exactly as the Thumb code does.
    """
    s4 = (state << 2) & MASK
    a = (s4 + 2) & MASK
    b = (s4 + 3) & MASK
    return ((a * b) & MASK) >> 2


def luck_reduce(draw: int, good: int = 0, bad: int = 0) -> int:
    """One draw -> a luck roll, exactly as 0x0802333A..0x08023414 does it."""
    return draw % (10 + good) - bad


def strike_luck(state: int, good: int = 0, bad: int = 0, *,
                counter_possible: bool = True, draw: int = None) -> int:
    """The roll the NEXT driven attack's strike will get, from the state at
    target-confirm. A battle whose defender can answer burns four draws and
    the strike's is the third; one whose defender cannot burns two and the
    strike's is the second. Feed the result to damage.damage_for_luck() for
    the exact damage. `good`/`bad` are the CO record's +0x2A/+0x2B: the
    range is -bad .. 9+good-bad, so from a (min, max) pair
    good = max - min - 9 and bad = -min."""
    if draw is None:
        draw = STRIKE_DRAW if counter_possible else STRIKE_DRAW_NO_COUNTER
    s = state
    for _ in range(draw):
        s = next_state(s)
    return luck_reduce(s, good, bad)

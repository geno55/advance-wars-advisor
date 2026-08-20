"""The game's RNG, as far as it is actually derived.

The generator IS derived now. Read off the ROM at 0x08010A84 and confirmed
live twelve times out of twelve (DERIVATION 30: the meteor probe wrote a seed,
activated, and read the state back after the single draw the activation makes):

    state' = ((4*state + 2) * (4*state + 3)  mod 2**32) >> 2

on the u32 at 0x03001D30. Callers get the NEW state back as the draw, and
reduce it themselves -- the meteor picks its strategy as state' % 3 through
the modulo helper at 0x0807B6AC.

WHAT THIS DOES NOT SETTLE: how the combat LUCK path consumes the state. The
luck roll is uniform 0..9 (measured, DERIVATION 16), and this generator plus
a %10 would make every damage roll predictable from a read of 0x03001D30 --
but that consumption has not been read or measured, so damage stays an
envelope and this module refuses to predict a luck roll. The obvious kill:
seed the state, fight one battle, check the roll equals next(seed) % 10.
"""

MASK = 0xFFFFFFFF
ADDR = 0x03001D30


def next_state(state: int) -> int:
    """One draw: the new state, which is also the value consumers reduce.

    Every add and multiply wraps at 32 bits exactly as the Thumb code does.
    """
    s4 = (state << 2) & MASK
    a = (s4 + 2) & MASK
    b = (s4 + 3) & MASK
    return ((a * b) & MASK) >> 2

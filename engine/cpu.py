"""The CPU's turn in the game's own words -- ROADMAP step 3, begun.

WHAT EXISTS HERE

  * `Command`: the AI's 20-byte command record at 0x030050F0, decoded. The
    AI issues one per unit and dispatches each through the switch at
    0x080669A0 (DERIVATION 44): id, unit slot, the tile the unit moves to,
    the tile it started from, the target slot for Fire, the RNG state the
    record carries (the dispatcher writes it back into 0x03001D30 before
    executing, so the record's battle is reproducible from it), and the
    fuel the record restores.
  * `to_action`: the engine Action a record names on a board, found among
    `actions.actions_for` by kind, tile and target -- so a traced turn is a
    list of Actions the forward model can apply.
  * `replay`: the board the traced turn leaves, through `sim.apply` with
    each record's own RNG state. `tools/cpu_trace.py replay` diffs that
    against the game's after-dump: a third differential check on sim.py,
    and the proof that the record fields are read correctly.

  * `predict`: the turn the CPU WILL take, from engine/cpu_ai.py -- the
    game's AI ported routine by routine (DERIVATION 45). It returns the
    commands in order, the board they leave, and every RNG draw the AI
    makes on the way, and it reproduces all seven traced turns record for
    record and draw for draw (tests/test_cpu.py). What it does not port
    raises NotImplementedError naming the ROM routine.

Command ids, as the dispatcher's jump table at 0x08066A04 orders them and
as the traces confirmed them: 2 wait, 3 capture, 4 fire (target slot at
+6), 6 load, 7 drop (a direction per cargo slot at +6/+7; 1 = north
measured, 2..4 assumed east/south/west), 9 join, 10 dive, 11 rise (from
the routines the arms call), 12 build and 1/13 moves (from the arms; not
yet seen in a trace), 5/8/14/15/16/17 unread.

Seven traces replay exactly through this module and sim.apply
(tests/test_cpu.py); the eighth, under fog, needs `to_action(..., fog=False)`
-- the CPU's commands were identical to the clear-weather turn (the AI does
not look at fog), and actions_for under fog offers no shot at an enemy
that only becomes visible once the unit has moved. That is an action-layer
gap, listed in ASSUMPTIONS; the predictor enumerates fog-off to apply what
the CPU does.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    from . import actions, sim
except ImportError:                     # engine/ on the path
    import actions
    import sim

Coord = Tuple[int, int]

COMMAND_NAMES = {1: "move", 2: "wait", 3: "capture", 4: "fire", 5: "supply",
                 6: "load", 7: "drop", 8: "cmd8", 9: "join", 10: "dive",
                 11: "rise", 12: "build", 13: "move13", 14: "cmd14",
                 15: "cmd15", 16: "cmd16", 17: "end"}


@dataclass(frozen=True)
class Command:
    id: int
    slot: int
    tile: Coord            # +2/+3: where the unit ends its move
    origin: Coord          # +4/+5: where it started
    arg: int               # +6: the target slot for fire, the cargo index for drop
    arg2: int              # +7
    rng: int               # +8: the RNG state the record carries
    fuel: int              # +0x12

    @property
    def name(self) -> str:
        return COMMAND_NAMES.get(self.id, f"cmd{self.id}")


DROP_DIRS = {1: lambda t: (t[0], t[1] - 1), 2: lambda t: (t[0] + 1, t[1]),
             3: lambda t: (t[0], t[1] + 1), 4: lambda t: (t[0] - 1, t[1])}

# The AI's Fire resolves its strike on the FIRST draw from the record's RNG
# state, where the human's confirm-from-forecast path takes the third
# (DERIVATION 32) -- measured on the Max trace, where draw 1 reproduces the
# 55 the game dealt and draw 3 would have dealt 57 (DERIVATION 44). One
# trace; the two other AI battles agree under either draw.
AI_STRIKE_DRAW = 1


def from_record(rec: dict) -> Command:
    """A trace row (tools/cpu_trace.py) as a Command."""
    return Command(id=rec["id"], slot=rec["slot"], tile=(rec["x"], rec["y"]),
                   origin=(rec["tx"], rec["ty"]), arg=rec["b6"], arg2=rec["b7"],
                   rng=rec["rng"], fuel=rec["fuel"])


def to_action(board, cmd: Command, warnings: Optional[list] = None,
              fog: Optional[bool] = None):
    """The Action this record names on `board`, or None with a warning.
    `fog=False` enumerates as if fog were off -- the CPU plays that way
    (DERIVATION 44), and its predicted shots at units the mover cannot yet
    see need the Action the fog-on enumeration withholds."""
    warnings = warnings if warnings is not None else []
    unit = sim.unit_in(board, cmd.slot)
    if unit is None:
        warnings.append(f"{cmd.name} #{cmd.slot}: no such unit on the board")
        return None
    every = actions.actions_for(board, unit, warnings=warnings, fog=fog)
    kind = {"move": "wait", "wait": "wait", "capture": "capture", "fire": "attack",
            "load": "load", "drop": "drop", "join": "join", "dive": "dive",
            "rise": "rise", "supply": "supply"}.get(cmd.name)
    if kind is None:
        warnings.append(f"{cmd.name} #{cmd.slot}: no Action mapping for id {cmd.id}")
        return None
    found = [a for a in every if a.kind == kind and tuple(a.tile) == cmd.tile]
    if kind == "attack":
        found = [a for a in found if a.target is not None and a.target.slot == cmd.arg]
    if kind == "supply":
        # id 5, +6 the slot the APC came to refill (the supply-apc traces,
        # DERIVATION 51); the engine's Supply refills every needy neighbour
        # of the ending tile, that one among them
        found = [a for a in found
                 if any(f.target.slot == cmd.arg for f in a.supplies)]
    if kind == "drop":
        # +6/+7 are per cargo slot: 0 keeps the passenger, 1..4 drops it in
        # a direction (0x08066D64 -> the delta table at 0x083B7ED8; 1 read
        # north on the a15 trace, DERIVATION 44)
        found = [a for a in found
                 if a.target is not None and a.drop_tile is not None
                 and ((cmd.arg and a.target.slot == unit.cargo
                       and a.drop_tile == DROP_DIRS[cmd.arg](cmd.tile))
                      or (cmd.arg2 and a.target.slot == unit.cargo2
                          and a.drop_tile == DROP_DIRS[cmd.arg2](cmd.tile)))]
    if kind in ("load", "join"):
        pass                                    # the tile names the partner
    if len(found) != 1:
        warnings.append(f"{cmd.name} #{cmd.slot} -> {cmd.tile}: {len(found)} "
                        f"matching action(s) among {len(every)}")
        return found[0] if found else None
    return found[0]


def predict(board, player: int, ctx, *, rng: Optional[int] = None):
    """The turn the CPU will take from `board` for `player` (engine/cpu_ai).

    `ctx` is a cpu_ai.Context: the per-unit AI bytes, the sides' team and
    HQ bytes, the mission's profile and the settings bytes, all from a dump
    (`cpu_ai.Context.from_dump`). `board` is the board the CPU starts its
    turn on -- hand the human's End Turn over with sim.end_turn first --
    and `rng` the RNG state at that moment (the dump's). Returns the
    cpu_ai.Turn: `.commands`, `.board`, `.draws`."""
    try:
        from . import cpu_ai
    except ImportError:
        import cpu_ai
    return cpu_ai.predict(board, player, ctx, rng=rng)


def replay(board, cmds: List[Command], *, warnings: Optional[list] = None,
           luck_draw: Optional[int] = AI_STRIKE_DRAW):
    """The board after the traced commands, each applied through sim.apply
    with the RNG state its record carried (a fire's strike is then a point
    on AI_STRIKE_DRAW; `luck_draw` overrides it). `board` is the board the
    CPU starts its turn on: hand the human's End Turn over with
    sim.end_turn first. Records that name no Action are skipped with a
    warning."""
    warnings = warnings if warnings is not None else []
    for cmd in cmds:
        act = to_action(board, cmd, warnings)
        if act is None:
            continue
        kw = {"rng_state": cmd.rng, "luck_draw": luck_draw} if act.kind == "attack" else {}
        board = sim.apply(board, act, warnings=warnings, **kw)
    return board

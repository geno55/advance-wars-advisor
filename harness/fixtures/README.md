# Measurement fixtures

mGBA save states, each sitting at **target-select**: the attacker has moved, Fire
is chosen, the cursor is on the defender, and A has not been pressed. Every
seeded sweep under `tests/fixtures/` was taken on one of these, and
`tests/test_corpus.py` replays those sweeps on every run.

They are checked in over the `*.ss[0-9]` rule in `.gitignore` — see the comment
there for why, and for what is and is not inside one.

| fixture | attacker | defender | what it measures |
|---|---|---|---|
| `atk_tank_v_inf_mountain.ss1` | Tank, slot 7 | Infantry, slot 66, Mountain (4★) | the display rule (`att57`, `def81`, `def85`, `def65`) and the counter formula |
| `atk_tank_v_inf_wood.ss1` | Tank, slot 7 | Infantry, slot 66, Wood (2★) | the stars axis, and `ceil` off the mountain |
| `atk_tank_v_inf_city.ss1` | Tank, slot 7 | Infantry, slot 66, City (3★) | the stars axis; also where A10 was caught |
| `atk_inf_v_tank_wood.ss1` | Infantry, slot 7 | Tank, slot 66, Wood (2★) | where the CO modifiers enter the COUNTER path — **derived, not played**, see below |

Every fixture holds **P1 = Andy (1), P2 = Max (2)**. That is the fixture's own
state, not a neutral one: a sweep that forces `co_abilities = 1` and writes only
P1 leaves Max live on the other side. `dmg_seedsweep` now says so, and records
`co_p1`/`co_p2` read back out of RAM so a reader never has to assume.

## The derived one

`atk_inf_v_tank_wood.ss1` was not played. It is `atk_tank_v_inf_wood.ss1` with
two unit records rewritten — slot 7 Tank→Infantry, slot 66 Infantry→Tank, ammo
and fuel with them — by `harness/mgba_counter_co.lua:counter_co_build()`.

The reason it has to exist at all: locating a CO modifier needs a counter big
enough to move, and every played fixture counters with an Infantry's base 5,
which lands on 1 or 2 whatever the CO says. Turning the pair round makes the
counter a Tank's base 75.

The reason it might not have been a real board: the state is parked at
target-select, so the game chose Fire while the attacker was still a Tank. If it
cached the matchup then rather than resolving it on confirm, the sweep measures
the cache. **That is a reading, not an argument** — an Infantry hitting a Tank
does 4–12 and a Tank hitting an Infantry does 60–67. `counter_co_probe()` fires
one case and says which it saw.

**It read 4–12.** All four sweeps report `attacker_type: 1` and openings of
4–11 and 4–12, so the game resolves the matchup from the unit records at
confirm and the rewrite reaches the damage path. The counter came back 59–64 on
the neutral sweep, which is what the board predicts to the point. A retype at
target-select is therefore a legitimate way to build a damage fixture — one
result, on one pair of types, so it is worth re-checking the opening the same
way next time rather than assuming it generalises.

Both units are at 100 internal HP in every fixture. Sweeps that need a different
HP write it per case — see `unithp()` — and every sweep ships the control pair
that proves the write changes nothing.

## The attacker's tile is not the one in the header

A fixture is at target-select, so the attacker's unit record still holds the tile
it **started** on, not the tile it fires from. This is measured, not suspected:
the City fixture reads

    (9,7) terrain 5 (Road, 0★) before confirming,  (10,8) terrain 1 (Plain, 1★) after

That difference is what made the City counterattacks miss on every hypothesis
until they were scored against the tile the unit actually fought from. See
`docs/ASSUMPTIONS.md` A10.

So: **read `attacker_terrain_after`, never `attacker_terrain`.** Sweeps taken
before the harness recorded both carry only the pre-move tile, and
`tools/counter_check.py` says so when it sees one.

The mountain and wood fixtures scored correctly against their headers, but not
because the headers were right — their pre-move and post-move tiles happened to
carry the same defence. Whether those attackers move at all is unrecorded; re-run
either with the current harness and it will say.

## Getting the slots

    dmg_units("harness/fixtures/atk_tank_v_inf_city.ss1")

Read-only. `dmg_save()` prints the same table but writes the state as a side
effect, which will overwrite the fixture you are trying to inspect.

## Making a new one

1. Turn battle animations **off** in the game options. The sweep polls either
   way, but animations cost seconds per case and there are 64 of them.
2. Move the attacker, choose Fire, put the cursor on the defender. Do not press A.
3. `dmg_save("harness/fixtures/<name>.ss1")` — writes the state and lists slots.
4. `dmg_probe(fixture, ATT, DEF)` once, to check the exchange fits inside
   `EXCHANGE_FRAMES`. If it warns that a unit was still changing near the end of
   the window, raise it and re-run rather than recording.
5. Add a row to the table above, including the terrain the defender is on.

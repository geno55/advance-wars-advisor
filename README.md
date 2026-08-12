# Advance Wars advisor — milestone 2: the damage engine

Table-driven damage model for **Advance Wars (USA) Rev 1** on GBA, plus the
harness that will prove it matches the real game.

> **You supply the ROM.** No ROM is included or redistributed here, and none ever
> should be. The tools take a path to your own legally obtained copy and verify
> its SHA-1 (`15053499d5b3f49128a941d7f2d84876f5424d0c`) before reading anything;
> a mismatch is a hard error, because the table offsets are build-specific.
> What *is* checked in is the extracted numeric data — base damage values — which
> this tool needs in order to interoperate with the game.

The rule this project runs on: a tool that gives *confidently wrong* advice is
worse than no tool. So the ROM-derived facts and the unverified guesses are kept
strictly apart, and the engine refuses to hand out advice under a formula that
has not been checked yet.

## Layout

```
data/aw1_damage.json      extracted tables + provenance + open questions
engine/damage.py          the model: weapon selection, 6 formula variants, ranges
tools/extract_tables.py   ROM -> JSON, with 17 structural assertions
tools/find_tables.py      how the tables were located in the first place
tools/quote.py            CLI: quote a single matchup
tools/shopping_list.py    ranks which battles are worth recording

RE toolkit (all reusable for the state reader and beyond):
tools/dump_region.py      strided hexdump
tools/dump_words.py       32-bit words with pointer/string annotation
tools/disasm.py           THUMB disassembly with literal-pool resolution
tools/find_xrefs.py       literal-pool references to an address
tools/find_calls.py       THUMB BL callers of an address
tools/find_ptr_table.py   locate pointer tables by known string targets
tools/find_strings.py     ASCII clusters, terrain/mission name hunting
tools/lz77.py             GBA BIOS LZ77 decompression
tools/map_dump.py         render a mission map (u16 w,h + tile grid)
tools/co_mods.py          per-CO unit modifier tables
harness/observations.csv  where you record real battles
harness/mgba_ramtool.lua  RAM search/diff, groundwork for the state reader
tests/test_damage.py      23 regression tests
tests/calibrate.py        eliminates wrong formula hypotheses from observations
docs/DERIVATION.md        exactly how the tables were found (reproducible)
docs/ASSUMPTIONS.md       what is established, assumed, and unknown
```

## Status

**Done and verified against the ROM:**
- Both 24×24 damage matrices, all 18 units, extracted from `0x283B48` and
  `0x283D88` and cross-checked 17 ways.
- Internal unit ID map recovered structurally, not guessed.
- Weapon selection reproducing known behaviour (Md Tank → 105 on Infantry with
  its MG, 85 on Tank with its cannon; out-of-ammo fallback).
- 23 regression tests, all passing.
- Calibration machinery proven correct by synthetic round-trip: from 3750
  hypotheses it recovers the exact formula variant and terrain-star map.

**Not yet verified — needs you and an emulator:**
- The damage *formula* (6 candidate variants).
- Terrain defence stars (solved as free parameters by the same run).
- The `fighter-secondary` ROM discrepancy.

No emulator was available here, so nothing in this project has yet touched the
running game. That is the gap milestone 2 closes, and it needs ~20 minutes of
your time.

## Closing the loop

```bash
python tests/test_damage.py
```

```bash
python tests/calibrate.py --selftest
```

Then record real battles with the interactive recorder — do not hand-edit the CSV:

```bash
python harness/record.py
```

It takes a matchup once, then one number per battle, appends valid rows, and
re-runs the hypothesis sweep after every entry so you stop the moment it
converges. `u` undoes, blank line changes matchup, `q` quits.

Protocol:

1. Play a **neutral CO** with no power active. CO modifiers are real and large
   (the ROM has entries at 150/100 and 170/100); the wrong CO silently scales
   every observation.
2. Use **both units at full health** — the only internal HP you can be sure of
   by looking.
3. Note the **defender's** terrain.
4. Save state → attack → read the defender's HP → load state.

**On repeats and the RNG.** A save state restores the RNG along with everything
else. If AW1 only advances its RNG on demand, reloading and repeating the same
attack reproduces the same luck roll and the repeat teaches you nothing. If it
ticks per frame, your timing decorrelates them and repeats do sample. This is
unverified — test it first: repeat one identical attack three times and see
whether the results differ. If they are identical, **vary the matchup, terrain,
or attacker HP instead of repeating**; each distinct combination is a fresh
constraint and converges faster. `record.py` detects the identical-run case and
tells you to move on.

Either way the solver stays sound: it only ever asserts "some luck value in 0–9
explains this", so a frozen roll slows convergence but cannot produce a wrong
answer.

Then:

```bash
python tests/calibrate.py harness/observations.csv --suggest
```

It prints which formula variants and star values survive, and — if it has not
converged — which experiment to run next to split the remaining hypotheses
fastest. Repeat until one hypothesis is left, then flip
`provenance.verified_against_emulator` to `true` and set `DEFAULT_VARIANT`.

At that point `resolve()` stops raising `Unverified`, and milestone 2 is
genuinely done rather than nominally done.

## Re-extracting from the ROM

```bash
python tools/extract_tables.py "../Advance Wars (USA) (Rev 1).gba" data/aw1_damage.json
```

Exits non-zero on a ROM whose sha1 does not match, or if any structural
assertion about the unit ID mapping fails.

## Next

Milestone 1 (state reader) is now the bottleneck: `harness/mgba_ramtool.lua` is
the way in — find the unit array by value search, then the advisor can read the
board instead of asking you to transcribe it.

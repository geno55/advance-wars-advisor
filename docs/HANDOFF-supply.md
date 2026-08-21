# Handoff: modeling supply

For the session that picks this up. Everything here is either established
with a pointer, or explicitly a question with a designed measurement. The
repo's rule applies throughout: **measure or read at a named address; never
ship community lore as a finding.** The lesson that keeps repeating
(DERIVATION 26): a term that never leaves the integers is a term that has
never been tested — vary every variable at least once.

## The task

`engine/actions.py` declines supply outright ("Terrain repair/resupply on
WAIT. Real, unmeasured, absent."). Model it, so the advisor stops
under-reporting what a turn can do:

1. **APC Supply** — the menu command and (if it exists in AW1) turn-start
   auto-supply of adjacent friendlies.
2. **Property repair/resupply on wait** — which terrains restore what to
   whom, the +HP amount, and the funds charge with its refusal rule.
3. **Daily fuel burn** — the per-turn drain, including the dived-sub
   question, and the crash/sink rule at fuel 0.
4. Joining and unloading are ADJACENT, not in scope — take them only if
   they fall out of the same turn-start walker for free.

## Established, with pointers

- **Unit record (12 bytes, base ptr `[0x08282CB8]`, slot×12):** `+0` type
  (1-based), `+1` flags (bit0 acted, bit3 loaded, bit5 dived, bits1-2
  animation), `+2/+3` x/y, `+4` u16 = hp(0-6) | ammo(7-10) | capture(11+),
  **`+6` fuel (`%128`; what the high bit means is unread)**, `+7` cargo
  slot. `harness/mgba_state.lua:247` reads fuel; `state.py Unit.fuel`
  carries it. NOTE: the Mesen fixture dumpers (`mesen_sonja_fix.lua`,
  `mesen_vision_rules.lua`) hardcode `"fuel": 99` — fix that before any
  supply fixture is dumped, the offset is known.
- **Stats table** (`0x08283058 + type*0x70`, 1-based type): max fuel
  `+0x12`, max ammo `+0x0D`, **fuel per turn `+0x38`**, unit class
  `+0x14`, all extracted in `data/aw1_unit_stats.json`. The extractor
  noted `+0x38` "repeats across many offsets; only +0x38 is read here" —
  the repeats at +0x39.. may be the dived/other-state burn rates. Dump
  them first thing; the dived-sub extra burn likely lives there.
- **The repair routine is already located: `0x08029D9C`.** Andy's Hyper
  Repair drives it per unit with `(unit_ptr, amount_display, 0)` and
  brackets the call with funds forced to 999999 and restored
  (DERIVATION 27, `0x0801C264`) — which proves it CHARGES FUNDS
  internally. Disassemble it directly: the charge arithmetic, the
  partial-repair-when-broke rule, and whether it also caps at max HP are
  all in there. This is the shortest path in the whole task.
- **Ammo spend site** `0x080232B2` (primary fires); ammo gate `& 0x780`.
- **Terrain records**: `0x08284170 + terrain*20` (stars at +8, A3);
  owner bits are the map byte's top 3 (`map+0x1432+tile`, writes proven
  to carry real defence). A "who repairs whom" table plausibly sits in
  the terrain record's unread bytes — extract before hypothesizing.
- **Gates**: `0x03004318` CO modifiers, `0x03004317` charge,
  `0x03004316` fixed luck. Supply itself should NOT be CO-gated, except
  Andy's +0x0A heal-adjust byte (zero on every record) touching repair.

## The unknowns, each with its designed measurement

The rig recipe is DERIVATION 25 + the memory file; fixture = savestate
slot 2 (powers-on VS, 15×10, P2 to move; P2 APC is slot 69 at (12,7),
P2 Tank slot 71 at (9,7)). Launch with `Start-Process -FilePath <Mesen>
-ArgumentList ... -PassThru -Wait` — a plain `&` returns without waiting.

1. **APC Supply command.** Write Tank fuel low (`ua(71)+6`), real-move
   the APC adjacent (counted direction taps — the map cursor does NOT
   track in move-select mode, DERIVATION 29), screenshot the action menu
   (expect Supply), drive it with a **write-watch on the Tank's `+6`**:
   the PC names the supply routine. Then read whether it also fills ammo
   (watch `+4`), whether both adjacent sides fill, and whether the APC's
   acted bit is spent.
2. **Turn-start auto-supply and fuel burn.** Park the low-fuel Tank next
   to the APC, End Turn twice (map-menu End is last item; up-wraps from
   the top; park the cursor on an EMPTY tile first — a P1 unit under the
   cursor opens unit-select instead), with write-watches on several
   units' `+6` across the boundary. One run answers three questions:
   does adjacency refill at turn start, which units burned how much
   (compare stats `+0x38`), and the PCs of both walkers. Disassemble
   from the PCs — expect one turn-start walker doing burn + supply +
   repair in a fixed order; READ THE ORDER, it decides whether a
   0-fuel air unit adjacent to an APC crashes or refuels.
3. **Property repair.** Damage a P2 unit (hp write), park it on a
   P2-owned city (terrain byte's owner bits are writable — write a city
   with P2's owner tag under the unit, same trick as the Wood in A16),
   End Turn twice, diff hp AND army funds. Sweep: hp 81 (partial bar),
   funds written to 0 (the broke rule), funds written to just-under the
   charge (partial repair or refusal?), wrong-class unit on the property
   (air on city: resupply only?). The charge formula read off
   `0x08029D9C` predicts these; the sweep confirms.
4. **Fuel 0.** Air unit (type-write a Sub→BCopter stays valid — type
   writes are transparent) with fuel written 0, End Turn pair: does it
   vanish at turn start, and at which PC? Watch the unit's `+0` (type
   byte zeroing = removal). Naval same question. Dived sub burn: set
   bit5 on a sub (honoured when written, DERIVATION 31), measure the
   burn delta across a turn boundary.

## Traps this repo has already paid for

- Position writes do NOT relocate your own units for selection, and the
  tile→unit index (`map+0x51A`, second layer `+0x12`) does not follow
  ANY position write. Supply adjacency may read records OR the index —
  measure with a real move first; only trust teleports after proving the
  path reads records (the meteor applier did, the scorer did not).
- `goto_tile` works on the map, silently fails in move-select mode.
- Savestate loads reproduce the RNG state — "same roll every reload" is
  the savestate, not determinism worth reporting. Battles burn exactly
  four draws, the strike's roll is the third (`rng.strike_luck`), so
  seeded runs can pin damage exactly when a probe needs it.
- Mesen boots from `Documents/Mesen2/Saves` — do not touch save files;
  fixtures are .mss states the user parks (ask, don't scan for paths).
- PowerShell: `git commit -F <file>` (double quotes in `-m` split argv);
  `2>&1` on native commands fakes failure exit codes.

## Deliverables, repo-style

- DERIVATION 33: the narrative, including what was guessed wrong.
- `tests/fixtures/supply_probes.json` + replay tests (the measured rows
  are the spec; tests refute alternatives, not just confirm the pick).
- `engine/actions.py`: offer Supply as an action with facts, add wait-
  repair/resupply facts to move candidates, and DELETE the decline
  bullet; `engine/state.py` fuel notes updated with the high-bit answer.
- Any ROM table found (repair amounts, terrain-supply classes) goes
  through an extractor with assertions into `data/`, not hand-typed.
- ASSUMPTIONS: Unknown bullet replaced by an Established entry naming
  addresses and fixtures. README file-list and test counts.
- Single-sentence-finding commit subjects, straight to main, push.

## Adjacent stale items, cheap to fix while in there

- `actions.py`'s docstring still says CO power activation "threshold is
  not known" — DERIVATION 27 settled it; activation could now be offered
  as an army action (meter, threshold, ready flag all modelled in
  `engine/co.py`).
- The two Mesen fixture dumpers hardcode `"fuel": 99` (above).
- Unit flags bit4 (`0x10`, seen on the fixture APC) is undecoded — it
  will probably fall out of reading the transport/supply code, since it
  appeared on exactly the unit that carries things.

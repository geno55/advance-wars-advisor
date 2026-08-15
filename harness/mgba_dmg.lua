-- Damage sweep: sample the luck roll by varying WHEN we confirm the attack.
--
-- THE PROBLEM THIS SOLVES, from docs/ASSUMPTIONS.md A4. Twenty hand-recorded
-- trials of one matchup produced only three distinct damage values. The GBA
-- advances its RNG as frames pass, so a human reloading a save state and
-- confirming after a similar delay each time samples a narrow window of the
-- sequence. An earlier "357:1 in favour of one variant" was computed from the
-- absence of high rolls under exactly that bias, and had to be withdrawn.
--
-- A4's own kill condition is "deliberately vary the delay between loading and
-- confirming". That is a loop, not a chore:
--
--     for k = 0, kmax do
--       load the fixture          -- restores RAM *and* the RNG state
--       idle k frames             -- the only thing that differs
--       press A                   -- confirm the attack
--       read the damage
--     end
--
-- NO RAM IS WRITTEN. Every iteration is the game playing itself from an
-- identical state; k is the sole independent variable. So this sweep carries
-- none of the write-transparency risk that docs/DERIVATION.md section 15 had to
-- rule out for the movement sweep -- the k=0 case IS the control.
--
-- MAKING THE FIXTURE
--   1. Move your attacker, choose Fire, and put the target cursor on the
--      defender. Do NOT press A.
--   2. Turn battle animations OFF in the game options. The sweep polls for
--      completion either way, but animations make each case several seconds.
--   3. dmg_save("C:/tmp/atk.ss1")   -- writes the fixture and lists slots
--
-- THEN
--   dmg_probe("C:/tmp/atk.ss1", ATT_SLOT, DEF_SLOT)
--   dmg_sweep("C:/tmp/atk.ss1", ATT_SLOT, DEF_SLOT, "C:/tmp/dmg.json", 60)
--
-- and feed the result to tools/dmg_ingest.py.

UNIT_BASE_PTR = UNIT_BASE_PTR or 0x08282CB8
MAP_ADDR = MAP_ADDR or 0x02016C2A
MAP_DIMS = MAP_DIMS or 0x030036E0
UNIT_STRIDE = UNIT_STRIDE or 12
ARMY_SLOTS = ARMY_SLOTS or 64
KEY_A = 0x001

-- How long to wait for an attack to resolve. Generous: with animations on, a
-- naval bombardment takes a while. The poll exits as soon as damage lands.
DMG_TIMEOUT_FRAMES = 900

-- THE RNG, read straight off the disassembly at 0x083D69D4:
--     state = state * 20077 + 12345          (u32, wrapping)
--     return (state >> 16) & 0x7FFF          (lsls #1 then lsrs #17)
-- 0x083D69C8 is the matching seed setter. See DERIVATION.md section 16.
--
-- This is why the frame-delay sweep sampled so badly: idling k frames advances
-- the state by whatever the frame happened to consume, which walks a small
-- orbit rather than covering the space. Writing the seed removes the sampling
-- problem entirely -- luck stops being something we infer from damage and
-- becomes something we set.
RNG_LIB_STATE = 0x03000750          -- the library LCG; NOT what combat uses
RNG_MUL, RNG_ADD = 20077, 12345

-- THE COMBAT LUCK STATE, found by bisection rather than by reading code.
-- rng_bisect() narrowed the bytes that decide a roll to four CONSECUTIVE
-- addresses -- a u32 -- which is also why neither half of the final range
-- flipped the damage alone: each split cut the word in two.
--
-- Its algorithm is unknown. It matches no standard LCG (eight were tried
-- against the observed before/after pair). That does not matter for the
-- experiment: to make luck an input we only need to WRITE it.
RNG_STATE = 0x03001D30

function rng_read(addr) return emu:read32(addr or RNG_STATE) end
function rng_write(v, addr)
  emu:write32(addr or RNG_STATE, v % 0x100000000)
end

-- Dump the state over n frames. Three consecutive samples are enough to solve
-- for an effective per-frame LCG offline (a = (x2-x1)/(x1-x0), c = x1 - a*x0),
-- which either identifies the generator or shows it is not linear.
function rng_trace(n, addr)
  n = n or 24
  local out = {}
  for i = 1, n do
    out[#out + 1] = string.format("%08X", rng_read(addr))
    emu:runFrame()
  end
  console:log("state at 0x" .. string.format("%08X", addr or RNG_STATE) .. ":")
  console:log("  " .. table.concat(out, " "))
end

local function unitaddr(slot)
  return emu:read32(UNIT_BASE_PTR) + slot * UNIT_STRIDE
end

local function readunit(slot)
  local a = unitaddr(slot)
  local v = emu:read16(a + 4)
  return {
    type = emu:read8(a), acted = emu:read8(a + 1) % 2,
    x = emu:read8(a + 2), y = emu:read8(a + 3),
    hp = v % 128, ammo = math.floor(v / 128) % 16,
    fuel = emu:read8(a + 6) % 128,
  }
end

-- Read, or write, a unit's internal HP without disturbing what shares its
-- halfword.
--
-- Record +4 packs THREE fields: hp in bits 0-6, ammo in 7-10, capture progress
-- in 11-15. That was itself a correction -- `ammo = v >> 7` held only while
-- capture was zero, which it was in everything dumped until a capturing
-- infantry reported "ammo 160". So a plain write16 of an hp value zeroes both
-- neighbours, and rng_write's write32 would take out fuel (+6) and cargo (+7)
-- as well. Read, mask, write back.
--
-- Then read it AGAIN, and check the neighbours too. A write that silently did
-- not take turns the sweep below into the machine agreeing with itself, which
-- is the one failure this harness exists to prevent.
function unithp(slot, v)
  local a = unitaddr(slot)
  local cur = emu:read16(a + 4)
  if v == nil then return cur % 128 end
  if v < 1 or v > 100 then
    console:error("internal hp must be 1..100; the field holds 7 bits but no "
      .. "board the game can reach has a unit outside that range")
    return nil
  end
  emu:write16(a + 4, cur - cur % 128 + v)
  local back = emu:read16(a + 4)
  if back % 128 ~= v then
    console:error(string.format("hp write did not take: wrote %d, read %d", v,
      back % 128))
    return nil
  end
  if math.floor(back / 128) ~= math.floor(cur / 128) then
    console:error("hp write disturbed the ammo or capture bits it shares +4 "
      .. "with; aborting rather than measuring a board the game cannot reach")
    return nil
  end
  return v
end

local function terrainat(x, y)
  local w = emu:read8(MAP_DIMS)
  return emu:read8(MAP_ADDR + y * w + x) % 32
end

local function fileexists(path)
  if not (io and io.open) then return nil end
  local f = io.open(path, "rb")
  if f then f:close(); return true end
  return false
end

local function loadfixture(path)
  if fileexists(path) == false then
    console:error("no such file: " .. tostring(path)
      .. "  -- get into position and run dmg_save(\"" .. tostring(path) .. "\")")
    return false
  end
  if not emu:loadStateFile(path) then
    console:error("mGBA refused to load " .. tostring(path))
    return false
  end
  emu:runFrame()
  return true
end

function dmg_save(path)
  if not emu:saveStateFile(path) then
    console:error("could not write " .. tostring(path)
      .. " -- does the directory exist?")
    return
  end
  console:log("saved fixture to " .. path)
  console:log("units (pick the attacker and defender slots):")
  local ubase = emu:read32(UNIT_BASE_PTR)
  for i = 0, 255 do
    local a = ubase + i * UNIT_STRIDE
    local t = emu:read8(a)
    if t >= 1 and t <= 24 then
      local u = readunit(i)
      console:log(string.format("  slot %3d  P%d  type %2d at (%2d,%2d)  hp %3d"
        .. "  ammo %d  on terrain %d",
        i, math.floor(i / ARMY_SLOTS) + 1, u.type, u.x, u.y, u.hp, u.ammo,
        terrainat(u.x, u.y)))
    end
  end
end

-- How long to keep running after the first HP change before reading the result.
-- This used to be 30 frames, taken on faith. Thirty is enough for ONE animation
-- and not for two: the counterattack lands after the opening one finishes, so
-- the old code read the board mid-exchange and threw the attacker away. The
-- window is now generous and, more importantly, SELF-CHECKING -- see below.
EXCHANGE_FRAMES = EXCHANGE_FRAMES or 240

-- Press A, run the whole exchange out, and read BOTH sides.
--
-- Pass att_slot to get the counterattack. The old signature still works and
-- still returns three values, so existing callers are unchanged.
--
-- Returns damage, frames_waited, died, counter, att_ammo_after, att_died.
local function confirm_and_read(def_slot, hp_before, att_slot, att_hp_before)
  emu:setKeys(KEY_A)
  for _ = 1, 4 do emu:runFrame() end
  emu:setKeys(0)
  for f = 1, DMG_TIMEOUT_FRAMES do
    emu:runFrame()
    local d = readunit(def_slot)
    if d.type == 0 or d.hp ~= hp_before then
      -- Something landed. Run the exchange out, tracking the frame at which
      -- either side last moved. A window long enough to contain the whole
      -- exchange ends with a long quiet tail; one that is too short ends while
      -- a unit is still changing. That is checkable, so check it rather than
      -- trusting the constant.
      local last_move, dprev, aprev = 0, nil, nil
      for g = 1, EXCHANGE_FRAMES do
        emu:runFrame()
        local dd = readunit(def_slot)
        local dk = dd.type == 0 and -1 or dd.hp
        local ak = 0
        if att_slot then
          local aa = readunit(att_slot)
          ak = aa.type == 0 and -1 or aa.hp * 16 + aa.ammo
        end
        if dk ~= dprev or ak ~= aprev then
          if dprev ~= nil then last_move = g end
          dprev, aprev = dk, ak
        end
      end
      if last_move > EXCHANGE_FRAMES - 60 then
        console:error(string.format(
          "a unit was still changing at frame %d of a %d-frame window -- this "
          .. "reading may be mid-exchange. Raise EXCHANGE_FRAMES and re-run; do "
          .. "NOT record it.", last_move, EXCHANGE_FRAMES))
      end
      local after = readunit(def_slot)
      local died = after.type == 0
      local dmg = died and hp_before or (hp_before - after.hp)
      if not att_slot then return dmg, f, died end
      local a = readunit(att_slot)
      if a.type == 0 then return dmg, f, died, att_hp_before, 0, true end
      return dmg, f, died, att_hp_before - a.hp, a.ammo, false
    end
  end
  return nil, DMG_TIMEOUT_FRAMES, false         -- nothing happened
end

-- List a fixture's units WITHOUT saving over it. dmg_save() prints the same
-- table but writes the state as a side effect, which is the wrong tool when you
-- have a fixture already and just need its slot numbers and terrain.
function dmg_units(fixture)
  if not loadfixture(fixture) then return end
  local ubase = emu:read32(UNIT_BASE_PTR)
  console:log("units in " .. tostring(fixture) .. ":")
  for i = 0, 255 do
    local t = emu:read8(ubase + i * UNIT_STRIDE)
    if t >= 1 and t <= 24 then
      local u = readunit(i)
      console:log(string.format("  slot %3d  P%d  type %2d at (%2d,%2d)  hp %3d"
        .. "  ammo %d  fuel %3d  on terrain %d",
        i, math.floor(i / ARMY_SLOTS) + 1, u.type, u.x, u.y, u.hp, u.ammo,
        u.fuel, terrainat(u.x, u.y)))
    end
  end
  console:log("terrain ids: 1 Plain, 2 River, 3 Mountain, 12 Bridge -- see "
    .. "data/aw1_terrain.json for the rest")
end

-- One attack, loudly. Run this before sweeping.
function dmg_probe(fixture, att_slot, def_slot)
  if not loadfixture(fixture) then return end
  local a, d = readunit(att_slot), readunit(def_slot)
  if a.type == 0 or d.type == 0 then
    console:error("attacker or defender slot is empty -- wrong slots?")
    return
  end
  console:log(string.format("attacker slot %d: type %d hp %d ammo %d at (%d,%d) on terrain %d",
    att_slot, a.type, a.hp, a.ammo, a.x, a.y, terrainat(a.x, a.y)))
  console:log(string.format("defender slot %d: type %d hp %d at (%d,%d) on terrain %d",
    def_slot, d.type, d.hp, d.x, d.y, terrainat(d.x, d.y)))

  local dmg, frames, died, counter, att_ammo, att_died =
    confirm_and_read(def_slot, d.hp, att_slot, a.hp)
  if dmg == nil then
    console:error("no damage after " .. DMG_TIMEOUT_FRAMES .. " frames. The "
      .. "fixture is probably not sitting on a confirmable attack -- it should "
      .. "be at target select, with the cursor on the defender, before A.")
    return
  end
  console:log(string.format("damage %d after %d frames%s", dmg, frames,
    died and "  (DESTROYED -- damage is a lower bound, use a healthier defender)"
    or ""))
  console:log(string.format("counter %d back at the attacker%s; its ammo is now %d",
    counter, att_died and "  (ATTACKER DESTROYED)" or "", att_ammo))
  return dmg, counter
end

-- The sweep. k frames of idling is the only thing that varies.
function dmg_sweep(fixture, att_slot, def_slot, outpath, kmax)
  if not (io and io.open) then
    console:error("no io library; cannot write " .. tostring(outpath)); return
  end
  kmax = kmax or 60
  if not loadfixture(fixture) then return end
  local a0, d0 = readunit(att_slot), readunit(def_slot)
  local att_terr, def_terr = terrainat(a0.x, a0.y), terrainat(d0.x, d0.y)

  local rows, seen, destroyed = {}, {}, 0
  for k = 0, kmax do
    if not loadfixture(fixture) then return end
    local d = readunit(def_slot)
    for _ = 1, k do emu:runFrame() end
    local dmg, _, died = confirm_and_read(def_slot, d.hp)
    if dmg == nil then
      console:error("case k=" .. k .. " produced no damage; aborting")
      return
    end
    if died then destroyed = destroyed + 1 end
    rows[#rows + 1] = string.format(
      '    {"k": %d, "damage": %d, "destroyed": %s}', k, dmg,
      died and "true" or "false")
    if not died then seen[dmg] = (seen[dmg] or 0) + 1 end
  end

  local vals = {}
  for v in pairs(seen) do vals[#vals + 1] = v end
  table.sort(vals)
  local hist = {}
  for _, v in ipairs(vals) do hist[#hist + 1] = v .. "x" .. seen[v] end
  console:log("damage values seen: " .. table.concat(hist, "  "))
  if #vals > 0 then
    console:log(string.format("range %d..%d over %d case(s)%s",
      vals[1], vals[#vals], kmax + 1,
      destroyed > 0 and ("; " .. destroyed .. " destroyed the defender and were "
        .. "excluded -- their damage is only a lower bound") or ""))
  end

  local out = {
    "{",
    string.format('  "attacker_slot": %d, "defender_slot": %d,', att_slot, def_slot),
    string.format('  "attacker_type": %d, "attacker_hp": %d, "attacker_ammo": %d,',
      a0.type, a0.hp, a0.ammo),
    string.format('  "defender_type": %d, "defender_hp": %d,', d0.type, d0.hp),
    string.format('  "attacker_terrain": %d, "defender_terrain": %d,',
      att_terr, def_terr),
    string.format('  "kmax": %d,', kmax),
    '  "cases": [',
    table.concat(rows, ",\n"),
    "  ]",
    "}",
  }
  local f = io.open(outpath, "w")
  if not f then console:error("could not open " .. outpath); return end
  f:write(table.concat(out, "\n"))
  f:close()
  console:log(string.format("wrote %s: %d cases", outpath, kmax + 1))
end

-- Seed sweep: set the RNG state, then attack. Deterministic, and it covers the
-- luck space evenly instead of walking whatever orbit the frame timing lands on.
--
-- We do NOT assume how many times the RNG is consumed between the write and the
-- luck roll, nor what modulus is applied. Both fall out of the data: the seed
-- is recorded next to the damage, and tools/rng_fit.py searches for the
-- (consumption depth, modulus) that makes damage a consistent function of the
-- roll. Guessing either would be inventing a value to fill a gap.
ARMY_BASE_PTR = ARMY_BASE_PTR or 0x08282CBC
ARMY_STRIDE = ARMY_STRIDE or 0x68

-- Read or write a player's CO id (army +0x1D). Writing it changes the CO live,
-- name and portrait included, which is how every record was identified.
function coid(p, v)
  local a = emu:read32(ARMY_BASE_PTR) + p * ARMY_STRIDE + 0x1D
  if v then
    if v < 0 or v > 11 then
      console:error("co id must be 0..11; higher indexes past the record table")
      return
    end
    emu:write8(a, v)
  end
  return emu:read8(a)
end

-- Sweep with the CO WRITTEN AFTER LOADING rather than baked into the fixture.
--
-- Two separate fixtures for two COs invites the obvious confound: if the CO id
-- did not persist into one of the save states, both sweeps run the same CO and
-- produce identical histograms, which looks exactly like "the CO has no
-- effect". Writing it here means one fixture serves every CO and the only
-- difference between runs is the byte we set. The sweep reports the id it read
-- back, so a failed write cannot pass silently.
function dmg_seedsweep(fixture, att_slot, def_slot, outpath, nseeds, stride, addr, co, co_player, opts)
  if not (io and io.open) then
    console:error("no io library; cannot write " .. tostring(outpath)); return
  end
  -- Late knobs live in a table so the positional list stops growing. Passing
  -- the table in the nseeds slot works too, which is what you want when none of
  -- the middle arguments are being overridden:
  --   dmg_seedsweep(fix, 7, 66, out, {def_hp = 81})
  if type(nseeds) == "table" then opts, nseeds = nseeds, nil end
  opts = opts or {}
  nseeds = nseeds or opts.nseeds or 64
  stride = stride or opts.stride or 2654435761   -- big odd step, spreads seeds
  addr = addr or opts.addr or RNG_STATE
  co = co or opts.co
  co_player = co_player or opts.co_player or 1
  local def_hp, att_hp = opts.def_hp, opts.att_hp
  if not loadfixture(fixture) then return end
  local a0, d0 = readunit(att_slot), readunit(def_slot)
  local att_terr, def_terr = terrainat(a0.x, a0.y), terrainat(d0.x, d0.y)
  console:log(string.format("seeding 0x%08X (reads 0x%08X in the fixture)",
    addr, rng_read(addr)))
  local co_in_fixture = coid(co_player)
  if co then
    coid(co_player, co)
    if coid(co_player) ~= co then
      console:error("CO write did not take"); return
    end
    console:log(string.format("P%d CO: fixture had %d, writing %d each case",
      co_player, co_in_fixture, co))
    -- MEASURED, and it is a trap: at a target-select fixture this write does
    -- not reach the damage path. Max (150/100 on Tank) written here produced
    -- 60-67 on Tank -> Infantry in woods, identical to Andy, where his own
    -- modifiers demand 90-97. The byte takes, and the intel screen agrees --
    -- that is how the twelve records were named -- but combat has already
    -- resolved its CO by the time Fire is chosen.
    --
    -- Left in because it still swaps the identity for anything that reads
    -- +0x1D live. Do not use it to measure a CO's effect on damage: it will
    -- return "no difference" for every CO, including ones whose effect is not
    -- in doubt. Build the fixture with the CO chosen in VS setup instead.
    console:log("  !! WARNING: a written CO does NOT change damage from a")
    console:log("  !! target-select fixture -- verified with Max, who should")
    console:log("  !! move the band and does not. See engine/co.py.")
  else
    console:log(string.format("P%d CO: %d (from the fixture, not written)",
      co_player, co_in_fixture))
  end

  -- One case, from a clean reload. Everything below goes through this, so a
  -- control cannot accidentally take a different path from a real case.
  -- `writes` maps slot -> internal hp, or is nil to write no hp at all.
  local function runcase(seed_v, writes)
    if not loadfixture(fixture) then return nil end
    if seed_v then
      rng_write(seed_v, addr)
      if rng_read(addr) ~= seed_v then
        console:error(string.format("seed write did not take: wrote 0x%08X "
          .. "read 0x%08X", seed_v, rng_read(addr)))
        return nil
      end
    end
    if co then coid(co_player, co) end       -- every case, after every reload
    if writes then
      for slot, v in pairs(writes) do
        if unithp(slot, v) == nil then return nil end
      end
    end
    local d, a = readunit(def_slot), readunit(att_slot)
    local dmg, _, died, counter, att_ammo, att_died =
      confirm_and_read(def_slot, d.hp, att_slot, a.hp)
    if dmg == nil then return nil end
    -- Where the attacker was BEFORE the exchange, and where it ends up.
    --
    -- A fixture sits at target-select, after the move has been chosen and
    -- before it is confirmed. If the unit record still holds the tile the unit
    -- STARTED on rather than the one it will fire from, then the attacker
    -- terrain this harness reports is the wrong tile -- and the counterattack
    -- lands on a tile nobody recorded. That is checkable rather than
    -- arguable: read the position again once the dust settles.
    local a2 = readunit(att_slot)
    local t0 = terrainat(a.x, a.y)
    local t1 = att_died and t0 or terrainat(a2.x, a2.y)
    return { damage = dmg, destroyed = died, counter = counter,
             att_ammo = att_ammo, att_died = att_died,
             def_hp = d.hp, att_hp = a.hp,
             att_x0 = a.x, att_y0 = a.y, att_t0 = t0,
             att_x1 = a2.x, att_y1 = a2.y, att_t1 = t1,
             moved = (a.x ~= a2.x or a.y ~= a2.y) and not att_died }
  end

  -- THE CONTROLS. The rule is that every sweep ships one, because a machine
  -- agreeing with itself looks exactly like a measurement. Three, and it is the
  -- last pair that carries the argument:
  --
  --   none      load, write nothing. The fixture's own answer, for the record.
  --   seed      load, write the seed only.
  --   identity  load, write the seed AND write each unit's CURRENT hp back.
  --
  -- `seed` and `identity` differ by exactly one thing: identity performed the
  -- write we are about to trust, with a value that changes nothing. They must
  -- agree. If they do not, the ACT of writing perturbs the result and every row
  -- below is an artifact -- which is the failure this cannot be allowed to miss,
  -- because it would look like a clean measurement in every other respect.
  local ctrl_seed = 0
  local identity = { [def_slot] = d0.hp, [att_slot] = a0.hp }
  local c_none = runcase(nil, nil)
  local c_seed = runcase(ctrl_seed, nil)
  local c_ident = runcase(ctrl_seed, identity)
  if not (c_none and c_seed and c_ident) then
    console:error("a control case produced no damage; aborting before the sweep")
    return
  end
  console:log(string.format("control  none: damage %d, counter %d  (unwritten "
    .. "fixture, for the record)", c_none.damage, c_none.counter))
  console:log(string.format("control  seed: damage %d, counter %d",
    c_seed.damage, c_seed.counter))
  console:log(string.format("control ident: damage %d, counter %d  (same seed, "
    .. "hp written to the value it already held)", c_ident.damage, c_ident.counter))
  if c_seed.damage ~= c_ident.damage or c_seed.counter ~= c_ident.counter then
    console:error("CONTROL FAILED: writing hp changed the result even when the "
      .. "value written was the one already there. The write is not transparent "
      .. "on this path, so nothing measured with it means anything. Aborting.")
    return
  end
  console:log("control passed: an hp write of the existing value changes nothing")

  -- Did the attacker's record move when the attack was confirmed? If so, the
  -- terrain read from the fixture is the tile it came FROM, not the one it
  -- fought from -- and every attacker-terrain figure this file reports, past
  -- and present, is attributed to the wrong tile.
  if c_seed.moved then
    console:error(string.format(
      "ATTACKER TERRAIN IS THE PRE-MOVE TILE. The record read (%d,%d) terrain "
      .. "%d before confirming and (%d,%d) terrain %d after. The counterattack "
      .. "lands on terrain %d, not %d. Header values are the pre-move tile; "
      .. "use attacker_terrain_after.",
      c_seed.att_x0, c_seed.att_y0, c_seed.att_t0,
      c_seed.att_x1, c_seed.att_y1, c_seed.att_t1,
      c_seed.att_t1, c_seed.att_t0))
  elseif c_seed.att_t0 ~= c_seed.att_t1 then
    console:error(string.format(
      "attacker terrain changed from %d to %d without the unit moving -- that "
      .. "should not be possible; do not trust either value",
      c_seed.att_t0, c_seed.att_t1))
  else
    console:log(string.format(
      "attacker stands on terrain %d before and after confirming (%d,%d)",
      c_seed.att_t0, c_seed.att_x0, c_seed.att_y0))
  end

  local writes = nil
  if def_hp or att_hp then
    writes = {}
    if def_hp then writes[def_slot] = def_hp end
    if att_hp then writes[att_slot] = att_hp end
    console:log(string.format("writing hp each case -- defender %s, attacker %s",
      def_hp and tostring(def_hp) or "unchanged",
      att_hp and tostring(att_hp) or "unchanged"))
  end

  local rows, seen, cseen = {}, {}, {}
  local obs_att_hp, obs_def_hp = a0.hp, d0.hp
  for i = 0, nseeds - 1 do
    local seed = (i * stride) % 0x100000000
    local r = runcase(seed, writes)
    if r == nil then
      console:error("case seed=" .. seed .. " produced no damage; aborting"); return
    end
    obs_att_hp, obs_def_hp = r.att_hp, r.def_hp
    rows[#rows + 1] = string.format(
      '    {"seed": %d, "damage": %d, "destroyed": %s, "attacker_hp_before": %d,'
      .. ' "counter": %d, "attacker_ammo_after": %d, "attacker_destroyed": %s,'
      .. ' "attacker_terrain_before": %d, "attacker_terrain_after": %d,'
      .. ' "attacker_moved_on_confirm": %s}',
      seed, r.damage, r.destroyed and "true" or "false", r.att_hp, r.counter,
      r.att_ammo, r.att_died and "true" or "false",
      r.att_t0, r.att_t1, r.moved and "true" or "false")
    if not r.destroyed then seen[r.damage] = (seen[r.damage] or 0) + 1 end
    if not r.att_died then cseen[r.counter] = (cseen[r.counter] or 0) + 1 end
  end

  local vals = {}
  for v in pairs(seen) do vals[#vals + 1] = v end
  table.sort(vals)
  local hist = {}
  for _, v in ipairs(vals) do hist[#hist + 1] = v .. "x" .. seen[v] end
  console:log("damage values seen: " .. table.concat(hist, "  "))
  if #vals <= 1 then
    console:error("every seed gave the same damage -- 0x" ..
      string.format("%08X", addr) .. " is not the luck source, or the write is "
      .. "overwritten before the roll. Do NOT read anything into the value.")
  else
    console:log(string.format("range %d..%d across %d random-ish seeds; the "
      .. "seeds are spread over the whole 32-bit space, so this is not the "
      .. "narrow orbit a frame-delay sweep walks", vals[1], vals[#vals], nseeds))
  end

  -- The counterattack histogram. This is the whole point of reading the
  -- attacker: if the opening damage varies across seeds and the counter does
  -- NOT, the counter carries no luck roll -- which is what the ROM says at
  -- 0x080234DA and what engine/damage.py's counterattack() contradicts.
  local cvals = {}
  for v in pairs(cseen) do cvals[#cvals + 1] = v end
  table.sort(cvals)
  local chist = {}
  for _, v in ipairs(cvals) do chist[#chist + 1] = v .. "x" .. cseen[v] end
  if #cvals == 0 then
    console:log("counter: none observed (attacker never lost HP)")
  else
    console:log("counter values seen: " .. table.concat(chist, "  "))
    if #cvals == 1 and #vals > 1 then
      console:log("*** the opening damage varied and the counter did NOT. "
        .. "A luck-carrying counter cannot do that. ***")
    end
  end

  local out = {
    "{",
    '  "mode": "seed",',
    string.format('  "rng_state_addr": %d, "rng_mul": %d, "rng_add": %d,',
      addr, RNG_MUL, RNG_ADD),
    string.format('  "co_written": %s, "co_player": %d, "co_in_fixture": %d,',
      co and tostring(co) or "null", co_player, co_in_fixture),
    string.format('  "attacker_slot": %d, "defender_slot": %d,', att_slot, def_slot),
    -- The HP the cases actually ran at, read back AFTER the write. Reporting
    -- a0/d0 here would stamp the fixture's values onto every row of a sweep
    -- that deliberately ran at something else, and no consumer could tell.
    string.format('  "attacker_type": %d, "attacker_hp": %d, "attacker_ammo": %d,',
      a0.type, obs_att_hp, a0.ammo),
    string.format('  "defender_type": %d, "defender_hp": %d,', d0.type, obs_def_hp),
    string.format('  "hp_written": {"attacker": %s, "defender": %s},',
      att_hp and tostring(att_hp) or "null",
      def_hp and tostring(def_hp) or "null"),
    string.format('  "hp_in_fixture": {"attacker": %d, "defender": %d},',
      a0.hp, d0.hp),
    string.format('  "controls": {"none": {"damage": %d, "counter": %d},'
      .. ' "seed": {"damage": %d, "counter": %d},'
      .. ' "identity": {"damage": %d, "counter": %d}, "passed": true},',
      c_none.damage, c_none.counter, c_seed.damage, c_seed.counter,
      c_ident.damage, c_ident.counter),
    -- attacker_terrain is the tile read FROM THE FIXTURE, which is the tile the
    -- unit came from if the record has not yet been updated for the pending
    -- move. attacker_terrain_after is where it actually fought. Consumers that
    -- care about the counterattack want the second one.
    string.format('  "attacker_terrain": %d, "defender_terrain": %d,',
      att_terr, def_terr),
    string.format('  "attacker_terrain_after": %d, "attacker_moved_on_confirm": %s,',
      c_seed.att_t1, c_seed.moved and "true" or "false"),
    '  "cases": [',
    table.concat(rows, ",\n"),
    "  ]",
    "}",
  }
  local f = io.open(outpath, "w")
  if not f then console:error("could not open " .. outpath); return end
  f:write(table.concat(out, "\n"))
  f:close()
  console:log(string.format("wrote %s: %d seeded cases", outpath, nseeds))
end

-- ---------------------------------------------------------------------------
-- BISECTING FOR THE LUCK SOURCE
--
-- The library RNG at 0x03000750 is not what combat uses: writing it changes
-- nothing, and it has only three callers, all far from the combat path. But
-- idling k frames before confirming DOES change the damage, so some byte that
-- differs between two such states determines the roll.
--
-- So stop reading and start bisecting. Take two delays that give different
-- damage, snapshot RAM at the moment of confirmation for each, and diff them.
-- The luck source is somewhere in that diff. Then binary search: copy half of
-- B's differing bytes into A's state, attack, and see which damage comes out.
-- ~log2(n) attacks instead of a search over 288KB.
--
-- This blends two states the game itself produced rather than writing garbage,
-- so it is far less likely to wedge the emulator than corrupting a region.
--
--   rng_bisect("C:/tmp/atk.ss1", ATT, DEF, 0, 6)
-- ---------------------------------------------------------------------------

local REGIONS = {
  { base = 0x02000000, len = 0x40000 },
  { base = 0x03000000, len = 0x08000 },
}

local function snapshot()
  local s = {}
  for i, r in ipairs(REGIONS) do s[i] = emu:readRange(r.base, r.len) end
  return s
end

-- Run to the confirmation point and grab RAM, without attacking.
local function state_at(fixture, k)
  if not loadfixture(fixture) then return nil end
  for _ = 1, k do emu:runFrame() end
  return snapshot()
end

local function damage_at(fixture, k, def_slot, patch)
  if not loadfixture(fixture) then return nil end
  for _ = 1, k do emu:runFrame() end
  if patch then
    for _, w in ipairs(patch) do emu:write8(w[1], w[2]) end
  end
  local d = readunit(def_slot)
  local dmg = confirm_and_read(def_slot, d.hp)
  return dmg
end

function rng_bisect(fixture, att_slot, def_slot, ka, kb)
  ka, kb = ka or 0, kb or 6
  local da = damage_at(fixture, ka, def_slot)
  local db = damage_at(fixture, kb, def_slot)
  if da == nil or db == nil then
    console:error("one of the delays produced no damage"); return
  end
  console:log(string.format("k=%d -> %d damage, k=%d -> %d damage", ka, da, kb, db))
  if da == db then
    console:error("both delays give the same damage; pick two that differ "
      .. "(check your dmg_sweep output for a pair)")
    return
  end

  local A, B = state_at(fixture, ka), state_at(fixture, kb)
  if not (A and B) then return end
  local diff = {}
  for i, r in ipairs(REGIONS) do
    local a, b = A[i], B[i]
    for j = 1, r.len do
      local x, y = string.byte(a, j), string.byte(b, j)
      if x ~= y then diff[#diff + 1] = { r.base + j - 1, y } end
    end
  end
  console:log(string.format("%d byte(s) differ between the two states", #diff))
  if #diff == 0 then
    console:error("no difference, yet the damage differs -- the roll must "
      .. "depend on something outside EWRAM/IWRAM, such as a hardware "
      .. "register or the CPU state at the moment of the call")
    return
  end

  -- Confirm the whole diff reproduces B's damage before bisecting into it.
  local whole = damage_at(fixture, ka, def_slot, diff)
  console:log(string.format("patching all %d byte(s) into the k=%d state gives %s",
    #diff, ka, tostring(whole)))
  if whole ~= db then
    console:error("patching the full diff does NOT reproduce the other damage. "
      .. "The roll depends on something not captured in these two snapshots -- "
      .. "bisection cannot proceed, and that is itself the finding.")
    return
  end

  local lo, hi = 1, #diff
  while lo < hi do
    local mid = math.floor((lo + hi) / 2)
    local first = {}
    for i = lo, mid do first[#first + 1] = diff[i] end
    local got = damage_at(fixture, ka, def_slot, first)
    if got == db then
      hi = mid
      console:log(string.format("  [%d..%d] flips it (%d byte(s))", lo, mid, mid - lo + 1))
    else
      local second = {}
      for i = mid + 1, hi do second[#second + 1] = diff[i] end
      local got2 = damage_at(fixture, ka, def_slot, second)
      if got2 == db then
        lo = mid + 1
        console:log(string.format("  [%d..%d] flips it (%d byte(s))",
          mid + 1, hi, hi - mid))
      else
        console:log(string.format("  neither half of [%d..%d] flips it alone -- "
          .. "the roll depends on several bytes together; stopping here",
          lo, hi))
        break
      end
    end
  end

  console:log("candidate byte(s):")
  for i = lo, math.min(hi, lo + 15) do
    console:log(string.format("  0x%08X  %d -> %d", diff[i][1],
      emu:read8(diff[i][1]), diff[i][2]))
  end
  return diff[lo] and diff[lo][1]
end

console:log("damage sweep loaded.  dmg_save(path)  dmg_probe(fix, att, def)")
console:log("  dmg_sweep(fix, att, def, out, kmax)        -- vary frame delay")
console:log("  dmg_seedsweep(fix, att, def, out, nseeds)  -- set the RNG seed")

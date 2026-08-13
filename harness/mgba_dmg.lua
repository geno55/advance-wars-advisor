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

-- Press A and wait for the defender's HP to move, or for it to die.
-- Returns damage, frames_waited, died.
local function confirm_and_read(def_slot, hp_before)
  emu:setKeys(KEY_A)
  for _ = 1, 4 do emu:runFrame() end
  emu:setKeys(0)
  for f = 1, DMG_TIMEOUT_FRAMES do
    emu:runFrame()
    local d = readunit(def_slot)
    if d.type == 0 then
      return hp_before, f, true                 -- destroyed: took all its HP
    end
    if d.hp ~= hp_before then
      -- let the exchange finish; a counterattack changes the ATTACKER's hp,
      -- not the defender's, so the first change to settle is the real one
      for _ = 1, 30 do emu:runFrame() end
      local after = readunit(def_slot)
      if after.type == 0 then return hp_before, f, true end
      return hp_before - after.hp, f, false
    end
  end
  return nil, DMG_TIMEOUT_FRAMES, false         -- nothing happened
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

  local dmg, frames, died = confirm_and_read(def_slot, d.hp)
  if dmg == nil then
    console:error("no damage after " .. DMG_TIMEOUT_FRAMES .. " frames. The "
      .. "fixture is probably not sitting on a confirmable attack -- it should "
      .. "be at target select, with the cursor on the defender, before A.")
    return
  end
  console:log(string.format("damage %d after %d frames%s", dmg, frames,
    died and "  (DESTROYED -- damage is a lower bound, use a healthier defender)"
    or ""))
  return dmg
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
function dmg_seedsweep(fixture, att_slot, def_slot, outpath, nseeds, stride, addr)
  if not (io and io.open) then
    console:error("no io library; cannot write " .. tostring(outpath)); return
  end
  nseeds = nseeds or 64
  stride = stride or 2654435761        -- a big odd step, to spread the seeds
  addr = addr or RNG_STATE
  if not loadfixture(fixture) then return end
  local a0, d0 = readunit(att_slot), readunit(def_slot)
  local att_terr, def_terr = terrainat(a0.x, a0.y), terrainat(d0.x, d0.y)
  console:log(string.format("seeding 0x%08X (reads 0x%08X in the fixture)",
    addr, rng_read(addr)))

  local rows, seen = {}, {}
  for i = 0, nseeds - 1 do
    if not loadfixture(fixture) then return end
    local seed = (i * stride) % 0x100000000
    rng_write(seed, addr)
    if rng_read(addr) ~= seed then
      console:error(string.format("seed write did not take: wrote 0x%08X read 0x%08X",
        seed, rng_read(addr)))
      return
    end
    local d = readunit(def_slot)
    local dmg, _, died = confirm_and_read(def_slot, d.hp)
    if dmg == nil then
      console:error("case seed=" .. seed .. " produced no damage; aborting"); return
    end
    rows[#rows + 1] = string.format(
      '    {"seed": %d, "damage": %d, "destroyed": %s}', seed, dmg,
      died and "true" or "false")
    if not died then seen[dmg] = (seen[dmg] or 0) + 1 end
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

  local out = {
    "{",
    '  "mode": "seed",',
    string.format('  "rng_state_addr": %d, "rng_mul": %d, "rng_add": %d,',
      addr, RNG_MUL, RNG_ADD),
    string.format('  "attacker_slot": %d, "defender_slot": %d,', att_slot, def_slot),
    string.format('  "attacker_type": %d, "attacker_hp": %d, "attacker_ammo": %d,',
      a0.type, a0.hp, a0.ammo),
    string.format('  "defender_type": %d, "defender_hp": %d,', d0.type, d0.hp),
    string.format('  "attacker_terrain": %d, "defender_terrain": %d,',
      att_terr, def_terr),
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

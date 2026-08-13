-- SPIKE: can we generate test cases by WRITING state instead of playing to it?
--
-- Everything in this project has so far cost a human one emulator session per
-- observation. That is the reason there are 14 damage observations and not
-- 14,000. mGBA exposes loadStateFile, write8/16/32, setKeys and runFrame, so in
-- principle a case is: restore a fixture, overwrite some bytes, press A, read
-- the result. This script exists to find out whether that actually reproduces
-- what playing to the same state would produce, because if it does not, every
-- number it generates is measuring the harness rather than the game.
--
-- THE TRICK THAT AVOIDS NEEDING THE CURSOR. We do not know where the cursor
-- position is stored, and finding it would be its own RAM hunt. So we never
-- move the cursor: the fixture is saved with it already resting on one of your
-- units, and we overwrite THAT unit's type, fuel and ammo in place. Position is
-- untouched, so the selection stays valid and pressing A selects whatever we
-- just wrote. That is enough to sweep every movement type and fuel level.
--
-- USAGE
--   1. Start your turn. Put the cursor on one of your own units. Do NOT select.
--   2. spike_save("C:/tmp/fix.ss1")   -- writes the fixture AND lists every
--                                        unit with its slot, so you can pick
--                                        the one the cursor is resting on.
--   3. spike_probe("C:/tmp/fix.ss1", SLOT)        -- one case, prove it works
--   4. spike_sweep("C:/tmp/fix.ss1", SLOT, "C:/tmp/spike.json")
--
-- then compare with tools/spike_check.py.
--
-- Use spike_save rather than mGBA's own save-state keys: those write to
-- <rom>.ss1 next to the ROM, not to the path you hand this script.

UNIT_BASE_PTR = UNIT_BASE_PTR or 0x08282CB8
MAP_ADDR = MAP_ADDR or 0x02016C2A
MAP_DIMS = MAP_DIMS or 0x030036E0
DIM_TABLE = DIM_TABLE or 0x03003600
UNIT_STRIDE = UNIT_STRIDE or 12
ARMY_SLOTS = ARMY_SLOTS or 64

KEY_A = 0x001

-- 1-based RAM type ids, i.e. damage-table row + 1. The six vestigial slots are
-- absent because no unit has them.
SPIKE_TYPES = {
  {1, "Infantry"}, {2, "Mech"}, {3, "MdTank"}, {5, "Tank"}, {6, "Recon"},
  {7, "APC"}, {10, "Artillery"}, {11, "Rockets"}, {14, "AntiAir"},
  {15, "Missiles"}, {16, "Fighter"}, {17, "Bomber"}, {19, "BCopter"},
  {20, "TCopter"}, {21, "Battleship"}, {22, "Cruiser"}, {23, "Lander"},
  {24, "Sub"},
}

local function fileexists(path)
  if not (io and io.open) then return nil end       -- cannot tell
  local f = io.open(path, "rb")
  if f then f:close(); return true end
  return false
end

-- Load a fixture, distinguishing "no such file" from "mGBA refused it". Those
-- need completely different fixes and the raw false tells you neither.
local function loadfixture(path)
  if fileexists(path) == false then
    console:error("no such file: " .. tostring(path))
    console:error("  mGBA's own save states go next to the ROM as <rom>.ss1, "
      .. "not where you point this script.")
    console:error("  Get into position in game, then: spike_save(\"" ..
      tostring(path) .. "\")")
    return false
  end
  if not emu:loadStateFile(path) then
    console:error("mGBA refused to load " .. tostring(path)
      .. " -- the file exists but is not a save state for this ROM")
    return false
  end
  emu:runFrame()
  return true
end

-- Make the fixture from wherever you are right now. Beats hunting for where
-- the GUI put slot 1, and guarantees the path the sweep will use is the path
-- that was written.
function spike_save(path)
  if not emu.saveStateFile then
    console:error("this mGBA build has no emu:saveStateFile")
    return
  end
  if not emu:saveStateFile(path) then
    console:error("could not write " .. tostring(path)
      .. " -- does the directory exist?")
    return
  end
  console:log("saved fixture to " .. path)
  local u = nil
  local ubase = emu:read32(UNIT_BASE_PTR)
  for i = 0, 255 do
    local a = ubase + i * UNIT_STRIDE
    if emu:read8(a) >= 1 and emu:read8(a) <= 24 then
      u = u or {}
      u[#u + 1] = string.format("slot %d: type %d at (%d,%d)", i,
        emu:read8(a), emu:read8(a + 2), emu:read8(a + 3))
    end
  end
  if u then
    console:log("units in this state (pick the slot the cursor is resting on):")
    for _, line in ipairs(u) do console:log("  " .. line) end
  end
end

local function unitaddr(slot)
  return emu:read32(UNIT_BASE_PTR) + slot * UNIT_STRIDE
end

local function dims()
  return emu:read8(MAP_DIMS), emu:read8(MAP_DIMS + 1)
end

-- Hold a key for `hold` frames, release, then let the game settle for `wait`.
-- Menus need the release edge, so never leave a key held.
local function press(mask, hold, wait)
  emu:setKeys(mask)
  for _ = 1, (hold or 4) do emu:runFrame() end
  emu:setKeys(0)
  for _ = 1, (wait or 20) do emu:runFrame() end
end

-- Read the game's movement flood fill. 255 = unreachable.
local function readgrid(w, h)
  local g = {}
  for y = 0, h - 1 do
    local rowp = emu:read32(DIM_TABLE + y * 4)
    local row = {}
    for x = 0, w - 1 do row[#row + 1] = emu:read8(rowp + x) end
    g[#g + 1] = row
  end
  return g
end

local function gridcount(g)
  local n = 0
  for _, row in ipairs(g) do
    for _, v in ipairs(row) do if v ~= 255 then n = n + 1 end end
  end
  return n
end

-- Overwrite a unit in place. Position is deliberately NOT touched.
local function writeunit(slot, typeid, fuel, ammo, hp)
  local a = unitaddr(slot)
  emu:write8(a, typeid)
  emu:write16(a + 4, (hp or 100) + (ammo or 0) * 128)
  emu:write8(a + 6, fuel or 99)
end

local function readunit(slot)
  local a = unitaddr(slot)
  local v = emu:read16(a + 4)
  return {
    type = emu:read8(a),
    x = emu:read8(a + 2), y = emu:read8(a + 3),
    hp = v % 128, ammo = math.floor(v / 128) % 16,
    fuel = emu:read8(a + 6) % 128,
  }
end

-- One case, loudly. Run this before sweeping anything.
function spike_probe(fixture, slot, typeid, fuel)
  if not emu.loadStateFile then
    console:error("this mGBA build has no emu:loadStateFile; the spike cannot run")
    return
  end
  if not loadfixture(fixture) then return end

  local before = readunit(slot)
  console:log(string.format("fixture: slot %d holds type %d at (%d,%d) fuel %d",
    slot, before.type, before.x, before.y, before.fuel))
  if before.type == 0 then
    console:error("that slot is empty -- wrong slot, or the fixture is not "
      .. "what you think it is")
    return
  end

  if typeid then
    writeunit(slot, typeid, fuel or 99, 9, 100)
    local after = readunit(slot)
    if after.type ~= typeid then
      console:error("the write did not take: wrote " .. typeid .. ", read back "
        .. after.type)
      return
    end
    console:log(string.format("wrote type %d fuel %d, position unchanged (%d,%d)",
      after.type, after.fuel, after.x, after.y))
  end

  local w, h = dims()
  local blank = gridcount(readgrid(w, h))
  press(KEY_A)
  local g = readgrid(w, h)
  local n = gridcount(g)

  console:log(string.format("map %dx%d | reachable tiles before A: %d, after: %d",
    w, h, blank, n))
  if n == 0 then
    console:error("no range appeared. The cursor is probably not on a "
      .. "selectable unit in this fixture, or A did not register.")
  elseif n == blank then
    console:error("the grid did not change. Selection may not have happened; "
      .. "try a longer wait, or check the fixture is mid-turn.")
  else
    console:log("OK -- the game recomputed a movement range for a unit we wrote.")
  end
  return n
end

-- Sweep every unit type through the same tile and dump the results as JSON.
function spike_sweep(fixture, slot, outpath, fuel)
  if not (io and io.open) then
    console:error("no io library; cannot write " .. tostring(outpath))
    return
  end
  local out, cases = {}, {}
  local w, h

  -- CONTROL: load the fixture and press A having written NOTHING. This is the
  -- whole point of the spike. Restoring a save state and pressing A is simply
  -- playing; if the written case of the same unit type produces a different
  -- grid, then writing RAM is not transparent and every swept number is
  -- measuring the harness. Without this the sweep can only show that written
  -- state agrees with our model, which they could both do while being wrong.
  if not loadfixture(fixture) then return end
  do
    local u = readunit(slot)
    w, h = dims()
    press(KEY_A)
    local g = readgrid(w, h)
    local rows = {}
    for y = 1, h do rows[#rows + 1] = "[" .. table.concat(g[y], ",") .. "]" end
    cases[#cases + 1] = string.format(
      '  {"control": true, "type_id": %d, "type": "control", "x": %d, "y": %d, '
      .. '"fuel": %d, "hp": %d, "ammo": %d, "reachable": %d, "grid": [%s]}',
      u.type, u.x, u.y, u.fuel, u.hp, u.ammo, gridcount(g),
      table.concat(rows, ","))
    console:log(string.format("  %-11s %d tile(s)  (type %d, untouched)",
      "CONTROL", gridcount(g), u.type))
  end

  for _, t in ipairs(SPIKE_TYPES) do
    local typeid, name = t[1], t[2]
    if not loadfixture(fixture) then return end
    writeunit(slot, typeid, fuel or 99, 9, 100)
    local u = readunit(slot)
    if u.type ~= typeid then
      console:error("write failed for " .. name); return
    end
    w, h = dims()
    press(KEY_A)
    local g = readgrid(w, h)
    local rows = {}
    for y = 1, h do rows[#rows + 1] = "[" .. table.concat(g[y], ",") .. "]" end
    cases[#cases + 1] = string.format(
      '  {"type_id": %d, "type": "%s", "x": %d, "y": %d, "fuel": %d, '
      .. '"hp": %d, "ammo": %d, "reachable": %d, "grid": [%s]}',
      typeid, name, u.x, u.y, u.fuel, u.hp, u.ammo, gridcount(g),
      table.concat(rows, ","))
    console:log(string.format("  %-11s %d tile(s)", name, gridcount(g)))
  end

  -- board context, so the checker can rebuild the same Board we measured
  if not loadfixture(fixture) then return end
  local terr = {}
  for y = 0, h - 1 do
    local t, o = {}, {}
    for x = 0, w - 1 do
      local v = emu:read8(MAP_ADDR + y * w + x)
      t[#t + 1] = v % 32
      o[#o + 1] = math.floor(v / 32)
    end
    terr[#terr + 1] = string.format('    {"y": %d, "t": [%s], "owner": [%s]}',
      y, table.concat(t, ","), table.concat(o, ","))
  end
  local units = {}
  local ubase = emu:read32(UNIT_BASE_PTR)
  for i = 0, 255 do
    local a = ubase + i * UNIT_STRIDE
    local ty = emu:read8(a)
    if ty >= 1 and ty <= 24 then
      local v = emu:read16(a + 4)
      units[#units + 1] = string.format(
        '    {"slot": %d, "player": %d, "type_id": %d, "x": %d, "y": %d, '
        .. '"hp": %d, "ammo": %d, "fuel": %d}',
        i, math.floor(i / ARMY_SLOTS) + 1, ty, emu:read8(a + 2), emu:read8(a + 3),
        v % 128, math.floor(v / 128) % 16, emu:read8(a + 6) % 128)
    end
  end

  out[#out + 1] = "{"
  out[#out + 1] = string.format('  "width": %d, "height": %d, "slot": %d,',
    w, h, slot)
  out[#out + 1] = '  "terrain": [\n' .. table.concat(terr, ",\n") .. "\n  ],"
  out[#out + 1] = '  "fixture_units": [\n' .. table.concat(units, ",\n") .. "\n  ],"
  out[#out + 1] = '  "cases": [\n' .. table.concat(cases, ",\n") .. "\n  ]"
  out[#out + 1] = "}"

  local f = io.open(outpath, "w")
  if not f then console:error("could not open " .. outpath); return end
  f:write(table.concat(out, "\n"))
  f:close()
  console:log(string.format("wrote %s: %d cases", outpath, #cases))
end

console:log("spike loaded.  spike_probe(fixture, slot)  then  "
  .. "spike_sweep(fixture, slot, out)")

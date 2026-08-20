-- CAPTURE: settle what is left of ASSUMPTIONS A15 -- who may capture, and
-- what moving does to progress. The arithmetic itself (rate, clamp, fall) is
-- already READ off the ROM at 0x08026180-0x080262E4 and is not what this
-- measures; it comes along for free as a cross-check on every row.
--
-- Same trick as mgba_spike.lua: never touch the cursor. The fixture is saved
-- with the cursor already resting on the unit, the unit's record is rewritten
-- in place, and the menu is driven blind with A presses. Pressing A selects
-- the unit, A again confirms its own tile as the destination, and A a third
-- time takes the TOP menu item -- which for a foot unit on a hostile property
-- is Capture, and for anything that cannot capture is Wait. So "did the
-- progress byte move" is the answer to "was Capture on the menu", and the
-- acted flag tells a no from a sequence that never executed.
--
-- FIXTURE REQUIREMENTS (cap_save prints a checklist):
--   * the cursor rests on YOUR unit standing ON a neutral city
--   * no enemy unit adjacent (Fire would take the top menu slot)
--   * no friendly transport adjacent and nothing loadable nearby
--   * for cap_move_probe: a SECOND neutral city directly RIGHT of the unit
--
-- USAGE (Tools -> Scripting, load this file):
--   cap_save("C:/tmp/cap.ss1")
--   cap_probe("C:/tmp/cap.ss1", SLOT)             -- control: prove the rig
--   cap_menu_sweep("C:/tmp/cap.ss1", SLOT, "C:/tmp/cap_menu.json")
--   cap_rate("C:/tmp/cap.ss1", SLOT, "C:/tmp/cap_rate.json")
--   cap_move_probe("C:/tmp/cap2.ss1", SLOT, "C:/tmp/cap_move.json")
-- then score with tools/capture_check.py.

UNIT_BASE_PTR = UNIT_BASE_PTR or 0x08282CB8
MAP_ADDR = MAP_ADDR or 0x02016C2A
MAP_DIMS = MAP_DIMS or 0x030036E0
UNIT_STRIDE = UNIT_STRIDE or 12
ARMY_SLOTS = ARMY_SLOTS or 64

KEY_A, KEY_RIGHT = 0x001, 0x010
SETTLE_FRAMES = SETTLE_FRAMES or 240

local function fileexists(path)
  if not (io and io.open) then return nil end
  local f = io.open(path, "rb")
  if f then f:close(); return true end
  return false
end

local function loadfixture(path)
  if fileexists(path) == false then
    console:error("no such file: " .. tostring(path)
      .. "  -- get into position and run cap_save(\"" .. tostring(path) .. "\")")
    return false
  end
  if not emu:loadStateFile(path) then
    console:error("mGBA refused to load " .. tostring(path)); return false
  end
  emu:runFrame()
  return true
end

local function unitaddr(slot)
  return emu:read32(UNIT_BASE_PTR) + slot * UNIT_STRIDE
end

-- Record +4 packs hp (bits 0-6), ammo (7-10), capture progress (11-15).
local function readunit(slot)
  local a = unitaddr(slot)
  local v = emu:read16(a + 4)
  return {
    type = emu:read8(a), acted = emu:read8(a + 1) % 2,
    x = emu:read8(a + 2), y = emu:read8(a + 3),
    hp = v % 128, ammo = math.floor(v / 128) % 16,
    capture = math.floor(v / 2048),
    fuel = emu:read8(a + 6) % 128,
  }
end

local function terrainbyte(x, y)
  local w = emu:read8(MAP_DIMS)
  return emu:read8(MAP_ADDR + y * w + x)
end

-- type + 32*owner, same encoding the reader uses
local function terrainat(x, y) return terrainbyte(x, y) % 32 end
local function ownerat(x, y) return math.floor(terrainbyte(x, y) / 32) end

-- Write one packed field of +4, preserving its neighbours, and read it back --
-- the same discipline as mgba_dmg's unithp, for the same reason: a write that
-- silently did not take turns the sweep into the machine agreeing with itself.
local function writefield(slot, shift, width, v)
  local a = unitaddr(slot)
  local cur = emu:read16(a + 4)
  local unit = 2 ^ shift
  local span = 2 ^ width
  local old = math.floor(cur / unit) % span
  local new = cur + (v - old) * unit
  emu:write16(a + 4, new)
  local back = emu:read16(a + 4)
  if math.floor(back / unit) % span ~= v then
    console:error(string.format("field write did not take (shift %d): wrote %d",
      shift, v))
    return false
  end
  if back - math.floor(back / unit) % span * unit
      ~= cur - old * unit then
    console:error("field write disturbed its neighbours; aborting")
    return false
  end
  return true
end

function unitcapture(slot, v)
  if v == nil then return readunit(slot).capture end
  if v < 0 or v > 20 then
    console:error("capture progress is 0..20 in every live observation; "
      .. "refusing to write a board the game may never reach")
    return nil
  end
  if writefield(slot, 11, 5, v) then return v end
end

function unittype(slot, v)
  local a = unitaddr(slot)
  if v == nil then return emu:read8(a) end
  emu:write8(a, v)
  if emu:read8(a) ~= v then
    console:error("type write did not take"); return nil
  end
  return v
end

function unithp(slot, v)
  if v == nil then return readunit(slot).hp end
  if v < 1 or v > 100 then console:error("hp is 1..100"); return nil end
  if writefield(slot, 0, 7, v) then return v end
end

function cap_save(path)
  if not emu:saveStateFile(path) then
    console:error("could not write " .. tostring(path)); return
  end
  console:log("saved fixture to " .. path)
  console:log("checklist: cursor ON your unit; unit ON a neutral city;")
  console:log("nothing adjacent that adds menu items (enemies, transports).")
  console:log("units (pick the slot under the cursor):")
  local ubase = emu:read32(UNIT_BASE_PTR)
  for i = 0, 255 do
    local a = ubase + i * UNIT_STRIDE
    local t = emu:read8(a)
    if t >= 1 and t <= 24 then
      local u = readunit(i)
      console:log(string.format(
        "  slot %3d  P%d  type %2d at (%2d,%2d)  hp %3d  capture %2d  terrain %d owner %d",
        i, math.floor(i / ARMY_SLOTS) + 1, u.type, u.x, u.y, u.hp, u.capture,
        terrainat(u.x, u.y), ownerat(u.x, u.y)))
    end
  end
end

local function press(mask, hold, gap)
  emu:setKeys(mask)
  for _ = 1, (hold or 4) do emu:runFrame() end
  emu:setKeys(0)
  for _ = 1, (gap or 20) do emu:runFrame() end
end

-- One case: load, rewrite the unit, drive the menu, read what happened.
-- keys is a list of masks pressed in order. Returns a row table.
local function runcase(fixture, slot, writes, keys)
  if not loadfixture(fixture) then return nil end
  writes = writes or {}
  if writes.type and not unittype(slot, writes.type) then return nil end
  if writes.hp and not unithp(slot, writes.hp) then return nil end
  if writes.capture and not unitcapture(slot, writes.capture) then return nil end
  local before = readunit(slot)
  local t0 = terrainat(before.x, before.y)
  for _, k in ipairs(keys) do press(k) end
  for _ = 1, SETTLE_FRAMES do emu:runFrame() end
  local after = readunit(slot)
  return {
    type = before.type, hp = before.hp,
    capture_before = before.capture, capture_after = after.capture,
    acted = after.acted,
    x = after.x, y = after.y, terrain = terrainat(after.x, after.y),
    owner_after = ownerat(after.x, after.y),
    terrain_start = t0,
  }
end

local function writerows(outpath, name, rows, extra)
  local out = { "{", string.format('  "probe": "%s",', name) }
  for _, line in ipairs(extra or {}) do out[#out + 1] = line end
  out[#out + 1] = '  "cases": ['
  local body = {}
  for _, r in ipairs(rows) do
    body[#body + 1] = string.format(
      '    {"type": %d, "hp": %d, "capture_before": %d, "capture_after": %d,'
      .. ' "acted": %d, "x": %d, "y": %d, "terrain": %d, "owner_after": %d}',
      r.type, r.hp, r.capture_before, r.capture_after, r.acted,
      r.x, r.y, r.terrain, r.owner_after)
  end
  out[#out + 1] = table.concat(body, ",\n")
  out[#out + 1] = "  ]"
  out[#out + 1] = "}"
  local f = io.open(outpath, "w")
  if not f then console:error("could not open " .. outpath); return end
  f:write(table.concat(out, "\n"))
  f:close()
  console:log("wrote " .. outpath)
end

-- THE CONTROL. Unwritten fixture, A A A: the unit under the cursor (a real
-- foot unit the fixture was built with) must capture for exactly its bar
-- count. If this reads 0, the drive sequence is broken and nothing after it
-- means anything -- fix the fixture before sweeping.
function cap_probe(fixture, slot)
  local r = runcase(fixture, slot, nil, { KEY_A, KEY_A, KEY_A })
  if not r then return end
  console:log(string.format(
    "control: type %d hp %d -> capture %d -> %d, acted %d",
    r.type, r.hp, r.capture_before, r.capture_after, r.acted))
  local bars = math.ceil(r.hp / 10)
  if r.capture_after == math.min(20, r.capture_before + bars) then
    console:log("control PASSED: progress moved by the bar count")
  elseif r.acted == 0 then
    console:error("control FAILED: the unit never acted -- the A sequence "
      .. "did not reach the menu. Re-park the fixture.")
  else
    console:error(string.format("control FAILED: expected +%d, read %d -> %d",
      bars, r.capture_before, r.capture_after))
  end
end

-- A15a: who may capture. One row per unit type, everything else identical.
-- capture_after > 0 means Capture was on the menu and was the top item.
-- Rows where the written type could never legally stand on a city (naval)
-- still run; score them as suspect, not as data -- see capture_check.py.
function cap_menu_sweep(fixture, slot, outpath)
  local rows = {}
  local ctrl = runcase(fixture, slot, nil, { KEY_A, KEY_A, KEY_A })
  if not ctrl then return end
  for t = 1, 24 do
    -- skip the six vestigial ids; no unit carries them
    if not (t == 3 or t == 12 or t == 13 or t == 17 or t == 21 or t == 24) then
      local r = runcase(fixture, slot, { type = t }, { KEY_A, KEY_A, KEY_A })
      if not r then return end
      rows[#rows + 1] = r
      console:log(string.format("type %2d: capture %d -> %d  acted %d",
        t, r.capture_before, r.capture_after, r.acted))
    end
  end
  writerows(outpath, "menu", rows, {
    string.format('  "control": {"type": %d, "capture_after": %d, "acted": %d},',
      ctrl.type, ctrl.capture_after, ctrl.acted) })
end

-- Rate and threshold rows, as a live cross-check on the ROM read:
-- bars at four HPs, the clamp at 20, and the fall.
function cap_rate(fixture, slot, outpath)
  local rows = {}
  for _, c in ipairs({ { hp = 100 }, { hp = 70 }, { hp = 45 }, { hp = 9 },
                       { hp = 100, capture = 15 },   -- clamp: 15+10 stores 20
                       { hp = 100, capture = 10 },   -- exact fall at 20
                       { hp = 45, capture = 10 } }) do
    local r = runcase(fixture, slot, c, { KEY_A, KEY_A, KEY_A })
    if not r then return end
    rows[#rows + 1] = r
    console:log(string.format("hp %3d capture %2d -> %2d  owner_after %d",
      r.hp, r.capture_before, r.capture_after, r.owner_after))
  end
  writerows(outpath, "rate", rows)
end

-- A15c: what moving does. Needs the SECOND fixture: another neutral city
-- directly RIGHT of the unit. Two rows:
--   stay:  write progress 10, capture in place        -> 10+bars, or reset?
--   move:  write progress 10, move one tile right,
--          capture the OTHER city                     -> bars if moving
--                                                        resets, 10+bars if not
-- The stay row is the control for the written progress itself: if the game
-- discards written progress even when the unit never moved, the move row
-- cannot be read at all and the tool says so.
function cap_move_probe(fixture, slot, outpath)
  local stay = runcase(fixture, slot, { capture = 10 },
                       { KEY_A, KEY_A, KEY_A })
  if not stay then return end
  console:log(string.format("stay: capture 10 -> %d", stay.capture_after))
  local move = runcase(fixture, slot, { capture = 10 },
                       { KEY_A, KEY_RIGHT, KEY_A, KEY_A })
  if not move then return end
  console:log(string.format("move: capture 10 -> %d at (%d,%d) terrain %d",
    move.capture_after, move.x, move.y, move.terrain))
  writerows(outpath, "move", { stay, move })
end

-- CAPTURE PROBES, headless -- the runs that killed ASSUMPTIONS A15.
--
-- This drives Mesen2's GBA core (the Expanded build) rather than mGBA:
-- Mesen's --testrunner runs a Lua script against the ROM with no window and
-- no human, which is what let these probes run as code instead of as a
-- checklist. mGBA 0.10.5 has no script CLI; its GUI console route lives in
-- mgba_capture.lua and remains valid for a human at the keyboard.
--
--   Mesen.exe --testrunner --timeout=600 --     --debug.scriptWindow.allowIoOsAccess=true --     "Advance Wars (USA) (Rev 1).gba" harness/mesen_capture.lua
--
-- Needs: a real gba_bios.bin in Mesen's Firmware folder (the core has no HLE
-- fallback and AW1 calls BIOS Div constantly), and a savestate parked at the
-- player's turn with the probe unit one tile SOUTH of a neutral city and the
-- cursor resting on it. Set MSS and SLOT below.
--
-- Three things this file knows that took real debugging to learn:
--   * emu.loadSavestate() must run inside an EXEC memory callback on the main
--     CPU. The scheduler below resumes its coroutine from a callback on the
--     BIOS IRQ vector (0x18), gated by an endFrame flag to once per frame.
--   * The cursor is a byte pair at 0x030033F0/F1, found by pressing Right and
--     Down and diffing IWRAM. goto_tile() navigates closed-loop off it.
--   * Writing a unit record's x,y moves the RECORD but not the unit the game
--     lets you select -- there is a tile->unit index somewhere beyond the
--     record. Do not trust position writes; drive real moves instead.
--
-- Outputs capture_probes rows as JSON plus a screenshot per case. Two parts:
-- PART 1 sweeps all 18 types plus rate/threshold rows through one
-- move-and-capture, PART 2 plays the two-round stay probe with real turns.
-- Both ship controls: an unwritten case and an identity write must match.

-- ======================= PART 1: sweep + rates =======================
-- Set MODE to "sweep" or "stay" before running.
MODE = MODE or "sweep"
OUT_DIR = OUT_DIR or nil    -- output folder, trailing slash
MSS_PATH = MSS_PATH or nil  -- the parked savestate

if MODE == "sweep" then

local OUT = OUT_DIR or "C:/tmp/cap/"
local MSS = MSS_PATH or "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_1.mss"
local UNIT_PTR, MAP, DIMS = 0x08282CB8, 0x02016C2A, 0x030036E0
local SLOT, CITY_X, CITY_Y = 2, 4, 1

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end
local function w16(a, v) emu.write16(a, v, emu.memType.gbaMemory) end

local held = {}
emu.addEventCallback(function()
  pcall(function() emu.setInput(held, 0) end)
end, emu.eventType.inputPolled)

local function wait(n) for _ = 1, n do coroutine.yield() end end
local function tap(btn, hold, gap)
  held = { [btn] = true }; wait(hold or 6); held = {}; wait(gap or 30)
end

local state
local function fresh()
  emu.loadSavestate(state)
  wait(30)
end

local function ua() return r32(UNIT_PTR) + SLOT * 12 end

-- packed +4 field write with read-back, the harness discipline
local function setfield(shift, width, v)
  local a = ua() + 4
  local cur = r16(a)
  local unitv = 2 ^ shift
  local span = 2 ^ width
  local old = math.floor(cur / unitv) % span
  w16(a, cur + (v - old) * unitv)
  local back = r16(a)
  if math.floor(back / unitv) % span ~= v then return false end
  if back - math.floor(back / unitv) % span * unitv
     ~= cur - old * unitv then return false end
  return true
end

local function readrow()
  local a = ua()
  local v = r16(a + 4)
  local x, y = r8(a + 2), r8(a + 3)
  local w = r8(DIMS)
  local tb = r8(MAP + y * w + x)
  local cb = r8(MAP + CITY_Y * w + CITY_X)
  return {
    type = r8(a), x = x, y = y, hp = v % 128,
    cap = math.floor(v / 2048), acted = r8(a + 1) % 2,
    terrain = tb % 32, owner = math.floor(tb / 32),
    city_owner = math.floor(cb / 32),
  }
end

local rows = {}
local function runcase(c)
  fresh()
  local wrote_ok = true
  if c.type then
    w8(ua(), c.type)
    wrote_ok = wrote_ok and (r8(ua()) == c.type)
  end
  if c.hp then wrote_ok = wrote_ok and setfield(0, 7, c.hp) end
  if c.cap then wrote_ok = wrote_ok and setfield(11, 5, c.cap) end
  if c.pos then
    w8(ua() + 2, c.pos[1]); w8(ua() + 3, c.pos[2])
    wrote_ok = wrote_ok and r8(ua() + 2) == c.pos[1]
               and r8(ua() + 3) == c.pos[2]
  end
  if c.pos then tap("up") end
  tap("a")
  if not c.pos then tap("up") end
  tap("a", 6, 50)
  tap("a", 6, 60)
  wait(240)
  local r = readrow()
  r.name = c.name
  r.wrote_ok = wrote_ok
  rows[#rows + 1] = r
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "run4-" .. c.name .. ".png", "wb")
  fh:write(png); fh:close()
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  local cases = {
    { name = "ctrl" },
    { name = "ident", type = 1 },
    { name = "move_keep", cap = 15 },
    { name = "stay_keep", cap = 15, pos = { CITY_X, CITY_Y } },
    { name = "hp70", hp = 70 },
    { name = "hp45", hp = 45 },
    { name = "hp9", hp = 9 },
    { name = "fall", cap = 10 },
  }
  local types = { 1, 2, 3, 5, 6, 7, 10, 11, 14, 15, 16, 17, 19, 20, 21, 22, 23, 24 }
  for _, t in ipairs(types) do
    cases[#cases + 1] = { name = "t" .. t, type = t }
  end

  local log = io.open(OUT .. "run4.json", "w")
  log:write("[\n")
  for i, c in ipairs(cases) do
    runcase(c)
    local r = rows[#rows]
    log:write(string.format(
      '%s{"name":"%s","wrote_ok":%s,"type":%d,"x":%d,"y":%d,"hp":%d,'
      .. '"cap":%d,"acted":%d,"terrain":%d,"owner":%d,"city_owner":%d}',
      i > 1 and ",\n" or "", r.name, tostring(r.wrote_ok), r.type, r.x, r.y,
      r.hp, r.cap, r.acted, r.terrain, r.owner, r.city_owner))
    log:flush()
  end
  log:write("\n]\n"); log:close()
  emu.stop(0)
end

local co = coroutine.create(main)
local pending = false
emu.addEventCallback(function() pending = true end, emu.eventType.endFrame)
emu.addMemoryCallback(function()
  if not pending then return end
  pending = false
  if coroutine.status(co) ~= "dead" then
    local ok, err = coroutine.resume(co)
    if not ok then
      local ef = io.open(OUT .. "run4-err.log", "w"); ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

end

-- ======================= PART 2: the stay probe ======================
if MODE == "stay" then

local OUT = OUT_DIR or "C:/tmp/cap/"
local MSS = MSS_PATH or "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_1.mss"
local UNIT_PTR, MAP, DIMS = 0x08282CB8, 0x02016C2A, 0x030036E0
local CURX, CURY = 0x030033F0, 0x030033F1
local DAY, ACTIVE = 0x03004420, 0x03004422
local SLOT, CX, CY = 2, 4, 1

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local held = {}
emu.addEventCallback(function()
  pcall(function() emu.setInput(held, 0) end)
end, emu.eventType.inputPolled)
local function wait(n) for _ = 1, n do coroutine.yield() end end
local function tap(btn, hold, gap)
  held = { [btn] = true }; wait(hold or 6); held = {}; wait(gap or 24)
end
local log
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "run8-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end
local function cursor() return r8(CURX), r8(CURY) end
local function goto_tile(x, y)
  for _ = 1, 80 do
    local cx, cy = cursor()
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left")
    elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function unitrow(tag)
  local a = r32(UNIT_PTR) + SLOT * 12
  local v = r16(a + 4)
  local w = r8(DIMS)
  local cb = r8(MAP + CY * w + CX)
  log:write(string.format(
    "%s: unit(%d,%d) hp %d cap %d acted %d | city owner %d | day %d active %d cur(%d,%d)\n",
    tag, r8(a + 2), r8(a + 3), v % 128, math.floor(v / 2048), r8(a + 1) % 2,
    math.floor(cb / 32), r16(DAY), r8(ACTIVE), cursor()))
  log:flush()
end
local function settle()
  -- dismiss any banner / begin-turn screen: B, then wait
  wait(90); tap("b", 6, 40); wait(60)
end
local function end_turn(tag)
  local before = r8(ACTIVE)
  for attempt = 1, 2 do
    settle()
    local ok = goto_tile(4, 3)
    log:write(string.format("%s attempt %d: goto=%s active=%d\n",
      tag, attempt, tostring(ok), r8(ACTIVE)))
    tap("a", 6, 45)
    shot(tag .. "-menu" .. attempt)
    tap("down"); tap("down"); tap("down"); tap("down")
    tap("a", 6, 60)
    for _ = 1, 12 do                       -- poll up to ~3s for turn change
      if r8(ACTIVE) ~= before then break end
      wait(15)
    end
    if r8(ACTIVE) ~= before then
      wait(90); tap("a", 6, 40); wait(90)  -- ride out the next-turn banner
      return true
    end
    tap("b", 6, 40)                        -- close whatever we opened, retry
  end
  return false
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); local state = fh:read("*a"); fh:close()
  emu.loadSavestate(state); wait(30)
  log = io.open(OUT .. "run8.log", "w")
  unitrow("start")
  tap("a"); tap("up"); tap("a", 6, 50); tap("a", 6, 60); wait(240)
  unitrow("after-cap1")
  log:write("p1 end: " .. tostring(end_turn("p1end")) .. "\n")
  unitrow("p2-turn")
  log:write("p2 end: " .. tostring(end_turn("p2end")) .. "\n")
  unitrow("p1-next-day")
  settle()
  local ok = goto_tile(CX, CY)
  log:write("goto city: " .. tostring(ok) .. "\n")
  tap("a", 6, 40); tap("a", 6, 50)
  shot("menu2")
  tap("a", 6, 60)
  wait(300)
  unitrow("after-cap2")
  shot("final")
  log:write("done\n"); log:close()
  emu.stop(0)
end

local co = coroutine.create(main)
local pending = false
emu.addEventCallback(function() pending = true end, emu.eventType.endFrame)
emu.addMemoryCallback(function()
  if not pending then return end
  pending = false
  if coroutine.status(co) ~= "dead" then
    local ok, err = coroutine.resume(co)
    if not ok then
      local ef = io.open(OUT .. "run8-err.log", "w"); ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

end

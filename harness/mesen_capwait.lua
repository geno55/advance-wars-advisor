-- Does a Wait in place keep capture progress? (for engine/sim.py, which has
-- to decide what a non-capture action on the property does to the byte.)
-- Fixture: savestate slot 1 -- P1's Infantry (slot 2) one tile south of a
-- neutral city at (4,1), cursor resting on it (the A15 fixture).
--   C1  move north, Capt (progress 10 for a full Infantry); end P1; end P2;
--       P1 again: select the unit on the city, confirm the tile, pick WAIT
--       (second item of Capt / Wait). Read the capture bits before/after.
--   C2  then end P1, end P2, P1 again: Capt. Does it continue from what the
--       byte holds, or restart? Read the byte and the city owner.
-- The +4 field packs hp bits 0-6, ammo 7-10, capture 11-15.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_1.mss"
local UNIT_PTR, MAP, DIMS = 0x08282CB8, 0x02016C2A, 0x030036E0
local CURX, CURY = 0x030033F0, 0x030033F1
local DAY, ACTIVE = 0x03004420, 0x03004422
local SLOT, CX, CY = 2, 4, 1
local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local held = {}
emu.addEventCallback(function() pcall(function() emu.setInput(held, 0) end) end, emu.eventType.inputPolled)
local function wait(n) for _ = 1, n do coroutine.yield() end end
local function tap(btn, hold, gap) held = { [btn] = true }; wait(hold or 6); held = {}; wait(gap or 24) end
local log = io.open(OUT .. "capwait.log", "w")
local function L(s) log:write(s .. "\n"); log:flush() end
local function shot(tag) local png = emu.takeScreenshot(); local fh = io.open(OUT .. "cw-" .. tag .. ".png", "wb"); fh:write(png); fh:close() end
local function cursor() return r8(CURX), r8(CURY) end
local function goto_tile(x, y)
  for _ = 1, 80 do
    local cx, cy = cursor()
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left") elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function unitrow(tag)
  local a = r32(UNIT_PTR) + SLOT * 12
  local v = r16(a + 4)
  local w = r8(DIMS)
  local cb = r8(MAP + CY * w + CX)
  L(string.format("%s: unit(%d,%d) hp %d cap %d acted %d | city owner %d | day %d active %d cur(%d,%d)",
    tag, r8(a + 2), r8(a + 3), v % 128, math.floor(v / 2048), r8(a + 1) % 2,
    math.floor(cb / 32), r16(DAY), r8(ACTIVE), cursor()))
end
local function settle() wait(90); tap("b", 6, 40); wait(60) end
local function end_turn(tag)
  local before = r8(ACTIVE)
  for attempt = 1, 2 do
    settle()
    goto_tile(4, 3)
    tap("a", 6, 45)
    tap("down"); tap("down"); tap("down"); tap("down")
    tap("a", 6, 60)
    for _ = 1, 12 do
      if r8(ACTIVE) ~= before then break end
      wait(15)
    end
    if r8(ACTIVE) ~= before then
      wait(90); tap("a", 6, 40); wait(90)
      return true
    end
    tap("b", 6, 40)
  end
  return false
end
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); local state = fh:read("*a"); fh:close()
  emu.loadSavestate(state); wait(30)
  unitrow("start")
  tap("a"); tap("up"); tap("a", 6, 50); tap("a", 6, 60); wait(240)
  unitrow("after-cap1")
  L("p1 end: " .. tostring(end_turn("p1")))
  L("p2 end: " .. tostring(end_turn("p2")))
  unitrow("p1-day2")
  settle()
  L("goto city: " .. tostring(goto_tile(CX, CY)))
  tap("a", 6, 40); tap("a", 6, 50)
  shot("menu-day2")
  tap("down", 6, 20); tap("a", 6, 60)            -- Wait, the second item
  wait(120)
  unitrow("after-wait")
  L("p1 end: " .. tostring(end_turn("p1b")))
  L("p2 end: " .. tostring(end_turn("p2b")))
  unitrow("p1-day3")
  settle()
  L("goto city: " .. tostring(goto_tile(CX, CY)))
  tap("a", 6, 40); tap("a", 6, 50)
  shot("menu-day3")
  tap("a", 6, 60)                                  -- Capt, the first item
  wait(300)
  unitrow("after-cap-day3")
  shot("final")
  log:close(); emu.stop(0)
end
local co = coroutine.create(main)
local pending = false
emu.addEventCallback(function() pending = true end, emu.eventType.endFrame)
emu.addMemoryCallback(function()
  if not pending then return end
  pending = false
  if coroutine.status(co) ~= "dead" then
    local ok, err = coroutine.resume(co)
    if not ok then local ef = io.open(OUT .. "capwait_err.log", "w"); ef:write(tostring(err)); ef:close(); emu.stop(1) end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

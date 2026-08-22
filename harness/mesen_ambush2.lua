-- Fog ambush, round 2 (DERIVATION 38): the game's own move-select grid at
-- [0x03003600 + y*4][x] with a HIDDEN enemy on the route, vs the same board
-- with fog off. P1's Mech #3 is real-moved (7,6)->(6,7) on P1's turn; P2's
-- Tank 71 (9,7) is typed APC (vision 1) so the Mech is unlit at P2's turn.
-- Then select the APC and dump rows 6..8 of the grid (cost spent, 255 = no).
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local FOG = 0x0300431D
local VISION = 0x0201763A
local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end
local held = {}
emu.addEventCallback(function() pcall(function() emu.setInput(held, 0) end) end, emu.eventType.inputPolled)
local function wait(n) for _ = 1, n do coroutine.yield() end end
local function tap(btn, hold, gap) held = { [btn] = true }; wait(hold or 6); held = {}; wait(gap or 24) end
local function goto_tile(x, y)
  for _ = 1, 90 do
    local cx, cy = r8(CURX), r8(CURY)
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left") elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end
local log = io.open(OUT .. "ambush2.log", "w")
local function shot(tag) local png = emu.takeScreenshot(); local fh = io.open(OUT .. "amb2-" .. tag .. ".png", "wb"); fh:write(png); fh:close() end
local state
local function reload() emu.loadSavestate(state); wait(30) end
local function urow(slot)
  local a = ua(slot); local v4 = r16(a + 4)
  return string.format("u%d type=%d flags=%02X x=%d y=%d hp=%d fuel=%02X", slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128, r8(a + 6))
end
local function vis(x, y) return r8(VISION + y * 15 + x) end
local function end_turn()
  for attempt = 1, 2 do
    local pre = r16(0x03004424)
    goto_tile(2, 6); tap("a", 8, 50); tap("up", 8, 26); tap("a", 8, 70)
    for i = 1, 4 do tap("a", 8, 60) end
    wait(300)
    if r16(0x03004424) ~= pre then
      wait(120); tap("a", 8, 60); tap("a", 8, 60); wait(150)
      for _ = 1, 3 do tap("b", 8, 40) end
      return true
    end
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  return false
end
local function setup(fog)
  reload()
  w8(FOG, fog)
  log:write("  end P2 ok=" .. tostring(end_turn()) .. "\n")
  for attempt = 1, 3 do
    goto_tile(7, 6); tap("a", 8, 50); tap("down", 8, 26); tap("left", 8, 26); tap("a", 8, 60); tap("a", 8, 70); wait(90)
    if r8(ua(3) + 2) == 6 and r8(ua(3) + 3) == 7 then break end
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  log:write("  P1 mech " .. urow(3) .. "\n")
  w8(ua(71), 7)
  log:write("  end P1 ok=" .. tostring(end_turn()) .. "\n")
  wait(200)
  log:write(string.format("  P2 turn: fog=%d vis(6,7)=%d vis(7,7)=%d  %s\n", r8(FOG), vis(6, 7), vis(7, 7), urow(71)))
end
local function overlay_row(y)
  local rowp = r32(0x03003600 + y * 4)
  local t = {}
  for x = 0, 14 do t[#t + 1] = string.format("%3d", r8(rowp + x)) end
  return table.concat(t, " ")
end
local function probe(name, fog)
  log:write("== " .. name .. "\n")
  setup(fog)
  goto_tile(9, 7)
  tap("a", 8, 50)
  wait(30)
  for y = 6, 8 do log:write(string.format("  grid y=%d: %s\n", y, overlay_row(y))) end
  shot(name .. "-select")
  for _ = 1, 3 do tap("b", 8, 40) end
end
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  probe("O1-fog-on", 1)
  probe("O2-fog-off", 0)
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
    if not ok then local ef = io.open(OUT .. "ambush2_err.log", "w"); ef:write(tostring(err)); ef:close(); emu.stop(1) end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

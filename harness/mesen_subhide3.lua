-- Dived-sub concealment, round three. Same fixture and layout as
-- mesen_subhide.lua / _subhide2.lua: row y=7 Sea from x=6..10 plus (7,6);
-- P2's Tank 71 typed Cruiser at (9,7); P1's Mech 3 typed Sub, real-moved
-- from (7,6) and dived through its menu on P1's turn. The Cruiser's move
-- grid is the objective readout of what P2's side is shown: a visible enemy
-- is 255 and blocks, a hidden one reads as open sea (rounds one and two).
--  W1  sub dives at (8,7), ADJACENT to the Cruiser from the start. Its menu
--      there is Fire / Wait / Dive, so Dive is two downs (round two picked
--      Wait by mistake). P2: grid, screenshot, Fire from a standing start.
--  W2  property branch: sub dived at (7,7) and the tile rewritten as a P2
--      Port BEFORE P1 ends, so P2's turn starts on it. Grid and screenshot.
--  W3  two-unit reveal: sub dived at (7,7); P2's APC 69 real-moves
--      (12,7) -> (7,8) and Waits (south of the sub); then the Cruiser's
--      grid, and the Cruiser moves to (8,7): is Fire offered now?
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local FOG = 0x0300431D
local MAP = 0x02016C2A
local W, SEA, PORT_P2 = 15, 7, 0x4B
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
local log = io.open(OUT .. "subhide3.log", "w")
local function L(s) log:write(s .. "\n"); log:flush() end
local function shot(tag) local png = emu.takeScreenshot(); local fh = io.open(OUT .. "sh3-" .. tag .. ".png", "wb"); fh:write(png); fh:close() end
local function urow(slot)
  local a = ua(slot); local v4 = r16(a + 4)
  return string.format("u%d type=%d flags=%02X x=%d y=%d hp=%d fuel=%d", slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128, r8(a + 6) % 128)
end
local function overlay_row(y)
  local rowp = r32(0x03003600 + y * 4)
  local t = {}
  for x = 0, 14 do local v = r8(rowp + x); if v > 127 then v = v - 256 end; t[#t + 1] = string.format("%3d", v) end
  return table.concat(t, " ")
end
local state
local function reload() emu.loadSavestate(state); wait(30) end
local function end_turn()
  for attempt = 1, 2 do
    local pre = r16(0x03004424)
    goto_tile(2, 6); tap("a", 8, 50); tap("up", 8, 26); tap("a", 8, 70)
    for i = 1, 4 do tap("a", 8, 60) end
    wait(300)
    if r16(0x03004424) ~= pre then
      for _ = 1, 3 do tap("b", 8, 40) end
      return true
    end
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  return false
end
-- sub_x 7: menu Wait/Dive (one down); sub_x 8: adjacent to the Cruiser,
-- menu Fire/Wait/Dive (two downs). `before_p1_end` runs after the dive.
local function setup(sub_x, before_p1_end)
  reload()
  w8(FOG, 0)
  for x = 6, 10 do w8(MAP + 7 * W + x, SEA) end
  w8(MAP + 6 * W + 7, SEA)
  w8(ua(71), 22)
  L("  end P2 ok=" .. tostring(end_turn()))
  w8(ua(3), 24)
  for attempt = 1, 3 do
    goto_tile(7, 6); tap("a", 8, 50); tap("down", 8, 26)
    if sub_x == 8 then tap("right", 8, 26) end
    tap("a", 8, 60)
    tap("down", 8, 20)
    if sub_x == 8 then tap("down", 8, 20) end
    tap("a", 8, 70); wait(90)
    if r8(ua(3) + 2) == sub_x and r8(ua(3) + 3) == 7 and r8(ua(3) + 1) % 64 >= 32 then break end
    L("  P1 sub attempt " .. attempt .. " failed: " .. urow(3)); for _ = 1, 4 do tap("b", 8, 30) end
  end
  L("  P1 sub after menu: " .. urow(3))
  if before_p1_end then before_p1_end() end
  L("  end P1 ok=" .. tostring(end_turn()))
  wait(200)
  L("  P2 turn: " .. urow(3) .. " | " .. urow(71) .. " | " .. urow(69))
end
local function grid_and_shot(tag)
  goto_tile(r8(ua(71) + 2), r8(ua(71) + 3))
  shot(tag .. "-map")
  tap("a", 8, 50); wait(30)
  L("  " .. tag .. " grid y=6: " .. overlay_row(6))
  L("  " .. tag .. " grid y=7: " .. overlay_row(7))
  L("  " .. tag .. " grid y=8: " .. overlay_row(8))
  shot(tag .. "-select")
  for _ = 1, 3 do tap("b", 8, 40) end
end
local function fire_after(tag, moves)
  goto_tile(r8(ua(71) + 2), r8(ua(71) + 3)); tap("a", 8, 50); wait(20)
  for _, d in ipairs(moves) do tap(d, 8, 26) end
  tap("a", 8, 60); wait(30)
  shot(tag .. "-menu")
  local hp0 = r16(ua(3) + 4) % 128
  tap("a", 8, 50); shot(tag .. "-after-first-A"); tap("a", 8, 50); wait(500)
  local hp1 = r16(ua(3) + 4) % 128
  L(string.format("  %s A,A: sub hp %d -> %d (dmg %d); %s", tag, hp0, hp1, hp0 - hp1, urow(71)))
  for _ = 1, 3 do tap("b", 8, 40) end
end
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  L("== W1 dived at (8,7), adjacent to the Cruiser from the start")
  setup(8)
  grid_and_shot("W1")
  fire_after("W1", {})

  L("== W2 dived at (7,7) on a P2 Port, written before P1 ended")
  setup(7, function() w8(MAP + 7 * W + 7, PORT_P2); L("  (7,7) := P2 Port") end)
  grid_and_shot("W2")
  fire_after("W2", { "left" })

  L("== W3 dived at (7,7); APC 69 parks at (7,8) first, then the Cruiser moves in")
  setup(7)
  for attempt = 1, 3 do
    goto_tile(12, 7); tap("a", 8, 50); tap("down", 8, 26)
    for _ = 1, 5 do tap("left", 8, 26) end
    tap("a", 8, 60); wait(20); shot("W3-apc-menu")
    tap("down", 8, 20); tap("a", 8, 70); wait(90)     -- Drop / Wait: take Wait
    if r8(ua(69) + 2) == 7 and r8(ua(69) + 3) == 8 then break end
    L("  APC attempt " .. attempt .. " failed: " .. urow(69)); for _ = 1, 4 do tap("b", 8, 30) end
  end
  L("  APC parked: " .. urow(69))
  grid_and_shot("W3")
  fire_after("W3", { "left" })

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
    if not ok then local ef = io.open(OUT .. "subhide3_err.log", "w"); ef:write(tostring(err)); ef:close(); emu.stop(1) end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

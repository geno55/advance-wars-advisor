-- Dived-sub concealment, round two (after mesen_subhide.lua). Same fixture
-- and layout: row y=7 Sea from x=6..10 plus (7,6); P2's 71 typed Cruiser at
-- (9,7); P1's Mech 3 typed Sub, real-moved from (7,6) and dived on P1's turn.
-- The game's own check (0x08023BFC, 1 = shown, 0 = hidden) is asked by the
-- cursor-tile code for the unit UNDER the cursor, so every checkpoint parks
-- the cursor on the sub.
--  V1  sub dived at (7,7), nothing of P2's adjacent: check value; A on the
--      tile (a hidden unit's tile behaves as empty: the map menu opens);
--      then the tile rewritten as a P2 Port -- the property branch.
--  V2  Cruiser real-moves adjacent to (8,7) and Waits; P2 ends, P1 ends;
--      P2 again: check value with the Cruiser adjacent at turn start, and
--      Fire from a standing start (A, A, then Fire).
--  V3  sub dives at (8,7), already adjacent to the Cruiser: check value and
--      Fire from a standing start.
--  V4  sub dived at (7,7); Cruiser confirms onto (6,7), BEYOND the sub.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local UNIT_PTR, MAP_PTR, ARMY_PTR = 0x08282CB8, 0x08282CB4, 0x08282CBC
local CURX, CURY = 0x030033F0, 0x030033F1
local FOG = 0x0300431D
local MAP = 0x02016C2A
local W, SEA, PORT_P2 = 15, 7, 0x4B
local CHECK_ENTRY, CHECK_EXIT = 0x08023BFC, 0x08023D28
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
local log = io.open(OUT .. "subhide2.log", "w")
local function L(s) log:write(s .. "\n"); log:flush() end
local function shot(tag) local png = emu.takeScreenshot(); local fh = io.open(OUT .. "sh2-" .. tag .. ".png", "wb"); fh:write(png); fh:close() end
local function reg(n) local ok, st = pcall(emu.getState); if not ok then return -1 end; return tonumber(st["cpu.r" .. n]) or -1 end
local function urow(slot)
  local a = ua(slot); local v4 = r16(a + 4)
  return string.format("u%d type=%d flags=%02X x=%d y=%d hp=%d fuel=%d", slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128, r8(a + 6) % 128)
end
local calls, pend = {}, nil
local hooking = false
emu.addMemoryCallback(function()
  if not hooking then return end
  local p, x, y = reg(0), reg(1), reg(2)
  local base = r32(UNIT_PTR)
  local slot = (p ~= 0) and math.floor((p - base) / 12) or -1
  pend = string.format("u%d@(%d,%d)", slot, x % 65536, y % 65536)
end, emu.callbackType.exec, CHECK_ENTRY, CHECK_ENTRY, emu.cpuType.gba, emu.memType.gbaMemory)
emu.addMemoryCallback(function()
  if not hooking or not pend then return end
  local k = pend .. " -> " .. tostring(reg(0))
  calls[k] = (calls[k] or 0) + 1
  pend = nil
end, emu.callbackType.exec, CHECK_EXIT, CHECK_EXIT, emu.cpuType.gba, emu.memType.gbaMemory)
local function checkpoint(tag, frames)
  calls = {}; hooking = true; wait(frames or 90); hooking = false
  local keys = {}
  for k in pairs(calls) do keys[#keys + 1] = k end
  table.sort(keys)
  L("  check@" .. tag .. ": " .. #keys .. " distinct")
  for _, k in ipairs(keys) do L(string.format("    %s  x%d", k, calls[k])) end
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
-- sub_x: 7 -> move down one tile; 8 -> down then right
local function setup(dive, sub_x)
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
    if dive then tap("down", 8, 20) end
    tap("a", 8, 70); wait(90)
    if r8(ua(3) + 2) == sub_x and r8(ua(3) + 3) == 7 then break end
    L("  P1 sub move attempt " .. attempt .. " failed: " .. urow(3)); for _ = 1, 4 do tap("b", 8, 30) end
  end
  L("  P1 sub after menu: " .. urow(3))
  L("  end P1 ok=" .. tostring(end_turn()))
  wait(200)
  L("  P2 turn: active=" .. r16(0x030036AC) .. "  " .. urow(3) .. " | " .. urow(71))
end
local function fire_from_standing(tag)
  -- select the Cruiser where it stands, confirm the tile, then A on the
  -- first menu item and A again; Fire first => damage lands on the sub
  local cx, cy = r8(ua(71) + 2), r8(ua(71) + 3)
  goto_tile(cx, cy); tap("a", 8, 50); tap("a", 8, 60); wait(20)
  shot(tag .. "-menu")
  checkpoint(tag .. "-menu-open", 40)
  local hp0 = r16(ua(3) + 4) % 128
  tap("a", 8, 50); shot(tag .. "-after-first-A"); tap("a", 8, 50); wait(500)
  local hp1 = r16(ua(3) + 4) % 128
  L(string.format("  %s A,A: sub hp %d -> %d (dmg %d); %s", tag, hp0, hp1, hp0 - hp1, urow(71)))
  shot(tag .. "-after")
  for _ = 1, 3 do tap("b", 8, 40) end
end
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  L("== V1 dived at (7,7), nothing adjacent; cursor on the sub")
  setup(true, 7)
  goto_tile(7, 7)
  checkpoint("V1-cursor-on-sub", 90)
  shot("V1-cursor")
  tap("a", 8, 60); shot("V1-after-A"); checkpoint("V1-after-A", 40)
  for _ = 1, 3 do tap("b", 8, 40) end
  L("  V1 property branch: (7,7) rewritten as a P2 Port")
  w8(MAP + 7 * W + 7, PORT_P2)
  goto_tile(8, 7); goto_tile(7, 7)
  checkpoint("V1-on-P2-port", 90)
  shot("V1-port")
  w8(MAP + 7 * W + 7, SEA)

  L("== V2 Cruiser moves adjacent and waits; next P2 turn it is adjacent at turn start")
  setup(true, 7)
  goto_tile(9, 7); tap("a", 8, 50); tap("left", 8, 26); tap("a", 8, 70); wait(30)
  tap("a", 8, 60); wait(60)                 -- Wait (the only item, per H2)
  L("  V2 after move: " .. urow(71))
  L("  end P2 ok=" .. tostring(end_turn()))
  L("  end P1 ok=" .. tostring(end_turn()))
  wait(200)
  L("  P2 again: " .. urow(3) .. " | " .. urow(71))
  goto_tile(7, 7)
  checkpoint("V2-cursor-on-sub-adjacent", 90)
  shot("V2-cursor")
  L("  V2 grid before select: n/a")
  fire_from_standing("V2")

  L("== V3 sub dives at (8,7), adjacent to the Cruiser from the start")
  setup(true, 8)
  goto_tile(8, 7)
  checkpoint("V3-cursor-on-sub", 90)
  shot("V3-cursor")
  fire_from_standing("V3")

  L("== V4 dived at (7,7); Cruiser confirms onto (6,7), beyond the sub")
  setup(true, 7)
  goto_tile(9, 7); tap("a", 8, 50); wait(20)
  L("  V4 grid y=7: " .. overlay_row(7))
  tap("left", 8, 26); tap("left", 8, 26); tap("left", 8, 26); wait(20)
  shot("V4-arrow")
  tap("a", 8, 70); wait(150)
  shot("V4-after-confirm")
  L("  V4: " .. urow(71) .. " | " .. urow(3))
  checkpoint("V4-settled", 40)
  for _ = 1, 3 do tap("b", 8, 40) end
  L("  V4 final: " .. urow(71))

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
    if not ok then local ef = io.open(OUT .. "subhide2_err.log", "w"); ef:write(tostring(err)); ef:close(); emu.stop(1) end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

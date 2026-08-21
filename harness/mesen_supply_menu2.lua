-- Supply probes, round 2. Round 1 found the Supply MENU check reading the
-- tile->unit layer at map+0x51A (0x0802588C) where every standing ground
-- unit lives in map+0x12 -- and the menu refused an adjacent needy MdTank.
-- Hypothesis: +0x51A indexes AIR units, +0x12 ground, and the menu need-scan
-- therefore only ever sees air neighbours. This run:
--  P1 dump both layers as-is (expect +0x51A empty: fixture has no air).
--  P2 type MdTank67 -> BCopter, real-move it one tile; dump layers again:
--     which layer did the move applier register it in?
--  P3 write 67 (air) and MdTank68 (ground) low, both adjacent to (12,3).
--  P4 real-move APC70 to (12,3); screenshot the menu; if Supply is there,
--     drive it with watches: does it fill the air unit only, or both?
--  P5 rewrite both low, P2 city under Tank71 hp45, End Turn twice: the
--     write-PC log across P2's turn start gives repair/supply/burn order and
--     which layer the auto-supply walks.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local MAP_PTR = 0x08282CB4
local CURX, CURY = 0x030033F0, 0x030033F1

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
  held = { [btn] = true }; wait(hold or 6); held = {}; wait(gap or 24)
end
local function cursor() return r8(CURX), r8(CURY) end
local function goto_tile(x, y)
  for _ = 1, 90 do
    local cx, cy = cursor()
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left")
    elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end

local log = io.open(OUT .. "supply_menu2.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "sup2-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end

local watching = false
local ub0 = nil
emu.addMemoryCallback(function(addr, value)
  if not watching or not ub0 then return end
  local off = addr - ub0
  if off < 0 or off >= 130 * 12 then return end
  local slot = math.floor(off / 12)
  local field = off % 12
  if field == 1 then return end
  if slot < 64 then return end
  log:write(string.format("  W u%d+%d=%02X pc=%08X\n", slot, field, value,
    pc_of()))
end, emu.callbackType.write, 0x02019F34, 0x0201A54C,
   emu.cpuType.gba, emu.memType.gbaMemory)
local fundwatch = false
emu.addMemoryCallback(function(addr, value)
  if not fundwatch then return end
  log:write(string.format("  W funds2 val=%d pc=%08X\n", value, pc_of()))
end, emu.callbackType.write, ARMY + 2 * STRIDE, ARMY + 2 * STRIDE + 3,
   emu.cpuType.gba, emu.memType.gbaMemory)

local state
local function reload()
  emu.loadSavestate(state); wait(30)
  ub0 = r32(UNIT_PTR)
end
local function set_fuel(slot, v) w8(ua(slot) + 6, v) end
local function set_ammo(slot, n)
  local v = r16(ua(slot) + 4)
  w16(ua(slot) + 4, v - (math.floor(v / 128) % 16) * 128 + n * 128)
end
local function set_hp(slot, hp)
  local v = r16(ua(slot) + 4)
  w16(ua(slot) + 4, v - (v % 128) + hp)
end
local function urow(slot)
  local a = ua(slot)
  local v4 = r16(a + 4)
  return string.format("u%d type=%d flags=%02X x=%d y=%d hp=%d ammo=%d fuel=%02X",
    slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128,
    math.floor(v4 / 128) % 16, r8(a + 6))
end
local function dump_layers(tag, y0, y1)
  local map = r32(MAP_PTR)
  local w = r16(map)
  for y = y0, y1 do
    local rowoff = r16(map + 0x4682 + y * 2)
    local g, a = {}, {}
    for x = 0, w - 1 do
      g[#g + 1] = string.format("%02X", r8(map + 0x12 + rowoff + x))
      a[#a + 1] = string.format("%02X", r8(map + 0x51A + rowoff + x))
    end
    log:write(string.format("%s y=%d  g12: %s   a51A: %s\n", tag, y,
      table.concat(g, " "), table.concat(a, " ")))
  end
end
-- verified real move: returns true if the unit record lands on (dx,dy)
local function move(slot, x, y, dirs, dx, dy)
  for attempt = 1, 2 do
    goto_tile(x, y)
    tap("a", 8, 50)
    for _, d in ipairs(dirs) do tap(d, 8, 26) end
    tap("a", 8, 60)
    tap("a", 8, 70)                        -- top action item (Wait)
    wait(90)
    local a = ua(slot)
    if r8(a + 2) == dx and r8(a + 3) == dy then return true end
    log:write(string.format("  move u%d attempt %d failed (at %d,%d)\n",
      slot, attempt, r8(a + 2), r8(a + 3)))
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  return false
end
local function end_turn()
  for attempt = 1, 2 do
    local pre = r16(0x03004424)
    goto_tile(2, 6)
    tap("a", 8, 50); tap("up", 8, 26); tap("a", 8, 70)
    for i = 1, 4 do tap("a", 8, 60) end
    wait(300)
    if r16(0x03004424) ~= pre then return true end
    log:write(string.format("  end_turn attempt %d failed (tag=%04X)\n",
      attempt, r16(0x03004424)))
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  return false
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  reload()

  log:write("== P1 layers as parked\n")
  dump_layers("  pre", 0, 9)

  log:write("== P2 air unit registered by a real move\n")
  w8(ua(67), 19)                            -- MdTank67 -> BCopter
  local ok = move(67, 13, 4, { "left" }, 12, 4)
  log:write("  move67 ok=" .. tostring(ok) .. "  " .. urow(67) .. "\n")
  dump_layers("  mid", 3, 4)

  log:write("== P3 write both needy\n")
  set_fuel(67, 5); set_ammo(67, 0)
  set_fuel(68, 5); set_ammo(68, 1)          -- ground MdTank at (11,3)
  log:write("  " .. urow(67) .. "\n")
  log:write("  " .. urow(68) .. "\n")

  log:write("== P4 APC to (12,3), menu\n")
  goto_tile(14, 2)
  tap("a", 8, 50)
  tap("left", 8, 26); tap("down", 8, 26); tap("left", 8, 26)
  tap("a", 8, 60)                          -- confirm destination
  wait(40); shot("P4-menu")
  watching = true
  tap("a", 8, 90)                          -- top action item, watches on
  wait(150)
  watching = false
  shot("P4-after")
  log:write("  post " .. urow(67) .. "\n")
  log:write("  post " .. urow(68) .. "\n")
  log:write("  post " .. urow(70) .. "\n")

  log:write("== P5 turn start order\n")
  set_fuel(67, 5); set_ammo(67, 0)
  set_fuel(68, 5); set_ammo(68, 1)
  set_hp(71, 45); set_fuel(71, 20)
  local map = r32(MAP_PTR)
  local off7 = r16(map + 0x4682 + 7 * 2)
  w8(map + 0x1432 + off7 + 9, 0x46)         -- P2 city under Tank71 (9,7)
  log:write("  pre " .. urow(67) .. "\n")
  log:write("  pre " .. urow(68) .. "\n")
  log:write("  pre " .. urow(71) .. "\n")
  log:write(string.format("  funds2=%d day=%d\n",
    r32(ARMY + 2 * STRIDE), r8(0x03004420)))
  ok = end_turn()
  log:write("  end P2 ok=" .. tostring(ok) ..
    string.format(" tag=%04X\n", r16(0x03004424)))
  watching = true; fundwatch = true
  ok = end_turn()
  log:write("  end P1 ok=" .. tostring(ok) ..
    string.format(" tag=%04X\n", r16(0x03004424)))
  wait(600)
  watching = false; fundwatch = false
  shot("P5-after")
  log:write("  post " .. urow(67) .. "\n")
  log:write("  post " .. urow(68) .. "\n")
  log:write("  post " .. urow(71) .. "\n")
  log:write(string.format("  post funds2=%d day=%d\n",
    r32(ARMY + 2 * STRIDE), r8(0x03004420)))
  log:close()
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
      local ef = io.open(OUT .. "supply_menu2_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

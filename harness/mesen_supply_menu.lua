-- APC Supply probes (supply handoff, measurement 1 + the order question).
-- Fixture: slot-2 VS state, P2 to move. P2 roster: Inf65(8,4 acted),
-- Inf66(loaded in APC69), MdTank67(13,4), MdTank68(11,3), APC69(12,7,
-- carrying 66), APC70(14,2, empty), Tank71(9,7), Rockets72(14,4).
-- Cases, each from a fresh reload:
--  A  MdTank67 written low, APC70 real-moved (14,2)->(13,3) adjacent;
--     screenshot the action menu, tap the top item with write-watches on 67.
--  B  MdTank67 real-moved to (11,7) and Tank71 to (12,6), both written low,
--     APC69 (which carries cargo) selected in place: does Supply fill BOTH
--     adjacent sides, and what does the menu look like with Drop present?
--  C  APC70 selected in place, no adjacent friendly: menu control.
--  C2 APC70 moved adjacent to a FULL MdTank67: is Supply still offered?
--  D  order probe: MdTank67 moved to (11,7), then typed BCopter fuel 1;
--     Tank71 hp 45 on a written P2 city. End Turn twice; the write-PC log
--     across P2's turn start gives the burn/supply/repair order, and 67's
--     survival answers "crash or refuel next to an APC".
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
local function w32(a, v) emu.write32(a, v, emu.memType.gbaMemory) end

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

local log = io.open(OUT .. "supply_menu.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "sup-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end

-- write-watch over the whole unit array; log slot/offset/value/pc while armed
local watching = false
local ub0 = nil
emu.addMemoryCallback(function(addr, value)
  if not watching or not ub0 then return end
  local off = addr - ub0
  if off < 0 or off >= 130 * 12 then return end
  local slot = math.floor(off / 12)
  local field = off % 12
  if field == 1 then return end            -- animation bit churn, too noisy
  if slot < 64 then return end             -- only P2's block matters here
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
local function end_turn()
  tap("a", 6, 40); tap("up", 6, 16); tap("a", 6, 60)
  for i = 1, 4 do tap("a", 6, 60) end
  wait(240)
end
-- real-move: select the unit at (x,y), tap the listed directions, confirm,
-- then A on the top action-menu item (Wait for a plain arrival)
local function move(x, y, dirs)
  goto_tile(x, y)
  tap("a", 6, 40)
  for _, d in ipairs(dirs) do tap(d, 6, 16) end
  tap("a", 6, 50)
  tap("a", 6, 60)
  wait(60)
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  -- A. single neighbour, clean APC ------------------------------------------
  reload()
  log:write("== A single neighbour\n")
  set_fuel(67, 5); set_ammo(67, 1)
  log:write("  pre  " .. urow(67) .. "\n")
  goto_tile(14, 2)
  tap("a", 6, 40); tap("down", 6, 16); tap("left", 6, 16)
  tap("a", 6, 50)
  wait(30); shot("A-menu")
  watching = true
  tap("a", 6, 90)                          -- top menu item
  wait(120)
  watching = false
  shot("A-after")
  log:write("  post " .. urow(67) .. "\n")
  log:write("  post " .. urow(70) .. "\n")

  -- B. both sides, APC with cargo, supplied standing still -------------------
  reload()
  log:write("== B both sides\n")
  move(13, 4, { "down", "down", "down", "left", "left" })   -- MdTank67 -> 11,7
  move(9, 7, { "right", "right", "up", "right" })           -- Tank71  -> 12,6
  log:write("  moved " .. urow(67) .. "\n")
  log:write("  moved " .. urow(71) .. "\n")
  set_fuel(67, 5); set_ammo(67, 1)
  set_fuel(71, 7); set_ammo(71, 2)
  goto_tile(12, 7)
  tap("a", 6, 40)                          -- move-select on APC69
  tap("a", 6, 50)                          -- destination = own tile
  wait(30); shot("B-menu")
  watching = true
  tap("a", 6, 90)
  wait(120)
  watching = false
  shot("B-after")
  log:write("  post " .. urow(67) .. "\n")
  log:write("  post " .. urow(71) .. "\n")
  log:write("  post " .. urow(69) .. "\n")
  log:write("  post " .. urow(66) .. "\n")  -- the cargo: supplied too?

  -- C. no neighbour control ---------------------------------------------------
  reload()
  log:write("== C no neighbour\n")
  goto_tile(14, 2)
  tap("a", 6, 40); tap("a", 6, 50)
  wait(30); shot("C-menu")
  tap("b", 6, 40); tap("b", 6, 40); tap("b", 6, 40)

  -- C2. full neighbour --------------------------------------------------------
  reload()
  log:write("== C2 full neighbour\n")
  goto_tile(14, 2)
  tap("a", 6, 40); tap("down", 6, 16); tap("left", 6, 16)
  tap("a", 6, 50)
  wait(30); shot("C2-menu")
  tap("b", 6, 40); tap("b", 6, 40); tap("b", 6, 40)

  -- D. order probe across the turn boundary ----------------------------------
  reload()
  log:write("== D order probe\n")
  move(13, 4, { "down", "down", "down", "left", "left" })   -- MdTank67 -> 11,7
  w8(ua(67), 19)                            -- type -> BCopter
  set_fuel(67, 1)
  set_hp(71, 45)
  local map = r32(MAP_PTR)
  local off7 = r16(map + 0x4682 + 7 * 2)
  w8(map + 0x1432 + off7 + 9, 0x46)         -- P2 city under Tank71 at (9,7)
  log:write("  pre  " .. urow(67) .. "\n")
  log:write("  pre  " .. urow(71) .. "\n")
  log:write(string.format("  free4357=%d funds2=%d\n",
    r8(0x03004357), r32(ARMY + 2 * STRIDE)))
  goto_tile(2, 6)
  end_turn()                                -- P2 done
  log:write("  after P2 end: tag=" .. string.format("%04X", r16(0x03004424)) .. "\n")
  goto_tile(2, 6)
  watching = true; fundwatch = true
  end_turn()                                -- P1 done -> P2 turn start
  wait(600)
  watching = false; fundwatch = false
  shot("D-after")
  log:write("  post " .. urow(67) .. "\n")
  log:write("  post " .. urow(71) .. "\n")
  log:write(string.format("  post funds2=%d tag=%04X\n",
    r32(ARMY + 2 * STRIDE), r16(0x03004424)))
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
      local ef = io.open(OUT .. "supply_menu_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

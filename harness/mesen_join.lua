-- Join probes (DERIVATION 34). Recon70 (14,2) and Mech67 (13,4) are both
-- typed Tank (RAM 5); 70 real-moves left,down,down onto 67 (3 road tiles).
-- The merge routine 0x0802649C was read first; each case is a prediction:
--  J1 50+50 fuel 30/30 ammo 4/4 capture 3(mover)/7(target)
--     -> hp 100, fuel 57 (30-3+30), ammo 8, capture 7, no refund, 67 removed
--  J2 45+45 fuel 60/50 ammo 6/6 -> bars 5+5: hp 100 (not 90), fuel 70 cap,
--     ammo 9 cap
--  J3 70+60 -> 13 bars: hp 100, refund 3*700 = 2100
--  J4 Kanbei, 70+60 -> refund 3*840 = 2520
--  J5 target hp 100: Join refused (menu/confirm screenshot)
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local FUNDS2 = ARMY + 2 * STRIDE

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
local function goto_tile(x, y)
  for _ = 1, 90 do
    local cx, cy = r8(CURX), r8(CURY)
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left")
    elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end

local log = io.open(OUT .. "join.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "join-" .. tag .. ".png", "wb")
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
emu.addMemoryCallback(function(addr, value)
  if not watching then return end
  log:write(string.format("  W funds2 val=%d pc=%08X\n", value, pc_of()))
end, emu.callbackType.write, FUNDS2, FUNDS2 + 3,
   emu.cpuType.gba, emu.memType.gbaMemory)

local state
local function reload()
  emu.loadSavestate(state); wait(30)
  ub0 = r32(UNIT_PTR)
end
local function setu(slot, t, hp, fuel, ammo, cap)
  local a = ua(slot)
  w8(a, t)
  w16(a + 4, hp + ammo * 128 + (cap or 0) * 2048)
  w8(a + 6, fuel)
end
local function urow(slot)
  local a = ua(slot)
  local v4 = r16(a + 4)
  return string.format(
    "u%d type=%d flags=%02X x=%d y=%d hp=%d ammo=%d cap=%d fuel=%02X",
    slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128,
    math.floor(v4 / 128) % 16, math.floor(v4 / 2048), r8(a + 6))
end

local function run_case(name, setup, expect_join)
  reload()
  log:write("== " .. name .. "\n")
  setup()
  log:write("  pre  " .. urow(70) .. "\n  pre  " .. urow(67) .. "\n")
  log:write(string.format("  pre  funds=%d b4342=%d\n", r32(FUNDS2),
    r8(0x03004342)))
  goto_tile(14, 2)
  tap("a", 8, 50)
  tap("left", 8, 26); tap("down", 8, 26); tap("down", 8, 26)
  tap("a", 8, 60)                          -- confirm onto 67
  wait(40); shot(name .. "-menu")
  if expect_join then
    watching = true
    tap("a", 8, 90)
    wait(200)
    watching = false
  else
    for _ = 1, 3 do tap("b", 8, 40) end
  end
  log:write("  post " .. urow(70) .. "\n  post " .. urow(67) .. "\n")
  log:write(string.format("  post funds=%d\n", r32(FUNDS2)))
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  run_case("J1", function()
    setu(70, 5, 50, 30, 4, 3); setu(67, 5, 50, 30, 4, 7)
  end, true)
  run_case("J2", function()
    setu(70, 5, 45, 60, 6); setu(67, 5, 45, 50, 6)
  end, true)
  run_case("J3", function()
    setu(70, 5, 70, 50, 9); setu(67, 5, 60, 50, 9)
  end, true)
  run_case("J4", function()
    w8(ARMY + 2 * STRIDE + 0x1d, 6)
    setu(70, 5, 70, 50, 9); setu(67, 5, 60, 50, 9)
  end, true)
  run_case("J5", function()
    setu(70, 5, 50, 50, 9); setu(67, 5, 100, 50, 9)
  end, false)
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
      local ef = io.open(OUT .. "join_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

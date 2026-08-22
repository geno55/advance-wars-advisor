-- Production probes (DERIVATION 36). The fixture map has no factories, so
-- each case WRITES one under an empty tile (terrain id | P2 tag 0x40) and
-- presses A on it. Round 1 is reconnaissance: screenshots of each shop and
-- one purchase (top item) under write-watches on the unit array and funds.
--  B1 Base (0x4E) at (11,6): screenshot, buy top item (Infantry?)
--  B2 Airport (0x4A) at (11,6): screenshot, B out
--  B3 Port (0x4B) at (11,6): screenshot, B out
--  B4 Kanbei (co 6), Base: buy top item -- the deploy multiplier
--  B5 funds written 900, Base: screenshot (Infantry greyed?), try A, B out
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local MAP_PTR = 0x08282CB4
local CURX, CURY = 0x030033F0, 0x030033F1
local FUNDS2 = ARMY + 2 * STRIDE

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end
local function w32(a, v) emu.write32(a, v, emu.memType.gbaMemory) end

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

local log = io.open(OUT .. "build.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "build-" .. tag .. ".png", "wb")
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
local function set_terr(x, y, v)
  local map = r32(MAP_PTR)
  w8(map + 0x1432 + r16(map + 0x4682 + y * 2) + x, v)
end
local function urow(slot)
  local a = ua(slot)
  local v4 = r16(a + 4)
  return string.format(
    "u%d type=%d flags=%02X x=%d y=%d hp=%d ammo=%d fuel=%02X cargo7=%d",
    slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128,
    math.floor(v4 / 128) % 16, r8(a + 6), r8(a + 7))
end
local function occupied_slots()
  local t = {}
  for s = 64, 127 do if r8(ua(s)) ~= 0 then t[#t + 1] = s end end
  return table.concat(t, ",")
end

local function shop_case(name, terr, buy, co, funds)
  reload()
  log:write("== " .. name .. "\n")
  if co then w8(ARMY + 2 * STRIDE + 0x1d, co) end
  if funds then w32(FUNDS2, funds) end
  set_terr(11, 6, terr)
  log:write("  pre  funds=" .. r32(FUNDS2) .. " slots=" .. occupied_slots() .. "\n")
  goto_tile(11, 6)
  tap("a", 8, 60)
  wait(40); shot(name .. "-shop")
  if buy then
    watching = true
    tap("a", 8, 90)                        -- buy the top item
    wait(60); shot(name .. "-after-a")
    tap("a", 8, 90)                        -- in case a confirm follows
    wait(150)
    watching = false
    shot(name .. "-after")
  else
    for _ = 1, 3 do tap("b", 8, 40) end
  end
  log:write("  post funds=" .. r32(FUNDS2) .. " slots=" .. occupied_slots() .. "\n")
  for s = 64, 127 do
    local a = ua(s)
    if r8(a) ~= 0 and r8(a + 2) == 11 and r8(a + 3) == 6 then
      log:write("  new  " .. urow(s) .. "\n")
    end
  end
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  shop_case("B1-base-buy", 0x4E, true)
  shop_case("B2-airport", 0x4A, false)
  shop_case("B3-port", 0x4B, false)
  shop_case("B4-kanbei-base-buy", 0x4E, true, 6)
  shop_case("B5-broke-base", 0x4E, true, nil, 900)
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
      local ef = io.open(OUT .. "build_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

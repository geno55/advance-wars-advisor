-- Grit range upper bound: targets parked at d=3,4,6,7. With power, which are
-- offered? Range 2-5 offers {3,4}; 2-6 adds d=6.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local CO_BASE, CO_STRIDE = 0x08284A0C, 292

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
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

local log = io.open(OUT .. "power_fx3.log", "w")

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); local state = fh:read("*a"); fh:close()
  emu.loadSavestate(state); wait(30)
  w8(ua(2) + 2, 14); w8(ua(2) + 3, 7)     -- d=3
  w8(ua(5) + 2, 14); w8(ua(5) + 3, 8)     -- d=4
  w8(ua(7) + 2, 14); w8(ua(7) + 3, 10)    -- d=6
  w8(ua(1) + 2, 14); w8(ua(1) + 3, 11)    -- d=7
  local a2 = ARMY + 2 * STRIDE
  w8(a2 + 0x1d, 5)
  w32(a2 + 0x20, r32(CO_BASE + 5 * CO_STRIDE + 8))
  w8(a2 + 0x24, 1)
  goto_tile(8, 7)
  tap("a", 6, 40); tap("down", 6, 16); tap("down", 6, 16)
  tap("a", 6, 60)
  for i = 1, 6 do tap("a", 6, 80) end
  wait(240)
  for i = 1, 3 do tap("b", 6, 30) end
  wait(30)
  goto_tile(14, 4)
  tap("a", 6, 40)
  tap("a", 6, 50)
  tap("a", 6, 50)
  local seen = {}
  for i = 1, 8 do
    local x, y = cursor()
    seen[x .. "," .. y] = true
    tap("right", 6, 24)
  end
  local list = {}
  for k in pairs(seen) do list[#list + 1] = k end
  table.sort(list)
  log:write("grit3 targets: " .. table.concat(list, "  ") .. "\n")
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
      local ef = io.open(OUT .. "power_fx3_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

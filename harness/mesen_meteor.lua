-- Meteor target-selection probe (DERIVATION 30). The scorer at 0x8063358
-- rolls rng%3 among three scans; this board makes each scan prefer a
-- DIFFERENT cluster, all built by in-place type rewrites (the tile index
-- keeps mapping the real tiles; teleports would break the scorer):
--   G1 raw-HP winner:  four Infantry around (1,7)          hp 400, value  4000
--   G2 value winner:   MdTank/MdTank/Tank around (6,2)     hp 300, value 39000
--   G3 indirect winner: lone Battleship at (7,6)           hp 100, weighted 56000
-- Per seed: write the RNG, activate Sturm (co=10), diff HPs -> hit cluster.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local CO_BASE, CO_STRIDE = 0x08284A0C, 292
local RNG = 0x03001D30

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

local log = io.open(OUT .. "meteor.log", "w")
local state

local function runseed(seed)
  emu.loadSavestate(state); wait(30)
  -- G1: slots 4,6,8 -> Infantry (slot 1 already is)
  w8(ua(4), 1); w8(ua(6), 1); w8(ua(8), 1)
  -- G2: slots 5,2 -> MdTank (slot 7 is already a Tank)
  w8(ua(5), 3); w8(ua(2), 3)
  -- G3: slot 3 -> Battleship
  w8(ua(3), 21)
  local a2 = ARMY + 2 * STRIDE
  w8(a2 + 0x1d, 10)
  w32(a2 + 0x20, r32(CO_BASE + 10 * CO_STRIDE + 8))
  w8(a2 + 0x24, 1)
  local pre = {}
  for s = 1, 8 do pre[s] = r16(ua(s) + 4) % 128 end
  w32(RNG, seed)
  goto_tile(8, 7)
  tap("a", 6, 40); tap("down", 6, 16); tap("down", 6, 16)
  tap("a", 6, 60)
  for i = 1, 6 do tap("a", 6, 80) end
  wait(300)
  local hits = {}
  for s = 1, 8 do
    local now = r16(ua(s) + 4) % 128
    if now ~= pre[s] then
      hits[#hits + 1] = string.format("%d:%d->%d", s, pre[s], now)
    end
  end
  log:write(string.format("seed=%d rng_after=%d hits=[%s]\n",
    seed, r32(RNG), table.concat(hits, " ")))
  log:flush()
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  for _, s in ipairs({ 0, 1, 2, 3, 4, 5, 6, 7, 100, 1000, 54321, 999999 }) do
    runseed(s)
  end
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
      local ef = io.open(OUT .. "meteor_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

-- The luck consumption, live (DERIVATION 32). P2 Tank at (9,7) fires on a
-- P1 Infantry teleported to the road at (8,7): base 75, 0 stars, full HP,
-- so damage - 75 IS the roll. The RNG is written right before the confirm
-- tap; the state after the battle counts the draws.
--   andy:  roll = next(seed) %% 10 predicted per seed
--   nell:  co=0, %% 20 -- seeds chosen to land rolls above 9
--   sonja: co=7, %% 25 - 15 -- negative rolls pull damage BELOW the base
--   fixed: settings byte +0x06 written 1 -- every roll becomes exactly +5
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local RNG = 0x03001D30
local LUCKOFF = 0x03004316

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

local log = io.open(OUT .. "luck.log", "w")
local state

local function battle(tag, seed, co, luckoff)
  emu.loadSavestate(state); wait(30)
  w8(ua(2) + 2, 8); w8(ua(2) + 3, 7)
  if co then w8(ARMY + 2 * STRIDE + 0x1d, co) end
  if luckoff then w8(LUCKOFF, 1) end
  local hp0 = r16(ua(2) + 4) % 128
  local thp0 = r16(ua(71) + 4) % 128
  goto_tile(9, 7)
  tap("a", 6, 40)
  tap("a", 6, 50)
  tap("a", 6, 50)          -- Fire
  w32(RNG, seed)
  tap("a", 6, 30)          -- confirm
  wait(420)
  local dmg = hp0 - r16(ua(2) + 4) % 128
  local counter = thp0 - r16(ua(71) + 4) % 128
  log:write(string.format(
    "%s seed=%d dmg=%d roll=%d counter=%d rng_after=%d\n",
    tag, seed, dmg, dmg - 75, counter, r32(RNG)))
  log:flush()
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  for _, s in ipairs({ 0, 1, 2, 3, 4, 5, 6, 7 }) do
    battle("andy", s, nil, false)
  end
  -- Nell: seeds whose first draws land 10..19 under %20 show the wider roll
  for _, s in ipairs({ 0, 3, 5, 7, 11, 13 }) do
    battle("nell", s, 0, false)
  end
  -- Sonja: %25 - 15; small rolls pull the damage below 75
  for _, s in ipairs({ 0, 1, 2, 5, 9, 12 }) do
    battle("sonja", s, 7, false)
  end
  battle("fixed", 12345, nil, true)
  battle("fixed", 99, nil, true)
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
      local ef = io.open(OUT .. "luck_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

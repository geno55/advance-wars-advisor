-- Meteor edge cases, all with seed 2 (strategy 0, the value scan):
--  1. friendly fire: P2's own Infantry teleported into G2's blast (the
--     APPLIER reads record x,y like Drake's, so a teleport lands in it even
--     though the scorer's tile index cannot see it).
--  2. the hp<=10 scoring exclusion: G2 written to 10 internal -- its score
--     must go to zero and the meteor must fall on G3 instead.
--  3. the damage floor: slot 7 at 5 internal inside the chosen blast.
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
local function set_hp(slot, v)
  local a = ua(slot) + 4
  local cur = r16(a)
  w16(a, cur - cur % 128 + v)
end

local log = io.open(OUT .. "meteor2.log", "w")
local state

local function setup()
  emu.loadSavestate(state); wait(30)
  w8(ua(4), 1); w8(ua(6), 1); w8(ua(8), 1)
  w8(ua(5), 3); w8(ua(2), 3)
  w8(ua(3), 21)
  local a2 = ARMY + 2 * STRIDE
  w8(a2 + 0x1d, 10)
  w32(a2 + 0x20, r32(CO_BASE + 10 * CO_STRIDE + 8))
  w8(a2 + 0x24, 1)
end

local function fire(tag, slots)
  local pre = {}
  for _, s in ipairs(slots) do pre[s] = r16(ua(s) + 4) % 128 end
  w32(RNG, 2)
  goto_tile(8, 7)
  tap("a", 6, 40); tap("down", 6, 16); tap("down", 6, 16)
  tap("a", 6, 60)
  for i = 1, 6 do tap("a", 6, 80) end
  wait(300)
  local rows = {}
  for _, s in ipairs(slots) do
    local now = r16(ua(s) + 4) % 128
    rows[#rows + 1] = string.format("%d:%d->%d", s, pre[s], now)
  end
  log:write(string.format("%s rng_after=%d %s\n",
    tag, r32(RNG), table.concat(rows, " ")))
  log:flush()
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  setup()
  w8(ua(65) + 2, 5); w8(ua(65) + 3, 2)    -- P2 Inf into G2's blast (d1)
  fire("friendly", { 2, 5, 7, 65, 3 })

  setup()
  set_hp(2, 10); set_hp(5, 10); set_hp(7, 10)
  fire("exclusion", { 2, 5, 7, 3, 1 })

  setup()
  set_hp(7, 5)
  fire("floor", { 2, 5, 7, 3 })

  setup()
  set_hp(2, 50)
  fire("clamp", { 2, 5, 7, 3 })

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
      local ef = io.open(OUT .. "meteor2_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)


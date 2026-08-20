-- Dive retakes: the command is the SECOND menu item (Wait / Dive, and
-- Wait / Rise once the bit is written). Plus the D-control: the BCopter must
-- be able to hit the SAME sub when it is surfaced, or D proved nothing.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local MAP = 0x02016C2A
local W, SEA = 15, 7

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end

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

local log = io.open(OUT .. "dive2.log", "w")
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end
local watch_addr, watching = 0, false
emu.addMemoryCallback(function(addr, value)
  if not watching or addr ~= watch_addr then return end
  log:write(string.format("  W flags val=%s pc=%08X\n", tostring(value), pc_of()))
  log:flush()
end, emu.callbackType.write, 0x0201A000, 0x0201B000,
   emu.cpuType.gba, emu.memType.gbaMemory)

local state
local function reload()
  emu.loadSavestate(state); wait(30)
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  -- A2: Dive (second item) sets the bit
  reload()
  w8(ua(71), 24)
  w8(MAP + 7 * W + 9, SEA)
  watch_addr = ua(71) + 1
  watching = true
  goto_tile(9, 7)
  tap("a", 6, 40)
  tap("a", 6, 50)
  tap("down", 6, 20)
  tap("a", 6, 60)
  wait(60)
  watching = false
  log:write(string.format("A2: flags=%02X dived=%d\n",
    r8(ua(71) + 1), math.floor(r8(ua(71) + 1) / 32) % 2))

  -- B2: Rise (second item) clears it
  reload()
  w8(ua(71), 24)
  w8(MAP + 7 * W + 9, SEA)
  w8(ua(71) + 1, 0x20)
  watch_addr = ua(71) + 1
  watching = true
  goto_tile(9, 7)
  tap("a", 6, 40)
  tap("a", 6, 50)
  tap("down", 6, 20)
  tap("a", 6, 60)
  wait(60)
  watching = false
  log:write(string.format("B2: flags=%02X dived=%d\n",
    r8(ua(71) + 1), math.floor(r8(ua(71) + 1) / 32) % 2))

  -- D2: bcopter CAN hit the surfaced sub (control for D)
  reload()
  w8(ua(2), 24)
  w8(ua(2) + 2, 8); w8(ua(2) + 3, 7)
  w8(MAP + 7 * W + 8, SEA)
  w8(ua(71), 19)
  local hp0 = r16(ua(2) + 4) % 128
  goto_tile(9, 7)
  tap("a", 6, 40)
  tap("a", 6, 50)
  tap("a", 6, 50)
  tap("a", 6, 30)
  wait(500)
  local hp1 = r16(ua(2) + 4) % 128
  log:write(string.format("D2: subhp %d->%d dmg=%d\n", hp0, hp1, hp0 - hp1))

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
      local ef = io.open(OUT .. "dive2_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

-- Charge probe on the powers-ON match (slot 2): P2 Tank fires on a teleported
-- P1 Infantry; every CPU write into the army array is logged with the PC that
-- did it. Control watch on the defender's HP halfword proves the machinery.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local GATE = 0x03004318
local ATT, DEF = 71, 2          -- P2 Tank, P1 Infantry
local AX, AY, DX, DY = 9, 7, 10, 7

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
  for _ = 1, 80 do
    local cx, cy = cursor()
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left")
    elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end
local function hp_of(slot) return r16(ua(slot) + 4) % 128 end

local log = io.open(OUT .. "charge.log", "w")
local watching = false

local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end

emu.addMemoryCallback(function(addr, value)
  if not watching then return end
  local rel = addr - ARMY
  log:write(string.format("army rec=%d off=+0x%02X val=%s pc=%08X\n",
    math.floor(rel / STRIDE), rel % STRIDE, tostring(value), pc_of()))
  log:flush()
end, emu.callbackType.write, ARMY, ARMY + 5 * STRIDE - 1,
   emu.cpuType.gba, emu.memType.gbaMemory)

local function army_row(n)
  local a = ARMY + n * STRIDE
  return string.format("army%d funds=%d co=%d blk=%d meter=%d",
    n, r32(a), r8(a + 0x1d), r8(a + 0x1e), r16(a + 0x20))
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); local state = fh:read("*a"); fh:close()
  emu.loadSavestate(state); wait(30)

  local def_hp = ua(DEF) + 4
  emu.addMemoryCallback(function(addr, value)
    if not watching then return end
    log:write(string.format("defhp val=%s pc=%08X\n", tostring(value), pc_of()))
    log:flush()
  end, emu.callbackType.write, def_hp, def_hp + 1,
     emu.cpuType.gba, emu.memType.gbaMemory)

  log:write(string.format("gate=%d\n", r8(GATE)))
  for n = 1, 2 do log:write(army_row(n), "\n") end
  w8(ua(DEF) + 2, DX); w8(ua(DEF) + 3, DY)
  local en0 = hp_of(DEF)
  goto_tile(AX, AY)
  watching = true
  tap("a", 6, 30)            -- select Tank
  tap("a", 6, 45)            -- stay put
  tap("a", 6, 45)            -- Fire
  tap("a", 6, 30)            -- confirm target
  local moved = false
  for _ = 1, 40 do
    if hp_of(DEF) ~= en0 then moved = true; break end
    wait(15)
  end
  wait(400)
  watching = false
  log:write(string.format("battle_seen=%s att_hp=%d def_dmg=%d\n",
    tostring(moved), hp_of(ATT), en0 - hp_of(DEF)))
  for n = 1, 2 do log:write(army_row(n), "\n") end
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
      local ef = io.open(OUT .. "charge_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

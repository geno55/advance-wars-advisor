-- CO power activation probe. For each CO id: reload the powers-ON fixture,
-- write army2 +0x1D = co, meter = ROM base cost, ready = 1, drive the map
-- menu's Power item, and diff the world: army fields, weather, fog, every
-- unit record. Army-array writes are logged with the PC that made them, so
-- each one-shot effect names its code site.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local CO_BASE, CO_STRIDE = 0x08284A0C, 292
local WEATHER, FOG = 0x0300433C, 0x0300431D

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
  for _ = 1, 80 do
    local cx, cy = cursor()
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left")
    elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end

local log = io.open(OUT .. "power_act.log", "w")
local watching = false
local writes = 0
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end
emu.addMemoryCallback(function(addr, value)
  if not watching then return end
  writes = writes + 1
  if writes > 40 then return end
  local rel = addr - ARMY
  log:write(string.format("  W army rec=%d off=+0x%02X val=%s pc=%08X\n",
    math.floor(rel / STRIDE), rel % STRIDE, tostring(value), pc_of()))
end, emu.callbackType.write, ARMY, ARMY + 5 * STRIDE - 1,
   emu.cpuType.gba, emu.memType.gbaMemory)

local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "act-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end

local function snap_units()
  local t = {}
  for s = 0, 90 do
    local a = ua(s)
    local ty = r8(a)
    if ty ~= 0 then
      t[s] = { ty, r8(a + 1), r8(a + 2), r8(a + 3), r16(a + 4) }
    end
  end
  return t
end

local function army_row(n)
  local a = ARMY + n * STRIDE
  return string.format(
    "army%d co=%d blk=%d meter=%d ready=%d uses=%d",
    n, r8(a + 0x1d), r8(a + 0x1e), r32(a + 0x20), r8(a + 0x24), r8(a + 0x25))
end

local state
local function runco(co)
  emu.loadSavestate(state); wait(30)
  local a2 = ARMY + 2 * STRIDE
  w8(a2 + 0x1d, co)
  local cost = r32(CO_BASE + co * CO_STRIDE + 8)
  w32(a2 + 0x20, cost)
  w8(a2 + 0x24, 1)
  local pre = snap_units()
  log:write(string.format("== co=%d cost=%d weather=%d fog=%d\n",
    co, cost, r8(WEATHER), r8(FOG)))
  log:write("  pre  ", army_row(2), "\n")
  goto_tile(8, 7)
  writes = 0; watching = true
  tap("a", 6, 40)            -- map menu
  tap("down", 6, 16)
  tap("down", 6, 16)
  tap("a", 6, 60)            -- Power
  shot(co .. "-fire")
  for i = 1, 6 do tap("a", 6, 80) end   -- ride out banner/animation
  wait(300)
  watching = false
  shot(co .. "-after")
  log:write(string.format("  writes=%d\n", writes))
  log:write("  post ", army_row(2), "\n")
  log:write("  post ", army_row(1), "\n")
  log:write(string.format("  post weather=%d fog=%d\n", r8(WEATHER), r8(FOG)))
  local post = snap_units()
  for s = 0, 90 do
    local p, q = pre[s], post[s]
    if p and q then
      if p[5] ~= q[5] or p[2] ~= q[2] then
        log:write(string.format(
          "  unit%d hp %d->%d flags %02X->%02X\n", s, p[5], q[5], p[2], q[2]))
      end
    elseif p and not q then
      log:write(string.format("  unit%d GONE (was type=%d hp=%d)\n",
        s, p[1], p[5]))
    end
  end
  log:flush()
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  for _, co in ipairs({ 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 0 }) do
    runco(co)
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
      local ef = io.open(OUT .. "power_act_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

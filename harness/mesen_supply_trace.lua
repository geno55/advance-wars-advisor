-- Trace WHY the Supply menu item hides and whether the auto-supply walker
-- runs. Exec breakpoints with register reads:
--   menu predicate 0x0802DDFC: entry + the r0 of each sub-check
--   auto-supply:   walker 0x0802A8A4 (counted), scan 0x0802A6FC,
--                  per-tile 0x0802A688 (+ its two compares), refill 0x0802A784
--   property:      walker 0x0802A334 (counted, control)
-- Phase 1: case A replica (APC70 -> (13,3), needy MdTank67 at (13,4)), menu
-- built, cancelled with B. Phase 2: same move ACTED (Wait), End Turn twice.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1

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

local log = io.open(OUT .. "supply_trace.log", "w")
local tracing = false
local counts = {}
local function reg(name)
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st[name]) or -1
end
local function hook(addr, label, regs, max)
  counts[label] = 0
  emu.addMemoryCallback(function()
    if not tracing then return end
    counts[label] = counts[label] + 1
    if counts[label] > (max or 12) then return end
    local vals = {}
    for _, rn in ipairs(regs or {}) do
      vals[#vals + 1] = string.format("%s=%X", rn, reg("cpu." .. rn))
    end
    log:write(string.format("  X %s #%d %s\n", label, counts[label],
      table.concat(vals, " ")))
  end, emu.callbackType.exec, addr, addr, emu.cpuType.gba, emu.memType.gbaMemory)
end

-- menu predicate internals
hook(0x0802DDFC, "supply-pred-entry", {}, 6)
hook(0x0802DE02, "after-da6c", { "r0" }, 6)
hook(0x0802DE0C, "after-dd5c", { "r0" }, 6)
hook(0x0802DE1A, "after-supplier", { "r0" }, 6)
hook(0x0802DE34, "after-f80", { "r0" }, 6)
-- auto-supply chain
hook(0x0802A8A4, "supply-walker", {}, 3)
hook(0x0802A6FC, "adj-scan", { "r0" }, 12)
hook(0x0802A688, "tile-check", { "r0", "r1" }, 24)
hook(0x0802A6D0, "tile-army-cmp", { "r0", "r2" }, 24)
hook(0x0802A6DC, "tile-supplier-res", { "r0" }, 24)
hook(0x0802A784, "full-refill", {}, 8)
-- property walker control
hook(0x0802A334, "prop-walker", {}, 3)

local state
local function reload()
  emu.loadSavestate(state); wait(30)
end
local function set_fuel(slot, v) w8(ua(slot) + 6, v) end
local function set_ammo(slot, n)
  local v = r16(ua(slot) + 4)
  w16(ua(slot) + 4, v - (math.floor(v / 128) % 16) * 128 + n * 128)
end
local function urow(slot)
  local a = ua(slot)
  local v4 = r16(a + 4)
  return string.format("u%d type=%d flags=%02X x=%d y=%d hp=%d ammo=%d fuel=%02X",
    slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128,
    math.floor(v4 / 128) % 16, r8(a + 6))
end
local function end_turn()
  for attempt = 1, 2 do
    local pre = r16(0x03004424)
    goto_tile(2, 6)
    tap("a", 8, 50); tap("up", 8, 26); tap("a", 8, 70)
    for i = 1, 4 do tap("a", 8, 60) end
    wait(300)
    if r16(0x03004424) ~= pre then return true end
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  return false
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  log:write("== phase 1: menu build\n")
  reload()
  set_fuel(67, 5); set_ammo(67, 1)
  goto_tile(14, 2)
  tap("a", 8, 50)
  tap("down", 8, 26); tap("left", 8, 26)
  tracing = true
  tap("a", 8, 60)                          -- confirm destination, menu builds
  wait(60)
  tracing = false
  log:write(string.format("  cursor33AC=(%d,%d) sel=%08X\n",
    r16(0x030033AC), r16(0x030033AE), r32(0x03004464)))
  for _ = 1, 3 do tap("b", 8, 40) end

  log:write("== phase 2: turn start\n")
  reload()
  goto_tile(14, 2)
  tap("a", 8, 50)
  tap("down", 8, 26); tap("left", 8, 26)
  tap("a", 8, 60)                          -- confirm dest
  tap("a", 8, 70)                          -- Wait
  wait(90)
  log:write("  " .. urow(70) .. "\n")
  set_fuel(67, 5); set_ammo(67, 1)
  local ok = end_turn()
  log:write("  end P2 ok=" .. tostring(ok) .. "\n")
  tracing = true
  ok = end_turn()
  wait(600)
  tracing = false
  log:write("  end P1 ok=" .. tostring(ok) .. "\n")
  log:write("  post " .. urow(67) .. "\n")
  for label, c in pairs(counts) do
    log:write(string.format("  count %s = %d\n", label, c))
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
      local ef = io.open(OUT .. "supply_trace_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

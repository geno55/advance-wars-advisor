-- CO power meter probe, take 2: who writes army +0x20, and from where.
-- Phase A is a control: a write-watch on the defender Tank's HP halfword must
-- fire during the battle, or the watch machinery itself is broken and "no
-- meter writes" means nothing. Phase B repeats the attack with the CO gate
-- 0x03004318 = 1, in case charging is gated like the modifier fetch.
-- Both memTypes are tried for the callbacks, tagged in the log.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_1.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local GATE = 0x03004318
local TANK = 71
local MX, MY, EX, EY = 1, 6, 2, 6

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

local log = io.open(OUT .. "meter_watch.log", "w")
local statedump_done = false
local watching = false
local phase = "?"

local function pc_guess()
  local ok, st = pcall(emu.getState)
  if not ok then return "getState_failed" end
  if not statedump_done then
    statedump_done = true
    local kf = io.open(OUT .. "meter_statekeys.log", "w")
    for k, v in pairs(st) do kf:write(tostring(k), " = ", tostring(v), "\n") end
    kf:close()
  end
  local hits = {}
  for k, v in pairs(st) do
    local lk = tostring(k):lower()
    if lk:find("r15") or lk:find("pc") then
      hits[#hits + 1] = tostring(k) .. "=" .. string.format("%X", tonumber(v) or -1)
    end
  end
  return table.concat(hits, " ")
end

local function on_army(tag)
  return function(addr, value)
    if not watching then return end
    local rel = addr - ARMY
    local rec = math.floor(rel / STRIDE)
    local off = rel % STRIDE
    log:write(string.format("%s %s army rec=%d off=+0x%02X addr=%08X val=%s  %s\n",
      phase, tag, rec, off, addr, tostring(value), pc_guess()))
    log:flush()
  end
end
local function on_hp(tag)
  return function(addr, value)
    if not watching then return end
    log:write(string.format("%s %s tankhp addr=%08X val=%s  %s\n",
      phase, tag, addr, tostring(value), pc_guess()))
    log:flush()
  end
end

local function army_row(n)
  local a = ARMY + n * STRIDE
  return string.format(
    "%s army%d funds=%d co=%d blk=%d meter=%d w22=%d gate=%d",
    phase, n, r32(a), r8(a + 0x1d), r8(a + 0x1e), r16(a + 0x20), r16(a + 0x22),
    r8(GATE))
end

local state
local function attack_once()
  w8(ua(TANK) + 2, EX); w8(ua(TANK) + 3, EY)
  local en0 = hp_of(TANK)
  goto_tile(MX, MY)
  watching = true
  tap("a", 6, 30)
  tap("a", 6, 45)
  tap("a", 6, 45)
  tap("a", 6, 30)
  local moved = false
  for _ = 1, 40 do
    if hp_of(TANK) ~= en0 then moved = true; break end
    wait(15)
  end
  wait(280)
  watching = false
  log:write(string.format("%s battle_seen=%s dmg=%d\n",
    phase, tostring(moved), en0 - hp_of(TANK)))
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  emu.loadSavestate(state); wait(30)
  -- register watches now, after the state is in place
  local tank_hp = ua(TANK) + 4
  emu.addMemoryCallback(on_army("mem"), emu.callbackType.write,
    ARMY, ARMY + 5 * STRIDE - 1, emu.cpuType.gba, emu.memType.gbaMemory)
  emu.addMemoryCallback(on_hp("mem"), emu.callbackType.write,
    tank_hp, tank_hp + 1, emu.cpuType.gba, emu.memType.gbaMemory)
  pcall(function()
    emu.addMemoryCallback(on_army("dbg"), emu.callbackType.write,
      ARMY, ARMY + 5 * STRIDE - 1, emu.cpuType.gba, emu.memType.gbaDebug)
    emu.addMemoryCallback(on_hp("dbg"), emu.callbackType.write,
      tank_hp, tank_hp + 1, emu.cpuType.gba, emu.memType.gbaDebug)
  end)

  phase = "A"
  for n = 1, 2 do log:write(army_row(n), "\n") end
  attack_once()
  for n = 1, 2 do log:write(army_row(n), "\n") end

  phase = "B"
  emu.loadSavestate(state); wait(30)
  w8(GATE, 1)
  for n = 1, 2 do log:write(army_row(n), "\n") end
  attack_once()
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
      local ef = io.open(OUT .. "meter_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

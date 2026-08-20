-- Power mechanics probes, four independent cases from the powers-ON fixture:
--  A. Grit range: enumerate Artillery Fire targets at d=2..5, control vs power.
--  B. Max move: movement overlay screenshot, control vs power.
--  C. Expiry: activate Olaf, end both turns, log +0x1E/weather at each phase,
--     with write-PCs on army2+0x1E and the weather byte naming the clearers.
--  D. Eagle refresh: act the Tank, activate, read its acted bit.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local CO_BASE, CO_STRIDE = 0x08284A0C, 292
local WEATHER = 0x0300433C

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

local log = io.open(OUT .. "power_fx.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "fx-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end
local watching = false
emu.addMemoryCallback(function(addr, value)
  if not watching then return end
  log:write(string.format("  W blk1E val=%s pc=%08X\n", tostring(value), pc_of()))
end, emu.callbackType.write, ARMY + 2 * STRIDE + 0x1E, ARMY + 2 * STRIDE + 0x1E,
   emu.cpuType.gba, emu.memType.gbaMemory)
emu.addMemoryCallback(function(addr, value)
  if not watching then return end
  log:write(string.format("  W weather val=%s pc=%08X\n", tostring(value), pc_of()))
end, emu.callbackType.write, WEATHER, WEATHER,
   emu.cpuType.gba, emu.memType.gbaMemory)

local state
local function reload()
  emu.loadSavestate(state); wait(30)
end
local function activate(co)
  local a2 = ARMY + 2 * STRIDE
  if co then w8(a2 + 0x1d, co) end
  local id = r8(a2 + 0x1d)
  w32(a2 + 0x20, r32(CO_BASE + id * CO_STRIDE + 8))
  w8(a2 + 0x24, 1)
  goto_tile(8, 7)
  tap("a", 6, 40); tap("down", 6, 16); tap("down", 6, 16)
  tap("a", 6, 60)
  for i = 1, 6 do tap("a", 6, 80) end
  wait(240)
end
local function end_turn()
  -- End is the last item; up from the top wraps to it
  tap("a", 6, 40); tap("up", 6, 16); tap("a", 6, 60)
  for i = 1, 4 do tap("a", 6, 60) end
  wait(240)
end
local function phase_row(tag)
  local a2 = ARMY + 2 * STRIDE
  log:write(string.format(
    "%s: player=%d blk=%d meter=%d ready=%d uses=%d weather=%d\n",
    tag, math.floor(r8(0x03004424) / 32), r8(a2 + 0x1e), r32(a2 + 0x20),
    r8(a2 + 0x24), r8(a2 + 0x25), r8(WEATHER)))
end

local function enum_targets(tag)
  -- artillery at (14,4): select, stay, Fire, then walk the target ring
  goto_tile(14, 4)
  tap("a", 6, 40)          -- select
  tap("a", 6, 50)          -- stay
  shot(tag .. "-menu")
  tap("a", 6, 50)          -- first action item (Fire when targets exist)
  shot(tag .. "-targets")
  local seen = {}
  for i = 1, 8 do
    local x, y = cursor()
    seen[x .. "," .. y] = true
    tap("right", 6, 24)
  end
  local list = {}
  for k in pairs(seen) do list[#list + 1] = k end
  table.sort(list)
  log:write(tag .. " targets: " .. table.concat(list, "  ") .. "\n")
  tap("b", 6, 30); tap("b", 6, 30); tap("b", 6, 30); tap("b", 6, 30)
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  -- A. Grit range
  reload()
  w8(ua(2) + 2, 14); w8(ua(2) + 3, 7)    -- d=3
  w8(ua(5) + 2, 14); w8(ua(5) + 3, 8)    -- d=4
  w8(ua(7) + 2, 14); w8(ua(7) + 3, 9)    -- d=5
  w8(ua(1) + 2, 14); w8(ua(1) + 3, 6)    -- d=2
  log:write("== A control (no power, Andy)\n")
  enum_targets("ctrl")
  reload()
  w8(ua(2) + 2, 14); w8(ua(2) + 3, 7)
  w8(ua(5) + 2, 14); w8(ua(5) + 3, 8)
  w8(ua(7) + 2, 14); w8(ua(7) + 3, 9)
  w8(ua(1) + 2, 14); w8(ua(1) + 3, 6)
  log:write("== A grit power\n")
  activate(5)
  enum_targets("grit")

  -- B. Max movement overlay
  reload()
  log:write("== B max\n")
  goto_tile(9, 7)
  tap("a", 6, 50)
  shot("move-ctrl")
  tap("b", 6, 40)
  reload()
  activate(2)
  goto_tile(9, 7)
  tap("a", 6, 50)
  shot("move-max")
  tap("b", 6, 40)

  -- C. Expiry: Olaf snow + blk lifetime across turns
  reload()
  log:write("== C expiry\n")
  watching = true
  activate(3)
  phase_row("after-activate")
  end_turn()
  phase_row("p1-turn")
  shot("p1-turn")
  end_turn()
  phase_row("p2-turn2")
  shot("p2-turn2")
  watching = false

  -- D. Eagle refresh on an acted Tank
  reload()
  log:write("== D eagle\n")
  goto_tile(9, 7)
  tap("a", 6, 40)          -- select Tank
  tap("left", 6, 20)       -- move one tile west (8,7 empty)
  tap("a", 6, 50)          -- confirm destination
  tap("a", 6, 60)          -- Wait (top item, no enemy adjacent)
  wait(60)
  local tank = ua(71)
  log:write(string.format("tank acted=%d flags=%02X\n",
    r8(tank + 1) % 2, r8(tank + 1)))
  activate(8)
  log:write(string.format("post-eagle acted=%d flags=%02X x=%d\n",
    r8(tank + 1) % 2, r8(tank + 1), r8(tank + 2)))

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
      local ef = io.open(OUT .. "power_fx_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

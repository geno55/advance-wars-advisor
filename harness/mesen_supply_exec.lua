-- Drive the Supply MENU command for real. Round 3 showed the APC action menu
-- is [Drop, Supply, Wait] with cargo aboard (Supply hidden when no adjacent
-- unit needs anything), so Supply = down, A.
--  M1 in place, two needy neighbours (Recon70 at (13,7), Tank71 at (12,6)),
--     cargo 66 written low: who gets filled, is it free, does the APC act?
--  M2 after a move: APC69 -> (10,7) next to needy Tank71 at (9,7).
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

local log = io.open(OUT .. "supply_exec.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "exec-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end
local watching = false
local ub0 = nil
local wcount = 0
emu.addMemoryCallback(function(addr, value)
  if not watching or not ub0 then return end
  local off = addr - ub0
  if off < 0 or off >= 130 * 12 then return end
  local slot = math.floor(off / 12)
  local field = off % 12
  if field == 1 then return end
  if slot < 64 then return end
  wcount = wcount + 1
  if field == 6 and wcount > 6 and value ~= 0 then
    -- the +1/point refill loops flood the log; keep first hits and endpoints
    if value % 16 ~= 0 then return end
  end
  log:write(string.format("  W u%d+%d=%02X pc=%08X\n", slot, field, value,
    pc_of()))
end, emu.callbackType.write, 0x02019F34, 0x0201A54C,
   emu.cpuType.gba, emu.memType.gbaMemory)
local fundwatch = false
emu.addMemoryCallback(function(addr, value)
  if not fundwatch then return end
  log:write(string.format("  W funds2 val=%d pc=%08X\n", value, pc_of()))
end, emu.callbackType.write, ARMY + 2 * STRIDE, ARMY + 2 * STRIDE + 3,
   emu.cpuType.gba, emu.memType.gbaMemory)

local state
local function reload()
  emu.loadSavestate(state); wait(30)
  ub0 = r32(UNIT_PTR)
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
local function move(slot, x, y, dirs, dx, dy)
  for attempt = 1, 2 do
    goto_tile(x, y)
    tap("a", 8, 50)
    for _, d in ipairs(dirs) do tap(d, 8, 26) end
    tap("a", 8, 60)
    tap("a", 8, 70)
    wait(90)
    local a = ua(slot)
    if r8(a + 2) == dx and r8(a + 3) == dy then return true end
    log:write(string.format("  move u%d attempt %d failed (at %d,%d)\n",
      slot, attempt, r8(a + 2), r8(a + 3)))
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  return false
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  -- M1 ------------------------------------------------------------------
  reload()
  log:write("== M1 supply in place, two neighbours\n")
  local ok = move(70, 14, 2, { "left", "down", "down", "down", "down", "down" },
    13, 7)
  log:write("  move70 ok=" .. tostring(ok) .. "\n")
  ok = move(71, 9, 7, { "right", "right", "up", "right" }, 12, 6)
  log:write("  move71 ok=" .. tostring(ok) .. "\n")
  set_fuel(70, 5)
  set_fuel(71, 7); set_ammo(71, 2)
  set_fuel(66, 10)
  log:write(string.format("  funds pre=%d\n", r32(ARMY + 2 * STRIDE)))
  goto_tile(12, 7)
  tap("a", 8, 50)
  tap("a", 8, 60)
  wait(40)
  watching = true; fundwatch = true; wcount = 0
  tap("down", 8, 26)
  tap("a", 8, 90)                          -- Supply
  wait(200)
  watching = false; fundwatch = false
  shot("M1-after")
  log:write("  post " .. urow(70) .. "\n")
  log:write("  post " .. urow(71) .. "\n")
  log:write("  post " .. urow(66) .. "\n")
  log:write("  post " .. urow(69) .. "\n")
  log:write(string.format("  funds post=%d\n", r32(ARMY + 2 * STRIDE)))

  -- M2 ------------------------------------------------------------------
  reload()
  log:write("== M2 supply after a move\n")
  set_fuel(71, 5); set_ammo(71, 2)
  goto_tile(12, 7)
  tap("a", 8, 50)
  tap("left", 8, 26); tap("left", 8, 26)
  tap("a", 8, 60)
  wait(40); shot("M2-menu")
  watching = true; wcount = 0
  tap("down", 8, 26)
  tap("a", 8, 90)
  wait(200)
  watching = false
  shot("M2-after")
  log:write("  post " .. urow(69) .. "\n")
  log:write("  post " .. urow(71) .. "\n")
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
      local ef = io.open(OUT .. "supply_exec_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

-- APC Supply probes, round 3 -- with the REAL APC (slot 69, RAM type 7,
-- at (12,7), carrying Infantry 66). Round 2's "APC" was the Recon (type 6):
-- RAM types are 1-based ids. Cases, fresh reload each:
--  S1 menu: APC69 -> (10,7), needy Tank71 at (9,7): screenshot menu, tap
--     the top item with watches on the whole unit array.
--  S2 both sides + cargo: Recon70 -> (13,7), Tank71 -> (12,6), both written
--     needy, cargo 66 written low; APC69 supplies IN PLACE at (12,7).
--  S3 full-neighbour control: APC69 -> (10,7), Tank71 written full: menu?
--  S4 auto-supply at turn start: after S2-style moves, rewrite both needy,
--     End Turn twice, watch the refills and their PCs (order vs burn).
--  S5 crash-vs-supply order: Tank71 -> (12,6) then typed BCopter fuel 1
--     (adjacent to APC69); Mech67 typed BCopter fuel 1 far away (control).
--     End Turn twice: who is still alive?
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local MAP_PTR = 0x08282CB4
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

local log = io.open(OUT .. "supply_apc.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "apc-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end
local watching = false
local ub0 = nil
emu.addMemoryCallback(function(addr, value)
  if not watching or not ub0 then return end
  local off = addr - ub0
  if off < 0 or off >= 130 * 12 then return end
  local slot = math.floor(off / 12)
  local field = off % 12
  if field == 1 then return end
  if slot < 64 then return end
  log:write(string.format("  W u%d+%d=%02X pc=%08X\n", slot, field, value,
    pc_of()))
end, emu.callbackType.write, 0x02019F34, 0x0201A54C,
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
-- verified real move ending with Wait (top item on a plain arrival)
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

  -- S1 ------------------------------------------------------------------
  reload()
  log:write("== S1 menu after move\n")
  set_fuel(71, 5); set_ammo(71, 2)
  goto_tile(12, 7)
  tap("a", 8, 50)
  tap("left", 8, 26); tap("left", 8, 26)
  tap("a", 8, 60)
  wait(40); shot("S1-menu")
  watching = true
  tap("a", 8, 90)
  wait(150)
  watching = false
  shot("S1-after")
  log:write("  post " .. urow(69) .. "\n")
  log:write("  post " .. urow(71) .. "\n")
  log:write("  post " .. urow(66) .. "\n")

  -- S2 ------------------------------------------------------------------
  reload()
  log:write("== S2 both sides, in place\n")
  local ok = move(70, 14, 2, { "left", "down", "down", "down", "down", "down" },
    13, 7)
  log:write("  move70 ok=" .. tostring(ok) .. "\n")
  ok = move(71, 9, 7, { "right", "right", "up", "right" }, 12, 6)
  log:write("  move71 ok=" .. tostring(ok) .. "\n")
  set_fuel(70, 5)
  set_fuel(71, 7); set_ammo(71, 2)
  set_fuel(66, 10)
  goto_tile(12, 7)
  tap("a", 8, 50)
  tap("a", 8, 60)                          -- destination = own tile
  wait(40); shot("S2-menu")
  watching = true
  tap("a", 8, 90)
  wait(150)
  watching = false
  shot("S2-after")
  log:write("  post " .. urow(70) .. "\n")
  log:write("  post " .. urow(71) .. "\n")
  log:write("  post " .. urow(66) .. "\n")
  log:write("  post " .. urow(69) .. "\n")

  -- S3 ------------------------------------------------------------------
  reload()
  log:write("== S3 full neighbour control\n")
  set_fuel(71, 70); set_ammo(71, 9)
  goto_tile(12, 7)
  tap("a", 8, 50)
  tap("left", 8, 26); tap("left", 8, 26)
  tap("a", 8, 60)
  wait(40); shot("S3-menu")
  for _ = 1, 3 do tap("b", 8, 40) end

  -- S4 ------------------------------------------------------------------
  reload()
  log:write("== S4 auto-supply at turn start\n")
  ok = move(70, 14, 2, { "left", "down", "down", "down", "down", "down" },
    13, 7)
  log:write("  move70 ok=" .. tostring(ok) .. "\n")
  ok = move(71, 9, 7, { "right", "right", "up", "right" }, 12, 6)
  log:write("  move71 ok=" .. tostring(ok) .. "\n")
  set_fuel(70, 5)
  set_fuel(71, 7); set_ammo(71, 2)
  set_fuel(66, 10)
  log:write("  pre " .. urow(70) .. "\n")
  log:write("  pre " .. urow(71) .. "\n")
  ok = end_turn()
  log:write("  end P2 ok=" .. tostring(ok) .. "\n")
  watching = true
  ok = end_turn()
  wait(600)
  watching = false
  log:write("  end P1 ok=" .. tostring(ok) .. "\n")
  log:write("  post " .. urow(70) .. "\n")
  log:write("  post " .. urow(71) .. "\n")
  log:write("  post " .. urow(66) .. "\n")

  -- S5 ------------------------------------------------------------------
  reload()
  log:write("== S5 crash vs supply order\n")
  ok = move(71, 9, 7, { "right", "right", "up", "right" }, 12, 6)
  log:write("  move71 ok=" .. tostring(ok) .. "\n")
  w8(ua(71), 19); set_fuel(71, 1)          -- BCopter, fuel 1, adjacent APC69
  w8(ua(67), 19); set_fuel(67, 1)          -- BCopter, fuel 1, far control
  log:write("  pre " .. urow(71) .. "\n")
  log:write("  pre " .. urow(67) .. "\n")
  ok = end_turn()
  log:write("  end P2 ok=" .. tostring(ok) .. "\n")
  watching = true
  ok = end_turn()
  wait(600)
  watching = false
  log:write("  end P1 ok=" .. tostring(ok) .. "\n")
  shot("S5-after")
  log:write("  post " .. urow(71) .. "\n")
  log:write("  post " .. urow(67) .. "\n")
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
      local ef = io.open(OUT .. "supply_apc_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

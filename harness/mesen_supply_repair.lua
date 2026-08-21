-- Property repair/resupply + daily burn sweeps. Each case reloads the slot-2
-- fixture, writes a board, ends both turns, and reads the result at P2's
-- turn start. Funds sweeps write funds from an exec hook on the property
-- walker entry (0x0802A334) because income lands first at 0x0802416A.
-- The repair formula under test (read off 0x08029D9C):
--   per bar: (cost/10 + pool[+2]) * co_hdr[+0x2D] / 100, hp += 10 (cap 100),
--   always exit by snapping hp up to bars*10; broke -> snap and stop;
--   hp 91..100 -> free exact 100. Charge only when [0x03004357] == 0.
-- Burn under test (0x08023978): stats[+0x38+terrain] (flat), dived -> 5,
--   Eagle air -2, no burn on own service terrain (0x08282EFE), loaded
--   exempt, fuel 0 kills air AND naval (remover 0x080243D8).
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local MAP_PTR = 0x08282CB4
local CURX, CURY = 0x030033F0, 0x030033F1
local FUNDS2 = ARMY + 2 * STRIDE

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

local log = io.open(OUT .. "supply_repair.log", "w")
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end

-- unit write watch with run-length suppression of point-by-point refills
local watching = false
local ub0 = nil
local last = { slot = -1, field = -1, pc = -1, n = 0, v = 0 }
local function flushrun()
  if last.n > 1 then
    log:write(string.format("  W u%d+%d ... x%d ... =%02X pc=%08X\n",
      last.slot, last.field, last.n, last.v, last.pc))
  end
  last.n = 0
end
emu.addMemoryCallback(function(addr, value)
  if not watching or not ub0 then return end
  local off = addr - ub0
  if off < 0 or off >= 130 * 12 then return end
  local slot = math.floor(off / 12)
  local field = off % 12
  if field == 1 then return end
  if slot < 64 then return end
  local pc = pc_of()
  if slot == last.slot and field == last.field and pc == last.pc then
    last.n = last.n + 1; last.v = value
    return
  end
  flushrun()
  last = { slot = slot, field = field, pc = pc, n = 1, v = value }
  log:write(string.format("  W u%d+%d=%02X pc=%08X\n", slot, field, value, pc))
end, emu.callbackType.write, 0x02019F34, 0x0201A54C,
   emu.cpuType.gba, emu.memType.gbaMemory)
local fundwatch = false
emu.addMemoryCallback(function(addr, value)
  if not fundwatch then return end
  log:write(string.format("  W funds2 val=%d pc=%08X\n", value, pc_of()))
end, emu.callbackType.write, FUNDS2, FUNDS2 + 3,
   emu.cpuType.gba, emu.memType.gbaMemory)
-- one-shot funds override, fired at the property walker's first tick
local override = { armed = false, value = 0 }
emu.addMemoryCallback(function()
  if not override.armed then return end
  override.armed = false
  w32(FUNDS2, override.value)
  log:write(string.format("  [funds forced to %d at prop walker]\n",
    override.value))
end, emu.callbackType.exec, 0x0802A334, 0x0802A334,
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
local function set_hp(slot, hp)
  local v = r16(ua(slot) + 4)
  w16(ua(slot) + 4, v - (v % 128) + hp)
end
local function set_terr(x, y, v)
  local map = r32(MAP_PTR)
  w8(map + 0x1432 + r16(map + 0x4682 + y * 2) + x, v)
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

local function run_case(name, setup, slots)
  reload()
  log:write("== " .. name .. "\n")
  setup()
  for _, s in ipairs(slots) do log:write("  pre  " .. urow(s) .. "\n") end
  log:write(string.format("  pre  funds=%d free4357=%d\n",
    r32(FUNDS2), r8(0x03004357)))
  local ok = end_turn()
  if not ok then log:write("  END-TURN P2 FAILED\n"); return end
  watching = true; fundwatch = true
  ok = end_turn()
  wait(600)
  flushrun()
  watching = false; fundwatch = false
  if not ok then log:write("  END-TURN P1 FAILED\n"); return end
  for _, s in ipairs(slots) do log:write("  post " .. urow(s) .. "\n") end
  log:write(string.format("  post funds=%d\n", r32(FUNDS2)))
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  -- R1: baseline free repair + the burn menagerie
  run_case("R1 free repair 45->70; burns copter2 fighter5 sub1", function()
    set_terr(9, 7, 0x46); set_hp(71, 45); set_fuel(71, 20); set_ammo(71, 2)
    w8(ua(67), 19); set_fuel(67, 50)       -- BCopter on plain
    w8(ua(68), 17); set_fuel(68, 50)       -- Bomber at (11,3)
    w8(ua(70), 24); set_terr(14, 2, 0x07); set_fuel(70, 50)  -- Sub on Sea
  end, { 71, 67, 68, 70 })

  -- R2: charged repair, plus dived sub burn
  run_case("R2 charged 45->70 (-1400); dived sub burns 5", function()
    set_terr(9, 7, 0x46); set_hp(71, 45); set_ammo(71, 2)
    w8(0x03004357, 0)
    w8(ua(70), 24); set_terr(14, 2, 0x07); set_fuel(70, 50)
    w8(ua(70) + 1, r8(ua(70) + 1) + 0x20)  -- dive bit
  end, { 71, 70 })

  -- R3: broke (funds 0 at walker), plus Eagle air burn -2
  run_case("R3 broke snap 45->50; Eagle copter burn 0", function()
    set_terr(9, 7, 0x46); set_hp(71, 45)
    w8(0x03004357, 0)
    override.armed = true; override.value = 0
    w8(ARMY + 2 * STRIDE + 0x1d, 8)        -- Eagle
    w8(ua(67), 19); set_fuel(67, 50)       -- BCopter
    w8(ua(68), 17); set_fuel(68, 50)       -- Bomber
  end, { 71, 67, 68 })

  -- R4: exactly one bar affordable, plus naval sinks at fuel 0
  run_case("R4 funds 700: 45->55->snap 60; sub fuel1 sinks, ground survives",
  function()
    set_terr(9, 7, 0x46); set_hp(71, 45)
    w8(0x03004357, 0)
    override.armed = true; override.value = 700
    w8(ua(70), 24); set_terr(14, 2, 0x07); set_fuel(70, 1)   -- sub, sinks
    set_fuel(72, 0)                                          -- Rockets, lives
  end, { 71, 70, 72 })

  -- R5: one bar plus change
  run_case("R5 funds 1050: 45->60, 350 left", function()
    set_terr(9, 7, 0x46); set_hp(71, 45)
    w8(0x03004357, 0)
    override.armed = true; override.value = 1050
  end, { 71 })

  -- R6: 91..100 free top-up; loaded copter exempt from burn
  run_case("R6 hp95 -> free 100; loaded copter no burn", function()
    set_terr(9, 7, 0x46); set_hp(71, 95)
    w8(0x03004357, 0)
    w8(ua(66), 19); set_fuel(66, 50)       -- cargo in APC69, typed BCopter
  end, { 71, 66 })

  -- R7: cap then snap, both bars charged
  run_case("R7 hp81 -> 100, -1400", function()
    set_terr(9, 7, 0x46); set_hp(71, 81)
    w8(0x03004357, 0)
  end, { 71 })

  -- R8: Kanbei pays 120%
  run_case("R8 kanbei 45->70, -1680", function()
    set_terr(9, 7, 0x46); set_hp(71, 45)
    w8(0x03004357, 0)
    w8(ARMY + 2 * STRIDE + 0x1d, 6)        -- Kanbei
  end, { 71 })

  -- R9: air serviced on own airport, no burn there
  run_case("R9 copter on own airport: 45->70 -1800, refuel 99, no burn",
  function()
    set_terr(9, 7, 0x4A)
    w8(ua(71), 19); set_hp(71, 45); set_fuel(71, 20); set_ammo(71, 2)
    w8(0x03004357, 0)
  end, { 71 })

  -- R10: air on own city: nothing but the burn
  run_case("R10 copter on own city: no service, burns 2", function()
    set_terr(9, 7, 0x46)
    w8(ua(71), 19); set_hp(71, 45); set_fuel(71, 20)
    w8(0x03004357, 0)
  end, { 71 })

  -- R11: dived sub on own port: no burn, serviced anyway
  run_case("R11 dived sub on own port: no burn, refuel to 60", function()
    set_terr(9, 7, 0x4B)
    w8(ua(71), 24); set_hp(71, 45); set_fuel(71, 30)
    w8(ua(71) + 1, r8(ua(71) + 1) + 0x20)
    w8(0x03004357, 0)
  end, { 71 })

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
      local ef = io.open(OUT .. "supply_repair_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

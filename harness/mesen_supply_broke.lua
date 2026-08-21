-- Rerun of the broke/partial repair rows. The previous run's funds override
-- fired on the property walker's first ENTRY, which precedes the income
-- write at 0x0802416A -- income then refilled the treasury and every "broke"
-- case paid in full. The repair routine re-reads funds per bar, so the
-- override now fires at 0x08029D9C entry (one-shot), after income.
--  B1 funds 0:    expect 45 -> 50 (snap only), no spend
--  B2 funds 700:  expect 45 -> 55 -> snap 60, funds 0
--  B3 funds 1050: expect 45 -> 60, funds 350
--  B4 Fighter burn -5; written fuel high bit survives the turn
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

local log = io.open(OUT .. "supply_broke.log", "w")
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end
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
  local pc = pc_of()
  if pc == 0x08029FB4 then return end       -- UI counter churn
  log:write(string.format("  W funds2 val=%d pc=%08X\n", value, pc))
end, emu.callbackType.write, FUNDS2, FUNDS2 + 3,
   emu.cpuType.gba, emu.memType.gbaMemory)
local override = { armed = false, value = 0 }
emu.addMemoryCallback(function()
  if not override.armed then return end
  override.armed = false
  w32(FUNDS2, override.value)
  log:write(string.format("  [funds forced to %d at repair entry]\n",
    override.value))
end, emu.callbackType.exec, 0x08029D9C, 0x08029D9C,
   emu.cpuType.gba, emu.memType.gbaMemory)

local state
local function reload()
  emu.loadSavestate(state); wait(30)
  ub0 = r32(UNIT_PTR)
end
local function set_fuel(slot, v) w8(ua(slot) + 6, v) end
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

  run_case("B1 broke: snap 45->50, no spend", function()
    set_terr(9, 7, 0x46); set_hp(71, 45)
    w8(0x03004357, 0)
    override.armed = true; override.value = 0
  end, { 71 })

  run_case("B2 funds 700: 45->55->snap 60, funds 0", function()
    set_terr(9, 7, 0x46); set_hp(71, 45)
    w8(0x03004357, 0)
    override.armed = true; override.value = 700
  end, { 71 })

  run_case("B3 funds 1050: 45->60, 350 left", function()
    set_terr(9, 7, 0x46); set_hp(71, 45)
    w8(0x03004357, 0)
    override.armed = true; override.value = 1050
  end, { 71 })

  run_case("B4 fighter burns 5; fuel high bit survives", function()
    w8(ua(67), 16); set_fuel(67, 50)        -- Fighter on plain
    set_fuel(72, 0x80 + 50)                 -- Rockets, high bit set
  end, { 67, 72 })

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
      local ef = io.open(OUT .. "supply_broke_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

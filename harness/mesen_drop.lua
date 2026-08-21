-- Drop probes (DERIVATION 35). APC69 at (12,7) carries Infantry 66.
--  D1 in place: Drop is the top item; pick it, confirm the selector's
--     default tile; watch 66 (+1 flags, +2/+3 position) and 69 (+1, +7).
--  D2 after a move to (10,7): same, next to the needy Tank (menu has Supply).
--  D3 Sea written at (11,7),(13,7), Tank71 real-moved to (12,6): the
--     selector should offer only (12,8).
--  D4 Sea on all four neighbours: Drop should vanish from the menu.
--  D5 cargo typed Tank, Mountain at (12,6),(12,8), Sea at (11,7): a Tank
--     cannot stand on a mountain, so only (13,7) should be offered.
--  D5b cargo Infantry, Sea at (11,7),(13,7), Mountain at (12,6),(12,8):
--     both mountains offered (foot may).
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local UNIT_PTR = 0x08282CB8
local MAP_PTR = 0x08282CB4
local CURX, CURY = 0x030033F0, 0x030033F1

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

local log = io.open(OUT .. "drop2.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "drop-" .. tag .. ".png", "wb")
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
  if slot ~= 66 and slot ~= 69 then return end
  log:write(string.format("  W u%d+%d=%02X pc=%08X\n", slot, field, value,
    pc_of()))
end, emu.callbackType.write, 0x02019F34, 0x0201A54C,
   emu.cpuType.gba, emu.memType.gbaMemory)

local state
local function reload()
  emu.loadSavestate(state); wait(30)
  ub0 = r32(UNIT_PTR)
end
local function set_terr(x, y, v)
  local map = r32(MAP_PTR)
  w8(map + 0x1432 + r16(map + 0x4682 + y * 2) + x, v)
end
local function urow(slot)
  local a = ua(slot)
  local v4 = r16(a + 4)
  return string.format(
    "u%d type=%d flags=%02X x=%d y=%d hp=%d ammo=%d fuel=%02X cargo7=%d cargo8=%d",
    slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128,
    math.floor(v4 / 128) % 16, r8(a + 6), r8(a + 7), r8(a + 8))
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
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  return false
end
-- select APC69 (optionally with move taps), open the action menu, pick the
-- top item (Drop), screenshot the selector, confirm with A under watches
local function drop_case(name, dirs, confirm)
  goto_tile(12, 7)
  tap("a", 8, 50)
  for _, d in ipairs(dirs or {}) do tap(d, 8, 26) end
  tap("a", 8, 60)                          -- destination
  wait(40); shot(name .. "-menu")
  if not confirm then
    for _ = 1, 3 do tap("b", 8, 40) end
    return
  end
  tap("a", 8, 60)                          -- Drop (top)
  wait(40); shot(name .. "-selector")
  watching = true
  tap("a", 8, 90)                          -- confirm default tile
  wait(200)
  watching = false
  shot(name .. "-after")
  log:write("  post " .. urow(66) .. "\n  post " .. urow(69) .. "\n")
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  reload(); log:write("== D1 in place\n")
  log:write("  pre  " .. urow(66) .. "\n  pre  " .. urow(69) .. "\n")
  drop_case("D1", nil, true)

  reload(); log:write("== D2 after move to (10,7)\n")
  drop_case("D2", { "left", "left" }, true)

  reload(); log:write("== D3 sea E/W, Tank north: only (12,8)\n")
  set_terr(11, 7, 7); set_terr(13, 7, 7)
  log:write("  move71 ok=" .. tostring(move(71, 9, 7,
    { "right", "right", "up", "right" }, 12, 6)) .. "\n")
  drop_case("D3", nil, true)

  reload(); log:write("== D4 sea all round: no Drop\n")
  set_terr(11, 7, 7); set_terr(13, 7, 7); set_terr(12, 6, 7); set_terr(12, 8, 7)
  drop_case("D4", nil, false)

  reload(); log:write("== D5 cargo typed Tank, mountains N/S, sea W\n")
  w8(ua(66), 5)
  set_terr(12, 6, 3); set_terr(12, 8, 3); set_terr(11, 7, 7)
  drop_case("D5", nil, true)

  reload(); log:write("== D6 origin tile: APC to (11,7), sea at (10,7),(11,6),(11,8)\n")
  set_terr(10, 7, 7); set_terr(11, 6, 7); set_terr(11, 8, 7)
  drop_case("D6", { "left" }, true)

  reload(); log:write("== D5b cargo Infantry, sea E/W, mountains N/S\n")
  set_terr(11, 7, 7); set_terr(13, 7, 7); set_terr(12, 6, 3); set_terr(12, 8, 3)
  drop_case("D5b", nil, true)
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
      local ef = io.open(OUT .. "drop_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

-- Dump the three Sonja vision captures as loader-compatible fixtures:
-- fog written on, P2's CO set to Andy / Sonja / Sonja+power, a real Tank move
-- forcing the recompute, then the whole board and the game's own vision array
-- serialized in the tests/fixtures schema.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/tests/fixtures/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local FOG, WEATHER = 0x0300431D, 0x0300433C
local MAP = 0x02016C2A
local VIS1, VIS2 = 0x0201763A, 0x02017B42
-- The height byte next to the width reads 13 here and is wrong: terrain rows
-- 10..12 are all zeroes and the vision array never lights them. The board is
-- 15x10 -- both HQs sit inside rows 0..9.
local W, H = 15, 10

local NAMES = { [1]="Infantry", [2]="Mech", [3]="MdTank", [5]="Tank",
  [6]="Recon", [7]="APC", [10]="Artillery", [11]="Rockets", [14]="AntiAir",
  [15]="Missiles", [16]="Fighter", [17]="Bomber", [19]="BCopter",
  [20]="TCopter", [21]="Battleship", [22]="Cruiser", [23]="Lander",
  [24]="Sub" }

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
  for _ = 1, 90 do
    local cx, cy = cursor()
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left")
    elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end

local function dump_fixture(path, note)
  local out = {}
  local function add(s) out[#out + 1] = s end
  add('{')
  add(string.format('  "_comment": ["%s"],', note))
  add(string.format('  "width": %d, "height": %d,', W, H))
  add(string.format('  "day": %d, "active_player": %d,',
    r16(0x03004420), math.floor(r8(0x03004424) / 32)))
  add(string.format('  "weather_index": %d,', r8(WEATHER)))
  add(string.format('  "fog": %s, "fog_raw": %d,',
    r8(FOG) ~= 0 and "true" or "false", r8(FOG)))
  local armies = {}
  for p = 1, 4 do
    local a = ARMY + p * STRIDE
    armies[#armies + 1] = string.format(
      '    {"player": %d, "funds": %d, "income": %d, "power": %d, ' ..
      '"co_id": %d, "power_active": %s}',
      p, r32(a), r32(a + 8), r32(a + 0x20), r8(a + 0x1d),
      r8(a + 0x1e) ~= 0 and "true" or "false")
  end
  add('  "armies": [')
  add(table.concat(armies, ",\n"))
  add('  ],')
  local units = {}
  for s = 1, 90 do
    local a = ua(s)
    local ty = r8(a)
    if ty ~= 0 and NAMES[ty] then
      local fl = r8(a + 1)
      local raw = r16(a + 4)
      local loaded = (fl % 16 >= 8) or (fl % 8 - fl % 2 == 2)
      units[#units + 1] = string.format(
        '    {"slot": %d, "player": %d, "type": "%s", "x": %d, "y": %d, ' ..
        '"hp": %d, "ammo": %d, "capture": %d, "fuel": 99, "acted": %s, ' ..
        '"carrying": false, "loaded": %s}',
        s, s <= 64 and 1 or 2, NAMES[ty], r8(a + 2), r8(a + 3),
        raw % 128, math.floor(raw / 128) % 16, math.floor(raw / 2048),
        fl % 2 == 1 and "true" or "false", loaded and "true" or "false")
    end
  end
  add('  "units": [')
  add(table.concat(units, ",\n"))
  add('  ],')
  local rows = {}
  for y = 0, H - 1 do
    local t, own = {}, {}
    for x = 0, W - 1 do
      local b = r8(MAP + y * W + x)
      t[#t + 1] = tostring(b % 32)
      own[#own + 1] = tostring(math.floor(b / 32))
    end
    rows[#rows + 1] = string.format(
      '    {"y": %d, "t": [%s], "owner": [%s]}',
      y, table.concat(t, ","), table.concat(own, ","))
  end
  add('  "terrain": [')
  add(table.concat(rows, ",\n"))
  add('  ],')
  local vis, agree = {}, true
  for y = 0, H - 1 do
    local row = {}
    for x = 0, W - 1 do
      local v = r8(VIS1 + y * W + x)
      if v ~= r8(VIS2 + y * W + x) then agree = false end
      row[#row + 1] = tostring(v)
    end
    vis[#vis + 1] = "    [" .. table.concat(row, ",") .. "]"
  end
  add('  "vision": [')
  add(table.concat(vis, ",\n"))
  add('  ],')
  add(string.format('  "vision_addr": "0x0201763A",'))
  add(string.format('  "vision_copies_agree": %s',
    agree and "true" or "false"))
  add('}')
  local fh = io.open(path, "w")
  fh:write(table.concat(out, "\n"), "\n")
  fh:close()
end

local state
local function case(fname, co, blk, note)
  emu.loadSavestate(state); wait(30)
  w8(FOG, 1)
  local a2 = ARMY + 2 * STRIDE
  w8(a2 + 0x1d, co)
  if blk then w8(a2 + 0x1e, 1) end
  goto_tile(9, 7)
  tap("a", 6, 40)
  tap("left", 6, 20)
  tap("a", 6, 50)
  tap("a", 6, 60)
  wait(90)
  dump_fixture(OUT .. fname, note)
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  case("sonja_vision_andy.json", 1, false,
    "Control: P2 Andy under written fog, Tank moved to (8,7). DERIVATION 28")
  case("sonja_vision_sonja.json", 7, false,
    "P2 Sonja under written fog: pool +8 vision, +1 all but Sub. DERIVATION 28")
  case("sonja_vision_power.json", 7, true,
    "P2 Sonja with power block written: +3 vision and concealment pierced. DERIVATION 28")
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
      local ef = io.open(
        "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/sonja_fix_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

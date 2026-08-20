-- Confirm the two code-read vision rules (DERIVATION 28) by measurement:
--   rain_penalty          fog on, weather byte written to 2 (rain), Tank
--                         moved to force the recompute.
--   air_over_concealment  Wood written UNDER P1's unit 2 at (6,3), its TYPE
--                         written to BCopter (type writes are transparent;
--                         position writes are not -- the tile index would not
--                         follow), P2's Mech 68 real-moved to the mountain at
--                         (9,3): a distance-3 viewer with mountain vision 4.
--                         Control: same board, same move, type left Infantry.
-- Each case dumps a loader-schema fixture with the game's own count array.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/tests/fixtures/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local FOG, WEATHER = 0x0300431D, 0x0300433C
local MAP = 0x02016C2A
local VIS1, VIS2 = 0x0201763A, 0x02017B42
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

local log = io.open(
  "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/vision_rules.log", "w")

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
  add('  "vision_addr": "0x0201763A",')
  add(string.format('  "vision_copies_agree": %s',
    agree and "true" or "false"))
  add('}')
  local fh = io.open(path, "w")
  fh:write(table.concat(out, "\n"), "\n")
  fh:close()
end

-- The map cursor bytes do NOT track inside move-select mode, so the
-- destination is driven by counted direction taps, and the move is verified
-- off the unit record afterwards.
local function real_move(slot, fx, fy, dir, steps)
  goto_tile(fx, fy)
  tap("a", 6, 40)
  for _ = 1, steps do tap(dir, 6, 20) end
  tap("a", 6, 50)
  tap("a", 6, 60)          -- Wait
  wait(90)
  return r8(ua(slot) + 2), r8(ua(slot) + 3)
end

local state
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  -- rain: same board and Tank move as the sonja captures, weather written 2
  emu.loadSavestate(state); wait(30)
  w8(FOG, 1)
  w8(WEATHER, 2)
  local tx, ty = real_move(71, 9, 7, "left", 1)
  log:write(string.format("rain: weather=%d tank=%d,%d vis(8,7)=%d\n",
    r8(WEATHER), tx, ty, r8(VIS1 + 7 * W + 8)))
  dump_fixture(OUT .. "fog_vision_rain.json",
    "Rain: fog and weather byte written (2), Tank moved to (8,7). Every radius -1, floored at 1. DERIVATION 29")

  -- air over concealment: wood written under P1 unit 2 at (6,3), its type
  -- written to BCopter, P2 Mech 68 moved (11,3)->(9,3) mountain, dist 3
  emu.loadSavestate(state); wait(30)
  w8(FOG, 1)
  w8(MAP + 3 * W + 6, 4)
  w8(ua(2), 19)                          -- BCopter
  local mx, my = real_move(68, 11, 3, "left", 2)
  log:write(string.format("air: t(6,3)=%d type2=%d mech=%d,%d vis(6,3)=%d\n",
    r8(MAP + 3 * W + 6) % 32, r8(ua(2)), mx, my, r8(VIS1 + 3 * W + 6)))
  dump_fixture(OUT .. "fog_vision_airwood.json",
    "Air over concealment: Wood written under P1 unit 2 (type written BCopter), P2 Mech on the (9,3) mountain sees it at distance 3. DERIVATION 29")

  -- ground control: identical, type untouched
  emu.loadSavestate(state); wait(30)
  w8(FOG, 1)
  w8(MAP + 3 * W + 6, 4)
  local gx, gy = real_move(68, 11, 3, "left", 2)
  log:write(string.format("ground: t(6,3)=%d type2=%d mech=%d,%d vis(6,3)=%d\n",
    r8(MAP + 3 * W + 6) % 32, r8(ua(2)), gx, gy, r8(VIS1 + 3 * W + 6)))
  dump_fixture(OUT .. "fog_vision_groundwood.json",
    "Control for the air rule: same Wood, same viewer, the occupant left Infantry -- the tile must stay dark. DERIVATION 29")

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
      local ef = io.open(
        "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/vision_rules_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)


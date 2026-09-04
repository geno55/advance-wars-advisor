-- Agent-facing board summary. Companion to mgba_state.lua, which dumps
-- EVERYTHING as JSON plus its own diagnostics (raw bytes, cross-checks,
-- move_grid, probe dumps); this prints only the state that matters for
-- deciding a move, formatted to be read rather than parsed.
--
-- Load alongside mgba_state.lua or on its own, then call:
--     brief()                     -- print to the console
--     brief("C:/path/brief.txt")  -- also write it to a file
--
-- Addresses and encodings are the ones mgba_state.lua documents; see the
-- notes there and docs/DERIVATION.md. When something in this output looks
-- wrong, run state() -- the diagnostics left out of here live there.
--
-- Name tables baked in from data/:
--   CO names      aw1_co.json "confirmed" (measured via army +0x1D)
--   power costs   aw1_co.json power_meta.cost; threshold scales as
--                 cost * (100 + 20*uses)/100 capped at 200% (DERIVATION 37)
--   weather       aw1_movecost.json: 0 Clear, 1 Snow, 2 Rain (confirmed live)

local UNIT_BASE_PTR = 0x08282CB8
local ARMY_BASE_PTR = 0x08282CBC
local MAP_ADDR      = 0x02016C2A
local TURN_ADDR     = 0x03004420
local WEATHER_ADDR  = 0x0300433C
local FOG_ADDR      = 0x0300431D
local VISION_ADDR   = 0x0201763A
local PROP_TABLE    = 0x03004500
local BRIEF_MAP_DIMS = 0x030036E0
local UNIT_STRIDE, ARMY_STRIDE, ARMY_SLOTS = 12, 0x68, 64

local UNIT = {
  "Infantry", "Mech", "MdTank", nil, "Tank", "Recon", "APC", nil, nil,
  "Artillery", "Rockets", nil, nil, "AntiAir", "Missiles", "Fighter",
  "Bomber", nil, "BCopter", "TCopter", "Battleship", "Cruiser", "Lander", "Sub",
}
local TERRAIN = {
  [1] = "Plain", [2] = "River", [3] = "Mountain", [4] = "Wood", [5] = "Road",
  [6] = "City", [7] = "Sea", [8] = "HQ", [10] = "Airport", [11] = "Port",
  [12] = "Bridge", [13] = "Shoal", [14] = "Base", [19] = "Reef",
}
-- One char per tile. Properties are upper-case; ownership is listed under
-- "properties", not encoded in the glyph.
local TCHAR = {
  [1] = ".", [2] = "r", [3] = "^", [4] = "w", [5] = "-", [6] = "C",
  [7] = "~", [8] = "H", [10] = "A", [11] = "P", [12] = "=", [13] = "s",
  [14] = "B", [19] = "%",
}
local CO_NAME = {
  [0] = "Nell", "Andy", "Max", "Olaf", "Sami", "Grit", "Kanbei", "Sonja",
  "Eagle", "Drake", "Sturm", "Sturm",
}
local CO_POWER_COST = {
  [0] = 30000, 30000, 30000, 30000, 25000, 30000, 50000, 30000,
  50000, 40000, 50000, 50000,
}
local WEATHER_NAME = { [0] = "Clear", [1] = "Snow", [2] = "Rain" }

function brief(path)
  -- MAP_W_OVERRIDE/MAP_H_OVERRIDE are mgba_state.lua's setdims() globals;
  -- honoured here when that script is loaded too, harmless nils otherwise.
  local w = MAP_W_OVERRIDE or emu:read8(BRIEF_MAP_DIMS)
  local h = MAP_H_OVERRIDE or emu:read8(BRIEF_MAP_DIMS + 1)
  local ubase, abase = emu:read32(UNIT_BASE_PTR), emu:read32(ARMY_BASE_PTR)

  local day = emu:read32(TURN_ADDR)
  local active_raw = emu:read32(TURN_ADDR + 4)
  local active = math.floor(active_raw / 32)
  local weather = emu:read8(WEATHER_ADDR)
  local fog = emu:read8(FOG_ADDR) ~= 0

  local out = {}
  local function w_(s) out[#out + 1] = s end

  -- units, gathered first so army lines can carry a unit count
  local units, ucount = {}, { 0, 0, 0, 0 }
  for i = 0, 255 do
    local a = ubase + i * UNIT_STRIDE
    local t = emu:read8(a)
    if t >= 1 and t <= 24 then
      local hpammo = emu:read16(a + 4)
      local st = emu:read8(a + 1)
      local p = math.floor(i / ARMY_SLOTS) + 1
      ucount[p] = (ucount[p] or 0) + 1
      units[#units + 1] = {
        player = p, name = UNIT[t] or ("id" .. t),
        x = emu:read8(a + 2), y = emu:read8(a + 3),
        hp = hpammo % 128,
        ammo = math.floor(hpammo / 128) % 16,
        capture = math.floor(hpammo / 2048) % 32,
        fuel = emu:read8(a + 6) % 128,
        acted = st % 2 == 1,
        carrying = math.floor(st / 16) % 2 == 1,
        loaded = math.floor(st / 2) % 2 == 1 and math.floor(st / 8) % 2 == 1,
        -- bit 0x20: submerged (Dive at 0x08066E90, Rise clears -- DERIVATION 31)
        dived = math.floor(st / 32) % 2 == 1,
      }
    end
  end

  -- properties from the game's own list, ownership from the terrain array
  local props = { [0] = {}, {}, {}, {}, {} }
  local i = 0
  while i < 512 do
    local a = PROP_TABLE + i * 8
    if emu:read8(a) == 0xFF then break end
    local x, y = emu:read8(a + 1), emu:read8(a + 2)
    local v = emu:read8(MAP_ADDR + y * w + x)
    local owner = math.floor(v / 32)
    local set = props[owner] or props[0]
    set[#set + 1] = string.format("%s(%d,%d)", TERRAIN[v % 32] or "?", x, y)
    i = i + 1
  end

  -- header
  w_(string.format("Day %d | P%d %s to move | %s | fog %s | income %d/property",
    day, active, CO_NAME[emu:read8(abase + active * ARMY_STRIDE + 0x1D)] or "?",
    WEATHER_NAME[weather] or ("weather?" .. weather),
    fog and "ON" or "off", emu:read32(0x03004338)))
  w_(string.format("Map %dx%d", w, h))
  w_("")

  -- armies: only the ones actually in the game
  for p = 1, 4 do
    local a = abase + p * ARMY_STRIDE
    local funds = emu:read32(a)
    if funds > 0 or ucount[p] > 0 or #(props[p] or {}) > 0 then
      local co = emu:read8(a + 0x1D)
      local meter = emu:read32(a + 0x20)
      local uses = emu:read8(a + 0x25)
      local cost = CO_POWER_COST[co] or 30000
      local threshold = math.floor(cost * (100 + 20 * math.min(uses, 5)) / 100)
      local pstate = ""
      if emu:read8(a + 0x1E) ~= 0 then pstate = "  POWER ACTIVE"
      elseif meter >= threshold then pstate = "  power READY" end
      -- +0x1B: 2 = the game's AI plays this side (DERIVATION 44)
      local ctrl = (emu:read8(a + 0x1B) == 2) and "  CPU" or ""
      w_(string.format("P%d %-6s  funds %-6d  units %-2d  meter %d/%d%s%s%s",
        p, CO_NAME[co] or ("co" .. co), funds, ucount[p], meter, threshold,
        uses > 0 and string.format("  (uses %d)", uses) or "", pstate, ctrl))
    end
  end
  w_("")

  -- terrain grid with an x/y ruler
  w_("Terrain (upper-case = property, see list below):")
  local indent = "     "
  if w > 10 then
    local tens = {}
    for x = 0, w - 1 do
      tens[#tens + 1] = x >= 10 and tostring(math.floor(x / 10)) or " "
    end
    w_(indent .. table.concat(tens))
  end
  local ones = {}
  for x = 0, w - 1 do ones[#ones + 1] = tostring(x % 10) end
  w_(indent .. table.concat(ones))
  for y = 0, h - 1 do
    local row = {}
    for x = 0, w - 1 do
      row[#row + 1] = TCHAR[emu:read8(MAP_ADDR + y * w + x) % 32] or "?"
    end
    w_(string.format("%4d %s", y, table.concat(row)))
  end
  w_("  . plain  - road  = bridge  w wood  ^ mountain  r river  ~ sea"
    .. "  s shoal  % reef")
  w_("  H HQ  B base  C city  A airport  P port")
  w_("")

  w_("Properties:")
  for p = 1, 4 do
    if #(props[p] or {}) > 0 then
      w_(string.format("  P%d: %s", p, table.concat(props[p], " ")))
    end
  end
  if #props[0] > 0 then
    w_("  neutral: " .. table.concat(props[0], " "))
  end
  w_("")

  w_("Units:")
  for p = 1, 4 do
    for _, u in ipairs(units) do
      if u.player == p then
        local flags = {}
        if u.acted then flags[#flags + 1] = "ACTED" end
        if u.capture > 0 then
          flags[#flags + 1] = string.format("capturing(%d)", u.capture)
        end
        if u.carrying then flags[#flags + 1] = "carrying" end
        if u.loaded then flags[#flags + 1] = "LOADED" end
        if u.dived then flags[#flags + 1] = "DIVED" end
        w_(string.format("  P%d %-10s (%2d,%2d)  hp %-3d fuel %-3d ammo %-2d %s",
          u.player, u.name, u.x, u.y, u.hp, u.fuel, u.ammo,
          table.concat(flags, " ")))
      end
    end
  end

  -- the game's own visibility counts, only when they matter
  if fog then
    w_("")
    w_("Vision for the active side (units that can see each tile; 0 = hidden):")
    for y = 0, h - 1 do
      local row = {}
      for x = 0, w - 1 do
        row[#row + 1] = tostring(emu:read8(VISION_ADDR + y * w + x))
      end
      w_(string.format("%4d %s", y, table.concat(row)))
    end
  end

  -- the same sanity checks state() runs, condensed to a single line each
  if day < 1 or active < 1 or active > 4 or active_raw % 32 ~= 0 then
    w_(string.format("WARNING: turn block looks wrong (day=%d active_raw=%d)"
      .. " -- run state() and check TURN_ADDR", day, active_raw))
  end
  local bad = 0
  for _, u in ipairs(units) do
    if u.x < w and u.y < h then
      local cell = emu:read8(MAP_ADDR + u.y * w + u.x) % 32
      if not TERRAIN[cell] then bad = bad + 1 end
    end
  end
  if bad > 0 then
    w_(string.format("WARNING: %d unit(s) on unknown terrain -- dimensions or"
      .. " MAP_ADDR may be wrong; run state() and check its dims_check", bad))
  end

  local text = table.concat(out, "\n")
  console:log(text)
  if path and io and io.open then
    local f = io.open(path, "w")
    if f then
      f:write(text)
      f:close()
      console:log("wrote " .. path)
    else
      console:log("could not open " .. path)
    end
  end
  return text
end

console:log("AW brief loaded.  brief()  or  brief(\"C:/tmp/brief.txt\")")

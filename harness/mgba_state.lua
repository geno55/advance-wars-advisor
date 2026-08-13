-- Milestone 1: read the complete board out of a running game as JSON.
--
-- Load alongside (or instead of) mgba_ramtool.lua, then call:
--     state()                     -- print JSON to the console
--     state("C:/path/state.json") -- also write it to a file, if io is available
--
-- Everything here was derived in docs/DERIVATION.md. Sources, in decreasing
-- order of how much they can be trusted:
--   units    [0x08282CB8] -- a ROM pointer, so correct on any map
--   armies   [0x08282CBC] -- likewise
--   dims     0x03003600   -- fixed IWRAM row table; row count is the height,
--                            pointer stride is the width
--   terrain  0x02016C2A   -- a STATIC address with no pointer to it anywhere.
--                            Verified stable across map switches and emulator
--                            restarts, and sanity-checked on every read.

UNIT_BASE_PTR = 0x08282CB8
ARMY_BASE_PTR = 0x08282CBC
DIM_TABLE = 0x03003600
MAP_ADDR = 0x02016C2A
UNIT_STRIDE, ARMY_STRIDE, ARMY_SLOTS = 12, 0x68, 64

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
local NAVAL = { [21] = true, [22] = true, [23] = true, [24] = true }
local WATER = { [7] = true, [19] = true }
local NAVAL_OK = { [7] = true, [11] = true, [13] = true, [19] = true }

local function dims()
  local p0, p1 = emu:read32(DIM_TABLE), emu:read32(DIM_TABLE + 4)
  local w, h = p1 - p0, 1
  while emu:read32(DIM_TABLE + h * 4) - emu:read32(DIM_TABLE + (h - 1) * 4) == w do
    h = h + 1
  end
  return w, h
end

local function q(s) return '"' .. s .. '"' end

function state(path)
  local w, h = dims()
  local ubase, abase = emu:read32(UNIT_BASE_PTR), emu:read32(ARMY_BASE_PTR)
  local out = {}
  local function w_(s) out[#out + 1] = s end

  w_("{")
  w_(string.format('  "width": %d, "height": %d,', w, h))

  -- armies: 1-indexed, record 0 is a dummy
  w_('  "armies": [')
  local rows = {}
  for p = 1, 4 do
    local a = abase + p * ARMY_STRIDE
    rows[#rows + 1] = string.format(
      '    {"player": %d, "funds": %d, "income": %d}',
      p, emu:read32(a), emu:read32(a + 8))
  end
  w_(table.concat(rows, ",\n"))
  w_("  ],")

  -- units
  w_('  "units": [')
  rows = {}
  for i = 0, 255 do
    local a = ubase + i * UNIT_STRIDE
    local t = emu:read8(a)
    if t >= 1 and t <= 24 then
      local hpammo = emu:read16(a + 4)
      -- "state" is the raw +1 byte, deliberately not interpreted. It was once
      -- labelled has-acted on one observation and a loaded transport that had
      -- not moved falsified that; values 0, 1, 11 and 16 have all been seen.
      -- "cargo" is the carried unit's slot, 0 meaning empty.
      rows[#rows + 1] = string.format(
        '    {"slot": %d, "player": %d, "type": %s, "x": %d, "y": %d, '
        .. '"hp": %d, "ammo": %d, "fuel": %d, "state": %d, "cargo": %d}',
        i, math.floor(i / ARMY_SLOTS) + 1, q(UNIT[t] or ("id" .. t)),
        emu:read8(a + 2), emu:read8(a + 3),
        hpammo % 128, math.floor(hpammo / 128),
        emu:read8(a + 6) % 128,
        emu:read8(a + 1), emu:read8(a + 7))
    end
  end
  w_(table.concat(rows, ",\n"))
  w_("  ],")

  -- terrain: type id and owner, row-major
  w_('  "terrain": [')
  rows = {}
  for y = 0, h - 1 do
    local t, o = {}, {}
    for x = 0, w - 1 do
      local v = emu:read8(MAP_ADDR + y * w + x)
      t[#t + 1] = tostring(v % 32)
      o[#o + 1] = tostring(math.floor(v / 32))
    end
    rows[#rows + 1] = string.format('    {"y": %d, "t": [%s], "owner": [%s]}',
      y, table.concat(t, ","), table.concat(o, ","))
  end
  w_(table.concat(rows, ",\n"))
  w_("  ],")

  -- self-check: units must stand on terrain they can occupy, and every
  -- terrain id must be one we know. A wrong MAP_ADDR shows up here.
  local bad, unknown = 0, {}
  for i = 0, 255 do
    local a = ubase + i * UNIT_STRIDE
    local t = emu:read8(a)
    if t >= 1 and t <= 24 then
      local x, y = emu:read8(a + 2), emu:read8(a + 3)
      if x < w and y < h then
        local cell = emu:read8(MAP_ADDR + y * w + x) % 32
        if not TERRAIN[cell] then unknown[cell] = true end
        if (NAVAL[t] and not NAVAL_OK[cell]) or (not NAVAL[t] and WATER[cell]) then
          bad = bad + 1
        end
      end
    end
  end
  local u = {}
  for k in pairs(unknown) do u[#u + 1] = tostring(k) end
  w_(string.format('  "check": {"units_on_impossible_terrain": %d, '
    .. '"unknown_terrain_ids": [%s]}', bad, table.concat(u, ",")))
  w_("}")

  local json = table.concat(out, "\n")
  console:log(json)
  if path and io and io.open then
    local f = io.open(path, "w")
    if f then
      f:write(json)
      f:close()
      console:log("wrote " .. path)
    else
      console:log("could not open " .. path .. " -- copy the JSON above instead")
    end
  end
  if bad > 0 then
    console:log(string.format("WARNING: %d unit(s) on impossible terrain -- "
      .. "MAP_ADDR may be wrong for this build", bad))
  end
  return json
end

console:log("AW state reader loaded.  state()  or  state(\"C:/tmp/state.json\")")

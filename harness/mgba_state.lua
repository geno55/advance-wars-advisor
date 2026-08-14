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
--   turn     0x03004420   -- fixed IWRAM, likewise static. Sanity-checked below.
--
-- THE TURN BLOCK at 0x03004420, u32 fields:
--   +0x00  day, 1-BASED, matching the on-screen Day. Confirmed to hold across
--          a player-turn boundary (P1 -> P2 within one day) and to advance by
--          exactly one at the day boundary, which is what separates it from a
--          per-turn counter.
--   +0x04  32 * active player, 1-based: 32 = P1, 64 = P2. Stored pre-multiplied
--          by 32 because that is the same shift the terrain array uses for
--          ownership (type + 32*owner), so it can be added onto a terrain byte
--          on capture without a shift.
--   +0x08  constant 1 in every capture so far -- NOT the active player, it did
--          not move across the turn boundary that changed +0x04.
--   +0x10  two s16: camera scroll, not needed here.
--
-- WEATHER at 0x0300433C, u8. Not in the turn block. Read straight off the
-- disassembly at 0x0803A734, which does:
--     r0 = [0x03004310 + 0x2C]      ; the weather index
--     r3 = r0 << 2                  ; * 4
--     r5 = [0x08284A40 + r3 + ...]  ; -> the active movement-cost table
-- so the value is an INDEX 0..2 selecting one of the three movement-cost
-- tables, in the same order they appear in aw1_movecost.json. Independently,
-- a labelled-snapshot hunt (tag/tagfilter/clusters in mgba_ramtool.lua)
-- surfaced this same address from RAM alone.
--
-- The index is authoritative; the NAMES are not. Which table is Snow and which
-- is Rain still rests on weather_inferred_from_difference_pattern in the
-- movecost JSON. One read of this byte on a known snow day settles it.

UNIT_BASE_PTR = 0x08282CB8
ARMY_BASE_PTR = 0x08282CBC
DIM_TABLE = 0x03003600
MAP_ADDR = 0x02016C2A
TURN_ADDR = 0x03004420
WEATHER_ADDR = 0x0300433C
-- The game's own list of every capturable property: 8-byte records of
-- {terrain type, x, y, ...}, sorted by y then x, terminated by 0xFF.
-- Reached from the ROM pointer at 0x08282CC4. This is the cross-check that
-- catches wrong map DIMENSIONS: if the terrain array is read with too large a
-- height, the extra rows are stale data from a previously loaded map, and any
-- property in them will be absent from this list.
PROP_TABLE = 0x03004500
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

-- DIMENSIONS. Two sources, and they check each other.
--
-- MAP_DIMS holds {u8 width, u8 height}. Found by searching IWRAM for the
-- current map's dimensions as adjacent bytes -- exactly ONE hit in 32KB, and
-- it sits four bytes before 0x030036E4, which the turn block already pointed
-- at from +0x1C. So this is a real map descriptor, not a coincidence.
--
-- The row-pointer table gives width a second, independent value: consecutive
-- entries are exactly one row apart. If the two disagree, something is wrong
-- and we say so rather than picking one.
--
-- THE ROW TABLE CANNOT GIVE HEIGHT. It is populated to the size of the LARGEST
-- map loaded since boot, not the current one -- on a 19x12 VS map it still held
-- 26 evenly spaced entries left from a taller map. Walking it returned 26, the
-- terrain array got read 14 rows too far into the previous map's data, and the
-- result looked entirely plausible: coherent terrain, every unit on legal
-- ground. Only the property cross-check in engine/state.py caught it. That is
-- why height now comes from MAP_DIMS and the walk is kept purely as a warning.
MAP_DIMS = 0x030036E0
MAP_W_OVERRIDE, MAP_H_OVERRIDE = nil, nil

function setdims(w, h)
  MAP_W_OVERRIDE, MAP_H_OVERRIDE = w, h
  if w then
    console:log(string.format("dimensions pinned to %dx%d for subsequent dumps", w, h))
  else
    console:log("dimension override cleared; falling back to the row table")
  end
end

function cleardims() setdims(nil, nil) end

-- Returns width, height, pinned, stride_width, walked_height.
-- The last two are the row table's opinion, kept so the JSON can record a
-- disagreement instead of hiding it.
local function dims()
  local p0, p1 = emu:read32(DIM_TABLE), emu:read32(DIM_TABLE + 4)
  local stride_w, walked_h = p1 - p0, 1
  while emu:read32(DIM_TABLE + walked_h * 4)
      - emu:read32(DIM_TABLE + (walked_h - 1) * 4) == stride_w do
    walked_h = walked_h + 1
  end
  local w = MAP_W_OVERRIDE or emu:read8(MAP_DIMS)
  local h = MAP_H_OVERRIDE or emu:read8(MAP_DIMS + 1)
  return w, h, (MAP_H_OVERRIDE ~= nil), stride_w, walked_h
end

local function q(s) return '"' .. s .. '"' end

-- ---------------------------------------------------------------------------
-- RAM PROBE -- a labelled snapshot, for pinning a setting whose address is not
-- known yet.
--
-- The method that found the weather byte: capture the same situation twice
-- with one thing deliberately changed, and diff. Doing it from a dump rather
-- than from the interactive search means the comparison happens offline, in
-- tools/fog_hunt.py, where a THIRD capture can separate the byte that tracks
-- the setting from the dozens that are merely noisy.
--
-- Off by default: it makes the dump about fifty times bigger.
--     state("C:/tmp/fog_off.json", true)
--
-- IWRAM is the whole 32K because that is where every battle-state address
-- found so far lives -- the settings struct, the turn block and the weather
-- byte are all in it. Casting narrowly is how you miss the thing you are
-- looking for and conclude it is not there.
local PROBE_REGIONS = {
  { 0x03000000, 0x8000, "iwram" },
}

local function hexdump(base, len)
  local data = emu:readRange(base, len)
  return (data:gsub(".", function(c)
    return string.format("%02x", string.byte(c))
  end))
end

function state(path, probe)
  local w, h, pinned, stride_w, walked_h = dims()
  local ubase, abase = emu:read32(UNIT_BASE_PTR), emu:read32(ARMY_BASE_PTR)
  local out = {}
  local function w_(s) out[#out + 1] = s end

  w_("{")
  w_(string.format('  "width": %d, "height": %d, "dims_pinned": %s,',
    w, h, pinned and "true" or "false"))
  w_(string.format('  "dims_check": {"stride_width": %d, "walked_height": %d, '
    .. '"width_sources_agree": %s},',
    stride_w, walked_h, (w == stride_w) and "true" or "false"))

  -- turn block. active_raw ships alongside the decoded player so an
  -- unrecognised encoding is visible rather than silently divided away.
  local day = emu:read32(TURN_ADDR)
  local active_raw = emu:read32(TURN_ADDR + 4)
  local active = math.floor(active_raw / 32)
  local weather = emu:read8(WEATHER_ADDR)
  w_(string.format('  "day": %d, "active_player": %d, "active_raw": %d,',
    day, active, active_raw))
  w_(string.format('  "weather_index": %d,', weather))

  -- armies: 1-indexed, record 0 is a dummy
  w_('  "armies": [')
  local rows, funds = {}, {}
  for p = 1, 4 do
    local a = abase + p * ARMY_STRIDE
    funds[p] = emu:read32(a)
    -- +0x20 is the CO power meter, cumulative from 0. Both the dealer and the
    -- receiver of damage charge. Scale and activation threshold unknown.
    -- +0x1D is the CO id, 0..11, indexing the CO record table as co*292. It is
    -- the field that named eleven of the twelve records by being written and
    -- read back off the screen, so it is confirmed rather than inferred.
    -- Without it every damage prediction quietly assumes a neutral CO, and Max
    -- at 150/100 would make that wrong by half.
    rows[#rows + 1] = string.format(
      '    {"player": %d, "funds": %d, "income": %d, "power": %d, "co_id": %d}',
      p, emu:read32(a), emu:read32(a + 8), emu:read16(a + 0x20),
      emu:read8(a + 0x1D))
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
      -- +4 packs three fields: hp bits 0-6, ammo bits 7-10, capture bits 11-15.
      -- +1 is a bitfield: bit 0 acted, bit 4 carrying, bits 1 and 3 both set
      -- while inside a transport. The raw byte ships too, so an unrecognised
      -- state is visible rather than lost.
      local st = emu:read8(a + 1)
      rows[#rows + 1] = string.format(
        '    {"slot": %d, "player": %d, "type": %s, "x": %d, "y": %d, '
        .. '"hp": %d, "ammo": %d, "capture": %d, "fuel": %d, '
        .. '"acted": %s, "carrying": %s, "loaded": %s, "state": %d, "cargo": %d}',
        i, math.floor(i / ARMY_SLOTS) + 1, q(UNIT[t] or ("id" .. t)),
        emu:read8(a + 2), emu:read8(a + 3),
        hpammo % 128, math.floor(hpammo / 128) % 16,
        math.floor(hpammo / 2048) % 32,
        emu:read8(a + 6) % 128,
        st % 2 == 1 and "true" or "false",
        math.floor(st / 16) % 2 == 1 and "true" or "false",
        (math.floor(st / 2) % 2 == 1 and math.floor(st / 8) % 2 == 1)
          and "true" or "false",
        st, emu:read8(a + 7))
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

  -- The game's own property list. Authoritative for WHERE the properties are;
  -- ownership stays in the terrain array's type + 32*owner encoding.
  w_('  "properties": [')
  rows = {}
  local i = 0
  while i < 512 do
    local a = PROP_TABLE + i * 8
    local t = emu:read8(a)
    if t == 0xFF then break end
    rows[#rows + 1] = string.format('    {"t": %d, "x": %d, "y": %d}',
      t, emu:read8(a + 1), emu:read8(a + 2))
    i = i + 1
  end
  w_(table.concat(rows, ",\n"))
  w_("  ],")

  -- The game's OWN movement flood fill, read through the same row-pointer
  -- table the dimensions come from. This is the oracle for milestone 3: rather
  -- than trusting our Dijkstra, diff it against what the game actually painted.
  --
  -- ONLY MEANINGFUL WHILE A UNIT IS SELECTED and its range is on screen. The
  -- rest of the time it holds whatever the previous selection left behind, so
  -- it ships with a flag rather than silently looking authoritative. 255 is
  -- unreachable; what the other values mean is exactly what the diff is for.
  w_('  "move_grid": [')
  rows = {}
  for y = 0, h - 1 do
    local rowp = emu:read32(DIM_TABLE + y * 4)
    local v = {}
    for x = 0, w - 1 do v[#v + 1] = tostring(emu:read8(rowp + x)) end
    rows[#rows + 1] = string.format('    {"y": %d, "v": [%s]}',
      y, table.concat(v, ","))
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

  -- Turn-block sanity. The active player used to be INFERRED from "only the
  -- current player's army record holds nonzero funds" (recorded in
  -- aw1_army_struct.json as a guess). Now that a real field exists, the old
  -- heuristic becomes a cross-check: if the two ever disagree, one of them is
  -- wrong and we want to know immediately rather than average them.
  local turn_ok = (day >= 1) and (active_raw % 32 == 0)
    and (active >= 1) and (active <= 4) and (weather >= 0) and (weather <= 2)
  local funded = {}
  for p = 1, 4 do
    if funds[p] and funds[p] > 0 then funded[#funded + 1] = p end
  end
  local funds_agree = (#funded == 1) and (funded[1] == active)

  w_(string.format('  "check": {"units_on_impossible_terrain": %d, '
    .. '"unknown_terrain_ids": [%s], "turn_block_sane": %s, '
    .. '"funds_heuristic_agrees": %s}%s',
    bad, table.concat(u, ","),
    turn_ok and "true" or "false",
    funds_agree and "true" or "false",
    probe and "," or ""))

  if probe then
    local parts = {}
    for _, r in ipairs(PROBE_REGIONS) do
      parts[#parts + 1] = string.format(
        '    %s: {"base": "0x%08X", "len": %d, "hex": "%s"}',
        q(r[3]), r[1], r[2], hexdump(r[1], r[2]))
    end
    w_('  "probe": {')
    w_(table.concat(parts, ",\n"))
    w_("  }")
  end
  w_("}")

  local json = table.concat(out, "\n")
  -- A probe dump is ~64K of hex on one line. Echoing that to the console is at
  -- best very slow, so with the probe on the console gets a receipt and the
  -- file gets the data -- which also means a probe run without a path is a
  -- mistake worth naming rather than a wall of hex.
  if probe then
    console:log(string.format("state+probe: %d bytes of JSON", #json))
    if not path then
      console:log("NOTE: no path given -- a probe dump is only useful written "
        .. "to a file for tools/fog_hunt.py. Try state(\"C:/tmp/fog_off.json\", true)")
    end
  else
    console:log(json)
  end
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
  if not turn_ok then
    console:log(string.format("WARNING: turn block at 0x%08X looks wrong "
      .. "(day=%d active_raw=%d) -- TURN_ADDR may be wrong for this build",
      TURN_ADDR, day, active_raw))
  elseif not funds_agree then
    console:log(string.format("NOTE: active player %d, but the army records "
      .. "with funds are {%s}. The funds heuristic and the turn field "
      .. "disagree -- worth understanding before trusting either.",
      active, table.concat(funded, ",")))
  end
  return json
end

console:log("AW state reader loaded.  state()  or  state(\"C:/tmp/state.json\")")
console:log("  state(path, true) also dumps an IWRAM probe -- see tools/fog_hunt.py")

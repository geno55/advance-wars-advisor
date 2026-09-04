-- The board reader for headless Mesen2: harness/mgba_state.lua ported to
-- Mesen's Lua API, same addresses, same JSON schema (engine/state.py loads
-- both). ROADMAP step 2: the differential test needs a before and an after
-- dump inside the headless loop, and the two fixture dumpers that existed
-- (mesen_sonja_fix.lua, mesen_vision_rules.lua) skipped cargo, the armies'
-- power fields and the repair-free byte.
--
-- A LIBRARY, not a script: it fills the global table `AW` and runs nothing.
-- tools/sim_diff.py concatenates it with mesen_drive.lua and a generated
-- runner into one script (Mesen's sandbox is not relied on for dofile), so
-- every top-level name here is either local or lives on AW.
--
-- What it reads, and where each address was established (docs/DERIVATION.md):
--   units      [0x08282CB8], 12-byte records, slot/64 = player-1   (8)
--   armies     [0x08282CBC], 0x68-byte records, 1-indexed           (10, 27)
--   terrain    0x02016C2A, type + 32*owner per tile, static          (9)
--   dims       0x030036E0 {w, h}; the row table 0x03003600 gives the
--              width a second time and CANNOT give the height          (14)
--   turn       0x03004420 day, +4 = 32*active                          (10)
--   weather    0x0300433C, an index into the three movement tables     (11)
--   fog        0x0300431D                                              (20)
--   vision     0x0201763A (+ the copy at 0x02017B42), the ACTIVE
--              player's per-tile viewer count                          (21-23)
--   properties 0x03004500, 8-byte records terminated by 0xFF           (14)
--   repair-free 0x03004357, funds rate 0x03004338                      (33, 39)
--   RNG        0x03001D30 -- shipped so an attack's roll is a point     (32)
--
-- The height byte is unreliable on at least one parked state (the 15x10 VS
-- fixture reads 13, with three rows of zeroes below the map -- see
-- mesen_sonja_fix.lua), so AW.set_dims(w, h) pins it; the raw bytes ship in
-- the JSON either way, and engine/state.py's property cross-check is the
-- backstop.

AW = AW or {}
local M = AW

M.UNIT_PTR = 0x08282CB8
M.ARMY_PTR = 0x08282CBC
M.MAP = 0x02016C2A
M.DIM_TABLE = 0x03003600
M.MAP_DIMS = 0x030036E0
M.TURN = 0x03004420
M.WEATHER = 0x0300433C
M.FOG = 0x0300431D
M.REPAIR_FREE = 0x03004357
M.RATE = 0x03004338
M.VISION = 0x0201763A
M.VISION_DUP = 0x02017B42
M.PROP_TABLE = 0x03004500
M.RNG = 0x03001D30
M.CURX, M.CURY = 0x030033F0, 0x030033F1
M.UNIT_STRIDE, M.ARMY_STRIDE, M.ARMY_SLOTS = 12, 0x68, 64

function M.r8(a) return emu.read(a, emu.memType.gbaDebug) end
function M.r16(a) return emu.read16(a, emu.memType.gbaDebug) end
function M.r32(a) return emu.read32(a, emu.memType.gbaDebug) end
function M.w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end
function M.w16(a, v) emu.write16(a, v, emu.memType.gbaMemory) end
function M.w32(a, v) emu.write32(a, v, emu.memType.gbaMemory) end

-- RAM type bytes are 1-BASED (type 7 is the APC, 6 the Recon).
M.UNIT_NAMES = {
  "Infantry", "Mech", "MdTank", nil, "Tank", "Recon", "APC", nil, nil,
  "Artillery", "Rockets", nil, nil, "AntiAir", "Missiles", "Fighter",
  "Bomber", nil, "BCopter", "TCopter", "Battleship", "Cruiser", "Lander", "Sub",
}
M.TYPE_OF = {}
for i, n in pairs(M.UNIT_NAMES) do M.TYPE_OF[n] = i end
M.TERRAIN = {
  [1] = "Plain", [2] = "River", [3] = "Mountain", [4] = "Wood", [5] = "Road",
  [6] = "City", [7] = "Sea", [8] = "HQ", [10] = "Airport", [11] = "Port",
  [12] = "Bridge", [13] = "Shoal", [14] = "Base", [19] = "Reef",
}
local NAVAL = { [21] = true, [22] = true, [23] = true, [24] = true }
local WATER = { [7] = true, [19] = true }
local NAVAL_OK = { [7] = true, [11] = true, [13] = true, [19] = true }

M.W_OVERRIDE, M.H_OVERRIDE = nil, nil
function M.set_dims(w, h) M.W_OVERRIDE, M.H_OVERRIDE = w, h end

-- width, height, pinned, stride_width, walked_height (mgba_state.lua dims())
function M.dims()
  local p0, p1 = M.r32(M.DIM_TABLE), M.r32(M.DIM_TABLE + 4)
  local stride_w, walked_h = p1 - p0, 1
  while walked_h < 64 and M.r32(M.DIM_TABLE + walked_h * 4)
      - M.r32(M.DIM_TABLE + (walked_h - 1) * 4) == stride_w do
    walked_h = walked_h + 1
  end
  local w = M.W_OVERRIDE or M.r8(M.MAP_DIMS)
  local h = M.H_OVERRIDE or M.r8(M.MAP_DIMS + 1)
  return w, h, (M.H_OVERRIDE ~= nil), stride_w, walked_h
end

function M.unit_addr(slot) return M.r32(M.UNIT_PTR) + slot * M.UNIT_STRIDE end
function M.army_addr(player) return M.r32(M.ARMY_PTR) + player * M.ARMY_STRIDE end
function M.map_addr(x, y) local w = M.dims(); return M.MAP + y * w + x end

local function q(s) return '"' .. s .. '"' end
local function b(v) return v and "true" or "false" end

-- The record decoded the way engine/state.py reads it: +4 packs hp bits
-- 0-6, ammo 7-10, capture 11-15; +1 is the flag byte (bit 0 acted, bit 4
-- carrying, bits 1 and 3 together = aboard a transport, bit 5 dived);
-- +6 bits 0-6 fuel; +7/+8 the two cargo slots.
function M.unit(slot)
  local a = M.unit_addr(slot)
  local t = M.r8(a)
  if t < 1 or t > 24 then return nil end
  local v4 = M.r16(a + 4)
  local st = M.r8(a + 1)
  return {
    slot = slot, player = math.floor(slot / M.ARMY_SLOTS) + 1,
    type = t, name = M.UNIT_NAMES[t] or ("id" .. t),
    x = M.r8(a + 2), y = M.r8(a + 3),
    hp = v4 % 128, ammo = math.floor(v4 / 128) % 16,
    capture = math.floor(v4 / 2048) % 32,
    fuel = M.r8(a + 6) % 128, state = st,
    acted = st % 2 == 1,
    carrying = math.floor(st / 16) % 2 == 1,
    loaded = (math.floor(st / 2) % 2 == 1 and math.floor(st / 8) % 2 == 1),
    dived = math.floor(st / 32) % 2 == 1,
    cargo = M.r8(a + 7), cargo2 = M.r8(a + 8),
    ai9 = M.r8(a + 9), ai10 = M.r8(a + 10), ai11 = M.r8(a + 11),
  }
end

function M.army(player)
  local a = M.army_addr(player)
  return {
    player = player, funds = M.r32(a), income = M.r32(a + 8),
    power = M.r32(a + 0x20), co_id = M.r8(a + 0x1D),
    power_active = M.r8(a + 0x1E) ~= 0, power_ready = M.r8(a + 0x24) ~= 0,
    power_uses = M.r8(a + 0x25), control = M.r8(a + 0x1B),
    flag14 = M.r16(a + 0x14), flag1C = M.r8(a + 0x1C),
    team = M.r8(a + 0x26), enemies = M.r8(a + 0x28),
    hqx = M.r8(a + 0x29), hqy = M.r8(a + 0x2A),
  }
end

-- opts.move_grid: include the game's flood-fill grid (only meaningful while
-- a unit is selected; off by default here, the differential test never
-- dumps mid-selection). opts.note: a string for "_comment".
function M.state_json(opts)
  opts = opts or {}
  local w, h, pinned, stride_w, walked_h = M.dims()
  local out = {}
  local function w_(s) out[#out + 1] = s end

  w_("{")
  if opts.note then w_(string.format('  "_comment": [%s],', q(opts.note))) end
  w_('  "source": "harness/mesen_state.lua",')
  w_(string.format('  "width": %d, "height": %d, "dims_pinned": %s,', w, h, b(pinned)))
  w_(string.format('  "dims_raw": [%d, %d],', M.r8(M.MAP_DIMS), M.r8(M.MAP_DIMS + 1)))
  w_(string.format('  "dims_check": {"stride_width": %d, "walked_height": %d, '
    .. '"width_sources_agree": %s},', stride_w, walked_h, b(w == stride_w)))

  local day = M.r32(M.TURN)
  local active_raw = M.r32(M.TURN + 4)
  local active = math.floor(active_raw / 32)
  local weather = M.r8(M.WEATHER)
  w_(string.format('  "day": %d, "active_player": %d, "active_raw": %d,',
    day, active, active_raw))
  w_(string.format('  "weather_index": %d,', weather))
  local fogv = M.r8(M.FOG)
  w_(string.format('  "fog": %s, "fog_raw": %d,', b(fogv ~= 0), fogv))
  local repfree = M.r8(M.REPAIR_FREE)
  w_(string.format('  "repair_free": %s, "repair_free_raw": %d,', b(repfree ~= 0), repfree))
  w_(string.format('  "funds_per_property": %d,', M.r32(M.RATE)))
  w_(string.format('  "rng": %d,', M.r32(M.RNG)))
  w_(string.format('  "cursor": [%d, %d],', M.r8(M.CURX), M.r8(M.CURY)))
  -- what the AI reads (DERIVATION 45): the mission id that picks its
  -- profile, the two settings bytes the forecast and move tables switch
  -- on, and the 0x130-byte profile copy the AI's state 0 leaves in EWRAM
  w_(string.format('  "map_id": %d, "settings_6": %d, "settings_8": %d,',
    M.r8(0x03004310 + 2), M.r8(0x03004310 + 6), M.r8(0x03004310 + 8)))
  local prof = {}
  for i = 0, 0x12F do prof[#prof + 1] = string.format("%02x", M.r8(0x020235DC + i)) end
  w_(string.format('  "ai_profile": "%s",', table.concat(prof)))

  w_('  "armies": [')
  local rows, funds = {}, {}
  for p = 1, 4 do
    local a = M.army(p)
    funds[p] = a.funds
    rows[#rows + 1] = string.format(
      '    {"player": %d, "funds": %d, "income": %d, "power": %d, "co_id": %d, '
      .. '"power_active": %s, "power_ready": %s, "power_uses": %d, "control": %d, "flag14": %d, "flag1C": %d, '
      .. '"team": %d, "enemies": %d, "hq": [%d, %d]}',
      p, a.funds, a.income, a.power, a.co_id, b(a.power_active),
      b(a.power_ready), a.power_uses, a.control, a.flag14, a.flag1C,
      a.team, a.enemies, a.hqx, a.hqy)
  end
  w_(table.concat(rows, ",\n"))
  w_("  ],")

  w_('  "units": [')
  rows = {}
  local bad, unknown = 0, {}
  for i = 0, 255 do
    local u = M.unit(i)
    if u then
      rows[#rows + 1] = string.format(
        '    {"slot": %d, "player": %d, "type": %s, "x": %d, "y": %d, '
        .. '"hp": %d, "ammo": %d, "capture": %d, "fuel": %d, '
        .. '"acted": %s, "carrying": %s, "loaded": %s, "state": %d, "cargo": %d, '
        .. '"cargo2": %d, "ai": [%d, %d, %d]}',
        u.slot, u.player, q(u.name), u.x, u.y, u.hp, u.ammo, u.capture, u.fuel,
        b(u.acted), b(u.carrying), b(u.loaded), u.state, u.cargo, u.cargo2,
        u.ai9, u.ai10, u.ai11)
      if u.x < w and u.y < h then
        local cell = M.r8(M.MAP + u.y * w + u.x) % 32
        if not M.TERRAIN[cell] then unknown[cell] = true end
        if (NAVAL[u.type] and not NAVAL_OK[cell]) or (not NAVAL[u.type] and WATER[cell]) then
          bad = bad + 1
        end
      end
    end
  end
  w_(table.concat(rows, ",\n"))
  w_("  ],")

  w_('  "terrain": [')
  rows = {}
  for y = 0, h - 1 do
    local t, o = {}, {}
    for x = 0, w - 1 do
      local v = M.r8(M.MAP + y * w + x)
      t[#t + 1] = tostring(v % 32)
      o[#o + 1] = tostring(math.floor(v / 32))
    end
    rows[#rows + 1] = string.format('    {"y": %d, "t": [%s], "owner": [%s]}',
      y, table.concat(t, ","), table.concat(o, ","))
  end
  w_(table.concat(rows, ",\n"))
  w_("  ],")

  w_(string.format('  "vision_addr": "0x%08X", "vision": [', M.VISION))
  rows = {}
  local vdup = true
  for y = 0, h - 1 do
    local v = {}
    for x = 0, w - 1 do
      local off = y * w + x
      local a1 = M.r8(M.VISION + off)
      v[#v + 1] = tostring(a1)
      if a1 ~= M.r8(M.VISION_DUP + off) then vdup = false end
    end
    rows[#rows + 1] = string.format('    [%s]', table.concat(v, ","))
  end
  w_(table.concat(rows, ",\n"))
  w_(string.format('  ], "vision_copies_agree": %s,', b(vdup)))

  w_('  "properties": [')
  rows = {}
  local i = 0
  while i < 512 do
    local a = M.PROP_TABLE + i * 8
    local t = M.r8(a)
    if t == 0xFF then break end
    rows[#rows + 1] = string.format('    {"t": %d, "x": %d, "y": %d}',
      t, M.r8(a + 1), M.r8(a + 2))
    i = i + 1
  end
  w_(table.concat(rows, ",\n"))
  w_("  ],")

  if opts.move_grid then
    w_('  "move_grid": [')
    rows = {}
    for y = 0, h - 1 do
      local rowp = M.r32(M.DIM_TABLE + y * 4)
      local v = {}
      for x = 0, w - 1 do v[#v + 1] = tostring(M.r8(rowp + x)) end
      rows[#rows + 1] = string.format('    {"y": %d, "v": [%s]}', y, table.concat(v, ","))
    end
    w_(table.concat(rows, ",\n"))
    w_("  ],")
  end

  local u = {}
  for k in pairs(unknown) do u[#u + 1] = tostring(k) end
  local turn_ok = (day >= 1) and (active_raw % 32 == 0)
    and (active >= 1) and (active <= 4) and (weather >= 0) and (weather <= 2)
    and (fogv == 0 or fogv == 1)
  local funded = {}
  for p = 1, 4 do
    if funds[p] and funds[p] > 0 then funded[#funded + 1] = p end
  end
  local funds_agree = (#funded == 1) and (funded[1] == active)
  w_(string.format('  "check": {"units_on_impossible_terrain": %d, '
    .. '"unknown_terrain_ids": [%s], "turn_block_sane": %s, '
    .. '"funds_heuristic_agrees": %s}',
    bad, table.concat(u, ","), b(turn_ok), b(funds_agree)))
  w_("}")
  return table.concat(out, "\n") .. "\n"
end

function M.dump(path, opts)
  local json = M.state_json(opts)
  local f = assert(io.open(path, "w"), "cannot open " .. path)
  f:write(json)
  f:close()
  return json
end

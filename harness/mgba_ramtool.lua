-- RAM hunter for mGBA 0.10+, aimed at finding the unit array in Advance Wars.
--
-- Load: Tools > Scripting > Load script, then drive from the console prompt.
--
-- WHY CHANGE-DETECTION AND NOT VALUE SEARCH
-- Searching for a known value does not work here. Internal HP is 1..100 but the
-- screen only shows 1..10 bars, so after an attack you know the value fell into
-- a ten-wide range, not what it is. Scanning for a literal 100 also returns
-- thousands of hits across 288 KB. So instead: snapshot, let the game change
-- something, and keep only the addresses that changed the way HP must have.
--
-- KNOWN LAYOUT, read off the disassembly (see docs/DERIVATION.md):
--   unit record +0 = unit type, 1-based (0 means empty slot)
--   unit record +2 = map x
--   unit record +3 = map y
--   map row pointer table lives at 0x03003600
-- So once a candidate HP address is found, dump() around it and the type/x/y
-- bytes should be visible a few bytes below -- that is your confirmation.
--
-- TYPICAL SESSION
--   mark()                       -- snapshot before the attack
--   (attack a full-health unit in game)
--   dec()                        -- keep addresses that went down
--   range(1,100)                 -- HP is 1..100, prune the rest
--   mark(); (attack again); dec()
--   list()                       -- should be a handful now
--   dump(0x0200XXXX)             -- confirm type/x/y sit nearby
--   stride()                     -- infer the array stride from 2+ hits

-- ---------------------------------------------------------------------------
-- THE UNIT ARRAY  (solved -- see docs/DERIVATION.md)
--
-- The AI loop at 0x08060B06 does:
--     r1 = index*12 ; r0 = [0x08282CB8] ; r7 = r0 + r1 ; type = [r7]
-- so the array base is the EWRAM pointer stored in ROM at 0x08282CB8, records
-- are 12 bytes:
--     +0    unit type, 1-BASED (0 = empty; subtract 1 for the damage table)
--     +1    has-acted-this-turn (CONFIRMED: set on exactly the unit that
--           acted, and cleared when the turn ended)
--     +2    map x
--     +3    map y
--     +4:2  u16 bitfield -> hp = v & 0x7F, ammo = v >> 7
--     +7    CARGO: the slot number of the unit being carried, 0 = empty.
--           Confirmed by diffing a loaded APC against an unloaded one: the only
--           cargo-related difference was +7 holding the passenger's slot. This
--           is also why slot 0 is never used by any army -- reserving it lets 0
--           mean "nothing" without ambiguity. A loaded unit keeps its own
--           record, with its coordinates tracking the transport's, so two
--           records sharing a tile is normal rather than an error.
--     +6    fuel = v & 0x7F; bit 7 is a separate flag, meaning UNKNOWN.
--           It is not "acted" (it survives end-of-turn), not "damaged" and not
--           "has moved" -- a Tank that is both damaged and short on fuel does
--           not have it. Suspect it clears at the start of that army's own next
--           turn. Masking is safe regardless: max fuel in AW1 is 99 < 128.
--
-- The army is NOT a field: it is the block. 64 slots per army, so
-- army = slot / 64. That is confirmed structurally -- 4*64 records * 12 bytes
-- = 0xC00, and base + 0xC00 lands exactly on 0x0201AB34, the next known
-- pointer in ROM (0x08282CBC).
--
-- Verified byte-for-byte against a live capture of 8 units: Mech ammo 3,
-- Artillery ammo 9, APC/Recon/Infantry ammo 0, fuel 99/70/70/80/50, and a Tank
-- on 42 HP with 8 ammo after spending one on a counterattack.
-- ---------------------------------------------------------------------------
UNIT_BASE_PTR = 0x08282CB8
UNIT_STRIDE = 12
HP_OFF = 4
ARMY_SLOTS = 64        -- slots per army; army index = slot / 64

-- Indexed by the 1-based in-RAM type id.
local TYPE_NAMES = {
  "Infantry", "Mech", "MdTank", "-", "Tank", "Recon", "APC", "-", "-",
  "Artillery", "Rockets", "-", "-", "AntiAir", "Missiles", "Fighter",
  "Bomber", "-", "BCopter", "TCopter", "Battleship", "Cruiser", "Lander", "Sub",
}

function unitbase()
  return emu:read32(UNIT_BASE_PTR)
end

-- Dump the live board. This is the state reader in miniature.
-- +4 is a u16 holding THREE fields, not two:
--     hp      = bits 0-6    (max 100, needs 7 bits)
--     ammo    = bits 7-10   (max   9, needs 4)
--     capture = bits 11-15  (max  20, needs 5)
-- 7 + 4 + 5 = 16 exactly. This was originally decoded as hp + (v >> 7) as ammo,
-- which is right only while capture is zero -- and it was, in everything dumped
-- until a capturing infantry reported "ammo 160".
--
-- +6 is fuel in bits 0-6 with bit 7 a separate, still-unidentified flag.
function unithp(a) return emu:read16(a + HP_OFF) % 128 end
function unitammo(a) return math.floor(emu:read16(a + HP_OFF) / 128) % 16 end
function unitcapture(a) return math.floor(emu:read16(a + HP_OFF) / 2048) % 32 end
function unitfuel(a) return emu:read8(a + 6) % 128 end
function unitfuelflag(a) return emu:read8(a + 6) >= 128 end

-- +1 is a bitfield, not a boolean or an enum:
--     bit 0 (1)  has acted this turn
--     bit 4 (16) is carrying cargo
--     bits 1 and 3 (2 and 8) both set while a unit is INSIDE a transport
-- Observed: 0 idle, 1 acted, 11 loaded (1+2+8), 16 carrying and unmoved,
-- 17 carrying and moved. Unloading dropped both units to 1, clearing bit 4 on
-- the transport, which is what identified that bit.
function unitacted(a) return emu:read8(a + 1) % 2 == 1 end
function unitcarrying(a) return math.floor(emu:read8(a + 1) / 16) % 2 == 1 end
function unitloaded(a)
  local s = emu:read8(a + 1)
  return math.floor(s / 2) % 2 == 1 and math.floor(s / 8) % 2 == 1
end

function units(n)
  n = n or 256
  local base = unitbase()
  console:log(string.format("unit array @ 0x%08X, stride %d, %d slots/army",
    base, UNIT_STRIDE, ARMY_SLOTS))
  local shown, bad, armies = 0, 0, {}
  for i = 0, n - 1 do
    local a = base + i * UNIT_STRIDE
    local t = emu:read8(a)
    if t >= 1 and t <= 24 then
      local hp, ammo = unithp(a), unitammo(a)
      local army = math.floor(i / ARMY_SLOTS)
      armies[army] = (armies[army] or 0) + 1
      local flag = ""
      if hp < 1 or hp > 100 then
        flag = "  <-- IMPOSSIBLE HP"
        bad = bad + 1
      end
      -- +1 was labelled "acted" on the strength of a single observation (a tank
      -- that had just attacked). A loaded transport that has NOT moved also has
      -- it set, so the label was wrong. Print the raw value instead of an
      -- interpretation until we know what it means.
      local marks = ""
      if unitacted(a) then marks = marks .. " acted" end
      if unitloaded(a) then marks = marks .. " LOADED" end
      local cap = unitcapture(a)
      if cap > 0 then marks = marks .. string.format(" capturing %d/20", cap) end
      -- Surface any bit of +1 we cannot yet account for, so a new state
      -- announces itself instead of being silently swallowed.
      local rest = emu:read8(a + 1)
      for _, bit in ipairs({ 16, 8, 2, 1 }) do
        if math.floor(rest / bit) % 2 == 1 then rest = rest - bit end
      end
      if rest ~= 0 then marks = marks .. string.format(" ?bits=%d", rest) end
      local cargo = emu:read8(a + 7)
      if cargo ~= 0 then
        local ct = emu:read8(base + cargo * UNIT_STRIDE)
        marks = marks .. string.format(" carrying[%d]=%s", cargo,
          TYPE_NAMES[ct] or ("id" .. ct))
      end
      if unitfuelflag(a) then marks = marks .. " ?bit7" end
      console:log(string.format(
        "  [%3d] 0x%08X P%d %-11s (%2d,%2d) hp=%3d (%2d bars) ammo=%2d fuel=%3d%s%s",
        i, a, army + 1, TYPE_NAMES[t] or "?",
        emu:read8(a + 2), emu:read8(a + 3), hp, math.ceil(hp / 10),
        unitammo(a), unitfuel(a), marks, flag))
      shown = shown + 1
    end
  end
  local as = {}
  for army, cnt in pairs(armies) do
    as[#as + 1] = string.format("P%d: %d", army + 1, cnt)
  end
  console:log(string.format("  %d units (%s)", shown, table.concat(as, ", ")))
  if bad > 0 then
    console:log(string.format("  %d record(s) have an impossible HP -- run "
      .. "hexdump(0x%08X, 128) and send it over", bad, base))
  end
end

-- HP address of a unit by army and slot, e.g. hpaddr(1, 7) for P2 slot 7.
function unitaddr(army, slot)
  return unitbase() + (army * ARMY_SLOTS + slot) * UNIT_STRIDE
end

-- All 12 bytes of one unit record, labelled. Known fields are named; the rest
-- are shown raw, because +7..+11 are still undecoded and cargo probably lives
-- there.
function unitrec(slot)
  local a = unitbase() + slot * UNIT_STRIDE
  local b = {}
  for i = 0, 11 do b[i] = emu:read8(a + i) end
  local hpammo = emu:read16(a + 4)
  console:log(string.format("slot %d @0x%08X  %s", slot, a,
    TYPE_NAMES[b[0]] or ("id" .. b[0])))
  console:log(string.format("  +0 type=%-3d +1 st=%-3d +2 x=%-3d +3 y=%-3d",
    b[0], b[1], b[2], b[3]))
  console:log(string.format("  +4:2 hp=%d ammo=%d   +6 fuel=%d (bit7 %s)",
    hpammo % 128, math.floor(hpammo / 128), b[6] % 128,
    b[6] >= 128 and "set" or "clear"))
  console:log(string.format("  +7 cargo=%s", b[7] == 0 and "empty"
    or string.format("slot %d (%s)", b[7],
      TYPE_NAMES[emu:read8(unitbase() + b[7] * UNIT_STRIDE)] or "?")))
  console:log(string.format("  undecoded: +8=%d +9=%d +10=%d +11=%d",
    b[8], b[9], b[10], b[11]))
  console:log("  raw: " .. table.concat({ b[0], b[1], b[2], b[3], b[4], b[5],
    b[6], b[7], b[8], b[9], b[10], b[11] }, " "))
end

-- Byte-by-byte diff of two unit records. The fastest way to see what "loaded"
-- costs: compare a transport carrying something against an identical one that
-- is not.
function unitcmp(slotA, slotB)
  local a = unitbase() + slotA * UNIT_STRIDE
  local b = unitbase() + slotB * UNIT_STRIDE
  console:log(string.format("slot %d vs slot %d", slotA, slotB))
  local diffs = 0
  for i = 0, 11 do
    local x, y = emu:read8(a + i), emu:read8(b + i)
    if x ~= y then
      console:log(string.format("  +%-2d  %3d  vs %3d", i, x, y))
      diffs = diffs + 1
    end
  end
  console:log(string.format("  %d byte(s) differ", diffs))
end

-- HP address of one unit slot, for reading before/after an attack.
function hpaddr(i)
  local a = unitbase() + i * UNIT_STRIDE + HP_OFF
  console:log(string.format("unit %d hp at 0x%08X = %d", i, a, emu:read8(a)))
  return a
end

-- ---------------------------------------------------------------------------
-- MILESTONE 1: the map and the armies
--
-- The game addresses map-sized data through ROW POINTER TABLES: a run of 32-bit
-- pointers, one per map row, each pointing at a row of bytes. The AI loop at
-- 0x08060B1C does exactly this via the table at 0x03003600:
--     row = [0x03003600 + y*4] ; value = row[x]
-- but it sign-extends that byte and requires it > 0, which reads more like a
-- reachability/scratch array than terrain. So do NOT assume 0x03003600 is the
-- map -- find the tables empirically and look at what is in them.
--
-- A row-pointer table is easy to spot: consecutive words that are all valid RAM
-- pointers separated by a CONSTANT positive delta. That delta is the row
-- stride, which hands you the map width for free.
-- ---------------------------------------------------------------------------

local function isram(p)
  return (p >= 0x02000000 and p < 0x02040000)
      or (p >= 0x03000000 and p < 0x03008000)
end

-- Find candidate row-pointer tables. minrows should be about your map height.
function rowtables(minrows)
  minrows = minrows or 8
  local found = 0
  for _, r in ipairs({ { 0x03000000, 0x8000 }, { 0x02000000, 0x40000 } }) do
    local base, len = r[1], r[2]
    local a = base
    while a < base + len - 8 do
      local p0, p1 = emu:read32(a), emu:read32(a + 4)
      if isram(p0) and isram(p1) and p1 > p0 and (p1 - p0) <= 64 then
        local delta, n = p1 - p0, 2
        while true do
          local nxt = emu:read32(a + n * 4)
          if isram(nxt) and nxt - emu:read32(a + (n - 1) * 4) == delta then
            n = n + 1
          else
            break
          end
        end
        if n >= minrows then
          console:log(string.format(
            "  0x%08X: %2d rows, stride %d, first row @0x%08X", a, n, delta, p0))
          found = found + 1
          a = a + n * 4
        end
      end
      a = a + 4
    end
  end
  console:log(string.format("%d candidate row-pointer table(s)", found))
end

-- Dump the 2D array behind a row-pointer table.
function grid(tableaddr, rows, cols)
  rows = rows or 12
  cols = cols or 20
  console:log(string.format("grid via 0x%08X, %dx%d", tableaddr, cols, rows))
  for y = 0, rows - 1 do
    local row = emu:read32(tableaddr + y * 4)
    local out = {}
    for x = 0, cols - 1 do
      out[#out + 1] = string.format("%3d", emu:read8(row + x))
    end
    console:log(string.format("  y=%2d  %s", y, table.concat(out, " ")))
  end
end

-- Find the terrain map as a FLAT array. rowtables() turned up only the
-- movement-range scratch field (0x03003600, a flood fill where 255 is
-- unreachable), so terrain is evidently not behind row pointers.
--
-- Scans for w*h consecutive cells that look like terrain indices: small values,
-- several distinct ones, no single value swamping everything. Checks both u8
-- and u16, since the ROM stores maps as u16 and RAM may keep that.
function findmap(w, h, near)
  w = w or 15
  h = h or 10
  local n = w * h
  local hits = 0
  for _, unit in ipairs({ 1, 2 }) do
    local step = unit
    local lo, hi = 0x02000000, 0x02040000 - n * step
    for a = lo, hi, 2 do
      local counts, distinct, ok, total = {}, 0, true, 0
      for i = 0, n - 1 do
        local v = (unit == 1) and emu:read8(a + i) or emu:read16(a + i * 2)
        if v > 512 then ok = false break end
        if not counts[v] then counts[v] = 0; distinct = distinct + 1 end
        counts[v] = counts[v] + 1
        if counts[v] > total then total = counts[v] end
      end
      if ok and distinct >= 4 and distinct <= 40 and total < n * 0.7 then
        console:log(string.format("  u%d @0x%08X  %d distinct, commonest %d/%d",
          unit * 8, a, distinct, total, n))
        hits = hits + 1
        if hits > 24 then
          console:log("  (stopping, too many -- narrow with near=)")
          return
        end
      end
    end
  end
  console:log(string.format("%d candidate flat map array(s)", hits))
end

-- Match a known row of terrain by its EQUIVALENCE PATTERN rather than by value.
--
-- We do not know which index means "river" yet, so matching absolute values is
-- impossible. But we do know which tiles in a row are the SAME as each other,
-- and that is enough: canonicalise both the candidate row and the known row to
-- "first distinct value = A, second = B, ..." and compare. A 15-tile pattern
-- with several distinct terrains is extremely selective.
--
--   matchrow(5, "ABBBBCBBBBBBBBA", 15, 10)
-- means row 5 is: something, then four of a second thing, then a third thing,
-- and so on. Same letter = same terrain; the letters themselves are arbitrary.
local function canon(vals)
  local seen, out, nid = {}, {}, 0
  for i = 1, #vals do
    local v = vals[i]
    if seen[v] == nil then seen[v] = nid; nid = nid + 1 end
    out[i] = string.char(65 + (seen[v] % 26))
  end
  return table.concat(out)
end

local function canonstr(s)
  local vals = {}
  for i = 1, #s do vals[i] = s:sub(i, i) end
  return canon(vals)
end

function matchrow(y, pattern, w, h, unit)
  w, h, unit = w or 15, h or 10, unit or 0
  local want = canonstr(pattern:upper())
  if #want ~= w then
    console:log(string.format("pattern is %d long but width is %d", #want, w))
    return
  end
  local hits = 0
  for _, u in ipairs(unit == 0 and { 1, 2 } or { unit }) do
    local n = w * h
    for a = 0x02000000, 0x02040000 - n * u, 2 do
      local vals, ok = {}, true
      for x = 0, w - 1 do
        local i = y * w + x
        local v = (u == 1) and emu:read8(a + i) or emu:read16(a + i * u)
        if v > 512 then ok = false break end
        vals[x + 1] = v
      end
      if ok and canon(vals) == want then
        console:log(string.format("  u%d @0x%08X  row %d = %s",
          u * 8, a, y, table.concat(vals, ",")))
        hits = hits + 1
        if hits > 20 then console:log("  (stopping)"); return end
      end
    end
  end
  console:log(string.format("%d array(s) whose row %d matches that pattern", hits, y))
end

-- ---------------------------------------------------------------------------
-- MAP LOCATION. No pointer to the terrain array exists: it is absent from the
-- ROM as a literal, absent from the EWRAM pointer table at 0x282CAC..0x282CC4
-- (which holds only seven entries, none of them the map), and a live RAM scan
-- found nothing holding its address. It survived a map switch unchanged, so it
-- is evidently a static allocation the game never re-points.
--
-- So we use a known address and VERIFY it, rather than trusting it. Dimensions
-- come from the movement-range row table at 0x03003600, which is at a fixed
-- IWRAM address and whose row count and pointer stride are the map's height and
-- width. verifymap() then checks the terrain array is self-consistent with the
-- unit array, which is what will catch the address moving.
-- ---------------------------------------------------------------------------
MAP_ADDR = 0x02016C2A

-- Height and width, read from the movement-range row table.
function mapdims()
  local base = 0x03003600
  local p0, p1 = emu:read32(base), emu:read32(base + 4)
  local w = p1 - p0
  local h = 1
  while true do
    local a = emu:read32(base + h * 4)
    local b = emu:read32(base + (h - 1) * 4)
    if a - b ~= w then break end
    h = h + 1
  end
  return w, h
end

-- Land units must not stand on water and naval units must not stand on land.
-- If MAP_ADDR ever drifts this fails loudly instead of yielding a plausible
-- but wrong board.
local WATER = { [7] = true, [19] = true }             -- Sea, Reef
local NAVAL_OK = { [7] = true, [11] = true, [13] = true, [19] = true }
local NAVAL_UNIT = { [21] = true, [22] = true, [23] = true, [24] = true }

function verifymap(addr)
  addr = addr or MAP_ADDR
  local w, h = mapdims()
  console:log(string.format("map %dx%d from the 0x03003600 row table, terrain @0x%08X",
    w, h, addr))
  local bad, checked = 0, 0
  local base = unitbase()
  for i = 0, 255 do
    local a = base + i * UNIT_STRIDE
    local t = emu:read8(a)
    if t >= 1 and t <= 24 then
      local x, y = emu:read8(a + 2), emu:read8(a + 3)
      if x < w and y < h then
        local cell = emu:read8(addr + y * w + x) % 32
        checked = checked + 1
        local naval = NAVAL_UNIT[t]
        local wrong = (naval and not NAVAL_OK[cell]) or (not naval and WATER[cell])
        if wrong then
          bad = bad + 1
          console:log(string.format("  MISMATCH: %s at (%d,%d) on terrain %s",
            TYPE_NAMES[t] or t, x, y, TERRAIN_NAMES[cell] or cell))
        end
      end
    end
  end
  if bad == 0 then
    console:log(string.format("  OK: all %d units stand on terrain they can occupy", checked))
  else
    console:log(string.format("  %d/%d units on impossible terrain -- MAP_ADDR is wrong,",
      bad, checked))
    console:log("  re-find it with matchrow()")
  end
  return bad == 0
end

-- Find every RAM location holding a given 32-bit value -- i.e. pointers TO an
-- address. Needed because the map array's address is NOT a ROM constant: a
-- scan of the whole ROM for it (and for the few bytes before it) found nothing,
-- so it is computed at runtime and must be reachable through a RAM pointer.
--
-- Self-contained on purpose so it does not depend on declaration order.
function findptr(target)
  local needle = string.char(target % 256,
                             math.floor(target / 256) % 256,
                             math.floor(target / 65536) % 256,
                             math.floor(target / 16777216) % 256)
  local total = 0
  for _, r in ipairs({ { 0x02000000, 0x40000, "EWRAM" },
                       { 0x03000000, 0x08000, "IWRAM" } }) do
    local data = emu:readRange(r[1], r[2])
    local i = 1
    while true do
      local p = string.find(data, needle, i, true)
      if not p then break end
      console:log(string.format("  %s 0x%08X holds 0x%08X",
        r[3], r[1] + p - 1, target))
      total = total + 1
      i = p + 1
      if total > 40 then console:log("  (stopping)"); return end
    end
  end
  console:log(string.format("%d location(s) hold 0x%08X", total, target))
end

-- Show the bytes just before a map array. The array being at an ODD address
-- suggests a header in front of it -- width and height would be the obvious
-- candidates, and would explain the offset.
function maphdr(addr, back)
  back = back or 16
  local out = {}
  for i = -back, 3 do
    out[#out + 1] = string.format("%s%d", i == 0 and ">" or "", emu:read8(addr + i))
  end
  console:log(string.format("bytes 0x%08X-0x%08X (> marks the array start):",
    addr - back, addr + 3))
  console:log("  " .. table.concat(out, " "))
  console:log("  looking for your map's width and height as adjacent bytes")
end

-- ---------------------------------------------------------------------------
-- TERRAIN. The map cell is a bitfield: low 5 bits terrain, high 3 bits owner.
--     terrain = v % 32,  owner = floor(v / 32)   (0 = neutral, 1..4 = players)
-- Confirmed against four owned bases reading 46/78/110/142, i.e. 14 + 32*n,
-- and two HQs at 40/72 = 8 + 32*n. It is also why every terrain id is under 32.
-- ---------------------------------------------------------------------------
TERRAIN_NAMES = {
  [1] = "Plain", [2] = "River", [3] = "Mountain", [4] = "Wood", [5] = "Road",
  [6] = "City", [7] = "Sea", [8] = "HQ", [10] = "Airport", [11] = "Port",
  [12] = "Bridge", [13] = "Shoal", [14] = "Base", [19] = "Reef",
}
TERRAIN_SHORT = {
  [1] = "..", [2] = "~~", [3] = "^^", [4] = "ww", [5] = "==",
  [6] = "Ci", [7] = "SS", [8] = "HQ", [10] = "Ap", [11] = "Pt",
  [12] = "bb", [13] = "sh", [14] = "Ba", [19] = "rf",
}

function terrainat(addr, w, x, y)
  local v = emu:read8(addr + y * w + x)
  return v % 32, math.floor(v / 32)
end

-- Render the map with terrain names and ownership. Properties show their owner
-- as a digit, so P1's base prints "Ba1".
function terrain(addr, w, h)
  w, h = w or 15, h or 10
  console:log(string.format("terrain @0x%08X, %dx%d", addr, w, h))
  local unknown = {}
  for y = 0, h - 1 do
    local out = {}
    for x = 0, w - 1 do
      local t, owner = terrainat(addr, w, x, y)
      local s = TERRAIN_SHORT[t]
      if not s then
        s = string.format("?%d", t)
        unknown[t] = true
      end
      out[#out + 1] = s .. (owner > 0 and tostring(owner) or " ")
    end
    console:log(string.format("  y=%2d %s", y, table.concat(out, " ")))
  end
  local u = {}
  for t in pairs(unknown) do u[#u + 1] = t end
  if #u > 0 then
    console:log("unknown terrain ids present: " .. table.concat(u, ", "))
  end
  console:log("  .. Plain  ~~ River  ^^ Mountain  ww Wood  == Road  bb Bridge")
  console:log("  Ci City  Ba Base  Ap Airport  Pt Port  HQ HQ  SS Sea  sh Shoal  rf Reef")
  console:log("  trailing digit = owning player, blank = neutral")
end

-- Dump a flat w*h array as a grid. unit is 1 for bytes, 2 for u16.
function flatgrid(addr, w, h, unit)
  w, h, unit = w or 15, h or 10, unit or 1
  console:log(string.format("flat grid @0x%08X, %dx%d, u%d", addr, w, h, unit * 8))
  for y = 0, h - 1 do
    local out = {}
    for x = 0, w - 1 do
      local i = y * w + x
      local v = (unit == 1) and emu:read8(addr + i) or emu:read16(addr + i * 2)
      out[#out + 1] = string.format("%3d", v)
    end
    console:log(string.format("  y=%2d  %s", y, table.concat(out, " ")))
  end
end

-- Raw hex of the army records, including one BEFORE the array base.
--
-- Needed because the filtered view was ambiguous: a known funds value showed up
-- in records 2-4 but not record 1, which either means the array is offset by a
-- record or funds is not where it looked. Guessing between those would put a
-- wrong offset into the state reader, so read the bytes instead.
function armyhex(n, back)
  n, back = n or 4, back or 1
  local base = emu:read32(0x08282CBC)
  console:log(string.format("army array @0x%08X, stride 0x68, showing %d record(s) before",
    base, back))
  for i = -back, n - 1 do
    local a = base + i * 0x68
    console:log(string.format("--- record %d @0x%08X %s", i, a,
      i < 0 and "(BEFORE the array base)" or ""))
    for row = 0, 6 do
      local off = row * 16
      if off < 0x68 then
        local hex, dec = {}, {}
        for k = 0, 15 do
          if off + k < 0x68 then
            local v = emu:read8(a + off + k)
            hex[#hex + 1] = string.format("%02X", v)
            dec[#dec + 1] = string.format("%3d", v)
          end
        end
        console:log(string.format("  +%02X  %s", off, table.concat(hex, " ")))
        console:log(string.format("       %s", table.concat(dec, " ")))
      end
    end
  end
  console:log("Look for your on-screen funds as a 32-bit little-endian value.")
end

-- Dump the army structs. Stride 0x68 comes from the damage path, which does
-- `movs r0,#0x68 ; muls r0,r6,r0` before indexing [0x08282CBC]. Funds is the
-- easy field to identify: you can read it off the screen.
-- Army records. The array is 1-INDEXED: record 0 is a dummy and P1 is record 1,
-- which matches the terrain ownership encoding where 0 is neutral and 1..4 are
-- players. Army record N therefore owns tiles tagged owner N.
--
--   +00 u32  available funds  (spendable now; zero for players whose turn it
--                              is not, which is what pinned this offset)
--   +08 u32  incoming funds   (income; equal for all players on a fresh map)
--   +1A u8   player number, 1..4
--   +1D u8   CO-related, differs per CO
--   +1E u8   the byte the damage path uses as its CO-modifier table index
function armyaddr(player)
  return emu:read32(0x08282CBC) + player * 0x68
end

function armies(n)
  n = n or 4
  console:log(string.format("army array @0x%08X, stride 0x68, 1-indexed",
    emu:read32(0x08282CBC)))
  for p = 1, n do
    local a = armyaddr(p)
    console:log(string.format(
      "  P%d @0x%08X  funds=%d  income=%d  playerno=%d  +1D=%d +1E=%d",
      p, a, emu:read32(a), emu:read32(a + 8), emu:read8(a + 0x1A),
      emu:read8(a + 0x1D), emu:read8(a + 0x1E)))
  end
  console:log("  funds is 0 for everyone except the player whose turn it is")
end

local REGIONS = {
  { base = 0x02000000, len = 0x40000, name = "EWRAM" },
  { base = 0x03000000, len = 0x08000, name = "IWRAM" },
}
local MAX_PRINT = 60

local prev = nil      -- previous snapshot, per region
local cands = nil     -- nil means "every address is still a candidate"

local function readRegion(r)
  if emu.readRange then return emu:readRange(r.base, r.len) end
  local t = {}
  for i = 0, r.len - 1 do t[i + 1] = string.char(emu:read8(r.base + i)) end
  return table.concat(t)
end

local function snapshot()
  local s = {}
  for i, r in ipairs(REGIONS) do s[i] = readRegion(r) end
  return s
end

local function count()
  if cands == nil then return -1 end
  local n = 0
  for _ in pairs(cands) do n = n + 1 end
  return n
end

local function report(what)
  local n = count()
  if n < 0 then
    console:log(what .. ": all addresses still candidates")
  else
    console:log(string.format("%s: %d candidates", what, n))
    if n > 0 and n <= MAX_PRINT then list() end
  end
end

-- Take the baseline snapshot. Call this immediately before the in-game action.
function mark()
  prev = snapshot()
  console:log("marked. now perform the action in game, then call dec()/unc().")
end

-- Generic filter: keep addresses where cmp(oldByte, newByte) is true.
local function filter(cmp, label)
  if not prev then console:log("call mark() first"); return end
  local cur = snapshot()
  local kept = {}
  for i, r in ipairs(REGIONS) do
    local a, b = prev[i], cur[i]
    if cands == nil then
      for j = 1, r.len do
        local o, n = string.byte(a, j), string.byte(b, j)
        if cmp(o, n) then kept[r.base + j - 1] = true end
      end
    else
      for addr in pairs(cands) do
        if addr >= r.base and addr < r.base + r.len then
          local j = addr - r.base + 1
          local o, n = string.byte(a, j), string.byte(b, j)
          if cmp(o, n) then kept[addr] = true end
        end
      end
    end
  end
  cands = kept
  prev = cur
  report(label)
end

function dec() filter(function(o, n) return n < o end, "decreased") end
function inc() filter(function(o, n) return n > o end, "increased") end
function unc() filter(function(o, n) return n == o end, "unchanged") end
function chg() filter(function(o, n) return n ~= o end, "changed") end

-- Keep only candidates whose CURRENT value is within [lo,hi]. HP is 1..100.
function range(lo, hi)
  if cands == nil then console:log("filter with dec()/chg() first"); return end
  local kept = {}
  for addr in pairs(cands) do
    local v = emu:read8(addr)
    if v >= lo and v <= hi then kept[addr] = true end
  end
  cands = kept
  report(string.format("range(%d,%d)", lo, hi))
end

-- Convenience: the unit shows N bars, so internal HP is 10N-9 .. 10N.
-- Much tighter than range(1,100) and costs nothing to apply.
function bars(n)
  range(n * 10 - 9, n * 10)
end

-- Structural filter. A unit record is: type at +0 (1..24), map x at +2,
-- map y at +3 (both small). We do not know HP's offset within the record, so
-- try every plausible offset K and keep the candidate if ANY K makes the bytes
-- around it look like a real unit record. Kills volatile counters that happen
-- to sit in the right value range but have no record structure around them.
function unitlike(maxoff, maxxy)
  maxoff, maxxy = maxoff or 32, maxxy or 40
  if cands == nil then console:log("filter with dec()/chg() first"); return end
  local kept = {}
  for addr in pairs(cands) do
    for k = 0, maxoff do
      local base = addr - k
      local t = emu:read8(base)
      local x = emu:read8(base + 2)
      local y = emu:read8(base + 3)
      if t >= 1 and t <= 24 and x < maxxy and y < maxxy then
        kept[addr] = true
        break
      end
    end
  end
  cands = kept
  report(string.format("unitlike(maxoff=%d)", maxoff))
end

function list()
  if cands == nil then console:log("no filtering yet"); return end
  local t = {}
  for addr in pairs(cands) do t[#t + 1] = addr end
  table.sort(t)
  for i = 1, math.min(#t, MAX_PRINT) do
    console:log(string.format("  0x%08X = %3d", t[i], emu:read8(t[i])))
  end
  if #t > MAX_PRINT then
    console:log(string.format("  ... and %d more", #t - MAX_PRINT))
  end
end

-- Multi-row hexdump, 16 bytes per line with addresses. Use this to see the
-- unit ARRAY: once you know one record, the repeating pattern gives the stride.
function hexdump(addr, count)
  count = count or 128
  local start = addr - (addr % 16)
  for row = 0, math.ceil(count / 16) - 1 do
    local a = start + row * 16
    local hex, txt = {}, {}
    for i = 0, 15 do
      local v = emu:read8(a + i)
      hex[#hex + 1] = string.format("%02X", v)
      txt[#txt + 1] = string.format("%3d", v)
    end
    console:log(string.format("%08X  %s", a, table.concat(hex, " ")))
    console:log(string.format("          %s", table.concat(txt, " ")))
  end
end

-- Given one record's HP address and the offset of HP within the record, scan
-- forward for records with the same shape and report the stride.
function scanarray(hpaddr, hpoff, n)
  hpoff, n = hpoff or 4, n or 24
  local base = hpaddr - hpoff
  console:log(string.format("assuming record base 0x%08X (hp at +%d)", base, hpoff))
  local last = nil
  for i = 0, n - 1 do
    for _, stride in ipairs({ 8, 12, 16, 20, 24, 28, 32, 40, 48 }) do
      local a = base + i * stride
      local t = emu:read8(a)
      if t >= 1 and t <= 24 and emu:read8(a + 2) < 40 and emu:read8(a + 3) < 40
         and emu:read8(a + hpoff) >= 1 and emu:read8(a + hpoff) <= 100 then
        if last ~= stride then
          console:log(string.format("  stride %2d: 0x%08X type=%2d x=%2d y=%2d hp=%3d",
            stride, a, t, emu:read8(a + 2), emu:read8(a + 3), emu:read8(a + hpoff)))
        end
      end
    end
  end
end

-- Show bytes around an address. Unit type/x/y should be visible nearby.
function dump(addr, before, after)
  before, after = before or 8, after or 16
  local lo, hi = addr - before, addr + after
  local out = {}
  for a = lo, hi do
    local v = emu:read8(a)
    out[#out + 1] = string.format(a == addr and "[%d]" or "%d", v)
  end
  console:log(string.format("0x%08X-0x%08X:", lo, hi))
  console:log("  " .. table.concat(out, " "))
end

-- With 2+ surviving candidates from different units, guess the array stride.
function stride()
  if cands == nil then console:log("nothing to compare"); return end
  local t = {}
  for addr in pairs(cands) do t[#t + 1] = addr end
  table.sort(t)
  if #t < 2 then console:log("need at least 2 candidates"); return end
  for i = 1, #t - 1 do
    console:log(string.format("  0x%08X -> 0x%08X   delta %d (0x%X)",
      t[i], t[i + 1], t[i + 1] - t[i], t[i + 1] - t[i]))
  end
end

function reset()
  cands, prev = nil, nil
  console:log("reset: all addresses are candidates again")
end

console:log("AW ram hunter loaded.")
console:log("  mark()  -> snapshot before an action")
console:log("  dec() inc() unc() chg()  -> filter by how bytes moved")
console:log("  range(1,100)  list()  dump(addr)  stride()  reset()")

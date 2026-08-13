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
-- Bit 7 is used as a flag bit in more than one field, so mask before reading.
--   +4 (u16): hp = v & 0x7F, ammo = v >> 7
--   +6 (u8) : fuel = v & 0x7F, bit 7 is a flag
function unithp(a) return emu:read16(a + HP_OFF) % 128 end
function unitammo(a) return math.floor(emu:read16(a + HP_OFF) / 128) end
function unitfuel(a) return emu:read8(a + 6) % 128 end
function unitfuelflag(a) return emu:read8(a + 6) >= 128 end

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
      local marks = ""
      if emu:read8(a + 1) ~= 0 then marks = marks .. " acted" end
      if unitfuelflag(a) then marks = marks .. " ?bit7" end
      console:log(string.format(
        "  [%3d] 0x%08X P%d %-11s (%2d,%2d) hp=%3d (%2d bars) ammo=%2d fuel=%3d%s%s",
        i, a, army + 1, TYPE_NAMES[t] or "?",
        emu:read8(a + 2), emu:read8(a + 3), hp, math.ceil(hp / 10),
        ammo, unitfuel(a), marks, flag))
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

-- Dump the army structs. Stride 0x68 comes from the damage path, which does
-- `movs r0,#0x68 ; muls r0,r6,r0` before indexing [0x08282CBC]. Funds is the
-- easy field to identify: you can read it off the screen.
function armies(n)
  n = n or 4
  local base = emu:read32(0x08282CBC)
  console:log(string.format("army array @0x%08X, stride 0x68", base))
  for i = 0, n - 1 do
    local a = base + i * 0x68
    local u16, u32 = {}, {}
    for off = 0, 0x66, 2 do
      local v = emu:read16(a + off)
      if v > 0 and v <= 99999 then
        u16[#u16 + 1] = string.format("+%02X=%d", off, v)
      end
    end
    for off = 0, 0x64, 4 do
      local v = emu:read32(a + off)
      if v > 999 and v <= 999999 then
        u32[#u32 + 1] = string.format("+%02X=%d", off, v)
      end
    end
    console:log(string.format("P%d @0x%08X  co bytes +1D=%d +1E=%d",
      i + 1, a, emu:read8(a + 0x1D), emu:read8(a + 0x1E)))
    console:log("     u16 candidates: " .. table.concat(u16, " "))
    console:log("     u32 candidates: " .. table.concat(u32, " "))
  end
  console:log("Compare against the funds shown on screen to pin the offset.")
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

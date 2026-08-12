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
--     +1    acted-this-turn flag (hypothesis: set only on the unit that moved)
--     +2    map x
--     +3    map y
--     +4:2  u16 bitfield -> hp = v & 0x7F, ammo = v >> 7
--     +6    fuel
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
-- hp and ammo share a 16-bit bitfield at +4: hp in bits 0-6, ammo above.
function unithp(a) return emu:read16(a + HP_OFF) % 128 end
function unitammo(a) return math.floor(emu:read16(a + HP_OFF) / 128) end

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
      console:log(string.format(
        "  [%3d] 0x%08X P%d %-11s (%2d,%2d) hp=%3d (%2d bars) ammo=%2d fuel=%3d%s%s",
        i, a, army + 1, TYPE_NAMES[t] or "?",
        emu:read8(a + 2), emu:read8(a + 3), hp, math.ceil(hp / 10),
        ammo, emu:read8(a + 6),
        emu:read8(a + 1) ~= 0 and " acted" or "", flag))
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

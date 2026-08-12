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

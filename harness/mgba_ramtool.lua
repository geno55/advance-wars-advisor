-- RAM search/diff tool for mGBA, aimed at locating the unit array.
--
-- This is the groundwork for the state reader (milestone 1): once you know
-- where unit HP lives, calibration stops depending on squinting at the HP
-- display and the advisor can read the board automatically.
--
-- NOTE: written against the mGBA 0.10+ scripting API and NOT executed by the
-- author -- no emulator was available. Expect to fix a typo or two on first run.
--
-- Load with Tools > Scripting > Load script, then drive it from the console:
--     search(100)     -- units at full health hold 100 somewhere
--     -- (attack something in game so its HP changes to, say, 55)
--     refine(55)
--     show()
--     snap(); -- do one action in game --; diff()

local EWRAM, EWRAM_LEN = 0x02000000, 0x40000
local IWRAM, IWRAM_LEN = 0x03000000, 0x08000
local REGIONS = { { EWRAM, EWRAM_LEN, "EWRAM" }, { IWRAM, IWRAM_LEN, "IWRAM" } }

local candidates = nil
local snapshot = nil
local MAX_PRINT = 40

local function readRegion(base, len)
  if emu.readRange then return emu:readRange(base, len) end
  local t = {}
  for i = 0, len - 1 do t[i + 1] = string.char(emu:read8(base + i)) end
  return table.concat(t)
end

local function eachByte(fn)
  for _, r in ipairs(REGIONS) do
    local base, len, name = r[1], r[2], r[3]
    local data = readRegion(base, len)
    for i = 1, #data do fn(base + i - 1, string.byte(data, i), name) end
  end
end

-- Start a fresh search for every address currently holding `value`.
function search(value)
  candidates = {}
  local n = 0
  eachByte(function(addr, b)
    if b == value then candidates[addr] = true; n = n + 1 end
  end)
  console:log(string.format("search(%d): %d candidate addresses", value, n))
end

-- Keep only candidates that now hold `value`. Repeat as the value changes.
function refine(value)
  if not candidates then console:log("call search() first"); return end
  local kept, n = {}, 0
  for addr in pairs(candidates) do
    if emu:read8(addr) == value then kept[addr] = true; n = n + 1 end
  end
  candidates = kept
  console:log(string.format("refine(%d): %d candidates remain", value, n))
  if n <= MAX_PRINT then show() end
end

function show()
  if not candidates then console:log("no active search"); return end
  local list = {}
  for addr in pairs(candidates) do list[#list + 1] = addr end
  table.sort(list)
  for i = 1, math.min(#list, MAX_PRINT) do
    console:log(string.format("  0x%08X = %d", list[i], emu:read8(list[i])))
  end
  if #list > MAX_PRINT then
    console:log(string.format("  ... and %d more", #list - MAX_PRINT))
  end
end

-- Snapshot / diff: useful for spotting the whole unit record at once, not just
-- the HP byte. Snap, take exactly one in-game action, then diff.
function snap()
  snapshot = {}
  for _, r in ipairs(REGIONS) do
    snapshot[r[3]] = readRegion(r[1], r[2])
  end
  console:log("snapshot taken")
end

function diff()
  if not snapshot then console:log("call snap() first"); return end
  local changes = 0
  for _, r in ipairs(REGIONS) do
    local base, len, name = r[1], r[2], r[3]
    local before, after = snapshot[name], readRegion(base, len)
    for i = 1, len do
      local a, b = string.byte(before, i), string.byte(after, i)
      if a ~= b then
        changes = changes + 1
        if changes <= MAX_PRINT then
          console:log(string.format("  0x%08X  %3d -> %3d", base + i - 1, a, b))
        end
      end
    end
  end
  console:log(string.format("diff: %d bytes changed", changes))
  if changes > MAX_PRINT then
    console:log("(too noisy -- diff across a smaller action, or snap closer to it)")
  end
end

console:log("AW ram tool loaded: search(v) refine(v) show() snap() diff()")

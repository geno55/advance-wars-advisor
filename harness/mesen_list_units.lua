-- One-shot: list the fixture's live unit records (slots 0..15, 64..79).
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local UNIT_PTR, MAP = 0x08282CB8, 0x02016C2A
local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function wait(n) for _ = 1, n do coroutine.yield() end end
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end
local state
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  emu.loadSavestate(state); wait(30)
  local log = io.open(OUT .. "units.log", "w")
  log:write(string.format("active=%d day=%d\n", r16(0x030036AC), r16(0x03004424)))
  for _, range in ipairs({{0, 15}, {64, 79}}) do
    for s = range[1], range[2] do
      local a = ua(s)
      if r8(a) ~= 0 then
        log:write(string.format("u%d type=%d flags=%02X x=%d y=%d hp=%d fuel=%d\n", s, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), r16(a + 4) % 128, r8(a + 6) % 128))
      end
    end
  end
  for y = 5, 9 do
    local t = {}
    for x = 0, 14 do t[#t + 1] = string.format("%02X", r8(MAP + y * 15 + x)) end
    log:write("row " .. y .. ": " .. table.concat(t, " ") .. "\n")
  end
  log:close(); emu.stop(0)
end
local co = coroutine.create(main)
local pending = false
emu.addEventCallback(function() pending = true end, emu.eventType.endFrame)
emu.addMemoryCallback(function()
  if not pending then return end
  pending = false
  if coroutine.status(co) ~= "dead" then
    local ok, err = coroutine.resume(co)
    if not ok then local ef = io.open(OUT .. "units_err.log", "w"); ef:write(tostring(err)); ef:close(); emu.stop(1) end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

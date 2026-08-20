-- Peek a parked savestate: army records, gate, cursor, screenshot. No inputs.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local GATE = 0x03004318
local CURX, CURY = 0x030033F0, 0x030033F1
local UNIT_PTR = 0x08282CB8
local DIMS = 0x030036E0

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function wait(n) for _ = 1, n do coroutine.yield() end end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); local state = fh:read("*a"); fh:close()
  emu.loadSavestate(state); wait(30)
  local log = io.open(OUT .. "peek.log", "w")
  log:write(string.format("gate=%d cursor=%d,%d dims=%dx%d player=%d\n",
    r8(GATE), r8(CURX), r8(CURY), r8(DIMS), r8(DIMS + 2),
    r8(0x03004424) / 32))
  for n = 0, 4 do
    local a = ARMY + n * STRIDE
    log:write(string.format(
      "army%d funds=%d p=%d co=%d blk=%d meter=%d w22=%d w14=%d\n",
      n, r32(a), r8(a + 0x1a), r8(a + 0x1d), r8(a + 0x1e),
      r16(a + 0x20), r16(a + 0x22), r16(a + 0x14)))
  end
  -- first 12 unit slots, and slots 60..75, to find who is where
  local ub = r32(UNIT_PTR)
  for s = 0, 75 do
    local a = ub + s * 12
    local t, fl, x, y, hp = r8(a), r8(a + 1), r8(a + 2), r8(a + 3),
      r16(a + 4) % 128
    if t ~= 0 then
      log:write(string.format("unit%d type=%d flags=%02X x=%d y=%d hp=%d\n",
        s, t, fl, x, y, hp))
    end
  end
  log:close()
  local png = emu.takeScreenshot()
  local sh = io.open(OUT .. "peek.png", "wb"); sh:write(png); sh:close()
  emu.stop(0)
end

local co = coroutine.create(main)
local pending = false
emu.addEventCallback(function() pending = true end, emu.eventType.endFrame)
emu.addMemoryCallback(function()
  if not pending then return end
  pending = false
  if coroutine.status(co) ~= "dead" then
    local ok, err = coroutine.resume(co)
    if not ok then
      local ef = io.open(OUT .. "peek_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

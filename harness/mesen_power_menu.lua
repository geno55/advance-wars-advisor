local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local CO_BASE, CO_STRIDE = 0x08284A0C, 292
local CURX, CURY = 0x030033F0, 0x030033F1
local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end
local function w32(a, v) emu.write32(a, v, emu.memType.gbaMemory) end
local held = {}
emu.addEventCallback(function() pcall(function() emu.setInput(held, 0) end) end, emu.eventType.inputPolled)
local function wait(n) for _ = 1, n do coroutine.yield() end end
local function tap(btn, hold, gap) held = { [btn] = true }; wait(hold or 6); held = {}; wait(gap or 24) end
local function goto_tile(x, y)
  for _ = 1, 90 do
    local cx, cy = r8(CURX), r8(CURY)
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left") elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local log = io.open(OUT .. "powermenu.log", "w")
local function shot(tag) local png = emu.takeScreenshot(); local fh = io.open(OUT .. "pm-" .. tag .. ".png", "wb"); fh:write(png); fh:close() end
local state
local a2 = ARMY + 2 * STRIDE
local function case(name, meter, latch, active, uses)
  emu.loadSavestate(state); wait(30)
  if meter then w32(a2 + 0x20, meter) end
  if latch then w8(a2 + 0x24, latch) end
  if active then w8(a2 + 0x1e, active) end
  if uses then w8(a2 + 0x25, uses) end
  log:write(string.format("== %s meter=%d latch=%d active=%d uses=%d cost=%d\n", name, r32(a2 + 0x20), r8(a2 + 0x24), r8(a2 + 0x1e), r8(a2 + 0x25), r32(CO_BASE + r8(a2 + 0x1d) * CO_STRIDE + 8)))
  goto_tile(2, 6); tap("a", 8, 60); wait(40); shot(name)
  for _ = 1, 3 do tap("b", 8, 40) end
end
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  case("M1-latch-only", 0, 1)
  case("M2-meter-only", 30000, 0)
  case("M3-both", 30000, 1)
  case("M4-both-but-active", 30000, 1, 1)
  case("M5-latch-active", 0, 1, 1)
  case("M6-uses1-meter30000", 30000, 0, 0, 1)
  case("M7-uses1-meter36000", 36000, 0, 0, 1)
  case("M8-uses1-meter35990", 35990, 0, 0, 1)
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
    if not ok then local ef = io.open(OUT .. "powermenu_err.log", "w"); ef:write(tostring(err)); ef:close(); emu.stop(1) end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)


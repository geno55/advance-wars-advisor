-- Power-menu recon: fill P2's meter, set the ready flag, open the map menu.
-- Screenshots name the menu layout so the activation probe can drive it.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local CURX, CURY = 0x030033F0, 0x030033F1
local CO_BASE, CO_STRIDE = 0x08284A0C, 292

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end
local function w32(a, v) emu.write32(a, v, emu.memType.gbaMemory) end

local held = {}
emu.addEventCallback(function()
  pcall(function() emu.setInput(held, 0) end)
end, emu.eventType.inputPolled)
local function wait(n) for _ = 1, n do coroutine.yield() end end
local function tap(btn, hold, gap)
  held = { [btn] = true }; wait(hold or 6); held = {}; wait(gap or 24)
end
local function cursor() return r8(CURX), r8(CURY) end
local function goto_tile(x, y)
  for _ = 1, 80 do
    local cx, cy = cursor()
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left")
    elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end

local log = io.open(OUT .. "power_recon.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "pr-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end
local function army_row(n)
  local a = ARMY + n * STRIDE
  return string.format(
    "army%d co=%d blk=%d meter=%d ready=%d uses=%d",
    n, r8(a + 0x1d), r8(a + 0x1e), r32(a + 0x20), r8(a + 0x24), r8(a + 0x25))
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); local state = fh:read("*a"); fh:close()
  emu.loadSavestate(state); wait(30)

  local a2 = ARMY + 2 * STRIDE
  local co = r8(a2 + 0x1d)
  local cost = r32(CO_BASE + co * CO_STRIDE + 8)
  log:write(string.format("co=%d base_cost=%d\n", co, cost))
  w32(a2 + 0x20, cost)
  w8(a2 + 0x24, 1)
  log:write(army_row(2), "\n")

  goto_tile(8, 7)
  tap("a", 6, 45)
  shot("menu1"); log:write("menu opened\n")
  tap("down", 6, 20)
  shot("menu2")
  tap("down", 6, 20)
  shot("menu3")
  tap("down", 6, 20)
  shot("menu4")
  tap("down", 6, 20)
  shot("menu5")
  tap("down", 6, 20)
  shot("menu6")
  log:write(army_row(2), "\n")
  log:close()
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
      local ef = io.open(OUT .. "power_recon_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

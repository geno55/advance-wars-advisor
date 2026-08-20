-- Sonja probes, headless, from the powers-ON fixture (slot 2).
--  A: vision under fog. Write fog=1, set P2's CO (Andy / Sonja / Sonja+power),
--     drive a real Tank move to force a recompute, dump the game's own vision
--     arrays. The diffs ARE the trait.
--  B: the HP question. Damage a P1 Mech, set P1's CO (Andy / Sonja), hover it,
--     screenshot the panel -- with a READ watchpoint on Sonja's record header
--     bytes 0/1 in ROM, so whatever consumes them names its own code.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local CURX, CURY = 0x030033F0, 0x030033F1
local FOG = 0x0300431D
local VIS1, VIS2 = 0x0201763A, 0x02017B42
local W, H = 15, 13
-- Sonja record true base 0x08284A0C + 7*292; extract header at +0x24
local SONJA_HDR = 0x08284A0C + 7 * 292 + 0x24

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end

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
  for _ = 1, 90 do
    local cx, cy = cursor()
    if cx == x and cy == y then return true end
    if cx < x then tap("right") elseif cx > x then tap("left")
    elseif cy < y then tap("down") elseif cy > y then tap("up") end
  end
  return false
end
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end

local log = io.open(OUT .. "sonja.log", "w")
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "sj-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
end
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end

local watching = false
local read_pcs = {}
emu.addMemoryCallback(function(addr, value)
  if not watching then return end
  local pc = pc_of()
  local key = string.format("+%d pc=%08X", addr - SONJA_HDR, pc)
  read_pcs[key] = (read_pcs[key] or 0) + 1
end, emu.callbackType.read, SONJA_HDR, SONJA_HDR + 1,
   emu.cpuType.gba, emu.memType.gbaMemory)

local function dump_vis(tag)
  for name, base in pairs({ a = VIS1, b = VIS2 }) do
    for y = 0, H - 1 do
      local row = {}
      for x = 0, W - 1 do
        row[#row + 1] = string.format("%d", r8(base + y * W + x))
      end
      log:write(string.format("%s.%s y%02d %s\n",
        tag, name, y, table.concat(row)))
    end
  end
  log:flush()
end

local state
local function reload()
  emu.loadSavestate(state); wait(30)
end
local function move_tank()
  goto_tile(9, 7)
  tap("a", 6, 40)
  tap("left", 6, 20)
  tap("a", 6, 50)
  tap("a", 6, 60)          -- Wait
  wait(90)
end

local function vis_case(tag, co, blk)
  reload()
  w8(FOG, 1)
  local a2 = ARMY + 2 * STRIDE
  w8(a2 + 0x1d, co)
  if blk then w8(a2 + 0x1e, 1) end
  move_tank()
  log:write(string.format("== %s co=%d blk=%d fog=%d tank=%d,%d\n",
    tag, co, blk and 1 or 0, r8(FOG), r8(ua(71) + 2), r8(ua(71) + 3)))
  dump_vis(tag)
  shot(tag)
end

local function hp_case(tag, co)
  reload()
  local a1 = ARMY + 1 * STRIDE
  w8(a1 + 0x1d, co)
  local mech = ua(3)               -- P1 Mech at (7,6)
  local cur = r16(mech + 4)
  emu.write16(mech + 4, cur - cur % 128 + 45, emu.memType.gbaMemory)
  read_pcs = {}
  watching = true
  goto_tile(7, 6)
  wait(120)
  shot(tag)
  watching = false
  log:write(string.format("== %s co=%d mech_hp=%d\n", tag, co,
    r16(mech + 4) % 128))
  for k, n in pairs(read_pcs) do
    log:write(string.format("  R %s x%d\n", k, n))
  end
  log:flush()
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  vis_case("andy", 1, false)
  vis_case("sonja", 7, false)
  vis_case("sonjaP", 7, true)
  hp_case("hp-andy", 1)
  hp_case("hp-sonja", 7)
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
      local ef = io.open(OUT .. "sonja_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

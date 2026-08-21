-- Supply-task recon peek: full unit dump (fuel/ammo/cargo), map terrain+owner
-- around the P2 cluster, and the settings bytes the supply code reads
-- (0x03004357 free-repair, 0x030041E8 active base slot, 0x03004424 owner tag).
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local MAP_PTR = 0x08282CB4

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function wait(n) for _ = 1, n do coroutine.yield() end end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); local state = fh:read("*a"); fh:close()
  emu.loadSavestate(state); wait(30)
  local log = io.open(OUT .. "supply_peek.log", "w")
  local map = r32(MAP_PTR)
  local w, h = r16(map), r16(map + 2)
  log:write(string.format(
    "map=%08X dims=%dx%d tag4424=%04X base41E8=%d free_repair4357=%d " ..
    "gate4318=%d chg4317=%d luck4316=%d\n",
    map, w, h, r16(0x03004424), r16(0x030041E8), r8(0x03004357),
    r8(0x03004318), r8(0x03004317), r8(0x03004316)))
  for n = 0, 2 do
    local a = ARMY + n * STRIDE
    log:write(string.format("army%d funds=%d co=%d blk=%d\n",
      n, r32(a), r8(a + 0x1d), r8(a + 0x1e)))
  end
  local ub = r32(UNIT_PTR)
  for s = 0, 130 do
    local a = ub + s * 12
    local t = r8(a)
    if t ~= 0 then
      local v4 = r16(a + 4)
      log:write(string.format(
        "unit%d type=%d flags=%02X x=%d y=%d hp=%d ammo=%d cap=%d " ..
        "fuel_raw=%02X cargo7=%d\n",
        s, t, r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128,
        math.floor(v4 / 128) % 16, math.floor(v4 / 2048), r8(a + 6), r8(a + 7)))
    end
  end
  -- terrain + row offsets for the whole board
  for y = 0, h - 1 do
    local rowoff = r16(map + 0x4682 + y * 2)
    local row = {}
    for x = 0, w - 1 do
      row[#row + 1] = string.format("%02X", r8(map + 0x1432 + rowoff + x))
    end
    log:write(string.format("terr y=%d off=%d  %s\n", y, rowoff,
      table.concat(row, " ")))
  end
  -- tile->unit index (layer +0x12) for the same board
  for y = 0, h - 1 do
    local rowoff = r16(map + 0x4682 + y * 2)
    local row = {}
    for x = 0, w - 1 do
      row[#row + 1] = string.format("%02X", r8(map + 0x12 + rowoff + x))
    end
    log:write(string.format("uidx y=%d  %s\n", y, table.concat(row, " ")))
  end
  log:close()
  local png = emu.takeScreenshot()
  local sh = io.open(OUT .. "supply_peek.png", "wb"); sh:write(png); sh:close()
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
      local ef = io.open(OUT .. "supply_peek_err.log", "w")
      ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

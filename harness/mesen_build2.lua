local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local ARMY, STRIDE = 0x0201AB34, 0x68
local UNIT_PTR = 0x08282CB8
local MAP_PTR = 0x08282CB4
local CURX, CURY = 0x030033F0, 0x030033F1
local FUNDS2 = ARMY + 2 * STRIDE
local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
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
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end
local log = io.open(OUT .. "build2.log", "w")
local function shot(tag) local png = emu.takeScreenshot(); local fh = io.open(OUT .. "build2-" .. tag .. ".png", "wb"); fh:write(png); fh:close() end
local state
local function reload() emu.loadSavestate(state); wait(30) end
local function set_terr(x, y, v) local map = r32(MAP_PTR); w8(map + 0x1432 + r16(map + 0x4682 + y * 2) + x, v) end
local function slots() local t = {}; for s = 64, 127 do if r8(ua(s)) ~= 0 then t[#t + 1] = s end end; return table.concat(t, ",") end
local function case(name, x, y, terr, buy, funds, pre)
  reload(); log:write("== " .. name .. "\n")
  if funds then w32(FUNDS2, funds) end
  if terr then set_terr(x, y, terr) end
  if pre then pre() end
  log:write("  pre  funds=" .. r32(FUNDS2) .. " slots=" .. slots() .. "\n")
  goto_tile(x, y); tap("a", 8, 60); wait(40); shot(name)
  if buy then tap("a", 8, 90); wait(60); tap("a", 8, 90); wait(150) else for _ = 1, 3 do tap("b", 8, 40) end end
  log:write("  post funds=" .. r32(FUNDS2) .. " slots=" .. slots() .. "\n")
end
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  case("R1-own-HQ", 14, 1, nil, false)
  case("R2-neutral-base", 11, 6, 0x0E, false)
  case("R3-enemy-base", 11, 6, 0x2E, false)
  case("R4-exact-funds", 11, 6, 0x4E, true, 1000)
  case("R5-lowest-free-slot", 11, 6, 0x4E, true, nil, function() w8(ua(65), 0) end)
  case("R6-kanbei-airport", 11, 6, 0x4A, false, nil, function() w8(ARMY + 2 * STRIDE + 0x1d, 6) end)
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
    if not ok then local ef = io.open(OUT .. "build2_err.log", "w"); ef:write(tostring(err)); ef:close(); emu.stop(1) end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

-- The dived sub's concealment (DERIVATION 40 follow-up). Fixture: savestate
-- slot 2, P2's turn, fog written OFF. Row y=7 is written to Sea from x=6 to
-- x=10 and (7,6) too; P2's Tank 71 at (9,7) is typed Cruiser; on P1's turn
-- the Mech #3 at (7,6) is typed Sub, real-moved to (7,7) and (in the dive
-- setups) dived through its menu. Back on P2's turn the sub is at distance 2
-- from the Cruiser and adjacent to nothing of P2's.
--  H1  P2 view, sub dived, nothing adjacent: what does the game's own check
--      at 0x08023BFC return for the sub, is it drawn (screenshot), and does
--      the Cruiser's move grid mark (7,7) -- 255 = a visible blocker, a cost
--      = the fog-ambush "hidden, enterable" marking?
--  H2  the Cruiser moves to (8,7), adjacent: check value, menu (screenshot),
--      then A,A -- if Fire was offered the sub's HP drops by the table's 90+luck.
--  H3  the Cruiser confirms ONTO (7,7): ambush stop, or refused?
--  H0  control: same, sub surfaced.
local OUT = "C:/Users/geno5/Documents/Claude/advance-wars/advisor/harness/out/"
local MSS = "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_2.mss"
local UNIT_PTR, MAP_PTR, ARMY_PTR = 0x08282CB8, 0x08282CB4, 0x08282CBC
local CURX, CURY = 0x030033F0, 0x030033F1
local FOG = 0x0300431D
local MAP = 0x02016C2A
local W, SEA = 15, 7
local CHECK_ENTRY, CHECK_EXIT = 0x08023BFC, 0x08023D28
local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end
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
local log = io.open(OUT .. "subhide.log", "w")
local function L(s) log:write(s .. "\n"); log:flush() end
local function shot(tag) local png = emu.takeScreenshot(); local fh = io.open(OUT .. "sh-" .. tag .. ".png", "wb"); fh:write(png); fh:close() end
local function reg(n) local ok, st = pcall(emu.getState); if not ok then return -1 end; return tonumber(st["cpu.r" .. n]) or -1 end
local function urow(slot)
  local a = ua(slot); local v4 = r16(a + 4)
  return string.format("u%d type=%d flags=%02X x=%d y=%d hp=%d fuel=%d", slot, r8(a), r8(a + 1), r8(a + 2), r8(a + 3), v4 % 128, r8(a + 6) % 128)
end
-- the game's own check, logged: entry args and exit value, aggregated per
-- (slot, x, y, ret) between checkpoints -- the renderer asks every frame
local calls, pend = {}, nil
local hooking = false
emu.addMemoryCallback(function()
  if not hooking then return end
  local p, x, y = reg(0), reg(1), reg(2)
  local base = r32(UNIT_PTR)
  local slot = (p ~= 0) and math.floor((p - base) / 12) or -1
  pend = string.format("u%d@(%d,%d)", slot, x % 65536, y % 65536)
end, emu.callbackType.exec, CHECK_ENTRY, CHECK_ENTRY, emu.cpuType.gba, emu.memType.gbaMemory)
emu.addMemoryCallback(function()
  if not hooking or not pend then return end
  local k = pend .. " -> " .. tostring(reg(0))
  calls[k] = (calls[k] or 0) + 1
  pend = nil
end, emu.callbackType.exec, CHECK_EXIT, CHECK_EXIT, emu.cpuType.gba, emu.memType.gbaMemory)
local function checkpoint(tag, frames)
  calls = {}; hooking = true; wait(frames or 90); hooking = false
  local keys = {}
  for k in pairs(calls) do keys[#keys + 1] = k end
  table.sort(keys)
  L("  check@" .. tag .. ": " .. #keys .. " distinct")
  for _, k in ipairs(keys) do L(string.format("    %s  x%d", k, calls[k])) end
end
local function overlay_row(y)
  local rowp = r32(0x03003600 + y * 4)
  local t = {}
  for x = 0, 14 do local v = r8(rowp + x); if v > 127 then v = v - 256 end; t[#t + 1] = string.format("%3d", v) end
  return table.concat(t, " ")
end
local function armies()
  local ab = r32(ARMY_PTR)
  return string.format("armies=%08X map=%08X (+0x1432=%08X) active=%d  a1+1C=%02X a1+26=%02X a2+1C=%02X a2+26=%02X",
    ab, r32(MAP_PTR), r32(MAP_PTR) + 0x1432, r16(0x030036AC),
    r8(ab + 0x68 + 0x1C), r8(ab + 0x68 + 0x26), r8(ab + 0xD0 + 0x1C), r8(ab + 0xD0 + 0x26))
end
local state
local function reload() emu.loadSavestate(state); wait(30) end
local function end_turn()
  for attempt = 1, 2 do
    local pre = r16(0x03004424)
    goto_tile(2, 6); tap("a", 8, 50); tap("up", 8, 26); tap("a", 8, 70)
    for i = 1, 4 do tap("a", 8, 60) end
    wait(300)
    if r16(0x03004424) ~= pre then
      for _ = 1, 3 do tap("b", 8, 40) end
      return true
    end
    for _ = 1, 4 do tap("b", 8, 30) end
  end
  return false
end
local function setup(dive)
  reload()
  w8(FOG, 0)
  for x = 6, 10 do w8(MAP + 7 * W + x, SEA) end
  w8(MAP + 6 * W + 7, SEA)
  w8(ua(71), 22)                            -- P2 Tank 71 -> Cruiser
  L("  " .. armies())
  L("  end P2 ok=" .. tostring(end_turn()))
  L("  P1 turn: " .. armies())
  w8(ua(3), 24)                             -- P1 Mech 3 -> Sub
  -- (7,6) -> (7,7), then Wait (first item) or Dive (second)
  for attempt = 1, 3 do
    goto_tile(7, 6); tap("a", 8, 50); tap("down", 8, 26); tap("a", 8, 60)
    if dive then tap("down", 8, 20) end
    tap("a", 8, 70); wait(90)
    if r8(ua(3) + 2) == 7 and r8(ua(3) + 3) == 7 then break end
    L("  P1 sub move attempt " .. attempt .. " failed: " .. urow(3)); for _ = 1, 4 do tap("b", 8, 30) end
  end
  L("  P1 sub after menu: " .. urow(3))
  L("  end P1 ok=" .. tostring(end_turn()))
  wait(200)
  L("  P2 turn: " .. armies())
  L("  " .. urow(3) .. " | " .. urow(71))
end
local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()

  L("== H1/H2 dived, P2 view")
  setup(true)
  goto_tile(8, 7)
  checkpoint("H1-map-cursor-adjacent", 90)
  shot("H1-map")
  goto_tile(9, 7); tap("a", 8, 50); wait(30)
  L("  H1 grid y=6: " .. overlay_row(6))
  L("  H1 grid y=7: " .. overlay_row(7))
  L("  H1 grid y=8: " .. overlay_row(8))
  shot("H1-select")
  checkpoint("H1-in-move-select", 60)
  -- H2: move one west, adjacent to the sub, open the menu
  tap("left", 8, 26); tap("a", 8, 70); wait(30)
  checkpoint("H2-menu-open", 60)
  shot("H2-menu")
  L("  H2 before fire: " .. urow(3) .. " | " .. urow(71))
  local hp0 = r16(ua(3) + 4) % 128
  tap("a", 8, 50); shot("H2-after-first-A"); tap("a", 8, 50); wait(500)
  local hp1 = r16(ua(3) + 4) % 128
  L(string.format("  H2 after A,A: sub hp %d -> %d (dmg %d); %s", hp0, hp1, hp0 - hp1, urow(71)))
  shot("H2-after")
  checkpoint("H2-settled", 60)

  L("== H3 dived, confirm onto the sub's tile")
  setup(true)
  goto_tile(9, 7); tap("a", 8, 50); wait(20)
  tap("left", 8, 26); tap("left", 8, 26); tap("a", 8, 70); wait(120)
  shot("H3-after-confirm")
  L("  H3: " .. urow(71) .. " | " .. urow(3))
  checkpoint("H3-settled", 60)
  for _ = 1, 3 do tap("b", 8, 40) end

  L("== H0 control, surfaced")
  setup(false)
  goto_tile(8, 7)
  checkpoint("H0-map", 90)
  shot("H0-map")
  goto_tile(9, 7); tap("a", 8, 50); wait(30)
  L("  H0 grid y=7: " .. overlay_row(7))
  shot("H0-select")
  for _ = 1, 3 do tap("b", 8, 40) end

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
    if not ok then local ef = io.open(OUT .. "subhide_err.log", "w"); ef:write(tostring(err)); ef:close(); emu.stop(1) end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

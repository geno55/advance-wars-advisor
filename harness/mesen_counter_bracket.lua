-- The A16 probe (headless, Mesen2 --testrunner; see DERIVATION 25-26): the counter's bracket at a damaged target.
-- My Infantry (slot 1) is written to hp 100/81/57, its tile written to Wood
-- (2 stars), an enemy teleported adjacent, and Fire driven. Each case's
-- observed strike damage names the survivor exactly, so each (damage,
-- counter) pair scores the display rules with no luck bookkeeping.
local OUT = OUT_DIR or "C:/tmp/cap/"
local MSS = MSS_PATH or "C:/Users/geno5/Documents/Mesen2/SaveStates/Advance Wars (USA) (Rev 1)_1.mss"
local UNIT_PTR, MAP, DIMS = 0x08282CB8, 0x02016C2A, 0x030036E0
local CURX, CURY = 0x030033F0, 0x030033F1
local RNG = 0x03001D30
local ME, TANK, INF = 1, 71, 65      -- slots: my Infantry, enemy Tank, enemy Infantry
local MX, MY, EX, EY = 1, 6, 2, 6    -- my tile (written to Wood), enemy teleport tile

local function r8(a) return emu.read(a, emu.memType.gbaDebug) end
local function r16(a) return emu.read16(a, emu.memType.gbaDebug) end
local function r32(a) return emu.read32(a, emu.memType.gbaDebug) end
local function w8(a, v) emu.write(a, v, emu.memType.gbaMemory) end
local function w16(a, v) emu.write16(a, v, emu.memType.gbaMemory) end
local function w32(a, v) emu.write32(a, v, emu.memType.gbaMemory) end

local held = {}
emu.addEventCallback(function()
  pcall(function() emu.setInput(held, 0) end)
end, emu.eventType.inputPolled)
local function wait(n) for _ = 1, n do coroutine.yield() end end
local function tap(btn, hold, gap)
  held = { [btn] = true }; wait(hold or 6); held = {}; wait(gap or 24)
end
local function shot(tag)
  local png = emu.takeScreenshot()
  local fh = io.open(OUT .. "run9-" .. tag .. ".png", "wb")
  fh:write(png); fh:close()
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
local function ua(slot) return r32(UNIT_PTR) + slot * 12 end
local function hp_of(slot) return r16(ua(slot) + 4) % 128 end
local function set_hp(slot, v)
  local a = ua(slot) + 4
  local cur = r16(a)
  w16(a, cur - cur % 128 + v)
  return r16(a) % 128 == v and math.floor(r16(a) / 128) == math.floor(cur / 128)
end

local state, rows = nil, {}
local function runcase(c, snap)
  emu.loadSavestate(state); wait(30)
  local ok = true
  -- my tile becomes Wood (id 4, neutral): logic array write, screen may lag
  local w = r8(DIMS)
  w8(MAP + MY * w + MX, 4)
  ok = ok and r8(MAP + MY * w + MX) == 4
  -- teleport the chosen enemy next to me
  local es = c.enemy
  w8(ua(es) + 2, EX); w8(ua(es) + 3, EY)
  ok = ok and r8(ua(es) + 2) == EX and r8(ua(es) + 3) == EY
  if c.hp then ok = ok and set_hp(ME, c.hp) end
  w32(RNG, c.seed)
  local my0, en0 = hp_of(ME), hp_of(es)
  goto_tile(MX, MY)
  if snap then shot(c.name .. "-hover") end
  wait(c.jitter or 0)
  tap("a", 6, 30)            -- select
  tap("a", 6, 45)            -- destination: stay put
  if snap then shot(c.name .. "-menu") end
  tap("a", 6, 45)            -- top item: Fire (enemy adjacent)
  if snap then shot(c.name .. "-target") end
  tap("a", 6, 30)            -- confirm target
  -- wait out the battle: poll for the defender losing hp, then settle
  local moved = false
  for _ = 1, 40 do
    if hp_of(es) ~= en0 then moved = true; break end
    wait(15)
  end
  wait(280)
  local my1, en1 = hp_of(ME), hp_of(es)
  local a = ua(ME)
  rows[#rows + 1] = string.format(
    '{"name":"%s","enemy_slot":%d,"my_hp0":%d,"my_hp1":%d,"en_hp0":%d,'
    .. '"en_hp1":%d,"damage":%d,"counter":%d,"acted":%d,"wrote_ok":%s,'
    .. '"battle_seen":%s}',
    c.name, es, my0, my1, en0, en1, en0 - en1, my0 - my1,
    r8(a + 1) % 2, tostring(ok), tostring(moved))
  if snap then shot(c.name .. "-after") end
end

local function main()
  wait(5)
  local fh = io.open(MSS, "rb"); state = fh:read("*a"); fh:close()
  local cases = {}
  -- feasibility controls at full hp: every display rule agrees there
  for i = 1, 3 do
    cases[#cases + 1] = { name = "ctrl" .. i, enemy = TANK,
                          seed = (i * 2654435761) % 4294967296, jitter = i }
  end
  for i = 1, 12 do
    cases[#cases + 1] = { name = "h81_" .. i, enemy = TANK, hp = 81,
                          seed = (1000 + i * 2654435761) % 4294967296,
                          jitter = i % 7 }
  end
  for i = 1, 12 do
    cases[#cases + 1] = { name = "h57_" .. i, enemy = INF, hp = 57,
                          seed = (77777 + i * 2654435761) % 4294967296,
                          jitter = i % 7 }
  end
  local log = io.open(OUT .. "run9.json", "w")
  log:write("[\n")
  for i, c in ipairs(cases) do
    runcase(c, i <= 3)
    log:write((i > 1 and ",\n" or "") .. rows[#rows]); log:flush()
  end
  log:write("\n]\n"); log:close()
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
      local ef = io.open(OUT .. "run9-err.log", "w"); ef:write(tostring(err)); ef:close()
      emu.stop(1)
    end
  end
end, emu.callbackType.exec, 0x18, 0x18, emu.cpuType.gba, emu.memType.gbaMemory)

-- The single-action driver for headless Mesen2 (ROADMAP step 2). Executes
-- ONE engine Action on the running game -- select, move by counted taps,
-- confirm, pick the menu item by its position in the offered list, the
-- Fire target cursor, the drop selector, a purchase, the CO power, End
-- Turn -- and verifies every step by reading the records back. Nothing here
-- chains actions on its own: tools/sim_diff.py hands it one parked state,
-- one action, and the checks the action must satisfy; a failed check
-- reloads the state and tries again, up to three times, and says why.
--
-- A LIBRARY on the global table `AW`, concatenated after mesen_state.lua by
-- tools/sim_diff.py; runs nothing by itself.
--
-- The rig facts it is built on (docs/DERIVATION.md, HANDOFF-advisor.md):
--   * the map cursor is the byte pair 0x030033F0/F1 and tracks in map mode,
--     so goto_tile() navigates closed-loop (25); it FREEZES in move-select,
--     so a move is counted direction taps along the path the engine drew,
--     verified off the unit record afterwards (29). The driver taps exactly
--     pathing.path(), so the arrow the game walks IS the engine's route --
--     the one place the drawn route could matter is thereby not a
--     divergence (HANDOFF, "Whether the drawn route matters").
--   * unit action menu, table order (40): Fire, Capt, Load, Drop, Drop(2nd
--     slot), Join, Supply, Wait, Dive, Rise -- only offered items show, so
--     the item is picked by its index in the PREDICTED list, and the
--     read-back is what catches a wrong prediction.
--   * map menu: Unit / Intel / [Power] / Save / Options / End; End is last
--     and up from the top wraps to it (25, 38).
--   * under fog the turn-start card waits for a press and the cursor then
--     rests ON a unit: B x3 after every turn change (38).
--   * position writes are NOT transparent (the tile->unit index); the write
--     helper refuses them. Type/hp/ammo/fuel/capture/state, terrain, army
--     and settings writes are (15, 29, 33-37).
--   * the RNG at 0x03001D30 read right before a Fire confirm predicts the
--     strike's roll (32); read before the Power confirm, Sturm's strategy
--     (30). Both are recorded on the result as rng_at_confirm.
--
-- Recorded here, not a rule: the Fire target cursor. The bytes are read
-- after Fire and steered toward the target if they track; if they do not,
-- a candidate tap list is cycled across attempts and the battle read-back
-- (target HP / RNG moved) is the verdict. The drop selector is driven the
-- same way: a candidate list per landing direction, north first.

AW = AW or {}
local M = AW

M.OUT = M.OUT or "./"
M.log = M.log or nil
function M.L(s) if M.log then M.log:write(s .. "\n"); M.log:flush() end end

M.held = {}
emu.addEventCallback(function()
  pcall(function() emu.setInput(M.held, 0) end)
end, emu.eventType.inputPolled)

function M.wait(n) for _ = 1, n do coroutine.yield() end end
function M.tap(btn, hold, gap)
  M.held = { [btn] = true }; M.wait(hold or 6); M.held = {}; M.wait(gap or 24)
end
function M.cancel(n) for _ = 1, (n or 3) do M.tap("b", 8, 40) end end
function M.cursor() return M.r8(M.CURX), M.r8(M.CURY) end

function M.goto_tile(x, y)
  -- A cursor that stops answering mid-turn is a scripted lesson that
  -- opened over the map (mission two, day 2: Nell on Grit, after the
  -- turn's first step had already been planned; 2026-09-05): after six
  -- taps that moved nothing, an A turns its page, and the walk goes on.
  local stuck = 0
  for _ = 1, 150 do
    local cx, cy = M.cursor()
    if cx == x and cy == y then return true end
    if cx < x then M.tap("right") elseif cx > x then M.tap("left")
    elseif cy < y then M.tap("down") else M.tap("up") end
    local nx, ny = M.cursor()
    if nx == cx and ny == cy then
      stuck = stuck + 1
      if stuck >= 6 then M.tap("a", 6, 30); stuck = 0 end
    else
      stuck = 0
    end
  end
  return false
end

function M.shot(tag)
  local ok, png = pcall(emu.takeScreenshot)
  if not ok or not png then return end
  local fh = io.open(M.OUT .. tag .. ".png", "wb")
  if fh then fh:write(png); fh:close() end
end

function M.active_player() return math.floor(M.r32(M.TURN + 4) / 32) end

function M.urow(slot)
  local u = M.unit(slot)
  if not u then return string.format("u%d <absent>", slot) end
  return string.format(
    "u%d %s P%d (%d,%d) hp=%d ammo=%d fuel=%d cap=%d flags=%02X cargo=%d/%d",
    slot, u.name, u.player, u.x, u.y, u.hp, u.ammo, u.fuel, u.capture, u.state,
    u.cargo, u.cargo2)
end

-- ---------------------------------------------------------------------------
-- writes, each read back
-- ---------------------------------------------------------------------------

function M.apply_writes(writes)
  for i, wr in ipairs(writes or {}) do
    local k = wr.kind
    if k == "unit" then
      if wr.x or wr.y then
        return false, "write " .. i .. ": position writes are refused (the tile->unit index does not follow them)"
      end
      local a = M.unit_addr(wr.slot)
      if wr.type == 0 then                    -- remove the record outright
        M.w8(a, 0)
        if M.unit(wr.slot) then return false, "write " .. i .. ": unit " .. wr.slot .. " still typed" end
      else
        if wr.type then M.w8(a, wr.type) end
        if wr.hp or wr.ammo or wr.capture then
          local v = M.r16(a + 4)
          local hp = wr.hp or (v % 128)
          local ammo = wr.ammo or (math.floor(v / 128) % 16)
          local cap = wr.capture or (math.floor(v / 2048) % 32)
          M.w16(a + 4, hp + ammo * 128 + cap * 2048)
        end
        if wr.fuel then
          local hi = M.r8(a + 6) - (M.r8(a + 6) % 128)      -- bit 7 is not fuel
          M.w8(a + 6, hi + wr.fuel)
        end
        if wr.state then M.w8(a + 1, wr.state) end
        local u = M.unit(wr.slot)
        if not u then return false, "write " .. i .. ": unit " .. wr.slot .. " unreadable after the write" end
        if (wr.type and u.type ~= wr.type) or (wr.hp and u.hp ~= wr.hp)
            or (wr.ammo and u.ammo ~= wr.ammo) or (wr.capture and u.capture ~= wr.capture)
            or (wr.fuel and u.fuel ~= wr.fuel) or (wr.state and u.state ~= wr.state) then
          return false, "write " .. i .. ": read-back mismatch on unit " .. wr.slot
        end
      end
    elseif k == "terrain" then
      local a = M.map_addr(wr.x, wr.y)
      local v = wr.id + 32 * (wr.owner or 0)
      M.w8(a, v)
      if M.r8(a) ~= v then return false, "write " .. i .. ": terrain read-back mismatch" end
    elseif k == "army" then
      local a = M.army_addr(wr.player)
      if wr.funds then M.w32(a, wr.funds) end
      if wr.co_id then M.w8(a + 0x1D, wr.co_id) end
      if wr.meter then M.w32(a + 0x20, wr.meter) end
      if wr.uses then M.w8(a + 0x25, wr.uses) end
      if wr.ready then M.w8(a + 0x24, wr.ready) end
      if wr.active then M.w8(a + 0x1E, wr.active) end
      -- +0x1B is the side's controller: 1 human, 2 COM (the match phase
      -- switch at 0x08035034 sends 2 to the AI phase, DERIVATION 44)
      if wr.control then M.w8(a + 0x1B, wr.control) end
      local ar = M.army(wr.player)
      if (wr.funds and ar.funds ~= wr.funds) or (wr.co_id and ar.co_id ~= wr.co_id)
          or (wr.meter and ar.power ~= wr.meter) or (wr.uses and ar.power_uses ~= wr.uses) then
        return false, "write " .. i .. ": army read-back mismatch"
      end
    elseif k == "proplist" then
      -- Insert {id, x, y, 0,0,0,0,0} into the game's property list at
      -- [0x08282CC4] (8-byte records sorted by y then x, 0xFF-terminated,
      -- DERIVATION 14). The AI's build phase walks THIS list (0x08067684),
      -- not the terrain grid, so a written factory has to join it.
      local base = M.r32(0x08282CC4)
      local recs = {}
      local n = 0
      while n < 512 and M.r8(base + n * 8) ~= 0xFF do
        local r = {}
        for j = 0, 7 do r[j + 1] = M.r8(base + n * 8 + j) end
        recs[#recs + 1] = r
        n = n + 1
      end
      local at = #recs + 1
      for j, r in ipairs(recs) do
        if r[3] > wr.y or (r[3] == wr.y and r[2] > wr.x) then at = j; break end
      end
      table.insert(recs, at, { wr.id, wr.x, wr.y, 0, 0, 0, 0, 0 })
      for j, r in ipairs(recs) do
        for b = 1, 8 do M.w8(base + (j - 1) * 8 + b - 1, r[b]) end
      end
      M.w8(base + #recs * 8, 0xFF)
      local chk = M.r8(base + (at - 1) * 8)
      if chk ~= wr.id then return false, "write " .. i .. ": property list read-back mismatch" end
      -- the tile -> record index table: every index at or past the
      -- insertion point moves up one, and the new tile gets the slot
      local map = M.r32(0x08282CB4)
      local w, h = M.dims()
      local idx = at - 1
      local shifted = 0
      for y = 0, h - 1 do
        local rowoff = M.r16(map + 0x4682 + y * 2)
        for x = 0, w - 1 do
          local a = map + 0x193A + rowoff + x
          local v = M.r8(a)
          if v >= idx and v < 0x80 and not (x == wr.x and y == wr.y) then M.w8(a, v + 1); shifted = shifted + 1 end
        end
      end
      M.w8(map + 0x193A + M.r16(map + 0x4682 + wr.y * 2) + wr.x, idx)
      -- The income is CACHED: the walker at 0x08025208 fills army +0x08
      -- and the per-type counters +0xC..+0xF (Base, City, Airport, Port)
      -- at map load and on capture, and the turn-start payer pays +0x08
      -- as it stands (DERIVATION 47). A written property joins that cache
      -- here, the way a capture would, or the game pays for one fewer
      -- property than the board shows.
      local owner = math.floor(M.r8(M.map_addr(wr.x, wr.y)) / 32)
      if owner > 0 then
        local a = M.army_addr(owner)
        M.w32(a + 8, M.r32(a + 8) + M.r32(M.RATE))
        local ctr = ({ [14] = 0xC, [6] = 0xD, [10] = 0xE, [11] = 0xF })[wr.id]
        if ctr then M.w8(a + ctr, M.r8(a + ctr) + 1) end
        M.L(string.format("  proplist: P%d cached income now %d", owner, M.r32(a + 8)))
      end
      M.L(string.format("  proplist: inserted id %d at (%d,%d) as record %d, now %d records; %d tile indices shifted; sample index bytes (4,1)=%d (0,8)=%d",
        wr.id, wr.x, wr.y, idx, #recs, shifted,
        M.r8(map + 0x193A + M.r16(map + 0x4682 + 1 * 2) + 4), M.r8(map + 0x193A + M.r16(map + 0x4682 + 8 * 2) + 0)))
    elseif k == "raw" then
      if wr.size == 4 then M.w32(wr.addr, wr.value)
      elseif wr.size == 2 then M.w16(wr.addr, wr.value)
      else M.w8(wr.addr, wr.value) end
    elseif k == "fog" then M.w8(M.FOG, wr.value)
    elseif k == "weather" then M.w8(M.WEATHER, wr.value)
    elseif k == "rng" then M.w32(M.RNG, wr.value)
    elseif k == "repair_free" then M.w8(M.REPAIR_FREE, wr.value)
    elseif k == "rate" then M.w32(M.RATE, wr.value)
    else
      return false, "write " .. i .. ": unknown kind " .. tostring(k)
    end
  end
  return true
end

-- ---------------------------------------------------------------------------
-- read-back checks
-- ---------------------------------------------------------------------------

function M.snapshot()
  local s = { units = {}, funds = {}, rng = M.r32(M.RNG), active = M.active_player() }
  for i = 0, 255 do
    local u = M.unit(i)
    if u then s.units[i] = u end
  end
  for p = 1, 4 do s.funds[p] = M.r32(M.army_addr(p)) end
  return s
end

-- checks: a list of
--   {what="unit", slot=, x=, y=, acted=, loaded=, dived=, gone=}
--   {what="changed", slot=, fields={...}}   any field differs, or the unit is gone
--   {what="hit", slot=}                     target hp moved, or gone, or the RNG drew
--   {what="captured", slot=, x=, y=, player=}  capture field moved or the tile is theirs
--   {what="active", player=}
--   {what="army", player=, active=}
function M.check(checks, snap)
  for _, c in ipairs(checks or {}) do
    if c.what == "unit" then
      local u = M.unit(c.slot)
      if c.gone then
        if u then return false, string.format("unit %d still present at (%d,%d)", c.slot, u.x, u.y) end
      else
        if not u then return false, string.format("unit %d is missing", c.slot) end
        if c.x and (u.x ~= c.x or u.y ~= c.y) then
          return false, string.format("unit %d at (%d,%d), expected (%d,%d)", c.slot, u.x, u.y, c.x, c.y)
        end
        if c.acted ~= nil and u.acted ~= c.acted then
          return false, string.format("unit %d acted=%s, expected %s", c.slot, tostring(u.acted), tostring(c.acted))
        end
        if c.loaded ~= nil and u.loaded ~= c.loaded then
          return false, string.format("unit %d loaded=%s, expected %s", c.slot, tostring(u.loaded), tostring(c.loaded))
        end
        if c.dived ~= nil and u.dived ~= c.dived then
          return false, string.format("unit %d dived=%s, expected %s", c.slot, tostring(u.dived), tostring(c.dived))
        end
      end
    elseif c.what == "changed" then
      local before, now = snap.units[c.slot], M.unit(c.slot)
      if now and before then
        local moved = false
        for _, f in ipairs(c.fields) do if before[f] ~= now[f] then moved = true end end
        if not moved then return false, string.format("unit %d: none of %s changed", c.slot, table.concat(c.fields, "/")) end
      end
    elseif c.what == "hit" then
      local before, now = snap.units[c.slot], M.unit(c.slot)
      if now and before and now.hp == before.hp and M.r32(M.RNG) == snap.rng then
        -- a strike that rounds to nothing is still a battle: the counter
        -- moved the attacker's HP or ammo, or the target spent a shell
        -- (a Recon on a MdTank, m01-s day 9 and m01-a day 11: the step
        -- was called failed, the game had played it, 2026-09-05)
        local fought = now.ammo ~= before.ammo
        if c.attacker then
          local a0, a1 = snap.units[c.attacker], M.unit(c.attacker)
          if a0 and a1 and a1.acted and (a1.hp ~= a0.hp or a1.ammo ~= a0.ammo) then fought = true end
        end
        if not fought then
          return false, string.format("unit %d untouched and the RNG never drew: no battle", c.slot)
        end
      end
    elseif c.what == "captured" then
      local before, now = snap.units[c.slot], M.unit(c.slot)
      local owner = math.floor(M.r8(M.map_addr(c.x, c.y)) / 32)
      if owner ~= c.player and (not now or not before or now.capture == before.capture) then
        return false, string.format("unit %d: capture field unchanged and (%d,%d) owned by %d", c.slot, c.x, c.y, owner)
      end
    elseif c.what == "active" then
      if M.active_player() ~= c.player then
        return false, string.format("active player %d, expected %d", M.active_player(), c.player)
      end
    elseif c.what == "army" then
      local a = M.army(c.player)
      if c.active ~= nil and a.power_active ~= c.active then
        return false, string.format("P%d power_active=%s, expected %s", c.player, tostring(a.power_active), tostring(c.active))
      end
    else
      return false, "unknown check " .. tostring(c.what)
    end
  end
  return true
end

-- ---------------------------------------------------------------------------
-- the CPU's turn, traced
-- ---------------------------------------------------------------------------

-- The AI issues its decisions as 20-byte command records at 0x030050F0 and
-- dispatches each through the switch at 0x080669A0 (+0 id 1..17, +1 slot,
-- +2/+3 the move's tile, +4/+5 the target tile, +8 the RNG state the
-- record carries, +0x12 the fuel to restore). An exec hook on that
-- function's entry copies the record as it is dispatched, so a CPU turn
-- comes back as the ordered list of what it decided (DERIVATION 44).
M.CMD = 0x030050F0
M.CMD_DISPATCH = 0x080669A0
M.MATCH_PHASE = 0x0300357C
M.trace = nil
M.draws = nil
-- Every draw from the RNG (0x08010A84, the step the record's +8 state is
-- restored by 0x08010A78) while the CPU plays: the state before the draw,
-- the caller, the unit being decided (0x030041F8). The AI's forecast rolls
-- luck through the same RNG, so a predictor has to replay every draw.
M.RNG_DRAW = 0x08010A84
emu.addMemoryCallback(function()
  if not M.draws then return end
  local ok, st = pcall(emu.getState)
  local lr = -1
  if ok then lr = tonumber(st["cpu.r14"]) or -1 end
  M.draws[#M.draws + 1] = { rng = M.r32(0x03001D30), lr = lr, unit = M.r8(0x030041F8) }
end, emu.callbackType.exec, M.RNG_DRAW, M.RNG_DRAW, emu.cpuType.gba, emu.memType.gbaMemory)
emu.addMemoryCallback(function()
  if not M.trace then return end
  local c = M.CMD
  local rec = {
    id = M.r8(c), slot = M.r8(c + 1), x = M.r8(c + 2), y = M.r8(c + 3),
    tx = M.r8(c + 4), ty = M.r8(c + 5), b6 = M.r8(c + 6), b7 = M.r8(c + 7),
    rng = M.r32(c + 8), b12 = M.r8(c + 12), b13 = M.r8(c + 13),
    b14 = M.r8(c + 14), b15 = M.r8(c + 15), b16 = M.r8(c + 16),
    b17 = M.r8(c + 17), fuel = M.r8(c + 0x12), b19 = M.r8(c + 19),
    dest_x = M.r16(0x030033AC), dest_y = M.r16(0x030033AE),
    sel_x = M.r16(0x030041DC), sel_y = M.r16(0x030041DE),
  }
  local u = M.unit(rec.slot)
  if u then
    rec.unit = u.name; rec.ux, rec.uy = u.x, u.y; rec.uhp = u.hp
    rec.ai = { u.ai9, u.ai10, u.ai11 }
  end
  rec.draws = M.draws and #M.draws or 0
  M.trace[#M.trace + 1] = rec
  if M.log then                       -- the sub-phase's order list as the game sorted it (DERIVATION 54)
    local slots, keys = {}, {}
    for i = 0, 11 do slots[#slots + 1] = M.r8(0x03005020 + i); keys[#keys + 1] = M.r8(0x03005110 + i) end
    M.L(string.format("  order: flag %d slots [%s] keys [%s]", M.r32(0x030051AC), table.concat(slots, ","), table.concat(keys, ",")))
  end
  -- the CPU is playing: every other side goes back to human NOW, so the
  -- next-player search at the CPU's End Turn hands to a human (a CPU turn
  -- can finish inside the End Turn tap gaps, so no poll is early enough)
  if M.cpu_side then
    for p = 1, 4 do
      M.w8(M.army_addr(p) + 0x1B, (p == M.cpu_side) and 2 or M.control_orig[p])
    end
  end
end, emu.callbackType.exec, M.CMD_DISPATCH, M.CMD_DISPATCH, emu.cpuType.gba, emu.memType.gbaMemory)

-- The AI BUILDS inside driver state 4 (0x08066EC8): its writer 0x08067A48
-- calls the purchase routine 0x080243DC(x, y, type) directly, so a build
-- never passes the command dispatcher and the record trace cannot show
-- it. An exec hook on the purchase's entry logs the arguments (r0 x, r1
-- y, r2 RAM type), the funds and the RNG state as it is called.
M.PURCHASE = 0x080243DC
M.builds = nil
emu.addMemoryCallback(function()
  if not M.builds then return end
  local ok, st = pcall(emu.getState)
  if not ok then return end
  local side = M.r16(0x030036AC)
  M.builds[#M.builds + 1] = {
    x = tonumber(st["cpu.r0"]) or -1, y = tonumber(st["cpu.r1"]) or -1,
    type = tonumber(st["cpu.r2"]) or -1, lr = tonumber(st["cpu.r14"]) or -1,
    funds = M.r32(M.army_addr(side)), rng = M.r32(0x03001D30),
    draws = M.draws and #M.draws or 0, cmds = M.trace and #M.trace or 0,
    state = M.r16(0x030051B0), mode = M.r8(0x030050F0 + 7),
    -- what the foot-cap check 0x08068824(0) sums: army record 0's bytes
    -- +0xC..+0xF; and the side flags byte the transport choosers test
    a0 = { M.r8(M.army_addr(0) + 0xC), M.r8(M.army_addr(0) + 0xD),
           M.r8(M.army_addr(0) + 0xE), M.r8(M.army_addr(0) + 0xF) },
    flags_e4 = M.r8(0x030050E4), day = M.r32(M.TURN),
  }
end, emu.callbackType.exec, M.PURCHASE, M.PURCHASE, emu.cpuType.gba, emu.memType.gbaMemory)
-- the AI driver's state (0x030051B0): every write, with the writer's PC
M.state_log = nil
emu.addMemoryCallback(function(addr, value)
  if not M.state_log then return end
  local ok, st = pcall(emu.getState)
  local pc = ok and (tonumber(st["cpu.r15"]) or -1) or -1
  local last = M.state_log[#M.state_log]
  if last and last.v == value and last.pc == pc then last.n = last.n + 1; return end
  M.state_log[#M.state_log + 1] = { v = value, pc = pc, n = 1, cmds = M.trace and #M.trace or 0,
                                    builds = M.builds and #M.builds or 0 }
end, emu.callbackType.write, 0x030051B0, 0x030051B0, emu.cpuType.gba, emu.memType.gbaMemory)

-- End the human's turn and let the game play the next side (whose control
-- byte the case wrote to 2), polling until the turn comes back; the trace
-- is returned on the step result.
M.watch_writes = false
local function pc_of()
  local ok, st = pcall(emu.getState)
  if not ok then return -1 end
  return tonumber(st["cpu.r15"]) or -1
end
emu.addMemoryCallback(function(addr, value)
  -- The turn has just passed to the CPU side (cpu_turn wrote both sides'
  -- controller bytes to 2 so the End Turn's next-player search would land
  -- on it): give every other side its byte back HERE, in the write, so
  -- the CPU's turn can be as short as it likes. The poll loop's version
  -- missed whole turns once the battle animation was off -- the game's AI
  -- then played the human's next turn as well (m01-tuned-noanim, days 8,
  -- 14 and 16; 2026-09-05).
  if M.cpu_side and addr == 0x030036AC and value == M.cpu_side then
    for p = 1, 4 do M.w8(M.army_addr(p) + 0x1B, (p == M.cpu_side) and 2 or M.control_orig[p]) end
    M.handed = true
  end
  if not M.watch_writes then return end
  M.L(string.format("  W idx36AC addr %08X val %d pc %08X phase %d", addr, value, pc_of(), M.r16(M.MATCH_PHASE)))
end, emu.callbackType.write, 0x030036AC, 0x030036AD, emu.cpuType.gba, emu.memType.gbaMemory)
emu.addMemoryCallback(function(addr, value)
  if not M.watch_writes then return end
  local base = M.r32(M.ARMY_PTR)
  local off = addr - base
  local f = off % M.ARMY_STRIDE
  if f == 0x1B or f == 0x14 or f == 0x15 or f == 0x1C then
    M.L(string.format("  W army P%d +0x%02X val %d pc %08X phase %d day %d", math.floor(off / M.ARMY_STRIDE), f, value, pc_of(), M.r16(M.MATCH_PHASE), M.r32(M.TURN)))
  end
end, emu.callbackType.write, 0x0201AB34, 0x0201AB34 + 5 * 0x68, emu.cpuType.gba, emu.memType.gbaMemory)

function M.cpu_turn(s)
  local r = { kind = "cpu_turn", ok = false }
  M.watch_writes = true
  if not M.goto_tile(s.empty.x, s.empty.y) then r.why = "cursor never reached the empty tile"; return r end
  local before = M.active_player()
  M.trace = {}
  M.draws = {}
  M.builds = {}
  M.state_log = {}
  M.cpu_side = s.cpu
  M.handed = false
  -- an exec watch: each address in s.watch_pcs logs who called it (r14)
  -- and when, so a routine's invoker can be read off the run log
  local watchers = {}
  for _, pc in ipairs(s.watch_pcs or {}) do
    local cb = emu.addMemoryCallback(function()
      local ok, st = pcall(emu.getState)
      local lr = -1
      if ok then lr = tonumber(st["cpu.r14"]) or -1 end
      M.L(string.format("  watch %08X lr %08X phase %d cmds %d draws %d", pc, lr,
        M.r16(M.MATCH_PHASE), #(M.trace or {}), #(M.draws or {})))
    end, emu.callbackType.exec, pc, pc, emu.cpuType.gba, emu.memType.gbaMemory)
    watchers[#watchers + 1] = { cb = cb, pc = pc }
  end
  -- each side's byte AS RELOADED is what comes back (M.control_orig, read
  -- in run_case before any write): a 0 (no controller, the empty P3/P4
  -- records) written to 1 made the AI's end-of-turn elimination check
  -- treat them as live sides -- +0x14 := 4 and tile (0,0) used as scratch
  -- and left as a City (0x080284C0/0x080285C2)
  M.L(string.format("  cpu_turn: active %d idx36AC %d control P1=%d P2=%d",
    before, M.r16(0x030036AC), M.army(1).control, M.army(2).control))
  M.tap("a", 8, 50); M.tap("up", 8, 26); M.shot(s.tag .. "-end-menu"); M.tap("a", 8, 70)
  M.shot(s.tag .. "-after-end")
  for _ = 1, 4 do M.tap("a", 8, 60) end
  M.shot(s.tag .. "-after-taps")
  -- The next-player search at End Turn (0x08024D58) accepts the first
  -- side whose +0x1B is nonzero and whose +0x14 is zero, and the phase-10
  -- switch then reads that side's +0x1B: 2 is the CPU phase. Both bytes
  -- were written to 2 before End Turn so the choice holds whichever way
  -- the search runs; the command hook above puts every other side back
  -- to 1 on the CPU's first command. The turn is back when the human
  -- phase (5) is running for `before` again and the CPU issued at least
  -- one command; the CPU's whole turn can pass before the first poll.
  local cpu = s.cpu
  r.cpu_player = cpu
  local back, last = false, ""
  local day0 = M.r32(M.TURN)
  local handed = false
  for _ = 1, (s.limit or 3000) do
    local ph = M.r16(M.MATCH_PHASE)
    local idx = M.r16(0x030036AC)
    local sig = string.format("phase %d idx36AC %d control P1=%d P2=%d day %d cmds %d",
      ph, idx, M.army(1).control, M.army(2).control, M.r32(M.TURN), #M.trace)
    if sig ~= last then M.L("  " .. sig); last = sig end
    -- The CPU side is up: give every other side its controller byte back
    -- NOW, not on the CPU's first command. A CPU turn with no command at
    -- all (every unit settled where it stood -- Olaf on day 20 of the
    -- m01-2 acceptance run, 2026-09-05) never fires the command hook, so
    -- the human side's 2 stood and the game's AI played the human's next
    -- turn as well; the turn is also back when the day has moved on with
    -- nothing dispatched.
    if (idx ~= before or M.handed) and not handed then
      handed = true
      for p = 1, 4 do M.w8(M.army_addr(p) + 0x1B, (p == cpu) and 2 or M.control_orig[p]) end
      M.L("  cpu side up: the other sides' control bytes restored")
    end
    if ph == 5 and idx == before and (#M.trace > 0 or handed or M.r32(M.TURN) ~= day0) then back = true; break end
    if s.watch then                       -- mesen_play: the match decided mid-turn
      local st, why = s.watch()
      if st then r.result, r.result_why = st, why; M.L("  watch: " .. st .. " -- " .. tostring(why)); break end
    end
    if ph ~= 5 then M.tap("a", 4, 6) else M.wait(10) end
  end
  for _, w in ipairs(watchers) do
    pcall(emu.removeMemoryCallback, w.cb, emu.callbackType.exec, w.pc, w.pc, emu.cpuType.gba, emu.memType.gbaMemory)
  end
  r.commands = M.trace
  r.draws = M.draws
  r.builds = M.builds
  r.state_log = M.state_log
  M.trace = nil
  M.draws = nil
  M.builds = nil
  M.state_log = nil
  M.cpu_side = nil
  for p = 1, 4 do M.w8(M.army_addr(p) + 0x1B, M.control_orig[p]) end
  if r.result then r.why = "the match was decided: " .. r.result; return r end
  if not back then r.why = string.format("P%d never handed the turn back (%s)", cpu, last); return r end
  M.wait(120); M.tap("a", 8, 60); M.tap("a", 8, 60); M.wait(150)
  M.cancel(3)
  M.watch_writes = false
  r.ok = true
  return r
end

-- ---------------------------------------------------------------------------
-- the pieces of one action
-- ---------------------------------------------------------------------------

local function select_and_move(s)
  if not M.goto_tile(s.from.x, s.from.y) then
    return false, string.format("cursor never reached (%d,%d)", s.from.x, s.from.y)
  end
  M.tap("a", 8, 50)
  for _, d in ipairs(s.taps or {}) do M.tap(d, 8, 26) end
  M.tap("a", 8, 60)                      -- confirm the destination
  return true
end

-- With `count` (the predicted menu's length) the item is reached from
-- the END, up from the first item wrapping to the last: the unit menu is
-- a subset of a fixed order, and an item the prediction missed at the
-- front -- the Fire an unmoved indirect unit is offered with no target in
-- range, seen on the Field Training state (campaign_run, 2026-09-04) --
-- then shifts nothing. The shop keeps the down-count.
local function pick_menu(index, count)
  if count then
    -- count - index ups, no modulo: the last predicted item is ONE up
    -- away (the wrap), so an extra item at the front shifts nothing --
    -- a lone predicted "Wait" behind an unpredicted Fire included
    for _ = 1, count - index do M.tap("up", 8, 26) end
  else
    for _ = 1, index do M.tap("down", 8, 26) end
  end
  M.tap("a", 8, 70)
end

-- After Fire: steer the target cursor to `t` if the cursor bytes track in
-- target-select, else fall back to a blind candidate per attempt.
local BLIND = { {}, { "right" }, { "down" }, { "left" }, { "up" } }
function M.select_target(t, attempt)
  M.wait(20)
  local cx, cy = M.cursor()
  if cx == t.x and cy == t.y then return true, "cursor on target" end
  local tracked = false
  for _ = 1, 12 do
    cx, cy = M.cursor()
    if cx == t.x and cy == t.y then return true, "steered" end
    local btn = (cx < t.x) and "right" or (cx > t.x) and "left"
      or (cy < t.y) and "down" or "up"
    M.tap(btn, 6, 20)
    local nx, ny = M.cursor()
    if nx == cx and ny == cy then break end
    tracked = true
  end
  cx, cy = M.cursor()
  if cx == t.x and cy == t.y then return true, "steered" end
  local taps = BLIND[((attempt - 1) % #BLIND) + 1]
  for _, d in ipairs(taps) do M.tap(d, 6, 20) end
  return true, string.format("cursor %s at (%d,%d); blind taps [%s]",
    tracked and "tracked but missed" or "not tracked", cx, cy, table.concat(taps, ","))
end

-- Poll until the attacker has acted and the two records and the RNG have
-- sat still for 90 frames; false if nothing happens in 900.
function M.wait_battle(att, def)
  local function sig()
    local a, d = M.unit(att), M.unit(def)
    return string.format("%s|%s|%d", a and (a.hp .. "," .. a.ammo) or "x",
      d and (d.hp .. "," .. d.ammo) or "x", M.r32(M.RNG))
  end
  local acted, last, still, nudges = false, sig(), 0, 0
  for _ = 1, 90 do
    M.wait(10)
    local a = M.unit(att)
    if a == nil or a.acted then acted = true end
    local now = sig()
    if now == last then still = still + 1 else still = 0 end
    last = now
    if acted and still >= 9 then return true end
    -- the Field Training state (campaign_run, 2026-09-04): the confirm on
    -- the target screen needed a second A, and the battle scene a third,
    -- before anything changed -- a nudge whenever nothing has moved for
    -- 120 frames, at most six times
    if still >= 12 and nudges < 6 then
      nudges = nudges + 1; still = 0
      M.L(string.format("  wait_battle: nothing for 120 frames, nudge %d (acted %s)", nudges, tostring(acted)))
      M.tap("a", 6, 10)
    end
  end
  return false, "the battle never resolved (acted=" .. tostring(acted) .. ")"
end

-- One step of a case. `s` carries kind, tag and the fields its kind needs.
function M.do_step(s, attempt)
  local r = { kind = s.kind, ok = false }
  local snap = M.snapshot()
  local k = s.kind
  if k == "write" then
    local ok, why = M.apply_writes(s.writes)
    r.ok, r.why = ok, why
    return r
  elseif k == "cpu_turn" then
    return M.cpu_turn(s)
  elseif k == "build" then
    if not M.goto_tile(s.factory.x, s.factory.y) then r.why = "cursor never reached the factory"; return r end
    M.tap("a", 8, 60); M.wait(40); M.shot(s.tag .. "-shop")
    pick_menu(s.shop_index)
    M.wait(40); M.tap("a", 8, 90); M.wait(150)
  elseif k == "power" then
    if not M.goto_tile(s.empty.x, s.empty.y) then r.why = "cursor never reached the empty tile"; return r end
    M.tap("a", 6, 40); M.tap("down", 6, 16); M.tap("down", 6, 16)
    M.shot(s.tag .. "-menu")
    r.rng_at_confirm = M.r32(M.RNG)
    M.tap("a", 6, 60)
    for _ = 1, 6 do M.tap("a", 6, 80) end
    M.wait(300); M.cancel(3)
  elseif k == "end_turn" then
    if not M.goto_tile(s.empty.x, s.empty.y) then r.why = "cursor never reached the empty tile"; return r end
    local before = M.active_player()
    M.tap("a", 8, 50); M.tap("up", 8, 26); M.shot(s.tag .. "-menu"); M.tap("a", 8, 70)
    for _ = 1, 4 do M.tap("a", 8, 60) end
    M.wait(300)
    if M.active_player() ~= before then
      M.wait(120); M.tap("a", 8, 60); M.tap("a", 8, 60); M.wait(150)
    end
    M.cancel(3)
  else
    local ok, why = select_and_move(s)
    if not ok then r.why = why; return r end
    if k == "trap" then
      M.wait(120)
    else
      M.wait(30); M.shot(s.tag .. "-menu")
      pick_menu(s.menu_index, s.menu and #s.menu or nil)
      if k == "attack" then
        local _, note = M.select_target(s.target, attempt)
        r.target_note = note
        M.wait(10); M.shot(s.tag .. "-target")
        if M.TARGET_LINGER then M.wait(M.TARGET_LINGER) end   -- a probe's knob: frames spent on the target
        r.rng_at_confirm = M.r32(M.RNG)
        M.tap("a", 6, 30)
        local ok3, why3 = M.wait_battle(s.slot, s.target.slot)
        if not ok3 then r.why = why3; return r end
        M.wait(60)
      elseif k == "drop" then
        M.wait(40); M.shot(s.tag .. "-selector")
        local cands = s.drop_taps or { {} }
        local taps = cands[((attempt - 1) % #cands) + 1]
        r.drop_note = "taps [" .. table.concat(taps, ",") .. "]"
        for _, d in ipairs(taps) do M.tap(d, 8, 26) end
        M.tap("a", 8, 90); M.wait(200)
      else
        M.wait(120)
      end
    end
  end
  local ok, why = M.check(s.checks, snap)
  -- a text box or a confirm the state wants pressed (Field Training):
  -- before calling it a failure, nudge A and look again, three times
  local nudges = 0
  while not ok and nudges < 3 do
    nudges = nudges + 1
    M.L(string.format("  check failed (%s); nudge %d", tostring(why), nudges))
    M.tap("a", 6, 90)
    ok, why = M.check(s.checks, snap)
  end
  r.ok, r.why = ok, why
  return r
end

-- ---------------------------------------------------------------------------
-- one case: reload, write, set up, dump, act, verify, dump
-- ---------------------------------------------------------------------------

function M.run_case(c, states)
  local st = states[c.state]
  local result = { name = c.name, state = c.state, ok = false, attempts = 0, steps = {} }
  M.L("== " .. c.name)
  for attempt = 1, (c.attempts or 3) do
    result.attempts = attempt
    emu.loadSavestate(st.bytes); M.wait(30)
    M.control_orig = {}
    for p = 1, 4 do M.control_orig[p] = M.army(p).control end
    M.watch_writes = (c.action.kind == "cpu_turn")
    if M.watch_writes then
      M.L(string.format("  at reload: P1 ctrl %d f14 %d f1C %d | P2 ctrl %d f14 %d f1C %d",
        M.army(1).control, M.army(1).flag14, M.army(1).flag1C, M.army(2).control, M.army(2).flag14, M.army(2).flag1C))
    end
    M.set_dims(st.w, st.h)
    local ok, why = M.apply_writes(c.writes)
    if not ok then result.why = why; M.L("  " .. why); break end
    local failed = nil
    for i, s in ipairs(c.setup or {}) do
      local r = M.do_step(s, attempt)
      result.steps[i] = r
      if not r.ok then failed = string.format("setup %d (%s): %s", i, s.kind, r.why or "?"); break end
    end
    if not failed then
      M.dump(c.before, { note = c.name .. " before" })
      local r = M.do_step(c.action, attempt)
      result.rng_at_confirm = r.rng_at_confirm
      result.target_note = r.target_note
      result.drop_note = r.drop_note
      result.commands = r.commands
      result.draws = r.draws
      result.builds = r.builds
      result.state_log = r.state_log
      result.cpu_player = r.cpu_player
      if r.ok then
        M.wait(60)
        if c.action.kind == "cpu_turn" then M.wait(300); M.cancel(2) end
        M.dump(c.after, { note = c.name .. " after" })
        result.ok = true
        M.L(string.format("  ok on attempt %d", attempt))
        break
      end
      failed = string.format("action (%s): %s", c.action.kind, r.why or "?")
    end
    result.why = failed
    M.L(string.format("  attempt %d failed: %s", attempt, failed))
    M.shot(c.name .. "-fail" .. attempt)
    M.cancel(4)
  end
  return result
end

-- ---------------------------------------------------------------------------
-- a small JSON writer for the result records
-- ---------------------------------------------------------------------------

function M.json(v)
  local t = type(v)
  if t == "nil" then return "null"
  elseif t == "boolean" then return tostring(v)
  elseif t == "number" then
    if v == math.floor(v) then return string.format("%d", v) end
    return tostring(v)
  elseif t == "string" then
    return '"' .. (v:gsub('[%c"\\]', function(ch)
      return string.format("\\u%04x", ch:byte())
    end)) .. '"'
  elseif t == "table" then
    if #v > 0 or next(v) == nil then
      local parts = {}
      for i = 1, #v do parts[i] = M.json(v[i]) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local parts = {}
    for k, val in pairs(v) do parts[#parts + 1] = M.json(tostring(k)) .. ":" .. M.json(val) end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end

-- ---------------------------------------------------------------------------
-- Field Training lessons (DERIVATION 62)
-- ---------------------------------------------------------------------------
-- A mission's tutorial script (mission record +0x2C, interpreter threads at
-- 0x03001D50) waits for the player by registering EVENT HANDLERS: 8-byte
-- entries at 0x03004280 (kind 2 = a script handler, +1 the event code, +4
-- the handler's script). The game raises an event code through 0x08018BA8
-- for what the player does -- 0x2C a unit selected, 0x19 Fire chosen, 0x21
-- Wait, 0x18 Capt, 0x1F Join, 0x0F an attack resolved, 0x11 the map menu
-- opened, 0x15/0x22/0x25/0x27/0x2A its items, 0x29 End -- and a registered
-- handler runs: a NAG re-registers the same table after its text, the
-- wanted one branches on (op 0x1E/0x0D) and goes on. While a script holds
-- the input (text pages, cursor moves) the halfword at 0x03000DA4 is 1.
M.LESSON_TABLE = 0x03004280
M.LESSON_LOCK = 0x03000DA4
M.LESSON_THREADS = 0x03001D50     -- 24-byte thread entries: +0 active, +4 the script PC, +0xC a delay
M.WAIT_NAMES = { [0x08036D91] = "a unit selected", [0x08036C1D] = "a destination chosen" }   -- THUMB addresses
M.MENU_HOOK = 0x08018EA0          -- the open menu's per-frame handler: r0 = the menu object
M.EVENT_HOOK = 0x08018BA8         -- raise(event): r0 = the event code
M.MAP_ITEMS = { [0x15] = true, [0x22] = true, [0x25] = true, [0x27] = true, [0x29] = true, [0x2A] = true }
M.EVENT_NAMES = { [0x0F] = "attack", [0x11] = "map-menu", [0x15] = "Options", [0x18] = "Capt", [0x19] = "Fire",
                  [0x1C] = "Load", [0x1D] = "Drop", [0x1F] = "Join", [0x20] = "Supply", [0x21] = "Wait",
                  [0x29] = "End", [0x2C] = "select" }
M.menu_struct, M.menu_frame = nil, -100
M.watch_events = false

function M.frame()
  local ok, st = pcall(emu.getState)
  return ok and (st["frameCount"] or 0) or 0
end
emu.addMemoryCallback(function()
  local ok, st = pcall(emu.getState)
  if ok then M.menu_struct = tonumber(st["cpu.r0"]); M.menu_frame = st["frameCount"] or 0 end
end, emu.callbackType.exec, M.MENU_HOOK, M.MENU_HOOK, emu.cpuType.gba, emu.memType.gbaMemory)
emu.addMemoryCallback(function()
  if not M.watch_events then return end
  local ok, st = pcall(emu.getState)
  if ok then M.L(string.format("  event %02X raised (from %08X)", tonumber(st["cpu.r0"]) or -1, tonumber(st["cpu.r14"]) or -1)) end
end, emu.callbackType.exec, M.EVENT_HOOK, M.EVENT_HOOK, emu.cpuType.gba, emu.memType.gbaMemory)

function M.menu_open() return M.menu_struct ~= nil and (M.frame() - M.menu_frame) <= 2 end
-- the open menu's rows as event codes, and the highlighted row
function M.menu_rows()
  local s = M.menu_struct
  local tbl = M.r32(s + 0x20)
  local rows = {}
  for i = 0, 7 do
    local item = M.r8(s + 0x30 + i)
    if item == 255 then break end
    rows[#rows + 1] = M.r8(tbl + item * 32)
  end
  return rows, M.r8(s + 0x40)
end

function M.lesson_handlers()
  local out = {}
  for i = 0, 14 do
    local e = M.LESSON_TABLE + 8 * i
    local kind = M.r8(e)
    if kind == 0xFF then break end
    if kind == 2 then out[#out + 1] = { ev = M.r8(e + 1), handler = M.r32(e + 4) } end
  end
  return out
end
local function event_set(hs)
  local s = {}
  for _, h in ipairs(hs) do s[h.ev] = true end
  return s
end
-- a handler that only re-registers the table it was raised from, with no
-- branch on the way, is a nag
local function handler_is_nag(h, hs)
  local mine = event_set(hs)
  local a, branch, again = h, false, false
  for _ = 1, 96 do
    local op = M.r32(a)
    if op == 5 or op == 4 then break end
    if op == 0x0C or op == 0x0D or op == 0x0E or op == 0x1E then branch = true end
    if op == 0x27 then
      local t = M.r32(a + 4)
      local theirs, n = {}, 0
      for k = 0, 14 do
        local ev = M.r8(t + 8 * k)
        if ev == 0xFF then break end
        theirs[ev] = true; n = n + 1
      end
      local same = (n == #hs)
      if same then for ev in pairs(mine) do if not theirs[ev] then same = false end end end
      if same then again = true end
    end
    a = a + 16
  end
  return again and not branch
end
function M.lesson_wanted()
  local hs = M.lesson_handlers()
  local wanted = {}
  for _, h in ipairs(hs) do
    if not handler_is_nag(h.handler, hs) then wanted[#wanted + 1] = h.ev end
  end
  return wanted, hs
end
-- A lesson can also wait on op 0x1C, a native predicate polled every frame
-- (0x08036D90: has the player selected a unit; 0x08036C1C: chosen a
-- destination): the thread's PC stays on that record until it holds.
function M.lesson_waits()
  for i = 0, 7 do
    local e = M.LESSON_THREADS + 24 * i
    if M.r32(e) ~= 0 then
      local pc = M.r32(e + 4)
      if pc >= 0x08000000 and pc < 0x0A000000 and M.r32(pc) == 0x1C then
        return { thread = i, pc = pc, fn = M.r32(pc + 4) }
      end
    end
  end
  return nil
end
-- The tile a lesson means: the last op 0x28 before a native wait, or the
-- op 0x28 a handler re-places the cursor with before its nag.
local function tile_of(rec)
  local v = M.r32(rec + 8)
  return { x = math.floor((v % 0x10000) / 16), y = math.floor(math.floor(v / 0x10000) / 16), mode = M.r32(rec + 12) }
end
-- the cursor placements before a native wait, nearest first
function M.lesson_placements(waiting)
  local out = {}
  local a = waiting.pc
  for _ = 1, 24 do
    a = a - 16
    local op = M.r32(a)
    if op == 0x28 then out[#out + 1] = tile_of(a) end
    if op == 5 or op == 4 or op == 0x27 then break end
  end
  return out
end
function M.unit_at(x, y)
  for slot = 0, 4 * M.ARMY_SLOTS - 1 do
    local u = M.unit(slot)
    if u and not u.loaded and u.x == x and u.y == y then return u end
  end
  return nil
end
function M.lesson_target(waiting, hs, ev)
  if waiting then
    return M.lesson_placements(waiting)[1]
  end
  for _, h in ipairs(hs or {}) do
    if h.ev == ev then
      local a = h.handler
      for _ = 1, 96 do
        local op = M.r32(a)
        if op == 0x28 then return tile_of(a) end
        if op == 5 or op == 4 then break end
        a = a + 16
      end
    end
  end
  return nil
end
-- the unit the game holds selected (its state byte is 6 in move-select mode)
function M.selected_unit()
  local me = M.active_player()
  for slot = 0, 4 * M.ARMY_SLOTS - 1 do
    local u = M.unit(slot)
    -- bits 1 and 2 of the state byte (a carrying transport adds bit 4)
    if u and u.player == me and not u.loaded and math.floor(u.state / 2) % 2 == 1 and math.floor(u.state / 4) % 2 == 1 then return u end
  end
  return nil
end
-- The cursor the lesson predicates read: the u32 at 0x030036A4, x in the
-- low halfword, y in the high one. It follows the script's placements and
-- the pad in every mode (map, move-select, the drop selector, the target
-- selector), where the byte pair at 0x030033F0 tracks the map only.
M.TRUE_CURSOR = 0x030036A4
function M.true_cursor() return M.r16(M.TRUE_CURSOR), M.r16(M.TRUE_CURSOR + 2) end
function M.steer(t)
  local stuck = 0
  for _ = 1, 60 do
    local cx, cy = M.true_cursor()
    if cx == t.x and cy == t.y then return true end
    if cx < t.x then M.tap("right", 6, 16) elseif cx > t.x then M.tap("left", 6, 16)
    elseif cy < t.y then M.tap("down", 6, 16) else M.tap("up", 6, 16) end
    local nx, ny = M.true_cursor()
    if nx == cx and ny == cy then stuck = stuck + 1; if stuck >= 4 then return false end else stuck = 0 end
  end
  return false
end
-- The tiles a native wait accepts, read off its code: the predicates
-- (0x08036D90 and its neighbours) compare the true cursor with u32
-- literals of the form y << 16 | x and set the flag at 0x03003200.
function M.wait_tiles(fn)
  local base = fn - (fn % 2)
  local out = {}
  for i = 0, 63 do
    local pc = base + 2 * i
    local ins = M.r16(pc)
    if ins == 0x4770 then break end                      -- bx lr
    if math.floor(ins / 0x800) == 9 then                  -- ldr rN, [pc, #imm]
      local lit = (pc + 4) - ((pc + 4) % 4) + (ins % 256) * 4
      local v = M.r32(lit)
      local x, y = v % 0x10000, math.floor(v / 0x10000)
      if x < 40 and y < 40 and v ~= 0 then out[#out + 1] = { x = x, y = y } end
    end
  end
  return out
end
-- Walk the cursor to a tile. In map mode the cursor bytes track and
-- goto_tile does it; in move-select mode they do not (DERIVATION 29), so
-- the walk is counted taps from the selected unit's tile.
function M.walk_to(t)
  local u = M.selected_unit()
  if not u then return M.goto_tile(t.x, t.y) end
  local dx, dy = t.x - u.x, t.y - u.y
  for _ = 1, math.abs(dx) do M.tap(dx > 0 and "right" or "left", 6, 16) end
  for _ = 1, math.abs(dy) do M.tap(dy > 0 and "down" or "up", 6, 16) end
  return true
end
local function ev_name(ev) return string.format("%02X%s", ev, M.EVENT_NAMES[ev] and ("=" .. M.EVENT_NAMES[ev]) or "") end
function M.lesson_line()
  local wanted, hs = M.lesson_wanted()
  local all, want = {}, {}
  for _, h in ipairs(hs) do all[#all + 1] = ev_name(h.ev) end
  for _, ev in ipairs(wanted) do want[#want + 1] = ev_name(ev) end
  local cx, cy = M.cursor()
  local w = M.lesson_waits()
  local waiting = w and string.format(" waiting on %08X (%s)", w.fn, M.WAIT_NAMES[w.fn] or "?") or ""
  return string.format("registered [%s] wanted [%s]%s lock %d cursor %d,%d menu %s",
    table.concat(all, " "), table.concat(want, " "), waiting, M.r16(M.LESSON_LOCK), cx, cy, tostring(M.menu_open()))
end

-- Does the map cursor answer the pad?  A step aside and back, so a lesson's
-- cursor placement survives the question.
function M.cursor_answers()
  local cx, cy = M.cursor()
  local w = M.dims()
  local there, back = "right", "left"
  if cx >= w - 1 then there, back = "left", "right" end
  M.tap(there, 6, 16)
  local nx, ny = M.cursor()
  if nx == cx and ny == cy then return false end
  M.tap(back, 6, 16)
  return true
end

-- Play the lesson's part of the turn: page through its text, then do what
-- the registered handlers want -- select the unit under the cursor, press A
-- where the cursor was put, pick the wanted menu row, open the map menu --
-- until the script leaves the turn to the player (no handler, or only nags,
-- End or an attack the planner will make). Returns the number of actions.
function M.follow_lesson(cfg, tag)
  local acted, last_acted = 0, false
  for round = 1, 40 do
    local taps = 0
    while M.r16(M.LESSON_LOCK) ~= 0 and taps < 200 do M.tap("a", 6, 24); taps = taps + 1 end
    if taps >= 200 then
      M.L("  lesson: the script still holds the input after 200 taps"); M.shot(tag .. "-lesson-held")
      return acted
    end
    local wanted, hs = M.lesson_wanted()
    local waiting = M.lesson_waits()
    local ev = nil
    for _, e in ipairs(wanted) do
      if e == 0x2C or e == 0x11 or (e >= 0x15 and e <= 0x2A and e ~= 0x29) then ev = e; break end
      -- an attack wanted with the unit's menu already open: Fire, then the
      -- target the selector offers (a wait names the target's tile if the
      -- lesson minds which)
      if e == 0x0F and M.menu_open() then ev = 0x19 end
      -- ... or with the unit held selected and no menu yet (the Rockets
      -- lesson: "now open the Command Menu"): A opens it where it stands
      if e == 0x0F and not M.menu_open() and M.selected_unit() then ev = 0x2C end
    end
    if not ev and not waiting and last_acted then
      -- the script reacts to an action over a few frames (a cursor walk,
      -- a timer, a native wait): give it a moment before calling it free
      for _ = 1, 8 do
        M.wait(20)
        if M.r16(M.LESSON_LOCK) ~= 0 or #M.lesson_handlers() > 0 or M.lesson_waits() then break end
      end
      last_acted = false
      if M.r16(M.LESSON_LOCK) ~= 0 or #M.lesson_handlers() > 0 or M.lesson_waits() then goto continue end
    end
    if round == 1 or ev or waiting then M.L("  lesson: " .. M.lesson_line()) end
    if not ev and not waiting then break end
    M.shot(string.format("%s-lesson-%d", tag, round))
    if ev == 0x2C or (not ev and waiting) then
      if M.menu_open() then M.tap("b", 8, 40) end
      local t = M.lesson_target(waiting, hs, ev)
      local u = t and M.selected_unit()
      local foe = t and M.unit_at(t.x, t.y)
      local tiles = waiting and M.wait_tiles(waiting.fn) or {}
      if #tiles > 0 then
        -- the wait says which tiles it takes: the nearest to where the
        -- cursor is, steered by the true cursor in whatever mode the
        -- game is in (a move, a drop, a target), then A
        local cx, cy = M.true_cursor()
        local pick, bd = nil, 999
        local names = {}
        for _, tt in ipairs(tiles) do
          names[#names + 1] = string.format("(%d,%d)", tt.x, tt.y)
          local d = math.abs(tt.x - cx) + math.abs(tt.y - cy)
          if d < bd then pick, bd = tt, d end
        end
        u = M.selected_unit()
        M.L(string.format("  lesson: the wait takes %s; cursor (%d,%d)%s", table.concat(names, " "), cx, cy,
          u and string.format(", %s selected at (%d,%d)", u.name or "unit", u.x, u.y) or ""))
        if not u and not M.menu_open() then
          -- nothing selected: the unit to select is the last map placement
          for _, pl in ipairs(M.lesson_placements(waiting)) do
            if pl.mode == 7 and M.unit_at(pl.x, pl.y) and not (pl.x == pick.x and pl.y == pick.y) then
              M.L(string.format("  lesson: select the unit at (%d,%d) first", pl.x, pl.y))
              M.goto_tile(pl.x, pl.y); M.tap("a", 8, 60)
              break
            end
          end
        end
        if not M.steer(pick) then M.L("  lesson: the cursor would not reach the tile"); M.shot(tag .. "-lesson-steer") end
        M.tap("a", 8, 60)
      elseif t and u and foe and foe.player ~= M.active_player() then
        -- "Fire on this unit": the placement is the target and one of ours
        -- is selected -- stand beside it, Fire, confirm the target
        M.L(string.format("  lesson: attack %s at (%d,%d) with the selected unit at (%d,%d)", foe.type or "?", t.x, t.y, u.x, u.y))
        local best, bd = nil, 999
        for _, d in ipairs({ { 1, 0 }, { -1, 0 }, { 0, 1 }, { 0, -1 } }) do
          local nx, ny = t.x + d[1], t.y + d[2]
          local w, h = M.dims()
          if nx >= 0 and ny >= 0 and nx < w and ny < h and (M.unit_at(nx, ny) == nil or (nx == u.x and ny == u.y)) then
            local dist = math.abs(nx - u.x) + math.abs(ny - u.y)
            if dist < bd then best, bd = { x = nx, y = ny }, dist end
          end
        end
        if best then M.walk_to(best) end
        M.tap("a", 8, 60)
        if M.menu_open() then
          local rows, sel = M.menu_rows()
          local idx = nil
          for i, r in ipairs(rows) do if r == 0x19 then idx = i - 1 end end
          if idx then
            while sel < idx do M.tap("down", 6, 16); sel = sel + 1 end
            M.tap("a", 8, 60)
            M.select_target(t, 1)
            M.tap("a", 8, 60)
            M.wait(120)
          end
        end
      elseif t then
        if not u and t.mode ~= 7 and waiting then
          -- a destination with nothing selected: the unit is the last
          -- map placement before it ("Would you select it? ... move it here")
          for _, pl in ipairs(M.lesson_placements(waiting)) do
            if pl.mode == 7 then
              M.L(string.format("  lesson: select the unit at (%d,%d) first", pl.x, pl.y))
              M.goto_tile(pl.x, pl.y); M.tap("a", 8, 60)
              u = M.selected_unit()
              break
            end
          end
        end
        M.L(string.format("  lesson: the tile it means is (%d,%d) mode %d%s", t.x, t.y, t.mode,
          u and string.format(", walked from the selected unit at (%d,%d)", u.x, u.y) or ""))
        M.walk_to(t)
        M.tap("a", 8, 60)             -- A where the lesson put the cursor
      else
        M.tap("a", 8, 60)
      end
    elseif ev == 0x11 then
      local e = M.find_empty(cfg); M.goto_tile(e.x, e.y); M.tap("a", 8, 60)
    elseif M.menu_open() then
      local rows, sel = M.menu_rows()
      local idx = nil
      for i, r in ipairs(rows) do if r == ev then idx = i - 1 end end
      if idx then
        while sel < idx do M.tap("down", 6, 16); sel = sel + 1 end
        while sel > idx do M.tap("up", 6, 16); sel = sel - 1 end
        M.tap("a", 8, 90)
        if ev == 0x19 then
          -- the target selector: a wait may name the tile, else the first target
          M.wait(20)
          local w2 = M.lesson_waits()
          local tiles = w2 and M.wait_tiles(w2.fn) or {}
          if #tiles > 0 then M.steer(tiles[1]) end
          M.tap("a", 8, 60); M.wait(120)
        end
      else
        local names = {}
        for _, r in ipairs(rows) do names[#names + 1] = ev_name(r) end
        M.L("  lesson: the open menu [" .. table.concat(names, " ") .. "] has no row for " .. ev_name(ev))
        M.shot(tag .. "-lesson-menu"); M.tap("b", 8, 40)
      end
    elseif M.MAP_ITEMS[ev] then
      local e = M.find_empty(cfg); M.goto_tile(e.x, e.y); M.tap("a", 8, 60)
    else
      local t = M.lesson_target(nil, hs, ev)
      if t and t.mode ~= 7 then
        M.L(string.format("  lesson: the tile it means is (%d,%d) mode %d", t.x, t.y, t.mode))
        M.walk_to(t)
      end
      M.tap("a", 8, 60)               -- a unit item: A where the lesson put the cursor
    end
    acted = acted + 1
    last_acted = true
    M.wait(40)
    ::continue::
  end
  if acted > 0 then
    M.L(string.format("  lesson: %d action(s); %s", acted, M.lesson_line()))
    if M.menu_open() then M.tap("b", 8, 40) end
  end
  return acted
end

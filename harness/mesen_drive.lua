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
  for _ = 1, 90 do
    local cx, cy = M.cursor()
    if cx == x and cy == y then return true end
    if cx < x then M.tap("right") elseif cx > x then M.tap("left")
    elseif cy < y then M.tap("down") else M.tap("up") end
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
      local ar = M.army(wr.player)
      if (wr.funds and ar.funds ~= wr.funds) or (wr.co_id and ar.co_id ~= wr.co_id)
          or (wr.meter and ar.power ~= wr.meter) or (wr.uses and ar.power_uses ~= wr.uses) then
        return false, "write " .. i .. ": army read-back mismatch"
      end
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
        return false, string.format("unit %d untouched and the RNG never drew: no battle", c.slot)
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

local function pick_menu(index)
  for _ = 1, index do M.tap("down", 8, 26) end
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
  local acted, last, still = false, sig(), 0
  for _ = 1, 90 do
    M.wait(10)
    local a = M.unit(att)
    if a == nil or a.acted then acted = true end
    local now = sig()
    if now == last then still = still + 1 else still = 0 end
    last = now
    if acted and still >= 9 then return true end
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
      pick_menu(s.menu_index)
      if k == "attack" then
        local _, note = M.select_target(s.target, attempt)
        r.target_note = note
        M.wait(10); M.shot(s.tag .. "-target")
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
      if r.ok then
        M.wait(60)
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

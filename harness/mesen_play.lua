-- The acceptance loop for headless Mesen2 (ROADMAP step 6): the planner
-- plays a whole match against the game's own CPU, from a parked savestate
-- to the result, one action at a time through mesen_drive.lua's verified
-- single-action driver.
--
-- A LIBRARY, appended after mesen_state.lua and mesen_drive.lua by
-- tools/campaign_run.py, which also generates the runner body. Every
-- top-level name lives on AW.
--
-- The loop, per turn of ours:
--   1. dump the board (M.dump) to <run>/tNN.before.json;
--   2. shell out: `python tools/campaign_run.py plan ...` reads the dump,
--      judges the game (won / lost / day cap), plans the turn with the
--      CPU reply, compiles the steps for the driver and writes them as a
--      Lua table file this loop `load`s -- Mesen's Lua has no JSON reader,
--      Python has sim_diff.lua();
--   3. drive the steps with M.do_step (each verified by read-back); after
--      an attack, a build, a power or a failed step the board is dumped
--      and the rest of the turn re-planned from what the game actually
--      did (the plan was scored in the worst-case world) -- at most
--      MAX_REPLANS times a turn;
--   4. end the turn through M.cpu_turn (the CPU side's control byte
--      written to 2, End Turn, the CPU's whole turn, the turn back), then
--      save a checkpoint savestate <run>/tNN.mss so a run can resume;
--   5. repeat until the plan file says the game is over, the CPU never
--      hands the turn back (the dump then says why), or the turn cap.
--
-- Nothing here is a fact about the game; it is the harness that lets the
-- planner be judged by the game. The win is read off the board the game
-- leaves (engine/state.py) by campaign_run.py, never assumed.

AW = AW or {}
local M = AW

M.MAX_REPLANS = 4

local function load_table(path)
  local fh = io.open(path, "r")
  if not fh then return nil, "no file " .. path end
  local src = fh:read("*a"); fh:close()
  local chunk, err = load(src, "steps", "t", {})
  if not chunk then return nil, err end
  local ok, val = pcall(chunk)
  if not ok then return nil, tostring(val) end
  return val
end

-- Ask the planner service (campaign_run.py serve, started beside Mesen by
-- campaign_run.py run) for the turn's steps from a fresh dump. No process
-- is ever spawned from inside Mesen: a GUI process's os.execute opens a
-- console window per call. The request is a file; the answer is a file.
function M.ask_plan(cfg, turn, replan)
  local suffix = replan > 0 and ("r" .. replan) or ""
  local dump = string.format("%st%02d%s.json", cfg.run_dir, turn, suffix)
  local steps = string.format("%st%02d%s.steps.lua", cfg.run_dir, turn, suffix)
  M.dump(dump, { note = string.format("turn %d replan %d, campaign_run", turn, replan) })
  local rq = assert(io.open(steps .. ".req", "w"))
  rq:write(string.format("%d %d %s\n%s\n", turn, replan, dump, steps)); rq:close()
  M.L(string.format("  plan requested: turn %d replan %d", turn, replan))
  for _ = 1, (cfg.plan_wait or 36000) / 30 do      -- up to ten minutes of frames
    M.wait(30)
    local fh = io.open(steps, "r")
    if fh then
      fh:close()
      local t, err = load_table(steps)
      if t then return t end
      M.wait(30)                                     -- half-written: read again
      t, err = load_table(steps)
      if t then return t end
      return nil, "bad steps file: " .. tostring(err)
    end
  end
  return nil, "the planner service did not answer in time"
end

-- Tap through whatever is on screen (a turn card, a dialogue page) until
-- the map cursor answers again.
function M.settle_screen(tag)
  -- a campaign mission's scripted dialogue (mission one's CO power lesson
  -- ran past twelve presses) is pages of A; B does nothing for it
  for i = 1, 80 do
    if M.goto_tile(M.settle_tile.x, M.settle_tile.y) then
      if i > 1 then M.L(string.format("  settle: cursor answers after %d tap(s)", i - 1)) end
      return true
    end
    if i % 10 == 1 then M.L("  settle: cursor not answering, tap " .. i); M.shot(string.format("%s-settle-%d", tag, i)) end
    M.tap("a", 6, 30)
  end
  M.shot(tag .. "-stuck")
  return false
end

-- The match decided, read off RAM while the game is still in it: an HQ's
-- owner byte (the terrain byte is type + 32 * owner) or a side with no
-- unit record left. The VS result screen resets the map to day 1, so the
-- board must be read before the game leaves the match.
function M.match_status(cfg)
  local w = M.dims()
  for _, h in ipairs(cfg.hqs or {}) do
    local owner = math.floor(M.r8(M.MAP + h.y * w + h.x) / 32)
    if owner ~= h.owner then
      if owner == cfg.player then return "win", string.format("the HQ at (%d,%d) is ours", h.x, h.y) end
      if h.owner == cfg.player then return "loss", string.format("our HQ at (%d,%d) is P%d's", h.x, h.y, owner) end
    end
  end
  local mine, theirs = 0, 0
  for slot = 0, 4 * M.ARMY_SLOTS - 1 do
    local u = M.unit(slot)
    if u then
      if u.player == cfg.player then mine = mine + 1 elseif u.player == cfg.cpu then theirs = theirs + 1 end
    end
  end
  if mine == 0 then return "loss", "no unit of ours is left" end
  if theirs == 0 then return "win", "the enemy has no unit left" end
  return nil
end

-- A unit-free Plain or Road nearest the middle, for opening the map menu:
-- chosen fresh each time, since the tile picked at the start fills up.
function M.find_empty(cfg)
  local w, h = M.dims()
  local occ = {}
  for slot = 0, 4 * M.ARMY_SLOTS - 1 do
    local u = M.unit(slot)
    if u and not u.loaded then occ[u.y * 256 + u.x] = true end
  end
  local best, bx, by = nil, cfg.empty.x, cfg.empty.y
  for y = 0, h - 1 do
    for x = 0, w - 1 do
      local t = M.r8(M.MAP + y * w + x) % 32
      if (t == 1 or t == 5) and not occ[y * 256 + x] then
        local d = math.abs(x - math.floor(w / 2)) + math.abs(y - math.floor(h / 2))
        if best == nil or d < best then best, bx, by = d, x, y end
      end
    end
  end
  return { x = bx, y = by }
end

-- Once the match is decided, let the game finish it: the result screens
-- and the save of a mission clear happen AFTER the HQ's owner byte flips,
-- and an emulator stopped at once records nothing (the Field Training
-- clear, 2026-09-05). Tap A through the sequence for a while, screenshot,
-- and give the SRAM time to settle before the run ends.
function M.play_out(tag)
  M.L("  playing the ending out")
  for i = 1, 60 do
    M.tap("a", 6, 84)
    if i % 20 == 0 then M.shot(string.format("%s-ending-%d", tag, i)) end
  end
  M.wait(300)
  M.shot(tag .. "-ending-final")
  M.L(string.format("  ending played out: phase %d day %d", M.r16(M.MATCH_PHASE), M.r32(M.TURN)))
end

function M.play_game(cfg)
  local result = { ok = false, turns = {}, over = nil }
  local fh = assert(io.open(cfg.mss, "rb")); local bytes = fh:read("*a"); fh:close()
  emu.loadSavestate(bytes); M.wait(30)
  if cfg.w then M.set_dims(cfg.w, cfg.h) end
  M.control_orig = {}
  for p = 1, 4 do M.control_orig[p] = M.army(p).control end
  M.L(string.format("== play: %s as P%d, day %d active %d, control P1=%d P2=%d",
    cfg.name, cfg.player, M.r32(M.TURN), M.active_player(), M.army(1).control, M.army(2).control))
  M.settle_tile = cfg.empty
  M.settle_screen("start")
  for turn = 1, cfg.max_turns do
    local rec = { turn = turn, day = M.r32(M.TURN), steps = {}, replans = 0 }
    result.turns[turn] = rec
    if M.active_player() ~= cfg.player then
      rec.why = string.format("not our turn: active P%d", M.active_player())
      M.L("  " .. rec.why); M.shot(string.format("t%02d-not-ours", turn))
      result.over = "stuck"; break
    end
    local plan, err = M.ask_plan(cfg, turn, 0)
    if not plan then rec.why = err; M.L("  " .. err); result.over = "harness"; break end
    if plan.over then result.over = plan.over; rec.note = plan.note; M.L("  over: " .. plan.over .. " -- " .. tostring(plan.note)); break end
    rec.note = plan.note
    local queue = plan.steps or {}
    local i = 1
    while i <= #queue do
      local s = queue[i]
      if s.kind == "power" or s.kind == "end_turn" then s.empty = M.find_empty(cfg) end
      M.settle_screen(s.tag)          -- a dialogue the last step triggered
      local r = M.do_step(s, 1)
      rec.steps[#rec.steps + 1] = { tag = s.tag, kind = s.kind, ok = r.ok, why = r.why }
      M.L(string.format("  step %s %s: %s%s", s.tag, s.kind, r.ok and "ok" or "FAILED", r.why and (" -- " .. r.why) or ""))
      local decided, dwhy = M.match_status(cfg)
      if decided then
        result.over, rec.note = decided, dwhy
        M.L("  decided on our turn: " .. decided .. " -- " .. dwhy); M.shot(s.tag .. "-decided")
        break
      end
      -- re-plan on a failed step (the driver's read-back caught a stale
      -- step) and, only if cfg.replan_after says so, after an attack,
      -- build or power whose real outcome the worst-case plan did not
      -- know; a plan costs minutes on a fogged 12-unit board
      local replan = (not r.ok) or (cfg.replan_after and (s.kind == "attack" or s.kind == "build" or s.kind == "power"))
      if not r.ok then M.cancel(4); M.settle_screen(s.tag) end
      if replan and i < #queue or (not r.ok) then
        if rec.replans >= M.MAX_REPLANS then
          M.L("  replan budget spent; ending the turn"); break
        end
        rec.replans = rec.replans + 1
        local again, err2 = M.ask_plan(cfg, turn, rec.replans)
        if not again then rec.why = err2; M.L("  " .. err2); break end
        if again.over then result.over = again.over; rec.note = again.note; M.L("  over: " .. again.over .. " -- " .. tostring(again.note)); break end
        queue = again.steps or {}
        i = 1
      else
        i = i + 1
      end
    end
    if result.over then break end
    -- the CPU side plays: its control byte to 2, End Turn, wait for the turn back
    local w = M.do_step({ kind = "write", tag = string.format("t%02d-ctrl", turn),
                          writes = { { kind = "army", player = cfg.cpu, control = 2 },
                                     { kind = "army", player = cfg.player, control = 2 } } }, 1)
    local empty = M.find_empty(cfg)
    M.settle_tile = empty
    local c = M.cpu_turn({ kind = "cpu_turn", tag = string.format("t%02d-cpu", turn), empty = empty,
                           limit = cfg.cpu_limit or 3000, cpu = cfg.cpu, checks = {},
                           watch = function() return M.match_status(cfg) end })
    if c.result then
      result.over, rec.note = c.result, c.result_why
      M.L("  decided on the CPU's turn: " .. c.result .. " -- " .. tostring(c.result_why))
      M.shot(string.format("t%02d-decided", turn))
      break
    end
    rec.cpu_ok, rec.cpu_why, rec.cpu_commands = c.ok, c.why, c.commands and #c.commands or 0
    M.L(string.format("  cpu turn: %s%s, %d command(s)", c.ok and "back" or "NOT BACK", c.why and (" -- " .. c.why) or "", rec.cpu_commands))
    M.dump(string.format("%st%02d.after.json", cfg.run_dir, turn), { note = string.format("turn %d after the CPU", turn) })
    if not c.ok then
      -- the game may have ended on the CPU's turn: Python judges the dump
      local verdict, err3 = M.ask_plan(cfg, turn, 9)
      result.over = verdict and verdict.over or "stuck"
      rec.note = verdict and verdict.note or err3
      M.L("  after a turn that never came back: " .. tostring(result.over) .. " -- " .. tostring(rec.note))
      break
    end
    M.settle_screen(string.format("t%02d-next", turn))
    local okc, st = pcall(emu.createSavestate)
    if okc and type(st) == "string" then
      local f = io.open(string.format("%st%02d.mss", cfg.run_dir, turn), "wb")
      if f then f:write(st); f:close() end
    end
  end
  if result.over == "win" or result.over == "loss" then M.play_out(string.format("t%02d", #result.turns)) end
  if result.over == nil then result.over = "turncap" end
  result.ok = result.over ~= "harness" and result.over ~= "stuck"
  result.day = M.r32(M.TURN)
  return result
end

-- Where the CO modifiers enter the COUNTERATTACK path.
--
-- The last open half of A9b. The counter's SHAPE is settled -- it is
-- `base * raw_internal_hp / 100`, no display rule and no luck -- but every
-- counter ever recorded was neutral on both sides, so nothing says whether the
-- counter-attacker's ATTACK modifier enters at all, or where the target's
-- DEFENCE modifier lands. `engine/damage.py:counterattack()` raises rather than
-- guess. `tools/counter_check.py` scores the nine surviving positions; this
-- file produces the sweeps it scores.
--
-- WHY A NEW BOARD IS NEEDED. The counter has to be big enough for a modifier to
-- show. Every existing fixture is Tank -> Infantry, so the counter is an
-- Infantry's base 5 against a Tank, which lands on 1 or 2 whatever the CO says:
-- `sami_def_wood.json` has Sami's 120 on the counter-attacker and all nine
-- positions still predict 1. Turning the pair round -- Infantry attacking a
-- Tank -- makes the counter a Tank's base 75, which separates all nine.
--
-- USAGE
--   counter_co_build()    -- derive the Infantry-v-Tank fixture from the wood
--                            one by rewriting two unit records. Optional: skip
--                            it if you played a fixture yourself.
--   counter_co_probe()    -- one case. CHECK THIS BEFORE SWEEPING (see below).
--   counter_co_run()      -- the four sweeps, written to OUTDIR.
--
-- THE CONTROL THAT DECIDES WHETHER ANY OF IT COUNTS. `counter_co_build`
-- overwrites unit TYPES in a state that is already parked at target-select, so
-- the game chose "Fire" while the attacker was still a Tank. If it cached the
-- matchup at that moment rather than resolving it on confirm, the sweep
-- measures the cache and not the game. That is not an argument, it is a
-- reading: an Infantry hitting a Tank does 4-12, a Tank hitting an Infantry
-- does 60-67, and the two cannot be confused. `counter_co_probe` prints the
-- opening damage and says which one it saw. If it reports the Tank's number,
-- STOP -- the derived fixture is measuring the harness, and the fixture has to
-- be played by hand instead (see harness/fixtures/README.md).

-- Absolute, deliberately. mGBA's working directory when a script runs is
-- whatever the file dialog last landed on, so a relative fixture path fails
-- with "no such file" somewhere that has nothing to do with the experiment.
ADVISOR = ADVISOR or "C:/Users/geno5/Documents/Claude/advance-wars/advisor"
SRC_FIXTURE = SRC_FIXTURE or ADVISOR .. "/harness/fixtures/atk_tank_v_inf_wood.ss1"
FIXTURE = FIXTURE or ADVISOR .. "/harness/fixtures/atk_inf_v_tank_wood.ss1"
OUTDIR = OUTDIR or "C:/tmp"
ATT_SLOT = ATT_SLOT or 7
DEF_SLOT = DEF_SLOT or 66

-- RAM type ids are 1-based: damage-table row + 1.
TYPE_INFANTRY = 1
TYPE_TANK = 5
INFANTRY_MAX_AMMO, INFANTRY_MAX_FUEL = 0, 99
TANK_MAX_AMMO, TANK_MAX_FUEL = 9, 70

if not dmg_seedsweep then
  console:error("load harness/mgba_dmg.lua FIRST -- this file uses its "
    .. "readunit/dmg_seedsweep and does not duplicate them")
end

local function unitaddr(slot)
  return emu:read32(UNIT_BASE_PTR) + slot * UNIT_STRIDE
end

-- mgba_dmg.lua keeps its own readunit local, so this is a second copy rather
-- than a borrow. Same layout, and it stays in step because the constants it
-- reads (UNIT_BASE_PTR, UNIT_STRIDE) are that file's globals: +4 packs hp in
-- bits 0-6, ammo in 7-10 and capture progress in 11-15.
local function readunit(slot)
  local a = unitaddr(slot)
  local v = emu:read16(a + 4)
  return {
    type = emu:read8(a), x = emu:read8(a + 2), y = emu:read8(a + 3),
    hp = v % 128, ammo = math.floor(v / 128) % 16,
    fuel = emu:read8(a + 6) % 128,
  }
end

-- Rewrite a unit's type, ammo and fuel together.
--
-- Type alone is not enough and the leftover is not cosmetic: record +4 packs
-- hp/ammo/capture, so an Infantry promoted to Tank keeps the Infantry's 0 ammo.
-- The counter itself would survive that -- a Tank hits Infantry hardest with
-- its unlimited secondary, 75 against the primary's 35, so `select_weapon`
-- picks the same weapon at 0 ammo -- but a unit carrying its predecessor's
-- ammo is a board reached by no play, and leaving it there would be one more
-- thing to have to rule out if a number came back wrong.
local function retype(slot, newtype, ammo, fuel)
  local a = unitaddr(slot)
  local before = readunit(slot)
  emu:write8(a, newtype)
  local v = emu:read16(a + 4)
  -- hp bits 0-6 kept, ammo bits 7-10 replaced, capture bits 11-15 kept.
  local hp = v % 128
  local cap = math.floor(v / 2048)
  emu:write16(a + 4, cap * 2048 + ammo * 128 + hp)
  local f = emu:read8(a + 6)
  emu:write8(a + 6, f - f % 128 + fuel)

  local after = readunit(slot)
  if after.type ~= newtype or after.ammo ~= ammo or after.hp ~= before.hp then
    console:error(string.format("retype of slot %d did not take: type %d "
      .. "ammo %d hp %d (wanted type %d ammo %d hp %d)", slot, after.type,
      after.ammo, after.hp, newtype, ammo, before.hp))
    return false
  end
  console:log(string.format("  slot %3d: type %d -> %d, ammo %d -> %d, "
    .. "fuel %d -> %d, hp %d unchanged", slot, before.type, after.type,
    before.ammo, after.ammo, before.fuel, after.fuel, after.hp))
  return true
end

-- Derive the Infantry-v-Tank fixture from the Tank-v-Infantry one.
function counter_co_build(src, dst, att_slot, def_slot)
  src, dst = src or SRC_FIXTURE, dst or FIXTURE
  att_slot, def_slot = att_slot or ATT_SLOT, def_slot or DEF_SLOT
  if not emu:loadStateFile(src) then
    console:error("could not load " .. src); return
  end
  emu:runFrame()
  console:log("building " .. dst .. " from " .. src)
  console:log(string.format("fixture COs: P1 = %d, P2 = %d", coid(1), coid(2)))
  local ok_att = retype(att_slot, TYPE_INFANTRY, INFANTRY_MAX_AMMO,
                        INFANTRY_MAX_FUEL)
  if not ok_att then return end
  if not retype(def_slot, TYPE_TANK, TANK_MAX_AMMO, TANK_MAX_FUEL) then
    return
  end
  if not emu:saveStateFile(dst) then
    console:error("could not write " .. dst); return
  end
  console:log("wrote " .. dst)
  console:log("now run counter_co_probe() and check the opening damage")
end

-- One case, unswept, purely to read the opening damage. See the header: this is
-- the reading that says whether the derived fixture is a real board.
function counter_co_probe(fixture, att_slot, def_slot)
  fixture = fixture or FIXTURE
  att_slot, def_slot = att_slot or ATT_SLOT, def_slot or DEF_SLOT
  local dmg, counter = dmg_probe(fixture, att_slot, def_slot)
  if dmg == nil then return end
  console:log("")
  -- Judged here rather than left to the reader. The two candidates are 30-odd
  -- apart, so the boundary can be crude without being arguable.
  if dmg <= 20 then
    console:log(string.format("OPENING %d -- consistent with Infantry -> Tank "
      .. "(4-12). The retype is live.", dmg))
    console:log(string.format("counter %d came back; the neutral prediction "
      .. "for this board is 59-64.", counter))
    console:log("Sweep with counter_co_run().")
  elseif dmg >= 40 then
    console:log(string.format("OPENING %d -- that is a TANK hitting an "
      .. "Infantry (60-67), not an", dmg))
    console:log("Infantry hitting a Tank. The game resolved the matchup it had "
      .. "when Fire")
    console:log("was chosen, so the retype did not reach the damage path.")
    console:log("DO NOT SWEEP. Play the fixture by hand instead -- "
      .. "harness/fixtures/README.md.")
  else
    console:log(string.format("OPENING %d -- neither 4-12 nor 60-67. Something "
      .. "else is wrong; do not", dmg))
    console:log("sweep until it is understood.")
  end
end

-- The four sweeps.
--
-- Sweep 4 alone separates all nine positions; the other three are what make a
-- null answer readable. If 4 comes back in the neutral band, "no modifier
-- enters the counter" and "the CO never reached the damage path" predict the
-- same number, and that is the confusion A12 cost four sweeps. So 2 and 3 move
-- one modifier each, and 1 fixes the neutral band on this exact board rather
-- than on the engine's say-so.
--
-- Both COs are written every case. Writing one and inheriting the other is what
-- makes this fixture dangerous: its own P2 is Max, so "wrote nothing to P2" is
-- not "P2 was neutral".
COUNTER_CO_SWEEPS = {
  {name = "counter_co_1_baseline", p1 = 1, p2 = 1,
   why = "neutral both sides: fixes the band this board calls unmodified"},
  {name = "counter_co_2_attack",   p1 = 1, p2 = 2,
   why = "Max counter-attacks: 150 on Tank. Moves only if the ATTACK modifier enters"},
  {name = "counter_co_3_defence",  p1 = 4, p2 = 1,
   why = "Sami is countered: 90 on Infantry. Moves only if the DEFENCE modifier enters"},
  {name = "counter_co_4_both",     p1 = 4, p2 = 2,
   why = "both at once: nine positions, nine distinct predictions"},
}

function counter_co_run(fixture, att_slot, def_slot, outdir, nseeds)
  fixture = fixture or FIXTURE
  att_slot, def_slot = att_slot or ATT_SLOT, def_slot or DEF_SLOT
  outdir = outdir or OUTDIR
  for i, s in ipairs(COUNTER_CO_SWEEPS) do
    local out = string.format("%s/%s.json", outdir, s.name)
    console:log("")
    console:log(string.format("=== %d/%d  %s ===", i, #COUNTER_CO_SWEEPS,
      s.name))
    console:log("    " .. s.why)
    dmg_seedsweep(fixture, att_slot, def_slot, out,
      {cos = {[1] = s.p1, [2] = s.p2}, co_abilities = 1,
       nseeds = nseeds or 64})
  end
  console:log("")
  console:log("done. Score them with:")
  for _, s in ipairs(COUNTER_CO_SWEEPS) do
    console:log(string.format("  python tools/counter_check.py %s/%s.json",
      outdir, s.name))
  end
end

console:log("counter-CO harness loaded.")
console:log("  counter_co_build()   derive the Infantry-v-Tank fixture")
console:log("  counter_co_probe()   one case -- CHECK the opening damage")
console:log("  counter_co_run()     the four sweeps")

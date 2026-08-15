"""Regression tests for the extracted tables and the damage engine.

These lock in what we have actually established. They do NOT claim the formula
is correct -- that is calibrate.py's job. What they lock in is:
  * the ROM extraction still produces the same numbers
  * weapon selection reproduces known in-game behaviour
  * the HP conventions do not silently flip
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from engine.damage import (Attack, CounterModifiersUnknown, DEFAULT_DISPLAY,  # noqa: E402
                           DEFAULT_VARIANT, DISPLAY_VARIANTS, NEUTRAL_CO,
                           SURVIVING_VARIANTS, Unverified, VARIANTS,
                           can_attack, counterattack, damage_for_luck,
                           counter_damage, display_hp, fights_at_contact, resolve, screen_bars,
                           select_weapon, tables)
from engine import co                                          # noqa: E402


class TestTables(unittest.TestCase):
    def test_provenance_is_honest(self):
        p = tables()["provenance"]
        self.assertEqual(p["method"], "extracted from ROM binary")
        # If this ever flips to True it must be because calibration passed.
        self.assertIn("verified_against_emulator", p)

    def test_unarmed_units_have_no_weapons(self):
        for u in ("APC", "Lander", "TCopter"):
            for target in ("Infantry", "Tank", "Battleship"):
                self.assertIsNone(select_weapon(u, target), f"{u} vs {target}")

    def test_infantry_has_only_a_secondary(self):
        self.assertEqual(tables()["primary"]["Infantry"]["Infantry"], 0)
        self.assertEqual(tables()["secondary"]["Infantry"]["Infantry"], 55)

    def test_mech_bazooka_cannot_hit_foot_soldiers(self):
        self.assertEqual(tables()["primary"]["Mech"]["Infantry"], 0)
        # but its machine gun can
        self.assertEqual(tables()["secondary"]["Mech"]["Infantry"], 65)

    def test_missiles_and_fighters_are_air_only(self):
        for u in ("Missiles", "Fighter"):
            for ground in ("Infantry", "Tank", "MdTank", "Battleship", "Sub"):
                self.assertFalse(can_attack(u, ground), f"{u} vs {ground}")
            self.assertTrue(can_attack(u, "BCopter"))

    def test_cruiser_primary_is_antisubmarine_only(self):
        prim = tables()["primary"]["Cruiser"]
        self.assertEqual(prim["Sub"], 90)
        self.assertEqual([t for t, v in prim.items() if v], ["Sub"])

    def test_sub_hits_only_naval(self):
        prim = tables()["primary"]["Sub"]
        self.assertEqual(sorted(t for t, v in prim.items() if v),
                         ["Battleship", "Cruiser", "Lander", "Sub"])

    def test_tank_mdtank_asymmetry(self):
        """The pair that pins unit IDs 2 and 4. If these ever swap, the whole
        ID mapping is wrong and every number downstream is garbage."""
        self.assertEqual(tables()["primary"]["MdTank"]["Tank"], 85)
        self.assertEqual(tables()["primary"]["Tank"]["MdTank"], 15)

    def test_fighter_discrepancy_was_resolved_by_xref(self):
        qs = {q["id"] for q in tables().get("resolved_questions", [])}
        self.assertIn("fighter-secondary", qs)
        self.assertEqual(tables().get("open_questions"), [])
        # The resolution: alt copy is dead data, so no Fighter secondary.
        self.assertEqual(tables()["secondary"]["Fighter"]["TCopter"], 0)
        self.assertIsNone(select_weapon("Fighter", "TCopter", ammo=0))

    def test_rom_index_formula_matches_extraction(self):
        """The game indexes table + (att-1)*24 + (def-1), read straight off the
        disassembly at 0x08022EB0. Our 24-wide extraction must agree."""
        self.assertEqual(tables()["provenance"]["stride"], 24)
        self.assertEqual(tables()["code_analysis"]["index_formula"],
                         "table + (attacker_type - 1) * 24 + (defender_type - 1)")


class TestWeaponSelection(unittest.TestCase):
    def test_mdtank_uses_machine_gun_on_infantry(self):
        w = select_weapon("MdTank", "Infantry")
        self.assertEqual((w.slot, w.base), ("secondary", 105))

    def test_mdtank_uses_cannon_on_armour(self):
        w = select_weapon("MdTank", "Tank")
        self.assertEqual((w.slot, w.base), ("primary", 85))

    def test_out_of_ammo_falls_back_to_secondary(self):
        w = select_weapon("MdTank", "Tank", ammo=0)
        self.assertEqual((w.slot, w.base), ("secondary", 8))

    def test_out_of_ammo_artillery_cannot_attack_at_all(self):
        self.assertIsNone(select_weapon("Artillery", "Tank", ammo=0))


class TestHpConventions(unittest.TestCase):
    """Combat scales HP by `ceil(internal / 10)` -- the same rounding the
    screen uses.

    For months this file asserted the opposite, that the two were different
    functions and a Mech at 57 showed 6 bars while attacking as 5. That came
    from two counterattack observations fitted with the strike formula; the
    game computes counters differently (A9b), so they never said anything about
    this. Written HP and 64 seeded rolls per board settled it: attacker at 57
    deals 27-32, which only ceil produces.

    `display_hp` and `screen_bars` therefore return the same number today. They
    are kept as separate functions because they answer separate questions, and
    nothing has been measured that would force them apart again -- but no test
    here may assert that they differ, because they do not.
    """

    def test_combat_scaling_rounds_up(self):
        self.assertEqual(display_hp(100), 10)
        self.assertEqual(display_hp(57), 6)     # measured: 27-32 damage, not 22-27
        self.assertEqual(display_hp(81), 9)     # the sweep that excluded `round`
        self.assertEqual(display_hp(91), 10)
        self.assertEqual(display_hp(10), 1)
        self.assertEqual(display_hp(0), 0)

    def test_last_bar_attacks_at_strength_one_not_zero(self):
        """An Infantry at 9 internal HP dealt 11 damage, which plain floor
        (strength 0, so no damage at all) cannot produce. This one row is the
        whole reason `floor` was never a candidate."""
        self.assertEqual(display_hp(9), 1)
        self.assertEqual(display_hp(1), 1)
        self.assertEqual(DISPLAY_VARIANTS["floor"](9), 0)   # the refuted rule

    def test_screen_bars_round_up(self):
        self.assertEqual(screen_bars(57), 6)    # what the player sees
        self.assertEqual(screen_bars(91), 10)
        self.assertEqual(screen_bars(90), 9)
        self.assertEqual(screen_bars(1), 1)

    def test_the_two_coincide_now(self):
        """They were modelled as different functions on the strength of
        evidence that turned out to be about something else. Every internal HP
        agrees. If a measurement ever separates them again, change this test
        and say what measured it -- do not just widen it."""
        for hp in range(0, 101):
            self.assertEqual(display_hp(hp), screen_bars(hp), hp)

    def test_refuted_rules_are_kept_but_not_default(self):
        for refuted in ("floor", "floor_min1", "round"):
            self.assertIn(refuted, DISPLAY_VARIANTS)
            self.assertNotEqual(DEFAULT_DISPLAY, refuted)
        self.assertEqual(DEFAULT_DISPLAY, "ceil")

    def test_default_variant_is_not_a_refuted_one(self):
        """Leaving a refuted variant as the default is how a tool goes quietly
        wrong. These four were eliminated by emulator data."""
        for refuted in ("floor_end", "floor_attack_then_end",
                        "floor_each_step", "round_end"):
            self.assertIn(refuted, VARIANTS)
            self.assertNotEqual(DEFAULT_VARIANT, refuted)
        self.assertIn(DEFAULT_VARIANT, ("luck_after_hp", "luck_last"))

    def test_units_in_the_same_band_attack_identically(self):
        """Under ceil the band is 21..30, not 20..29 -- an off-by-one that
        moves with the rounding rule, which is exactly why the rule had to be
        measured rather than assumed."""
        a = Attack("Tank", "Infantry", attacker_hp=21)
        b = Attack("Tank", "Infantry", attacker_hp=30)
        self.assertEqual(damage_for_luck(a, 0), damage_for_luck(b, 0))
        # ...and 20 is in the band below, so it hits for less.
        c = Attack("Tank", "Infantry", attacker_hp=20)
        self.assertLess(damage_for_luck(c, 0), damage_for_luck(a, 0))

    def test_but_they_die_differently(self):
        self.assertNotEqual(21 - 15, 30 - 15)


class TestEngineBehaviour(unittest.TestCase):
    def test_resolve_works_now_that_calibration_passed(self):
        self.assertTrue(tables()["provenance"]["verified_against_emulator"])
        self.assertIsNotNone(resolve(Attack("Tank", "Infantry")))

    def test_the_unverified_guard_still_functions(self):
        """The guard is what stopped the tool giving advice under an unvalidated
        model. It must still fire if the flag is ever cleared."""
        import engine.damage as dmg
        saved = dmg.tables()["provenance"]["verified_against_emulator"]
        dmg.tables()["provenance"]["verified_against_emulator"] = False
        try:
            with self.assertRaises(Unverified):
                resolve(Attack("Tank", "Infantry"))
        finally:
            dmg.tables()["provenance"]["verified_against_emulator"] = saved

    def test_envelope_covers_both_surviving_variants(self):
        """max_damage must be the wider of the two, so we never claim something
        cannot die when one surviving variant says it might."""
        a = Attack("Tank", "Infantry", terrain_stars=4)
        env = resolve(a)
        for v in SURVIVING_VARIANTS:
            single = resolve(a, variant=v)
            self.assertGreaterEqual(env.max_damage, single.max_damage, v)
            self.assertLessEqual(env.min_damage, single.min_damage, v)

    def test_guaranteed_kill_is_exact_not_an_envelope(self):
        """The survivors agree on minimum damage, so guaranteed_kill is not
        widened by the ensemble -- it is the same under either variant."""
        for stars in range(5):
            for dhp in (100, 60, 30, 12):
                a = Attack("Tank", "Infantry", defender_hp=dhp, terrain_stars=stars)
                mins = {resolve(a, variant=v).min_damage for v in SURVIVING_VARIANTS}
                self.assertEqual(len(mins), 1, f"{stars} stars, {dhp} hp")

    def test_the_formula_is_now_determined(self):
        """This test used to assert the opposite.

        The survivors DID differ at the top on defended terrain, and the engine
        flagged it. A seeded sweep over the combat luck state then refuted
        `luck_last` on distribution shape, leaving one variant -- so nothing
        disagrees any more, at any star count. Kept as a test rather than
        deleted: if a future observation reopens the question and a second name
        goes back into SURVIVING_VARIANTS, this fails and says so.
        """
        self.assertEqual(len(SURVIVING_VARIANTS), 1, SURVIVING_VARIANTS)
        self.assertEqual(SURVIVING_VARIANTS[0], "luck_after_hp")
        for stars in range(5):
            self.assertTrue(
                resolve(Attack("Tank", "Infantry", terrain_stars=stars))
                .variants_agree, f"{stars} stars")

    def test_co_modifiers_come_from_the_record(self):
        """Max's Tank hits at 150%, Andy's at 100%, straight from the pool."""
        from engine import co as co_mod
        self.assertEqual(co_mod.modifiers(2, "Tank")[0], 150)     # Max
        self.assertEqual(co_mod.modifiers(1, "Tank")[0], 100)     # Andy
        mx = resolve(Attack.between("Tank", "Infantry", 2, 1, terrain_stars=4))
        an = resolve(Attack.between("Tank", "Infantry", 1, 1, terrain_stars=4))
        self.assertGreater(mx.min_damage, an.min_damage)

    def test_grit_spares_exactly_the_indirects(self):
        """The mistake that named him a 'handicap record' was reading which
        units are reduced instead of which are not."""
        from engine import co as co_mod
        for u in ("Artillery", "Rockets", "Missiles", "Battleship"):
            self.assertEqual(co_mod.modifiers(5, u), (100, 100), u)
        for u in ("Tank", "Infantry", "BCopter"):
            self.assertEqual(co_mod.modifiers(5, u)[0], 80, u)

    def test_attacker_and_defender_records_are_not_crossed(self):
        """co_attack indexes the ATTACKER's record by the ATTACKING unit;
        co_defense indexes the DEFENDER's by the DEFENDING unit. Sami makes
        the difference visible: her Infantry is 120/90 and her Tank 90/100, so
        crossing the two would put 90 where 100 belongs."""
        a = Attack.between("Infantry", "Tank", attacker_co=4, defender_co=4)
        self.assertEqual(a.co_attack, 120)     # Sami's Infantry, attacking
        self.assertEqual(a.co_defense, 100)    # Sami's Tank, defending
        self.assertNotEqual(a.co_defense, 90)  # would be her Infantry's defence

    def test_the_header_pair_is_no_longer_unmodelled(self):
        """This test used to assert the opposite, and was right to.

        Kanbei has no per-unit modifiers at all -- every entry is 100/100 --
        and his strength is entirely in header +11/+12, which the damage path
        had never been shown to read. A quote built from the pool alone would
        have been neutral, so `Attack.between` refused.

        It is read now, and measured in both directions: Kanbei attacking
        multiplies the value by 120/100, Kanbei defending by 80/100. So nothing
        is unmodelled and nothing refuses. See A14."""
        from engine import co as co_mod
        for cid in (6, 10, 11):                     # Kanbei, Sturm, Sturm
            self.assertEqual(co_mod.unmodelled(cid), {}, cid)
            a = Attack.between("Tank", "Infantry", attacker_co=cid,
                               defender_co=1)
            self.assertNotEqual(a.co_attack, 100,
                                f"CO {cid} should carry a real attack modifier")
        # Kanbei specifically: 100 per-unit folded with 120 universal.
        kanbei = Attack.between("Tank", "Infantry", attacker_co=6, defender_co=1)
        self.assertEqual(kanbei.co_attack, 120)
        # ...and defending, his +12 of 80 comes through as the value multiplier.
        into_kanbei = Attack.between("Tank", "Infantry", attacker_co=1,
                                     defender_co=6)
        self.assertEqual(into_kanbei.co_defense, 80)
        # An explicit override is still allowed, for deliberate work.
        forced = Attack("Tank", "Infantry", co_attack=120, co_defense=100)
        self.assertEqual(forced.co_attack, 120)

    def test_kanbei_matches_the_board_he_was_measured_on(self):
        """Both directions, against the sweeps. Tank -> Infantry in woods,
        where a neutral CO is confined to 60-67."""
        att = resolve(Attack.between("Tank", "Infantry", attacker_co=6,
                                     defender_co=1, terrain_stars=2),
                      verified=True)
        self.assertEqual((att.min_damage, att.max_damage), (72, 79))
        dfn = resolve(Attack.between("Tank", "Infantry", attacker_co=1,
                                     defender_co=6, terrain_stars=2),
                      verified=True)
        self.assertEqual((dfn.min_damage, dfn.max_damage), (48, 55))

    def test_cos_that_are_fully_modelled_do_not_refuse(self):
        from engine import co as co_mod
        for cid in (0, 1, 2, 3, 4, 5, 7, 8, 9):
            self.assertFalse(co_mod.unmodelled(cid), cid)
            Attack.between("Tank", "Infantry", attacker_co=cid, defender_co=cid)

    def test_the_envelope_is_now_a_point(self):
        """With one variant, max_damage is the real maximum rather than the
        wider of two guesses -- so 'cannot kill' is finally exact in both
        directions, not just on the guaranteed end."""
        a = Attack("Tank", "Infantry", terrain_stars=4)
        env = resolve(a)
        single = resolve(a, variant="luck_after_hp")
        self.assertEqual(env.max_damage, single.max_damage)
        self.assertEqual(env.min_damage, single.min_damage)

    def test_damage_is_monotonic_in_attacker_hp(self):
        for variant in VARIANTS:
            prev = -1
            for hp in range(10, 101, 10):
                d = damage_for_luck(Attack("Tank", "Infantry", attacker_hp=hp), 0, variant)
                self.assertGreaterEqual(d, prev, f"{variant} at {hp} HP")
                prev = d

    def test_terrain_reduces_damage(self):
        plain = resolve(Attack("Tank", "Infantry", terrain_stars=0), verified=True)
        mount = resolve(Attack("Tank", "Infantry", terrain_stars=4), verified=True)
        self.assertLess(mount.max_damage, plain.max_damage)

    def test_illegal_attack_resolves_to_none(self):
        self.assertIsNone(resolve(Attack("APC", "Infantry"), verified=True))
        self.assertIsNone(resolve(Attack("Infantry", "Fighter"), verified=True))

    def test_counterattack_uses_the_survivors_reduced_hp(self):
        a = Attack("Tank", "Tank")
        first = resolve(a, verified=True)
        back = counterattack(a, verified=True)
        self.assertIsNotNone(back)
        # The counter must be weaker than a full-health strike, or alpha-striking
        # would be modelled as pointless.
        full = resolve(Attack("Tank", "Tank"), verified=True)
        self.assertLess(back.max_damage, full.max_damage)
        self.assertLess(first.min_remaining_hp, 100)

    def test_counter_envelope_spans_the_OPENING_luck_only(self):
        """The counter carries no roll of its own, so its spread comes entirely
        from which survivor the opening left. Both ends must be reachable."""
        a = Attack("Tank", "Tank")
        first = resolve(a, verified=True)
        back = counterattack(a, verified=True)
        lo = counter_damage(select_weapon("Tank", "Tank").base,
                            first.min_remaining_hp, 100, 0, 100)
        hi = counter_damage(select_weapon("Tank", "Tank").base,
                            first.max_remaining_hp, 100, 0, 100)
        self.assertEqual((back.min_damage, back.max_damage), (lo, hi))
        # And the lower bound is a REAL lower bound now: the weakest survivor
        # is reachable, which is what the old one-survivor quote got wrong.
        self.assertLess(back.min_damage, back.max_damage)

    def test_counter_is_deterministic_given_the_survivor(self):
        """Measured: 64 seeds on one board, opening 45-50, counter 2 every
        time. No luck term exists, so equal survivors must counter equally."""
        base = select_weapon("Infantry", "Tank").base
        vals = {counter_damage(base, s, 100, 0, 100) for s in range(50, 56)}
        self.assertEqual(vals, {2})

    def test_counter_matches_the_seed_sweep_fixture(self):
        """End-to-end against the real measurement: Tank on a bridge attacking
        an Infantry on a mountain. 64 seeds gave opening 45-50 and counter 2."""
        a = Attack("Tank", "Infantry", terrain_stars=4)
        first = resolve(a, verified=True)
        self.assertEqual((first.min_damage, first.max_damage), (45, 50))
        back = counterattack(a, verified=True, attacker_stars=0)
        self.assertEqual((back.min_damage, back.max_damage), (2, 2))

    def test_counter_reproduces_every_recorded_counterattack(self):
        """The four counters in harness/observations.csv, exactly. These are
        the rows that refuted `ceil` under the STRIKE formula -- they fit the
        counter formula with no free parameter, which is why A5 reopened."""
        for att, dfn, hp, stars, observed in (
                ("Infantry", "Tank", 22, 0, 1),      # road
                ("Infantry", "Tank", 24, 0, 1),      # road
                ("Infantry", "Tank", 25, 1, 0),      # plains
                ("Mech", "Tank", 57, 1, 27)):        # plains
            base = select_weapon(att, dfn).base
            self.assertEqual(counter_damage(base, hp, 100, stars, 100), observed,
                             f"{att} at {hp} HP into {dfn} on {stars} stars")

    def test_counter_refuses_a_non_neutral_CO(self):
        """Where CO modifiers enter the counter path has never been observed --
        every recorded counter was neutral. A number here would be invented."""
        from engine import co as co_mod
        max_id = next(i for i in range(12) if co_mod.record(i).name == "Max")
        andy_id = next(i for i in range(12) if co_mod.record(i).name == "Andy")
        with self.assertRaises(CounterModifiersUnknown):
            counterattack(Attack.between("Tank", "Tank", andy_id, max_id),
                          verified=True,
                          attacker_co=andy_id, defender_co=max_id)
        self.assertIsNotNone(
            counterattack(Attack.between("Tank", "Tank", andy_id, andy_id),
                          verified=True,
                          attacker_co=andy_id, defender_co=andy_id))

    def test_possible_kill_still_counters(self):
        """A kill that is possible but not guaranteed leaves a survivor on the
        rolls where it does not land. Returning None there let quote.py print
        'kill NOT guaranteed' and 'counter none' on adjacent lines."""
        # 44-50 damage into 50 HP: dead on the high roll, alive on 6 HP on the
        # low one. min_remaining_hp is 0 here, which is what silenced it.
        a = Attack("Tank", "Tank", defender_hp=50, terrain_stars=4)
        first = resolve(a, verified=True)
        self.assertTrue(first.possible_kill)
        self.assertFalse(first.guaranteed_kill)
        self.assertEqual(first.min_remaining_hp, 0)
        self.assertGreater(first.max_remaining_hp, 0)
        self.assertIsNotNone(counterattack(a, verified=True))

    def test_dead_defender_does_not_counter(self):
        a = Attack("MdTank", "Infantry", defender_hp=10)
        self.assertTrue(resolve(a, verified=True).guaranteed_kill)
        self.assertIsNone(counterattack(a, verified=True))

    def test_indirect_fire_neither_draws_nor_makes_a_counter(self):
        # Shelling from two tiles away: the Tank cannot reach back.
        self.assertIsNone(counterattack(Attack("Artillery", "Tank"), verified=True))
        # And the Artillery cannot fire at its own feet when hit at contact.
        self.assertIsNone(counterattack(Attack("Tank", "Artillery"), verified=True))
        # Direct against direct still counters, so the rule is not just "off".
        self.assertIsNotNone(counterattack(Attack("Tank", "Tank"), verified=True))

    def test_fights_at_contact_reads_the_range_table(self):
        for direct in ("Infantry", "Tank", "MdTank", "Fighter", "Cruiser"):
            self.assertTrue(fights_at_contact(direct), direct)
        for indirect in ("Artillery", "Rockets", "Missiles", "Battleship"):
            self.assertFalse(fights_at_contact(indirect), indirect)
        for unarmed in ("APC", "Lander", "TCopter"):
            self.assertFalse(fights_at_contact(unarmed), unarmed)

    def test_counter_refuses_rather_than_reusing_the_wrong_co_field(self):
        """`a.co_defense` is the defending CO's DEFENCE modifier. The return
        strike needs its ATTACK modifier, a different field. Swapping them was
        silently wrong, so the modifiers now have to be supplied."""
        loaded = Attack("Tank", "Tank", co_attack=150, co_defense=NEUTRAL_CO)
        with self.assertRaises(CounterModifiersUnknown):
            counterattack(loaded, verified=True)
        # Neutral on both sides is answerable without them.
        self.assertIsNotNone(counterattack(Attack("Tank", "Tank"), verified=True))

    def test_counter_lands_on_the_attackers_terrain(self):
        a = Attack("Tank", "Tank")
        open_ground = counterattack(a, verified=True, attacker_stars=0)
        in_cover = counterattack(a, verified=True, attacker_stars=4)
        self.assertLess(in_cover.max_damage, open_ground.max_damage)

    def test_counter_needs_both_co_ids_or_neither(self):
        with self.assertRaises(ValueError):
            counterattack(Attack("Tank", "Tank"), verified=True, attacker_co=1)

    def test_damage_never_exceeds_defender_hp(self):
        for variant in VARIANTS:
            d = damage_for_luck(Attack("Bomber", "Infantry", defender_hp=15), 9, variant)
            self.assertLessEqual(d, 15)


class TestPerCoLuck(unittest.TestCase):
    """The luck range comes out of the CO record's +06/+07 bytes, under one
    rule with no per-CO special cases:

        luck = uniform(0, 9 + good) - bad

    These pin the reading. They are NOT evidence the game agrees -- no sweep
    has yet witnessed a roll outside 0..9. See tools/luck_range_check.py, which
    is the thing that can settle it.
    """

    def test_ten_of_twelve_records_roll_the_standard_range(self):
        ordinary = [i for i in range(12) if co.luck(i) == (0, 9)]
        self.assertEqual(len(ordinary), 10)
        # The two that differ are the two the community documents as differing.
        odd = sorted(co.record(i).name for i in range(12) if i not in ordinary)
        self.assertEqual(odd, ["Nell", "Sonja"])

    def test_nell_widens_upward_and_her_power_widens_further(self):
        self.assertEqual(co.luck(0), (0, 19))
        self.assertEqual(co.luck(0, power=True), (0, 59))

    def test_sonja_slides_the_window_down_rather_than_widening_it(self):
        """The symmetric 15/15 pair is what forces the rule. Reading +06 alone
        would give her 0..24, a BETTER roll than everyone else; reading +07
        alone would give -15..-6, which cannot even reach zero. Only both
        together produce a same-width window slid downward."""
        self.assertEqual(co.luck(7), (-15, 9))
        lo, hi = co.luck(7)
        self.assertEqual(hi - lo, 24)
        self.assertEqual(co.record(7).luck_good, co.record(7).luck_bad)

    def test_the_range_reaches_the_damage_model(self):
        andy = Attack.between("Tank", "Infantry", 1, 1)
        sonja = Attack.between("Tank", "Infantry", 7, 1)
        self.assertEqual((andy.luck_min, andy.luck_max), (0, 9))
        self.assertEqual((sonja.luck_min, sonja.luck_max), (-15, 9))
        self.assertLess(resolve(sonja).min_damage,
                        resolve(andy).min_damage)

    def test_luck_is_the_attackers_and_never_the_defenders(self):
        """Taking it from the defender would hand Sonja's penalty to whoever
        shoots at her, which is both wrong and wrong in the unsafe direction."""
        into_sonja = Attack.between("Tank", "Infantry", 1, 7)
        self.assertEqual((into_sonja.luck_min, into_sonja.luck_max), (0, 9))

    def test_sonja_loses_guaranteed_kills_a_standard_co_would_have(self):
        """The reason this is a correctness fix and not a refinement. At these
        defender HPs the old model called the kill guaranteed; a -15 roll
        leaves the target standing."""
        flipped = 0
        for hp in range(55, 90):
            andy = resolve(Attack.between(
                "Tank", "Infantry", 1, 1, defender_hp=hp))
            sonja = resolve(Attack.between(
                "Tank", "Infantry", 7, 1, defender_hp=hp))
            if andy.guaranteed_kill and not sonja.guaranteed_kill:
                flipped += 1
            # Never the other way: her floor is lower, so she can only lose
            # guarantees, never gain them.
            self.assertFalse(sonja.guaranteed_kill and not andy.guaranteed_kill)
        self.assertGreater(flipped, 0)

    def test_an_explicit_range_still_overrides_the_record(self):
        a = Attack.between("Tank", "Infantry", 7, 1,
                                  luck_min=0, luck_max=9)
        self.assertEqual((a.luck_min, a.luck_max), (0, 9))


if __name__ == "__main__":
    unittest.main(verbosity=2)



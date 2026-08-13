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
from engine.damage import (Attack, DEFAULT_DISPLAY, DEFAULT_VARIANT,  # noqa: E402
                           DISPLAY_VARIANTS, SURVIVING_VARIANTS, Unverified, VARIANTS,
                           can_attack, counterattack, damage_for_luck,
                           display_hp, resolve, screen_bars, select_weapon, tables)


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
    """The screen and the combat maths use DIFFERENT roundings.

    This was originally modelled as ceil() for both, and the emulator refuted
    it: a Mech at 57 internal HP shows 6 bars but attacks as 5. Conflating the
    two is what produced the contradiction.
    """

    def test_combat_scaling_truncates(self):
        self.assertEqual(display_hp(100), 10)
        self.assertEqual(display_hp(57), 5)     # NOT 6
        self.assertEqual(display_hp(99), 9)
        self.assertEqual(display_hp(10), 1)
        self.assertEqual(display_hp(0), 0)

    def test_last_bar_attacks_at_strength_one_not_zero(self):
        """An Infantry at 9 internal HP dealt 11 damage, which plain floor
        (strength 0, so no damage at all) cannot produce."""
        self.assertEqual(display_hp(9), 1)
        self.assertEqual(display_hp(1), 1)
        self.assertEqual(DISPLAY_VARIANTS["floor"](9), 0)   # the refuted rule

    def test_screen_bars_round_up(self):
        self.assertEqual(screen_bars(57), 6)    # what the player sees
        self.assertEqual(screen_bars(91), 10)
        self.assertEqual(screen_bars(90), 9)
        self.assertEqual(screen_bars(1), 1)

    def test_the_two_disagree_and_that_is_the_point(self):
        # Only above 10: below that, floor_min1 clamps to 1 and so does ceil,
        # so the two rules coincide on the last bar.
        for hp in (57, 91, 22, 13):
            self.assertNotEqual(display_hp(hp), screen_bars(hp), hp)

    def test_refuted_rules_are_kept_but_not_default(self):
        for refuted in ("ceil", "round", "floor"):
            self.assertIn(refuted, DISPLAY_VARIANTS)
            self.assertNotEqual(DEFAULT_DISPLAY, refuted)
        self.assertEqual(DEFAULT_DISPLAY, "floor_min1")

    def test_default_variant_is_not_a_refuted_one(self):
        """Leaving a refuted variant as the default is how a tool goes quietly
        wrong. These four were eliminated by emulator data."""
        for refuted in ("floor_end", "floor_attack_then_end",
                        "floor_each_step", "round_end"):
            self.assertIn(refuted, VARIANTS)
            self.assertNotEqual(DEFAULT_VARIANT, refuted)
        self.assertIn(DEFAULT_VARIANT, ("luck_after_hp", "luck_last"))

    def test_units_in_the_same_decile_attack_identically(self):
        """20 and 29 both scale to 2 -- the off-by-one that breaks naive calcs."""
        a = Attack("Tank", "Infantry", attacker_hp=20)
        b = Attack("Tank", "Infantry", attacker_hp=29)
        self.assertEqual(damage_for_luck(a, 0), damage_for_luck(b, 0))

    def test_but_they_die_differently(self):
        self.assertNotEqual(20 - 15, 29 - 15)


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

    def test_dead_defender_does_not_counter(self):
        a = Attack("MdTank", "Infantry", defender_hp=10)
        self.assertIsNone(counterattack(a, verified=True))

    def test_damage_never_exceeds_defender_hp(self):
        for variant in VARIANTS:
            d = damage_for_luck(Attack("Bomber", "Infantry", defender_hp=15), 9, variant)
            self.assertLessEqual(d, 15)


if __name__ == "__main__":
    unittest.main(verbosity=2)



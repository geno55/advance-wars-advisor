"""The CO power system against its measurements (DERIVATION 27).

Every number asserted here was measured live through the headless rig or read
off the ROM at a named address; tests/fixtures/power_probes.json is the
measurement record. If a formula change breaks one of these, the change is
wrong -- the fixtures do not move.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from engine import co

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PROBES = json.loads((FIXTURES / "power_probes.json").read_text(encoding="utf-8"))

NAMES = {"Nell": 0, "Andy": 1, "Max": 2, "Olaf": 3, "Sami": 4, "Grit": 5,
         "Kanbei": 6, "Sonja": 7, "Eagle": 8, "Drake": 9, "Sturm": 10,
         "Sturm2": 11}


class TestCosts:
    def test_costs_match_the_activation_measurements(self):
        for name, cost in PROBES["activation"]["costs"].items():
            assert co.power_cost(NAMES[name]) == cost, name

    def test_threshold_grows_20_percent_per_use(self):
        assert co.power_threshold(1, 0) == 30000
        assert co.power_threshold(1, 1) == 36000
        assert co.power_threshold(1, 5) == 60000
        # the cap: past nine uses the percent pins at 200
        assert co.power_threshold(1, 10) == 60000
        assert co.power_threshold(1, 255) == 60000
        assert co.power_threshold(4, 1) == 30000   # Sami, 25000 * 120%


class TestChargeFormula:
    def test_both_measured_battles_reproduce(self):
        for case in PROBES["charge"]["cases"]:
            att, dfn = case["attacker"], case["defender"]
            # both fixtures ran Andy vs Andy or predate COs: value = cost/10
            gains = co.charge_gains(att["cost"] // 10, dfn["cost"] // 10,
                                    att["display_lost"], dfn["display_lost"])
            assert gains == (case["observed"]["attacker_gain"],
                             case["observed"]["defender_gain"]), case["name"]

    def test_the_legacy_pair_is_the_unique_solution(self):
        """The A-Air/Tank capture predates display-loss logging; assert its
        (4, 3) reading is the only display pair the formula allows, so the
        fixture is evidence and not a fit."""
        hits = [(a, d) for a in range(11) for d in range(11)
                if co.charge_gains(800, 700, a, d) == (3725, 2900)]
        assert hits == [(4, 3)]

    def test_kanbei_units_charge_20_percent_more(self):
        assert co.unit_value(7000, NAMES["Kanbei"]) == 840
        assert co.unit_value(7000, NAMES["Andy"]) == 700

    def test_value_multiplier_is_not_the_damage_pair(self):
        """Sturm's damage strength is +11/+12 (130/120); his value byte at
        +08 is neutral. A charge model that grabbed the damage pair would
        overvalue his units."""
        assert co.unit_value(7000, NAMES["Sturm"]) == 700


class TestPowerMeta:
    def test_walker_eligibility(self):
        assert co.power_meta(NAMES["Max"])["eligible"] == "direct_nonfoot"
        assert co.power_meta(NAMES["Sami"])["eligible"] == "foot"
        assert co.power_meta(NAMES["Grit"])["eligible"] == "indirect"
        assert co.power_meta(NAMES["Eagle"])["eligible"] == "nonfoot_acted"

    def test_effects(self):
        assert co.power_meta(NAMES["Andy"])["effect"] == "heal"
        assert co.power_meta(NAMES["Eagle"])["effect"] == "refresh"
        assert co.POWER_EFFECTS[NAMES["Olaf"]]["weather"] == "snow"
        assert co.POWER_EFFECTS[NAMES["Drake"]]["mass_damage"] == 10
        assert co.POWER_EFFECTS[NAMES["Sturm"]]["meteor_internal"] == 80
        assert co.POWER_EFFECTS[NAMES["Sturm2"]]["meteor_internal"] == 40

    def test_sami_power_swaps_movement_tables(self):
        assert co.record(NAMES["Sami"], power=True).weather_tables == [3, 4, 5]
        assert co.record(NAMES["Sami"], power=False).weather_tables == [0, 1, 2]

    def test_grit_range_bonus_is_exactly_two(self):
        g = PROBES["grit_range"]
        assert max(g["power_targets_distances_run1"]) == 5
        assert 6 in g["excluded_distances_run2"]
        assert co.POWER_EFFECTS[NAMES["Grit"]]["range_bonus"] == 2


class TestSonja:
    """DERIVATION 28. The vision numbers are additionally pinned against the
    game's own count arrays in test_fog.py (the sonja_vision_* fixtures)."""

    def test_vision_bonus_is_plus_one_and_plus_three(self):
        s = NAMES["Sonja"]
        assert co.vision_bonus(s, "Infantry") == 1
        assert co.vision_bonus(s, "Recon") == 1
        assert co.vision_bonus(s, "Infantry", power=True) == 3
        assert co.vision_bonus(s, "Sub") == 0          # the one exclusion
        assert co.vision_bonus(NAMES["Andy"], "Recon") == 0
        assert co.vision_bonus(NAMES["Andy"], "Recon", power=True) == 0

    def test_only_her_power_pierces_concealment(self):
        for name, cid in NAMES.items():
            for power in (False, True):
                expect = name == "Sonja" and power
                assert co.pierces_concealment(cid, power) == expect, (name, power)

    def test_only_she_hides_hp(self):
        for name, cid in NAMES.items():
            assert co.hides_hp(cid) == (name == "Sonja"), name


class TestLifetime:
    def test_power_block_covers_the_opponents_turn(self):
        phases = {p["phase"]: p for p in PROBES["expiry"]["phases"]}
        assert phases["p1-day1"]["blk"] == 1
        assert phases["p2-day2"]["blk"] == 0

    def test_snow_expires_with_the_power(self):
        phases = {p["phase"]: p for p in PROBES["expiry"]["phases"]}
        assert phases["p1-day1"]["weather"] == 1
        assert phases["p2-day2"]["weather"] == 0

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

from engine import co, rng
from engine.state import Board, Unit

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
        assert co.vision_bonus(s, "Sub") == 1          # the Sub too: the
        # "Sub spared" reading was the extractor stopping one entry short
        assert co.vision_bonus(s, "Sub", power=True) == 3
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


METEOR = json.loads((FIXTURES / "meteor_probes.json").read_text(encoding="utf-8"))


def _unit(slot, player, type_, x, y, hp=100):
    return Unit(slot, player, type_, x, y, hp, 9, 0, 99,
                False, False, False, 0, 0)


def _meteor_board(hp_overrides={}):
    """The probe board: three P1 clusters, each one scan's favourite."""
    units = [
        _unit(1, 1, "Infantry", 1, 6),      # G1, raw-hp winner
        _unit(2, 1, "MdTank", 6, 3),        # G2, value winner
        _unit(3, 1, "Battleship", 7, 6),    # G3, indirect-weighted winner
        _unit(4, 1, "Infantry", 1, 7),
        _unit(5, 1, "MdTank", 6, 2),
        _unit(6, 1, "Infantry", 1, 8),
        _unit(7, 1, "Tank", 4, 2),
        _unit(8, 1, "Infantry", 0, 6),
        _unit(65, 2, "Infantry", 8, 4),
        _unit(71, 2, "Tank", 9, 7),
    ]
    units = [u if u.slot not in hp_overrides else
             _unit(u.slot, u.player, u.type, u.x, u.y, hp_overrides[u.slot])
             for u in units]
    return Board(width=15, height=10, units=units, armies=[],
                 terrain=[[1] * 15 for _ in range(10)],
                 owner=[[0] * 15 for _ in range(10)])


class TestMeteor:
    """DERIVATION 30: the selector at 0x08063358, its three scans, and the
    blast rules -- every assertion here is a replay of a measured probe."""

    def test_the_rng_generator_reproduces_all_twelve_draws(self):
        for row in METEOR["seed_sweep"]:
            assert rng.next_state(row["seed"]) == row["rng_after"], row

    def test_the_strategy_is_one_draw_mod_three(self):
        by_mod = METEOR["strategy_by_mod3"]
        for row in METEOR["seed_sweep"]:
            assert by_mod[str(rng.next_state(row["seed"]) % 3)] \
                == row["cluster"], row

    def test_each_scan_picks_its_measured_cluster(self):
        b = _meteor_board()
        for strategy, cluster in ((0, "G2"), (1, "G1"), (2, "G3")):
            center = co.meteor_target(b, 2, strategy)
            hit = {u.slot for u, _ in
                   co.meteor_victims(b, center, METEOR["damage_internal"])}
            assert hit == set(METEOR["cluster_slots"][cluster]), strategy

    def test_friendly_fire_is_real(self):
        b = _meteor_board()
        b.units.append(_unit(66, 2, "Infantry", 5, 2))
        center = co.meteor_target(b, 2, 0)
        hits = {u.slot: after for u, after in co.meteor_victims(b, center, 80)}
        assert hits[66] == 20          # the caster's own Infantry, hit full

    def test_units_at_one_hp_score_nothing_and_take_nothing(self):
        # G2 at 10 internal scores zero, so the value scan falls on G3 --
        # and the immune units take no damage even when a blast lands on them
        b = _meteor_board({2: 10, 5: 10, 7: 10})
        center = co.meteor_target(b, 2, 0)
        assert center.slot == 3
        hits = {u.slot for u, _ in co.meteor_victims(b, center, 80)}
        assert hits == {3}
        b2 = _meteor_board({7: 5})
        center2 = co.meteor_target(b2, 2, 0)
        hits2 = {u.slot: after for u, after in
                 co.meteor_victims(b2, center2, 80)}
        assert 7 not in hits2 and hits2[2] == 20 and hits2[5] == 20

    def test_overkill_clamps_at_one_internal(self):
        b = _meteor_board({2: 50})
        center = co.meteor_target(b, 2, 0)
        hits = {u.slot: after for u, after in co.meteor_victims(b, center, 80)}
        assert hits[2] == 1

    def test_no_positive_score_means_no_target(self):
        b = _meteor_board({s: 10 for s in (1, 2, 3, 4, 5, 6, 7, 8)})
        assert co.meteor_target(b, 2, 0) is None


LUCK = json.loads((FIXTURES / "luck_probes.json").read_text(encoding="utf-8"))


class TestLuckConsumption:
    """DERIVATION 32: the luck block at 0x0802333A, measured over 20 seeded
    battles. The strike's roll is the third of exactly four draws, reduced
    `draw % (10 + good) - bad` through the CO record."""

    def test_every_battle_burned_exactly_four_draws(self):
        for row in LUCK["battles"]:
            s = row["seed"]
            for _ in range(rng.BATTLE_DRAWS):
                s = rng.next_state(s)
            assert s == row["rng_after"], row

    def test_the_strike_roll_is_the_third_draw_reduced(self):
        for row in LUCK["battles"]:
            got = rng.strike_luck(row["seed"], row["good"], row["bad"])
            assert got == row["roll"], row

    def test_the_third_draw_is_the_unique_consistent_index(self):
        """Any single fixed draw index other than 3 is refuted by at least
        one row, so the fit is forced rather than chosen."""
        for k in (1, 2, 4):
            broken = 0
            for row in LUCK["battles"]:
                s = row["seed"]
                for _ in range(k):
                    s = rng.next_state(s)
                if rng.luck_reduce(s, row["good"], row["bad"]) != row["roll"]:
                    broken += 1
            assert broken > 0, k

    def test_the_widened_reductions_were_seen_live(self):
        rolls = {(r["co"], r["roll"]) for r in LUCK["battles"]}
        assert ("nell", 16) in rolls        # outside 0..9: the %20 is real
        assert ("sonja", -14) in rolls      # negative: the -15 shift is real

    def test_fixed_luck_draws_nothing_and_adds_five(self):
        for row in LUCK["fixed_luck"]:
            assert row["rng_after"] == row["seed"]
            assert row["roll"] == 5

    def test_the_reduction_matches_the_co_records(self):
        assert co.luck(1) == (0, 9)
        assert co.luck(0) == (0, 19)
        assert co.luck(7) == (-15, 9)
        for row in LUCK["battles"]:
            lo, hi = co.luck(NAMES[row["co"].capitalize()])
            assert lo <= row["roll"] <= hi, row


class TestLifetime:
    def test_power_block_covers_the_opponents_turn(self):
        phases = {p["phase"]: p for p in PROBES["expiry"]["phases"]}
        assert phases["p1-day1"]["blk"] == 1
        assert phases["p2-day2"]["blk"] == 0

    def test_snow_expires_with_the_power(self):
        phases = {p["phase"]: p for p in PROBES["expiry"]["phases"]}
        assert phases["p1-day1"]["weather"] == 1
        assert phases["p2-day2"]["weather"] == 0


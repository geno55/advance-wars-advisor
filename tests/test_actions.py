"""Regression tests for action enumeration.

Same discipline as test_threat.py: boards are built by hand so each test
isolates one rule, and the point being proved throughout is that the SAME
function offers different actions to different units purely from table data --
who may capture is `unit_class`, who may shoot is `armed` and the range
fields, who may load is the cargo mask. A unit-type branch added to actions.py
to make one of these pass should break the others.

The damage numbers are not re-derived here. What this module adds is WIRING --
the right terrain stars on each side, the right HP, the right CO, the counter
taking the ENDING tile's cover -- so the resolution tests assert equality with
engine/damage.py called directly on the same inputs, and the formula itself
stays tested where it lives.
"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import actions                                                # noqa: E402
import damage                                                 # noqa: E402
import pathing                                                # noqa: E402
from state import Army, Board, Unit                           # noqa: E402

PLAIN, MOUNTAIN, WOOD, ROAD, SEA, CITY, SHOAL = 1, 3, 4, 5, 7, 6, 13


def board(rows, units=(), owner=None, armies=()):
    return Board(width=len(rows[0]), height=len(rows), units=list(units),
                 armies=list(armies),
                 terrain=[list(r) for r in rows],
                 owner=owner or [[0] * len(rows[0]) for _ in rows],
                 weather_index=0)


def unit(utype, x, y, player=1, slot=1, hp=100, fuel=99, ammo=9, acted=False,
         loaded=False, cargo=0, capture=0):
    return Unit(slot=slot, player=player, type=utype, x=x, y=y, hp=hp,
                ammo=ammo, capture=capture, fuel=fuel, acted=acted,
                carrying=bool(cargo), loaded=loaded, state=0, cargo=cargo)


def of_kind(acts, kind):
    return [a for a in acts if a.kind == kind]


class TestWait(unittest.TestCase):
    def test_every_destination_is_a_wait_and_nothing_else_is(self):
        """WAIT is destinations() verbatim -- the set matched against the
        game's own flood fill -- minus nothing and plus nothing."""
        b = board([[PLAIN] * 8], [unit("Infantry", 2, 0)])
        acts = actions.actions_for(b, b.units[0], warnings=[])
        self.assertEqual({a.tile for a in of_kind(acts, "wait")},
                         set(pathing.destinations(b, b.units[0])))

    def test_staying_put_is_a_legal_move_at_cost_zero(self):
        b = board([[PLAIN] * 4], [unit("Infantry", 1, 0)])
        acts = actions.actions_for(b, b.units[0], warnings=[])
        here = [a for a in of_kind(acts, "wait") if a.tile == (1, 0)]
        self.assertEqual(len(here), 1)
        self.assertEqual(here[0].move_cost, 0)
        self.assertEqual(here[0].fuel_after, 99)

    def test_an_acted_or_loaded_unit_has_no_actions(self):
        b = board([[PLAIN] * 4],
                  [unit("Infantry", 0, 0, slot=1, acted=True),
                   unit("APC", 2, 0, slot=2, cargo=3),
                   unit("Mech", 2, 0, slot=3, loaded=True)])
        self.assertEqual(actions.actions_for(b, b.units[0], warnings=[]), [])
        self.assertEqual(actions.actions_for(b, b.units[2], warnings=[]), [])


class TestAttackGeometry(unittest.TestCase):
    """The same three ROM fields that drive threat.py, pointed at your own
    units: armed, can_move_and_fire, min_range/max_range."""

    def test_direct_unit_attacks_from_a_tile_it_moved_to(self):
        """Tank at x=0, Infantry at x=5 on one row: the enemy blocks passage,
        so the only adjacent tile in reach is (4,0)."""
        b = board([[PLAIN] * 8],
                  [unit("Tank", 0, 0, slot=1),
                   unit("Infantry", 5, 0, player=2, slot=2)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        self.assertEqual([(a.tile, a.target.slot) for a in atts], [((4, 0), 2)])

    def test_indirect_attacks_only_from_where_it_stands(self):
        """Artillery, range 2..3: the Tank two tiles out is an option, the
        Infantry adjacent is inside min_range, and no attack is offered from
        any tile the Artillery could drive to."""
        b = board([[PLAIN] * 12],
                  [unit("Artillery", 5, 0, slot=1),
                   unit("Tank", 3, 0, player=2, slot=2),
                   unit("Infantry", 4, 0, player=2, slot=3)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        self.assertEqual([(a.tile, a.target.slot, a.move_cost) for a in atts],
                         [((5, 0), 2, 0)])

    def test_unarmed_unit_attacks_nothing(self):
        b = board([[PLAIN] * 4],
                  [unit("APC", 0, 0, slot=1),
                   unit("Infantry", 2, 0, player=2, slot=2)])
        acts = actions.actions_for(b, b.units[0], warnings=[])
        self.assertEqual(of_kind(acts, "attack"), [])
        self.assertTrue(of_kind(acts, "wait"))

    def test_a_matchup_the_damage_table_forbids_is_not_offered(self):
        """AntiAir beside a Lander: geometry says yes, the matrix says no,
        and the matrix wins -- same rule as threat.py, opposite direction."""
        b = board([[SHOAL, SEA]],
                  [unit("AntiAir", 0, 0, slot=1),
                   unit("Lander", 1, 0, player=2, slot=2)])
        acts = actions.actions_for(b, b.units[0], warnings=[])
        self.assertEqual(of_kind(acts, "attack"), [])


class TestAttackResolution(unittest.TestCase):
    """Wiring, not arithmetic: the right stars, HP, ammo and CO must reach
    damage.py. The formula's own numbers are tested in test_damage.py."""

    def test_strike_and_counter_match_damage_called_directly(self):
        b = board([[PLAIN, PLAIN, WOOD]],
                  [unit("Tank", 0, 0, slot=1, hp=80, ammo=5),
                   unit("Mech", 2, 0, player=2, slot=2, hp=60, ammo=3)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        self.assertEqual(len(atts), 1)
        a = atts[0]
        atk = damage.Attack(attacker="Tank", defender="Mech",
                            attacker_hp=80, defender_hp=60,
                            terrain_stars=2, ammo=5)   # Mech stands on Wood
        self.assertEqual(a.strike, damage.resolve(atk))
        self.assertEqual(a.counter, damage.counterattack(
            atk, attacker_stars=1, defender_ammo=3))   # Tank fires from Plain
        self.assertEqual(a.hp_after, a.counter.min_remaining_hp)

    def test_counter_takes_the_cover_of_the_ending_tile(self):
        """Same target, two firing tiles: the counter coming back at a Tank on
        Wood must be smaller than at a Tank on Plain. If the counter read the
        tile the unit STARTED on, these would be equal."""
        rows = [[PLAIN, PLAIN, PLAIN],
                [PLAIN, WOOD, PLAIN]]
        b = board(rows, [unit("Tank", 0, 0, slot=1),
                         unit("Mech", 1, 0, player=2, slot=2)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        by_tile = {a.tile: a for a in atts if a.target.slot == 2}
        self.assertIn((1, 1), by_tile)                 # the wood tile
        self.assertIn((2, 0), by_tile)                 # a plain tile
        self.assertLess(by_tile[(1, 1)].counter.max_damage,
                        by_tile[(2, 0)].counter.max_damage)
        # The strike itself must NOT move -- it reads the defender's tile.
        self.assertEqual(by_tile[(1, 1)].strike, by_tile[(2, 0)].strike)

    def test_an_indirect_strike_draws_no_counter(self):
        """fights_at_contact is false for a 2..3 ring, so the counter is None
        without Artillery being named anywhere."""
        b = board([[PLAIN] * 6],
                  [unit("Artillery", 0, 0, slot=1),
                   unit("Tank", 2, 0, player=2, slot=2)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        self.assertEqual(len(atts), 1)
        self.assertIsNone(atts[0].counter)
        self.assertEqual(atts[0].hp_after, 100)

    def test_co_ids_reach_the_quote(self):
        """A board that carries CO identity must produce a different strike
        than the neutral fallback -- wired through Attack.between, which is
        the path that also fills the universal pair and the per-CO luck."""
        names = json.loads((ROOT / "data" / "aw1_co.json")
                           .read_text(encoding="utf-8"))["confirmed"]
        max_id = int(next(k for k, v in names.items() if v == "Max"))
        andy_id = int(next(k for k, v in names.items() if v == "Andy"))
        b = board([[PLAIN] * 4],
                  [unit("Tank", 0, 0, slot=1),
                   unit("Infantry", 2, 0, player=2, slot=2)],
                  armies=[Army(1, 0, 0, co_id=max_id),
                          Army(2, 0, 0, co_id=andy_id)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        expected = damage.resolve(damage.Attack.between(
            "Tank", "Infantry", max_id, andy_id, terrain_stars=1, ammo=9))
        self.assertEqual(atts[0].strike, expected)

    def test_an_unknown_co_warns_once_and_quotes_neutral(self):
        b = board([[PLAIN] * 4],
                  [unit("Tank", 0, 0, slot=1),
                   unit("Infantry", 2, 0, player=2, slot=2)])
        warnings = []
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=warnings),
                       "attack")
        expected = damage.resolve(damage.Attack(
            attacker="Tank", defender="Infantry", terrain_stars=1, ammo=9))
        self.assertEqual(atts[0].strike, expected)
        self.assertEqual(
            len([w for w in warnings if "attack quotes assume neutral" in w]), 1)


class TestPostTradeExposure(unittest.TestCase):
    def test_a_guaranteed_kill_removes_the_target_from_next_turn(self):
        """One enemy, too weak to survive any roll: the exposure after the
        attack must be empty, because the only attacker is dead on every
        branch of the worst case."""
        b = board([[PLAIN] * 4],
                  [unit("Tank", 0, 0, slot=1),
                   unit("Infantry", 2, 0, player=2, slot=2, hp=5)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        a = atts[0]
        self.assertTrue(a.strike.guaranteed_kill)
        self.assertIsNone(a.counter)          # no survivor on any roll
        self.assertEqual(a.exposure.worst_damage, 0)

    def test_a_survivor_still_threatens_next_turn(self):
        """Tank into a full Md Tank: the strike cannot kill, so the exposure
        must contain the weakened Md Tank hitting back next turn."""
        b = board([[PLAIN] * 4],
                  [unit("Tank", 0, 0, slot=1),
                   unit("MdTank", 2, 0, player=2, slot=2)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        a = atts[0]
        self.assertFalse(a.strike.possible_kill)
        self.assertGreater(a.exposure.worst_damage, 0)

    def test_worst_case_death_reports_no_exposure(self):
        """A 1-bar Mech poking a full Md Tank eats a counter that kills on
        every roll. Projecting next turn for a unit that may not exist would
        be an invented number, so exposure is None and the counter carries
        the verdict."""
        b = board([[PLAIN] * 3],
                  [unit("Mech", 0, 0, slot=1, hp=10),
                   unit("MdTank", 1, 0, player=2, slot=2)])
        atts = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "attack")
        a = atts[0]
        self.assertTrue(a.counter.guaranteed_kill)
        self.assertIsNone(a.exposure)
        self.assertEqual(a.hp_after, 0)


class TestCapture(unittest.TestCase):
    def test_a_foot_unit_may_capture_and_treads_may_not(self):
        """unit_class is the ROM field that decides it. Same board, same
        neutral city, two units -- only the foot one is offered the action."""
        rows = [[PLAIN, CITY]]
        foot = board(rows, [unit("Infantry", 0, 0)])
        treads = board(rows, [unit("Tank", 0, 0)])
        self.assertTrue(of_kind(actions.actions_for(foot, foot.units[0],
                                                    warnings=[]), "capture"))
        self.assertEqual(of_kind(actions.actions_for(treads, treads.units[0],
                                                     warnings=[]), "capture"), [])

    def test_progress_counts_displayed_bars_toward_twenty(self):
        """Full health is 10 bars, so a fresh capture reads 10/20 after this
        turn and finishes on the second."""
        b = board([[PLAIN, CITY]], [unit("Infantry", 0, 0)])
        cap = of_kind(actions.actions_for(b, b.units[0], warnings=[]),
                      "capture")[0]
        self.assertEqual((cap.tile, cap.progress_after, cap.captures_now,
                          cap.capture_turns_left), ((1, 0), 10, False, 2))

    def test_a_damaged_unit_captures_slower(self):
        b = board([[PLAIN, CITY]], [unit("Infantry", 0, 0, hp=45)])
        cap = of_kind(actions.actions_for(b, b.units[0], warnings=[]),
                      "capture")[0]
        self.assertEqual((cap.progress_after, cap.capture_turns_left), (5, 4))

    def test_continuing_keeps_progress_and_moving_resets_it(self):
        """The unit stands mid-capture at 15/20. Staying finishes this turn;
        walking to the next city starts over from zero."""
        b = board([[CITY, CITY]], [unit("Infantry", 0, 0, capture=15)])
        caps = {c.tile: c for c in of_kind(
            actions.actions_for(b, b.units[0], warnings=[]), "capture")}
        self.assertTrue(caps[(0, 0)].captures_now)
        self.assertEqual(caps[(0, 0)].progress_after, 20)
        self.assertFalse(caps[(1, 0)].captures_now)
        self.assertEqual(caps[(1, 0)].progress_after, 10)

    def test_your_own_property_is_not_a_capture_target(self):
        b = board([[PLAIN, CITY]], [unit("Infantry", 0, 0)],
                  owner=[[0, 1]])
        self.assertEqual(of_kind(actions.actions_for(b, b.units[0],
                                                     warnings=[]), "capture"), [])


class TestCaptureRateIsTheRomArithmetic(unittest.TestCase):
    """The increment is read off the ROM at 0x08026180-0x080261FC:

        gain = bars + (bars >> (8 - shift))     bars  = ceil(hp/10), BIOS Div
                                                shift = CO record +0x0D

    Eleven records carry shift 0, where bars >> 8 is zero for any board the
    game can reach, so the neutral rate IS the bar count. Sami carries 7 in
    both blocks: bars >> 1, the documented 1.5x, truncated.
    """

    def _ids(self):
        names = json.loads((ROOT / "data" / "aw1_co.json")
                           .read_text(encoding="utf-8"))["confirmed"]
        return {v: int(k) for k, v in names.items()}

    def test_the_shift_is_sami_alone(self):
        import co
        ids = self._ids()
        self.assertEqual(co.capture_shift(ids["Sami"]), 7)
        self.assertEqual(co.capture_shift(ids["Sami"], power=True), 7)
        others = [co.capture_shift(i) for i in range(12) if i != ids["Sami"]]
        self.assertEqual(others, [0] * 11)

    def _capture(self, co_id, hp=100, capture=0):
        b = board([[PLAIN, CITY]],
                  [unit("Infantry", 0, 0, hp=hp, capture=capture)],
                  armies=[Army(1, 0, 0, co_id=co_id)])
        caps = of_kind(actions.actions_for(b, b.units[0], warnings=[]),
                       "capture")
        return next(c for c in caps if c.tile == (1, 0))

    def test_sami_captures_half_again_as_fast(self):
        ids = self._ids()
        self.assertEqual(self._capture(ids["Andy"]).progress_after, 10)
        self.assertEqual(self._capture(ids["Sami"]).progress_after, 15)
        # 7 bars: 7 for anyone, 7 + 3 for Sami
        self.assertEqual(self._capture(ids["Andy"], hp=70).progress_after, 7)
        self.assertEqual(self._capture(ids["Sami"], hp=70).progress_after, 10)

    def test_the_bonus_can_change_the_turn_count(self):
        """A full-health capturer finishes in 2 turns either way -- 15+15 and
        10+10 both reach 20 -- so the bonus only shows below full health.
        At 7 bars Sami finishes in 2 where everyone else needs 3."""
        ids = self._ids()
        self.assertEqual(self._capture(ids["Andy"], hp=70).capture_turns_left, 3)
        self.assertEqual(self._capture(ids["Sami"], hp=70).capture_turns_left, 2)

    def test_progress_clamps_at_twenty(self):
        """cmp #0x13 / bls / movs #0x14 at 0x080262CA: 15 + 15 stores 20."""
        ids = self._ids()
        a = self._capture(ids["Sami"], hp=100)
        b2 = board([[CITY, PLAIN]],
                   [unit("Infantry", 0, 0, capture=15)],
                   armies=[Army(1, 0, 0, co_id=ids["Sami"])])
        cap = of_kind(actions.actions_for(b2, b2.units[0], warnings=[]),
                      "capture")[0]
        self.assertEqual(cap.progress_after, 20)
        self.assertTrue(cap.captures_now)

    def test_an_unknown_co_warns_and_uses_the_bar_count(self):
        b = board([[PLAIN, CITY]], [unit("Infantry", 0, 0, hp=70)])
        warnings = []
        caps = of_kind(actions.actions_for(b, b.units[0], warnings=warnings),
                       "capture")
        self.assertEqual(caps[0].progress_after, 7)
        self.assertTrue(any("capture rate assumes no CO bonus" in w
                            for w in warnings))


class TestLoad(unittest.TestCase):
    def test_loading_is_offered_and_scores_the_transport(self):
        """The exposure on a LOAD is the transport's, because that is what a
        shot would hit -- a passenger dies with its ride."""
        b = board([[PLAIN] * 3],
                  [unit("Infantry", 0, 0, slot=1),
                   unit("APC", 1, 0, slot=2),
                   unit("Tank", 2, 0, player=2, slot=3)])
        loads = of_kind(actions.actions_for(b, b.units[0], warnings=[]), "load")
        self.assertEqual([(l.tile, l.target.slot) for l in loads], [((1, 0), 2)])
        # The Tank can hit the APC where it sits, so riding is not free.
        self.assertGreater(loads[0].exposure.worst_damage, 0)

    def test_a_full_transport_is_not_offered(self):
        b = board([[PLAIN] * 3],
                  [unit("Infantry", 0, 0, slot=1),
                   unit("APC", 1, 0, slot=2, cargo=3),
                   unit("Mech", 1, 0, slot=3, loaded=True)])
        self.assertEqual(of_kind(actions.actions_for(b, b.units[0],
                                                     warnings=[]), "load"), [])


class TestFog(unittest.TestCase):
    def test_a_unit_you_cannot_see_is_not_a_target(self):
        """Tank vision 3, enemy at distance 6: in the clear it is attackable,
        under fog it is not offered -- attacking into the dark on knowledge
        the player does not have is the advisor cheating."""
        b = board([[PLAIN] * 10],
                  [unit("Tank", 0, 0, slot=1),
                   unit("Infantry", 6, 0, player=2, slot=2)])
        clear = of_kind(actions.actions_for(b, b.units[0], fog=False,
                                            warnings=[]), "attack")
        fogged = of_kind(actions.actions_for(b, b.units[0], fog=True,
                                             warnings=[]), "attack")
        self.assertEqual(len(clear), 1)
        self.assertEqual(fogged, [])

    def test_a_visible_enemy_is_still_offered_under_fog(self):
        b = board([[PLAIN] * 6],
                  [unit("Tank", 0, 0, slot=1),
                   unit("Infantry", 2, 0, player=2, slot=2)])
        fogged = of_kind(actions.actions_for(b, b.units[0], fog=True,
                                             warnings=[]), "attack")
        self.assertEqual(len(fogged), 1)


class TestNoUnitTypeBranches(unittest.TestCase):
    def test_actions_names_no_unit_type(self):
        """The design constraint, enforced -- the same test pathing and threat
        carry. Who may capture, load or shoot must come from unit_class, the
        cargo mask and the range fields, never from a name."""
        src = (ROOT / "engine" / "actions.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        code = code.split('"""')[0] + '"""'.join(code.split('"""')[2::2])
        names = json.loads((ROOT / "data" / "aw1_unit_stats.json")
                           .read_text(encoding="utf-8"))["units"]
        for name in names:
            self.assertNotIn(f'"{name}"', code, f"{name} is named in actions.py")
            self.assertNotIn(f"'{name}'", code, f"{name} is named in actions.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Turn-start income, checked against the game's own figure on every dump.

`Army.income` is army + 0x08, the running total the property walker leaves
behind, so every parked state is a free row of evidence for the model: count
the owned paying tiles, multiply by the rate, and the two numbers must be the
same. Eleven fixtures across two maps and two rate settings do that below --
including the 9500 rows that made the old "1000 x owned" reading look wrong,
which it was, because the rate is a setting and not the terrain constant.

The refutation tests pin the SHAPE the same way the supply tests do: each
encodes a reading a plausible implementation might have shipped -- HQ excluded,
neutral property counted, the terrain struct's 1000 used as the rate, an
enemy's property counted as ours -- and shows a measured board disagreeing.

The ROM tests close the two claims that no fixture can reach: the jump table
that decides WHICH terrains pay, and the CO-table terms the payer adds, which
are zero for all twelve COs in both stat blocks and therefore dead.
"""
import glob
import json
import pathlib
import struct
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

import economy                                                # noqa: E402
from state import Army, Board, load                           # noqa: E402

FIXDIR = pathlib.Path(__file__).parent / "fixtures"
ROM = ROOT.parent / "Advance Wars (USA) (Rev 1).gba"

PLAIN, CITY, SEA, HQ, AIRPORT, PORT, BASE = 1, 6, 7, 8, 10, 11, 14


def states():
    """Every fixture that is a full board dump, as (name, Board)."""
    for f in sorted(glob.glob(str(FIXDIR / "*.json"))):
        raw = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "armies" not in raw:
            continue
        if "terrain" not in raw or "width" not in raw:
            continue
        yield pathlib.Path(f).name, load(f)


def board(terrain, owner, armies, **kw):
    return Board(width=len(terrain[0]), height=len(terrain), units=[],
                 armies=armies, terrain=terrain, owner=owner, **kw)


class AgainstTheGame(unittest.TestCase):
    """The model reproduces army + 0x08 on every parked state."""

    def test_every_fixture_agrees(self):
        seen = 0
        for name, b in states():
            complaints = economy.check(b)
            self.assertEqual(complaints, [], f"{name}: {complaints}")
            seen += 1
        self.assertGreater(seen, 8, "expected the full-board fixture set")

    def test_both_rate_settings_are_present(self):
        """The evidence spans two rates, or it proves nothing about the rate."""
        rates = {economy.funds_rate(b)[0] for _, b in states()
                 if any(a.income for a in b.armies)}
        self.assertIn(1000, rates)
        self.assertIn(9500, rates)

    def test_property_counts_span_zero_to_many(self):
        counts = set()
        for _, b in states():
            for a in b.armies:
                counts.add(economy.properties(b, a.player))
        self.assertTrue({0, 1}.issubset(counts))
        self.assertGreaterEqual(max(counts), 7)

    def test_zero_properties_pays_nothing(self):
        for name, b in states():
            for a in b.armies:
                if economy.properties(b, a.player) == 0:
                    self.assertEqual(a.income, 0, name)


class TheRate(unittest.TestCase):

    def test_the_income_field_wins_over_the_dumped_cell(self):
        """DERIVATION 43: a cell written to 200 was paid at 9500, so the
        game's own running total is the witness and the cell the fallback."""
        b = board([[HQ]], [[1]], [Army(1, 0, 9500)], funds_per_property=200)
        self.assertEqual(economy.funds_rate(b), (9500, "derived"))

    def test_the_dumped_cell_is_used_when_nobody_owns_property(self):
        b = board([[HQ]], [[0]], [Army(1, 0, 0)], funds_per_property=9500)
        self.assertEqual(economy.funds_rate(b), (9500, "dump"))

    def test_derived_from_income_when_the_dump_predates_the_field(self):
        """A campaign dump with no rate cell still gets the right number."""
        b = board([[HQ, CITY]], [[1, 1]], [Army(1, 0, 2000)])
        self.assertEqual(economy.funds_rate(b), (1000, "derived"))

    def test_derivation_survives_a_non_default_rate(self):
        b = board([[HQ, CITY]], [[1, 1]], [Army(1, 0, 19000)])
        self.assertEqual(economy.funds_rate(b), (9500, "derived"))

    def test_default_is_labelled_as_a_guess(self):
        b = board([[PLAIN]], [[0]], [Army(1, 0, 0)])
        rate, source = economy.funds_rate(b)
        self.assertEqual((rate, source), (economy.DEFAULT_RATE, "default"))

    def test_a_propertyless_army_does_not_poison_the_derivation(self):
        b = board([[HQ, CITY]], [[2, 2]],
                  [Army(1, 0, 0), Army(2, 0, 19000)])
        self.assertEqual(economy.funds_rate(b), (9500, "derived"))


class WhichTilesPay(unittest.TestCase):

    def test_hq_pays(self):
        b = board([[HQ]], [[1]], [Army(1, 0, 1000)], funds_per_property=1000)
        self.assertEqual(economy.income(b, 1).amount, 1000)

    def test_every_property_type_pays_the_same(self):
        row = [CITY, HQ, AIRPORT, PORT, BASE]
        b = board([row], [[1] * 5], [Army(1, 0, 5000)],
                  funds_per_property=1000)
        self.assertEqual(economy.income(b, 1).amount, 5000)

    def test_plain_and_sea_pay_nothing(self):
        b = board([[PLAIN, SEA]], [[1, 1]], [Army(1, 0, 0)],
                  funds_per_property=1000)
        self.assertEqual(economy.income(b, 1).amount, 0)

    def test_neutral_property_pays_nobody(self):
        b = board([[CITY]], [[0]], [Army(1, 0, 0)], funds_per_property=1000)
        self.assertEqual(economy.income(b, 1).amount, 0)

    def test_an_enemy_property_is_not_ours(self):
        b = board([[CITY, CITY]], [[1, 2]],
                  [Army(1, 0, 1000), Army(2, 0, 1000)],
                  funds_per_property=1000)
        self.assertEqual(economy.income(b, 1).amount, 1000)
        self.assertEqual(economy.income(b, 2).amount, 1000)

    def test_property_tiles_matches_the_count(self):
        for _, b in states():
            for a in b.armies:
                self.assertEqual(len(economy.property_tiles(b, a.player)),
                                 economy.properties(b, a.player))


class Refutations(unittest.TestCase):
    """Each names a reading that a plausible implementation might have had."""

    def test_hq_excluded_would_miss_a_measured_board(self):
        """The 9500 fixtures are one HQ and nothing else -- excluding it
        would predict zero income where the game paid 9500."""
        hit = 0
        for name, b in states():
            for a in b.armies:
                tiles = economy.property_tiles(b, a.player)
                if a.income and all(b.terrain[y][x] == HQ for x, y in tiles):
                    hit += 1
                    self.assertNotEqual(a.income, 0, name)
        self.assertGreater(hit, 0, "expected HQ-only armies in the fixtures")

    def test_terrain_struct_income_is_not_the_rate(self):
        """1000 x owned is the reading the 9500 rows refute."""
        terrain = json.loads((ROOT / "data" / "aw1_terrain.json")
                             .read_text(encoding="utf-8"))
        blob = json.dumps(terrain)
        self.assertIn("1000", blob)
        bad = 0
        for _, b in states():
            for a in b.armies:
                if a.income != 1000 * economy.properties(b, a.player):
                    bad += 1
        self.assertGreater(bad, 0,
                           "no fixture refutes the terrain-constant reading")

    def test_counting_neutral_properties_would_overpay(self):
        for name, b in states():
            for a in b.armies:
                naive = sum(1 for y in range(b.height) for x in range(b.width)
                            if b.terrain[y][x] in economy.FUNDING_TERRAIN)
                if naive != economy.properties(b, a.player):
                    rate, _ = economy.funds_rate(b)
                    self.assertNotEqual(a.income, naive * rate, name)


class FromTheRom(unittest.TestCase):
    """The two claims no fixture can reach."""

    def setUp(self):
        if not ROM.exists():
            self.skipTest("ROM not present")
        self.rom = ROM.read_bytes()

    def test_the_jump_table_names_exactly_these_terrains(self):
        """0x08025138 switches ids 6..18 onto two bodies; read which."""
        PAYS, ZERO = 0x08025184, 0x08025190
        pays = set()
        for i in range(13):
            v = struct.unpack_from("<I", self.rom, 0x025150 + 4 * i)[0]
            self.assertIn(v, (PAYS, ZERO), f"unexpected body for id {6 + i}")
            if v == PAYS:
                pays.add(6 + i)
        self.assertEqual(
            pays, economy.FUNDING_TERRAIN | economy.FUNDING_TERRAIN_UNUSED)

    def test_the_paying_body_reads_the_settings_cell(self):
        """ldr r0,=0x03004310 ; ldr r0,[r0,#0x28] -- the rate, not a table."""
        # ldr r0, [pc, #4] at 0x08025184 -> ((pc + 4) & ~3) + 4 = 0x0802518C
        lit = struct.unpack_from("<I", self.rom, 0x02518C)[0]
        self.assertEqual(lit, 0x03004310)
        self.assertEqual(struct.unpack_from("<H", self.rom, 0x025186)[0],
                         0x6A80)          # ldr r0, [r0, #0x28]
        self.assertEqual(economy.RATE_ADDR, 0x03004310 + 0x28)

    def test_the_co_income_terms_are_dead(self):
        terms = economy.co_income_terms(str(ROM))
        self.assertEqual(len(terms), economy.CO_COUNT * 2)
        for key, pair in terms.items():
            self.assertEqual(pair, (0, 0), f"CO {key} has a live income term")


if __name__ == "__main__":
    unittest.main()

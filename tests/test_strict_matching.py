"""Strict matching rules and the Loose toggle (stdlib unittest).

Regression: "Bm, 79 BPM" once picked drums at 80 BPM, a 155 BPM F#m bass and
a 79 BPM Bm piano. Strict mode must reject the first two and leave the bass
slot empty with a reason instead of picking a near miss.
"""
import shutil
import tempfile
import unittest

from samplelib import matching
from samplelib.store import Store
from test_matching import BL, DL, ML, mk


class StrictRulesTest(unittest.TestCase):
    def test_tempo_within_half_percent(self):
        self.assertTrue(matching.tempo_ok(79, 79.3)[0])        # 0.38%
        self.assertFalse(matching.tempo_ok(79, 80)[0])         # 1.27%
        self.assertTrue(matching.tempo_ok(79, 158.5)[0])       # double, 0.32%
        self.assertFalse(matching.tempo_ok(79, 155)[0])        # double, 1.9%
        self.assertTrue(matching.tempo_ok(140, 70.2)[0])       # half
        self.assertFalse(matching.tempo_ok(None, 120)[0])

    def test_key_same_or_relative_only(self):
        self.assertTrue(matching.key_ok("Bm", "Bm")[0])
        self.assertTrue(matching.key_ok("Bm", "D")[0])
        self.assertTrue(matching.key_ok("Am", "C")[0])
        self.assertFalse(matching.key_ok("Bm", "F#m")[0])      # Camelot neighbor
        self.assertFalse(matching.key_ok("Am", "Em")[0])       # Camelot neighbor
        self.assertFalse(matching.key_ok("A", "Am")[0])        # parallel, not relative
        self.assertFalse(matching.key_ok("Am", None)[0])

    def test_bars_divide_evenly(self):
        self.assertTrue(matching.bars_ok(4, 8))
        self.assertTrue(matching.bars_ok(2, 8))
        self.assertFalse(matching.bars_ok(3, 4))
        self.assertFalse(matching.bars_ok(4, 12))
        self.assertTrue(matching.bars_ok(None, 4))


class StrictStoreBase(unittest.TestCase):
    items = ()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.st = Store(self.d, "/x")
        self.st._set_inventory(list(self.items), [], persist=False)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def name(self, r, slot):
        v = r["slots"][slot]
        return v["row"]["name"][:-4] if v else None


class Bm79RegressionTest(StrictStoreBase):
    items = [
        mk("Drums 80", DL, "loop", 80),
        mk("Bass F#m 155", BL, "loop", 155, "F#m"),
        mk("Piano Bm 79", ML, "loop", 79, "Bm"),
    ]

    def test_bm_79_rejects_near_misses(self):
        for seed in range(20):
            r = matching.starter(self.st, key="Bm", bpm=79, seed=seed)
            self.assertEqual(self.name(r, "melody"), "Piano Bm 79")
            self.assertIsNone(r["slots"]["drums"], "80 BPM drums are 1.3% off 79")
            self.assertIsNone(r["slots"]["bass"], "155 BPM F#m bass is wrong tempo and key")
            self.assertIn("tempo", r["missing"]["drums"])
            self.assertIn("bass", r["missing"]["bass"])
            self.assertIn("Bm or D", r["missing"]["bass"])
            self.assertFalse(r["loose"])

    def test_loose_still_allows_old_behavior(self):
        r = matching.starter(self.st, key="Bm", bpm=79, seed=1, loose=True)
        self.assertEqual(self.name(r, "drums"), "Drums 80")
        self.assertTrue(r["loose"])

    def test_goes_well_with_is_strict_by_default(self):
        piano = self.items[2]["id"]
        self.assertEqual(matching.matches(self.st, piano), [])
        loose = [m["row"]["name"] for m in matching.matches(self.st, piano, loose=True)]
        self.assertIn("Drums 80.wav", loose)


class StrictPicksTest(StrictStoreBase):
    items = [
        mk("Drums 79.2", DL, "loop", 79.2, bars=4),
        mk("Drums 158", DL, "loop", 158, bars=8),
        mk("Drums 3 bar", DL, "loop", 79, bars=3),
        mk("Bass D 79", BL, "loop", 79, "D", bars=4),
        mk("Bass Bm 79", BL, "loop", 79, "Bm", bars=2),
        mk("Piano Bm 79", ML, "loop", 79, "Bm", bars=4),
    ]

    def test_every_pick_passes_strict_rules(self):
        allowed = {"drums": {"Drums 79.2", "Drums 158"}, "bass": {"Bass D 79", "Bass Bm 79"}}
        for seed in range(30):
            r = matching.starter(self.st, key="Bm", bpm=79, seed=seed)
            self.assertEqual(self.name(r, "melody"), "Piano Bm 79")
            self.assertIn(self.name(r, "drums"), allowed["drums"])
            self.assertIn(self.name(r, "bass"), allowed["bass"])
            self.assertEqual(r["missing"], {})

    def test_drums_ignore_key(self):
        piano = self.items[5]["id"]
        names = {m["row"]["name"] for m in matching.matches(self.st, piano, role="drums")}
        self.assertEqual(names, {"Drums 79.2.wav", "Drums 158.wav"})

    def test_bars_from_duration_when_analysis_missing(self):
        it = mk("Loop", ML, "loop", 120, "Am")
        it["audio"] = {"duration_s": 8.0}  # 4 bars at 120 BPM
        self.assertEqual(matching.F(it).bars, 4)


class GenrePreferenceTest(StrictStoreBase):
    @classmethod
    def setUpClass(cls):
        rows = []
        for i, g in enumerate(["House", "Trap", "Trap", "House"]):
            it = mk("Drums %s %d" % (g, i), DL, "loop", 140)
            it["meta"]["genre"] = {"value": g, "source": "folder", "confidence": 0.8}
            rows.append(it)
        cls.items = rows + [mk("Mel Am", ML, "loop", 140, "Am")]

    def test_genre_preferred_when_available(self):
        for seed in range(15):
            r = matching.starter(self.st, key="Am", bpm=140, seed=seed, genre="trap")
            self.assertIn("Trap", self.name(r, "drums"))

    def test_unknown_genre_falls_back(self):
        r = matching.starter(self.st, key="Am", bpm=140, seed=1, genre="Polka")
        self.assertIsNotNone(r["slots"]["drums"])


if __name__ == "__main__":
    unittest.main()

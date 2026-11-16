"""Library filters: compatible keys, half/double tempo, verified only, genre (stdlib unittest)."""
import shutil
import tempfile
import unittest

from samplelib.models import field
from samplelib.store import Store
from test_matching import BL, DL, ML, mk


def names(st, **q):
    q = {k: str(v) for k, v in q.items()}
    return sorted(i["name"][:-4] for i in st.query(q)[1])


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.st = Store(self.d, "/x")
        guess = mk("Mel Guess", ML, "loop", 140, "Am")
        guess["meta"]["bpm"] = field(140, "analysis", 0.4)
        clash = mk("Mel Clash", ML, "loop", 140, "Am")
        clash["warnings"] = ["Filename says 140 BPM, audio measures 128"]
        trap = mk("Drum Trap", DL, "loop", 70)
        trap["meta"]["genre"] = field("Trap", "pack", 0.5)
        house = mk("Drum House", DL, "loop", 124)
        house["meta"]["genre"] = field("House", "tag", None)
        self.st._set_inventory([
            mk("Mel Am", ML, "loop", 140, "Am"),
            mk("Bass C", BL, "loop", 140, "C"),
            mk("Bass Em", BL, "loop", 140, "Em"),
            guess, clash, trap, house,
        ], [], persist=False)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_key_exact_by_default(self):
        self.assertEqual(names(self.st, key="Am"), ["Mel Am", "Mel Clash", "Mel Guess"])

    def test_compatible_keys_adds_relative_only(self):
        got = names(self.st, key="Am", key_compat=1)
        self.assertIn("Bass C", got)
        self.assertNotIn("Bass Em", got)  # Camelot neighbor is not included

    def test_half_double_tempo(self):
        self.assertEqual(names(self.st, bpm_min=138, bpm_max=142, loop_type="loop", category=DL), [])
        self.assertEqual(names(self.st, bpm_min=138, bpm_max=142, category=DL, bpm_half=1),
                         ["Drum Trap"])

    def test_verified_only(self):
        got = names(self.st, verified=1, category=ML)
        self.assertEqual(got, ["Mel Am"])  # guessed BPM and contradicted BPM drop out

    def test_genre_filter_and_verified_genre(self):
        self.assertEqual(names(self.st, genre="trap"), ["Drum Trap"])
        self.assertEqual(names(self.st, genre="house", verified=1), ["Drum House"])
        self.assertEqual(names(self.st, genre="trap", verified=1), ["Drum Trap"])  # from pack counts

    def test_smart_collection_keeps_new_params(self):
        from samplelib.collections import SMART_KEYS
        for k in ("key_compat", "bpm_half", "verified"):
            self.assertIn(k, SMART_KEYS)


if __name__ == "__main__":
    unittest.main()

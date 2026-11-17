"""Scan: "no key" for drums/FX/noise, loudness, genre from text only (stdlib unittest)."""
import unittest

from samplelib import analysis, classify
from samplelib.models import field
from test_matching import mk


def item(name, cat, lt="one-shot", inst=None, key=None, key_src="analysis", key_conf=0.5,
         pack="P", genre=None, genre_src=None):
    it = mk(name, cat, lt, pack=pack)
    if inst:
        it["meta"]["instrument"] = field(inst, "filename", 0.8)
    if key:
        it["meta"]["key"] = field(key, key_src, key_conf)
    if genre:
        it["meta"]["genre"] = field(genre, genre_src, 0.6)
    return it


class NoKeyTest(unittest.TestCase):
    def test_low_confidence_measured_key_on_drums_is_dropped(self):
        it = item("Kick", "One-Shots/Drums", key="F#", key_conf=0.4)
        classify.apply_no_key(it)
        self.assertTrue(it["no_key"])
        self.assertIsNone(it["meta"]["key"]["value"])

    def test_fx_and_noise_marked_no_key(self):
        for inst in ("riser", "noise", "fx"):
            it = item("Thing", "One-Shots/FX & Vocals", inst=inst, key="C", key_conf=0.5)
            classify.apply_no_key(it)
            self.assertTrue(it["no_key"], inst)

    def test_confident_or_named_keys_are_kept(self):
        strong = item("Kick", "One-Shots/Drums", key="F", key_conf=0.85)
        named = item("Tom", "One-Shots/Drums", key="G", key_src="filename", key_conf=0.9)
        manual = item("Snare", "One-Shots/Drums", key="A", key_src="manual", key_conf=None)
        for it in (strong, named, manual):
            classify.apply_no_key(it)
            self.assertFalse(it["no_key"], it["name"])
            self.assertTrue(it["meta"]["key"]["value"])

    def test_melodic_low_confidence_key_untouched(self):
        it = item("Pad", "Loops/Melody Loops", lt="loop", key="Dm", key_conf=0.35)
        classify.apply_no_key(it)
        self.assertFalse(it["no_key"])
        self.assertEqual(it["meta"]["key"]["value"], "Dm")

    def test_drums_without_key_show_no_key(self):
        it = item("Hat", "One-Shots/Drums")
        classify.apply_no_key(it)
        self.assertTrue(it["no_key"])


class LoudnessTest(unittest.TestCase):
    def test_rms_db(self):
        self.assertAlmostEqual(analysis.rms_db([0.5, -0.5] * 100), -6.0, places=1)
        self.assertAlmostEqual(analysis.rms_db([0.05, -0.05] * 100), -26.0, places=1)
        self.assertIsNone(analysis.rms_db([0.0] * 10))
        self.assertIsNone(analysis.rms_db([]))


class GenreTest(unittest.TestCase):
    def test_pack_genre_spreads_as_from_pack(self):
        items = [item("A%d" % i, "One-Shots/Drums", genre="Trap", genre_src="tag") for i in range(3)]
        items += [item("B", "One-Shots/Drums"), item("C", "One-Shots/Drums")]
        classify.spread_pack_genre(items)
        for it in items[3:]:
            self.assertEqual(it["meta"]["genre"]["value"], "Trap")
            self.assertEqual(it["meta"]["genre"]["source"], "pack")
        self.assertEqual(items[0]["meta"]["genre"]["source"], "tag")

    def test_no_spread_when_pack_is_mixed_or_thin(self):
        mixed = [item("T%d" % i, "One-Shots/Drums", genre="Trap", genre_src="tag") for i in range(3)]
        mixed += [item("H%d" % i, "One-Shots/Drums", genre="House", genre_src="tag") for i in range(3)]
        mixed += [item("X", "One-Shots/Drums")]
        classify.spread_pack_genre(mixed)
        self.assertIsNone(mixed[-1]["meta"]["genre"]["value"])
        thin = [item("T", "One-Shots/Drums", genre="Trap", genre_src="tag", pack="Q"),
                item("Y", "One-Shots/Drums", pack="Q")]
        classify.spread_pack_genre(thin)
        self.assertIsNone(thin[1]["meta"]["genre"]["value"])

    def test_genre_from_pack_name_is_labeled_pack(self):
        it = {"rel_path": "Trap Essentials Vol 2/Kick 01.wav", "name": "Kick 01.wav",
              "ext": ".wav", "kind": "audio", "pack": "Trap Essentials Vol 2",
              "tags_raw": {}, "audio": {"duration_s": 0.3}}
        meta = classify.classify_item(it)
        self.assertEqual(meta["genre"]["value"], "Trap")
        self.assertEqual(meta["genre"]["source"], "pack")

    def test_genre_never_from_audio(self):
        it = {"rel_path": "Pack/Loop 01.wav", "name": "Loop 01.wav", "ext": ".wav",
              "kind": "audio", "pack": "Pack", "tags_raw": {}, "audio": {"duration_s": 8},
              "analysis": {"bpm": 140, "key": "Am", "key_conf": 0.9, "bars": 4,
                           "rms_db": -12.0, "genre": "Trap"}}
        meta = classify.classify_item(it)
        self.assertIsNone(meta["genre"]["value"])
        self.assertNotEqual(meta["genre"]["source"], "analysis")


if __name__ == "__main__":
    unittest.main()

"""Mix matching and Beat Starter (stdlib unittest)."""
import shutil
import tempfile
import unittest

from samplelib import matching
from samplelib.models import field, item_id
from samplelib.store import Store


def mk(name, cat, lt, bpm=None, key=None, pack="P", sha=None, bars=None, on_grid=None,
       near=(), excluded=False):
    rel = "%s/%s.wav" % (pack, name)
    meta = {k: field() for k in ("category", "instrument", "loop_type", "bpm", "key",
                                  "genre", "manufacturer")}
    meta["tags"] = field([])
    meta["category"] = field(cat, "inference", 0.9)
    meta["loop_type"] = field(lt, "filename", 0.9)
    meta["bpm"] = field(bpm, "filename" if bpm else None, 0.9 if bpm else None)
    meta["key"] = field(key, "filename" if key else None, 0.9 if key else None)
    return {"id": item_id("incoming", rel), "origin": "incoming", "rel_path": rel,
            "abs_path": "/x/" + rel, "name": name + ".wav", "ext": ".wav", "size": 1,
            "mtime_ns": 0, "kind": "audio", "sha256": sha or name, "pack": pack,
            "meta": meta, "analysis": {"bars": bars, "on_grid": on_grid} if bars else None,
            "near_dupes": list(near), "excluded": excluded, "warnings": [], "tags_raw": {},
            "preview": "native", "kit_id": None}


ML, DL, BL = "Loops/Melody Loops", "Loops/Drum Loops", "Loops/Bass Loops"


class MatchingBase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.st = Store(self.d, "/x")
        near = mk("Mel Near", ML, "loop", 128, "Am")
        self.items = [
            mk("Mel A", ML, "loop", 128, "Am", bars=4, on_grid=True, near=[near["id"]]),
            mk("Mel A copy", ML, "loop", 128, "Am", pack="Q", sha="Mel A"),
            near,
            mk("Mel Other", ML, "loop", 128, "Am", pack="Q"),
            mk("Drum 128", DL, "loop", 128, bars=4, on_grid=True),
            mk("Drum 64", DL, "loop", 64),
            mk("Drum 99", DL, "loop", 99),
            mk("Bass Same", BL, "loop", 128, "Am", pack="Q"),
            mk("Bass Rel", BL, "loop", 128, "C", pack="Q"),
            mk("Bass Clash", BL, "loop", 128, "F#", pack="Q"),
            mk("Bass Excl", BL, "loop", 128, "Am", excluded=True),
            mk("808 A", "One-Shots/Bass & 808s", "one-shot", None, "A"),
        ]
        self.st._set_inventory(self.items, [], persist=False)
        self.src = self.items[0]["id"]

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def names(self, res):
        return [r["row"]["name"][:-4] for r in res]


class MatchesTest(MatchingBase):
    def test_same_key_above_relative_above_clash(self):
        n = self.names(matching.matches(self.st, self.src, role="bass"))
        self.assertLess(n.index("Bass Same"), n.index("Bass Rel"))
        if "Bass Clash" in n:
            self.assertLess(n.index("Bass Rel"), n.index("Bass Clash"))

    def test_half_time(self):
        res = matching.matches(self.st, self.src, role="drums")
        n = self.names(res)
        self.assertLess(n.index("Drum 128"), n.index("Drum 64"))
        half = res[n.index("Drum 64")]
        self.assertTrue(any("half-time" in r for r in half["reasons"]), half["reasons"])
        self.assertEqual(matching.tempo_compat(70, 140)[0], 0.8)
        self.assertEqual(matching.tempo_compat(128, 129)[0], 1.0)

    def test_exclusions(self):
        n = self.names(matching.matches(self.st, self.src, limit=100))
        for bad in ("Mel A", "Mel A copy", "Mel Near", "Bass Excl"):
            self.assertNotIn(bad, n)

    def test_role_filter(self):
        res = matching.matches(self.st, self.src, role="bass")
        self.assertTrue(res)
        self.assertTrue(all(matching.role_of(self.st.by_id[r["row"]["id"]]) == "bass" for r in res))

    def test_prefers_other_roles(self):
        n = self.names(matching.matches(self.st, self.src))
        self.assertLess(n.index("Bass Same"), n.index("Mel Other"))
        self.assertLess(n.index("Drum 128"), n.index("Mel Other"))

    def test_one_shot_key_match(self):
        n = self.names(matching.matches(self.st, self.src, role="bass"))
        self.assertIn("808 A", n)

    def test_unknown_id(self):
        with self.assertRaises(KeyError):
            matching.matches(self.st, "nope")


if __name__ == "__main__":
    unittest.main()

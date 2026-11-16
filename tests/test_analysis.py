"""Audio analysis: decoding, tempo, bars, downbeat, key (stdlib unittest)."""
import array
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import make_fixtures as mf  # noqa: E402
from samplelib import analysis  # noqa: E402

SR = 22050


def _wav(samples, sr=SR, bits=16, fmt_tag=1):
    if fmt_tag == 3:
        data = array.array("f", samples).tobytes()
        bits = 32
    elif bits == 24:
        data = b"".join(struct.pack("<i", int(max(-1, min(1, v)) * 8388607))[:3] for v in samples)
    else:
        return mf.wav_bytes(samples, sr=sr)
    align = bits // 8
    fmt = struct.pack("<HHIIHH", fmt_tag, 1, sr, sr * align, align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


class AnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def put(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_128_am_four_bars(self):
        p = self.put("a.wav", _wav(mf.beat_loop(128, 4, "Am")))
        r, w = analysis.analyze(p, ".wav")
        self.assertAlmostEqual(r["bpm"], 128, delta=1.28)
        self.assertEqual(r["bars"], 4)
        self.assertTrue(r["on_grid"])
        self.assertIn(r["key"], {"Am", "C"})
        self.assertEqual(r["v"], analysis.ANALYSIS_VERSION)

    def test_90_fsharp_major(self):
        p = self.put("b.wav", _wav(mf.beat_loop(90, 2, "F#")))
        r, _ = analysis.analyze(p, ".wav")
        self.assertAlmostEqual(r["bpm"], 90, delta=0.9)
        self.assertIn(r["key"], {"F#", "D#m"})

    def test_formats_decode(self):
        s = mf.beat_loop(120, 1, "C")
        for name, data in (("c24.wav", _wav(s, bits=24)), ("cf.wav", _wav(s, fmt_tag=3)),
                           ("c.aif", mf.aiff_bytes(s, sr=SR))):
            dec = analysis.decode(self.put(name, data), os.path.splitext(name)[1])
            self.assertIsNotNone(dec, name)
            x, fs, dur = dec
            self.assertAlmostEqual(dur, 2.0, delta=0.01, msg=name)
            self.assertGreater(max(abs(v) for v in x), 0.3, name)

    def test_offset_is_off_grid(self):
        p = self.put("o.wav", _wav(mf.beat_loop(128, 4, "Am", offset_s=0.15)))
        r, _ = analysis.analyze(p, ".wav")
        self.assertFalse(r["on_grid"])
        self.assertGreater(r["downbeat_ms"], 100)
        self.assertAlmostEqual(r["bpm"], 128, delta=1.28)

    def test_silence(self):
        p = self.put("s.wav", _wav([0.0] * SR * 2))
        r, _ = analysis.analyze(p, ".wav")
        self.assertIsNone(r["bpm"])
        self.assertIsNone(r["key"])

    def test_truncated(self):
        data = _wav(mf.beat_loop(120, 1, "C"))
        p = self.put("t.wav", data[:30])
        r, w = analysis.analyze(p, ".wav")
        self.assertIsNone(r)
        self.assertTrue(w)

    def test_one_shot_root(self):
        p = self.put("bass.wav", _wav(mf.sine(55.0, 0.6, 0.6, decay=3, sr=SR)))
        r, _ = analysis.analyze(p, ".wav", want=frozenset({"root"}))
        self.assertEqual(r["key"], "A")
        self.assertIsNone(r["bpm"])

    def test_key_compat(self):
        self.assertEqual(analysis.key_compat("Am", "Am")[0], 1.0)
        self.assertEqual(analysis.key_compat("Am", "C")[0], 0.9)
        self.assertEqual(analysis.key_compat("Am", "Em")[0], 0.7)
        self.assertEqual(analysis.key_compat("Bbm", "A#m")[0], 1.0)
        self.assertLess(analysis.key_compat("C", "F#")[0], 0.5)
        self.assertEqual(analysis.camelot("Am"), "8A")


if __name__ == "__main__":
    unittest.main()

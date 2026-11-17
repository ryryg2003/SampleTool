"""Faster tempo & key measurement: the C-speed rewrites give the same numbers as
the plain formulas, and parallel worker processes give the same results as
measuring one file at a time."""
from __future__ import annotations

import array
import math
import os
import random
import shutil
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (REPO, os.path.join(REPO, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import make_fixtures as mf  # noqa: E402
from samplelib import analysis, classify, inventory  # noqa: E402


def _ref_to_mono(data, ch, bits, enc, big, fs_in):
    """The original per-sample formulation, kept here as the reference."""
    if enc == "float":
        a = array.array("f" if bits == 32 else "d")
        scale = 1.0
    elif bits == 8:
        a = array.array("B" if enc == "uint8" else "b")
        scale = 1 / 128
    elif bits == 16:
        a = array.array("h")
        scale = 1 / 32768
    elif bits == 24:
        n = len(data) // 3
        vals = []
        for i in range(n):
            b = data[3 * i:3 * i + 3]
            vals.append(int.from_bytes(b, "big" if big else "little", signed=True) << 8)
        a = array.array("i", vals)
        data, big, scale = a.tobytes(), sys.byteorder == "big", 1 / 2147483648
        a = array.array("i")
    else:
        a = array.array("i")
        scale = 1 / 2147483648
    usable = len(data) - len(data) % (a.itemsize * ch)
    a.frombytes(data[:usable])
    if big != (sys.byteorder == "big") and a.itemsize > 1:
        a.byteswap()
    off = 128 * ch if enc == "uint8" else 0
    d = max(1, round(fs_in / analysis.TARGET_FS))
    step = d * ch
    k = scale / step
    return [(sum(a[i:i + step]) - off * d) * k for i in range(0, len(a) - step + 1, step)], fs_in / d


class FastPathsMatchReferenceTest(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(7)

    def test_autocorrelation_matches_plain_formula(self):
        env = [self.rng.uniform(-1, 1) for _ in range(500)]
        ac = analysis._AC(env)
        for lag in (0, 1, 2.5, 17.25, 43.0, 86.7, 172.4, 298.9, 498.5, 499, 600):
            self.assertAlmostEqual(ac(lag), analysis._ac(env, lag), places=9, msg=lag)

    def test_downmix_matches_plain_formula(self):
        cases = [
            ("int", 16, 2, False, 44100), ("int", 16, 1, False, 22050), ("int", 24, 2, False, 48000),
            ("int", 24, 2, True, 44100), ("int", 32, 1, False, 96000), ("uint8", 8, 2, False, 22050),
            ("float", 32, 2, False, 44100), ("float", 64, 1, False, 11025),
        ]
        for enc, bits, ch, big, fs in cases:
            n = 4000 * ch + 3  # a ragged tail is dropped by both
            if enc == "float":
                vals = [self.rng.uniform(-1, 1) for _ in range(n)]
                data = array.array("f" if bits == 32 else "d", vals).tobytes()
            else:
                width = bits // 8
                lo, hi = (0, 255) if enc == "uint8" else (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
                data = b"".join(self.rng.randint(lo, hi).to_bytes(width, "big" if big else "little",
                                                                  signed=enc != "uint8") for _ in range(n))
            got, fs_got = analysis._to_mono(data, ch, bits, enc, big, fs)
            want, fs_want = _ref_to_mono(data, ch, bits, enc, big, fs)
            self.assertEqual(fs_got, fs_want)
            self.assertEqual(len(got), len(want), (enc, bits, ch))
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, places=9, msg=(enc, bits, ch, big))

    def test_loudness_and_peak(self):
        x = [0.5, -0.25, 0.75, -0.9]
        self.assertEqual(analysis.rms_db(x), round(10 * math.log10(sum(v * v for v in x) / 4), 1))


class WorkerCountTest(unittest.TestCase):
    def test_small_jobs_stay_in_process(self):
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": ""}):
            self.assertEqual(classify.analysis_workers(classify.PARALLEL_MIN_FILES - 1), 1)

    def test_uses_spare_cores_up_to_the_cap(self):
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": ""}), \
                mock.patch("os.cpu_count", return_value=16):
            self.assertEqual(classify.analysis_workers(1000), classify.MAX_WORKERS)
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": ""}), \
                mock.patch("os.cpu_count", return_value=2):
            self.assertEqual(classify.analysis_workers(1000), 1)

    def test_override(self):
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": "3"}):
            self.assertEqual(classify.analysis_workers(2), 2)
            self.assertEqual(classify.analysis_workers(50), 3)


class ParallelAnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="slo-par-")
        loops = os.path.join(cls.root, "Pack", "Loops")
        os.makedirs(loops)
        keys = ["Am", "C", "Em", "G", "Dm", "F"]
        for i in range(14):
            data = mf.wav_bytes(mf.beat_loop(90 + 4 * i, 1, keys[i % len(keys)]), sr=22050)
            with open(os.path.join(loops, f"Synth Loop {i:02d}.wav"), "wb") as f:
                f.write(data)
            if i < 4:  # identical copies are measured once
                with open(os.path.join(loops, f"Synth Loop {i:02d} copy.wav"), "wb") as f:
                    f.write(data)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def fresh(self):
        items = inventory.scan(self.root, os.path.join(self.root, "Samples"), None)["items"]
        classify.enrich(items)
        return items

    def results(self, items):
        return {i["name"]: (i.get("analysis"), (i["meta"].get("bpm") or {}).get("value"),
                            (i["meta"].get("key") or {}).get("value")) for i in items}

    def test_parallel_matches_one_at_a_time(self):
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": "1"}):
            seq = self.fresh()
            cache_seq = classify.analyze_items(seq, {})
        calls = []
        real = classify._run_parallel

        def spy(jobs, workers, cancel, on_result):
            calls.append((len(jobs), workers))
            return real(jobs, workers, cancel, on_result)

        seen = []
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": "2"}), \
                mock.patch.object(classify, "_run_parallel", spy):
            par = self.fresh()
            cache_par = classify.analyze_items(par, {}, progress=seen.append)
        self.assertEqual(calls, [(14, 2)], "14 distinct files, two workers; copies not re-measured")
        self.assertEqual(self.results(par), self.results(seq))
        self.assertEqual(cache_par, cache_seq)
        self.assertTrue(all("analysis" in i for i in par if i["kind"] == "audio"))
        last = seen[-1]
        self.assertEqual((last["done"], last["total"]), (18, 18))

    def test_cancel_before_start_measures_nothing(self):
        stop = threading.Event()
        stop.set()
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": "2"}):
            items = self.fresh()
            used = classify.analyze_items(items, {}, cancel=stop)
        self.assertEqual(used, {})
        self.assertFalse(any("analysis" in i for i in items))

    def test_cancel_while_workers_run(self):
        stop = threading.Event()
        got = []

        def progress(p):
            got.append(p)
            if p.get("eta_mark") == "measuring" and p.get("done", 0) > 0:
                stop.set()  # the user presses Cancel after the first results

        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": "2"}), \
                mock.patch.object(classify, "PARALLEL_MIN_FILES", 1):
            items = self.fresh()
            for it in items:
                it["sha256"] = None  # nothing cached or shared: 18 separate jobs
            classify.analyze_items(items, {}, progress=progress, cancel=stop)
        measured = sum(1 for i in items if "analysis" in i)
        self.assertTrue(stop.is_set())
        self.assertLess(measured, 18, "cancel stops handing out work")

    def test_rescan_reports_cached_files_before_the_clock_starts(self):
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": "1"}):
            items = self.fresh()
            cache = classify.analyze_items(items, {})
            items = self.fresh()
            seen = []
            classify.analyze_items(items, cache, progress=seen.append)
        marks = [p.get("eta_mark") for p in seen if p.get("phase") == "analyzing"]
        self.assertIn("cached", marks)
        self.assertEqual(marks[marks.index("measuring")], "measuring")
        self.assertNotIn("cached", marks[marks.index("measuring"):], "cached work is reported first")

    def test_pool_that_cannot_start_falls_back(self):
        import concurrent.futures as cf
        with mock.patch.dict(os.environ, {"SLO_ANALYSIS_WORKERS": "2"}), \
                mock.patch.object(cf, "ProcessPoolExecutor", side_effect=OSError("no processes")):
            items = self.fresh()
            classify.analyze_items(items, {})
        self.assertTrue(all("analysis" in i for i in items if i["kind"] == "audio"))

    def test_worker_never_raises(self):
        res, warns = analysis.analyze_job(os.path.join(self.root, "missing.wav"), ".wav", frozenset({"tempo"}))
        self.assertIsNone(res)
        self.assertTrue(warns)


if __name__ == "__main__":
    unittest.main()

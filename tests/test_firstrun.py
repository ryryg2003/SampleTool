"""First run: no default collection folder, the read-only folder picker API,
and the scan time-left estimate."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from samplelib import api_folders, settings  # noqa: E402
from samplelib.server import serve  # noqa: E402
from samplelib.store import Store  # noqa: E402


def _snapshot(top):
    out = {}
    for base, dirs, files in os.walk(top):
        for n in dirs + files:
            p = os.path.join(base, n)
            st = os.stat(p)
            out[os.path.relpath(p, top)] = (st.st_size, st.st_mtime_ns)
    return out


class FirstRunServer:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="slo-first-")
        self.data = os.path.join(self.tmp, "appdata")
        self.store = Store(self.data, "")
        self.httpd, self.app = serve(self.store, port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def get(self, endpoint, **q):
        url = f"http://127.0.0.1:{self.port}{endpoint}" + ("?" + urllib.parse.urlencode(q) if q else "")
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class NoDefaultFolderTest(unittest.TestCase):
    def test_fresh_store_has_no_collection_folder(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(os.path.join(d, "data"), "")
            self.assertEqual(st.root, "")
            self.assertEqual(settings.load(os.path.join(d, "data"))["root"], "")

    def test_saved_folder_still_wins(self):
        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "data")
            settings.update(data, {"root": os.path.join(d, "packs")})
            self.assertEqual(Store(data, "").root, os.path.join(d, "packs"))

    def test_no_e_drive_anywhere_in_the_app(self):
        hits = []
        for sub in ("samplelib", "ui"):
            for base, _dirs, files in os.walk(os.path.join(REPO, sub)):
                for f in files:
                    if f.endswith((".py", ".js", ".html")) and f != "fixtures.js":
                        with open(os.path.join(base, f), encoding="utf-8") as fh:
                            if "E:\\" in fh.read().replace("E:\\\\", "E:\\"):
                                hits.append(f)
        self.assertEqual(hits, [])


class FolderApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = FirstRunServer()
        cls.top = os.path.join(cls.srv.tmp, "Music")
        for d in ("Sample Packs/Pack A/Drums", "Sample Packs/pack b", "Sample Packs/.hidden", "Sample Packs/$RECYCLE.BIN"):
            os.makedirs(os.path.join(cls.top, *d.split("/")))
        for f in ("kick.wav", "SNARE.AIF", "notes.txt", "loop.mp3"):
            with open(os.path.join(cls.top, "Sample Packs", f), "wb") as fh:
                fh.write(b"x")

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def test_state_on_first_run(self):
        code, s = self.srv.get("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(s["root"], "")
        self.assertFalse(s["has_inventory"])

    def test_start_list(self):
        code, j = self.srv.get("/api/folders")
        self.assertEqual(code, 200)
        self.assertEqual(j["path"], "")
        self.assertIsNone(j["parent"])
        self.assertTrue(j["folders"], "drives (or /) are listed")
        self.assertTrue(all(os.path.isdir(f["path"]) for f in j["folders"]))
        self.assertTrue(any(p["label"] == "Home" for p in j["places"]))

    def test_lists_subfolders_and_counts_audio(self):
        packs = os.path.join(self.top, "Sample Packs")
        before = _snapshot(self.top)
        code, j = self.srv.get("/api/folders", path=packs)
        self.assertEqual(code, 200, j)
        self.assertEqual(j["path"], packs)
        self.assertEqual(j["parent"], self.top)
        self.assertEqual([f["name"] for f in j["folders"]], ["Pack A", "pack b"], "sorted, hidden ones skipped")
        self.assertEqual(j["folders"][0]["path"], os.path.join(packs, "Pack A"))
        self.assertEqual(j["audio_files"], 3)
        self.assertFalse(j["truncated"])
        self.assertEqual(_snapshot(self.top), before, "listing never changes anything")

    def test_quoted_path_and_trailing_space(self):
        code, j = self.srv.get("/api/folders", path=f' "{self.top}" ')
        self.assertEqual(code, 200, j)
        self.assertEqual(j["path"], self.top)

    def test_missing_folder_is_a_clear_error(self):
        code, j = self.srv.get("/api/folders", path=os.path.join(self.top, "nope"))
        self.assertEqual(code, 400)
        self.assertIn("Folder not found", j["error"])

    def test_a_file_is_not_a_folder(self):
        code, j = self.srv.get("/api/folders", path=os.path.join(self.top, "Sample Packs", "kick.wav"))
        self.assertEqual(code, 400)

    def test_drive_root_has_no_parent(self):
        top = api_folders.drives()[0]
        self.assertEqual(api_folders._parent(top), "")

    def test_truncates_huge_folders(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(12):
                os.mkdir(os.path.join(d, f"f{i:02d}"))
            old = api_folders.MAX_FOLDERS
            api_folders.MAX_FOLDERS = 5
            try:
                j = api_folders.listing(d)
            finally:
                api_folders.MAX_FOLDERS = old
            self.assertEqual(len(j["folders"]), 5)
            self.assertTrue(j["truncated"])

    def test_places_skip_missing_and_duplicates(self):
        with tempfile.TemporaryDirectory() as home:
            os.mkdir(os.path.join(home, "Music"))
            labels = [p["label"] for p in api_folders.places(home)]
            self.assertEqual(labels, ["Home", "Music"])



class FirstScanTest(unittest.TestCase):
    def setUp(self):
        self.srv = FirstRunServer()
        self.top = os.path.join(self.srv.tmp, "Music")
        os.makedirs(os.path.join(self.top, "Sample Packs", "Pack A"))
        with open(os.path.join(self.top, "Sample Packs", "Pack A", "kick.wav"), "wb") as fh:
            fh.write(b"RIFF")

    def tearDown(self):
        self.srv.close()

    def test_first_scan_with_a_picked_folder(self):
        packs = os.path.join(self.top, "Sample Packs")
        before = _snapshot(self.top)
        req = urllib.request.Request(f"http://127.0.0.1:{self.srv.port}/api/scan", method="POST",
                                     data=json.dumps({"root": packs}).encode(),
                                     headers={"X-SLO": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            self.assertEqual(r.status, 200)
        import time
        t0 = time.time()
        while time.time() - t0 < 30:
            _, p = self.srv.get("/api/progress")
            if not p["running"]:
                break
            time.sleep(0.1)
        _, s = self.srv.get("/api/state")
        self.assertEqual(s["root"], packs)
        self.assertTrue(s["has_inventory"])
        self.assertEqual(settings.load(self.srv.data)["root"], packs, "the picked folder is remembered")
        after = _snapshot(self.top)
        self.assertEqual(after, before, "the scan wrote nothing into the sample folder")


if __name__ == "__main__":
    unittest.main()


class EtaTest(unittest.TestCase):
    def setUp(self):
        from samplelib.server import Eta
        self.t = 100.0
        self.eta = Eta(clock=lambda: self.t)

    def p(self, **kw):
        base = {"seq": 1, "job": "scan", "phase": "analyzing", "running": True, "done": 0, "total": 100,
                "bytes_done": 0, "bytes_total": 0}
        base.update(kw)
        return base

    def test_needs_a_couple_of_seconds(self):
        self.assertIsNone(self.eta.update(self.p(done=0)))
        self.t += 1
        self.assertIsNone(self.eta.update(self.p(done=10)), "too early to guess")

    def test_rate_since_step_start(self):
        self.eta.update(self.p(done=0))
        self.t += 4
        self.assertAlmostEqual(self.eta.update(self.p(done=20)), 16.0)  # 5/s, 80 left

    def test_new_step_restarts_the_clock(self):
        self.eta.update(self.p(done=0))
        self.t += 10
        self.eta.update(self.p(done=50))
        self.assertIsNone(self.eta.update(self.p(phase="kits", done=0, total=10)))
        self.t += 3
        self.assertAlmostEqual(self.eta.update(self.p(phase="kits", done=3, total=10)), 7.0)

    def test_fingerprinting_uses_bytes(self):
        self.eta.update(self.p(phase="hashing", done=0, total=10, bytes_done=0, bytes_total=1000))
        self.t += 2
        # one huge file done out of ten: bytes say 80% left, counts would say 90%
        self.assertAlmostEqual(self.eta.update(self.p(phase="hashing", done=1, total=10,
                                                      bytes_done=200, bytes_total=1000)), 8.0)

    def test_mark_restarts_the_clock_after_cached_files(self):
        # a rescan: 900 of 1000 come from the cache at once, then 100 need measuring
        self.eta.update(self.p(done=0, total=1000, eta_mark="cached"))
        self.t += 0.5
        self.eta.update(self.p(done=900, total=1000, eta_mark="cached"))
        self.assertIsNone(self.eta.update(self.p(done=900, total=1000, eta_mark="measuring")))
        self.t += 10
        self.assertAlmostEqual(self.eta.update(self.p(done=910, total=1000, eta_mark="measuring")), 90.0)

    def test_finished_or_idle_has_no_estimate(self):
        self.eta.update(self.p(done=0))
        self.t += 5
        self.assertIsNone(self.eta.update(self.p(done=100)))
        self.assertIsNone(self.eta.update(self.p(running=False)))

    def test_app_progress_carries_eta_and_step(self):
        srv = FirstRunServer()
        try:
            app = srv.app
            app.set_progress({"seq": 9, "job": "scan", "phase": "hashing", "running": True,
                              "done": 0, "total": 4, "bytes_done": 0, "bytes_total": 400})
            p = app.get_progress()
            self.assertEqual((p["step"], p["steps"]), (2, 5))
            self.assertIsNone(p["eta_s"])
            app._eta.t0 -= 5  # pretend the step began five seconds ago
            app.set_progress({"done": 1, "bytes_done": 100})
            self.assertAlmostEqual(app.get_progress()["eta_s"], 15.0)
            app.set_progress({"running": False, "phase": "complete"})
            p = app.get_progress()
            self.assertEqual((p["eta_s"], p["step"]), (None, None))
        finally:
            srv.close()

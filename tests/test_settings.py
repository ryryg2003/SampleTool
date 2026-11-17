"""Settings, desktop-app behaviour (logging, single instance, idle shutdown) and the icon.

Temp folders only. Windows-only parts (window focus via ctypes, the shortcut .bat)
run on Windows and are skipped elsewhere.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import zlib
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from samplelib import __version__, settings, winapp  # noqa: E402
from samplelib import __main__ as entry  # noqa: E402
from samplelib.server import serve  # noqa: E402
from samplelib.store import Store  # noqa: E402
from tests.test_e2e import Env  # noqa: E402

IS_WIN = os.name == "nt"


def _read(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)


class SettingsModuleTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="slo-set-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_defaults_without_file(self):
        self.assertEqual(settings.load(self.d), settings.DEFAULTS)

    def test_old_root_only_file_loads(self):
        _write(settings.path(self.d), {"root": "/some/packs"})
        s = settings.load(self.d)
        self.assertEqual(s["root"], "/some/packs")
        self.assertEqual(s["port"], 8765)
        self.assertEqual(s["volume"], 80)
        self.assertFalse(s["auto_audition"])
        self.assertEqual(s["density"], "comfortable")

    def test_old_file_through_store(self):
        root = os.path.join(self.d, "packs")
        os.makedirs(root)
        _write(settings.path(self.d), {"root": root})
        st = Store(self.d, "")
        self.assertEqual(st.root, root)
        self.assertEqual(_read(settings.path(self.d))["root"], root)

    def test_bad_stored_values_fall_back(self):
        _write(settings.path(self.d), {"port": 80, "volume": "loud", "density": "huge", "auto_audition": "maybe"})
        self.assertEqual({k: v for k, v in settings.load(self.d).items() if k != "root"},
                         {k: v for k, v in settings.DEFAULTS.items() if k != "root"})

    def test_corrupt_file_falls_back(self):
        with open(settings.path(self.d), "w") as f:
            f.write("{not json")
        self.assertEqual(settings.load(self.d), settings.DEFAULTS)

    def test_validation(self):
        bad = [("port", 80), ("port", 1023), ("port", 65536), ("port", "abc"), ("port", True), ("port", 8000.5),
               ("volume", -1), ("volume", 101), ("volume", None), ("density", "huge"), ("auto_audition", "maybe")]
        for k, v in bad:
            with self.assertRaises(ValueError, msg=f"{k}={v!r}"):
                settings.update(self.d, {k: v})
        with self.assertRaises(KeyError):
            settings.update(self.d, {"nope": 1})
        good = settings.update(self.d, {"port": "9001", "volume": 0, "density": "compact", "auto_audition": True})
        self.assertEqual((good["port"], good["volume"], good["density"], good["auto_audition"]), (9001, 0, "compact", True))
        self.assertEqual(settings.update(self.d, {"port": 1024, "volume": 100})["port"], 1024)
        self.assertEqual(settings.update(self.d, {"port": 65535})["port"], 65535)

    def test_all_or_nothing(self):
        settings.update(self.d, {"volume": 40})
        with self.assertRaises(ValueError):
            settings.update(self.d, {"volume": 50, "port": 1})
        self.assertEqual(settings.load(self.d)["volume"], 40)

    def test_unknown_keys_kept_and_store_does_not_clobber(self):
        _write(settings.path(self.d), {"root": "/a", "future_key": [1, 2]})
        settings.update(self.d, {"volume": 33})
        st = Store(self.d, "")
        other = os.path.join(self.d, "other")
        os.makedirs(other)
        st.set_root(other)
        raw = _read(settings.path(self.d))
        self.assertEqual(raw["future_key"], [1, 2])
        self.assertEqual(raw["volume"], 33)
        self.assertEqual(raw["root"], other)

    def test_root_refused_when_not_allowed(self):
        with self.assertRaises(ValueError):
            settings.update(self.d, {"root": "/x"}, allow_root=False)


class SettingsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env()

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_get_shape(self):
        j = self.env.get("/api/settings")
        self.assertEqual(set(j), {"settings", "defaults", "info", "busy", "job"})
        self.assertEqual(j["settings"]["root"], self.env.store.root)
        self.assertEqual(j["info"]["version"], __version__)
        self.assertEqual(j["info"]["data_dir"], self.env.data)
        self.assertTrue(j["info"]["log_file"].endswith(os.path.join("logs", "app.log")))
        self.assertFalse(j["busy"])
        py = j["info"]["python"]
        self.assertEqual(py["version"], "%d.%d.%d" % sys.version_info[:3])
        self.assertEqual(py["executable"], sys.executable)
        on_bundled = os.path.normcase(os.path.dirname(os.path.abspath(sys.executable))) == \
            os.path.normcase(os.path.join(REPO, "python"))
        self.assertEqual(py["bundled"], on_bundled)

    def test_python_info_spots_the_bundled_copy(self):
        from samplelib import api_settings
        exe = os.path.join(REPO, "python", "pythonw.exe")
        self.assertTrue(api_settings.python_info(exe)["bundled"])
        self.assertFalse(api_settings.python_info(os.path.join(REPO, "python-other", "python.exe"))["bundled"])
        self.assertFalse(api_settings.python_info("")["bundled"])

    def test_post_partial(self):
        _, j = self.env.post("/api/settings", {"volume": 42})
        self.assertEqual(j["settings"]["volume"], 42)
        _, j = self.env.post("/api/settings", {"density": "compact"})
        self.assertEqual(j["settings"]["volume"], 42)  # earlier value kept
        self.assertEqual(j["settings"]["density"], "compact")
        raw = _read(settings.path(self.env.data))
        self.assertEqual((raw["volume"], raw["density"], raw["root"]), (42, "compact", self.env.store.root))

    def test_post_rejects_bad_input(self):
        for body in ({"port": 99}, {"volume": 500}, {"nope": 1}, {}, {"root": "/elsewhere"}):
            code, j = self.env.post("/api/settings", body, expect=None)
            self.assertEqual(code, 400, body)
            self.assertIn("error", j)
        self.assertEqual(self.env.get("/api/settings")["settings"]["root"], self.env.store.root)

    def test_post_needs_csrf_header(self):
        code, _ = self.env.post("/api/settings", {"volume": 1}, slo=False, expect=None)
        self.assertEqual(code, 403)

    def test_state_has_version_and_ui_settings(self):
        st = self.env.get("/api/state")
        self.assertEqual(st["version"], __version__)
        self.assertIn("density", st["ui_settings"])
        self.assertNotIn("root", st["ui_settings"])

    def test_focus_bumps_progress(self):
        before = self.env.get("/api/progress").get("focus_seq", 0)
        _, j = self.env.post("/api/focus", {})
        self.assertTrue(j["ok"])
        self.assertEqual(self.env.get("/api/progress")["focus_seq"], before + 1)


class SettingsRootSwitchTest(unittest.TestCase):
    """The Settings screen switches folders through POST /api/scan {root} (the safe path)."""

    def setUp(self):
        self.env = Env()
        self.other = tempfile.mkdtemp(prefix="slo-other-")
        os.makedirs(os.path.join(self.other, "Pack B"))
        with open(os.path.join(self.other, "Pack B", "Kick B.wav"), "wb") as f:
            f.write(b"RIFF" + b"\0" * 64)

    def tearDown(self):
        self.env.close()
        shutil.rmtree(self.other, ignore_errors=True)

    def test_refused_while_job_runs_and_reported_busy(self):
        env = self.env
        env.scan()
        gate = threading.Event()
        orig = env.app.job_plan

        def slow_plan():
            gate.wait(10)
            orig()
        env.app.job_plan = slow_plan
        env.post("/api/plan/build", {})
        try:
            self.assertTrue(env.get("/api/settings")["busy"])
            code, j = env.post("/api/scan", {"root": self.other}, expect=None)
            self.assertEqual(code, 409, j)
            self.assertEqual(os.path.normcase(env.store.root), os.path.normcase(env.root))
            self.assertEqual(_read(settings.path(env.data))["root"], env.root)
            # settings that don't touch the collection still save during a job
            env.post("/api/settings", {"volume": 12})
        finally:
            gate.set()
            env.wait()

    def test_refused_during_approved_run(self):
        env = self.env
        env.scan()
        env.app.executing = True
        try:
            code, _ = env.post("/api/scan", {"root": self.other}, expect=None)
            self.assertEqual(code, 409)
        finally:
            env.app.executing = False

    def test_switch_clears_plan_and_approval_and_keeps_settings(self):
        env = self.env
        env.post("/api/settings", {"volume": 21, "density": "compact"})
        env.scan()
        p = env.build_plan()
        env.approve(p["plan_hash"])
        env.post("/api/scan", {"root": self.other})
        env.wait()
        st = env.get("/api/state")
        self.assertIsNone(st["plan"])
        self.assertIsNone(env.store.approved_hash)
        self.assertFalse(os.path.exists(os.path.join(env.data, "approval.json")))
        s = env.get("/api/settings")["settings"]
        self.assertEqual(os.path.normcase(s["root"]), os.path.normcase(os.path.abspath(self.other)))
        self.assertEqual((s["volume"], s["density"]), (21, "compact"))
        code, _ = env.post("/api/execute", {"plan_hash": p["plan_hash"]}, expect=None)
        self.assertEqual(code, 409)


class IdleShutdownTest(unittest.TestCase):
    TIMEOUT = 0.3

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="slo-idle-")
        root = os.path.join(self.d, "root")
        os.makedirs(root)
        self.httpd, self.app = serve(Store(os.path.join(self.d, "data"), root), port=0)
        self.port = self.httpd.server_address[1]
        self.watcher = winapp.IdleWatcher(self.httpd, self.app, self.TIMEOUT, interval=0.05)
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.watcher.start()

    def tearDown(self):
        self.watcher.stop()
        if self.thread.is_alive():
            self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.d, ignore_errors=True)

    def _ui_poll(self):
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/progress", timeout=5) as r:
            return json.loads(r.read())

    def _stopped_within(self, seconds):
        self.thread.join(seconds)
        return not self.thread.is_alive()

    def test_not_before_any_ui_request(self):
        self.assertFalse(self._stopped_within(1.0))
        self.assertFalse(self.watcher.fired)

    def test_shuts_down_when_idle(self):
        self._ui_poll()
        self.assertTrue(self._stopped_within(5))
        self.assertTrue(self.watcher.fired)
        with self.assertRaises(RuntimeError):  # nothing may start while the process exits
            self.app.start_job("scan", lambda: None)

    def test_ui_requests_keep_it_alive(self):
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1.0:
            self._ui_poll()
            time.sleep(0.1)
        self.assertTrue(self.thread.is_alive())

    def test_never_during_a_running_job(self):
        gate = threading.Event()
        self.app.start_job("scan", lambda: gate.wait(20))
        self._ui_poll()
        self.assertFalse(self._stopped_within(1.0))
        self.assertTrue(self._ui_poll()["running"])  # the server still answers
        gate.set()
        self.assertTrue(self._stopped_within(5))

    def test_never_while_executing(self):
        self.app.executing = True  # an approved run between its job phases
        self._ui_poll()
        self.assertFalse(self._stopped_within(1.0))
        self.app.executing = False
        self.assertTrue(self._stopped_within(5))

    def test_sleep_gap_restarts_the_clock(self):
        """After the PC sleeps the clock jumps; the UI gets a fresh timeout to poll again."""
        real = time.monotonic

        class Clock:
            offset = 0.0

            @staticmethod
            def monotonic():
                return real() + Clock.offset
        self._ui_poll()
        with mock.patch.object(winapp, "time", Clock):
            Clock.offset = 1000.0  # ~17 minutes asleep
            self.assertFalse(self._stopped_within(0.15), "shut down right after wake-up")
            self.assertTrue(self._stopped_within(5))


class MainEntryTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="slo-main-")
        self.root = os.path.join(self.d, "root")
        os.makedirs(self.root)
        self.data = os.path.join(self.d, "data")

    def tearDown(self):
        for h in list(winapp.log.handlers):
            if getattr(h, "_slo", False):
                winapp.log.removeHandler(h)
                h.close()
        shutil.rmtree(self.d, ignore_errors=True)

    def _fake_serve(self, seen):
        real = serve

        def fake(store, port=8765):
            httpd, app = real(store, port=0)
            seen.update(port=port, httpd=httpd, app=app)
            httpd.serve_forever = lambda poll_interval=0.5: None  # return at once
            return httpd, app
        return fake

    def test_pythonw_without_stdout_logs_to_data_dir(self):
        seen = {}
        old = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = None
        try:
            with mock.patch.object(entry, "serve", self._fake_serve(seen)), \
                    mock.patch.object(winapp, "find_running", return_value=None):
                rc = entry.main(["--root", self.root, "--data-dir", self.data, "--no-browser"])
            self.assertIsNotNone(sys.stdout)  # replaced by a log stream
            print("stray print under pythonw", file=sys.stderr)
            sys.stderr.flush()
        finally:
            sys.stdout, sys.stderr = old
        self.assertEqual(rc, 0)
        log = os.path.join(self.data, "logs", "app.log")
        with open(log, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Sample Library Organizer", text)
        self.assertIn(self.root, text)
        self.assertIn("stray print under pythonw", text)
        self.assertEqual(seen["app"].log_file, log)

    def test_saved_port_used_when_no_flag(self):
        settings.update(self.data, {"port": 9123})
        seen = {}
        with mock.patch.object(entry, "serve", self._fake_serve(seen)), \
                mock.patch.object(winapp, "find_running", return_value=None), \
                mock.patch("sys.stdout", io.StringIO()):
            entry.main(["--root", self.root, "--data-dir", self.data, "--no-browser"])
        self.assertEqual(seen["port"], 9123)
        self.assertEqual(seen["app"].idle_exit, 0)  # --no-browser disables idle exit
        seen.clear()
        with mock.patch.object(entry, "serve", self._fake_serve(seen)), \
                mock.patch.object(winapp, "find_running", return_value=None), \
                mock.patch("sys.stdout", io.StringIO()):
            entry.main(["--root", self.root, "--data-dir", self.data, "--no-browser", "--port", "9200",
                        "--idle-exit", "60"])
        self.assertEqual(seen["port"], 9200)
        self.assertEqual(seen["app"].idle_exit, 60)

    def test_idle_exit_on_by_default_with_window(self):
        seen = {}
        with mock.patch.object(entry, "serve", self._fake_serve(seen)), \
                mock.patch.object(winapp, "find_running", return_value=None), \
                mock.patch.object(entry, "_open_window") as ow, \
                mock.patch("sys.stdout", io.StringIO()):
            entry.main(["--root", self.root, "--data-dir", self.data])
        self.assertEqual(seen["app"].idle_exit, winapp.DEFAULT_IDLE_EXIT)
        ow.assert_called_once()

    def test_second_launch_does_not_start_a_server(self):
        with mock.patch.object(winapp, "handle_second_launch", return_value=8765) as h, \
                mock.patch.object(entry, "serve") as sv, mock.patch("sys.stdout", io.StringIO()):
            rc = entry.main(["--data-dir", self.data])
        self.assertEqual(rc, 0)
        sv.assert_not_called()
        self.assertEqual(h.call_args[0][0], 8765)


class _Resp:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SingleInstanceTest(unittest.TestCase):
    def _urlopen(self, ours=None, other=None):
        calls = []

        def fake(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            calls.append(req)
            port = int(url.split(":")[2].split("/")[0])
            if url.endswith("/api/focus"):
                return _Resp(b'{"ok": true}')
            if port == ours:
                return _Resp(json.dumps({"version": "1.0.0", "dest_root": "x", "root": "y"}).encode())
            if port == other:
                return _Resp(b'{"hello": "another app"}')
            raise urllib.error.URLError("refused")
        return fake, calls

    def test_detects_only_this_app(self):
        fake, _ = self._urlopen(ours=9001, other=9000)
        self.assertTrue(winapp.is_our_server(9001, fake))
        self.assertFalse(winapp.is_our_server(9000, fake))
        self.assertFalse(winapp.is_our_server(9002, fake))
        html, _ = self._urlopen()
        self.assertFalse(winapp.is_our_server(1, lambda *a, **k: _Resp(b"<html>")))
        self.assertEqual(winapp.find_running(9000, fake), 9001)  # fell back past a busy port
        self.assertIsNone(winapp.find_running(9100, fake))

    def test_focuses_existing_window(self):
        fake, calls = self._urlopen(ours=8765)
        opened, focus = [], mock.Mock(return_value=True)
        self.assertEqual(winapp.handle_second_launch(8765, opened.append, urlopen=fake, focus=focus), 8765)
        focus.assert_called_once()
        self.assertEqual(opened, [])
        posts = [c for c in calls if not isinstance(c, str)]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].get_method(), "POST")
        self.assertEqual(posts[0].get_header("X-slo"), "1")

    def test_opens_window_when_none_found(self):
        fake, _ = self._urlopen(ours=8765)
        opened = []
        winapp.handle_second_launch(8765, opened.append, urlopen=fake, focus=lambda: False)
        self.assertEqual(opened, ["http://127.0.0.1:8765/"])

    def test_no_browser_neither_focuses_nor_opens(self):
        fake, _ = self._urlopen(ours=8765)
        opened, focus = [], mock.Mock(return_value=False)
        winapp.handle_second_launch(8765, opened.append, no_browser=True, urlopen=fake, focus=focus)
        focus.assert_not_called()
        self.assertEqual(opened, [])

    def test_nothing_running(self):
        fake, _ = self._urlopen()
        opened, focus = [], mock.Mock()
        self.assertIsNone(winapp.handle_second_launch(8765, opened.append, urlopen=fake, focus=focus))
        focus.assert_not_called()
        self.assertEqual(opened, [])

    def test_real_server_is_recognised(self):
        env = Env()
        try:
            self.assertTrue(winapp.is_our_server(env.port))
            self.assertTrue(winapp.notify_focus(env.port))
            self.assertEqual(env.get("/api/progress")["focus_seq"], 1)
        finally:
            env.close()

    def test_window_title_match(self):
        self.assertTrue(winapp._title_matches("Library · Sample Library Organizer", "Chrome_WidgetWin_1"))
        self.assertTrue(winapp._title_matches("Sample Library Organizer", "Chrome_WidgetWin_1"))
        self.assertFalse(winapp._title_matches("Sample Library Organizer", "ConsoleWindowClass"))
        self.assertFalse(winapp._title_matches("Inbox - Outlook", "Chrome_WidgetWin_1"))

    @unittest.skipIf(IS_WIN, "non-Windows behaviour")
    def test_focus_is_noop_off_windows(self):
        self.assertFalse(winapp.focus_app_window())
        self.assertIsNone(winapp.find_app_window())


def _png_info(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        crc = struct.unpack(">I", data[pos + 8 + n:pos + 12 + n])[0]
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, tag
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    return ihdr, zlib.decompress(idat)


class IconTest(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_icon", os.path.join(REPO, "tools", "make_icon.py"))
        self.mi = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mi)
        self.d = tempfile.mkdtemp(prefix="slo-icon-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_ico_is_valid(self):
        svg, ico = self.mi.build(self.d)
        with open(ico, "rb") as f:
            data = f.read()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 7))
        sizes = []
        for i in range(count):
            w, h, colors, _r, planes, bpp, size, off = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
            px = w or 256
            sizes.append(px)
            self.assertEqual((w, colors, planes, bpp), (h, 0, 1, 32))
            ihdr, raw = _png_info(data[off:off + size])
            self.assertEqual(ihdr[:4], (px, px, 8, 6))  # RGBA 8-bit
            self.assertEqual(len(raw), px * (1 + 4 * px))
            stride = 1 + 4 * px
            corner = raw[1:5]
            mid = raw[(px // 2) * stride + 1 + 4 * (px // 8): (px // 2) * stride + 5 + 4 * (px // 8)]
            self.assertEqual(corner[3], 0, "rounded corner is transparent")
            self.assertEqual(mid[3], 255, "inside is opaque")
        self.assertEqual(sizes, [16, 24, 32, 48, 64, 128, 256])
        with open(svg, encoding="utf-8") as f:
            self.assertIn("#F0B43C", f.read())

    @staticmethod
    def _ico_frames(blob: bytes) -> list:
        """Each frame's (directory entry, PNG header, decompressed pixel rows). Compares
        what the icon shows, not how zlib packed it: zlib builds differ (Python 3.14
        on Windows uses zlib-ng), so identical pixels can compress to other bytes."""
        _res, _typ, count = struct.unpack("<HHH", blob[:6])
        frames = []
        for i in range(count):
            entry = blob[6 + 16 * i: 6 + 16 * (i + 1)]
            size, offset = struct.unpack("<II", entry[8:16])
            png = blob[offset:offset + size]
            pos, ihdr, idat = 8, None, b""
            while pos < len(png):
                n = struct.unpack(">I", png[pos:pos + 4])[0]
                tag, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + n]
                if tag == b"IHDR":
                    ihdr = data
                elif tag == b"IDAT":
                    idat += data
                pos += 12 + n
            frames.append((entry[:8], ihdr, zlib.decompress(idat)))
        return frames

    def test_committed_icon_is_current(self):
        _svg, ico = self.mi.build(self.d)
        with open(ico, "rb") as a, open(os.path.join(REPO, "ui", "app.ico"), "rb") as b:
            built, committed = self._ico_frames(a.read()), self._ico_frames(b.read())
        self.assertEqual(len(built), 7)
        self.assertEqual(built, committed, "run python tools/make_icon.py and commit ui/app.ico")
        with open(os.path.join(REPO, "ui", "index.html"), encoding="utf-8") as f:
            self.assertIn('rel="icon" href="icon.svg"', f.read())


class LauncherFilesTest(unittest.TestCase):
    def _bat(self, name):
        with open(os.path.join(REPO, name), "rb") as f:
            return f.read()

    def test_no_unix_null_device(self):
        for name in ("run_tests.bat", "Launch Sample Organizer.bat", "Create Desktop Shortcut.bat"):
            self.assertNotIn(b"/dev/null", self._bat(name), name)

    def test_crlf_and_contents(self):
        for name in ("Launch Sample Organizer.bat", "Create Desktop Shortcut.bat"):
            b = self._bat(name)
            self.assertNotIn(b"\n", b.replace(b"\r\n", b""), f"{name} must use CRLF line endings")
        sc = self._bat("Create Desktop Shortcut.bat").decode()
        for needle in ("pythonw.exe", "WScript.Shell", "-m samplelib", r"ui\app.ico", "Sample Organizer.lnk"):
            self.assertIn(needle, sc)
        self.assertIn("troubleshooting", self._bat("Launch Sample Organizer.bat").decode())


@unittest.skipUnless(IS_WIN, "Windows only")
class WindowsDesktopTest(unittest.TestCase):
    def test_shortcut_bat_creates_lnk(self):
        d = tempfile.mkdtemp(prefix="slo-desk-")
        try:
            env = dict(os.environ, SLO_SHORTCUT_DIR=d, SLO_NO_PAUSE="1")
            r = subprocess.run(["cmd", "/c", os.path.join(REPO, "Create Desktop Shortcut.bat")], env=env,
                               capture_output=True, text=True, timeout=120)
            lnk = os.path.join(d, "Sample Organizer.lnk")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.isfile(lnk), r.stdout + r.stderr)
            ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK);"
                  "Write-Output $s.TargetPath; Write-Output $s.Arguments; Write-Output $s.WorkingDirectory;"
                  "Write-Output $s.IconLocation")
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=dict(os.environ, LNK=lnk),
                                 capture_output=True, text=True, timeout=60).stdout.splitlines()
            self.assertTrue(out[0].lower().endswith("pythonw.exe"), out)
            self.assertEqual(os.path.normcase(out[0]), os.path.normcase(os.path.join(REPO, "python", "pythonw.exe")),
                             "the shortcut uses the bundled Python")
            self.assertEqual(out[1], "-m samplelib")
            self.assertEqual(os.path.normcase(out[2].rstrip("\\")), os.path.normcase(REPO))
            self.assertIn("app.ico", out[3])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_window_lookup_does_not_raise(self):
        hwnd = winapp.find_app_window()
        self.assertTrue(hwnd is None or isinstance(hwnd, int))
        self.assertIn(winapp.focus_app_window(), (True, False))


if __name__ == "__main__":
    unittest.main()

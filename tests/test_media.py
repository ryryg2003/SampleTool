"""Media feature: AIFF->WAV conversion, Range on the converted stream, peaks,
peak cache, download of original bytes, clipboard command, read-only guarantee.

Synthetic fixtures in temp folders only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import math
import os
import random
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from samplelib import api_media, audiocodec  # noqa: E402
from samplelib.server import serve  # noqa: E402
from samplelib.store import Store  # noqa: E402

SR = 44100


# --------------------------------------------------------------- builders
def int_samples(bits: int, frames: int, channels: int, seed: int = 1) -> list:
    """Interleaved signed ints spanning the full range (incl. both extremes)."""
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    rnd = random.Random(seed)
    out = []
    for i in range(frames):
        for c in range(channels):
            if i == 0:
                v = lo if c == 0 else hi
            elif i == 1:
                v = hi if c == 0 else lo
            else:
                v = int(math.sin(2 * math.pi * 440 * i / SR + c) * hi * 0.9) + rnd.randint(-3, 3)
                v = max(lo, min(hi, v))
            out.append(v)
    return out


def pack_ints(vals, width: int, big: bool) -> bytes:
    return b"".join(v.to_bytes(width, "big" if big else "little", signed=True) for v in vals)


def ext80(x: float) -> bytes:
    if x == 0:
        return b"\0" * 10
    exp = int(math.floor(math.log2(x)))
    mant = int(x * (2 ** (63 - exp)))
    return struct.pack(">HQ", exp + 16383, mant)


def _chunk(cid: bytes, payload: bytes) -> bytes:
    return cid + struct.pack(">I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def aiff_file(data: bytes, frames: int, channels: int, bits: int, comp: bytes | None = None,
              ssnd_offset: int = 0, extra_before: bytes = b"", sr: float = SR) -> bytes:
    comm = struct.pack(">hIh", channels, frames, bits) + ext80(sr)
    if comp is not None:
        comm += comp + b"\x00\x00"  # compression type + empty pstring (padded)
    ssnd = struct.pack(">II", ssnd_offset, 0) + b"\xAB" * ssnd_offset + data
    body = (b"AIFC" if comp is not None else b"AIFF") + _chunk(b"COMM", comm) + extra_before + _chunk(b"SSND", ssnd)
    return b"FORM" + struct.pack(">I", len(body)) + body


def expected_wav(vals, width: int, channels: int, sr: int = SR) -> bytes:
    if width == 1:
        frames = bytes((v + 128) & 0xFF for v in vals)
    else:
        frames = pack_ints(vals, width, big=False)
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(sr)
        w.writeframes(frames)
    return bio.getvalue()


def wav_file(vals, width: int, channels: int, sr: int = SR) -> bytes:
    return expected_wav(vals, width, channels, sr)


def float_wav(samples, channels=1, sr=SR) -> bytes:
    data = struct.pack("<%df" % len(samples), *samples)
    fmt = struct.pack("<HHIIHH", 3, channels, sr, sr * 4 * channels, 4 * channels, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def extensible_wav(vals, width, channels, sr=SR) -> bytes:
    data = pack_ints(vals, width, big=False)
    ba = width * channels
    guid = struct.pack("<H", 1) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xAA\x00\x38\x9B\x71"
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, sr, sr * ba, ba, width * 8, 22, width * 8, 3) + guid
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def wav_frames(b: bytes):
    with wave.open(io.BytesIO(b), "rb") as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes(), w.readframes(w.getnframes())


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def snapshot(top):
    out = {}
    for dp, dn, fn in os.walk(top):
        for d in dn:
            out[os.path.join(dp, d)] = ("dir", os.lstat(os.path.join(dp, d)).st_mtime_ns)
        for n in fn:
            p = os.path.join(dp, n)
            st = os.lstat(p)
            out[p] = (st.st_size, st.st_mtime_ns, sha(p))
    out[top] = ("dir", os.lstat(top).st_mtime_ns)
    return out


# ------------------------------------------------------------ unit tests
class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slo-media-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def check_variant(self, bits, channels, comp=None, big=True, ssnd_offset=0, frames=777):
        width = (bits + 7) // 8
        vals = int_samples(bits, frames, channels, seed=bits * 10 + channels)
        raw = pack_ints(vals, width, big=big) if width > 1 else bytes(v & 0xFF for v in vals)
        p = self.write(f"t{bits}_{channels}_{comp}.aif", aiff_file(raw, frames, channels, bits, comp, ssnd_offset))
        got = audiocodec.convert_aiff_to_wav(p)
        src = audiocodec.probe_aiff(p)
        self.assertEqual(len(got), audiocodec.wav_size(src))
        exp = expected_wav(vals, width, channels)
        g, e = wav_frames(got), wav_frames(exp)
        self.assertEqual(g[:4], e[:4], f"params {bits}-bit {comp}")
        self.assertEqual(g[4], e[4], f"frames differ {bits}-bit {comp}")
        self.assertEqual(got, exp, "whole WAV byte-identical to the wave module's output")
        return p, got

    def test_aiff_16_24_8_32_bit(self):
        for bits in (16, 24, 8, 32):
            for ch in (1, 2):
                self.check_variant(bits, ch)

    def test_aifc_sowt_and_none(self):
        self.check_variant(16, 2, comp=b"sowt", big=False)
        self.check_variant(24, 1, comp=b"sowt", big=False)
        self.check_variant(16, 2, comp=b"NONE", big=True)

    def test_odd_ssnd_offset_and_odd_chunk_before(self):
        width, frames, ch = 2, 301, 1
        vals = int_samples(16, frames, ch)
        anno = _chunk(b"ANNO", b"odd")  # odd payload -> pad byte
        p = self.write("odd.aif", aiff_file(pack_ints(vals, width, True), frames, ch, 16, None, 3, anno))
        got = audiocodec.convert_aiff_to_wav(p)
        self.assertEqual(wav_frames(got)[4], wav_frames(expected_wav(vals, width, ch))[4])

    def test_fl32(self):
        vals = [math.sin(i / 7) * 0.8 for i in range(500)]
        raw = struct.pack(">%df" % len(vals), *vals)
        p = self.write("f.aif", aiff_file(raw, len(vals), 1, 32, b"fl32"))
        got = audiocodec.convert_aiff_to_wav(p)
        self.assertEqual(struct.unpack("<H", got[20:22])[0], 3)  # IEEE float
        self.assertEqual(got[44:], struct.pack("<%df" % len(vals), *vals))

    def test_unsupported_compression(self):
        p = self.write("ima.aif", aiff_file(b"\0" * 68, 64, 1, 16, b"ima4"))
        with self.assertRaises(audiocodec.Unsupported) as cm:
            audiocodec.probe_aiff(p)
        self.assertIn("ima4", str(cm.exception))

    def test_ranges_concatenate(self):
        p, full = self.check_variant(24, 2, frames=5000)
        src = audiocodec.probe_aiff(p)
        size = len(full)
        rnd = random.Random(7)
        cuts = sorted(set([0, size] + [rnd.randrange(1, size) for _ in range(40)] + [1, 43, 44, 45, 47]))
        parts = []
        for a, b in zip(cuts, cuts[1:]):
            chunk = b"".join(audiocodec.wav_range(src, p, a, b - 1, chunk=999))
            self.assertEqual(chunk, full[a:b], f"range {a}-{b - 1}")
            parts.append(chunk)
        self.assertEqual(b"".join(parts), full)

    def test_fixture_aif_auditions(self):
        spec = importlib.util.spec_from_file_location("make_fixtures", os.path.join(REPO, "tools", "make_fixtures.py"))
        mf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mf)
        root = os.path.join(self.tmp, "fx")
        mf.build(root)
        found = [os.path.join(d, n) for d, _, fs in os.walk(root) for n in fs if n == "Bass Hit G.aif"]
        self.assertEqual(len(found), 1)
        got = audiocodec.convert_aiff_to_wav(found[0])
        ch, w, sr, n, frames = wav_frames(got)
        self.assertEqual((ch, w, sr), (1, 2, mf.SR))
        self.assertGreater(n, 1000)
        pk = audiocodec.compute_peaks(found[0], ".aif", 200)
        self.assertGreater(max(p[1] for p in pk["peaks"]), 0.3)

    # ------------------------------------------------------------- peaks
    def test_peaks_wav_and_aiff_sine(self):
        frames, amp = SR, 0.5
        vals16 = [int(math.sin(2 * math.pi * 220 * i / SR) * amp * 32767) for i in range(frames)]
        for name, data, ext in (
            ("s16.wav", wav_file(vals16, 2, 1), ".wav"),
            ("s16.aif", aiff_file(pack_ints(vals16, 2, True), frames, 1, 16), ".aif"),
            ("s24.wav", wav_file([v * 256 for v in vals16], 3, 1), ".wav"),
            ("s32x.wav", extensible_wav([v * 65536 for v in vals16], 4, 1), ".wav"),
            ("u8.wav", wav_file([v >> 8 for v in vals16], 1, 1), ".wav"),
            ("f32.wav", float_wav([v / 32767 for v in vals16]), ".wav"),
        ):
            p = self.write(name, data)
            d = audiocodec.compute_peaks(p, ext, 400)
            self.assertEqual(d["n"], 400, name)
            self.assertEqual(len(d["peaks"]), 400, name)
            self.assertAlmostEqual(d["duration_s"], 1.0, places=3)
            for lo, hi in d["peaks"]:
                self.assertTrue(-1 <= lo <= hi <= 1, (name, lo, hi))
            self.assertAlmostEqual(max(p[1] for p in d["peaks"]), amp, delta=0.02, msg=name)
            self.assertAlmostEqual(min(p[0] for p in d["peaks"]), -amp, delta=0.02, msg=name)

    def test_peaks_unsupported(self):
        p = self.write("x.mp3", b"ID3" + b"\0" * 100)
        for ext in (".mp3", ".flac", ".ogg", ".m4a", ".rex", ".rx2"):
            with self.assertRaises(audiocodec.Unsupported):
                audiocodec.compute_peaks(p, ext, 100)
        p = self.write("adpcm.wav", b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 2, 1, SR, SR, 1, 4) + b"data\0\0\0\0")
        with self.assertRaises(audiocodec.Unsupported):
            audiocodec.compute_peaks(p, ".wav", 100)

    def test_peaks_long_file_is_bounded(self):
        # 10 minutes of stereo 24-bit 48 kHz (~173 MB) as a sparse file
        sr, ch, w = 48000, 2, 3
        frames = sr * 600
        data_len = frames * ch * w
        p = os.path.join(self.tmp, "long.wav")
        with open(p, "wb") as f:
            hdr = expected_wav([], w, ch, sr)[:36]
            f.write(b"RIFF" + struct.pack("<I", 36 + data_len) + hdr[8:36] + b"data" + struct.pack("<I", data_len))
            f.write(pack_ints([4_000_000, -4_000_000] * 1000, w, False))
            f.truncate(44 + data_len)
        t0 = time.perf_counter()
        d = audiocodec.compute_peaks(p, ".wav", 800)
        dt = time.perf_counter() - t0
        self.assertEqual(d["n"], 800)
        self.assertAlmostEqual(d["duration_s"], 600.0, places=3)
        self.assertAlmostEqual(d["peaks"][0][1], 4_000_000 / 8388608, places=3)
        self.assertLess(dt, 2.0, f"peaks took {dt:.3f}s")  # typically ~0.1-0.3 s

    def test_clipboard_command_is_safe(self):
        nasty = r"C:\Samples\a'b \"c\"; $(rm -rf) & [x].wav"
        argv, env = api_media.clipboard_command(nasty)
        self.assertEqual(argv[:4], ["powershell", "-NoProfile", "-NonInteractive", "-Command"])
        self.assertNotIn(nasty, " ".join(argv))
        self.assertIn("-LiteralPath", argv[4])
        self.assertEqual(env[api_media.CLIP_ENV], nasty)


# ---------------------------------------------------------- HTTP tests
class MediaHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="slo-media-http-")
        cls.root = os.path.join(cls.tmp, "Packs")
        cls.data = os.path.join(cls.tmp, "appdata")
        os.makedirs(os.path.join(cls.root, "Pack A"))
        frames = 4000
        cls.vals = int_samples(16, frames, 2)
        cls.files = {
            "Pack A/Bass Hit.aif": aiff_file(pack_ints(cls.vals, 2, True), frames, 2, 16, None, 5),
            "Pack A/Kick.wav": wav_file(int_samples(16, 3000, 1), 2, 1),
            "Pack A/Loop.mp3": b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\xc4" + b"\0" * 413,
            "Pack A/Break.rx2": b"CAT " + b"\0" * 60,
            "Pack A/Weird.aif": aiff_file(b"\0" * 64, 32, 1, 16, b"ima4"),
        }
        for rel, data in cls.files.items():
            with open(os.path.join(cls.root, rel), "wb") as f:
                f.write(data)
        cls.store = Store(cls.data, cls.root)
        cls.httpd, cls.app = serve(cls.store, port=0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        cls.req("POST", "/api/scan", {})
        t0 = time.time()
        while cls.req("GET", "/api/progress")[2].get("running"):
            if time.time() - t0 > 60:
                raise AssertionError("scan timeout")
            time.sleep(0.05)
        rows = cls.req("GET", "/api/samples?kind=audio&limit=100")[2]["items"]
        cls.by = {r["rel_path"]: r for r in rows}
        cls.snap0 = snapshot(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def req(cls, method, path, body=None, headers=None):
        h = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.update({"Content-Type": "application/json", "X-SLO": "1"})
        r = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                raw, code, hd = resp.read(), resp.status, {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as e:
            raw, code, hd = e.read(), e.code, {k.lower(): v for k, v in e.headers.items()}
        try:
            j = json.loads(raw) if "json" in hd.get("content-type", "") else raw
        except ValueError:
            j = raw
        return code, hd, j

    def test_aiff_row_is_previewable(self):
        self.assertEqual(self.by["Pack A/Bass Hit.aif"]["preview"], "native")
        self.assertEqual(self.by["Pack A/Break.rx2"]["preview"], "unsupported")
        # old inventories stored "unsupported" for .aif: row-time decision overrides it
        it = self.store.by_id[self.by["Pack A/Bass Hit.aif"]["id"]]
        old = it.get("preview")
        try:
            it["preview"] = "unsupported"
            self.assertEqual(self.store.row(it)["preview"], "native")
        finally:
            it["preview"] = old

    def test_converted_stream_and_ranges(self):
        iid = self.by["Pack A/Bass Hit.aif"]["id"]
        code, h, full = self.req("GET", f"/api/audio/{iid}")
        self.assertEqual(code, 200)
        self.assertEqual(h["content-type"], "audio/wav")
        self.assertEqual(h["accept-ranges"], "bytes")
        self.assertEqual(int(h["content-length"]), len(full))
        self.assertEqual(len(full), 44 + len(self.vals) * 2)
        self.assertEqual(wav_frames(full)[4], wav_frames(expected_wav(self.vals, 2, 2))[4])
        size = len(full)
        rnd = random.Random(3)
        cuts = sorted(set([0, size] + [rnd.randrange(1, size) for _ in range(12)] + [44, 45]))
        parts = []
        for a, b in zip(cuts, cuts[1:]):
            code, h, body = self.req("GET", f"/api/audio/{iid}", headers={"Range": f"bytes={a}-{b - 1}"})
            self.assertEqual(code, 206)
            self.assertEqual(h["content-range"], f"bytes {a}-{b - 1}/{size}")
            self.assertEqual(int(h["content-length"]), b - a)
            parts.append(body)
        self.assertEqual(b"".join(parts), full)
        code, h, body = self.req("GET", f"/api/audio/{iid}", headers={"Range": "bytes=-10"})
        self.assertEqual((code, body), (206, full[-10:]))
        code, h, body = self.req("GET", f"/api/audio/{iid}", headers={"Range": f"bytes={size - 5}-"})
        self.assertEqual((code, body), (206, full[-5:]))
        code, h, _ = self.req("GET", f"/api/audio/{iid}", headers={"Range": f"bytes={size}-"})
        self.assertEqual(code, 416)
        self.assertEqual(h["content-range"], f"bytes */{size}")

    def test_unsupported_aifc_reports(self):
        iid = self.by["Pack A/Weird.aif"]["id"]
        code, _, j = self.req("GET", f"/api/audio/{iid}")
        self.assertEqual(code, 415)
        self.assertIn("ima4", j["error"])
        code, _, j = self.req("GET", f"/api/peaks/{iid}?n=100")
        self.assertEqual(code, 200)
        self.assertTrue(j["unsupported"])
        self.assertIn("ima4", j["reason"])

    def test_download_returns_original_bytes(self):
        for rel in ("Pack A/Bass Hit.aif", "Pack A/Kick.wav", "Pack A/Break.rx2"):
            iid = self.by[rel]["id"]
            code, h, body = self.req("GET", f"/api/audio/{iid}?download=1")
            self.assertEqual(code, 200, rel)
            self.assertEqual(body, self.files[rel], rel)
            name = rel.split("/")[-1]
            self.assertIn("attachment", h["content-disposition"])
            self.assertIn(urllib.parse.quote(name), h["content-disposition"])
            self.assertEqual(int(h["content-length"]), len(self.files[rel]))
        iid = self.by["Pack A/Bass Hit.aif"]["id"]
        code, h, body = self.req("GET", f"/api/audio/{iid}?download=1", headers={"Range": "bytes=0-11"})
        self.assertEqual((code, body), (206, self.files["Pack A/Bass Hit.aif"][:12]))

    def test_peaks_endpoint_and_cache(self):
        iid = self.by["Pack A/Bass Hit.aif"]["id"]
        code, _, j = self.req("GET", f"/api/peaks/{iid}?n=200")
        self.assertEqual(code, 200)
        self.assertEqual(j["id"], iid)
        self.assertEqual(j["n"], 200)
        self.assertFalse(j.get("unsupported"))
        self.assertTrue(all(-1 <= a <= b <= 1 for a, b in j["peaks"]))
        sha256 = self.store.by_id[iid]["sha256"]
        cache = audiocodec.peaks_cache_path(self.data, sha256, 200)
        self.assertTrue(os.path.isfile(cache))
        self.assertTrue(cache.startswith(os.path.join(self.data, "peaks")))
        # second call must come from the cache: computing (or opening the file) would fail
        orig_compute = audiocodec.compute_peaks
        path = self.store.by_id[iid]["abs_path"]
        import builtins
        real_open = builtins.open

        def guarded_open(p, *a, **k):
            if os.fspath(p) == path:
                raise AssertionError("sample file reopened")
            return real_open(p, *a, **k)

        def boom(*a, **k):
            raise AssertionError("peaks recomputed")
        audiocodec.compute_peaks = boom
        builtins.open = guarded_open
        try:
            code, _, j2 = self.req("GET", f"/api/peaks/{iid}?n=200")
        finally:
            audiocodec.compute_peaks = orig_compute
            builtins.open = real_open
        self.assertEqual(code, 200)
        self.assertTrue(j2["cached"])
        self.assertEqual(j2["peaks"], j["peaks"])

    def test_peaks_wav_and_unsupported(self):
        code, _, j = self.req("GET", f"/api/peaks/{self.by['Pack A/Kick.wav']['id']}")
        self.assertEqual((code, j["n"]), (200, 800))
        for rel in ("Pack A/Loop.mp3", "Pack A/Break.rx2"):
            code, _, j = self.req("GET", f"/api/peaks/{self.by[rel]['id']}")
            self.assertEqual(code, 200)
            self.assertTrue(j["unsupported"], rel)
            self.assertTrue(j["reason"])
        code, _, _ = self.req("GET", "/api/peaks/doesnotexist")
        self.assertEqual(code, 404)

    def test_clipboard_endpoint(self):
        iid = self.by["Pack A/Kick.wav"]["id"]
        calls = []

        class R:
            returncode, stdout, stderr = 0, b"", b""

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            return R()
        orig_run, orig_win = api_media.subprocess.run, api_media.is_windows
        try:
            api_media.is_windows = lambda: False
            code, _, j = self.req("POST", "/api/clipboard/file", {"id": iid})
            self.assertEqual(code, 409)
            self.assertIn("Windows only", j["error"])
            api_media.is_windows = lambda: True
            api_media.subprocess.run = fake_run
            code, _, j = self.req("POST", "/api/clipboard/file", {"id": iid})
            self.assertEqual((code, j), (200, {"ok": True}))
        finally:
            api_media.subprocess.run, api_media.is_windows = orig_run, orig_win
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertIsInstance(argv, list)
        self.assertFalse(kw.get("shell"))
        p = self.store.by_id[iid]["abs_path"]
        self.assertEqual(kw["env"][api_media.CLIP_ENV], p)
        self.assertNotIn(p, " ".join(argv))
        code, _, _ = self.req("POST", "/api/clipboard/file", {})
        self.assertEqual(code, 400)

    def test_zz_state_and_read_only(self):
        code, _, s = self.req("GET", "/api/state")
        self.assertIn("media", s)
        # every request above was read-only: the collection is byte- and mtime-identical
        for rel in self.by:
            iid = self.by[rel]["id"]
            self.req("GET", f"/api/audio/{iid}", headers={"Range": "bytes=10-200"})
            self.req("GET", f"/api/peaks/{iid}?n=64")
        self.assertEqual(snapshot(self.root), self.snap0)
        for dp, _, fn in os.walk(self.root):
            self.assertFalse([n for n in fn if n.endswith((".json", ".tmp"))], dp)

if __name__ == "__main__":
    unittest.main()

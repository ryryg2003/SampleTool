"""Freesound integration tests. No network: every call goes through a fake transport.

OWNER: findsounds feature.
"""
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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from samplelib import freesound  # noqa: E402

KEY = "abcdEFGH1234567890wxyz"


def sound(sid, lic="Creative Commons 0", user="alice", name="kick.wav", typ="wav"):
    return {"id": sid, "name": name, "username": user, "duration": 0.5, "license": lic,
            "previews": {"preview-hq-mp3": f"https://cdn.freesound.org/previews/{sid}_hq.mp3"},
            "url": f"https://freesound.org/people/{user}/sounds/{sid}/", "tags": ["kick", "drum"],
            "bpm": None, "tonality": "A minor", "type": typ, "samplerate": 44100, "channels": 1, "filesize": 1000}


class FakeTransport:
    """Routes by URL prefix. Each route value: callable(method, url, headers, body) -> (status, headers, bytes)."""

    def __init__(self):
        self.calls = []
        self.routes = []

    def on(self, prefix, fn):
        self.routes.insert(0, (prefix, fn))

    def json(self, prefix, obj, status=200, headers=None):
        self.on(prefix, lambda m, u, h, b: (status, headers or {}, json.dumps(obj).encode()))

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        for prefix, fn in self.routes:
            if url.startswith(prefix):
                return fn(method, url, headers, body)
        return 404, {}, b'{"detail": "no route"}'


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slo-fs-")
        self.data = os.path.join(self.tmp, "data")
        self.root = os.path.join(self.tmp, "Sample Packs")
        os.makedirs(self.root)
        self.tr = FakeTransport()
        self.clock = Clock()
        self.wall = Clock()
        self.wall.t = 1_800_000_000.0
        self.day = ["2026-11-16"]
        self.c = self.make()

    def make(self):
        return freesound.Client(self.data, transport=self.tr, clock=self.clock, wall=self.wall,
                                today=lambda: self.day[0])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def search_url(self):
        return [c for c in self.tr.calls if "/apiv2/search/" in c["url"]][-1]["url"]


class TestSearch(Base):
    def setUp(self):
        super().setUp()
        self.c.set_key(KEY)
        self.tr.json("https://freesound.org/apiv2/search/", {
            "count": 4, "next": "https://freesound.org/apiv2/search/?page=2", "previous": None,
            "results": [sound(1), sound(2, "Attribution"), sound(3, "Attribution NonCommercial"),
                        sound(4, "http://creativecommons.org/licenses/by-nc/3.0/"),
                        sound(5, "http://creativecommons.org/publicdomain/zero/1.0/")]})

    def test_default_is_cc0_only_and_url_is_correct(self):
        r = self.c.search({"q": "kick", "tag": "kick", "bpm_min": "90", "bpm_max": "120", "key": "Am",
                           "type": "oneshot"})
        u = urllib.parse.urlsplit(self.search_url())
        self.assertEqual((u.scheme, u.netloc, u.path), ("https", "freesound.org", "/apiv2/search/"))
        q = urllib.parse.parse_qs(u.query)
        self.assertEqual(q["query"], ["kick"])
        self.assertEqual(q["filter"], ['license:"Creative Commons 0" tag:kick duration:[* TO 3] bpm:[90 TO 120] tonality:"A minor"'])
        self.assertIn("previews", q["fields"][0])
        self.assertNotIn("token", q)  # key goes in a header, never the URL
        self.assertEqual(self.tr.calls[-1]["headers"]["Authorization"], "Token " + KEY)
        ids = [x["id"] for x in r["results"]]
        self.assertEqual(ids, [1, 5])  # Attribution not opted in, NonCommercial never
        self.assertEqual(r["dropped"], 3)
        self.assertTrue(r["next"])
        self.assertTrue(all(x["license"] == "cc0" for x in r["results"]))
        self.assertEqual(r["results"][0]["preview_url"], "/api/freesound/preview/1")

    def test_attribution_opt_in_still_drops_noncommercial(self):
        r = self.c.search({"q": "kick", "attribution": "1"})
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.search_url()).query)
        self.assertEqual(q["filter"], ['license:("Creative Commons 0" OR "Attribution")'])
        self.assertEqual(sorted(x["id"] for x in r["results"]), [1, 2, 5])
        self.assertEqual({x["license"] for x in r["results"]}, {"cc0", "by"})

    def test_loop_and_duration_filters(self):
        self.c.search({"type": "loop", "dur_min": "1", "dur_max": "8.5"})
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.search_url()).query)
        self.assertEqual(q["filter"], ['license:"Creative Commons 0" tag:loop duration:[1 TO 8.5]'])

    def test_bad_input_rejected(self):
        with self.assertRaises(ValueError):
            self.c.search({"sort": "evil"})
        with self.assertRaises(ValueError):
            self.c.search({"bpm_min": "abc"})
        with self.assertRaises(ValueError):
            self.c.search({"key": "H#"})

    def test_filter_injection_is_neutralised(self):
        f = freesound.build_filter({"tag": 'a"b) OR license:"Attribution NonCommercial'})
        self.assertTrue(f.startswith('license:"Creative Commons 0" '))
        self.assertNotIn('"Attribution NonCommercial"', f)

    def test_no_key(self):
        self.c.set_key("")
        with self.assertRaises(freesound.NoKey):
            self.c.search({"q": "x"})


class TestRateLimit(Base):
    def setUp(self):
        super().setUp()
        self.c.set_key(KEY)

    def test_429_honours_retry_after_and_blocks(self):
        self.tr.json("https://freesound.org/apiv2/search/", {"detail": "Request was throttled (minute limit)"},
                     status=429, headers={"retry-after": "42"})
        with self.assertRaises(freesound.RateLimited) as cm:
            self.c.search({"q": "a"})
        self.assertEqual(cm.exception.retry_after_s, 42)
        self.assertIn("42 s", str(cm.exception))
        n = len(self.tr.calls)
        self.clock.t += 30
        with self.assertRaises(freesound.RateLimited) as cm2:
            self.c.search({"q": "a"})
        self.assertEqual(len(self.tr.calls), n, "no request may leave while blocked")
        self.assertEqual(cm2.exception.retry_after_s, 12)
        self.assertEqual(self.c.quota()["retry_after_s"], 12)
        self.clock.t += 13
        self.tr.json("https://freesound.org/apiv2/search/", {"count": 0, "results": []})
        self.c.search({"q": "a"})
        self.assertEqual(len(self.tr.calls), n + 1)
        self.assertIsNone(self.c.quota()["retry_after_s"])

    def test_429_without_retry_after_waits_a_minute(self):
        self.tr.json("https://freesound.org/apiv2/search/", {"detail": "throttled"}, status=429)
        with self.assertRaises(freesound.RateLimited) as cm:
            self.c.search({"q": "a"})
        self.assertEqual(cm.exception.retry_after_s, 60)

    def test_token_bucket_stays_under_minute_limit(self):
        self.tr.json("https://freesound.org/apiv2/search/", {"count": 0, "results": []})
        for _ in range(freesound.API_PER_MIN):
            self.c.search({"q": "a"})
        with self.assertRaises(freesound.RateLimited):
            self.c.search({"q": "a"})
        self.assertEqual(len(self.tr.calls), freesound.API_PER_MIN)
        self.assertLess(freesound.API_PER_MIN, 60)
        self.clock.t += 2
        self.c.search({"q": "a"})
        self.assertEqual(self.c.quota()["minute_remaining"], 0)

    def test_daily_quota_persists_across_restarts(self):
        self.tr.json("https://freesound.org/apiv2/search/", {"count": 0, "results": []})
        for _ in range(3):
            self.c.search({"q": "a"})
        c2 = self.make()
        self.assertEqual(c2.quota()["day_used"], 3)
        self.assertEqual(c2.quota()["day_limit"], 2000)
        c2.usage["api"] = freesound.API_PER_DAY
        with self.assertRaises(freesound.RateLimited) as cm:
            c2.search({"q": "a"})
        self.assertIn("Daily", str(cm.exception))
        self.day[0] = "2026-11-17"
        self.assertEqual(c2.quota()["day_used"], 0)

    def test_offline(self):
        def boom(*a):
            raise freesound.Offline("Could not reach freesound.org (no network).")
        self.tr.on("https://", boom)
        with self.assertRaises(freesound.Offline):
            self.c.search({"q": "a"})
        self.assertIs(self.c.status()["online"], False)


class TestKey(Base):
    def test_key_stored_in_data_dir_masked(self):
        r = self.c.set_key(KEY)
        self.assertEqual(r, {"ok": True, "has_key": True, "key_hint": "abcd…wxyz"})
        p = os.path.join(self.data, "freesound.json")
        self.assertTrue(os.path.isfile(p))
        self.assertEqual(json.load(open(p))["api_key"], KEY)
        st = self.c.status()
        self.assertNotIn(KEY, json.dumps(st))
        self.assertEqual(st["key_hint"], "abcd…wxyz")
        self.assertEqual(set(st["quota"]) >= {"minute_remaining", "day_used", "day_limit", "retry_after_s"}, True)
        self.assertEqual(self.make().status()["has_key"], True)
        self.c.set_key("")
        self.assertEqual(self.make().status(), dict(self.make().status(), has_key=False, key_hint=None))
        with self.assertRaises(ValueError):
            self.c.set_key("bad key with spaces")

    def test_key_not_in_repo(self):
        self.c.set_key(KEY)
        for dp, _dn, fn in os.walk(REPO):
            if ".git" in dp:
                continue
            for n in fn:
                if n == "freesound.json":
                    self.fail("freesound.json found in the repository: " + os.path.join(dp, n))


class TestDownload(Base):
    def setUp(self):
        super().setUp()
        self.c.set_key(KEY)
        self.tr.json("https://freesound.org/apiv2/sounds/7/", sound(7, user="../../evil", name="..\\..\\boom.wav"))
        self.tr.json("https://freesound.org/apiv2/sounds/8/", sound(8, user="Bob", name="Kick.wav"))
        self.tr.json("https://freesound.org/apiv2/sounds/9/", sound(9, lic="Attribution NonCommercial"))
        self.tr.on("https://cdn.freesound.org/previews/", lambda m, u, h, b: (200, {}, b"ID3mp3data"))

    def test_preview_download_writes_sidecar_and_never_overwrites(self):
        r1 = self.c.download(self.root, 8, "preview")
        folder = os.path.join(self.root, "_Freesound", "Bob")
        self.assertEqual(r1["path"], os.path.join(os.path.realpath(folder), "Kick (preview).mp3"))
        self.assertEqual(open(r1["path"], "rb").read(), b"ID3mp3data")
        side = open(r1["sidecar"], encoding="utf-8").read()
        for s in ("https://freesound.org/people/Bob/sounds/8/", "Kick.wav", "Bob", "Creative Commons 0",
                  "https://creativecommons.org/publicdomain/zero/1.0/", "Attribution line", "2027-01-15"):
            self.assertIn(s, side)
        # same name, different case already present -> suffix, original untouched
        os.rename(r1["path"], os.path.join(folder, "KICK (PREVIEW).mp3"))
        os.rename(r1["sidecar"], os.path.join(folder, "KICK (PREVIEW).mp3.license.txt"))
        r2 = self.c.download(self.root, 8, "preview")
        self.assertEqual(os.path.basename(r2["path"]), "Kick (preview) (2).mp3")
        r3 = self.c.download(self.root, 8, "preview")
        self.assertEqual(os.path.basename(r3["path"]), "Kick (preview) (3).mp3")
        self.assertEqual(sorted(os.listdir(folder)), sorted([
            "KICK (PREVIEW).mp3", "KICK (PREVIEW).mp3.license.txt", "Kick (preview) (2).mp3",
            "Kick (preview) (2).mp3.license.txt", "Kick (preview) (3).mp3", "Kick (preview) (3).mp3.license.txt"]))
        # the preview CDN never sees the API key
        cdn = [c for c in self.tr.calls if "cdn.freesound.org" in c["url"]]
        self.assertTrue(cdn and all("Authorization" not in c["headers"] for c in cdn))
        # nothing outside _Freesound
        self.assertEqual(os.listdir(self.root), ["_Freesound"])

    def test_path_traversal_contained(self):
        r = self.c.download(self.root, 7, "preview")
        base = os.path.realpath(os.path.join(self.root, "_Freesound"))
        self.assertEqual(os.path.dirname(os.path.dirname(r["path"])), base)
        self.assertNotIn("..", os.path.relpath(r["path"], base).split(os.sep)[0].strip("_."))
        self.assertEqual(os.listdir(self.tmp).count("evil"), 0)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["Sample Packs", "data"])
        for u in ("..", ".", "a/../../b", "CON", "x" * 300, ""):
            res = freesound.write_download(self.root, u, "../../n.wav", b"x", "s")
            self.assertTrue(res["path"].startswith(base + os.sep), res["path"])
            self.assertEqual(os.path.dirname(os.path.dirname(res["path"])), base)

    def test_noncommercial_refused(self):
        with self.assertRaises(PermissionError):
            self.c.download(self.root, 9, "preview")
        self.assertFalse(os.path.exists(os.path.join(self.root, "_Freesound")))

    def test_original_requires_oauth(self):
        with self.assertRaises(freesound.NotConnected):
            self.c.download(self.root, 8, "original")


class TestOAuth(Base):
    def setUp(self):
        super().setUp()
        self.c.set_key(KEY, client_id="myclientid123")
        self.tokens = []

        def token(m, u, h, b):
            form = urllib.parse.parse_qs(b.decode())
            self.tokens.append(form)
            if form["grant_type"] == ["authorization_code"]:
                if form["code"] != ["goodcode"]:
                    return 400, {}, b'{"error": "invalid_grant"}'
                return 200, {}, json.dumps({"access_token": "AT1", "refresh_token": "RT1", "expires_in": 86399,
                                            "scope": "read write"}).encode()
            if form["refresh_token"] == ["RT1"]:
                return 200, {}, json.dumps({"access_token": "AT2", "refresh_token": "RT2", "expires_in": 86399}).encode()
            return 400, {}, b'{"error": "invalid_grant"}'
        self.tr.on("https://freesound.org/apiv2/oauth2/access_token/", token)
        self.tr.json("https://freesound.org/apiv2/sounds/8/?", sound(8, user="Bob", name="Kick", typ="wav"))

        def dl(m, u, h, b):
            if h.get("Authorization") == "Bearer AT2":
                return 302, {"location": "https://cdn.freesound.org/sounds/8.wav?sig=1"}, b""
            if h.get("Authorization") == "Bearer AT1":
                return 401, {}, b'{"detail": "Expired token"}'
            return 403, {}, b'{"detail": "no"}'
        self.tr.on("https://freesound.org/apiv2/sounds/8/download/", dl)
        self.tr.on("https://cdn.freesound.org/sounds/8.wav", lambda m, u, h, b: (200, {}, b"RIFFdata"))

    def test_pkce_flow_exchange_and_refresh(self):
        st = self.c.oauth_start("http://127.0.0.1:8765/api/freesound/oauth/callback")
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(st["authorize_url"]).query)
        self.assertTrue(st["authorize_url"].startswith("https://freesound.org/apiv2/oauth2/authorize/?"))
        self.assertEqual(q["client_id"], ["myclientid123"])
        self.assertEqual(q["response_type"], ["code"])
        self.assertEqual(q["code_challenge_method"], ["S256"])
        self.assertEqual(q["redirect_uri"], ["http://127.0.0.1:8765/api/freesound/oauth/callback"])
        with self.assertRaises(PermissionError):
            self.c.oauth_finish("goodcode", "wrong-state")
        self.c.oauth_finish("goodcode", st["state"])
        f = self.tokens[-1]
        self.assertEqual(f["redirect_uri"], ["http://127.0.0.1:8765/api/freesound/oauth/callback"])
        import base64
        import hashlib
        ch = base64.urlsafe_b64encode(hashlib.sha256(f["code_verifier"][0].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(ch, q["code_challenge"][0])
        self.assertNotIn("client_secret", f)
        self.assertTrue(self.c.status()["oauth"]["connected"])
        with self.assertRaises(PermissionError):  # state is single-use
            self.c.oauth_finish("goodcode", st["state"])
        # access token AT1 is rejected as expired -> refresh to AT2 -> redirect to CDN without auth
        r = self.c.download(self.root, 8, "original")
        self.assertEqual(os.path.basename(r["path"]), "Kick.wav")
        self.assertEqual(open(r["path"], "rb").read(), b"RIFFdata")
        self.assertEqual(self.tokens[-1]["grant_type"], ["refresh_token"])
        cdn = [c for c in self.tr.calls if c["url"].startswith("https://cdn.freesound.org/sounds/")]
        self.assertTrue(cdn and "Authorization" not in cdn[0]["headers"])
        saved = json.load(open(os.path.join(self.data, "freesound.json")))
        self.assertEqual(saved["oauth"]["refresh_token"], "RT2")
        self.assertEqual(self.c.quota()["downloads_day_used"], 1)

    def test_expiry_triggers_refresh_before_call(self):
        st = self.c.oauth_start(None)
        self.assertNotIn("redirect_uri", st["authorize_url"])
        self.c.oauth_finish("goodcode", None)  # pasted code (manual mode)
        self.assertNotIn("redirect_uri", self.tokens[-1])
        self.wall.t += 90000
        self.c.download(self.root, 8, "original")
        self.assertEqual([t["grant_type"][0] for t in self.tokens], ["authorization_code", "refresh_token"])

    def test_bad_code_and_failed_refresh_disconnect(self):
        st = self.c.oauth_start(None)
        with self.assertRaises(freesound.NotConnected):
            self.c.oauth_finish("badcode", st["state"])
        self.assertFalse(self.c.status()["oauth"]["connected"])
        self.c.cred["oauth"] = {"access_token": "X", "refresh_token": "bogus", "expires_at": 0}
        with self.assertRaises(freesound.NotConnected):
            self.c.download(self.root, 8, "original")
        self.assertFalse(self.c.status()["oauth"]["connected"])


class TestServer(unittest.TestCase):
    """Through the real HTTP server with the transport mocked."""

    def setUp(self):
        from samplelib.server import serve
        from samplelib.store import Store
        from samplelib import api_freesound
        self.tmp = tempfile.mkdtemp(prefix="slo-fs-srv-")
        self.root = os.path.join(self.tmp, "Sample Packs")
        os.makedirs(self.root)
        self.store = Store(os.path.join(self.tmp, "data"), self.root)
        self.httpd, self.app = serve(self.store, port=0)
        self.tr = FakeTransport()
        self.app.freesound.transport = self.tr
        self.api = api_freesound
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json", "X-SLO": "1"} if method == "POST" else {}
        h.update(headers or {})
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method, headers=h,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def j(self, *a, **k):
        code, _h, b = self.req(*a, **k)
        return code, json.loads(b)

    def test_endpoints(self):
        code, st = self.j("GET", "/api/freesound/status")
        self.assertEqual(code, 200)
        self.assertEqual(st["has_key"], False)
        self.assertIsNone(st["key_hint"])
        self.assertEqual(set(st["oauth"]) >= {"connected", "available"}, True)
        self.assertEqual(set(st["quota"]) >= {"minute_remaining", "day_used", "day_limit", "retry_after_s"}, True)
        self.assertIn("online", st)
        code, r = self.j("GET", "/api/freesound/search?q=kick")
        self.assertEqual((code, r["code"]), (409, "no_key"))
        code, r = self.j("POST", "/api/freesound/key", {"key": KEY})
        self.assertEqual((code, r), (200, {"ok": True, "has_key": True, "key_hint": "abcd…wxyz"}))
        code, _h, raw = self.req("GET", "/api/freesound/status")
        self.assertNotIn(KEY.encode(), raw)
        code, _h, raw = self.req("GET", "/api/state")
        self.assertNotIn(KEY.encode(), raw)
        self.assertTrue(json.loads(raw)["freesound"]["has_key"])
        # search
        self.tr.json("https://freesound.org/apiv2/search/", {"count": 2, "next": None,
                                                              "results": [sound(1), sound(3, "Attribution NonCommercial")]})
        code, r = self.j("GET", "/api/freesound/search?q=kick&tag=kick&type=oneshot")
        self.assertEqual(code, 200)
        self.assertEqual([x["id"] for x in r["results"]], [1])
        self.assertEqual(r["dropped"], 1)
        self.assertIn("minute_remaining", r["quota"])
        code, r = self.j("GET", "/api/freesound/search?sort=bad")
        self.assertEqual(code, 400)
        # preview proxy with Range
        self.tr.on("https://cdn.freesound.org/previews/", lambda m, u, h, b: (200, {}, b"0123456789"))
        code, h, b = self.req("GET", "/api/freesound/preview/1")
        self.assertEqual((code, h["Content-Type"], b), (200, "audio/mpeg", b"0123456789"))
        code, h, b = self.req("GET", "/api/freesound/preview/1", headers={"Range": "bytes=2-4"})
        self.assertEqual((code, b, h["Content-Range"]), (206, b"234", "bytes 2-4/10"))
        # download (preview mode, only needs the key)
        code, r = self.j("POST", "/api/freesound/download", {"id": 1, "mode": "preview"})
        self.assertEqual(code, 200, r)
        self.assertTrue(os.path.isfile(r["path"]) and os.path.isfile(r["sidecar"]))
        self.assertTrue(r["path"].startswith(os.path.join(os.path.realpath(self.root), "_Freesound", "alice") + os.sep))
        code, r = self.j("POST", "/api/freesound/download", {"id": 1, "mode": "original"})
        self.assertEqual((code, r["code"]), (409, "oauth_required"))
        code, r = self.j("POST", "/api/freesound/download", {"id": "x"})
        self.assertEqual(code, 400)
        # CSRF guard still applies
        code, _h, _b = self.req("POST", "/api/freesound/key", {"key": ""}, headers={"X-SLO": "0"})
        self.assertEqual(code, 403)
        # 429 surfaces retry_after_s and quota
        self.tr.json("https://freesound.org/apiv2/search/", {"detail": "throttled"}, status=429,
                     headers={"retry-after": "17"})
        code, r = self.j("GET", "/api/freesound/search?q=x")
        self.assertEqual((code, r["code"], r["retry_after_s"]), (429, "rate_limited", 17))
        self.assertEqual(r["quota"]["retry_after_s"], 17)
        code, st = self.j("GET", "/api/freesound/status")
        self.assertEqual(st["quota"]["retry_after_s"], 17)
        # OAuth start uses the local callback; callback with bad state fails cleanly
        code, r = self.j("POST", "/api/freesound/oauth/start", {"mode": "local"})
        self.assertEqual((code, r["code"]), (409, "oauth_required"))  # no client id yet
        self.j("POST", "/api/freesound/key", {"client_id": "myclient1"})
        code, r = self.j("POST", "/api/freesound/oauth/start", {"mode": "local"})
        self.assertEqual(r["redirect_uri"], f"http://127.0.0.1:{self.port}/api/freesound/oauth/callback")
        code, _h, b = self.req("GET", "/api/freesound/oauth/callback?code=abcd&state=nope")
        self.assertEqual(code, 400)
        self.assertIn(b"sign-in", b)
        _c, st = self.j("GET", "/api/freesound/status")
        self.assertTrue(st["has_key"], "setting only client_id keeps the key")
        # clear
        code, r = self.j("POST", "/api/freesound/key", {"key": ""})
        self.assertEqual(r["has_key"], False)

    def test_download_refused_during_approved_run(self):
        self.assertIn("/api/freesound/download", self.api.MUTATING)
        self.j("POST", "/api/freesound/key", {"key": KEY})
        self.app.executing = True
        try:
            code, r = self.j("POST", "/api/freesound/download", {"id": 1, "mode": "preview"})
        finally:
            self.app.executing = False
        self.assertEqual(code, 409)
        self.assertIn("locked", r["error"])
        self.assertEqual(self.tr.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "_Freesound")))


if __name__ == "__main__":
    unittest.main()

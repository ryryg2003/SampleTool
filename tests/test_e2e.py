"""End-to-end tests through the real HTTP server on a synthetic fixture.

OWNER: QA. Every test builds a fresh disposable fixture in a temp dir (via
tools/make_fixtures.py), starts ``samplelib.server.serve`` on an ephemeral port
and drives the JSON API with urllib. Nothing here touches a real collection.

Assertions use contract fields (docs/CONTRACT.md). The few places that must
monkeypatch executor internals look the hook up with ``getattr`` and skip with
a clear message if it is missing.
"""
from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)


def _load_fixtures():
    spec = importlib.util.spec_from_file_location("make_fixtures",
                                                  os.path.join(REPO, "tools", "make_fixtures.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


make_fixtures = _load_fixtures()

try:  # the server/store are needed for everything except the fixture self-test
    from samplelib import models
    from samplelib.server import serve
    from samplelib.store import Store
    _IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001 - reported per test
    _IMPORT_ERROR = e

WRITE_ACTIONS = ("move", "link")
JOB_TIMEOUT = float(os.environ.get("SLO_E2E_TIMEOUT", "180"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def snapshot(top: str) -> dict:
    """relative path -> (size, mtime_ns, sha256) for files, ("dir", mtime_ns) for dirs."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(top, followlinks=False):
        for d in dirnames:
            p = os.path.join(dirpath, d)
            snap[os.path.relpath(p, top).replace(os.sep, "/") + "/"] = ("dir", os.lstat(p).st_mtime_ns)
        for n in filenames:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            snap[os.path.relpath(p, top).replace(os.sep, "/")] = (st.st_size, st.st_mtime_ns,
                                                                  sha256_file(p))
    snap["./"] = ("dir", os.lstat(top).st_mtime_ns)
    return snap


def native(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def key(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)).casefold()


class ApiError(Exception):
    def __init__(self, code, body):
        super().__init__(f"HTTP {code}: {body}")
        self.code, self.body = code, body


class Env:
    """A fixture + data dir + running server."""

    def __init__(self, large: int = 0):
        self.tmp = tempfile.mkdtemp(prefix="slo-e2e-")
        self.root = os.path.join(self.tmp, "Sample Packs")
        self.data = os.path.join(self.tmp, "appdata")
        self.manifest = make_fixtures.build(self.root, large=large)
        self.dest_root = os.path.join(self.root, "Samples")
        self.httpd = None
        self.app = None
        self.start()

    # -- server lifecycle -------------------------------------------------
    def start(self):
        self.store = Store(self.data, self.root)
        res = serve(self.store, port=0)
        if isinstance(res, tuple):
            self.httpd, self.app = res[0], res[1]
        else:
            self.httpd, self.app = res, None
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd is None:
            return
        try:
            if self.get("/api/progress").get("running"):
                self.post("/api/cancel", {})
                self.wait(timeout=30, check=False)
        except Exception:
            pass
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None

    def close(self):
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- http ---------------------------------------------------------------
    def url(self, path, **q):
        q = {k: v for k, v in q.items() if v is not None}
        return f"http://127.0.0.1:{self.port}{path}" + ("?" + urllib.parse.urlencode(q) if q else "")

    def raw(self, method, path, body=None, headers=None, q=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url(path, **(q or {})), data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def get(self, path, **q):
        code, _, b = self.raw("GET", path, q=q)
        if code != 200:
            raise ApiError(code, b[:500])
        return json.loads(b)

    def post(self, path, body, slo=True, expect=200):
        h = {"Content-Type": "application/json"}
        if slo:
            h["X-SLO"] = "1"
        code, _, b = self.raw("POST", path, body, h)
        try:
            j = json.loads(b) if b else {}
        except ValueError:
            j = {"raw": b[:500]}
        if expect is not None and code != expect:
            raise ApiError(code, j)
        return code, j

    def wait(self, timeout=JOB_TIMEOUT, check=True):
        t0 = time.time()
        while True:
            p = self.get("/api/progress")
            if not p.get("running"):
                break
            if time.time() - t0 > timeout:
                raise AssertionError(f"job did not finish in {timeout}s: {p}")
            time.sleep(0.05)
        if check and p.get("phase") == "failed":
            raise AssertionError(f"job failed: {p.get('message')} errors={p.get('errors')}")
        return p

    # -- flows --------------------------------------------------------------
    def scan(self):
        self.post("/api/scan", {})
        return self.wait()

    def all_samples(self, **q):
        out, off = [], 0
        while True:
            r = self.get("/api/samples", offset=off, limit=1000, **q)
            out += r["items"]
            off += len(r["items"])
            if not r["items"] or off >= r["total"]:
                return out

    def build_plan(self):
        self.post("/api/plan/build", {})
        self.wait()
        return self.plan()

    def plan(self, **q):
        ops, off = [], 0
        while True:
            r = self.get("/api/plan", offset=off, limit=1000, **q)
            ops += r["ops"]
            off += len(r["ops"])
            if not r["ops"] or off >= r["total"]:
                r["ops"] = ops
                return r

    def log(self, **q):
        items, off = [], 0
        while True:
            r = self.get("/api/log", offset=off, limit=1000, **q)
            items += r["items"]
            off += len(r["items"])
            if not r["items"] or off >= r["total"]:
                return items

    def approve(self, h):
        return self.post("/api/plan/approve", {"plan_hash": h})

    def execute(self, h, wait=True):
        self.post("/api/execute", {"plan_hash": h})
        return self.wait() if wait else None

    def by_rel(self):
        return {s["rel_path"]: s for s in self.all_samples()}

    def journal_records(self, h):
        from samplelib import executor
        jp = None
        if hasattr(self.store, "journal_path"):
            jp = self.store.journal_path(h)
        if not jp or not os.path.exists(jp):
            cands = [os.path.join(self.data, n) for n in os.listdir(self.data)
                     if n.startswith("journal") and n.endswith(".jsonl")]
            jp = cands[0] if len(cands) == 1 else None
        if not jp:
            raise unittest.SkipTest("cannot locate the execution journal in the data dir")
        return executor.read_journal(jp)


class E2ECase(unittest.TestCase):
    large = 0

    def setUp(self):
        if _IMPORT_ERROR is not None:
            self.fail(f"samplelib server/store import failed: {_IMPORT_ERROR!r}")
        self.env = Env(self.large)
        self.addCleanup(self.env.close)
        self.m = self.env.manifest

    # -- shared expectations ------------------------------------------------
    def ops_by_item(self, ops):
        d = {}
        for o in ops:
            d.setdefault(o["item_id"], []).append(o)
        return d

    def op_for(self, rows, ops, rel):
        it = rows[rel]
        mine = [o for o in ops if o["item_id"] == it["id"]]
        self.assertEqual(len(mine), 1, f"expected exactly one op for {rel}, got {mine}")
        return mine[0]

    def ready_plan(self):
        self.env.scan()
        plan = self.env.build_plan()
        self.assertFalse(plan["stale"])
        return plan

    def standalone_moves(self, plan, rows):
        """Move ops for loose files that no other op depends on / links to."""
        deps = {o.get("depends_on") for o in plan["ops"] if o.get("depends_on")}
        link_targets = {key(o["link_to"]) for o in plan["ops"] if o.get("link_to")}
        canon = {key(o["canonical"]) for o in plan["ops"] if o.get("canonical")}
        out = []
        for o in plan["ops"]:
            if o["action"] != "move" or o.get("kit_id") or o["id"] in deps:
                continue
            if key(o["dest"]) in link_targets or key(o["dest"]) in canon:
                continue
            if sum(1 for v in self.m["sha256"].values() if v == o.get("sha256")) > 1:
                continue  # part of a duplicate group
            out.append(o)
        return out

    def assert_final_state(self, plan, results, allow_held=(), mutated=()):
        """Post-execution invariants shared by several tests."""
        res = {r["op_id"]: r for r in results}
        ops = {o["id"]: o for o in plan["ops"]}
        self.assertEqual(set(res), set(ops), "log must list every operation exactly once")
        for oid, o in ops.items():
            r = res[oid]
            st = r["status"]
            if o["action"] == "move":
                if st in ("done", "already-done"):
                    self.assertTrue(os.path.isfile(o["dest"]), o["dest"])
                    self.assertEqual(sha256_file(o["dest"]), o["sha256"], o["dest"])
                    self.assertFalse(os.path.lexists(o["source"]),
                                     f"source should be gone after move: {o['source']}")
                else:
                    self.assertIn(oid, allow_held, f"unexpected {st} for move {o['source']}: {r.get('error')}")
                    self.assertTrue(os.path.isfile(o["source"]), "held move must keep its source")
            elif o["action"] == "link":
                if st in ("done", "already-done"):
                    a, b = os.stat(o["dest"]), os.stat(o["link_to"])
                    self.assertEqual((a.st_dev, a.st_ino), (b.st_dev, b.st_ino),
                                     f"link {o['dest']} must share identity with {o['link_to']}")
                    self.assertEqual(sha256_file(o["dest"]), o["sha256"])
                else:
                    self.assertIn(oid, allow_held, f"unexpected {st} for link {o['dest']}: {r.get('error')}")
            else:
                self.assertIn(st, ("skipped", "held"), f"{o['action']} op must not write: {r}")
        self.assertNotIn("failed", {r["status"] for r in results},
                         [r for r in results if r["status"] == "failed"])
        # No data loss: every incoming hash still exists somewhere under root.
        after = snapshot(self.env.root)
        have = {v[2] for v in after.values() if v[0] != "dir"}
        for rel in self.m["incoming"] + self.m["library"]:
            if self.m["sha256"][rel] in mutated:
                continue  # the test itself changed this file's bytes
            self.assertIn(self.m["sha256"][rel], have, f"content of {rel} vanished")
        # One physical copy per hash inside the library (Catalog excluded).
        ids: dict = {}
        for dirpath, dirnames, files in os.walk(self.env.dest_root):
            if key(dirpath).startswith(key(os.path.join(self.env.dest_root, "Catalog"))):
                continue
            for n in files:
                p = os.path.join(dirpath, n)
                st = os.stat(p)
                ids.setdefault(sha256_file(p), set()).add((st.st_dev, st.st_ino))
        multi = {h: v for h, v in ids.items() if len(v) > 1}
        self.assertEqual(multi, {}, "library must hold one physical copy per SHA-256")
        return after


# ---------------------------------------------------------------------------
# Fixture self-test (runs even before the backend is integrated)
# ---------------------------------------------------------------------------
class FixtureSelfTest(unittest.TestCase):
    def test_fixture_is_valid(self):
        import wave
        tmp = tempfile.mkdtemp(prefix="slo-fx-")
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, "Sample Packs")
        m = make_fixtures.build(root, large=5)
        self.assertTrue(os.path.isfile(os.path.join(tmp, "fixture_manifest.json")))
        self.assertFalse(os.path.exists(os.path.join(root, "fixture_manifest.json")))
        for rel in m["incoming"] + m["library"]:
            p = native(root, rel)
            self.assertEqual(sha256_file(p), m["sha256"][rel])
            if rel.lower().endswith(".wav"):
                with wave.open(p) as w:
                    self.assertGreater(w.getnframes(), 0, rel)
        with wave.open(native(root, "Nightfall Audio - Midnight Trap/One Shots/808s/808_C_Long.wav")) as w:
            self.assertAlmostEqual(w.getnframes() / w.getframerate(), 1.8, places=2)
        self.assertEqual(len(m["bulk"]), 5)
        self.assertGreaterEqual(len(m["packs_with_maker"]), 5)
        with open(native(root, m["kits"][0]["root_rel"] + "/Project.als"), "rb") as f:
            import gzip
            self.assertIn(b"Stems/Kick.wav", gzip.decompress(f.read()))
        with open(native(root, "SoundWorks - FX Toolkit/Vocals/Vocal Chant 120bpm.mp3"), "rb") as f:
            head = f.read(200)
        self.assertTrue(head.startswith(b"ID3") and b"TBPM" in head and b"TKEY" in head)
        with self.assertRaises(SystemExit):
            make_fixtures.build(root)  # refuses a non-empty folder


# ---------------------------------------------------------------------------
# Read-only review
# ---------------------------------------------------------------------------
class ReviewReadOnlyTest(E2ECase):
    def test_scan_and_review_never_touch_collection(self):
        env, m = self.env, self.m
        before = snapshot(env.root)
        env.scan()
        st = env.get("/api/state")
        self.assertTrue(st["has_inventory"])
        rows = env.by_rel()
        incoming = {r: s for r, s in rows.items() if s["origin"] == "incoming"}
        library = {r: s for r, s in rows.items() if s["origin"] == "library"}
        self.assertEqual(sorted(incoming), sorted(m["incoming"]))
        self.assertEqual(sorted(library), sorted(m["library"]))
        for r in rows:
            self.assertFalse(r.casefold().startswith("samples/catalog/"), f"Catalog inventoried: {r}")
        for r, s in rows.items():
            self.assertEqual(s["origin"] == "library", r.startswith("Samples/"), r)
        lib_only = env.all_samples(origin="library")
        self.assertEqual(sorted(s["rel_path"] for s in lib_only), sorted(m["library"]))

        # Every review interaction.
        env.get("/api/packs")
        env.get("/api/packs", q="midnight")
        env.get("/api/facets")
        env.get("/api/samples", q="kick", sort="bpm", dir="desc")
        env.get("/api/samples", bpm_min=80, bpm_max=150, sort="name")
        env.get("/api/samples", kind="audio", origin="incoming", status="needs-review")
        some = rows[m["plain_one_shots"][0]]
        env.get(f"/api/samples/{some['id']}")
        kits = env.get("/api/kits")
        for k in kits:
            env.get(f"/api/kits/{k['id']}")
        env.raw("GET", f"/api/audio/{some['id']}", headers={"Range": "bytes=0-31"})
        env.raw("GET", f"/api/audio/{some['id']}")
        env.post("/api/items/edit", {"ids": [some["id"]], "changes": {"category": "One-Shots/FX & Vocals"}})
        env.post("/api/items/edit", {"ids": [some["id"]], "changes": {"bpm": 120, "key": "Am",
                                                                     "tags": "dark, punchy"}})
        if kits:
            kid = kits[0]["id"]
            env.post("/api/kits/edit", {"kit_id": kid, "action": "rename", "name": "Renamed Kit"})
            env.post("/api/kits/edit", {"kit_id": kid, "action": "hold"})
            env.post("/api/kits/edit", {"kit_id": kid, "action": "release"})
        plan = env.build_plan()
        env.get("/api/plan", group="pack")
        env.get("/api/plan", group="warning", action="hold")
        env.get("/api/log")
        env.get("/api/state")
        self.assertTrue(plan["ops"])
        self.assertEqual(snapshot(env.root), before,
                         "scan/review/plan changed files, folders or timestamps under root")
        self.assertFalse(os.path.exists(os.path.join(env.dest_root, "Catalog", "Sample_Catalog.csv")))

    def test_search_filters_sort_and_metadata(self):
        env, m = self.env, self.m
        env.scan()
        packs = env.get("/api/packs")
        names = {p["name"]: p for p in packs}
        self.assertEqual(set(names), set(m["packs"]))
        for pack, maker in m["packs_with_maker"].items():
            self.assertEqual((names[pack]["manufacturer"] or "").casefold(), maker.casefold(), pack)
        self.assertIn(names[m["pack_without_maker"]]["manufacturer"], (None, ""))
        # partial pack name, and manufacturer-only match
        self.assertEqual([p["name"] for p in env.get("/api/packs", q="dusty")], ["Lofi Lab - Dusty Keys"])
        self.assertIn("Prime Grooves - Bass Science", [p["name"] for p in env.get("/api/packs", q="prime gro")])
        self.assertEqual(env.get("/api/packs", q="zzzz-nothing"), [])
        k1 = names["Nightfall Audio - Midnight Trap"]
        self.assertGreaterEqual(k1["kits"], 1)
        self.assertGreater(k1["audio"], 0)

        rows = env.by_rel()
        for rel, exp in m["expected_meta"].items():
            r = rows[rel]
            if "bpm" in exp:
                self.assertIsNotNone(r["bpm"], f"BPM unknown for {rel}")
                self.assertAlmostEqual(float(r["bpm"]), exp["bpm"], places=1, msg=rel)
            if "key" in exp:
                self.assertEqual(r["key"], exp["key"], rel)
            if exp.get("loop_type") == "loop":
                self.assertEqual(r["loop_type"], "loop", rel)
            if exp.get("loop_type") == "one-shot":
                self.assertIn(r["loop_type"], ("one-shot", None), f"{rel} must not be called a loop")
            if "category" in exp:
                self.assertIn(r["category"], (exp["category"], None), rel)
        long808 = rows["Nightfall Audio - Midnight Trap/One Shots/808s/808_C_Long.wav"]
        self.assertAlmostEqual(long808["duration_s"], 1.8, places=1)
        self.assertEqual(long808["sample_rate"], 44100)

        # BPM range + pack filter combined
        p1 = "Nightfall Audio - Midnight Trap"
        res = env.all_samples(pack=p1, bpm_min=130, bpm_max=150)
        self.assertTrue(res)
        self.assertIn(f"{p1}/Loops/Drum Loop 140bpm Am.wav", [r["rel_path"] for r in res])
        for r in res:
            self.assertEqual(r["pack"], p1)
            self.assertTrue(130 <= r["bpm"] <= 150, r)
        # search text + pack + kind
        res = env.all_samples(q="kick", pack="Lofi Lab - Dusty Keys", kind="audio")
        self.assertTrue(res)
        for r in res:
            self.assertEqual(r["pack"], "Lofi Lab - Dusty Keys")
            self.assertIn("kick", (r["rel_path"] + " " + (r.get("kit_name") or "") + " "
                                   + (r.get("instrument") or "")).casefold())
        # multi-term search is AND
        res = env.all_samples(q="dark keys")
        self.assertIn("Lofi Lab - Dusty Keys/Loops/Dark Keys 90 BPM F#m.wav", [r["rel_path"] for r in res])
        # sort by bpm desc: known BPMs descending, unknown last
        res = env.all_samples(sort="bpm", dir="desc", origin="incoming")
        bpms = [r["bpm"] for r in res]
        known = [b for b in bpms if b is not None]
        self.assertTrue(known and None in bpms)
        self.assertEqual(bpms[:len(known)], sorted(known, reverse=True))
        self.assertTrue(all(b is None for b in bpms[len(known):]), "unknown BPM must sort last")
        res = env.all_samples(sort="bpm", dir="asc", origin="incoming")
        bpms = [r["bpm"] for r in res]
        self.assertIsNone(bpms[-1], "unknown BPM must sort last ascending too")
        # facets
        f = env.get("/api/facets")
        for k in ("categories", "instruments", "keys", "genres", "manufacturers", "packs", "loop_types"):
            self.assertIn(k, f)
        self.assertIn("Am", f["keys"])
        self.assertIn("Nightfall Audio", f["manufacturers"])
        # kits
        kits = env.get("/api/kits")
        self.assertEqual(sorted(k["name"] for k in kits),
                         sorted(k["name"] for k in m["kits"]))

    def test_audio_range_is_read_only(self):
        env = self.env
        env.scan()
        rel = "Nightfall Audio - Midnight Trap/One Shots/808s/808_C_Long.wav"
        p = native(env.root, rel)
        st0 = os.stat(p)
        with open(p, "rb") as f:
            data = f.read()
        it = env.by_rel()[rel]
        code, h, body = env.raw("GET", f"/api/audio/{it['id']}", headers={"Range": "bytes=100-199"})
        self.assertEqual(code, 206)
        self.assertEqual(body, data[100:200])
        hl = {k.lower(): v for k, v in h.items()}
        self.assertEqual(hl.get("content-range"), f"bytes 100-199/{len(data)}")
        self.assertIn("audio", hl.get("content-type", ""))
        code, h, body = env.raw("GET", f"/api/audio/{it['id']}", headers={"Range": "bytes=-10"})
        self.assertEqual((code, body), (206, data[-10:]))
        code, h, body = env.raw("GET", f"/api/audio/{it['id']}")
        self.assertEqual((code, body), (200, data))
        st1 = os.stat(p)
        self.assertEqual((st0.st_size, st0.st_mtime_ns), (st1.st_size, st1.st_mtime_ns))

    def test_near_duplicate_reported(self):
        env, m = self.env, self.m
        env.scan()
        rows = env.by_rel()
        a, b = m["near_dupes"][0]
        da = env.get(f"/api/samples/{rows[a]['id']}")
        db = env.get(f"/api/samples/{rows[b]['id']}")
        linked = {x["id"] for x in da.get("near_dupes", [])} | {x["id"] for x in db.get("near_dupes", [])}
        self.assertTrue(rows[a]["id"] in linked or rows[b]["id"] in linked,
                        "near-duplicates (same name stem, slightly different bytes) should be reported")
        self.assertNotEqual(rows[a]["sha256"], rows[b]["sha256"])


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------
class PlanTest(E2ECase):
    def test_plan_accounts_for_everything(self):
        env, m = self.env, self.m
        plan = self.ready_plan()
        rows = env.by_rel()
        ops = plan["ops"]
        by_id = {s["id"]: s for s in rows.values()}
        # exactly one op per incoming item, none for library items
        per = self.ops_by_item(ops)
        for rel in m["incoming"]:
            self.assertEqual(len(per.get(rows[rel]["id"], [])), 1, f"{rel} needs exactly one op")
        for o in ops:
            self.assertEqual(by_id[o["item_id"]]["origin"], "incoming", o)
            self.assertIn(o["action"], ("move", "link", "keep", "hold", "exclude"))
            self.assertTrue(o.get("reason"), f"op without reason: {o}")
            self.assertFalse("/catalog/" in o["source"].replace("\\", "/").casefold() and
                             key(o["source"]).startswith(key(env.dest_root)))
        self.assertEqual(len(ops), m["incoming_count"])
        summ = plan["summary"] or {}
        if summ.get("by_action"):
            self.assertEqual(sum(summ["by_action"].values()), len(ops))

        # Write ops: destinations unique (case-insensitive), inside Samples, never an existing file.
        dests = [key(o["dest"]) for o in ops if o["action"] in WRITE_ACTIONS]
        self.assertEqual(len(dests), len(set(dests)), "two ops share a destination")
        lib_keys = {key(native(env.root, r)) for r in m["library"]}
        for o in ops:
            if o["action"] in WRITE_ACTIONS:
                self.assertTrue(key(o["dest"]).startswith(key(env.dest_root) + key(os.sep)), o["dest"])
                self.assertNotIn(key(o["dest"]), lib_keys, "plan would overwrite an existing library file")
                self.assertFalse(os.path.lexists(o["dest"]), o["dest"])
                self.assertTrue(o.get("sha256"))
            if o["action"] == "link":
                self.assertTrue(o.get("link_to"))
                ok_targets = {key(x["dest"]) for x in ops if x["action"] == "move"} | lib_keys
                self.assertIn(key(o["link_to"]), ok_targets, "link must target a moved canonical or library file")
                if o.get("depends_on"):
                    self.assertIn(o["depends_on"], {x["id"] for x in ops})

        # One physical copy per hash: at most one move per sha256, none if library has it.
        moves_by_sha: dict = {}
        for o in ops:
            if o["action"] == "move":
                moves_by_sha.setdefault(o["sha256"], []).append(o)
        for h, lst in moves_by_sha.items():
            self.assertEqual(len(lst), 1, f"hash {h[:12]} moved {len(lst)} times")
        lib_hashes = {m["sha256"][r] for r in m["library"]}
        for h in lib_hashes:
            self.assertNotIn(h, moves_by_sha, "content already in the library must not be moved again")

        # Renamed exact duplicates -> one move, others keep (loose) or link.
        for grp in ("renamed_loose_dups", "renamed_loose_dups_2"):
            acts = [self.op_for(rows, ops, r) for r in m[grp]]
            self.assertEqual(sum(1 for o in acts if o["action"] == "move"), 1, grp)
            for o in acts:
                self.assertIn(o["action"], ("move", "keep", "link"), o)
                if o["action"] == "keep":
                    self.assertTrue(o.get("canonical"), "redundant duplicate must name its canonical")
        # Library duplicate -> kept (or linked), canonical is the library file.
        o = self.op_for(rows, ops, m["library_dup"]["incoming"])
        self.assertIn(o["action"], ("keep", "link"))
        self.assertEqual(key(o.get("canonical") or o.get("link_to") or ""),
                         key(native(env.root, m["library_dup"]["library"])))
        # Kit stem identical to a loose file elsewhere: kit member is canonical.
        km = self.op_for(rows, ops, m["kit_loose_dup"]["kit_member"])
        lo = self.op_for(rows, ops, m["kit_loose_dup"]["loose"])
        self.assertIn(km["action"], ("move", "hold"))
        if km["action"] == "move":
            self.assertEqual(lo["action"], "keep")
            self.assertEqual(key(lo["canonical"]), key(km["dest"]))
        # Kit stem duplicated in another kit -> one move + one link (hard link).
        kk = [self.op_for(rows, ops, r) for r in m["kit_kit_dup"]]
        if all(o["action"] != "hold" for o in kk):
            self.assertEqual(sorted(o["action"] for o in kk), ["link", "move"])

        # Same name, different content -> different destinations, none equal to library file.
        same = [self.op_for(rows, ops, r) for r in m["same_name_diff_content"]]
        sdests = [key(o["dest"]) for o in same if o["action"] in WRITE_ACTIONS]
        self.assertEqual(len(sdests), len(set(sdests)))
        coll = native(env.root, m["collisions"][0]["library"])
        moved_kicks = [o for o in same if o["action"] == "move"]
        self.assertTrue(moved_kicks, "plain one-shot kicks should be organized")
        self.assertTrue(any(os.path.dirname(key(o["dest"])) == os.path.dirname(key(coll)) for o in moved_kicks),
                        "kicks expected in One-Shots/Drums")
        for o in moved_kicks:
            self.assertNotEqual(key(o["dest"]), key(coll))
        if "collisions" in summ:
            self.assertGreaterEqual(summ["collisions"], 1)

        # Unknown format: visible, never moved silently.
        for rel in m["unknown_formats"]:
            o = self.op_for(rows, ops, rel)
            self.assertIn(o["action"], ("keep", "hold"), f"unknown format {rel} must not move")
            self.assertTrue(o["reason"])
        if "unknown_formats" in summ:
            self.assertGreaterEqual(summ["unknown_formats"], 1)
        for o in ops:
            if o["action"] == "hold":
                self.assertTrue(o["reason"].strip())

        # Kits: members stay together with relative paths preserved.
        for kit in m["kits"]:
            kops = [self.op_for(rows, ops, r) for r in kit["members"]]
            if any(o["action"] == "hold" for o in kops):
                self.assertTrue(all(o["action"] in ("hold", "exclude") for o in kops),
                                f"a held kit must be held as a whole: {kit['name']}")
                continue
            roots = set()
            for rel, o in zip(kit["members"], kops):
                self.assertIn(o["action"], WRITE_ACTIONS, f"{rel} should travel with its kit")
                tail = os.path.join(*rel[len(kit["root_rel"]) + 1:].split("/"))
                self.assertTrue(key(o["dest"]).endswith(key(os.sep + tail)), (o["dest"], tail))
                roots.add(key(o["dest"])[:-len(tail)])
                self.assertIn(key(os.sep + models.KITS_DIR + os.sep), key(o["dest"]))
            self.assertEqual(len(roots), 1, f"kit {kit['name']} split across folders: {roots}")


# ---------------------------------------------------------------------------
# Edits, staleness and approval guards
# ---------------------------------------------------------------------------
class ApprovalGuardTest(E2ECase):
    def test_edit_marks_stale_and_guards(self):
        env, m = self.env, self.m
        plan = self.ready_plan()
        h = plan["plan_hash"]
        rows = env.by_rel()
        it = rows[m["plain_one_shots"][0]]  # a snare
        before = env.get(f"/api/samples/{it['id']}")["proposed_dest"]
        self.assertTrue(before)
        new_cat = "One-Shots/FX & Vocals" if before and "FX & Vocals" not in before else "One-Shots/Drums"
        _, r = env.post("/api/items/edit", {"ids": [it["id"]], "changes": {"category": new_cat}})
        self.assertEqual(r["updated"], 1)
        row = r["items"][0]
        self.assertEqual(row["category"], new_cat)
        self.assertEqual(row["prov"]["category"]["source"], "manual")
        self.assertEqual(row["review_status"], "edited")
        self.assertNotEqual(row["proposed_dest"], before)
        self.assertIn(os.path.join(*new_cat.split("/")), row["proposed_dest"])
        st = env.get("/api/state")["plan"]
        self.assertTrue(st["stale"])
        self.assertFalse(st["approved"])
        self.assertEqual(env.post("/api/plan/approve", {"plan_hash": h}, expect=None)[0], 409)
        self.assertEqual(env.post("/api/execute", {"plan_hash": h}, expect=None)[0], 409)

        plan2 = env.build_plan()
        h2 = plan2["plan_hash"]
        self.assertNotEqual(h2, h)
        self.assertFalse(plan2["stale"])
        moved = [o for o in plan2["ops"] if o["item_id"] == it["id"]][0]
        if moved["action"] == "move":
            self.assertIn(key(os.path.join(*new_cat.split("/"))), key(moved["dest"]))
        # guards
        self.assertEqual(env.post("/api/plan/approve", {"plan_hash": "deadbeef"}, expect=None)[0], 409)
        self.assertEqual(env.post("/api/execute", {"plan_hash": h2}, expect=None)[0], 409)
        self.assertEqual(env.post("/api/plan/approve", {"plan_hash": h2}, slo=False, expect=None)[0], 403)
        self.assertFalse(env.get("/api/state")["plan"]["approved"])
        env.approve(h2)
        self.assertTrue(env.get("/api/state")["plan"]["approved"])
        self.assertEqual(env.post("/api/execute", {"plan_hash": h2}, slo=False, expect=None)[0], 403)
        self.assertEqual(env.post("/api/execute", {"plan_hash": h}, expect=None)[0], 409)
        # any later edit clears approval
        env.post("/api/items/edit", {"ids": [it["id"]], "changes": {"bpm": 100}})
        st = env.get("/api/state")["plan"]
        self.assertTrue(st["stale"])
        self.assertFalse(st["approved"])
        self.assertEqual(env.post("/api/execute", {"plan_hash": h2}, expect=None)[0], 409)
        # kit edits also invalidate
        plan3 = env.build_plan()
        env.approve(plan3["plan_hash"])
        kits = env.get("/api/kits")
        env.post("/api/kits/edit", {"kit_id": kits[0]["id"], "action": "rename", "name": "X Kit"})
        self.assertFalse(env.get("/api/state")["plan"]["approved"])
        # invalid edits rejected, nothing written
        self.assertEqual(env.post("/api/items/edit", {"ids": [it["id"]], "changes": {"bpm": 9999}},
                                  expect=None)[0], 400)
        self.assertEqual(env.post("/api/items/edit", {"ids": [it["id"]], "changes": {"category": "Nope"}},
                                  expect=None)[0], 400)
        self.assertFalse(os.path.exists(os.path.join(env.dest_root, "Catalog", "Sample_Catalog.csv")))


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
REPORTS = {
    "Sample_Catalog.csv": [("path", "dest", "final"), ("bpm",), ("key",), ("sha",)],
    "Source_Path_Index.csv": [("source", "original"), ("path", "dest", "final")],
    "Duplicate_Report.csv": [("sha", "hash"), ("path", "source", "dest")],
    "Organization_Report.csv": [],
}


class ExecuteTest(E2ECase):
    def test_approve_execute_verify(self):
        env, m = self.env, self.m
        plan = self.ready_plan()
        rows = env.by_rel()
        h = plan["plan_hash"]
        env.approve(h)
        old_csv = native(env.root, m["ignored"][0])
        old_csv_before = (os.stat(old_csv).st_mtime_ns, sha256_file(old_csv))
        env.execute(h)
        results = env.log()
        self.assert_final_state(plan, results)
        res = {r["op_id"]: r for r in results}
        self.assertTrue(any(r["status"] == "done" for r in results))
        # every write op done in a clean run
        for o in plan["ops"]:
            if o["action"] in WRITE_ACTIONS:
                self.assertEqual(res[o["id"]]["status"], "done", (o["source"], res[o["id"]].get("error")))
        # redundant loose duplicates stay in place
        for o in plan["ops"]:
            if o["action"] == "keep":
                self.assertTrue(os.path.isfile(o["source"]), o["source"])
        self.assertTrue(os.path.isfile(native(env.root, m["library_dup"]["incoming"])))
        # existing library files untouched
        for rel in m["library"]:
            self.assertEqual(sha256_file(native(env.root, rel)), m["sha256"][rel])
        # kit structure preserved
        for kit in m["kits"]:
            kops = [self.op_for(rows, plan["ops"], r) for r in kit["members"]]
            if any(o["action"] not in WRITE_ACTIONS for o in kops):
                continue
            first_tail = os.path.join(*kit["members_rel_to_kit"][0].split("/"))
            d0 = [o["dest"] for r, o in zip(kit["members"], kops)
                  if r.endswith("/" + kit["members_rel_to_kit"][0])][0]
            kroot = d0[:-len(first_tail)]
            for rel in kit["members_rel_to_kit"]:
                self.assertTrue(os.path.isfile(os.path.join(kroot, *rel.split("/"))), (kroot, rel))
            for sub in ("Stems", "MIDI"):
                self.assertTrue(os.path.isdir(os.path.join(kroot, sub)))
        # kit-kit duplicate hard-linked
        kk = [self.op_for(rows, plan["ops"], r) for r in m["kit_kit_dup"]]
        if sorted(o["action"] for o in kk) == ["link", "move"]:
            a, b = (os.stat(o["dest"]) for o in kk)
            self.assertEqual((a.st_dev, a.st_ino), (b.st_dev, b.st_ino))
            self.assertGreaterEqual(a.st_nlink, 2)
        # reports
        cat = os.path.join(env.dest_root, "Catalog")
        for name, cols in REPORTS.items():
            p = os.path.join(cat, name)
            self.assertTrue(os.path.isfile(p), f"missing report {name}")
            with open(p, "r", encoding="utf-8-sig", newline="") as f:
                import csv
                rd = list(csv.reader(f))
            self.assertGreaterEqual(len(rd), 2 if name != "Duplicate_Report.csv" else 1, name)
            header = " ".join(rd[0]).casefold()
            for alts in cols:
                self.assertTrue(any(a in header for a in alts), f"{name} header lacks {alts}: {rd[0]}")
        self.assertEqual((os.stat(old_csv).st_mtime_ns, sha256_file(old_csv)), old_csv_before,
                         "Samples/Catalog/old.csv must be left alone")
        st = env.get("/api/state")
        ex = st["execution"]
        self.assertTrue(ex and ex["ran"])
        names = set(ex["reports"]) | {os.path.basename(str(v)) for v in ex["reports"].values()}
        self.assertTrue(set(REPORTS) <= names, ex["reports"])
        ver = ex["verification"]
        self.assertIsInstance(ver, dict)
        if isinstance(ver.get("ok"), bool):
            self.assertTrue(ver["ok"], ver)
        # log filters
        done = env.log(status="done")
        self.assertTrue(done and all(r["status"] == "done" for r in done))
        self.assertTrue(all(r.get("name") for r in results))
        # rows show final paths
        rows2 = env.by_rel()
        for o in plan["ops"]:
            if o["action"] == "move":
                r = [x for x in rows2.values() if x["id"] == o["item_id"]][0]
                self.assertEqual(key(r["final_path"] or ""), key(o["dest"]))
                self.assertEqual(r["review_status"], "done")
        # re-running the same approved plan repeats nothing
        if env.post("/api/execute", {"plan_hash": h}, expect=None)[0] == 200:
            env.wait()
            again = {r["op_id"]: r for r in env.log()}
            for o in plan["ops"]:
                if o["action"] in WRITE_ACTIONS:
                    self.assertEqual(again[o["id"]]["status"], "already-done")
            self.assert_final_state(plan, list(again.values()))

    def test_changed_locked_and_collision_after_approval(self):
        env = self.env
        plan = self.ready_plan()
        rows = env.by_rel()
        cands = self.standalone_moves(plan, rows)
        self.assertGreaterEqual(len(cands), 3, "need three independent loose moves")
        changed, collide, locked = cands[0], cands[1], cands[2]
        env.approve(plan["plan_hash"])

        # 1) bytes change after approval, size and mtime preserved
        st = os.stat(changed["source"])
        with open(changed["source"], "r+b") as f:
            f.seek(st.st_size - 2)
            b = f.read(2)
            f.seek(st.st_size - 2)
            f.write(bytes([b[0] ^ 0x55, b[1] ^ 0x55]))
        os.utime(changed["source"], ns=(st.st_atime_ns, st.st_mtime_ns))
        changed_sha = sha256_file(changed["source"])
        # 2) destination appears after approval
        os.makedirs(os.path.dirname(collide["dest"]), exist_ok=True)
        with open(collide["dest"], "wb") as f:
            f.write(b"someone else's file")
        # 3) locked source: unlink of that source raises PermissionError
        from samplelib import executor
        orig = getattr(executor, "_unlink", None)
        if orig is None:
            self.skipTest("executor has no _unlink hook; adapt the locked-file test")

        def fake_unlink(p, *a, **kw):
            if key(p.replace("\\\\?\\", "")) == key(locked["source"]):
                raise PermissionError(errno.EACCES, "The process cannot access the file", p)
            return orig(p, *a, **kw)

        executor._unlink = fake_unlink
        self.addCleanup(setattr, executor, "_unlink", orig)
        env.execute(plan["plan_hash"])
        executor._unlink = orig
        res = {r["op_id"]: r for r in env.log()}

        self.assertEqual(res[changed["id"]]["status"], "held")
        self.assertTrue(res[changed["id"]].get("error"))
        self.assertEqual(sha256_file(changed["source"]), changed_sha)
        self.assertFalse(os.path.exists(changed["dest"]), "changed file must not be written")

        self.assertEqual(res[collide["id"]]["status"], "held")
        with open(collide["dest"], "rb") as f:
            self.assertEqual(f.read(), b"someone else's file", "existing destination overwritten")
        self.assertEqual(sha256_file(collide["source"]), collide["sha256"])

        self.assertEqual(res[locked["id"]]["status"], "held", res[locked["id"]])
        self.assertIn("lock", (res[locked["id"]].get("error") or "").casefold())
        self.assertEqual(sha256_file(locked["source"]), locked["sha256"])
        self.assertFalse(os.path.lexists(locked["dest"]), "dest link must be rolled back")
        self.assertEqual(os.stat(locked["source"]).st_nlink, 1)

        allow = {changed["id"], collide["id"], locked["id"]}
        # remove the planted file so the one-copy-per-hash check only sees real content
        os.remove(collide["dest"])
        self.assert_final_state(plan, list(res.values()), allow_held=allow,
                                mutated={changed["sha256"]})


class ResumeTest(E2ECase):
    N = 3

    def _hook(self, name):
        from samplelib import executor
        fn = getattr(executor, name, None)
        if fn is None:
            self.skipTest(f"executor has no {name} hook; adapt the interrupt test")
        self.addCleanup(setattr, executor, name, fn)
        return executor, fn

    def test_cancel_then_resume(self):
        env = self.env
        plan = self.ready_plan()
        h = plan["plan_hash"]
        n_moves = sum(1 for o in plan["ops"] if o["action"] == "move")
        self.assertGreater(n_moves, self.N + 1)
        env.approve(h)
        executor, orig = self._hook("_link")
        run1, run2 = [], []
        port = env.port

        def link_cancel(src, dst, *a, **kw):
            r = orig(src, dst, *a, **kw)
            run1.append(key(dst))
            if len(run1) == self.N:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/cancel", data=b"{}",
                                             method="POST", headers={"X-SLO": "1",
                                                                     "Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10).read()
            return r

        executor._link = link_cancel
        env.execute(h)
        r1 = {r["op_id"]: r for r in env.log()}
        done1 = {oid for oid, r in r1.items() if r["status"] == "done"}
        self.assertTrue(done1, "some operations should complete before the interruption")
        self.assertTrue(any(r["status"] != "done" for oid, r in r1.items()
                            if any(o["id"] == oid and o["action"] in WRITE_ACTIONS for o in plan["ops"])),
                        "cancel should stop before all writes")
        st = env.get("/api/state")
        self.assertTrue(st["plan"]["approved"], "approval must survive an interrupted run")

        def link_count(src, dst, *a, **kw):
            run2.append(key(dst))
            return orig(src, dst, *a, **kw)

        executor._link = link_count
        env.execute(h)
        executor._link = orig
        r2 = {r["op_id"]: r for r in env.log()}
        for oid in done1:
            self.assertEqual(r2[oid]["status"], "already-done")
        self.assertFalse(set(run1) & set(run2), "completed operations were repeated")
        recs = env.journal_records(h)
        done_counts: dict = {}
        for rec in recs:
            if rec.get("phase") == "done":
                done_counts[rec["op_id"]] = done_counts.get(rec["op_id"], 0) + 1
        self.assertTrue(all(v == 1 for v in done_counts.values()), done_counts)
        moved = {oid for oid, r in r2.items() if r["status"] in ("done", "already-done")
                 and any(o["id"] == oid and o["action"] == "move" for o in plan["ops"])}
        self.assertEqual(len(moved), n_moves)
        self.assert_final_state(plan, list(r2.values()))

    def test_crash_mid_operation_then_resume(self):
        """Process dies after the dest link was made but before the source was removed."""
        env = self.env
        plan = self.ready_plan()
        h = plan["plan_hash"]
        env.approve(h)
        rows = env.by_rel()
        victim = self.standalone_moves(plan, rows)[0]
        executor, orig = self._hook("_unlink")

        class Crash(BaseException):
            pass

        def crash_unlink(p, *a, **kw):
            if key(p.replace("\\\\?\\", "")) == key(victim["source"]):
                raise Crash()
            return orig(p, *a, **kw)

        jp = env.store.journal_path(h) if hasattr(env.store, "journal_path") else None
        if not jp:
            self.skipTest("Store.journal_path missing; adapt the crash test")
        executor._unlink = crash_unlink
        with env.store.lock:
            p = env.store.plan
        with self.assertRaises(Crash):
            executor.execute(p, jp, None, None)
        executor._unlink = orig
        self.assertTrue(os.path.exists(victim["dest"]) and os.path.exists(victim["source"]),
                        "simulated crash should leave both names")
        env.execute(h)
        res = {r["op_id"]: r for r in env.log()}
        self.assertIn(res[victim["id"]]["status"], ("done", "already-done"))
        self.assertFalse(os.path.exists(victim["source"]))
        self.assertEqual(sha256_file(victim["dest"]), victim["sha256"])
        self.assert_final_state(plan, list(res.values()))


class MixMatchingApiTest(E2ECase):
    def test_matches_starter_and_saved_sets(self):
        self.env.scan()
        rows = self.env.get("/api/samples", limit=1000)["items"]
        loop = next(r for r in rows if r["loop_type"] == "loop")
        res = self.env.get("/api/samples/%s/matches" % loop["id"])
        self.assertIsInstance(res["items"], list)
        self.assertTrue(all(m["row"]["id"] != loop["id"] for m in res["items"]))
        code, _, _ = self.env.raw("GET", "/api/samples/nope/matches")
        self.assertEqual(code, 404)
        code, _, _ = self.env.raw("GET", "/api/samples/%s/matches" % loop["id"], q={"role": "x"})
        self.assertEqual(code, 400)
        _, st = self.env.post("/api/starter", {"seed": 5})
        self.assertEqual(set(st["slots"]), {"drums", "bass", "melody"})
        _, saved = self.env.post("/api/starters", {"name": "A", "key": st["key"], "bpm": st["bpm"],
                                                   "ids": [loop["id"]]})
        self.assertEqual(self.env.get("/api/starters")["items"][0]["id"], saved["starter"]["id"])
        code, _, _ = self.env.raw("DELETE", "/api/starters/" + saved["starter"]["id"],
                                  headers={"X-SLO": "1"})
        self.assertEqual(code, 200)
        self.assertEqual(self.env.get("/api/starters")["items"], [])


class PersistenceTest(E2ECase):
    def test_restart_reloads_everything(self):
        env, m = self.env, self.m
        env.scan()
        rows = env.by_rel()
        it = rows[m["plain_one_shots"][1]]
        env.post("/api/items/edit", {"ids": [it["id"]], "changes": {"category": "One-Shots/Instruments",
                                                                   "key": "C", "tags": ["warm"]}})
        kits = env.get("/api/kits")
        kid = kits[0]["id"]
        env.post("/api/kits/edit", {"kit_id": kid, "action": "rename", "name": "Persisted Kit"})
        plan = env.build_plan()
        env.approve(plan["plan_hash"])
        n_items = len(rows)
        env.stop()
        before = snapshot(env.root)

        env.start()  # fresh Store + server on the same data dir
        st = env.get("/api/state")
        self.assertTrue(st["has_inventory"])
        self.assertEqual(st["plan"]["plan_hash"], plan["plan_hash"])
        self.assertTrue(st["plan"]["approved"])
        self.assertFalse(st["plan"]["stale"])
        rows2 = env.by_rel()
        self.assertEqual(len(rows2), n_items)
        r = env.get(f"/api/samples/{it['id']}")
        self.assertEqual(r["category"], "One-Shots/Instruments")
        self.assertEqual(r["key"], "C")
        self.assertEqual(r["tags"], ["warm"])
        self.assertEqual(r["prov"]["category"]["source"], "manual")
        self.assertEqual(env.get(f"/api/kits/{kid}")["name"], "Persisted Kit")
        self.assertEqual(len(env.plan()["ops"]), len(plan["ops"]))
        # edits survive a rescan too
        env.scan()
        self.assertEqual(env.get(f"/api/samples/{it['id']}")["category"], "One-Shots/Instruments")
        self.assertFalse(env.get("/api/state")["plan"]["approved"], "rescan must clear approval")
        self.assertEqual(snapshot(env.root), before)


class EditLockTest(E2ECase):
    def test_edits_refused_during_execution(self):
        env = self.env
        plan = self.ready_plan()
        env.approve(plan["plan_hash"])
        from samplelib import executor
        orig = getattr(executor, "_link", None)
        if orig is None:
            self.skipTest("executor has no _link hook; adapt the edit-lock test")
        self.addCleanup(setattr, executor, "_link", orig)
        entered, release = threading.Event(), threading.Event()

        def slow_link(*a, **kw):
            entered.set()
            release.wait(30)
            return orig(*a, **kw)

        executor._link = slow_link
        self.addCleanup(release.set)
        env.execute(plan["plan_hash"], wait=False)
        self.assertTrue(entered.wait(30), "executor never reached a write")
        rows = env.by_rel()
        anyid = next(iter(rows.values()))["id"]
        self.assertEqual(env.post("/api/items/edit", {"ids": [anyid], "changes": {"bpm": 99}},
                                  expect=None)[0], 409)
        kits = env.get("/api/kits")
        if kits:
            self.assertEqual(env.post("/api/kits/edit", {"kit_id": kits[0]["id"], "action": "hold"},
                                      expect=None)[0], 409)
        self.assertEqual(env.post("/api/plan/build", {}, expect=None)[0], 409)
        self.assertEqual(env.post("/api/scan", {}, expect=None)[0], 409)
        release.set()
        env.wait()
        executor._link = orig
        self.assert_final_state(plan, env.log())


@unittest.skipUnless(os.environ.get("SLO_E2E_LARGE"), "set SLO_E2E_LARGE=N for the performance test")
class LargeScanTest(E2ECase):
    large = int(os.environ.get("SLO_E2E_LARGE") or 0)

    def test_large_scan_and_plan(self):
        env = self.env
        t0 = time.time()
        env.scan()
        t1 = time.time()
        plan = env.build_plan()
        t2 = time.time()
        r = env.get("/api/samples", q="perc", sort="bpm", limit=200)
        t3 = time.time()
        print(f"\n[large {self.large}] scan {t1 - t0:.1f}s, plan {t2 - t1:.1f}s, "
              f"query {1000 * (t3 - t2):.0f}ms, ops {len(plan['ops'])}")
        self.assertEqual(len(plan["ops"]), self.m["incoming_count"])
        self.assertGreater(r["total"], 0)
        self.assertLess(t3 - t2, 5.0, "a search over the inventory should be interactive")


if __name__ == "__main__":
    unittest.main()

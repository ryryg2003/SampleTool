"""Local HTTP server: JSON API + static UI + read-only audio streaming.

OWNER: lead. Binds to 127.0.0.1 only. POSTs require header X-SLO: 1 (CSRF guard)
and the Host header must be localhost/127.0.0.1 (DNS-rebinding guard).
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import models
from .store import Store

UI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui")

AUDIO_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
               ".flac": "audio/flac", ".m4a": "audio/mp4", ".aif": "audio/aiff",
               ".aiff": "audio/aiff"}


class App:
    """Owns the store and the single background job slot."""

    def __init__(self, store: Store):
        self.store = store
        self.job_lock = threading.Lock()
        self.cancel = threading.Event()
        self.progress: dict = {"seq": 0, "job": "idle", "phase": "", "running": False, "done": 0,
                               "total": 0, "bytes_done": 0, "bytes_total": 0, "current": "",
                               "warnings": 0, "errors": [], "message": ""}
        self._plock = threading.Lock()
        self.executing = False  # true for the whole approved run incl. reports

    # ------------------------------------------------------------ progress
    def set_progress(self, p: dict) -> None:
        with self._plock:
            errs = self.progress.get("errors", [])
            self.progress.update(p)
            if "errors" in p and p["errors"] is not errs:
                self.progress["errors"] = list(p["errors"])[-200:]

    def get_progress(self) -> dict:
        with self._plock:
            return json.loads(json.dumps(self.progress, default=str))

    # ----------------------------------------------------------------- jobs
    def start_job(self, name: str, fn) -> None:
        if not self.job_lock.acquire(blocking=False):
            raise RuntimeError(f"Another task is running ({self.progress.get('job')}). Wait for it or cancel it.")
        self.cancel.clear()
        self.seq = getattr(self, "seq", 0) + 1  # lets the UI notice jobs that start and end between polls
        self.set_progress({"seq": self.seq, "job": name, "phase": "starting", "running": True, "done": 0,
                           "total": 0, "bytes_done": 0, "bytes_total": 0, "current": "",
                           "warnings": 0, "errors": [], "message": ""})

        def run():
            try:
                fn()
                if self.cancel.is_set():
                    self.set_progress({"running": False, "phase": "cancelled",
                                       "message": f"{name.capitalize()} cancelled."})
                else:
                    self.set_progress({"running": False, "phase": "complete"})
            except Exception as e:  # surface, never crash the server
                traceback.print_exc()
                with self._plock:
                    self.progress["errors"] = (self.progress.get("errors") or [])[-199:] + \
                        [{"path": "", "error": f"{type(e).__name__}: {e}"}]
                self.set_progress({"running": False, "phase": "failed",
                                   "message": f"{name.capitalize()} failed: {e}"})
            finally:
                self.job_lock.release()

        threading.Thread(target=run, name=f"job-{name}", daemon=True).start()

    def job_scan(self, new_root: Optional[str] = None) -> None:
        from . import classify, inventory, kits
        st = self.store
        if new_root:  # switched only while holding the job lock
            st.set_root(new_root)
        root = st.root
        dest_root = st.dest_root
        if not root or not os.path.isdir(root):
            raise FileNotFoundError(f"Folder not found: {root}")
        res = inventory.scan(root, dest_root, self.set_progress, dict(st.hash_cache), self.cancel)
        if self.cancel.is_set():
            return
        items = res["items"]
        for s in res.get("skipped", []):
            with self._plock:
                self.progress["errors"] = (self.progress.get("errors") or [])[-199:] + \
                    [{"path": s.get("path"), "error": "Skipped: " + str(s.get("reason"))}]
        self.set_progress({"job": "scan", "phase": "metadata", "done": 0, "total": len(items)})
        classify.enrich(items, self.set_progress, self.cancel)
        if self.cancel.is_set():
            return
        self.set_progress({"job": "scan", "phase": "analyzing", "done": 0, "total": 0,
                           "message": "Measuring tempo and key"})
        acache = classify.analyze_items(items, dict(st.analysis_cache), self.set_progress,
                                        self.cancel)
        if self.cancel.is_set():
            return
        self.set_progress({"job": "scan", "phase": "kits", "message": "Detecting complete kits"})
        kit_list = kits.detect_kits(items)
        with st.lock:
            if os.path.normcase(st.root) != os.path.normcase(root):
                raise RuntimeError("The collection folder changed during the scan; results discarded.")
            st.set_scan_result(items, kit_list, res.get("hash_cache", {}), acache)
        self.skipped = res.get("skipped", [])
        n_in = sum(1 for i in items if i["origin"] == "incoming")
        self.set_progress({"job": "scan", "message":
                           f"Scan finished: {n_in} files found, {len(kit_list)} kits. Nothing was changed."})

    def job_plan(self) -> None:
        from . import planner
        st = self.store
        self.set_progress({"job": "plan", "phase": "planning", "message": "Building plan"})
        with st.lock:
            gen, root = st.gen, st.root
            plan = planner.build_plan(st.items, st.kits, st.root, st.dest_root)
        with st.lock:
            if os.path.normcase(st.root) != os.path.normcase(root):
                raise RuntimeError("The collection folder changed while planning; plan discarded.")
            st.set_plan(plan, gen)
        self.set_progress({"job": "plan", "message": f"Plan ready: {len(plan['ops'])} operations. Review, then approve."})

    def job_execute(self, plan_hash: str) -> None:
        self.executing = True
        try:
            self._job_execute(plan_hash)
        finally:
            self.executing = False

    def _job_execute(self, plan_hash: str) -> None:
        from . import executor, export
        st = self.store
        with st.lock:
            if st.approved_hash != plan_hash or not st.plan or st.plan["plan_hash"] != plan_hash or st.plan_stale:
                raise PermissionError("Plan is not approved")
            plan = st.plan
        self.set_progress({"job": "execute", "phase": "executing", "total": len(plan["ops"])})
        results = executor.execute(plan, st.journal_path(plan_hash), self.set_progress, self.cancel)
        ex = {"plan_hash": plan_hash, "ts": time.time(), "results": results,
              "interrupted": self.cancel.is_set(), "reports": {}, "verification": None}
        st.set_execution(ex)
        if self.cancel.is_set():
            self.set_progress({"message": "Run paused. Run the approved plan again to resume; completed operations will not repeat."})
            return
        self.set_progress({"job": "export", "phase": "reports", "message": "Writing catalog reports"})
        catalog_dir = os.path.join(plan["dest_root"], models.CATALOG_DIR)  # the approved destination
        with st.lock:
            ex["reports"] = export.write_reports(catalog_dir, st.items, st.kits, plan, results)
            self.set_progress({"phase": "verifying", "message": "Verifying library"})
            ex["verification"] = export.verify_library(st.items, st.kits, plan, results)
        st.set_execution(ex)
        counts = _count(results, "status")
        self.set_progress({"message": "Run complete: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))})


def _count(rows, key) -> dict:
    c: dict = {}
    for r in rows:
        c[r.get(key)] = c.get(r.get(key), 0) + 1
    return c


class Handler(BaseHTTPRequestHandler):
    app: App = None  # type: ignore
    server_version = "SampleOrganizer/1.0"

    def log_message(self, fmt, *args):  # quiet
        pass

    # ------------------------------------------------------------- helpers
    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, msg: str) -> None:
        self._send(code, {"error": msg})

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost", "::1")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            raise ValueError("Request too large")
        raw = self.rfile.read(n) if n else b"{}"
        return json.loads(raw or b"{}")

    # -------------------------------------------------------------- routes
    def do_GET(self):
        if not self._host_ok():
            return self._err(403, "Forbidden host")
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        try:
            if u.path.startswith("/api/"):
                return self._api_get(u.path, q)
            return self._static(u.path)
        except KeyError as e:
            return self._err(404, f"Not found: {e}")
        except ValueError as e:
            return self._err(400, str(e))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            return self._err(500, f"{type(e).__name__}: {e}")

    def do_DELETE(self):
        if not self._host_ok():
            return self._err(403, "Forbidden host")
        if self.headers.get("X-SLO") != "1":
            return self._err(403, "Missing X-SLO header")
        m = re.fullmatch(r"/api/starters/([\w-]+)", urllib.parse.urlsplit(self.path).path)
        if not m:
            return self._err(404, "Not found")
        if not self.app.store.delete_starter(m.group(1)):
            return self._err(404, "Saved set not found")
        return self._send(200, {"ok": True})

    def do_POST(self):
        if not self._host_ok():
            return self._err(403, "Forbidden host")
        if self.headers.get("X-SLO") != "1":
            return self._err(403, "Missing X-SLO header")
        u = urllib.parse.urlsplit(self.path)
        try:
            return self._api_post(u.path, self._body())
        except PermissionError as e:
            return self._err(409, str(e))
        except RuntimeError as e:
            return self._err(409, str(e))
        except (ValueError, KeyError) as e:
            return self._err(400, str(e).strip("'\""))
        except Exception as e:
            traceback.print_exc()
            return self._err(500, f"{type(e).__name__}: {e}")

    def _static(self, path: str):
        if path in ("", "/"):
            path = "/index.html"
        rel = urllib.parse.unquote(path).lstrip("/")
        full = os.path.realpath(os.path.join(UI_DIR, rel))
        if not full.startswith(os.path.realpath(UI_DIR) + os.sep) or not os.path.isfile(full):
            return self._err(404, "Not found")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js"):
            ctype = "text/javascript"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _propose(self):
        try:
            from . import planner
            return planner.propose_dest
        except ImportError:
            return None

    def _api_get(self, path: str, q: dict):
        app, st = self.app, self.app.store
        if path == "/api/state":
            plan = None
            if st.plan:
                plan = {"plan_hash": st.plan["plan_hash"], "created": st.plan.get("created"),
                        "stale": st.plan_stale, "approved": st.approved_hash == st.plan["plan_hash"],
                        "summary": st.plan.get("summary")}
            ex = None
            if st.execution:
                e = st.execution
                import datetime as _dt
                ran = _dt.datetime.fromtimestamp(e.get("ts") or time.time()).isoformat(timespec="seconds")
                ex = {"ran": ran, "plan_hash": e.get("plan_hash"), "interrupted": e.get("interrupted"),
                      "results_summary": _count(e.get("results", []), "status"),
                      "reports": e.get("reports") or {}, "verification": e.get("verification")}
            if ex and isinstance(ex.get("verification"), dict):
                v = dict(ex["verification"])
                v.setdefault("storage_avoided_bytes", v.get("bytes_avoided"))
                v.setdefault("space_freed_bytes", v.get("bytes_freed"))
                v.setdefault("unresolved", [*(v.get("missing") or []), *(v.get("size_mismatch") or []),
                                            *(v.get("hash_mismatch") or []),
                                            *(v.get("link_identity_mismatch") or []),
                                            *(v.get("kits_incomplete") or [])])
                ex["verification"] = v
            from .store import scan_summary
            with st.lock:
                ssum = scan_summary(st) if st.items else None
            return self._send(200, {"root": st.root, "dest_root": st.dest_root, "data_dir": st.data_dir,
                                    "scan_summary": ssum,
                                    "has_inventory": bool(st.items), "progress": app.get_progress(),
                                    "plan": plan, "execution": ex,
                                    "counts": {"files": sum(1 for i in st.items if i["origin"] == "incoming"),
                                               "library": sum(1 for i in st.items if i["origin"] == "library"),
                                               "kits": len(st.kits)}})
        if path == "/api/progress":
            return self._send(200, app.get_progress())
        if path == "/api/packs":
            return self._send(200, st.packs(q.get("q", "")))
        if path == "/api/facets":
            return self._send(200, st.facets())
        if path == "/api/samples":
            total, rows = st.query(q)
            off = max(0, int(q.get("offset") or 0))
            lim = min(1000, max(1, int(q.get("limit") or 200)))
            prop = self._propose()
            return self._send(200, {"total": total, "offset": off,
                                    "items": [st.row(i, prop) for i in rows[off:off + lim]]})
        if path == "/api/starters":
            out = []
            for sv in st.starters():
                sv = dict(sv)
                sv["rows"] = [st.row(st.by_id[i]) for i in sv.get("ids", []) if i in st.by_id]
                out.append(sv)
            return self._send(200, {"items": out})
        m = re.fullmatch(r"/api/samples/([\w-]+)/matches", path)
        if m:
            from . import matching
            role = q.get("role") or None
            if role and role not in matching.ROLES:
                raise ValueError("Unknown role: " + role)
            return self._send(200, {"items": matching.matches(
                st, m.group(1), role, int(q.get("limit") or 30))})
        m = re.fullmatch(r"/api/samples/([\w-]+)", path)
        if m:
            it = st.by_id[m.group(1)]
            r = st.row(it, self._propose())
            r.update({"meta": it.get("meta"), "tags_raw": it.get("tags_raw"), "audio": it.get("audio"),
                      "near_dupes": [st.row(st.by_id[i]) for i in it.get("near_dupes", []) if i in st.by_id][:20],
                      "dupes": [st.row(o) for o in st.items
                                if it.get("sha256") and o.get("sha256") == it["sha256"] and o is not it][:50]})
            return self._send(200, r)
        if path == "/api/kits":
            pack, text = q.get("pack"), (q.get("q") or "").casefold()
            out = []
            with st.lock:
                prop = self._propose()
                for k in st.kits:
                    if pack and k.get("pack") != pack:
                        continue
                    if text and text not in (k["name"] + " " + (k.get("manufacturer") or "")).casefold():
                        continue
                    mem = st._kit_members(k)
                    d = dict(k)
                    d["member_count"] = len(mem)
                    d["bytes"] = sum(i["size"] for i in mem)
                    d["proposed_root"] = None
                    if prop and mem:
                        try:
                            p = prop(mem[0], k, st.dest_root)
                            rel = mem[0]["rel_path"][len(k["root_rel"]):].lstrip("/") if k["root_rel"] else mem[0]["rel_path"]
                            if p and rel:
                                tail = rel.replace("/", os.sep)
                                d["proposed_root"] = p[:-len(tail)].rstrip("\\/") if p.endswith(tail) else os.path.dirname(p)
                        except Exception:
                            pass
                    out.append(d)
            return self._send(200, out)
        m = re.fullmatch(r"/api/kits/([\w-]+)", path)
        if m:
            k = dict(st.kit_by_id[m.group(1)])
            prop = self._propose()
            k["members"] = [st.row(i, prop) for i in st._kit_members(k)]
            return self._send(200, k)
        m = re.fullmatch(r"/api/audio/([\w-]+)", path)
        if m:
            return self._audio(st.by_id[m.group(1)])
        if path == "/api/plan":
            return self._plan(q)
        if path == "/api/log":
            return self._log(q)
        return self._err(404, "Unknown endpoint")

    def _audio(self, it: dict):
        st = self.app.store
        path = st._final_paths.get(it["id"]) or it["abs_path"]
        if not os.path.isfile(path):
            path = it["abs_path"]
        size = os.path.getsize(path)
        ctype = AUDIO_TYPES.get(it["ext"], "application/octet-stream")
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        status = 200
        if rng:
            mm = re.match(r"bytes=(\d*)-(\d*)", rng)
            if mm:
                if mm.group(1):
                    start = int(mm.group(1))
                    if mm.group(2):
                        end = min(int(mm.group(2)), size - 1)
                elif mm.group(2):
                    start = max(0, size - int(mm.group(2)))
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:  # read-only
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _plan(self, q: dict):
        st = self.app.store
        if not st.plan:
            return self._send(200, {"plan_hash": None, "stale": False, "approved": False,
                                    "summary": None, "total": 0, "ops": [], "items": []})
        ops = st.plan["ops"]
        action, flag, text = q.get("action"), q.get("flag"), (q.get("q") or "").casefold()
        kit, pack, cat = q.get("kit"), q.get("pack"), q.get("category")
        out = []
        for o in ops:
            if action and o.get("action") != action:
                continue
            if flag and flag not in (o.get("flags") or []):
                continue
            if kit and o.get("kit_id") != kit:
                continue
            if cat and o.get("category") != cat:
                continue
            it = st.by_id.get(o.get("item_id"), {})
            if pack and it.get("pack") != pack:
                continue
            if text and text not in (o.get("source", "") + " " + (o.get("dest") or "") + " " + o.get("reason", "")).casefold():
                continue
            out.append((o, it))
        group = q.get("group")
        gkey = {"pack": lambda p: (p[1].get("pack") or ""),
                "kit": lambda p: (st.kit_name(p[0].get("kit_id")) or "~"),
                "category": lambda p: (p[0].get("category") or "~"),
                "action": lambda p: p[0].get("action"),
                "warning": lambda p: (0 if p[0].get("flags") or p[0].get("action") == "hold" else 1)}.get(group)
        if gkey:
            out.sort(key=lambda p: (gkey(p), p[1].get("rel_path", "")))
        off = max(0, int(q.get("offset") or 0))
        lim = min(1000, max(1, int(q.get("limit") or 200)))
        rows = []
        for o, it in out[off:off + lim]:
            r = dict(o)
            r.update({"name": it.get("name"), "pack": it.get("pack"), "rel_path": it.get("rel_path"),
                      "kit_name": st.kit_name(o.get("kit_id"))})
            rows.append(r)
        return self._send(200, {"plan_hash": st.plan["plan_hash"], "stale": st.plan_stale,
                                "approved": st.approved_hash == st.plan["plan_hash"],
                                "summary": st.plan.get("summary"), "total": len(out), "ops": rows,
                                "items": rows})

    def _log(self, q: dict):
        st = self.app.store
        results = list((st.execution or {}).get("results", []))
        if not results and st.plan:
            # live view of an in-progress run from the journal
            try:
                from . import executor
                jp = st.journal_path(st.plan["plan_hash"])
                if os.path.exists(jp):
                    last = {}
                    for rec in executor.read_journal(jp):
                        last[rec.get("op_id")] = rec
                    ops = {o["id"]: o for o in st.plan["ops"]}
                    for oid, rec in last.items():
                        o = ops.get(oid, {})
                        results.append({"op_id": oid, "action": o.get("action"), "status": rec.get("phase"),
                                        "source": o.get("source"), "dest": o.get("dest"),
                                        "timestamp": rec.get("ts"), "error": rec.get("error")})
            except Exception:
                pass
        status, text = q.get("status"), (q.get("q") or "").casefold()
        ops = {o["id"]: o for o in (st.plan or {}).get("ops", [])}
        out = []
        for r in results:
            if status and r.get("status") != status:
                continue
            if text and text not in ((r.get("source") or "") + " " + (r.get("dest") or "") + " " + (r.get("error") or "")).casefold():
                continue
            rr = dict(r)
            it = st.by_id.get(ops.get(r.get("op_id"), {}).get("item_id"), {})
            rr["name"] = it.get("name") or os.path.basename(r.get("source") or "")
            out.append(rr)
        off = max(0, int(q.get("offset") or 0))
        lim = min(1000, max(1, int(q.get("limit") or 200)))
        return self._send(200, {"total": len(out), "items": out[off:off + lim]})

    MUTATING = {"/api/scan", "/api/items/edit", "/api/kits/edit", "/api/kits/create",
                "/api/plan/build", "/api/plan/approve", "/api/execute"}

    def _api_post(self, path: str, b: dict):
        app, st = self.app, self.app.store
        if path in self.MUTATING and (app.executing or (app.progress.get("running") and
                                                        app.progress.get("job") in ("execute", "export"))):
            raise RuntimeError("Changes are locked while the approved plan is running.")
        if path == "/api/scan" and app.progress.get("running"):
            raise RuntimeError(f"Another task is running ({app.progress.get('job')}). Wait for it or cancel it.")
        if path == "/api/scan":
            root = None
            if b.get("root"):
                root = os.path.abspath(str(b["root"]).strip().strip('"'))
                if not os.path.isdir(root):
                    raise ValueError(f"Folder not found: {root}")
            app.start_job("scan", lambda: app.job_scan(root))
            return self._send(200, {"ok": True})
        if path == "/api/cancel":
            app.cancel.set()
            return self._send(200, {"ok": True})
        if path == "/api/items/edit":
            items = st.edit_items(list(b.get("ids") or []), dict(b.get("changes") or {}))
            prop = self._propose()
            return self._send(200, {"ok": True, "updated": len(items), "items": [st.row(i, prop) for i in items]})
        if path == "/api/starter":
            from . import matching
            bpm = b.get("bpm")
            return self._send(200, matching.starter(
                st, b.get("key") or None, float(bpm) if bpm else None,
                b.get("locked") or {}, int(b["seed"]) if b.get("seed") else None))
        if path == "/api/starters":
            bpm = b.get("bpm")
            return self._send(200, {"ok": True, "starter": st.save_starter(
                b.get("name") or "", b.get("key"), float(bpm) if bpm else None, b.get("ids") or [])})
        if path == "/api/kits/edit":
            kit = st.edit_kit(b)
            return self._send(200, {"ok": True, "kit": kit})
        if path == "/api/kits/create":
            name = (b.get("name") or "").strip()
            if not name:
                raise ValueError("Kit name required")
            kit = st.edit_kit({"action": "create", "name": name, "ids": list(b.get("ids") or [])})
            return self._send(200, {"ok": True, "kit": kit})
        if path == "/api/plan/build":
            if not st.items:
                raise ValueError("Run a scan first")
            app.start_job("plan", app.job_plan)
            return self._send(200, {"ok": True})
        if path == "/api/plan/approve":
            st.approve(str(b.get("plan_hash") or ""))
            return self._send(200, {"ok": True})
        if path == "/api/execute":
            h = str(b.get("plan_hash") or "")
            if not st.plan or st.approved_hash != h or st.plan["plan_hash"] != h or st.plan_stale:
                raise PermissionError("Only the plan you approved can run. Approve the current plan first.")
            app.start_job("execute", lambda: app.job_execute(h))
            return self._send(200, {"ok": True})
        if path == "/api/reveal":
            from . import winfs
            p: Optional[str] = None
            if b.get("id"):
                it = st.by_id[b["id"]]
                p = st._final_paths.get(it["id"]) or it["abs_path"]
            elif b.get("path"):
                p = str(b["path"])
                if not (winfs.is_within(p, st.root) or winfs.is_within(p, st.data_dir)):
                    raise ValueError("Path is outside the library")
            if not p or not os.path.exists(p):
                raise ValueError("File not found")
            winfs.reveal_in_explorer(p)
            return self._send(200, {"ok": True})
        return self._err(404, "Unknown endpoint")


def serve(store: Store, host: str = "127.0.0.1", port: int = 8765) -> tuple[ThreadingHTTPServer, App]:
    app = App(store)
    handler = type("BoundHandler", (Handler,), {"app": app})
    last_err = None
    for p in [port] + list(range(port + 1, port + 20)) + [0]:
        try:
            httpd = ThreadingHTTPServer((host, p), handler)
            httpd.daemon_threads = True
            return httpd, app
        except OSError as e:
            last_err = e
    raise last_err  # type: ignore

"""Undo a completed run: run history and reverse plans.

Run history
    Each forward run is saved in the data dir as ``runs/<run_id>.json.gz`` when
    it finishes (also when it was interrupted), so a later rescan or restart
    cannot destroy what undo needs. ``run_id`` is derived from the collection
    root and the plan hash, so resuming an interrupted run updates the same
    record. ``runs/index.json`` keeps small summaries for the list endpoint.

Reverse plan
    Built only from journal-confirmed results (``done`` / ``already-done``):
    a forward ``move`` src->dest becomes a ``move`` dest->src; a forward ``link``
    becomes an ``unlink`` that removes only that extra name. Unlinks run first.
    Every op is checked at build time (identity recorded at run time, SHA-256,
    original path free case-insensitively) and becomes ``hold`` with a plain
    reason otherwise. The executor revalidates everything again at run time.

The undo plan is an ordinary plan (``kind: "undo"``, ``undo_of: run_id``)
stored as the current plan, approved by its exact hash and executed through
``/api/plan/approve`` and ``/api/execute``.

Folders that the forward run created inside Samples are left in place after
an undo (they may be empty); nothing here removes folders.
"""
from __future__ import annotations

import csv
import datetime as _dt
import gzip
import hashlib
import json
import os
import threading
import time
import traceback

from . import executor, models
from .winfs import file_identity, is_reparse_point, long_path

_LOCK = threading.RLock()
RUN_VERSION = 1
OP_KEYS = ("id", "action", "item_id", "source", "dest", "link_to", "depends_on", "canonical",
           "sha256", "size", "mtime_ns", "kit_id", "category")
DONE = ("done", "already-done")


# ---------------------------------------------------------------------------
# Run history persistence
# ---------------------------------------------------------------------------
def runs_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "runs")


def run_id_for(root: str, plan_hash: str) -> str:
    rk = hashlib.sha1(os.path.normcase(root or "").encode("utf-8")).hexdigest()[:8]
    return f"{rk}-{(plan_hash or '')[:16]}"


def _valid_id(run_id: str) -> bool:
    return bool(run_id) and all(c.isalnum() or c == "-" for c in run_id) and len(run_id) < 64


def _write(path: str, raw: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_run(data_dir: str, run_id: str) -> dict | None:
    if not _valid_id(run_id):
        return None
    try:
        with gzip.open(os.path.join(runs_dir(data_dir), run_id + ".json.gz"), "rt",
                       encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _summary(run: dict) -> dict:
    counts: dict = {}
    for r in run.get("results", {}).values():
        counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
    ops = run.get("ops", [])
    res = run.get("results", {})
    return {"run_id": run["run_id"], "ts": run.get("ts"), "first_ts": run.get("first_ts"),
            "root": run.get("root"), "dest_root": run.get("dest_root"),
            "plan_hash": run.get("plan_hash"), "interrupted": bool(run.get("interrupted")),
            "counts": counts,
            "moved": sum(1 for o in ops if o["action"] == "move"
                         and res.get(o["id"], {}).get("status") in DONE),
            "linked": sum(1 for o in ops if o["action"] == "link"
                          and res.get(o["id"], {}).get("status") in DONE),
            "undo": run.get("undo")}


def _read_index(data_dir: str) -> dict:
    try:
        with open(os.path.join(runs_dir(data_dir), "index.json"), "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _rebuild_index(data_dir: str) -> dict:
    idx = {}
    try:
        names = os.listdir(runs_dir(data_dir))
    except OSError:
        names = []
    for n in names:
        if n.endswith(".json.gz"):
            run = load_run(data_dir, n[:-8])
            if run and run.get("run_id"):
                idx[run["run_id"]] = _summary(run)
    return idx


def save_run(data_dir: str, run: dict) -> None:
    with _LOCK:
        raw = gzip.compress(json.dumps(run, ensure_ascii=False).encode("utf-8"), compresslevel=5)
        _write(os.path.join(runs_dir(data_dir), run["run_id"] + ".json.gz"), raw)
        idx = _read_index(data_dir)
        idx[run["run_id"]] = _summary(run)
        _write(os.path.join(runs_dir(data_dir), "index.json"),
               json.dumps(idx, ensure_ascii=False).encode("utf-8"))


def list_runs(data_dir: str) -> list:
    """Run summaries, newest first."""
    with _LOCK:
        idx = _read_index(data_dir)
        if not idx and os.path.isdir(runs_dir(data_dir)):
            idx = _rebuild_index(data_dir)
    return sorted(idx.values(), key=lambda r: r.get("ts") or 0, reverse=True)


def record_run(store, plan: dict, ex: dict) -> dict | None:
    """Persist (or update) the history record of a forward run. Never raises."""
    try:
        if not plan or plan.get("kind") == "undo" or ex.get("plan_hash") != plan.get("plan_hash"):
            return None
        rid = run_id_for(plan.get("root") or store.root, plan["plan_hash"])
        old = load_run(store.data_dir, rid) or {}
        by_id = getattr(store, "by_id", {}) or {}
        ops = []
        for o in plan.get("ops", []):
            if o.get("action") not in ("move", "link"):
                continue
            r = {k: o.get(k) for k in OP_KEYS}
            it = by_id.get(o.get("item_id")) or {}
            r["name"] = it.get("name") or os.path.basename(o.get("source") or "")
            r["pack"] = it.get("pack")
            ops.append(r)
        wanted = {o["id"] for o in ops}
        results = dict(old.get("results") or {})
        for res in ex.get("results", []):
            oid = res.get("op_id")
            if oid not in wanted:
                continue
            prev = results.get(oid)
            if prev and prev.get("status") in DONE and res.get("status") not in DONE:
                continue  # an earlier pass already completed it; keep that record
            results[oid] = {"status": res.get("status"), "file_id": res.get("file_id")
                            or (prev or {}).get("file_id"),
                            "timestamp": res.get("timestamp"), "error": res.get("error")}
        run = {"version": RUN_VERSION, "run_id": rid, "kind": "forward",
               "root": plan.get("root") or store.root, "dest_root": plan.get("dest_root"),
               "plan_hash": plan["plan_hash"], "plan_created": plan.get("created"),
               "first_ts": old.get("first_ts") or ex.get("ts") or time.time(),
               "ts": ex.get("ts") or time.time(), "interrupted": bool(ex.get("interrupted")),
               "ops": ops, "results": results, "undo": old.get("undo")}
        save_run(store.data_dir, run)
        return run
    except Exception:  # noqa: BLE001 - history must never break a run
        traceback.print_exc()
        return None


def backfill(store) -> None:
    """Create a history record for a run made by the previous version (no runs/ yet).

    Uses execution.json.gz when it belongs to the current plan, else the journal.
    """
    try:
        plan = store.plan
        if not plan or plan.get("kind") == "undo" or not plan.get("plan_hash"):
            return
        rid = run_id_for(plan.get("root") or store.root, plan["plan_hash"])
        if load_run(store.data_dir, rid):
            return
        ex = store.execution if (store.execution or {}).get("plan_hash") == plan["plan_hash"] else None
        if ex is None:
            jp = store.journal_path(plan["plan_hash"])
            if not os.path.exists(jp):
                return
            last: dict = {}
            for rec in executor.read_journal(jp):
                last[rec["op_id"]] = rec
            results = [{"op_id": oid, "status": "done" if rec.get("phase") == "done" else rec.get("phase"),
                        "file_id": rec.get("file_id"), "timestamp": rec.get("ts"),
                        "error": rec.get("error")} for oid, rec in last.items()]
            if not any(r["status"] == "done" for r in results):
                return
            ex = {"plan_hash": plan["plan_hash"], "ts": os.path.getmtime(jp), "results": results,
                  "interrupted": False}
        record_run(store, plan, ex)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def mark_undone(data_dir: str, run_id: str, info: dict) -> None:
    run = load_run(data_dir, run_id)
    if not run:
        return
    run["undo"] = info
    save_run(data_dir, run)


def undoable(run_sum: dict, root: str) -> tuple[bool, str | None]:
    if os.path.normcase(run_sum.get("root") or "") != os.path.normcase(root or ""):
        return False, "This run was made in a different packs folder."
    if not (run_sum.get("moved") or run_sum.get("linked")):
        return False, "Nothing was moved or linked in this run."
    u = run_sum.get("undo")
    if u and u.get("status") == "undone":
        return False, "This run was already undone."
    return True, None


# ---------------------------------------------------------------------------
# Reverse plan
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _check_file(path: str, want_id: str | None, sha: str | None, size) -> str | None:
    """None when ``path`` is still the file the run left there; else a plain reason."""
    if not os.path.lexists(long_path(path)):
        return "file no longer exists at its organized location"
    if is_reparse_point(path) or not os.path.isfile(long_path(path)):
        return "organized location is no longer a regular file"
    if want_id and file_identity(path) != want_id:
        return "changed since the run (replaced by a different file)"
    try:
        st = os.stat(long_path(path))
    except OSError as e:
        return f"cannot read the file: {e}"
    if size is not None and st.st_size != size:
        return "changed since the run (size differs)"
    try:
        if executor._hash(path) != sha:
            return "changed since the run (content differs)"
    except OSError as e:
        return f"cannot read the file: {e}"
    return None


def build_undo_plan(run: dict, progress=None, cancel=None) -> dict:
    root, dest_root = run["root"], run["dest_root"]
    res = run.get("results", {})
    fwd = [o for o in run.get("ops", []) if res.get(o["id"], {}).get("status") in DONE]
    unlinks, moves, others = [], [], []
    total = len(fwd)
    prog = {"job": "plan", "phase": "checking", "running": True, "done": 0, "total": total,
            "current": "", "message": "Checking files for undo"}
    for n, o in enumerate(sorted(fwd, key=lambda o: 0 if o["action"] == "link" else 1)):
        if cancel is not None and cancel.is_set():
            break
        if progress and n % 20 == 0:
            progress(dict(prog, done=n, current=o.get("dest") or ""))
        r = res[o["id"]]
        here, orig = o.get("dest"), o.get("source")
        base = {"item_id": o.get("item_id"), "sha256": o.get("sha256"), "size": o.get("size"),
                "kit_id": o.get("kit_id"), "category": o.get("category"),
                "file_id": r.get("file_id"), "undo_of_op": o["id"], "flags": ["undo"],
                "orig_name": o.get("name"), "orig_pack": o.get("pack"),
                "link_to": None, "depends_on": None}
        if o["action"] == "link":
            op = dict(base, source=here, dest=None, link_to=o.get("link_to"))
            reason = None
            if not os.path.lexists(long_path(here or "")):
                op.update(action="keep", reason="This kit link was already removed; nothing to undo")
                op["id"] = models.op_id("undo-keep", o.get("item_id") or "", here)
                others.append(op)
                continue
            reason = _check_file(here, r.get("file_id"), o.get("sha256"), o.get("size"))
            tgt = o.get("link_to") or ""
            if not reason:
                if not os.path.lexists(long_path(tgt)):
                    reason = "the file this link points to no longer exists; removing the link could remove the last copy"
                elif file_identity(tgt) != file_identity(here):
                    reason = "the link and the file it points to are no longer the same file"
                elif os.stat(long_path(here)).st_nlink < 2:
                    reason = "this is the last name of the file"
            if reason:
                op.update(action="hold", reason=reason)
                op["id"] = models.op_id("undo-hold", o.get("item_id") or "", here)
                others.append(op)
            else:
                st = os.stat(long_path(here))
                op.update(action="unlink", mtime_ns=st.st_mtime_ns,
                          reason="Remove the kit link this run created (the file stays at "
                                 + os.path.basename(tgt) + ")")
                op["id"] = models.op_id("undo-unlink", o.get("item_id") or "", here)
                unlinks.append(op)
            continue
        # forward move: put it back
        op = dict(base, source=here, dest=orig)
        if not os.path.lexists(long_path(here or "")) and os.path.isfile(long_path(orig or "")) \
                and r.get("file_id") and file_identity(orig) == r.get("file_id"):
            op.update(action="keep", reason="Already back at its original location")
            op["id"] = models.op_id("undo-keep", o.get("item_id") or "", orig)
            others.append(op)
            continue
        reason = _check_file(here, r.get("file_id"), o.get("sha256"), o.get("size"))
        if not reason and executor._dest_exists(orig):
            reason = "original location is occupied"
        if not reason:
            bad = executor._check_undo_dest(orig, root, dest_root)
            if bad:
                reason = bad
        if reason:
            op.update(action="hold", reason=reason)
            op["id"] = models.op_id("undo-hold", o.get("item_id") or "", here)
            others.append(op)
        else:
            op.update(action="move", mtime_ns=os.stat(long_path(here)).st_mtime_ns,
                      reason="Move back to its original location")
            op["id"] = models.op_id("undo-move", o.get("item_id") or "", orig)
            moves.append(op)
    ops = unlinks + moves + others
    by_action: dict = {}
    for op in ops:
        by_action[op["action"]] = by_action.get(op["action"], 0) + 1
    plan = {"kind": "undo", "undo_of": run["run_id"], "created": _now_iso(),
            "root": root, "dest_root": dest_root, "ops": ops,
            "summary": {"kind": "undo", "undo_of": run["run_id"],
                        "run_ts": run.get("ts"), "run_plan_hash": run.get("plan_hash"),
                        "total_files": len(ops),
                        "total_bytes": sum(int(o.get("size") or 0) for o in ops),
                        "by_action": by_action, "held": by_action.get("hold", 0),
                        "duplicate_groups": 0, "duplicate_bytes_avoided": 0, "collisions": 0}}
    plan["plan_hash"] = models.plan_hash(ops)
    return plan


# ---------------------------------------------------------------------------
# Jobs (called by api_undo / server)
# ---------------------------------------------------------------------------
def job_build(app, run_id: str) -> None:
    st = app.store
    with st.lock:
        gen, root = st.gen, st.root
    run = load_run(st.data_dir, run_id)
    if not run:
        raise KeyError("Unknown run")
    app.set_progress({"job": "plan", "phase": "checking", "message": "Checking files for undo"})
    plan = build_undo_plan(run, app.set_progress, app.cancel)
    if app.cancel.is_set():
        return
    with st.lock:
        if os.path.normcase(st.root) != os.path.normcase(root):
            raise RuntimeError("The collection folder changed while planning; plan discarded.")
        st.set_plan(plan, gen)
    ba = plan["summary"]["by_action"]
    app.set_progress({"job": "plan", "message":
                      f"Undo plan ready: {ba.get('move', 0)} to move back, {_n(ba.get('unlink', 0), 'link')} "
                      f"to remove, {ba.get('hold', 0)} held. Review, then approve."})


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def write_undo_report(catalog_dir: str, plan: dict, results: list) -> str:
    """Write Undo_Report.csv next to the forward reports. Never overwrites: a new
    numbered name is used when one exists. Returns the path."""
    os.makedirs(catalog_dir, exist_ok=True)
    n = 1
    while True:
        name = "Undo_Report.csv" if n == 1 else f"Undo_Report ({n}).csv"
        path = os.path.join(catalog_dir, name)
        if not executor._dest_exists(path):
            break
        n += 1
    ops = {o["id"]: o for o in plan.get("ops", [])}
    with open(path, "x", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Status", "Action", "Organized Path", "Original Path", "Remaining File",
                    "SHA256", "Note"])
        for r in results:
            o = ops.get(r.get("op_id"), {})
            w.writerow([r.get("status"), o.get("action"), o.get("source") or "",
                        o.get("dest") or "", o.get("link_to") or "", o.get("sha256") or "",
                        r.get("error") or o.get("reason") or ""])
    return path


def run_undo(app, plan: dict, plan_hash: str) -> None:
    """Execute an approved undo plan (called from Server._job_execute)."""
    st = app.store
    app.set_progress({"job": "execute", "phase": "executing", "total": len(plan["ops"]),
                      "message": "Undoing run"})
    results = executor.execute(plan, st.journal_path(plan_hash), app.set_progress, app.cancel)
    interrupted = app.cancel.is_set()
    ex = {"plan_hash": plan_hash, "ts": time.time(), "results": results, "kind": "undo",
          "undo_of": plan.get("undo_of"), "interrupted": interrupted, "reports": {},
          "verification": None}
    st.set_execution(ex)
    counts: dict = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    writes = [r for r in results if r.get("action") in ("move", "unlink")]
    bad = sum(1 for r in writes if r["status"] not in DONE) + \
        sum(1 for o in plan["ops"] if o["action"] == "hold")
    status = "interrupted" if interrupted else ("undone" if not bad else "partly-undone")
    mark_undone(st.data_dir, plan.get("undo_of"), {
        "status": status, "plan_hash": plan_hash, "ts": ex["ts"], "counts": counts})
    if interrupted:
        app.set_progress({"message": "Undo paused. Run the approved undo plan again to resume; "
                                     "completed operations will not repeat."})
        return
    app.set_progress({"job": "export", "phase": "reports", "message": "Writing undo report"})
    try:
        p = write_undo_report(os.path.join(plan["dest_root"], models.CATALOG_DIR), plan, results)
        ex["reports"] = {os.path.basename(p): p}
    except OSError as e:
        ex["reports"] = {}
        app.set_progress({"errors": [{"path": "", "error": f"Undo report not written: {e}"}]})
    st.set_execution(ex)
    moved = sum(1 for r in results if r.get("action") == "move" and r["status"] in DONE)
    unl = sum(1 for r in results if r.get("action") == "unlink" and r["status"] in DONE)
    held = sum(1 for r in results if r["status"] in ("held", "failed"))
    app.set_progress({"message": f"Undo complete: {_n(moved, 'file')} back in place, {_n(unl, 'kit link')} "
                                 f"removed, {held} held. Rescan before building a new plan."})

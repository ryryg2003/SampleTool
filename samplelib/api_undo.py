"""HTTP routes for undo (loaded by server.py as an extension).

GET  /api/runs        -> {runs: [RunSummary], rescan_needed: bool}
POST /api/undo/build  {run_id?} -> {ok, run_id}   (job "plan"; the undo plan becomes
                                                    the current plan, approve + execute as usual)
"""
from __future__ import annotations

import datetime as _dt
import os

from . import undo

MUTATING = {"/api/undo/build"}


def init(app) -> None:
    undo.backfill(app.store)


def _iso(ts) -> str | None:
    try:
        return _dt.datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return None


def _rescan_needed(st) -> bool:
    ex = st.execution or {}
    return ex.get("kind") == "undo" and not ex.get("interrupted")


def runs_payload(app) -> dict:
    st = app.store
    cur = (st.execution or {}).get("plan_hash")
    out = []
    for r in undo.list_runs(st.data_dir):
        ok, why = undo.undoable(r, st.root)
        u = r.get("undo")
        out.append({"run_id": r["run_id"], "ran": _iso(r.get("ts")), "plan_hash": r.get("plan_hash"),
                    "root": r.get("root"), "dest_root": r.get("dest_root"),
                    "interrupted": r.get("interrupted"), "counts": r.get("counts") or {},
                    "moved": r.get("moved", 0), "linked": r.get("linked", 0),
                    "undoable": ok, "why_not": why, "is_current": r.get("plan_hash") == cur,
                    "undo": None if not u else {"status": u.get("status"), "ran": _iso(u.get("ts")),
                                                "plan_hash": u.get("plan_hash"),
                                                "counts": u.get("counts") or {}}})
    return {"runs": out, "rescan_needed": _rescan_needed(st)}


def state(app) -> dict:
    st = app.store
    plan = st.plan or {}
    return {"undo": {"rescan_needed": _rescan_needed(st),
                     "execution_kind": (st.execution or {}).get("kind") or
                     ("forward" if st.execution else None),
                     "plan_undo_of": plan.get("undo_of") if plan.get("kind") == "undo" else None}}


def get(handler, path: str, q: dict) -> bool:
    if path == "/api/runs":
        handler._send(200, runs_payload(handler.app))
        return True
    return False


def post(handler, path: str, b: dict) -> bool:
    if path != "/api/undo/build":
        return False
    app, st = handler.app, handler.app.store
    rid = str(b.get("run_id") or "")
    runs = undo.list_runs(st.data_dir)
    if not rid:
        same = [r for r in runs if os.path.normcase(r.get("root") or "") == os.path.normcase(st.root)]
        if not same:
            raise ValueError("No completed run to undo")
        rid = same[0]["run_id"]
    summ = next((r for r in runs if r["run_id"] == rid), None)
    if not summ or not undo.load_run(st.data_dir, rid):
        raise ValueError("Unknown run")
    ok, why = undo.undoable(summ, st.root)
    if not ok:
        raise RuntimeError(why)
    app.start_job("plan", lambda: undo.job_build(app, rid))
    handler._send(200, {"ok": True, "run_id": rid})
    return True

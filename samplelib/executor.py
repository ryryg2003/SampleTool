"""Executes an approved Plan against the collection (the only writer).

Safety model (see docs/CONTRACT.md, "Executor semantics"):
- every write is preceded by revalidation (existence, reparse points, size,
  mtime, SHA-256, destination absent case-insensitively, same volume);
- a move is hard link -> verify -> unlink source, so the data always has at
  least one name; if the source is locked the new link is removed again;
- nothing is ever overwritten (``os.link`` fails if the destination exists);
- an append-only, fsync'd JSONL journal makes interrupted runs resumable.

Low-level operations go through small module-level functions (``_link``,
``_unlink``, ``_hash``, ``_copy``, ...) so tests can monkeypatch them.
"""
from __future__ import annotations

import datetime as _dt
import errno
import json
import os
import shutil
import time

from . import inventory
from .winfs import (IS_WINDOWS, file_identity, is_locked_error, is_reparse_point,
                    is_within, long_path, name_key, path_key,
                    same_volume)

WRITE_ACTIONS = ("move", "link")
PROGRESS_INTERVAL = 0.1

# errno / winerror values meaning "this volume cannot hard link".
_NO_HARDLINK_ERRNOS = {errno.EPERM, errno.EOPNOTSUPP, getattr(errno, "ENOTSUP", -1),
                       errno.ENOSYS}
_NO_HARDLINK_WINERRORS = {1, 50}  # ERROR_INVALID_FUNCTION, ERROR_NOT_SUPPORTED


# ---------------------------------------------------------------------------
# Low-level primitives (monkeypatch points for tests)
# ---------------------------------------------------------------------------
def _link(src: str, dst: str) -> None:
    os.link(long_path(src), long_path(dst))


def _unlink(p: str) -> None:
    os.unlink(long_path(p))


def _hash(p: str) -> str:
    return inventory.sha256_file(p)


def _stat(p: str) -> os.stat_result:
    return os.stat(long_path(p), follow_symlinks=False)


def _makedirs(p: str) -> None:
    os.makedirs(long_path(p), exist_ok=True)


def _copy(src: str, dst: str) -> None:
    """Copy bytes into a NEW file (``"xb"``: never overwrites), fsync, keep mtime."""
    with open(long_path(src), "rb") as fi, open(long_path(dst), "xb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
        fo.flush()
        os.fsync(fo.fileno())
    try:
        shutil.copystat(long_path(src), long_path(dst))
    except OSError:
        pass


def _rename_no_overwrite(src: str, dst: str) -> None:
    """Rename for volumes without hard links. Windows rename never overwrites."""
    if not IS_WINDOWS and _dest_exists(dst):
        raise FileExistsError(errno.EEXIST, "destination exists", dst)
    os.rename(long_path(src), long_path(dst))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _err(e: BaseException) -> str:
    winerr = getattr(e, "winerror", None)
    s = f"{type(e).__name__}: {getattr(e, 'strerror', None) or e}"
    if winerr:
        s += f" (winerror {winerr})"
    return s


def _hardlink_unsupported(e: OSError) -> bool:
    if isinstance(e, FileExistsError):
        return False
    if getattr(e, "winerror", None) in _NO_HARDLINK_WINERRORS:
        return True
    return e.errno in _NO_HARDLINK_ERRNOS


def _dest_exists(dest: str) -> bool:
    """Exact or case-variant entry already present at ``dest``."""
    if os.path.lexists(long_path(dest)):
        return True
    parent, name = os.path.split(dest)
    try:
        with os.scandir(long_path(parent)) as it:
            k = name_key(name)
            return any(name_key(e.name) == k for e in it)
    except FileNotFoundError:
        return False
    except NotADirectoryError:
        return True


def _check_dest_ancestors(dest: str, dest_root: str | None) -> str | None:
    """Refuse if dest is outside dest_root or any existing ancestor is a reparse point."""
    parent = os.path.dirname(os.path.abspath(dest))
    if dest_root:
        if not is_within(dest, dest_root) or path_key(dest) == path_key(dest_root):
            return f"destination is outside the library folder {dest_root}"
        chain = []
        cur = parent
        root_key = path_key(dest_root)
        while True:
            chain.append(cur)
            if path_key(cur) == root_key:
                break
            nxt = os.path.dirname(cur)
            if nxt == cur:
                break
            cur = nxt
    else:
        chain = [parent]
    for d in chain:
        if os.path.lexists(long_path(d)):
            if is_reparse_point(d):
                return f"destination folder {d} is a reparse point (junction/symlink)"
            if not os.path.isdir(long_path(d)):
                return f"destination folder {d} exists but is not a folder"
    return None


def _check_source_ancestors(src: str, root: str | None, dest_root: str | None) -> str | None:
    """Refuse a source outside root, inside the library, or below a reparse point."""
    if not root:
        return "plan has no collection root; cannot verify source location"
    if not is_within(src, root):
        return f"source is outside the collection folder {root}"
    if dest_root and is_within(src, dest_root):
        return "source is already inside the library folder"
    cur = os.path.dirname(os.path.abspath(src))
    root_key = path_key(root)
    while True:
        if os.path.lexists(long_path(cur)) and is_reparse_point(cur):
            return f"source folder {cur} is a reparse point (junction/symlink)"
        if path_key(cur) == root_key:
            return None
        nxt = os.path.dirname(cur)
        if nxt == cur:
            return f"source is outside the collection folder {root}"
        cur = nxt


def _partial_path(dest: str, opid: str) -> str:
    return f"{dest}.partial-{opid}"


class _Held(Exception):
    """Revalidation failed: no write performed."""


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------
def read_journal(journal_path: str) -> list[dict]:
    """All well-formed records (a torn final line from a crash is ignored)."""
    out: list[dict] = []
    try:
        with open(journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and "op_id" in rec:
                    out.append(rec)
    except FileNotFoundError:
        pass
    return out


class _Journal:
    def __init__(self, path: str):
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        # A crash mid-write can leave a torn final line. Terminate it so the next
        # record is not glued onto the fragment (read_journal skips the fragment).
        try:
            with open(path, "rb+") as g:
                g.seek(0, os.SEEK_END)
                if g.tell() > 0:
                    g.seek(-1, os.SEEK_END)
                    if g.read(1) != b"\n":
                        g.write(b"\n")
                        g.flush()
                        os.fsync(g.fileno())
        except FileNotFoundError:
            pass
        self.f = open(path, "a", encoding="utf-8")

    def write(self, op_id: str, phase: str, **fields) -> None:
        rec = {"op_id": op_id, "phase": phase, "ts": _now()}
        rec.update(fields)
        self.f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass


def _journal_state(records: list[dict]) -> dict:
    """op_id -> last phase ("start" | "done" | "held" | "failed")."""
    state: dict = {}
    for r in records:
        state[r["op_id"]] = r.get("phase")
    return state


# ---------------------------------------------------------------------------
# Revalidation
# ---------------------------------------------------------------------------
def _validate_source(path: str, op: dict, check_mtime: bool, label: str) -> None:
    if not os.path.lexists(long_path(path)):
        raise _Held(f"{label} no longer exists: {path}")
    if is_reparse_point(path):
        raise _Held(f"{label} is a reparse point (symlink/junction): {path}")
    try:
        st = _stat(path)
    except OSError as e:
        raise _Held(f"cannot stat {label}: {_err(e)}")
    if not os.path.isfile(long_path(path)):
        raise _Held(f"{label} is not a regular file: {path}")
    if op.get("size") is not None and st.st_size != op["size"]:
        raise _Held(f"{label} changed since approval (size {st.st_size} != {op['size']})")
    if check_mtime and op.get("mtime_ns") is not None and st.st_mtime_ns != op["mtime_ns"]:
        raise _Held(f"{label} changed since approval (modified time differs)")
    want = op.get("sha256")
    if not want:
        raise _Held("plan has no SHA-256 for this file; cannot verify")
    try:
        got = _hash(path)
    except OSError as e:
        if is_locked_error(e):
            raise _Held(f"{label} locked or unreadable: {_err(e)}")
        raise _Held(f"cannot read {label}: {_err(e)}")
    if got != want:
        raise _Held(f"{label} content changed since approval (SHA-256 mismatch)")


def _validate_dest(dest: str, src: str, dest_root: str | None) -> None:
    if not dest:
        raise _Held("operation has no destination")
    msg = _check_dest_ancestors(dest, dest_root)
    if msg:
        raise _Held(msg)
    if _dest_exists(dest):
        raise _Held(f"destination already exists (case-insensitive): {dest}")
    if not same_volume(src, dest):
        raise _Held("source and destination are on different volumes")


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def _verify_dest(dest: str, want_sha: str, want_identity: str | None) -> str | None:
    if want_identity is not None and file_identity(dest) != want_identity:
        return "destination identity does not match source after linking"
    try:
        if _hash(dest) != want_sha:
            return "destination SHA-256 mismatch after linking"
    except OSError as e:
        return f"cannot verify destination: {_err(e)}"
    return None


def _remove_source_or_rollback(op: dict, src: str, dest: str) -> tuple[str, str | None]:
    """Unlink the source; on failure remove the just-created dest. -> (status, error)."""
    try:
        _unlink(src)
        return "done", None
    except OSError as e:
        why = "source locked" if is_locked_error(e) else "could not remove source"
        try:
            _unlink(dest)
        except OSError as e2:
            return "failed", (f"{why} ({_err(e)}); rollback of {dest} also failed "
                              f"({_err(e2)}) - file now exists at both paths")
        return "held", f"{why}: {_err(e)}; new destination link removed, nothing changed"


def _do_move(op: dict, j: _Journal, dest_root: str | None, resume: bool) -> dict:
    src, dest, opid, sha = op["source"], op.get("dest"), op["id"], op.get("sha256")
    if resume:
        r = _reconcile_move(op, j)
        if r is not None:
            return r
    _validate_source(src, op, True, "source")
    _validate_dest(dest, src, dest_root)
    j.write(opid, "start", action="move", source=src, dest=dest)
    _makedirs(os.path.dirname(dest))
    src_id = file_identity(src)
    try:
        _link(src, dest)
    except FileExistsError:
        raise _Held(f"destination already exists: {dest}")
    except OSError as e:
        if not _hardlink_unsupported(e):
            raise
        return _staged_move(op, j)
    bad = _verify_dest(dest, sha, src_id)
    if bad:
        try:
            _unlink(dest)
        except OSError as e:
            return {"status": "failed", "error": f"{bad}; rollback failed: {_err(e)}"}
        return {"status": "held", "error": bad + "; new link removed"}
    status, err = _remove_source_or_rollback(op, src, dest)
    return {"status": status, "error": err, "file_id": file_identity(dest)
            if status == "done" else None}


def _staged_move(op: dict, j: _Journal) -> dict:
    """Copy fallback for volumes without hard-link support."""
    src, dest, opid, sha = op["source"], op["dest"], op["id"], op["sha256"]
    partial = _partial_path(dest, opid)
    if os.path.lexists(long_path(partial)):
        # Only reconcile (which runs when the journal shows this op started) may
        # remove a leftover staging file. Anything else is left alone.
        raise _Held(f"a staging file already exists and was not created by this run: {partial}")
    _copy(src, partial)
    try:
        if _hash(partial) != sha:
            _unlink(partial)
            return {"status": "failed", "error": "staged copy SHA-256 mismatch"}
        try:
            _link(partial, dest)
            _unlink(partial)
        except FileExistsError:
            _unlink(partial)
            raise _Held(f"destination already exists: {dest}")
        except OSError as e:
            if not _hardlink_unsupported(e):
                raise
            _rename_no_overwrite(partial, dest)
    except BaseException:
        if os.path.lexists(long_path(partial)):
            try:
                _unlink(partial)
            except OSError:
                pass
        raise
    status, err = _remove_source_or_rollback(op, src, dest)
    if status == "done":
        err = None
    return {"status": status, "error": err,
            "file_id": file_identity(dest) if status == "done" else None,
            "note": "copied (volume has no hard-link support)"}


def _reconcile_move(op: dict, j: _Journal) -> dict | None:
    """Resolve a move that has a ``start`` record but no terminal record.

    Returns a result dict, or None to proceed with a normal (fresh) attempt.
    """
    src, dest, opid, sha = op["source"], op.get("dest"), op["id"], op.get("sha256")
    if not dest:
        return None
    partial = _partial_path(dest, opid)
    if os.path.lexists(long_path(partial)):
        try:
            _unlink(partial)
        except OSError:
            pass
    src_exists = os.path.lexists(long_path(src))
    if not os.path.lexists(long_path(dest)):
        if not src_exists:
            return {"status": "failed", "error":
                    "interrupted move: neither source nor destination exists"}
        return None  # nothing happened yet; normal path revalidates
    try:
        dest_ok = (not is_reparse_point(dest)) and _hash(dest) == sha
    except OSError as e:
        return {"status": "held", "error": f"interrupted move: cannot verify destination: {_err(e)}"}
    if not dest_ok:
        return {"status": "held", "error":
                "interrupted move: destination exists with different content; left untouched"}
    if not src_exists:
        return {"status": "done", "error": None, "file_id": file_identity(dest),
                "note": "reconciled after interrupted run"}
    # Both exist: finish the move only if the source is the same data.
    if file_identity(src) != file_identity(dest):
        try:
            if _hash(src) != sha:
                return {"status": "held", "error": "interrupted move: source changed; left as is"}
        except OSError as e:
            return {"status": "held", "error": f"interrupted move: cannot verify source: {_err(e)}"}
    status, err = _remove_source_or_rollback(op, src, dest)
    return {"status": status, "error": err,
            "file_id": file_identity(dest) if status == "done" else None,
            "note": "finished after interrupted run"}


def _do_link(op: dict, j: _Journal, dest_root: str | None, resume: bool) -> dict:
    target, dest, opid, sha = op.get("link_to"), op.get("dest"), op["id"], op.get("sha256")
    if not target:
        raise _Held("link operation has no link_to target")
    if resume and dest and os.path.lexists(long_path(dest)):
        tid = file_identity(target)
        if tid is not None and file_identity(dest) == tid:
            return {"status": "done", "error": None, "file_id": tid,
                    "note": "reconciled after interrupted run"}
    _validate_source(target, op, False, "link target")
    _validate_dest(dest, target, dest_root)
    j.write(opid, "start", action="link", link_to=target, dest=dest)
    _makedirs(os.path.dirname(dest))
    tid = file_identity(target)
    try:
        _link(target, dest)
    except FileExistsError:
        raise _Held(f"destination already exists: {dest}")
    except OSError as e:
        if _hardlink_unsupported(e):
            raise _Held("volume does not support hard links; duplicate not linked")
        raise
    bad = _verify_dest(dest, sha, tid)
    if bad:
        try:
            _unlink(dest)
        except OSError as e:
            return {"status": "failed", "error": f"{bad}; rollback failed: {_err(e)}"}
        return {"status": "held", "error": bad + "; new link removed"}
    return {"status": "done", "error": None, "file_id": tid}


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------
def _order(ops: list) -> list:
    """Moves first, then non-writing ops, then links; dependencies before dependents."""
    rank = {"move": 0, "link": 2}
    ordered = sorted(range(len(ops)), key=lambda i: (rank.get(ops[i].get("action"), 1), i))
    ids = {o.get("id") for o in ops}
    placed: set = set()
    out: list = []
    pending = [ops[i] for i in ordered]
    while pending:
        progressed = False
        rest = []
        for o in pending:
            dep = o.get("depends_on")
            if dep and dep in ids and dep not in placed and dep != o.get("id"):
                rest.append(o)
            else:
                out.append(o)
                placed.add(o.get("id"))
                progressed = True
        if not progressed:  # dependency cycle: emit as-is (will be held)
            out.extend(rest)
            break
        pending = rest
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def execute(plan: dict, journal_path: str, progress, cancel=None) -> list[dict]:
    """Execute ``plan``. Returns one OpResult per op, in plan order.

    Per-op failures are recorded, never raised.
    """
    ops = list(plan.get("ops") or [])
    dest_root = plan.get("dest_root")
    prior = _journal_state(read_journal(journal_path))
    journal = _Journal(journal_path)
    results: dict = {}
    status_by_id: dict = {}
    total = len(ops)
    bytes_total = sum(int(o.get("size") or 0) for o in ops if o.get("action") in WRITE_ACTIONS)
    prog = {"job": "execute", "phase": "executing", "running": True, "done": 0,
            "total": total, "bytes_done": 0, "bytes_total": bytes_total,
            "current": "", "warnings": 0, "errors": [], "message": ""}
    last_emit = [0.0]

    def emit(force=False):
        if progress is None:
            return
        now = time.monotonic()
        if not force and now - last_emit[0] < PROGRESS_INTERVAL:
            return
        last_emit[0] = now
        try:
            progress(dict(prog, errors=list(prog["errors"])))
        except Exception:
            pass

    emit(force=True)
    try:
        for op in _order(ops):
            opid = op.get("id")
            action = op.get("action")
            res = {"op_id": opid, "action": action, "source": op.get("source"),
                   "dest": op.get("dest"), "timestamp": _now(), "error": None,
                   "file_id": None}
            prog["current"] = op.get("source") or ""
            if cancel is not None and cancel.is_set():
                res.update(status="skipped", error="cancelled before this operation ran")
            elif action not in WRITE_ACTIONS:
                if action == "keep":
                    res.update(status="skipped", error=None)
                    res["file_id"] = file_identity(op.get("source") or "")
                elif action in ("hold", "exclude"):
                    res.update(status="held" if action == "hold" else "skipped",
                               error=op.get("reason") or None)
                else:
                    res.update(status="held", error=f"unknown action {action!r}")
            elif prior.get(opid) == "done":
                d = op.get("dest")
                try:
                    st = _stat(d)
                    ok = st.st_size == op.get("size")
                except (OSError, TypeError):
                    ok = False
                if ok:
                    res.update(status="already-done", file_id=file_identity(d))
                else:
                    res.update(status="held", error="journal says done but destination "
                               "is missing or has a different size; not repeated")
            else:
                dep = op.get("depends_on")
                if dep and status_by_id.get(dep) not in ("done", "already-done") \
                        and prior.get(dep) != "done":
                    res.update(status="held", error=f"depends on operation {dep}, "
                               "which did not complete")
                    journal.write(opid, "held", error=res["error"])
                else:
                    resume = prior.get(opid) == "start"
                    try:
                        if action == "move":
                            bad = _check_source_ancestors(op.get("source") or "",
                                                          plan.get("root"), dest_root)
                            if bad:
                                raise _Held(bad)
                            r = _do_move(op, journal, dest_root, resume)
                        else:
                            r = _do_link(op, journal, dest_root, resume)
                    except _Held as h:
                        r = {"status": "held", "error": str(h)}
                    except Exception as e:  # noqa: BLE001 - recorded, never raised
                        r = {"status": "failed", "error": _err(e)}
                    res.update(status=r["status"], error=r.get("error"),
                               file_id=r.get("file_id"))
                    rec = {"error": res["error"]} if res["error"] else {}
                    if r.get("note"):
                        rec["note"] = r["note"]
                    journal.write(opid, res["status"], dest=op.get("dest"),
                                  file_id=res["file_id"], **rec)
                if res["status"] in ("done",):
                    prog["bytes_done"] += int(op.get("size") or 0)
            res["timestamp"] = _now()
            status_by_id[opid] = res["status"]
            results[opid] = res
            if res["status"] in ("held", "failed") and action in WRITE_ACTIONS:
                prog["warnings"] += 1
                if len(prog["errors"]) < inventory.ERRORS_CAP:
                    prog["errors"].append({"path": op.get("source"), "error": res["error"]})
            prog["done"] += 1
            emit()
    finally:
        journal.close()
    prog["running"] = True
    prog["message"] = "Execution complete"
    prog["current"] = ""
    emit(force=True)
    out, seen = [], set()
    for o in ops:
        oid = o.get("id")
        if oid in results and oid not in seen:
            seen.add(oid)
            out.append(results[oid])
    return out

"""Read-only inventory scan of the sample collection (stdlib only).

Strictly read-only: files are opened ``"rb"`` only; nothing under ``root`` is
created, renamed or modified. Reparse points (symlinks, junctions) are never
followed.
"""
from __future__ import annotations

import hashlib
import os
import time

from . import models
from .winfs import IS_WINDOWS, is_reparse_point, is_within, long_path, path_key

ERRORS_CAP = 200
PROGRESS_INTERVAL = 0.1  # seconds (~10 updates/s)


class Cancelled(Exception):
    """Raised by sha256_file when the cancel event is set."""


def sha256_file(path: str, chunk: int = 1 << 20, cancel=None) -> str:
    """Streaming SHA-256 of ``path`` (read-only)."""
    h = hashlib.sha256()
    with open(long_path(path), "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def cache_key(abs_path: str, size: int, mtime_ns: int) -> str:
    return f"{path_key(abs_path)}|{size}|{mtime_ns}"


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        return "file vanished during scan"
    if isinstance(exc, PermissionError):
        return f"permission denied: {exc.strerror or exc}"
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or exc}"
    return f"{type(exc).__name__}: {exc}"


class _Reporter:
    def __init__(self, progress):
        self.progress = progress
        self.last = 0.0
        self.errors: list = []
        self.warnings = 0
        self.state = {"job": "scan", "running": True, "phase": "walking", "done": 0,
                      "total": 0, "bytes_done": 0, "bytes_total": 0, "current": "",
                      "message": ""}

    def error(self, path: str, msg: str) -> None:
        self.warnings += 1
        if len(self.errors) < ERRORS_CAP:
            self.errors.append({"path": path, "error": msg})

    def emit(self, force: bool = False, **kw) -> None:
        self.state.update(kw)
        if self.progress is None:
            return
        now = time.monotonic()
        if not force and now - self.last < PROGRESS_INTERVAL:
            return
        self.last = now
        d = dict(self.state)
        d["warnings"] = self.warnings
        d["errors"] = list(self.errors)
        try:
            self.progress(d)
        except Exception:  # a broken progress callback must not kill the scan
            pass


def _rel(path: str, base: str) -> str:
    return os.path.relpath(path, base).replace(os.sep, "/")


def _walk(top: str, rep: _Reporter, skipped: list, cancel, exclude_keys: set,
          on_file) -> bool:
    """Iterative scandir walk. Returns False if cancelled."""
    stack = [top]
    while stack:
        if cancel is not None and cancel.is_set():
            return False
        d = stack.pop()
        try:
            it = os.scandir(long_path(d))
        except OSError as e:
            msg = _describe_error(e)
            skipped.append({"path": d, "reason": f"unreadable folder: {msg}"})
            rep.error(d, msg)
            continue
        subdirs = []
        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError as e:
                    msg = _describe_error(e)
                    skipped.append({"path": d, "reason": f"folder listing failed: {msg}"})
                    rep.error(d, msg)
                    break
                p = os.path.join(d, entry.name)
                try:
                    if is_reparse_point(entry):
                        skipped.append({"path": p, "reason":
                                        "reparse point (symlink/junction) not followed"})
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if path_key(p) in exclude_keys:
                            continue
                        subdirs.append(p)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        skipped.append({"path": p, "reason": "not a regular file"})
                        continue
                    if IS_WINDOWS:
                        # DirEntry.stat() lacks st_ino/st_dev/st_nlink on Windows.
                        st = os.stat(long_path(p), follow_symlinks=False)
                    else:
                        st = entry.stat(follow_symlinks=False)
                except OSError as e:
                    msg = _describe_error(e)
                    skipped.append({"path": p, "reason": msg})
                    rep.error(p, msg)
                    continue
                on_file(p, entry.name, st)
                rep.emit(current=p)
        # depth-first, alphabetical-ish order
        subdirs.sort(key=lambda s: s.casefold(), reverse=True)
        stack.extend(subdirs)
    return True


def _make_item(origin: str, rel_path: str, abs_path: str, name: str,
               st: os.stat_result, pack) -> dict:
    ext = os.path.splitext(name)[1].lower()
    kind = models.classify_kind(ext)
    if ext in models.BROWSER_PLAYABLE:
        preview = "native"
    elif kind == "audio":
        preview = "unsupported"
    else:
        preview = "none"
    return {
        "id": models.item_id(origin, rel_path),
        "origin": origin,
        "rel_path": rel_path,
        "abs_path": abs_path,
        "name": name,
        "ext": ext,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "file_id": f"{st.st_dev}:{st.st_ino}",
        "nlink": st.st_nlink,
        "kind": kind,
        "sha256": None,
        "hash_error": None,
        "pack": pack,
        "audio": None,
        "tags_raw": {},
        "meta": {},
        "kit_id": None,
        "excluded": False,
        "preview": preview,
        "warnings": [],
        "near_dupes": [],
    }


def scan(root: str, dest_root: str, progress, hash_cache: dict | None = None,
         cancel=None) -> dict:
    """Inventory ``root`` (origin "incoming") and ``dest_root`` (origin "library").

    Returns ``{"items", "skipped", "hash_cache", "cancelled"}``.
    """
    root = os.path.abspath(root)
    dest_root = os.path.abspath(dest_root)
    old_cache = hash_cache or {}
    new_cache: dict = {}
    rep = _Reporter(progress)
    items: list = []
    skipped: list = []
    dest_key = path_key(dest_root)
    dest_in_root = is_within(dest_root, root)

    def add_incoming(p, name, st):
        rel = _rel(p, root)
        parts = rel.split("/")
        pack = parts[0] if len(parts) > 1 else None
        items.append(_make_item("incoming", rel, p, name, st, pack))
        rep.state["done"] += 1
        rep.state["bytes_total"] += st.st_size

    def add_library(p, name, st):
        if dest_in_root:
            rel = _rel(p, root)
        else:
            rel = os.path.basename(dest_root) + "/" + _rel(p, dest_root)
        items.append(_make_item("library", rel, p, name, st, None))
        rep.state["done"] += 1
        rep.state["bytes_total"] += st.st_size

    rep.emit(force=True, phase="walking", message="Listing files")
    ok = True
    if not os.path.isdir(long_path(root)):
        rep.error(root, "root folder not found")
        skipped.append({"path": root, "reason": "root folder not found"})
    elif is_reparse_point(root):
        skipped.append({"path": root, "reason": "root is a reparse point; not followed"})
    else:
        ok = _walk(root, rep, skipped, cancel, {dest_key}, add_incoming)
    if ok and os.path.isdir(long_path(dest_root)):
        if is_reparse_point(dest_root):
            skipped.append({"path": dest_root,
                            "reason": "destination is a reparse point; not followed"})
        else:
            catalog_key = path_key(os.path.join(dest_root, models.CATALOG_DIR))
            ok = _walk(dest_root, rep, skipped, cancel, {catalog_key}, add_library)
    if not ok:
        rep.emit(force=True, message="Scan cancelled")
        return {"items": items, "skipped": skipped, "hash_cache": new_cache,
                "cancelled": True}

    total = len(items)
    rep.emit(force=True, phase="hashing", done=0, total=total, bytes_done=0,
             message="Hashing files")
    bytes_done = 0
    for i, it in enumerate(items):
        if cancel is not None and cancel.is_set():
            ok = False
            break
        p = it["abs_path"]
        key = cache_key(p, it["size"], it["mtime_ns"])
        digest = old_cache.get(key)
        if digest is None:
            try:
                digest = sha256_file(p, cancel=cancel)
            except Cancelled:
                ok = False
                break
            except Exception as e:  # noqa: BLE001 - per-file errors never crash the scan
                msg = _describe_error(e)
                it["hash_error"] = msg
                it["warnings"].append(f"could not hash: {msg}")
                rep.error(p, msg)
                digest = None
        if digest is not None:
            it["sha256"] = digest
            new_cache[key] = digest
        bytes_done += it["size"]
        rep.emit(done=i + 1, bytes_done=bytes_done, current=p)
    rep.emit(force=True, message="Scan cancelled" if not ok else "Scan complete",
             current="")
    return {"items": items, "skipped": skipped, "hash_cache": new_cache,
            "cancelled": not ok}

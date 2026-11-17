"""Folder picker API (loaded by server.py as an extension). Read-only.

GET /api/folders            -> the starting list: drives (Windows) or "/" plus
                               common places (Music, Documents, Desktop, ...).
GET /api/folders?path=P     -> P's subfolders and how many audio files sit
                               directly in P (not counting subfolders).

The browser cannot hand a real folder path to the app, so the first-run
screen and Settings use this to let the user click through to their sample
folder. It only lists names; it never opens, creates or changes anything.
"""
from __future__ import annotations

import os
import stat
import string

from . import models
from .winfs import long_path, strip_long_prefix

MUTATING: set = set()
MAX_FOLDERS = 1000      # more than this in one folder is cut off (and flagged)
MAX_ENTRIES = 20000     # stop counting audio files after this many entries
IS_WIN = os.name == "nt"
_HIDDEN = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)


def drives() -> list[str]:
    """Drive roots such as C:\\ (Windows), else ["/"]."""
    if not IS_WIN:
        return ["/"]
    if hasattr(os, "listdrives"):  # Python 3.12+
        try:
            return sorted(os.listdrives())
        except OSError:
            pass
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return [f"{c}:\\" for c in "CDEFGH" if os.path.isdir(f"{c}:\\")]
    return [f"{c}:\\" for i, c in enumerate(string.ascii_uppercase) if mask >> i & 1]


def places(home: str | None = None) -> list[dict]:
    """Common starting folders that exist, without duplicates."""
    home = home or os.path.expanduser("~")
    cands = [("Music", "Music"), ("Documents", "Documents"), ("Desktop", "Desktop"),
             ("Downloads", "Downloads"), ("OneDrive Music", os.path.join("OneDrive", "Music")),
             ("OneDrive Documents", os.path.join("OneDrive", "Documents")),
             ("OneDrive Desktop", os.path.join("OneDrive", "Desktop"))]
    out, seen = [], set()
    for label, rel in [("Home", "")] + cands:
        p = os.path.join(home, rel) if rel else home
        key = os.path.normcase(os.path.abspath(p))
        if key in seen or not os.path.isdir(p):
            continue
        seen.add(key)
        out.append({"label": label, "path": os.path.abspath(p)})
    return out


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith((".", "$")) or entry.name in ("System Volume Information", "__pycache__"):
        return True
    if IS_WIN:
        try:
            return bool(entry.stat(follow_symlinks=False).st_file_attributes & _HIDDEN)
        except (OSError, AttributeError):
            return False
    return False


def _parent(p: str) -> str:
    up = os.path.dirname(p)
    return "" if not up or os.path.normcase(up) == os.path.normcase(p) else up


def listing(path: str = "") -> dict:
    """What the folder picker shows for one folder ("" = the starting list)."""
    path = (path or "").strip().strip('"')
    if not path:
        return {"path": "", "parent": None, "audio_files": 0, "truncated": False,
                "folders": [{"name": d, "path": d} for d in drives()], "places": places()}
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(long_path(path)):
        raise ValueError(f"Folder not found: {path}")
    folders, audio, n = [], 0, 0
    try:
        with os.scandir(long_path(path)) as it:
            for entry in it:
                n += 1
                if n > MAX_ENTRIES:
                    break
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                if is_dir:
                    if not _hidden(entry):
                        folders.append(entry.name)
                elif os.path.splitext(entry.name)[1].lower() in models.AUDIO_EXTS:
                    audio += 1
    except PermissionError:
        raise ValueError(f"Windows did not allow reading {path}. Pick another folder.") from None
    except OSError as e:
        raise ValueError(f"Could not read {path}: {e.strerror or e}") from None
    folders.sort(key=str.casefold)
    truncated = len(folders) > MAX_FOLDERS or n > MAX_ENTRIES
    folders = folders[:MAX_FOLDERS]
    path = strip_long_prefix(path)
    return {"path": path, "parent": _parent(path), "audio_files": audio, "truncated": truncated,
            "folders": [{"name": f, "path": os.path.join(path, f)} for f in folders], "places": places()}


def get(handler, path: str, q: dict) -> bool:
    if path == "/api/folders":
        handler._send(200, listing(q.get("path", "")))
        return True
    return False

"""Media endpoints: AIFF auditions as WAV, waveform peaks, copy file to clipboard.

OWNER: media feature. Loaded by server.py as an optional extension.

GET  /api/peaks/<id>?n=800  -> {id, n, peaks: [[min, max], ...], duration_s, cached}
                               or {id, unsupported: true, reason}
POST /api/clipboard/file {id} -> {ok: true}; 409 "Windows only" elsewhere

AIFF/AIFC audio is served by ``serve_converted`` which ``Handler._audio`` calls
for /api/audio/<id> (without ``download=1``). Everything is read-only against
the collection; the peak cache lives in ``<data_dir>/peaks``.
"""
from __future__ import annotations

import os
import re
import subprocess
import urllib.parse

from . import audiocodec

MUTATING: set = set()  # nothing here changes the collection or the plan
PEAKS_DEFAULT = 800
PEAKS_MIN, PEAKS_MAX = 16, 4000
CLIP_ENV = "SLO_CLIP_PATH"


def init(app) -> None:  # nothing to set up; the cache dir is created lazily
    return None


def state(app) -> dict:
    return {"media": {"aiff_preview": True, "peaks": True, "clipboard_file": is_windows()}}


def is_windows() -> bool:
    return os.name == "nt"


def item_path(st, it: dict) -> str:
    p = st._final_paths.get(it["id"]) or it["abs_path"]
    if not os.path.isfile(p):
        p = it["abs_path"]
    return p


# ------------------------------------------------------------------- audio
def serve_converted(handler, it: dict, path: str) -> bool:
    """Answer /api/audio/<id> for AIFF/AIFC with an on-the-fly WAV. -> handled?"""
    if (it.get("ext") or "").lower() not in audiocodec.AIFF_EXTS:
        return False
    try:
        src = audiocodec.probe_aiff(path)
    except audiocodec.Unsupported as e:
        handler._send(415, {"error": str(e), "unsupported": True})
        return True
    size = audiocodec.wav_size(src)
    rng = audiocodec.parse_range(handler.headers.get("Range"), size)
    if rng is None:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True
    status, start, end = rng
    handler.send_response(status)
    handler.send_header("Content-Type", "audio/wav")
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(end - start + 1))
    handler.send_header("Cache-Control", "no-cache")
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    for chunk in audiocodec.wav_range(src, path, start, end):
        handler.wfile.write(chunk)
    return True


def content_disposition(name: str) -> str:
    """attachment with an ASCII fallback plus the exact UTF-8 name (RFC 6266/5987)."""
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name) or "sample"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}"


# ------------------------------------------------------------------- peaks
def peaks_for(app, it: dict, n: int) -> dict:
    st = app.store
    ext = (it.get("ext") or "").lower()
    out = {"id": it["id"]}
    if it.get("kind") != "audio" or ext not in (audiocodec.AIFF_EXTS | audiocodec.WAV_EXTS):
        out.update({"unsupported": True, "reason": audiocodec.unsupported_reason(ext)})
        return out
    sha = it.get("sha256")
    cached = audiocodec.peaks_cache_read(st.data_dir, sha, n)
    if cached is not None:
        out.update(cached)
        out["cached"] = True
        return out
    path = item_path(st, it)
    if not os.path.isfile(path):
        raise KeyError(f"file is missing: {path}")
    try:
        d = audiocodec.compute_peaks(path, ext, n)
    except audiocodec.Unsupported as e:
        d = {"unsupported": True, "reason": str(e)}
    audiocodec.peaks_cache_write(st.data_dir, sha, n, d)
    out.update(d)
    out["cached"] = False
    return out


# --------------------------------------------------------------- clipboard
def clipboard_command(path: str) -> tuple[list, dict]:
    """argv + env for PowerShell Set-Clipboard. The path travels in an environment
    variable and is bound with -LiteralPath, so no quoting or wildcard expansion."""
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
            f"Set-Clipboard -LiteralPath $env:{CLIP_ENV}"]
    env = dict(os.environ)
    env[CLIP_ENV] = path
    return argv, env


def copy_file_to_clipboard(path: str) -> None:
    if not is_windows():
        raise RuntimeError("Copying files to the clipboard is Windows only")
    argv, env = clipboard_command(path)
    kw = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        r = subprocess.run(argv, env=env, capture_output=True, timeout=20, shell=False, **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"Could not run PowerShell: {e}")
    if r.returncode != 0:
        msg = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError("Set-Clipboard failed" + (": " + msg[0] if msg else ""))


# ------------------------------------------------------------------ routes
def get(handler, path: str, q: dict) -> bool:
    m = re.fullmatch(r"/api/peaks/([\w-]+)", path)
    if not m:
        return False
    it = handler.app.store.by_id[m.group(1)]
    try:
        n = int(q.get("n") or PEAKS_DEFAULT)
    except ValueError:
        return handler._err(400, "n must be a number") or True
    n = max(PEAKS_MIN, min(PEAKS_MAX, n))
    handler._send(200, peaks_for(handler.app, it, n))
    return True


def post(handler, path: str, b: dict) -> bool:
    if path != "/api/clipboard/file":
        return False
    st = handler.app.store
    iid = b.get("id")
    if not iid:
        raise ValueError("id required")
    it = st.by_id[str(iid)]
    p = item_path(st, it)
    if not os.path.isfile(p):
        raise ValueError("File not found")
    copy_file_to_clipboard(p)
    handler._send(200, {"ok": True})
    return True

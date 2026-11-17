"""Settings API (loaded by server.py as an extension).

GET  /api/settings          -> {"settings": {...}, "defaults": {...}, "info": {...}}
POST /api/settings {partial}-> same shape after saving. "root" is refused here: the
                               collection folder changes only through POST /api/scan
                               {root}, which switches under the job lock.
POST /api/focus             -> {"ok": true}; a second launch announces itself.
"""
from __future__ import annotations

import os
import sys
import time

from . import __version__, settings, winapp

MUTATING: set = set()  # nothing here touches the collection or the plan


def init(app) -> None:
    app.focus_seq = 0
    winapp.track_ui_requests(app)


BUNDLED_PYTHON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python")


def python_info(executable: str | None = None) -> dict:
    """Which Python runs the app: the copy bundled in python/ or an installed one."""
    exe = sys.executable if executable is None else executable
    here = os.path.dirname(os.path.abspath(exe)) if exe else ""
    bundled = bool(exe) and os.path.normcase(here) == os.path.normcase(BUNDLED_PYTHON_DIR)
    return {"version": "%d.%d.%d" % sys.version_info[:3], "executable": exe or "", "bundled": bundled}


def _info(app) -> dict:
    st = app.store
    return {"version": __version__, "data_dir": st.data_dir,
            "settings_file": settings.path(st.data_dir),
            "log_file": getattr(app, "log_file", None) or winapp.log_file_path(st.data_dir),
            "port_in_use": getattr(app, "port", None),
            "idle_exit": getattr(app, "idle_exit", 0),
            "python": python_info()}


def _payload(app) -> dict:
    s = settings.load(app.store.data_dir)
    s["root"] = app.store.root  # the live collection folder
    busy = winapp.busy(app)
    return {"settings": s, "defaults": dict(settings.DEFAULTS), "info": _info(app),
            "busy": busy, "job": app.progress.get("job") if busy else None}


def state(app) -> dict:
    return {"ui_settings": {k: v for k, v in settings.load(app.store.data_dir).items() if k != "root"}}


def get(handler, path: str, q: dict) -> bool:
    if path == "/api/settings":
        handler._send(200, _payload(handler.app))
        return True
    return False


def post(handler, path: str, b: dict) -> bool:
    app = handler.app
    if path == "/api/settings":
        if not isinstance(b, dict) or not b:
            raise ValueError("Send at least one setting to change")
        settings.update(app.store.data_dir, b, allow_root=False)
        handler._send(200, _payload(app))
        return True
    if path == "/api/focus":
        app.focus_seq = getattr(app, "focus_seq", 0) + 1
        app.set_progress({"focus_seq": app.focus_seq, "focus_ts": time.time()})
        winapp.log.info("Second launch: asked the open window to come forward")
        handler._send(200, {"ok": True, "focus_seq": app.focus_seq})
        return True
    return False

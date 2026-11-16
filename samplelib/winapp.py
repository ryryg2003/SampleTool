"""Desktop-app behaviour: logging without a console, single instance, idle shutdown.

Windows calls go through ctypes (stdlib). On other systems the window functions
are no-ops that return False, so the same code paths run everywhere.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import time
import urllib.request
from typing import Callable, Optional

from . import __version__

log = logging.getLogger("samplelib")

WINDOW_TITLE = "Sample Library Organizer"
BROWSER_CLASSES = ("Chrome_WidgetWin_1", "Chrome_WidgetWin_0")  # Edge and Chrome top-level windows
DEFAULT_IDLE_EXIT = 180  # seconds; background windows may poll only once a minute


# --------------------------------------------------------------------- logging
class _LogStream:
    """Stands in for sys.stdout/sys.stderr under pythonw.exe, where they are None."""

    def __init__(self, level: int):
        self.level = level
        self._buf = ""

    def write(self, s) -> int:
        s = str(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                log.log(self.level, line.rstrip())
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            log.log(self.level, self._buf.rstrip())
        self._buf = ""

    def isatty(self) -> bool:
        return False


def log_file_path(data_dir: str) -> str:
    return os.path.join(data_dir, "logs", "app.log")


def setup_logging(data_dir: str) -> str:
    """Log to <data dir>/logs/app.log (rotating, ~1.5 MB max). Replace missing std streams."""
    path = log_file_path(data_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for h in list(log.handlers):
        if getattr(h, "_slo", False):
            log.removeHandler(h)
            h.close()
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    fh._slo = True  # type: ignore[attr-defined]
    log.addHandler(fh)
    log.setLevel(logging.INFO)
    log.propagate = False
    if sys.stdout is None:
        sys.stdout = _LogStream(logging.INFO)  # type: ignore[assignment]
    if sys.stderr is None:
        sys.stderr = _LogStream(logging.ERROR)  # type: ignore[assignment]
    return path


def say(msg: str) -> None:
    """Print for the console launcher and always log. Never fails without a console."""
    log.info(msg)
    out = sys.stdout
    if out is None or isinstance(out, _LogStream):
        return
    try:
        print(msg, file=out)
        out.flush()
    except Exception:
        pass


# ------------------------------------------------------------- single instance
def is_our_server(port: int, urlopen: Callable = urllib.request.urlopen, timeout: float = 1.0) -> bool:
    """True only when the port answers like this app (JSON state with version and dest_root)."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/state", timeout=timeout) as r:
            d = json.loads(r.read())
        return isinstance(d, dict) and "version" in d and "dest_root" in d
    except Exception:
        return False


def find_running(port: int, urlopen: Callable = urllib.request.urlopen) -> Optional[int]:
    """Port of a running instance: the requested one, or one it fell back to (+1..+19)."""
    for p in range(port, min(port + 20, 65536)):
        if is_our_server(p, urlopen):
            return p
    return None


def notify_focus(port: int, urlopen: Callable = urllib.request.urlopen) -> bool:
    """Tell the running server a second launch happened (the UI shows a toast)."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/focus", data=b"{}", method="POST",
                                     headers={"X-SLO": "1", "Content-Type": "application/json"})
        with urlopen(req, timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _title_matches(title: str, cls: str) -> bool:
    return cls in BROWSER_CLASSES and WINDOW_TITLE in title


def find_app_window() -> Optional[int]:
    """hwnd of a visible Edge/Chrome top-level window showing the app, or None."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: list[int] = []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        tbuf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, tbuf, n + 1)
        cbuf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cbuf, 256)
        if _title_matches(tbuf.value.rstrip(), cbuf.value):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(proto(cb), 0)
    return found[0] if found else None


def focus_app_window() -> bool:
    """Restore and raise the existing app window. False when none was found (or not Windows)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        hwnd = find_app_window()
        if not hwnd:
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW (keeps maximized windows maximized)
        user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        if not user32.SetForegroundWindow(hwnd):
            # Foreground lock: a synthetic Alt press lets this process take the foreground.
            user32.keybd_event(0x12, 0, 0, 0)        # VK_MENU down
            user32.keybd_event(0x12, 0, 0x0002, 0)   # VK_MENU up
            user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        return True
    except Exception:
        log.exception("Could not focus the app window")
        return False


def handle_second_launch(port: int, open_window: Callable[[str], None], *, no_browser: bool = False,
                         urlopen: Callable = urllib.request.urlopen,
                         focus: Callable[[], bool] = focus_app_window) -> Optional[int]:
    """If an instance already runs, bring it forward and return its port; else None."""
    running = find_running(port, urlopen)
    if running is None:
        return None
    notify_focus(running, urlopen)
    if not no_browser and not focus():
        open_window(f"http://127.0.0.1:{running}/")
    return running


# --------------------------------------------------------------- idle shutdown
def track_ui_requests(app) -> None:
    """Record the last UI request. The UI polls /api/progress (and /api/state), both of
    which call app.get_progress, so wrapping it per instance needs no server change."""
    if getattr(app, "_ui_tracked", False):
        return
    app.last_ui_request = None
    orig = app.get_progress

    def get_progress():
        app.last_ui_request = time.monotonic()
        return orig()
    app.get_progress = get_progress
    app._ui_tracked = True


def busy(app) -> bool:
    return bool(app.executing or app.job_lock.locked() or app.progress.get("running"))


class IdleWatcher(threading.Thread):
    """Shuts the server down after `timeout` s without UI requests, never during a job.

    The job lock is held while shutting down so no job can start in between. A long
    gap in this thread's own loop (sleep/hibernate) restarts the idle clock, so the
    UI gets a chance to poll again after the machine wakes up."""

    def __init__(self, httpd, app, timeout: float, interval: Optional[float] = None,
                 on_exit: Optional[Callable[[], None]] = None):
        super().__init__(name="idle-watch", daemon=True)
        self.httpd, self.app, self.timeout = httpd, app, float(timeout)
        self.interval = interval if interval is not None else max(0.05, min(5.0, self.timeout / 4))
        self.on_exit = on_exit
        self.stopped = threading.Event()
        self.fired = False
        track_ui_requests(app)

    def idle(self, now: float) -> bool:
        last = self.app.last_ui_request
        return last is not None and now - last > self.timeout

    def should_exit(self, now: float) -> bool:
        return self.idle(now) and not busy(self.app)

    def run(self) -> None:
        prev = time.monotonic()
        while not self.stopped.wait(self.interval):
            now = time.monotonic()
            if now - prev > max(self.interval * 6, 30):  # the machine slept: start counting afresh
                if self.app.last_ui_request is not None:
                    self.app.last_ui_request = now
            prev = now
            if not self.should_exit(now):
                continue
            if not self.app.job_lock.acquire(blocking=False):
                continue
            try:
                # we hold the job lock now, so check the rest of busy() by hand
                if self.app.executing or self.app.progress.get("running") or not self.idle(time.monotonic()):
                    continue
                log.info("No window has talked to the app for %ss; shutting down.", int(self.timeout))
                self.fired = True
                if self.on_exit:
                    self.on_exit()
                self.httpd.shutdown()  # serve_forever returns in the main thread
                return
            finally:
                if not self.fired:
                    self.app.job_lock.release()
                # when fired, keep the lock: nothing may start while the process exits

    def stop(self) -> None:
        self.stopped.set()


def describe() -> str:
    return f"Sample Library Organizer {__version__}"

"""Entry point: python -m samplelib [--root PATH] [--port N] [--data-dir DIR] [--no-browser]
[--idle-exit SECONDS]

Works with python.exe (console, "Launch Sample Organizer.bat") and pythonw.exe (desktop
shortcut, no console): everything is also logged to <data dir>/logs/app.log.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser

from . import __version__, settings, winapp
from .store import Store, default_data_dir
from .server import serve


def _already_running(port: int) -> bool:  # kept for callers of the old helper
    return winapp.is_our_server(port)


def _open_window(url: str) -> None:
    """Prefer an Edge/Chrome app window (no tabs or address bar); fall back to the default browser."""
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for exe in candidates:
            if os.path.isfile(exe):
                subprocess.Popen([exe, f"--app={url}", "--window-size=1440,900"], **quiet)
                return
    elif shutil.which("chromium"):
        subprocess.Popen(["chromium", f"--app={url}"], **quiet)
        return
    webbrowser.open(url)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="samplelib", description="Sample Library Organizer")
    ap.add_argument("--root", default="", help=r'Collection folder, e.g. "E:\Sample Packs" (default: the saved one)')
    ap.add_argument("--port", type=int, default=None, help="Port (default: the one saved in Settings, else 8765)")
    ap.add_argument("--data-dir", default=default_data_dir())
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--idle-exit", type=float, default=None, metavar="SECONDS",
                    help=f"Quit after this long without a window (default {winapp.DEFAULT_IDLE_EXIT} when the "
                         "app opens its own window, off with --no-browser; 0 disables). Never during a task.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv=None) -> int:
    a = _parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # never crash on non-ASCII paths in a cp1252 console
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    if sys.version_info < (3, 10):
        winapp.say("Python 3.10 or newer is required.")
        return 1
    try:
        log_file = winapp.setup_logging(a.data_dir)
    except OSError:
        log_file = None
    try:
        return _run(a, log_file)
    except Exception:
        winapp.log.exception("Sample Organizer stopped because of an error")
        raise


def _run(a, log_file) -> int:
    saved = settings.load(a.data_dir)
    port = a.port if a.port is not None else saved["port"]
    idle = a.idle_exit if a.idle_exit is not None else (0 if a.no_browser else winapp.DEFAULT_IDLE_EXIT)

    running = winapp.handle_second_launch(port, _open_window, no_browser=a.no_browser) if port else None
    if running:
        winapp.say(f"Sample Organizer is already running at http://127.0.0.1:{running}/ ; "
                   "brought its window forward.")
        return 0

    store = Store(a.data_dir, os.path.abspath(a.root) if a.root else "")
    httpd, app = serve(store, port=port)
    app.port = httpd.server_address[1]
    app.log_file = log_file
    app.idle_exit = idle
    url = f"http://127.0.0.1:{app.port}/"
    winapp.say(f"Sample Library Organizer {__version__}")
    winapp.say(f"  Collection : {store.root}")
    winapp.say(f"  App data   : {store.data_dir}")
    if log_file:
        winapp.say(f"  Log file   : {log_file}")
    winapp.say(f"  Open       : {url}")
    if app.port != port:
        winapp.say(f"  Port {port} was busy, so {app.port} is used this time.")
    winapp.say("  Scanning and review never change your files. Close this window to quit.")
    watcher = None
    if idle and idle > 0:
        watcher = winapp.IdleWatcher(httpd, app, idle)
        watcher.start()
    if not a.no_browser:
        _open_window(url)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        if watcher:
            watcher.stop()
        app.cancel.set()
        httpd.server_close()
        winapp.say("Sample Organizer stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

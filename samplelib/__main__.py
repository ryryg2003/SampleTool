"""Entry point: python -m samplelib [--root PATH] [--port N] [--data-dir DIR] [--no-browser]"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import webbrowser

from .store import Store, default_data_dir
from .server import serve


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as r:
            return "dest_root" in json.loads(r.read())
    except Exception:
        return False


def _open_window(url: str) -> None:
    """Prefer an Edge/Chrome app window (no tabs or address bar); fall back to the default browser."""
    if os.name == "nt":
        candidates = [
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        for exe in candidates:
            if os.path.isfile(exe):
                subprocess.Popen([exe, f"--app={url}", "--window-size=1440,900"])
                return
    elif shutil.which("chromium"):
        subprocess.Popen(["chromium", f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    webbrowser.open(url)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="samplelib", description="Sample Library Organizer")
    ap.add_argument("--root", default="", help=r'Collection folder, e.g. "E:\Sample Packs"')
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default=default_data_dir())
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # never crash on non-ASCII paths in a cp1252 console
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    if sys.version_info < (3, 10):
        print("Python 3.10 or newer is required.")
        return 1
    if a.port and _already_running(a.port):
        print("Sample Organizer is already running; opening it.")
        if not a.no_browser:
            _open_window(f"http://127.0.0.1:{a.port}/")
        return 0

    store = Store(a.data_dir, os.path.abspath(a.root) if a.root else "")
    httpd, app = serve(store, port=a.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print("Sample Library Organizer")
    print(f"  Collection : {store.root}")
    print(f"  App data   : {store.data_dir}")
    print(f"  Open       : {url}")
    print("  Scanning and review never change your files. Close this window to quit.")
    sys.stdout.flush()
    if not a.no_browser:
        _open_window(url)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        app.cancel.set()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

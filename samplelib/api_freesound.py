"""HTTP routes for the Find Sounds tab (Freesound). Loaded by server.py as an extension.

GET  /api/freesound/status              -> status (has_key, masked key_hint, oauth, quota, online)
GET  /api/freesound/search?...           -> {count, page, page_size, next, previous, results, dropped, filter, quota}
GET  /api/freesound/preview/<id>         -> audio/mpeg (proxied, Range supported)
GET  /api/freesound/oauth/callback       -> small HTML page (OAuth2 redirect target)
POST /api/freesound/key                  {key?, client_id?} -> {ok, has_key, key_hint}
POST /api/freesound/oauth/start          {mode: "local"|"manual"} -> {authorize_url, state, redirect_uri}
POST /api/freesound/oauth/code           {code} -> {ok, connected}
POST /api/freesound/oauth/disconnect     -> {ok, connected: false}
POST /api/freesound/download             {id, mode: "original"|"preview"} -> {ok, path, sidecar, folder, name, bytes, ...}

The browser never talks to freesound.org; the API key stays on this machine.
"""
from __future__ import annotations

import html
import re

from . import freesound

MUTATING = {"/api/freesound/download"}


def init(app) -> None:
    app.freesound = freesound.Client(app.store.data_dir)


def client(app) -> "freesound.Client":
    c = getattr(app, "freesound", None)
    if c is None or c.data_dir != app.store.data_dir:
        init(app)
        c = app.freesound
    return c


def state(app) -> dict:
    c = getattr(app, "freesound", None)
    return {"freesound": {"has_key": bool(c and c.key), "online": c.online if c else None}}


def _redirect_uri(handler) -> str:
    return f"http://127.0.0.1:{handler.server.server_address[1]}/api/freesound/oauth/callback"


def _fail(handler, e: Exception) -> bool:
    if isinstance(e, freesound.FreesoundError):
        body = {"error": str(e), "code": e.code}
        body.update({k: v for k, v in e.extra.items()})
        if "quota" not in body:
            body["quota"] = client(handler.app).quota()
        status = e.status
        if e.code == "upstream" and e.extra.get("http") == 404:
            status = 404
        handler._send(status, body)
        return True
    raise e


def get(handler, path: str, q: dict) -> bool:
    if not path.startswith("/api/freesound/"):
        return False
    c = client(handler.app)
    try:
        if path == "/api/freesound/status":
            handler._send(200, c.status(_redirect_uri(handler)))
            return True
        if path == "/api/freesound/search":
            handler._send(200, c.search(q))
            return True
        m = re.fullmatch(r"/api/freesound/preview/(\d{1,12})", path)
        if m:
            _stream(handler, c.preview(int(m.group(1))))
            return True
        if path == "/api/freesound/oauth/callback":
            _callback(handler, c, q)
            return True
    except ValueError as e:
        handler._send(400, {"error": str(e)})
        return True
    except PermissionError as e:
        handler._send(409, {"error": str(e)})
        return True
    except freesound.FreesoundError as e:
        return _fail(handler, e)
    return False


def post(handler, path: str, b: dict) -> bool:
    if not path.startswith("/api/freesound/"):
        return False
    c = client(handler.app)
    try:
        if path == "/api/freesound/key":
            if "key" not in b and "client_id" not in b:
                raise ValueError("key required")
            handler._send(200, c.set_key(b.get("key") if "key" in b else None,
                                         b.get("client_id") if "client_id" in b else None))
            return True
        if path == "/api/freesound/oauth/start":
            mode = b.get("mode") or "local"
            if mode not in ("local", "manual"):
                raise ValueError("mode must be local or manual")
            handler._send(200, c.oauth_start(_redirect_uri(handler) if mode == "local" else None))
            return True
        if path == "/api/freesound/oauth/code":
            handler._send(200, c.oauth_finish(str(b.get("code") or ""), None))
            return True
        if path == "/api/freesound/oauth/disconnect":
            handler._send(200, c.oauth_disconnect())
            return True
        if path == "/api/freesound/download":
            try:
                sid = int(b.get("id"))
            except (TypeError, ValueError):
                raise ValueError("id required")
            mode = b.get("mode") or "original"
            handler._send(200, c.download(handler.app.store.root, sid, mode))
            return True
    except freesound.FreesoundError as e:
        return _fail(handler, e)
    return False


def _stream(handler, data: bytes) -> None:
    size = len(data)
    start, end, status = 0, size - 1, 200
    rng = handler.headers.get("Range")
    if rng:
        mm = re.match(r"bytes=(\d*)-(\d*)", rng)
        if mm and (mm.group(1) or mm.group(2)):
            if mm.group(1):
                start = int(mm.group(1))
                if mm.group(2):
                    end = min(int(mm.group(2)), size - 1)
            else:
                start = max(0, size - int(mm.group(2)))
            if start > end or start >= size:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.end_headers()
                return
            status = 206
    handler.send_response(status)
    handler.send_header("Content-Type", "audio/mpeg")
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(end - start + 1))
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    handler.wfile.write(data[start:end + 1])


def _page(handler, code: int, title: str, msg: str) -> None:
    body = ("<!doctype html><meta charset=utf-8><title>%s</title>"
            "<body style=\"background:#1d2227;color:#dde3e9;font:15px Segoe UI,system-ui,sans-serif;padding:40px\">"
            "<h1 style=\"font-size:20px\">%s</h1><p>%s</p></body>") % (html.escape(title), html.escape(title), html.escape(msg))
    data = body.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def _callback(handler, c, q: dict) -> None:
    if q.get("error"):
        return _page(handler, 400, "Freesound sign-in cancelled",
                     "Freesound said: " + str(q.get("error")) + ". You can close this tab and try again from the app.")
    try:
        c.oauth_finish(str(q.get("code") or ""), str(q.get("state") or "") or "-")
    except (ValueError, PermissionError, freesound.FreesoundError) as e:
        return _page(handler, 400, "Freesound sign-in failed", str(e))
    _page(handler, 200, "Freesound connected",
          "You can close this tab and go back to Sample Organizer. Original-file downloads are now enabled.")

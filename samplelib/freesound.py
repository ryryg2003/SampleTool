"""Freesound APIv2 client: search, preview fetch, OAuth2, throttle, quota, key storage
and the download writer.

OWNER: findsounds feature. Standard library only. Verified against
https://freesound.org/docs/api/ on 2026-11-16 (see docs/FREE_SOUNDS_ADDON.md).

Safety:
- The API key and OAuth tokens live in ``<data dir>/freesound.json``. They are
  never logged and never returned in full (``key_hint`` is masked).
- Credentials are only sent to ``freesound.org``. Redirects are followed by hand
  and the Authorization header is dropped when the host changes.
- Every network call goes through ``transport`` (injectable for tests).
- ``write_download`` is the only function that writes into the collection, and
  only under ``<root>/_Freesound/<uploader>/``, never overwriting.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from typing import Callable, Optional

from .planner import sanitize
from .store import _atomic_write

API_BASE = "https://freesound.org/apiv2"
AUTHORIZE_URL = API_BASE + "/oauth2/authorize/"
TOKEN_URL = API_BASE + "/oauth2/access_token/"
FOLDER = "_Freesound"
CRED_FILE = "freesound.json"
USAGE_FILE = "freesound_usage.json"

# Documented limits: 60/min + 2000/day (standard), 30/min + 500/day (original downloads).
# We stay below the per-minute ones.
API_PER_MIN, API_PER_DAY = 50, 2000
DL_PER_MIN, DL_PER_DAY = 25, 500

LIC_CC0, LIC_BY, LIC_NC = "Creative Commons 0", "Attribution", "Attribution NonCommercial"
LICENSE_URL = {"cc0": "https://creativecommons.org/publicdomain/zero/1.0/",
               "by": "https://creativecommons.org/licenses/by/4.0/"}
LICENSE_NAME = {"cc0": "Creative Commons 0 (CC0 1.0, public domain dedication)",
                "by": "Creative Commons Attribution (CC BY)"}

SEARCH_FIELDS = "id,name,username,duration,license,previews,url,tags,bpm,tonality,type,samplerate,channels,filesize"
SORTS = {"score", "duration_desc", "duration_asc", "created_desc", "created_asc",
         "downloads_desc", "downloads_asc", "rating_desc", "rating_asc"}
AUDIO_EXTS = {"wav", "aif", "aiff", "flac", "mp3", "ogg", "m4a"}
PREVIEW_CACHE_N = 20
USER_AGENT = "SampleOrganizer-FindSounds/1.0"


class FreesoundError(Exception):
    """Base error with an HTTP status for the local API and a machine code for the UI."""
    status = 502
    code = "upstream"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.extra = extra


class NoKey(FreesoundError):
    status, code = 409, "no_key"


class NotConnected(FreesoundError):
    status, code = 409, "oauth_required"


class Offline(FreesoundError):
    status, code = 503, "offline"


class RateLimited(FreesoundError):
    status, code = 429, "rate_limited"

    def __init__(self, message: str, retry_after_s: float, **extra):
        super().__init__(message, retry_after_s=int(max(1, round(retry_after_s))), **extra)
        self.retry_after_s = int(max(1, round(retry_after_s)))


# --------------------------------------------------------------------------- transport
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # handled by the client so auth never leaks cross-host
        return None


_OPENER = urllib.request.build_opener(_NoRedirect())


def urllib_transport(method: str, url: str, headers: dict, body: Optional[bytes], timeout: float):
    """Default transport. Returns (status, headers{lower: value}, body bytes). Raises Offline."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(64 << 20)
    except urllib.error.HTTPError as e:
        try:
            data = e.read(1 << 20)
        except Exception:
            data = b""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, data
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise Offline(f"Could not reach freesound.org ({reason}). Check your internet connection.")


def _is_freesound_host(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    h = (u.hostname or "").lower()
    return u.scheme == "https" and (h == "freesound.org" or h.endswith(".freesound.org"))


# --------------------------------------------------------------------------- helpers
def mask(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    if len(key) <= 8:
        return "…" + key[-2:]
    return key[:4] + "…" + key[-4:]


def license_kind(lic) -> Optional[str]:
    """'cc0' | 'by' | 'nc' | None for a Freesound license name or Creative Commons URL."""
    s = str(lic or "").strip().lower()
    if not s:
        return None
    if "noncommercial" in s or "by-nc" in s or "sampling+" in s or "sampling-plus" in s:
        return "nc"
    if s == "creative commons 0" or "publicdomain/zero" in s or s in ("cc0", "cc0 1.0"):
        return "cc0"
    if s == "attribution" or re.search(r"creativecommons\.org/licenses/by/", s):
        return "by"
    return None


def license_url(lic) -> Optional[str]:
    s = str(lic or "")
    if s.startswith("http://") or s.startswith("https://"):
        return s.replace("http://", "https://", 1)
    return LICENSE_URL.get(license_kind(s) or "")


_KEY_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)\s*(m|min|minor|maj|major)?\s*$")


def tonality(key: str) -> Optional[str]:
    """'Am' / 'C#' / 'F minor' -> Freesound tonality 'A minor' / 'C# major' / 'F minor'."""
    m = _KEY_RE.match(key or "")
    if not m:
        return None
    acc = {"♯": "#", "♭": "b"}.get(m.group(2), m.group(2))
    mode = "minor" if (m.group(3) or "").lower() in ("m", "min", "minor") else "major"
    return f"{m.group(1).upper()}{acc} {mode}"


def _num(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"Not a number: {v}")
    if f < 0 or f > 100000:
        raise ValueError(f"Out of range: {v}")
    return f


def _fmt(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else f"{n:g}"


def _q(v: str) -> str:
    v = re.sub(r'["\\]', "", str(v)).strip()
    return f'"{v}"' if re.search(r"[\s:()\[\]]", v) else v


def build_filter(p: dict) -> str:
    """Map UI filters to Freesound's Solr filter. CC0 always; Attribution only when asked."""
    parts = []
    if p.get("attribution") in (True, "1", "true", 1):
        parts.append(f'license:("{LIC_CC0}" OR "{LIC_BY}")')
    else:
        parts.append(f'license:"{LIC_CC0}"')
    for t in [x for x in re.split(r"[,\s]+", str(p.get("tag") or "")) if x][:5]:
        parts.append("tag:" + _q(t.lower()))
    typ = p.get("type") or ""
    dmin, dmax = _num(p.get("dur_min")), _num(p.get("dur_max"))
    if typ == "oneshot" and dmax is None:
        dmax = 3
    if typ == "loop":
        parts.append("tag:loop")
    if dmin is not None or dmax is not None:
        parts.append(f"duration:[{_fmt(dmin) if dmin is not None else '*'} TO {_fmt(dmax) if dmax is not None else '*'}]")
    bmin, bmax = _num(p.get("bpm_min")), _num(p.get("bpm_max"))
    if bmin is not None or bmax is not None:
        parts.append(f"bpm:[{_fmt(bmin) if bmin is not None else '*'} TO {_fmt(bmax) if bmax is not None else '*'}]")
    if p.get("key"):
        t = tonality(str(p["key"]))
        if not t:
            raise ValueError(f"Unknown key: {p['key']}")
        parts.append(f'tonality:"{t}"')
    return " ".join(parts)


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _secs_to_utc_midnight() -> float:
    now = _dt.datetime.now(_dt.timezone.utc)
    nxt = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt - now).total_seconds()


# --------------------------------------------------------------------------- throttle
class Bucket:
    """Token bucket: `rate` tokens per 60 s, burst `rate`."""

    def __init__(self, per_min: int, clock: Callable[[], float]):
        self.cap = float(per_min)
        self.tokens = float(per_min)
        self.clock = clock
        self.t = clock()

    def _fill(self) -> None:
        now = self.clock()
        self.tokens = min(self.cap, self.tokens + (now - self.t) * self.cap / 60.0)
        self.t = now

    def remaining(self) -> int:
        self._fill()
        return int(self.tokens)

    def take(self) -> float:
        """0 when a token was taken, else seconds until one is available."""
        self._fill()
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) * 60.0 / self.cap


# --------------------------------------------------------------------------- client
class Client:
    def __init__(self, data_dir: str, transport=None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, today: Callable[[], str] = _today):
        self.data_dir = data_dir
        self.transport = transport or urllib_transport
        self.clock, self.wall, self.today = clock, wall, today
        self.lock = threading.RLock()
        self.api_bucket = Bucket(API_PER_MIN, clock)
        self.dl_bucket = Bucket(DL_PER_MIN, clock)
        self.blocked_until = 0.0          # clock() time; set by 429 or local limits
        self.blocked_reason = ""
        self.online: Optional[bool] = None
        self.sounds: "OrderedDict[int, dict]" = OrderedDict()   # id -> raw sound (for preview/download)
        self.previews: "OrderedDict[int, bytes]" = OrderedDict()
        self.pending: dict = {}           # oauth state -> {verifier, redirect_uri, t}
        self.cred = self._read(CRED_FILE, {})
        self.usage = self._read(USAGE_FILE, {})

    # ------------------------------------------------------------- persistence
    def _read(self, name: str, default):
        try:
            with open(os.path.join(self.data_dir, name), "r", encoding="utf-8") as f:
                v = json.load(f)
                return v if isinstance(v, dict) else default
        except (OSError, ValueError):
            return default

    def _write(self, name: str, obj) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        p = os.path.join(self.data_dir, name)
        _atomic_write(p, json.dumps(obj, indent=1).encode("utf-8"))
        if os.name != "nt":
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass

    # ------------------------------------------------------------- key + status
    @property
    def key(self) -> str:
        return str(self.cred.get("api_key") or "")

    def set_key(self, key: Optional[str] = None, client_id: Optional[str] = None) -> dict:
        """key/client_id: None leaves it unchanged, "" clears it."""
        if key is not None and not isinstance(key, str):
            raise ValueError("key must be a string")
        key = None if key is None else key.strip()
        if key and not re.fullmatch(r"[A-Za-z0-9_\-]{8,128}", key):
            raise ValueError("That does not look like a Freesound API key (letters and digits, from freesound.org/apiv2/apply).")
        with self.lock:
            if key:
                self.cred["api_key"] = key
            elif key == "":
                self.cred.pop("api_key", None)
            if client_id is not None:
                cid = str(client_id).strip()
                if cid and not re.fullmatch(r"[A-Za-z0-9_\-]{4,128}", cid):
                    raise ValueError("That does not look like a Freesound client ID.")
                if cid != self.cred.get("client_id"):
                    self.cred.pop("oauth", None)
                if cid:
                    self.cred["client_id"] = cid
                else:
                    self.cred.pop("client_id", None)
            self._write(CRED_FILE, self.cred)
        return {"ok": True, "has_key": bool(self.key), "key_hint": mask(self.key)}

    def _usage_today(self) -> dict:
        d = self.today()
        if self.usage.get("day") != d:
            self.usage = {"day": d, "api": 0, "downloads": 0}
        return self.usage

    def quota(self) -> dict:
        with self.lock:
            u = self._usage_today()
            left = self.blocked_until - self.clock()
            return {"minute_remaining": self.api_bucket.remaining(), "day_used": int(u.get("api", 0)),
                    "day_limit": API_PER_DAY, "downloads_minute_remaining": self.dl_bucket.remaining(),
                    "downloads_day_used": int(u.get("downloads", 0)), "downloads_day_limit": DL_PER_DAY,
                    "retry_after_s": int(left + 0.999) if left > 0 else None,
                    "blocked_reason": self.blocked_reason if left > 0 else None}

    def status(self, redirect_uri: Optional[str] = None) -> dict:
        o = self.cred.get("oauth") or {}
        return {"has_key": bool(self.key), "key_hint": mask(self.key),
                "oauth": {"connected": bool(o.get("access_token")), "available": bool(self.cred.get("client_id")),
                          "client_id_set": bool(self.cred.get("client_id")), "redirect_uri": redirect_uri},
                "quota": self.quota(), "online": self.online}

    # ------------------------------------------------------------- throttling
    def _admit(self, kind: str) -> None:
        """Take a token or raise RateLimited without touching the network."""
        with self.lock:
            now = self.clock()
            if self.blocked_until > now:
                left = self.blocked_until - now
                raise RateLimited(f"Rate limited by Freesound, try again in {int(left + 0.999)} s.", left,
                                  quota=self.quota())
            u = self._usage_today()
            if kind == "download":
                if u.get("downloads", 0) >= DL_PER_DAY:
                    raise RateLimited(f"Daily download limit ({DL_PER_DAY}) reached. It resets at midnight UTC.",
                                      _secs_to_utc_midnight(), quota=self.quota())
                wait = self.dl_bucket.take()
            else:
                if u.get("api", 0) >= API_PER_DAY:
                    raise RateLimited(f"Daily request limit ({API_PER_DAY}) reached. It resets at midnight UTC.",
                                      _secs_to_utc_midnight(), quota=self.quota())
                wait = self.api_bucket.take()
            if wait > 0:
                raise RateLimited(f"Slow down: too many requests this minute, try again in {int(wait + 0.999)} s.",
                                  wait, quota=self.quota())
            u["downloads" if kind == "download" else "api"] = int(u.get("downloads" if kind == "download" else "api", 0)) + 1
            self._write(USAGE_FILE, u)

    def _on_429(self, headers: dict, body: bytes) -> RateLimited:
        detail = ""
        try:
            detail = str((json.loads(body or b"{}") or {}).get("detail") or "")
        except ValueError:
            pass
        ra = headers.get("retry-after")
        wait = None
        if ra:
            try:
                wait = float(ra)
            except ValueError:
                try:
                    from email.utils import parsedate_to_datetime
                    wait = parsedate_to_datetime(ra).timestamp() - self.wall()
                except Exception:
                    wait = None
        if wait is None:
            wait = _secs_to_utc_midnight() if re.search(r"day", detail, re.I) else 60.0
        wait = max(1.0, wait)
        with self.lock:
            self.blocked_until = max(self.blocked_until, self.clock() + wait)
            self.blocked_reason = detail or "rate limit"
        return RateLimited(f"Rate limited by Freesound, try again in {int(wait + 0.999)} s."
                           + (f" ({detail})" if detail else ""), wait, quota=self.quota())

    # ------------------------------------------------------------- HTTP
    def _http(self, method: str, url: str, *, auth: Optional[str] = None, body: Optional[bytes] = None,
              ctype: Optional[str] = None, timeout: float = 20.0):
        """Send one request (following up to 5 redirects by hand). auth: 'token' | 'bearer' | None."""
        hdr = {"User-Agent": USER_AGENT, "Accept": "application/json, */*"}
        if ctype:
            hdr["Content-Type"] = ctype
        if auth == "token":
            if not self.key:
                raise NoKey("Add your Freesound API key first.")
            hdr["Authorization"] = "Token " + self.key
        elif auth == "bearer":
            hdr["Authorization"] = "Bearer " + str((self.cred.get("oauth") or {}).get("access_token") or "")
        for _ in range(6):
            if not _is_freesound_host(url) and "Authorization" in hdr:
                hdr.pop("Authorization")
            try:
                st, h, data = self.transport(method, url, dict(hdr), body, timeout)
            except Offline:
                self.online = False
                raise
            self.online = True
            if st in (301, 302, 303, 307, 308) and h.get("location"):
                nxt = urllib.parse.urljoin(url, h["location"])
                if urllib.parse.urlsplit(nxt).scheme != "https":
                    raise FreesoundError("Freesound redirected to a non-HTTPS address; refused.")
                if urllib.parse.urlsplit(nxt).hostname != urllib.parse.urlsplit(url).hostname:
                    hdr.pop("Authorization", None)
                url = nxt
                if st == 303:
                    method, body = "GET", None
                continue
            if st == 429:
                raise self._on_429(h, data)
            return st, h, data
        raise FreesoundError("Too many redirects from Freesound.")

    def _json(self, st: int, data: bytes, what: str) -> dict:
        try:
            obj = json.loads(data or b"{}")
        except ValueError:
            obj = {}
        if st == 401:
            raise FreesoundError(f"Freesound rejected the credentials ({obj.get('detail') or 'unauthorized'}). Check your API key.",
                                 http=401)
        if st == 404:
            raise FreesoundError(f"{what}: not found on Freesound.", http=404)
        if st >= 400:
            raise FreesoundError(f"{what} failed: HTTP {st} {obj.get('detail') or ''}".strip(), http=st)
        return obj

    # ------------------------------------------------------------- search
    def search(self, p: dict) -> dict:
        if not self.key:
            raise NoKey("Add your Freesound API key to search.")
        page = int(p.get("page") or 1)
        size = min(150, max(1, int(p.get("page_size") or 30)))
        if page < 1 or page > 1000:
            raise ValueError("Page out of range")
        sort = p.get("sort") or "score"
        if sort not in SORTS:
            raise ValueError(f"Unknown sort: {sort}")
        params = {"query": str(p.get("q") or "").strip()[:200], "filter": build_filter(p), "sort": sort,
                  "fields": SEARCH_FIELDS, "page": str(page), "page_size": str(size)}
        url = API_BASE + "/search/?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        self._admit("api")
        st, _h, data = self._http("GET", url, auth="token")
        obj = self._json(st, data, "Search")
        allow_by = params["filter"].startswith("license:(")
        out, dropped = [], 0
        for s in obj.get("results") or []:
            row = self._row(s, allow_by)
            if row is None:
                dropped += 1
                continue
            out.append(row)
        return {"count": int(obj.get("count") or 0), "page": page, "page_size": size,
                "next": bool(obj.get("next")), "previous": bool(obj.get("previous")),
                "results": out, "dropped": dropped, "filter": params["filter"], "quota": self.quota()}

    def _remember(self, s: dict) -> None:
        with self.lock:
            self.sounds[int(s["id"])] = s
            self.sounds.move_to_end(int(s["id"]))
            while len(self.sounds) > 2000:
                self.sounds.popitem(last=False)

    def _row(self, s: dict, allow_by: bool) -> Optional[dict]:
        try:
            sid = int(s.get("id"))
        except (TypeError, ValueError):
            return None
        kind = license_kind(s.get("license"))
        if kind not in ("cc0", "by") or (kind == "by" and not allow_by):
            return None  # NonCommercial, unknown, or Attribution not opted in: never shown
        self._remember(s)
        prev = (s.get("previews") or {})
        return {"id": sid, "name": s.get("name") or f"Sound {sid}", "username": s.get("username") or "",
                "duration_s": s.get("duration"), "license": kind, "license_name": LICENSE_NAME[kind],
                "license_url": license_url(s.get("license")), "bpm": s.get("bpm"), "key": s.get("tonality"),
                "tags": list(s.get("tags") or [])[:12], "url": s.get("url") if _is_freesound_host(str(s.get("url") or "")) else
                f"https://freesound.org/s/{sid}/", "type": s.get("type"), "samplerate": s.get("samplerate"),
                "channels": s.get("channels"), "filesize": s.get("filesize"),
                "has_preview": bool(prev.get("preview-hq-mp3") or prev.get("preview-lq-mp3")),
                "preview_url": f"/api/freesound/preview/{sid}"}

    def sound(self, sid: int) -> dict:
        with self.lock:
            s = self.sounds.get(sid)
        if s:
            return s
        self._admit("api")
        st, _h, data = self._http("GET", f"{API_BASE}/sounds/{sid}/?" +
                                  urllib.parse.urlencode({"fields": SEARCH_FIELDS}), auth="token")
        s = self._json(st, data, f"Sound {sid}")
        if int(s.get("id") or -1) != sid:
            raise FreesoundError("Unexpected answer from Freesound.")
        self._remember(s)
        return s

    # ------------------------------------------------------------- previews
    def preview(self, sid: int) -> bytes:
        with self.lock:
            if sid in self.previews:
                self.previews.move_to_end(sid)
                return self.previews[sid]
        s = self.sound(sid)
        if license_kind(s.get("license")) not in ("cc0", "by"):
            raise FreesoundError("This sound's license is not allowed in the app.", http=403)
        p = s.get("previews") or {}
        url = p.get("preview-hq-mp3") or p.get("preview-lq-mp3")
        if not url or not _is_freesound_host(url):
            raise FreesoundError("Freesound gave no usable preview for this sound.")
        st, _h, data = self._http("GET", url, auth=None, timeout=30)
        if st != 200 or not data:
            raise FreesoundError(f"Preview download failed (HTTP {st}).")
        with self.lock:
            self.previews[sid] = data
            while len(self.previews) > PREVIEW_CACHE_N:
                self.previews.popitem(last=False)
        return data

    # ------------------------------------------------------------- OAuth2 (authorization code + PKCE)
    def oauth_start(self, redirect_uri: Optional[str]) -> dict:
        cid = self.cred.get("client_id")
        if not cid:
            raise NotConnected("Add your Freesound API credential's client ID first (Settings).")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        with self.lock:
            now = self.wall()
            self.pending = {k: v for k, v in self.pending.items() if now - v["t"] < 600}
            self.pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "t": now}
        q = {"client_id": cid, "response_type": "code", "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256"}
        if redirect_uri:
            q["redirect_uri"] = redirect_uri
        return {"authorize_url": AUTHORIZE_URL + "?" + urllib.parse.urlencode(q), "state": state,
                "redirect_uri": redirect_uri}

    def oauth_finish(self, code: str, state: Optional[str]) -> dict:
        code = (code or "").strip()
        if not code or not re.fullmatch(r"[A-Za-z0-9_\-.~]{4,256}", code):
            raise ValueError("Paste the authorization code shown by Freesound.")
        with self.lock:
            if state:
                pend = self.pending.pop(state, None)
                if not pend:
                    raise PermissionError("This sign-in link is unknown or expired. Start again from the app.")
            else:  # pasted code: use the newest pending manual start
                manual = [(v["t"], k) for k, v in self.pending.items() if not v["redirect_uri"]]
                if not manual:
                    raise PermissionError("Click 'Connect' first, then paste the code Freesound shows.")
                pend = self.pending.pop(max(manual)[1])
            if self.wall() - pend["t"] > 600:
                raise PermissionError("The sign-in took longer than 10 minutes. Start again.")
        form = {"client_id": self.cred.get("client_id") or "", "grant_type": "authorization_code",
                "code": code, "code_verifier": pend["verifier"]}
        if pend["redirect_uri"]:
            form["redirect_uri"] = pend["redirect_uri"]
        return self._token(form)

    def _token(self, form: dict) -> dict:
        self._admit("api")
        st, _h, data = self._http("POST", TOKEN_URL, body=urllib.parse.urlencode(form).encode(),
                                  ctype="application/x-www-form-urlencoded")
        obj = self._json(st, data, "Freesound sign-in") if st != 400 else None
        if obj is None or not obj.get("access_token"):
            try:
                det = json.loads(data or b"{}")
            except ValueError:
                det = {}
            if form.get("grant_type") == "refresh_token":
                with self.lock:
                    self.cred.pop("oauth", None)
                    self._write(CRED_FILE, self.cred)
            raise NotConnected("Freesound did not accept the sign-in ("
                               + str(det.get("error_description") or det.get("error") or det.get("detail") or f"HTTP {st}")
                               + "). Connect again.")
        with self.lock:
            self.cred["oauth"] = {"access_token": obj["access_token"],
                                  "refresh_token": obj.get("refresh_token") or (self.cred.get("oauth") or {}).get("refresh_token"),
                                  "expires_at": self.wall() + float(obj.get("expires_in") or 86399),
                                  "scope": obj.get("scope")}
            self._write(CRED_FILE, self.cred)
        return {"ok": True, "connected": True}

    def oauth_disconnect(self) -> dict:
        with self.lock:
            self.cred.pop("oauth", None)
            self._write(CRED_FILE, self.cred)
        return {"ok": True, "connected": False}

    def _ensure_token(self, force: bool = False) -> None:
        o = self.cred.get("oauth") or {}
        if not o.get("access_token"):
            raise NotConnected("Connect your Freesound account to download original files.")
        if force or float(o.get("expires_at") or 0) - 60 < self.wall():
            if not o.get("refresh_token"):
                raise NotConnected("Your Freesound sign-in expired. Connect again.")
            self._token({"client_id": self.cred.get("client_id") or "", "grant_type": "refresh_token",
                         "refresh_token": o["refresh_token"]})

    # ------------------------------------------------------------- downloads
    def fetch_original(self, sid: int) -> tuple[bytes, str]:
        self._ensure_token()
        self._admit("download")
        url = f"{API_BASE}/sounds/{sid}/download/"
        st, h, data = self._http("GET", url, auth="bearer", timeout=120)
        if st == 401:
            self._ensure_token(force=True)
            st, h, data = self._http("GET", url, auth="bearer", timeout=120)
        if st != 200:
            self._json(st, data, "Download")
            raise FreesoundError(f"Download failed (HTTP {st}).")
        return data, h.get("content-disposition", "")

    def download(self, root: str, sid: int, mode: str = "original") -> dict:
        if not root or not os.path.isdir(root):
            raise ValueError("Set your collection folder on the Scan screen first.")
        s = self.sound(sid)
        kind = license_kind(s.get("license"))
        if kind not in ("cc0", "by"):
            raise PermissionError("This sound is not CC0 or Attribution licensed; the app does not download it.")
        if mode == "preview":
            data = self.preview(sid)
            stem = os.path.splitext(str(s.get("name") or f"sound {sid}"))[0]
            fname = f"{stem} (preview).mp3"
        elif mode == "original":
            data, _cd = self.fetch_original(sid)
            fname = str(s.get("name") or f"sound {sid}")
            ext = str(s.get("type") or "").lower().strip(".")
            if ext in AUDIO_EXTS and not fname.lower().endswith("." + ext):
                fname = f"{fname}.{ext}"
        else:
            raise ValueError("mode must be 'original' or 'preview'")
        res = write_download(root, str(s.get("username") or "unknown"), fname, data, sidecar_text(s, mode, self.wall()))
        res.update({"ok": True, "id": sid, "mode": mode, "license": kind, "quota": self.quota()})
        return res


# --------------------------------------------------------------------------- writer
def sidecar_text(s: dict, mode: str, now: float) -> str:
    kind = license_kind(s.get("license"))
    sid = s.get("id")
    url = s.get("url") if _is_freesound_host(str(s.get("url") or "")) else f"https://freesound.org/s/{sid}/"
    title, user = str(s.get("name") or ""), str(s.get("username") or "")
    lic_name = LICENSE_NAME.get(kind or "", str(s.get("license")))
    lic_url = license_url(s.get("license")) or ""
    date = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if kind == "cc0":
        attr = f'"{title}" by {user} ({url}), CC0 1.0 (no attribution required).'
    else:
        attr = f'"{title}" by {user} ({url}), licensed under CC BY ({lic_url}).'
    lines = [
        "Freesound download - keep this file with the sound.",
        "",
        f"Sound URL:    {url}",
        f"Sound ID:     {sid}",
        f"Title:        {title}",
        f"Author:       {user}",
        f"License:      {lic_name}",
        f"License URL:  {lic_url}",
        f"Downloaded:   {date}",
        f"File:         {'MP3 preview (lower quality, about 128 kbps)' if mode == 'preview' else 'original file'}",
        "",
        "Attribution line:",
        attr,
        "",
    ]
    if kind == "by":
        lines += ["CC BY requires you to credit the author when you publish work that uses this sound.", ""]
    lines += ["Note: a sound labelled CC0 or CC BY can still be a re-upload of someone else's material.",
              "Use your judgement for recognisable loops.", ""]
    return "\r\n".join(lines)


def _safe_name(name: str, limit: int = 120) -> str:
    name = os.path.basename(str(name).replace("\\", "/")) or "sound"
    n = sanitize(name)
    if n.startswith("."):
        n = "_" + n[1:]
    stem, ext = os.path.splitext(n)
    if len(ext) > 10:
        stem, ext = n, ""
    stem = stem[:limit].rstrip(" .") or "sound"
    return sanitize(stem + ext)


def _safe_dir(seg: str) -> str:
    s = sanitize(str(seg).replace("\\", "_").replace("/", "_"))[:60].rstrip(" .") or "_"
    if set(s) <= {"."}:
        s = "_"
    return sanitize(s)


def write_download(root: str, uploader: str, filename: str, data: bytes, sidecar: str) -> dict:
    """Write data to <root>/_Freesound/<uploader>/<filename> plus <filename>.license.txt.

    Never overwrites (case-insensitive); a taken name gets " (2)", " (3)", ...
    The bytes go to a temp file in the same folder first, then are linked (or
    exclusively created) under the final name.
    """
    from . import winfs
    root_real = os.path.realpath(root)
    base = os.path.join(root_real, FOLDER)
    folder = os.path.join(base, _safe_dir(uploader))
    if not winfs.is_within(folder, base) or os.path.normcase(os.path.dirname(folder)) != os.path.normcase(base):
        raise ValueError("Unsafe uploader name")
    for p in (base, folder):
        if os.path.lexists(p) and (winfs.is_reparse_point(p) or not os.path.isdir(p)):
            raise PermissionError(f"Refusing to write through a link or non-folder: {p}")
    os.makedirs(folder, exist_ok=True)
    if os.path.realpath(folder) != folder and os.path.normcase(os.path.realpath(folder)) != os.path.normcase(folder):
        raise PermissionError("Download folder resolves outside the collection")
    name = _safe_name(filename)
    stem, ext = os.path.splitext(name)
    tmp = os.path.join(folder, f".freesound-{secrets.token_hex(6)}.partial")
    with open(tmp, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        for n in range(1, 1000):
            cand = name if n == 1 else f"{stem} ({n}){ext}"
            taken = {e.casefold() for e in os.listdir(folder)}
            if cand.casefold() in taken or (cand + ".license.txt").casefold() in taken:
                continue
            dest = os.path.join(folder, cand)
            try:
                os.link(tmp, dest)  # fails if dest exists: never overwrites
            except FileExistsError:
                continue
            except OSError:
                try:
                    with open(tmp, "rb") as src, open(dest, "xb") as out:
                        while True:
                            b = src.read(1 << 20)
                            if not b:
                                break
                            out.write(b)
                        out.flush()
                        os.fsync(out.fileno())
                except FileExistsError:
                    continue
            side = dest + ".license.txt"
            try:
                with open(side, "x", encoding="utf-8", newline="") as f:
                    f.write(sidecar)
            except FileExistsError:  # extremely unlikely race: leave the sound, write a numbered sidecar
                side = dest + f".license-{secrets.token_hex(3)}.txt"
                with open(side, "x", encoding="utf-8", newline="") as f:
                    f.write(sidecar)
            return {"path": dest, "sidecar": side, "folder": folder, "name": cand, "bytes": len(data)}
        raise RuntimeError("Could not find a free file name")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

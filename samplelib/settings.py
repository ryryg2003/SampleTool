"""User settings persisted in <data dir>/settings.json.

The file predates this module and held only {"root": ...}; that shape still loads.
Unknown keys written by other features (or newer versions) are preserved on save.
Every value is validated; a bad stored value falls back to its default instead of
breaking startup.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

FILENAME = "settings.json"
DEFAULT_PORT = 8765

DEFAULTS: dict[str, Any] = {
    "root": "",                 # collection folder; switched only via POST /api/scan {root}
    "port": DEFAULT_PORT,       # applies on next launch
    "volume": 80,               # default audition volume, 0-100
    "auto_audition": False,     # play the focused row when moving with arrow keys
    "density": "comfortable",   # "comfortable" | "compact"
}
DENSITIES = ("comfortable", "compact")
_lock = threading.Lock()


def path(data_dir: str) -> str:
    return os.path.join(data_dir, FILENAME)


def _int(v: Any, name: str, lo: int, hi: int) -> int:
    if isinstance(v, bool):
        raise ValueError(f"{name} must be a whole number")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number") from None
    if f != int(f):
        raise ValueError(f"{name} must be a whole number")
    n = int(f)
    if not lo <= n <= hi:
        raise ValueError(f"{name} must be between {lo} and {hi}")
    return n


def _bool(v: Any, name: str) -> bool:
    if isinstance(v, bool):
        return v
    if v in (0, 1):
        return bool(v)
    if isinstance(v, str) and v.lower() in ("true", "false", "1", "0", "on", "off"):
        return v.lower() in ("true", "1", "on")
    raise ValueError(f"{name} must be true or false")


def validate_value(key: str, v: Any) -> Any:
    """Return the normalized value or raise ValueError with a user-facing message."""
    if key == "root":
        if not isinstance(v, str):
            raise ValueError("Collection folder must be text")
        return v.strip().strip('"')
    if key == "port":
        return _int(v, "Port", 1024, 65535)
    if key == "volume":
        return _int(v, "Default volume", 0, 100)
    if key == "auto_audition":
        return _bool(v, "Auto-audition")
    if key == "density":
        if v not in DENSITIES:
            raise ValueError("Density must be compact or comfortable")
        return v
    raise KeyError(f"Unknown setting: {key}")


def _read_raw(data_dir: str) -> dict:
    try:
        with open(path(data_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load(data_dir: str) -> dict:
    """Known settings with defaults filled in and invalid stored values replaced."""
    raw = _read_raw(data_dir)
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in raw:
            try:
                out[k] = validate_value(k, raw[k])
            except (ValueError, KeyError):
                pass
    return out


def _write(data_dir: str, obj: dict) -> None:
    os.makedirs(data_dir, exist_ok=True)
    p = path(data_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def update(data_dir: str, partial: dict, allow_root: bool = True) -> dict:
    """Validate every key first (all or nothing), then merge into the file. Returns load()."""
    if not isinstance(partial, dict):
        raise ValueError("Settings must be an object")
    clean = {}
    for k, v in partial.items():
        if k == "root" and not allow_root:
            raise ValueError("Change the collection folder with Switch and scan.")
        clean[k] = validate_value(k, v)
    with _lock:
        raw = _read_raw(data_dir)
        raw.update(clean)
        _write(data_dir, raw)
    return load(data_dir)

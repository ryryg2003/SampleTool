"""Favorites, named collections and smart collections (app data dir only).

Membership is keyed by SHA-256 content hash, so it survives rescans and follows a
file after an approved run moves it into Samples\\ (a moved file keeps its hash).
Several files with identical content share one membership: starring one copy stars
the content. Items without a hash (hash error) cannot be starred or collected.

Nothing here touches the collection or the plan: starring or collecting never
changes the plan, so it never clears approval.

File: <data_dir>/collections.json
  {"version": 1, "favorites": [sha256...],
   "collections": [{"id", "name", "kind": "manual"|"smart", "members": [sha256...],
                    "params": {...} (smart only), "created": ts}]}
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Iterable, Optional

FAVORITES_NAME = "Favorites"
MAX_NAME = 80
# Query parameters a smart collection may store (same names as GET /api/samples).
SMART_KEYS = ("q", "pack", "manufacturer", "category", "instrument", "loop_type", "key",
              "genre", "kit", "kind", "status", "origin", "bpm_min", "bpm_max", "new", "fav",
              "collection", "key_compat", "bpm_half", "verified")
NO_HASH_REASON = ("can't be starred or collected: the file could not be fingerprinted "
                  "(SHA-256 failed), so it has no stable identity")


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Collections:
    FILE = "collections.json"

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, self.FILE)
        self.lock = threading.RLock()
        self.favorites: set[str] = set()
        self.colls: list[dict] = []
        self.rev = 0
        self._load()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        self.favorites = {h for h in d.get("favorites") or [] if isinstance(h, str)}
        self.colls = []
        for c in d.get("collections") or []:
            if not isinstance(c, dict) or not c.get("id") or not c.get("name"):
                continue
            kind = "smart" if c.get("kind") == "smart" else "manual"
            self.colls.append({"id": str(c["id"]), "name": str(c["name"]), "kind": kind,
                               "members": [h for h in c.get("members") or [] if isinstance(h, str)],
                               "params": dict(c.get("params") or {}) if kind == "smart" else None,
                               "created": c.get("created")})

    def _save(self) -> None:
        self.rev += 1
        d = {"version": 1, "favorites": sorted(self.favorites),
             "collections": [{k: v for k, v in c.items() if not (k == "params" and v is None)}
                             for c in self.colls]}
        _atomic_write(self.path, json.dumps(d, ensure_ascii=False, indent=1).encode("utf-8"))

    # ---------------------------------------------------------------- lookups
    def get(self, cid: str) -> dict:
        for c in self.colls:
            if c["id"] == cid:
                return c
        raise KeyError(f"Unknown collection {cid}")

    def is_fav(self, sha: Optional[str]) -> bool:
        return bool(sha) and sha in self.favorites

    def member_sets(self) -> dict:
        return {c["id"]: set(c["members"]) for c in self.colls if c["kind"] == "manual"}

    def ids_for(self, sha: Optional[str]) -> list:
        if not sha:
            return []
        return [c["id"] for c in self.colls if c["kind"] == "manual" and sha in c["members"]]

    def names_for(self, sha: Optional[str]) -> list:
        """Names for the catalog column: Favorites first, then manual collections."""
        if not sha:
            return []
        out = [FAVORITES_NAME] if sha in self.favorites else []
        out += sorted((c["name"] for c in self.colls
                       if c["kind"] == "manual" and sha in c["members"]), key=str.casefold)
        return out

    def hashes(self, cid: str) -> set:
        if cid in ("fav", "favorites"):
            return set(self.favorites)
        c = self.get(cid)
        if c["kind"] != "manual":
            raise ValueError("A smart collection is a saved search, not a list of files")
        return set(c["members"])

    # ----------------------------------------------------------------- naming
    def _clean_name(self, name, exclude_id: Optional[str] = None) -> str:
        n = " ".join(str(name or "").split())
        if not n:
            raise ValueError("Give the collection a name")
        if len(n) > MAX_NAME:
            raise ValueError(f"Name is too long (max {MAX_NAME} characters)")
        if n.casefold() == FAVORITES_NAME.casefold():
            raise ValueError("“Favorites” is reserved for starred samples")
        for c in self.colls:
            if c["id"] != exclude_id and c["name"].casefold() == n.casefold():
                raise ValueError(f"A collection named “{c['name']}” already exists")
        return n

    @staticmethod
    def clean_params(params: dict) -> dict:
        out = {}
        for k, v in (params or {}).items():
            if k not in SMART_KEYS or v is None:
                continue
            v = str(v).strip()
            if v:
                out[k] = v
        return out

    # -------------------------------------------------------------- mutations
    def set_favorites(self, hashes: Iterable[str], on: bool) -> int:
        with self.lock:
            hs = {h for h in hashes if h}
            before = len(self.favorites)
            if on:
                self.favorites |= hs
            else:
                self.favorites -= hs
            changed = abs(len(self.favorites) - before)
            self._save()
            return changed

    def create(self, name: str, hashes: Iterable[str] = ()) -> dict:
        with self.lock:
            c = {"id": "c" + secrets.token_hex(6), "name": self._clean_name(name), "kind": "manual",
                 "members": [], "params": None, "created": time.time()}
            for h in hashes:
                if h and h not in c["members"]:
                    c["members"].append(h)
            self.colls.append(c)
            self._save()
            return c

    def create_smart(self, name: str, params: dict) -> dict:
        with self.lock:
            p = self.clean_params(params)
            if not p:
                raise ValueError("Set a search or at least one filter before saving a smart collection")
            c = {"id": "s" + secrets.token_hex(6), "name": self._clean_name(name), "kind": "smart",
                 "members": [], "params": p, "created": time.time()}
            self.colls.append(c)
            self._save()
            return c

    def update_smart(self, cid: str, params: dict) -> dict:
        with self.lock:
            c = self.get(cid)
            if c["kind"] != "smart":
                raise ValueError("Only smart collections store a search")
            p = self.clean_params(params)
            if not p:
                raise ValueError("Set a search or at least one filter first")
            c["params"] = p
            self._save()
            return c

    def rename(self, cid: str, name: str) -> dict:
        with self.lock:
            c = self.get(cid)
            c["name"] = self._clean_name(name, exclude_id=cid)
            self._save()
            return c

    def delete(self, cid: str) -> None:
        with self.lock:
            c = self.get(cid)
            self.colls.remove(c)
            self._save()

    def add(self, cid: str, hashes: Iterable[str]) -> int:
        with self.lock:
            c = self.get(cid)
            if c["kind"] != "manual":
                raise ValueError("Files can't be added to a smart collection; it is a saved search")
            have = set(c["members"])
            n = 0
            for h in hashes:
                if h and h not in have:
                    c["members"].append(h)
                    have.add(h)
                    n += 1
            self._save()
            return n

    def remove(self, cid: str, hashes: Iterable[str]) -> int:
        with self.lock:
            c = self.get(cid)
            if c["kind"] != "manual":
                raise ValueError("Files can't be removed from a smart collection; edit its filters")
            drop = set(hashes)
            before = len(c["members"])
            c["members"] = [h for h in c["members"] if h not in drop]
            self._save()
            return before - len(c["members"])

    # ------------------------------------------------------------------- views
    def public(self, c: dict, count: Optional[int] = None) -> dict:
        d = {"id": c["id"], "name": c["name"], "kind": c["kind"], "created": c.get("created")}
        if c["kind"] == "smart":
            d["params"] = dict(c["params"] or {})
        else:
            d["members"] = len(c["members"])
        if count is not None:
            d["count"] = count
        return d

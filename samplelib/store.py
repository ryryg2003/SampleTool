"""Application state: inventory, manual edits, kits, plan, approval, execution.

OWNER: lead. Everything here lives in the app data dir; nothing is written to
the collection.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
from typing import Any, Optional

from . import models
from .models import field

EDITABLE = {"category", "instrument", "loop_type", "bpm", "key", "genre", "tags", "manufacturer"}


def default_data_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "SampleOrganizer")
    return os.path.join(os.path.expanduser("~"), ".sample-organizer")


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Store:
    def __init__(self, data_dir: str, root: str):
        self.lock = threading.RLock()
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        self.items: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.kits: list[dict] = []
        self.kit_by_id: dict[str, dict] = {}
        self.edits: dict[str, dict] = {}       # rel_path.casefold() -> changes
        self.kit_edits: list[dict] = []        # replayed in order after detection
        self.plan: Optional[dict] = None
        self.plan_stale = False
        self.approved_hash: Optional[str] = None
        self.execution: Optional[dict] = None
        self.hash_cache: dict = {}
        self.analysis_cache: dict = {}
        self.gen = 0  # bumps on every edit/rescan; a plan built from an older gen is stale
        self._hay: dict[str, str] = {}
        self._dup_counts: dict[str, int] = {}
        self._final_paths: dict[str, str] = {}
        self._load_settings(root)
        self._load()

    # ------------------------------------------------------------------ paths
    def _p(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    @property
    def dest_root(self) -> str:
        return os.path.join(self.root, models.DEST_DIRNAME)

    def journal_path(self, plan_hash: str) -> str:
        import hashlib
        rk = hashlib.sha1(os.path.normcase(self.root).encode("utf-8")).hexdigest()[:8]
        return self._p(f"journal-{rk}-{plan_hash[:32]}.jsonl")

    # ------------------------------------------------------------ persistence
    def _read_json(self, name: str, default: Any) -> Any:
        p = self._p(name)
        try:
            if name.endswith(".gz"):
                with gzip.open(p, "rt", encoding="utf-8") as f:
                    return json.load(f)
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            return default

    def _write_json(self, name: str, obj: Any) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        if name.endswith(".gz"):
            raw = gzip.compress(raw, compresslevel=5)
        _atomic_write(self._p(name), raw)

    def _load_settings(self, root: str) -> None:
        s = self._read_json("settings.json", {})
        self.root = root or s.get("root") or (r"E:\Sample Packs" if os.name == "nt" else "")
        self.save_settings()

    def save_settings(self) -> None:
        self._write_json("settings.json", {"root": self.root})

    def _load(self) -> None:
        inv = self._read_json("inventory.json.gz", None)
        self.edits = self._read_json("edits.json", {})
        self.kit_edits = self._read_json("kit_edits.json", [])
        self.hash_cache = self._read_json("hash_cache.json.gz", {})
        self.analysis_cache = self._read_json("analysis_cache.json.gz", {})
        if inv and os.path.normcase(inv.get("root", "")) == os.path.normcase(self.root):
            self._set_inventory(inv["items"], inv["kits"], persist=False)
        p = self._read_json("plan.json.gz", None)
        if p and self.items:
            self.plan = p["plan"]
            self.plan_stale = p.get("stale", False)
            a = self._read_json("approval.json", {})
            if a.get("plan_hash") == self.plan.get("plan_hash") and not self.plan_stale:
                self.approved_hash = a["plan_hash"]
        ex = self._read_json("execution.json.gz", None)
        if ex and self.items:
            self.execution = ex
            self._index_results()

    def save_edits(self) -> None:
        self._write_json("edits.json", self.edits)
        self._write_json("kit_edits.json", self.kit_edits)

    # -------------------------------------------------------------- inventory
    def set_root(self, root: str) -> None:
        """Switch collections. Caller must hold no running job (server enforces)."""
        with self.lock:
            if os.path.normcase(root) != os.path.normcase(self.root):
                self._invalidate_plan()
                for name in ("plan.json.gz", "approval.json", "execution.json.gz",
                             "inventory.json.gz"):
                    try:
                        os.remove(self._p(name))
                    except OSError:
                        pass
                self.root = root
                self.save_settings()
                self.items, self.kits, self.plan, self.execution = [], [], None, None
                self.by_id, self.kit_by_id = {}, {}
                self._final_paths = {}
                self.approved_hash = None
                self.plan_stale = False

    def _set_inventory(self, items: list, kits: list, persist: bool = True) -> None:
        """items/kits are the detector's output (pre manual edits)."""
        with self.lock:
            if persist:
                self._write_json("inventory.json.gz", {"root": self.root, "items": items,
                                                       "kits": kits, "ts": time.time()})
            self.items = items
            self.by_id = {i["id"]: i for i in items}
            self.kits = kits
            self.kit_by_id = {k["id"]: k for k in kits}
            for it in items:
                ch = self.edits.get(it["rel_path"].casefold() + "|" + it["origin"])
                if ch:
                    self._apply_changes(it, ch)
            for ke in list(self.kit_edits):
                try:
                    self._apply_kit_edit(ke, record=False)
                except KeyError:
                    pass
            self._reindex()

    def set_scan_result(self, items: list, kits: list, hash_cache: dict,
                        analysis_cache: Optional[dict] = None) -> None:
        with self.lock:
            self.hash_cache = hash_cache
            self._write_json("hash_cache.json.gz", hash_cache)
            if analysis_cache is not None:
                self.analysis_cache = analysis_cache
                self._write_json("analysis_cache.json.gz", analysis_cache)
            self._set_inventory(items, kits)
            self._invalidate_plan()
            self.execution = None
            self._final_paths = {}
            try:
                os.remove(self._p("execution.json.gz"))
            except OSError:
                pass

    def _reindex(self) -> None:
        counts: dict[str, int] = {}
        for it in self.items:
            h = it.get("sha256")
            if h:
                counts[h] = counts.get(h, 0) + 1
        self._dup_counts = counts
        self._hay = {}
        for it in self.items:  # build the search index now so the first search is fast
            self._haystack(it)

    def _invalidate_plan(self) -> None:
        self.gen += 1
        if self.plan is not None:
            self.plan_stale = True
            self._write_json("plan.json.gz", {"plan": self.plan, "stale": True})
        self.approved_hash = None
        try:
            os.remove(self._p("approval.json"))
        except OSError:
            pass

    # ------------------------------------------------------------------ edits
    @staticmethod
    def _norm_change(k: str, v: Any) -> Any:
        if v == "" or v is None:
            return None
        if k == "bpm":
            v = float(v)
            if not (20 <= v <= 400):
                raise ValueError("BPM must be between 20 and 400")
            return round(v, 2)
        if k == "tags":
            if isinstance(v, str):
                v = [t.strip() for t in v.split(",")]
            return [t for t in (str(x).strip() for x in v) if t]
        if k == "category" and v not in models.CATEGORIES:
            raise ValueError(f"Unknown category: {v}")
        if k == "loop_type" and v not in ("one-shot", "loop"):
            raise ValueError("loop_type must be one-shot or loop")
        return str(v).strip()

    def _apply_changes(self, it: dict, ch: dict) -> None:
        meta = it.setdefault("meta", {})
        for k, v in ch.items():
            if k == "excluded":
                it["excluded"] = bool(v)
            elif k in EDITABLE:
                meta[k] = field(v, "manual", None)
        it["_edited"] = True

    def edit_items(self, ids: list, changes: dict) -> list:
        with self.lock:
            clean = {}
            for k, v in changes.items():
                if k == "excluded":
                    clean[k] = bool(v)
                elif k in EDITABLE:
                    clean[k] = self._norm_change(k, v)
                else:
                    raise ValueError(f"Field not editable: {k}")
            out = []
            for i in ids:
                it = self.by_id.get(i)
                if not it:
                    raise KeyError(f"Unknown item {i}")
                if it["origin"] != "incoming" and "excluded" in clean:
                    raise ValueError("Library files are not part of the plan")
                key = it["rel_path"].casefold() + "|" + it["origin"]
                self.edits.setdefault(key, {}).update(clean)
                self._apply_changes(it, clean)
                out.append(it)
                self._hay.pop(it["id"], None)
            self._invalidate_plan()
            self.save_edits()
            return out

    # ------------------------------------------------------------------- kits
    def _kit_members(self, kit: dict) -> list:
        return [self.by_id[i] for i in kit.get("member_ids", []) if i in self.by_id]

    def _apply_kit_edit(self, e: dict, record: bool = True) -> dict:
        a = e["action"]
        if a == "create":
            ids = [i for i in e["ids"] if i in self.by_id and self.by_id[i]["origin"] == "incoming"]
            if not ids:
                raise KeyError("no items")
            rels = [self.by_id[i]["rel_path"] for i in ids]
            root_rel = os.path.commonpath([r.rsplit("/", 1)[0] if "/" in r else "" for r in rels]) \
                if all("/" in r for r in rels) else ""
            root_rel = root_rel.replace("\\", "/")
            first = self.by_id[ids[0]]
            kid = e.get("kit_id") or models.kit_id(root_rel, e["name"])
            e["kit_id"] = kid
            for i in ids:  # remove from previous kits
                self._detach(i)
            kit = {"id": kid, "name": e["name"], "pack": first.get("pack"),
                   "manufacturer": (first.get("meta", {}).get("manufacturer") or {}).get("value"),
                   "root_rel": root_rel, "member_ids": ids, "bpm": None, "key": None,
                   "evidence": ["Created manually"], "confidence": 1.0, "held": False,
                   "hold_reason": None, "excluded": False, "ref_checks": [], "manual": True}
            self.kits.append(kit)
            self.kit_by_id[kid] = kit
            for i in ids:
                self.by_id[i]["kit_id"] = kid
        else:
            kit = self.kit_by_id[e["kit_id"]]
            if a == "hold":
                kit["held"] = True
                kit["hold_reason"] = e.get("reason") or "Held by you for review"
            elif a == "release":
                kit["held"] = False
                kit["hold_reason"] = None
            elif a == "exclude":
                kit["excluded"] = True
            elif a == "include":
                kit["excluded"] = False
            elif a == "rename":
                name = (e.get("name") or "").strip()
                if not name:
                    raise ValueError("Name required")
                kit["name"] = name
                kit["manual"] = True
            elif a == "remove_members":
                for i in e.get("ids", []):
                    if i in kit["member_ids"]:
                        kit["member_ids"].remove(i)
                        if i in self.by_id:
                            self.by_id[i]["kit_id"] = None
                kit["manual"] = True
            elif a == "add_members":
                for i in e.get("ids", []):
                    if i in self.by_id and self.by_id[i]["origin"] == "incoming":
                        self._detach(i)
                        kit["member_ids"].append(i)
                        self.by_id[i]["kit_id"] = kit["id"]
                kit["manual"] = True
            elif a == "dissolve":
                for i in kit["member_ids"]:
                    if i in self.by_id:
                        self.by_id[i]["kit_id"] = None
                self.kits.remove(kit)
                del self.kit_by_id[kit["id"]]
            else:
                raise ValueError(f"Unknown kit action {a}")
        if record:
            self.kit_edits.append(e)
        return kit

    def _detach(self, item_id: str) -> None:
        it = self.by_id[item_id]
        old = it.get("kit_id")
        if old and old in self.kit_by_id:
            k = self.kit_by_id[old]
            if item_id in k["member_ids"]:
                k["member_ids"].remove(item_id)
        it["kit_id"] = None

    def edit_kit(self, e: dict) -> Optional[dict]:
        with self.lock:
            if e.get("action") != "create" and e.get("kit_id") not in self.kit_by_id:
                raise KeyError("Unknown kit")
            self._invalidate_plan()
            kit = self._apply_kit_edit(dict(e))
            self.save_edits()
            self._hay = {}
            return kit if kit.get("id") in self.kit_by_id else None

    # ------------------------------------------------------------------- plan
    def set_plan(self, plan: dict, gen: Optional[int] = None) -> None:
        bba: dict = {}
        for o in plan.get("ops", []):
            bba[o.get("action")] = bba.get(o.get("action"), 0) + int(o.get("size") or 0)
        plan.setdefault("summary", {}).setdefault("bytes_by_action", bba)
        with self.lock:
            self.plan = plan
            self.plan_stale = gen is not None and gen != self.gen
            self.approved_hash = None
            try:
                os.remove(self._p("approval.json"))
            except OSError:
                pass
            self._write_json("plan.json.gz", {"plan": plan, "stale": self.plan_stale})

    def approve(self, plan_hash: str) -> None:
        with self.lock:
            if not self.plan:
                raise PermissionError("No plan has been built")
            if self.plan_stale:
                raise PermissionError("The plan changed after your edits. Rebuild and review it again.")
            if plan_hash != self.plan["plan_hash"]:
                raise PermissionError("That approval is for a different version of the plan.")
            self.approved_hash = plan_hash
            self._write_json("approval.json", {"plan_hash": plan_hash, "ts": time.time()})

    def set_execution(self, ex: dict) -> None:
        with self.lock:
            self.execution = ex
            self._write_json("execution.json.gz", ex)
            self._index_results()

    def _index_results(self) -> None:
        fp = {}
        ops = {o["id"]: o for o in (self.plan or {}).get("ops", [])}
        for r in (self.execution or {}).get("results", []):
            o = ops.get(r.get("op_id"))
            if o and r.get("status") in ("done", "already-done") and r.get("dest"):
                fp[o["item_id"]] = r["dest"]
        self._final_paths = fp

    # ----------------------------------------------------------------- views
    def kit_name(self, kid: Optional[str]) -> Optional[str]:
        k = self.kit_by_id.get(kid) if kid else None
        return k["name"] if k else None

    def _mv(self, it: dict, k: str) -> Any:
        f = it.get("meta", {}).get(k)
        return f.get("value") if isinstance(f, dict) else None

    def review_status(self, it: dict) -> str:
        if it["id"] in self._final_paths:
            return "done"
        if it.get("excluded"):
            return "excluded"
        kit = self.kit_by_id.get(it.get("kit_id")) if it.get("kit_id") else None
        if kit and kit.get("excluded"):
            return "excluded"
        if kit and kit.get("held"):
            return "held"
        if it.get("_edited"):
            return "edited"
        if it["origin"] == "incoming" and it.get("kind") == "audio" and not kit:
            c = it.get("meta", {}).get("category") or {}
            if c.get("value") is None or (c.get("confidence") is not None and c["confidence"] < 0.5):
                return "needs-review"
        if it.get("hash_error"):
            return "needs-review"
        return "ok"

    def row(self, it: dict, propose=None) -> dict:
        audio = it.get("audio") or {}
        meta = it.get("meta", {})
        kit = self.kit_by_id.get(it.get("kit_id")) if it.get("kit_id") else None
        an = it.get("analysis") or {}
        proposed = None
        if propose and it["origin"] == "incoming":
            try:
                proposed = propose(it, kit, self.dest_root)
            except Exception:  # preview must never break browsing
                proposed = None
        return {
            "id": it["id"], "origin": it["origin"], "name": it["name"],
            "rel_path": it["rel_path"], "abs_path": it["abs_path"], "ext": it["ext"],
            "size": it["size"], "kind": it["kind"], "pack": it.get("pack"),
            "manufacturer": self._mv(it, "manufacturer"), "category": self._mv(it, "category"),
            "instrument": self._mv(it, "instrument"), "loop_type": self._mv(it, "loop_type"),
            "bpm": self._mv(it, "bpm"), "key": self._mv(it, "key"), "genre": self._mv(it, "genre"),
            "tags": self._mv(it, "tags") or [],
            "bars": an.get("bars"), "on_grid": an.get("on_grid"),
            "downbeat_ms": an.get("downbeat_ms"),
            "duration_s": audio.get("duration_s"), "sample_rate": audio.get("sample_rate"),
            "channels": audio.get("channels"), "bits": audio.get("bits"),
            "sha256": it.get("sha256"), "kit_id": it.get("kit_id"),
            "kit_name": kit["name"] if kit else None, "excluded": bool(it.get("excluded")),
            "preview": it.get("preview", "none"), "warnings": it.get("warnings", []),
            "review_status": self.review_status(it), "proposed_dest": proposed,
            "final_path": self._final_paths.get(it["id"]),
            "dup_count": self._dup_counts.get(it.get("sha256") or "", 1),
            "prov": {k: {"source": (meta.get(k) or {}).get("source"),
                         "confidence": (meta.get(k) or {}).get("confidence")}
                     for k in models.META_KEYS},
        }

    def _haystack(self, it: dict) -> str:
        h = self._hay.get(it["id"])
        if h is None:
            parts = [it["rel_path"], it.get("pack") or "", self.kit_name(it.get("kit_id")) or ""]
            for k in ("manufacturer", "instrument", "genre", "key", "category", "loop_type"):
                v = self._mv(it, k)
                if v:
                    parts.append(str(v))
            b = self._mv(it, "bpm")
            if b:
                parts.append(f"{b:g}bpm {b:g}")
            parts.extend(self._mv(it, "tags") or [])
            h = " ".join(parts).casefold()
            self._hay[it["id"]] = h
        return h

    def query(self, q: dict) -> tuple[int, list]:
        """Filter + sort. q: dict of single string values."""
        g = lambda k: (q.get(k) or "").strip()
        terms = g("q").casefold().split()
        eq = {k: g(k) for k in ("pack", "manufacturer", "category", "instrument", "loop_type",
                                "key", "genre", "kit", "kind", "status", "origin") if g(k)}
        if eq.get("kit", "").casefold() == "none":
            eq["kit"] = "__none__"  # loose sounds: not part of any kit
        try:
            bmin = float(g("bpm_min")) if g("bpm_min") else None
            bmax = float(g("bpm_max")) if g("bpm_max") else None
        except ValueError:
            bmin = bmax = None
        with self.lock:
            out = []
            for it in self.items:
                if terms:
                    hay = self._haystack(it)
                    if not all(t in hay for t in terms):
                        continue
                ok = True
                for k, v in eq.items():
                    if k == "pack":
                        cur = it.get("pack")
                    elif k == "kit":
                        cur = it.get("kit_id")
                    elif k == "kind":
                        cur = it.get("kind")
                    elif k == "origin":
                        cur = it.get("origin")
                    elif k == "status":
                        cur = self.review_status(it)
                    else:
                        cur = self._mv(it, k)
                    if k == "kind" and "," in v:
                        if (cur or "") not in {x.strip() for x in v.split(",")}:
                            ok = False
                            break
                        continue
                    if v == "__none__":
                        if cur not in (None, ""):
                            ok = False
                            break
                    elif (str(cur).casefold() if cur is not None else None) != v.casefold():
                        ok = False
                        break
                if not ok:
                    continue
                if bmin is not None or bmax is not None:
                    b = self._mv(it, "bpm")
                    if b is None or (bmin is not None and b < bmin) or (bmax is not None and b > bmax):
                        continue
                out.append(it)
            sort = g("sort") or "name"
            rev = g("dir") == "desc"
            keyf = {
                "name": lambda i: i["name"].casefold(),
                "pack": lambda i: ((i.get("pack") or "").casefold(), i["name"].casefold()),
                "bpm": lambda i: self._mv(i, "bpm") or 0.0,
                "key": lambda i: str(self._mv(i, "key") or ""),
                "duration": lambda i: (i.get("audio") or {}).get("duration_s") or 0.0,
                "size": lambda i: i["size"],
                "status": lambda i: self.review_status(i),
            }.get(sort, lambda i: i["name"].casefold())
            # unknowns last regardless of direction
            known = [i for i in out if not _is_unknown(sort, i, self)]
            unknown = [i for i in out if _is_unknown(sort, i, self)]
            known.sort(key=keyf, reverse=rev)
            unknown.sort(key=lambda i: i["name"].casefold())
            return len(out), known + unknown

    def packs(self, q: str = "") -> list:
        q = q.casefold().strip()
        agg: dict[str, dict] = {}
        with self.lock:
            for it in self.items:
                p = it.get("pack")
                if not p or it["origin"] != "incoming":
                    continue
                a = agg.get(p)
                if a is None:
                    a = agg[p] = {"name": p, "manufacturer": None, "files": 0, "audio": 0,
                                  "kits": 0, "bytes": 0}
                a["files"] += 1
                a["bytes"] += it["size"]
                if it["kind"] == "audio":
                    a["audio"] += 1
                if not a["manufacturer"]:
                    a["manufacturer"] = self._mv(it, "manufacturer")
            for k in self.kits:
                if k.get("pack") in agg:
                    agg[k["pack"]]["kits"] += 1
        res = sorted(agg.values(), key=lambda a: a["name"].casefold())
        if q:
            res = [a for a in res if q in a["name"].casefold() or q in (a["manufacturer"] or "").casefold()]
        return res

    def facets(self) -> dict:
        sets = {k: set() for k in ("instruments", "keys", "genres", "manufacturers", "packs")}
        with self.lock:
            for it in self.items:
                for k, s in (("instrument", "instruments"), ("key", "keys"), ("genre", "genres"),
                             ("manufacturer", "manufacturers")):
                    v = self._mv(it, k)
                    if v:
                        sets[s].add(str(v))
                if it.get("pack"):
                    sets["packs"].add(it["pack"])
        out = {k: sorted(v, key=str.casefold) for k, v in sets.items()}
        out["categories"] = list(models.CATEGORIES)
        out["loop_types"] = ["one-shot", "loop"]
        out["statuses"] = ["ok", "needs-review", "edited", "held", "excluded", "done"]
        return out


def _is_unknown(sort: str, it: dict, st: Store) -> bool:
    if sort == "bpm":
        return st._mv(it, "bpm") is None
    if sort == "key":
        return not st._mv(it, "key")
    if sort == "duration":
        return not (it.get("audio") or {}).get("duration_s")
    return False


def scan_summary(st: "Store") -> dict:
    """Counts the Scan screen can show before any plan exists."""
    inc = [i for i in st.items if i["origin"] == "incoming"]
    by_hash: dict = {}
    for i in st.items:
        if i.get("sha256"):
            by_hash.setdefault(i["sha256"], []).append(i)
    dup_groups = [g for g in by_hash.values()
                  if len(g) > 1 and any(x["origin"] == "incoming" for x in g)]
    lib_names: dict = {}
    for i in st.items:
        if i["origin"] == "library":
            lib_names.setdefault(i["name"].casefold(), set()).add(i.get("sha256"))
    # Estimate before a plan exists: an incoming file needs a renamed destination when its
    # name clashes with a library file of different content. Exact duplicates are kept in
    # place instead of renamed, so they are not counted.
    collisions = sum(1 for i in inc if i["name"].casefold() in lib_names
                     and i.get("sha256") not in lib_names[i["name"].casefold()]
                     and len(by_hash.get(i.get("sha256"), ())) <= 1)
    statuses: dict = {}
    for i in inc:
        s = st.review_status(i)
        statuses[s] = statuses.get(s, 0) + 1
    return {
        "files": len(inc), "bytes": sum(i["size"] for i in inc),
        "audio": sum(1 for i in inc if i["kind"] == "audio"),
        "packs": len({i.get("pack") for i in inc if i.get("pack")}),
        "kits": len(st.kits),
        "library_files": sum(1 for i in st.items if i["origin"] == "library"),
        "duplicate_groups": len(dup_groups),
        "unknown_formats": sum(1 for i in inc if i["kind"] == "other"),
        "unreadable": sum(1 for i in inc if i.get("hash_error")),
        "collisions": ((st.plan or {}).get("summary") or {}).get("collisions", collisions)
                      if st.plan and not st.plan_stale else collisions,
        "needs_review": statuses.get("needs-review", 0),
        "held": statuses.get("held", 0),
    }

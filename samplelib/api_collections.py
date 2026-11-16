"""HTTP endpoints for favorites and collections (loaded by server.py as an extension).

None of these change the plan, so none of them clear plan approval. They only write
<data_dir>/collections.json.
"""
from __future__ import annotations

from .collections import NO_HASH_REASON

MUTATING: set = set()   # starring/collecting never touches the collection or the plan


def init(app) -> None:
    st = app.store
    if not hasattr(st, "collections"):
        from .collections import Collections
        st.collections = Collections(st.data_dir)


def state(app) -> dict:
    col = app.store.collections
    return {"collections": {"favorites": len(col.favorites),
                            "manual": sum(1 for c in col.colls if c["kind"] == "manual"),
                            "smart": sum(1 for c in col.colls if c["kind"] == "smart"),
                            "rev": col.rev}}


def listing(st) -> dict:
    """Sidebar data: counts are the number of scanned files that match."""
    col = st.collections
    with st.lock:
        sets = col.member_sets()
        counts = {cid: 0 for cid in sets}
        fav = 0
        for it in st.items:
            h = it.get("sha256")
            if not h:
                continue
            if h in col.favorites:
                fav += 1
            for cid, hs in sets.items():
                if h in hs:
                    counts[cid] += 1
        out = []
        for c in col.colls:
            if c["kind"] == "smart":
                try:
                    n, _ = st.query(dict(c["params"] or {}))
                except (KeyError, ValueError):
                    n = 0
            else:
                n = counts.get(c["id"], 0)
            out.append(col.public(c, n))
        new = sum(1 for i in st.items if i.get("is_new"))
    return {"favorites": {"count": fav, "hashes": len(col.favorites)},
            "new": {"count": new}, "collections": out}


def _hashes(st, ids) -> tuple[list, list, list]:
    """(hashes, ok_ids, skipped[{id,name,reason}]) for item ids."""
    hs, okids, skipped = [], [], []
    with st.lock:
        for i in ids or []:
            it = st.by_id.get(i)
            if not it:
                raise KeyError(f"Unknown item {i}")
            if not it.get("sha256"):
                skipped.append({"id": i, "name": it.get("name"), "reason": NO_HASH_REASON})
                continue
            okids.append(i)
            if it["sha256"] not in hs:
                hs.append(it["sha256"])
    return hs, okids, skipped


def _affected(st, hashes) -> list:
    hs = set(hashes)
    with st.lock:
        return [i["id"] for i in st.items if i.get("sha256") in hs]


def get(handler, path: str, q: dict) -> bool:
    st = handler.app.store
    if path == "/api/collections":
        handler._send(200, listing(st))
        return True
    if path.startswith("/api/collections/"):
        c = st.collections.get(path.rsplit("/", 1)[1])
        handler._send(200, st.collections.public(c))
        return True
    return False


def post(handler, path: str, b: dict) -> bool:
    st = handler.app.store
    col = st.collections
    if not path.startswith("/api/collections"):
        return False
    if path == "/api/collections/star":
        ids = list(b.get("ids") or [])
        if not ids:
            raise ValueError("Select one or more samples to star")
        hs, okids, skipped = _hashes(st, ids)
        if not hs:
            raise ValueError("Not starred: the selected file " + NO_HASH_REASON)
        on = b.get("on")
        if on is None:   # toggle: unstar only when every selected content is starred already
            on = not all(h in col.favorites for h in hs)
        changed = col.set_favorites(hs, bool(on))
        handler._send(200, {"ok": True, "starred": bool(on), "changed": changed,
                            "hashes": hs, "affected_ids": _affected(st, hs), "skipped": skipped})
        return True
    if path == "/api/collections/create":
        hs, okids, skipped = _hashes(st, b.get("ids") or [])
        c = col.create(b.get("name"), hs)
        handler._send(200, {"ok": True, "collection": col.public(c), "added": len(hs),
                            "skipped": skipped})
        return True
    if path == "/api/collections/smart":
        if b.get("id"):
            c = col.update_smart(str(b["id"]), dict(b.get("params") or {}))
        else:
            c = col.create_smart(b.get("name"), dict(b.get("params") or {}))
        st.query(dict(c["params"]))   # validate (unknown collection -> 400)
        handler._send(200, {"ok": True, "collection": col.public(c)})
        return True
    if path == "/api/collections/rename":
        c = col.rename(str(b.get("id") or ""), b.get("name"))
        handler._send(200, {"ok": True, "collection": col.public(c)})
        return True
    if path == "/api/collections/delete":
        col.delete(str(b.get("id") or ""))
        handler._send(200, {"ok": True})
        return True
    if path in ("/api/collections/add", "/api/collections/remove"):
        cid = str(b.get("id") or "")
        col.get(cid)
        hs, okids, skipped = _hashes(st, b.get("ids") or [])
        if not hs:
            raise ValueError("Nothing to change: the selected file " + NO_HASH_REASON)
        n = col.add(cid, hs) if path.endswith("/add") else col.remove(cid, hs)
        handler._send(200, {"ok": True, "changed": n, "hashes": hs,
                            "affected_ids": _affected(st, hs), "skipped": skipped,
                            "collection": col.public(col.get(cid))})
        return True
    return False

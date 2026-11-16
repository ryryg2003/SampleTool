""""New since last run": which files arrived after the last approved run.

Definition (documented choice)
- A *baseline* of known files is kept in the data dir (``known_files.json.gz``).
- The baseline is taken at the first-ever scan of a collection, and it moves forward
  only when an approved run has happened since it was taken: at the next scan the
  baseline becomes "every file of the previous scan, plus every destination the
  run wrote". Rescans without a run in between do NOT move it, so files stay "new"
  until a run has happened (scanning twice does not hide them).
- First-ever scan: nothing is new (the filter would otherwise match everything).
  Upgrading users whose previous version left an inventory but no baseline: that
  previous inventory becomes the baseline, so what was added since then is new.
- A file is known when its normalized path key is in the baseline, or when its file
  identity (volume:inode) *and* size match a baseline file. The identity check lets
  files that a run moved into Samples\\ (link + unlink keeps the identity) stay known;
  the size check guards against reused inode numbers. Content (SHA-256) is NOT used,
  so a new file that duplicates an old one is still new; duplicate handling for it
  is done by the planner, which always sees every item.

``restrict_plan_to_new`` turns a full plan into a "plan only new files" plan.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from .models import op_id, plan_hash
from .winfs import path_key

NOT_NEW_REASON = "not new since last run; left for a later plan"


def _fid_key(it: dict) -> Optional[str]:
    fid = it.get("file_id") or ""
    if not fid or fid.endswith(":0") or fid.startswith("0:"):
        return None
    return f"{fid}|{it.get('size')}"


def snapshot(items: list, extra_paths=(), reason: str = "scan") -> dict:
    paths = {path_key(i["abs_path"]) for i in items if i.get("abs_path")}
    paths |= {path_key(p) for p in extra_paths if p}
    fids = {k for k in (_fid_key(i) for i in items) if k}
    return {"ts": time.time(), "reason": reason, "paths": sorted(paths), "file_ids": sorted(fids)}


def is_known(it: dict, paths: set, fids: set) -> bool:
    if path_key(it["abs_path"]) in paths:
        return True
    k = _fid_key(it)
    return bool(k and k in fids)


def mark_new(new_items: list, prev_items: list, prev_final_paths: list,
             prev_exec_ts: Optional[float], baseline: Optional[dict]) -> tuple[dict, bool]:
    """Set ``is_new`` on every new item. Returns (baseline to keep, changed)."""
    changed = False
    if baseline is None:
        if prev_items:   # upgrade path: previous version's inventory is the baseline
            baseline = snapshot(prev_items, prev_final_paths, "previous scan")
        else:            # first-ever scan: nothing is new
            baseline = snapshot(new_items, (), "first scan")
        changed = True
    elif prev_exec_ts and prev_exec_ts > (baseline.get("ts") or 0) and prev_items:
        baseline = snapshot(prev_items, prev_final_paths, "last run")
        changed = True
    paths, fids = set(baseline.get("paths") or ()), set(baseline.get("file_ids") or ())
    for it in new_items:
        it["is_new"] = not is_known(it, paths, fids)
    return baseline, changed


# --------------------------------------------------------------- plan only new
def _summary(plan: dict, ops: list) -> dict:
    s = dict(plan.get("summary") or {})
    by = {}
    for o in ops:
        by[o["action"]] = by.get(o["action"], 0) + 1
    s["by_action"] = dict(sorted(by.items()))
    s["held"] = by.get("hold", 0)
    s["duplicate_bytes_avoided"] = sum(o.get("size") or 0 for o in ops
                                       if o["action"] == "link" or
                                       (o["action"] == "keep" and o.get("canonical")))
    s["collisions"] = sum(1 for o in ops if o["action"] in ("move", "link")
                          and "name-collision" in (o.get("flags") or []))
    bba: dict = {}
    for o in ops:
        bba[o["action"]] = bba.get(o["action"], 0) + int(o.get("size") or 0)
    s["bytes_by_action"] = bba
    return s


def restrict_plan_to_new(plan: dict, items: list) -> dict:
    """Keep every op of a new incoming item; every other incoming item gets ``keep``.

    The full plan was built from all items, so duplicate detection saw the library
    and all incoming files. Where a new file relied on an old file that now stays in
    place (old file was the canonical copy), the new file is re-pointed:
    - a new kit member that would have been a hard link to the old file's planned
      destination is moved instead (the first such member per content), and further
      new kit members of that content link to it;
    - a new loose duplicate keeps pointing at a real file: the moved new copy if any,
      otherwise the old file where it is now.
    """
    from .planner import ACTION_ORDER
    by_id = {i["id"]: i for i in items}
    new_ids = {i["id"] for i in items if i.get("origin") == "incoming" and i.get("is_new")}
    ops = [dict(o, flags=list(o.get("flags") or [])) for o in plan["ops"]]
    old_dest_src = {}           # planned dest of an old file -> its current source path
    old_move_ids = set()
    out = []
    for o in ops:
        if o["item_id"] in new_ids:
            out.append(o)
            continue
        if o["action"] in ("move", "link"):
            if o.get("dest"):
                old_dest_src[os.path.normcase(os.path.normpath(o["dest"]))] = o["source"]
            if o["action"] == "move":
                old_move_ids.add(o["id"])
        k = {"id": op_id("keep", o["item_id"], None), "action": "keep", "dest": None,
             "link_to": None, "depends_on": None, "canonical": None,
             "reason": NOT_NEW_REASON, "flags": [f for f in o["flags"] if f in ("kit",)] + ["not-new"]}
        o.update(k)
        out.append(o)

    def nk(p):
        return os.path.normcase(os.path.normpath(p)) if p else None

    promoted = {}   # old source path -> new op that now carries the content
    for o in sorted(out, key=lambda o: (o.get("source") or "").casefold()):
        if o["item_id"] not in new_ids:
            continue
        if o["action"] == "link" and (o.get("depends_on") in old_move_ids or
                                      nk(o.get("link_to")) in old_dest_src):
            old_src = old_dest_src.get(nk(o.get("link_to")))
            carrier = promoted.get(old_src)
            if carrier is None:
                o["action"] = "move"
                o["link_to"] = None
                o["depends_on"] = None
                o["canonical"] = None
                o["reason"] = ("kit member; its duplicate %s is not new and stays in place, "
                               "so this copy is moved" % (old_src or "elsewhere"))
                o["flags"].append("canonical")
                o["id"] = op_id("move", o["item_id"], o["dest"])
                promoted[old_src] = o
            else:
                o["link_to"] = carrier["dest"]
                o["canonical"] = carrier["dest"]
                o["depends_on"] = carrier["id"]
                o["id"] = op_id("link", o["item_id"], o["dest"])
        elif o["action"] == "keep" and nk(o.get("canonical")) in old_dest_src:
            old_src = old_dest_src[nk(o["canonical"])]
            carrier = promoted.get(old_src)
            o["canonical"] = carrier["dest"] if carrier else old_src
            o["reason"] = "exact duplicate of %s; left in place" % (
                carrier["source"] if carrier else old_src)
    # a keep whose canonical was promoted later in iteration order: second pass
    for o in out:
        if o["item_id"] in new_ids and o["action"] == "keep" and o.get("canonical") in promoted:
            o["canonical"] = promoted[o["canonical"]]["dest"]
    out.sort(key=lambda o: (ACTION_ORDER[o["action"]],
                            (o.get("dest") or o.get("source") or "").casefold(), o["id"]))
    res = dict(plan)
    res["ops"] = out
    res["plan_hash"] = plan_hash(out)
    res["summary"] = _summary(plan, out)
    res["summary"]["only_new"] = True
    res["summary"]["new_files"] = sum(1 for i in new_ids if i in by_id)
    res["only_new"] = True
    return res

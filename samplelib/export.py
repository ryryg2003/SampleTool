"""Post-run reports and library verification.

write_reports() writes four utf-8-sig CSVs (Excel friendly) into catalog_dir.
It is only called after an approved execution run (CONTRACT golden rule 2).
verify_library() re-checks the finished library on disk (read-only).
"""
from __future__ import annotations

import csv
import hashlib
import os
from collections import defaultdict

from .models import META_KEYS

DONE = ("done", "already-done")

CATALOG_COLUMNS = (
    ["original_filename", "final_path", "original_path", "kind", "category", "instrument",
     "loop_type", "bpm", "key", "genre", "duration_s", "sample_rate", "channels", "bits",
     "pack", "manufacturer", "kit", "sha256", "tags", "size"] +
    [c for k in META_KEYS for c in (k + "_source", k + "_confidence")])
SOURCE_INDEX_COLUMNS = ["original_path", "origin", "status", "final_path", "canonical_path",
                        "verified", "action", "reason", "op_id"]
DUPLICATE_COLUMNS = ["group", "sha256", "size", "canonical_path", "member_original_path",
                     "member_final_path", "disposition", "op_id"]
ORG_COLUMNS = ["op_id", "action", "status", "source", "destination", "link_to", "timestamp",
               "result", "error", "reason", "flags", "kit"]


def _val(it, k):
    f = (it.get("meta") or {}).get(k) or {}
    return f.get("value") if isinstance(f, dict) else None


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return ("%.3f" % v).rstrip("0").rstrip(".")
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    return str(v)


def _index(items, kits, plan, results):
    ops = (plan or {}).get("ops") or []
    op_by_item = {o["item_id"]: o for o in ops}
    res = {}
    for r in results or []:
        res[r.get("op_id")] = r   # last record wins
    return op_by_item, res, {k["id"]: k for k in kits or []}


def _final_path(it, op, r):
    """(status, final_path, canonical_path) for one item."""
    if it.get("origin") == "library":
        return "already-in-library", it.get("abs_path"), None
    if op is None:
        return "not-planned", None, None
    a = op["action"]
    st = (r or {}).get("status")
    if a in ("move", "link"):
        if st in DONE:
            return ("moved" if a == "move" else "hard-linked"), op.get("dest"), op.get("link_to")
        if st is None:
            return "not-run", None, None
        return st, None, None
    if a == "keep":
        return ("kept-duplicate" if op.get("canonical") else "kept"), None, op.get("canonical")
    return ("held" if a == "hold" else "excluded"), None, None


def _exists_size(path, size):
    try:
        return os.path.getsize(path) == size
    except OSError:
        return False


def write_reports(catalog_dir, items, kits, plan, results) -> dict:
    os.makedirs(catalog_dir, exist_ok=True)
    op_by_item, res, kits_by_id = _index(items, kits, plan, results)
    items_sorted = sorted(items, key=lambda i: (i.get("origin") != "library",
                                                i.get("rel_path", "").casefold()))
    out = {}

    def w(name, cols, rows):
        p = os.path.join(catalog_dir, name)
        tmp = p + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(cols)
            for row in rows:
                wr.writerow([_fmt(row.get(c)) for c in cols])
        os.replace(tmp, p)
        out[name] = p

    # Sample_Catalog: every file that is in the library after the run
    rows = []
    for it in items_sorted:
        op = op_by_item.get(it["id"])
        status, final, _canon = _final_path(it, op, res.get(op["id"]) if op else None)
        if not final:
            continue
        a = it.get("audio") or {}
        kit = kits_by_id.get(it.get("kit_id"))
        row = {"original_filename": it.get("name"), "final_path": final,
               "original_path": it.get("abs_path"), "kind": it.get("kind"),
               "duration_s": a.get("duration_s"), "sample_rate": a.get("sample_rate"),
               "channels": a.get("channels"), "bits": a.get("bits"), "pack": it.get("pack"),
               "kit": kit.get("name") if kit else None, "sha256": it.get("sha256"),
               "size": it.get("size")}
        for k in META_KEYS:
            f = (it.get("meta") or {}).get(k) or {}
            row[k] = f.get("value")
            row[k + "_source"] = f.get("source")
            row[k + "_confidence"] = f.get("confidence")
        rows.append(row)
    w("Sample_Catalog.csv", CATALOG_COLUMNS, rows)

    # Source_Path_Index: every original path
    rows = []
    for it in items_sorted:
        op = op_by_item.get(it["id"])
        r = res.get(op["id"]) if op else None
        status, final, canon = _final_path(it, op, r)
        verified = ""
        if final:
            verified = "yes" if _exists_size(final, it.get("size") or 0) else "no"
        elif canon:
            verified = "yes" if _exists_size(canon, it.get("size") or 0) else "no"
        reason = (op or {}).get("reason") or ""
        if r and r.get("error"):
            reason = (reason + "; " if reason else "") + str(r["error"])
        rows.append({"original_path": it.get("abs_path"), "origin": it.get("origin"),
                     "status": status, "final_path": final, "canonical_path": canon,
                     "verified": verified, "action": (op or {}).get("action"),
                     "reason": reason, "op_id": (op or {}).get("id")})
    w("Source_Path_Index.csv", SOURCE_INDEX_COLUMNS, rows)

    # Duplicate_Report: every sha256 shared by more than one file
    groups = defaultdict(list)
    for it in items_sorted:
        if it.get("sha256"):
            groups[it["sha256"]].append(it)
    rows, gno = [], 0
    for sha in sorted(groups, key=lambda s: (groups[s][0].get("rel_path", "").casefold(), s)):
        mem = groups[sha]
        if len(mem) < 2:
            continue
        gno += 1
        canon_path = None
        for it in mem:
            op = op_by_item.get(it["id"])
            if it.get("origin") == "library":
                canon_path = canon_path or it.get("abs_path")
            elif op and op.get("canonical"):
                canon_path = canon_path or op["canonical"]
        if canon_path is None:
            for it in mem:
                op = op_by_item.get(it["id"])
                if op and op["action"] == "move":
                    canon_path = op.get("dest")
                    break
        for it in mem:
            op = op_by_item.get(it["id"])
            r = res.get(op["id"]) if op else None
            status, final, _c = _final_path(it, op, r)
            if it.get("origin") == "library":
                disp = "canonical (existing library file)" if it.get("abs_path") == canon_path \
                    else "existing library copy"
            elif op is None:
                disp = "not planned"
            elif op["action"] == "move":
                disp = "canonical (moved)" if status == "moved" else "canonical (%s)" % status
            elif op["action"] == "link":
                disp = "hard link to canonical" if status == "hard-linked" \
                    else "hard link (%s)" % status
            elif op["action"] == "keep":
                disp = "redundant copy left in place" if op.get("canonical") else "kept"
            else:
                disp = status
            rows.append({"group": gno, "sha256": sha, "size": it.get("size"),
                         "canonical_path": canon_path, "member_original_path": it.get("abs_path"),
                         "member_final_path": final, "disposition": disp,
                         "op_id": (op or {}).get("id")})
    w("Duplicate_Report.csv", DUPLICATE_COLUMNS, rows)

    # Organization_Report: each op and its outcome
    rows = []
    for op in (plan or {}).get("ops") or []:
        r = res.get(op["id"]) or {}
        kit = kits_by_id.get(op.get("kit_id"))
        rows.append({"op_id": op["id"], "action": op["action"],
                     "status": r.get("status") or "not-run", "source": op.get("source"),
                     "destination": op.get("dest"), "link_to": op.get("link_to"),
                     "timestamp": r.get("timestamp"),
                     "result": r.get("status") or "not-run", "error": r.get("error"),
                     "reason": op.get("reason"), "flags": op.get("flags") or [],
                     "kit": kit.get("name") if kit else None})
    w("Organization_Report.csv", ORG_COLUMNS, rows)
    return out


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _identity(path):
    try:
        st = os.stat(path)
        return "%d:%d" % (st.st_dev, st.st_ino)
    except OSError:
        return None


def verify_library(items, kits, plan, results, check_hash: bool = False) -> dict:
    op_by_item, res, kits_by_id = _index(items, kits, plan, results)
    ops = (plan or {}).get("ops") or []
    missing, size_bad, hash_bad, link_bad = [], [], [], []
    checked = 0
    final_paths = {}   # path -> sha256
    for it in items:
        if it.get("origin") == "library" and it.get("abs_path"):
            final_paths[it["abs_path"]] = it.get("sha256")
    for op in ops:
        r = res.get(op["id"])
        if op["action"] not in ("move", "link") or not r or r.get("status") not in DONE:
            continue
        checked += 1
        d = op.get("dest")
        if not d or not os.path.exists(d):
            missing.append(d)
            continue
        if not _exists_size(d, op.get("size") or 0):
            size_bad.append(d)
        if check_hash and op.get("sha256"):
            try:
                if _sha256(d) != op["sha256"]:
                    hash_bad.append(d)
            except OSError:
                hash_bad.append(d)
        if op["action"] == "link" and op.get("link_to"):
            if _identity(d) is None or _identity(d) != _identity(op["link_to"]):
                link_bad.append(d)
        final_paths[d] = op.get("sha256")

    kits_checked, kits_incomplete = 0, []
    for k in kits or []:
        member_ops = [op_by_item.get(m) for m in k.get("member_ids") or []]
        placed = [o for o in member_ops if o and o["action"] in ("move", "link")]
        if not placed:
            continue
        kits_checked += 1
        absent = []
        for o in placed:
            r = res.get(o["id"])
            if not r or r.get("status") not in DONE or not os.path.exists(o.get("dest") or ""):
                absent.append(o.get("dest") or o.get("source"))
        if absent:
            kits_incomplete.append({"kit_id": k["id"], "name": k.get("name"),
                                    "missing": absent[:20], "missing_count": len(absent)})

    hashes = {s for p, s in final_paths.items() if s and os.path.exists(p)}
    idents = {}
    for p in final_paths:
        i = _identity(p)
        if i:
            idents.setdefault(i, final_paths[p])
    physical = len(idents)
    extra_copies = physical - len({s for s in idents.values() if s})
    avoided = 0
    for op in ops:
        r = res.get(op["id"])
        if op["action"] == "keep" and op.get("canonical"):
            avoided += op.get("size") or 0
        elif op["action"] == "link" and r and r.get("status") in DONE:
            avoided += op.get("size") or 0
    ok = not (missing or size_bad or hash_bad or link_bad or kits_incomplete)
    return {
        "ok": ok,
        "checked": checked,
        "missing": missing, "size_mismatch": size_bad, "hash_mismatch": hash_bad,
        "hash_checked": bool(check_hash), "link_identity_mismatch": link_bad,
        "kits_checked": kits_checked, "kits_incomplete": kits_incomplete,
        "library_paths": len(final_paths),
        "unique_hashes": len(hashes),
        "physical_files": physical,
        "extra_physical_copies": max(0, extra_copies),
        "bytes_avoided": avoided,
        "bytes_freed": 0,
        "explanation": (
            "Moves relocate files within the same volume (hard link + unlink), so they "
            "neither use nor free space. Redundant exact duplicates were left in place "
            "(keep) or hard-linked into kits, so %d bytes of duplicate copies were not "
            "added to the library, but no disk space was freed because no source copies "
            "were deleted. physical_files counts distinct file identities among library "
            "paths; hard links share one identity. extra_physical_copies > 0 means the "
            "library holds more than one physical file for some content (e.g. pre-existing "
            "duplicates)." % avoided),
    }

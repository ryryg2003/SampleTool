"""Plan builder (read-only): turns enriched items + kits into a deterministic Plan.

Rules (see docs/CONTRACT.md):
- one physical copy per sha256 in the finished library
- canonical preference: existing library file > kit member > first by rel_path
- kit members duplicating a canonical elsewhere -> "link"; loose redundant -> "keep"
- name collisions (casefolded dest) with different content -> " [Pack]", " [Pack 2]"...
- unknown category / low confidence / hash error / excluded / held kit -> hold/exclude
- every incoming item gets exactly one op; library items get none
"""
from __future__ import annotations

import datetime
import os
import re
from collections import defaultdict

from .models import (CATEGORIES, KITS_DIR, PRESETS_MIDI, UNSORTED, op_id, plan_hash)

MIN_CATEGORY_CONFIDENCE = 0.5
ACTION_ORDER = {"move": 0, "link": 1, "keep": 2, "hold": 3, "exclude": 4}

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | \
            {"LPT%d" % i for i in range(1, 10)}


def sanitize(seg: str) -> str:
    """Make one path segment legal on Windows."""
    s = _ILLEGAL.sub("_", seg or "").strip()
    s = s.rstrip(" .")
    if not s:
        s = "_"
    base = s.split(".", 1)[0].upper().rstrip(" ")
    if base in _RESERVED:
        s = s.split(".", 1)
        s = s[0] + "_" + ("." + s[1] if len(s) > 1 else "")
    return s


def _join(dest_root: str, rel: str) -> str:
    return os.path.join(dest_root, *[sanitize(p) for p in rel.split("/") if p])


def _meta(it, k):
    f = (it.get("meta") or {}).get(k) or {}
    return f.get("value") if isinstance(f, dict) else None


def _meta_conf(it, k):
    f = (it.get("meta") or {}).get(k) or {}
    if not isinstance(f, dict):
        return None
    if f.get("source") == "manual":
        return 1.0
    c = f.get("confidence")
    return 1.0 if c is None else float(c)


def kit_dir_name(kit: dict) -> str:
    maker = (kit.get("manufacturer") or kit.get("pack") or "Unknown").strip()
    name = (kit.get("name") or "Kit").strip()
    if name.casefold().startswith(maker.casefold() + " - ") or \
            name.casefold() == maker.casefold():
        return sanitize(name)   # avoid "Maker - Maker - Kit" when the pack is the kit
    return sanitize("%s - %s" % (maker, name))


def _kit_inner(item: dict, kit: dict) -> str:
    root = kit.get("root_rel") or ""
    rel = item.get("rel_path", "")
    if root and rel.casefold().startswith(root.casefold() + "/"):
        return rel[len(root) + 1:]
    return item.get("name") or rel.rsplit("/", 1)[-1]


def _category(it):
    c = _meta(it, "category")
    if c in (None, UNSORTED, ""):
        return None
    return c


def propose_dest(item, kit, dest_root: str):
    """Fast, collision-unaware destination preview."""
    if item.get("origin") == "library":
        return item.get("abs_path")
    if item.get("excluded"):
        return None
    if kit is not None and not kit.get("excluded"):
        return _join(dest_root, KITS_DIR + "/" + kit_dir_name(kit) + "/" + _kit_inner(item, kit))
    kind = item.get("kind")
    if kind in ("midi", "preset"):
        return _join(dest_root, PRESETS_MIDI + "/" + item.get("name", ""))
    if kind == "audio":
        cat = _category(item)
        if cat in CATEGORIES:
            return _join(dest_root, cat + "/" + item.get("name", ""))
    return None


def _pkey(p: str) -> str:
    return os.path.normpath(p or "").casefold()


def _suffixed(path: str, label: str) -> str:
    d, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    return os.path.join(d, "%s [%s]%s" % (stem, label, ext))


def build_plan(items, kits, root: str, dest_root: str) -> dict:
    kits_by_id = {k["id"]: k for k in kits or []}
    incoming = sorted([i for i in items if i.get("origin", "incoming") == "incoming"],
                      key=lambda i: (i.get("rel_path", "").casefold(), i.get("id", "")))
    library = sorted([i for i in items if i.get("origin") == "library"],
                     key=lambda i: (i.get("rel_path", "").casefold(), i.get("id", "")))
    by_id = {i["id"]: i for i in items}

    # effective kit holds: declared hold, or a member that cannot be hashed
    kit_hold = {}
    for k in kits_by_id.values():
        if k.get("held"):
            kit_hold[k["id"]] = k.get("hold_reason") or "kit held for review"
    for it in incoming:
        k = it.get("kit_id")
        if k in kits_by_id and not it.get("sha256") and k not in kit_hold \
                and not it.get("excluded"):
            kit_hold[k] = "member %s could not be hashed (%s)" % (
                it.get("rel_path"), it.get("hash_error") or "no hash")

    # ---- 1. intent per incoming item
    intent = {}   # id -> (kind, reason, flags)
    for it in incoming:
        iid = it["id"]
        kit = kits_by_id.get(it.get("kit_id"))
        flags = []
        if it.get("near_dupes"):
            flags.append("near-duplicate")
        if it.get("excluded"):
            intent[iid] = ("exclude", "excluded by user", flags)
        elif kit is not None:
            flags.append("kit")
            if kit.get("excluded"):
                intent[iid] = ("exclude", "kit '%s' excluded by user" % kit.get("name"), flags)
            elif kit["id"] in kit_hold:
                intent[iid] = ("hold", "kit '%s' held: %s" % (kit.get("name"),
                                                             kit_hold[kit["id"]]), flags)
            else:
                intent[iid] = ("place", "kit member", flags)
        elif not it.get("sha256"):
            intent[iid] = ("hold", "hash error: %s" % (it.get("hash_error") or "no hash"),
                           flags + ["hash-error"])
        elif it.get("kind") == "audio":
            cat = _category(it)
            conf = _meta_conf(it, "category")
            if cat not in CATEGORIES:
                intent[iid] = ("hold_cat", "unknown category; needs review", flags + ["unsorted"])
            elif conf is not None and conf < MIN_CATEGORY_CONFIDENCE:
                intent[iid] = ("hold_cat", "low confidence category %s (%.2f); needs review"
                               % (cat, conf), flags + ["low-confidence"])
            else:
                intent[iid] = ("place", "category %s" % cat, flags)
        elif it.get("kind") in ("midi", "preset"):
            intent[iid] = ("place", "loose %s" % it.get("kind"), flags)
        else:
            intent[iid] = ("keep", "not a sample; left in place", flags)

    def base_dest(it):
        kit = kits_by_id.get(it.get("kit_id"))
        return propose_dest(it, kit, dest_root)

    # ---- 2. dedup by sha256
    groups = defaultdict(list)
    for it in library:
        if it.get("sha256"):
            groups[it["sha256"]].append(it)
    for it in incoming:
        if intent[it["id"]][0] in ("place", "hold_cat") and it.get("sha256"):
            groups[it["sha256"]].append(it)
    canonical_of = {}      # item id -> canonical item id
    dup_group_count = 0
    for sha in sorted(groups):
        mem = groups[sha]
        cands_lib = [m for m in mem if m.get("origin") == "library"]
        cands_kit = [m for m in mem if m.get("origin") != "library" and
                     intent[m["id"]][0] == "place" and m.get("kit_id") in kits_by_id]
        cands_loose = [m for m in mem if m.get("origin") != "library" and
                       intent[m["id"]][0] == "place" and m.get("kit_id") not in kits_by_id]
        canon = (cands_lib or cands_kit or cands_loose or [None])[0]
        if canon is None or len(mem) < 2:
            continue
        dup_group_count += 1
        for m in mem:
            if m is not canon and m.get("origin") != "library":
                canonical_of[m["id"]] = canon["id"]

    # ---- 3. destinations with collision handling
    taken = {}   # path key -> (sha256, item id)
    lib_ids = {it["id"] for it in library}
    for it in library:
        taken[_pkey(it.get("abs_path", ""))] = (it.get("sha256"), it["id"])
    dest_of, collided, already = {}, set(), {}

    # kit directory names must be unique across planned kits
    kit_dirs = {}
    used_dirs = set()
    for k in sorted(kits_by_id.values(), key=lambda k: (k.get("root_rel", "").casefold(), k["id"])):
        base = kit_dir_name(k)
        name, n = base, 1
        while name.casefold() in used_dirs:
            n += 1
            label = sanitize(k.get("pack") or "Kit") + ("" if n == 2 else " %d" % (n - 1))
            name = "%s [%s]" % (base, label)
        used_dirs.add(name.casefold())
        kit_dirs[k["id"]] = name

    def planned_dest(it):
        kit = kits_by_id.get(it.get("kit_id"))
        if kit is not None:
            return _join(dest_root, KITS_DIR + "/" + kit_dirs[kit["id"]] + "/" +
                         _kit_inner(it, kit))
        return base_dest(it)

    placing = [it for it in incoming if intent[it["id"]][0] == "place" and
               (it["id"] not in canonical_of or it.get("kit_id") in kits_by_id)]
    # kits first (they own their names), then loose files; each by path
    placing.sort(key=lambda i: (0 if i.get("kit_id") in kits_by_id else 1,
                                i.get("rel_path", "").casefold(), i["id"]))
    for it in placing:
        d = planned_dest(it)
        if d is None:
            intent[it["id"]] = ("hold_cat", "no destination could be determined",
                                intent[it["id"]][2])
            continue
        key = _pkey(d)
        if key in taken:
            sha, owner = taken[key]
            if owner in lib_ids and sha and sha == it.get("sha256"):
                # identical file already sits at this exact library path
                already[it["id"]] = owner
                continue
            pack = sanitize(it.get("pack") or "Collection")
            n = 1
            while True:
                cand = _suffixed(d, pack if n == 1 else "%s %d" % (pack, n))
                if _pkey(cand) not in taken:
                    break
                n += 1
            d = cand
            collided.add(it["id"])
        taken[_pkey(d)] = (it.get("sha256"), it["id"])
        dest_of[it["id"]] = d

    # ---- 4. ops
    move_op_of = {}
    ops = []
    for it in incoming:   # first: moves (needed for link dependencies)
        iid = it["id"]
        if intent[iid][0] == "place" and iid not in canonical_of and iid in dest_of:
            o = _op(it, "move", dest_of[iid], intent[iid][1], intent[iid][2])
            if iid in collided:
                o["flags"].append("name-collision")
            if any(c == iid for c in canonical_of.values()):
                o["flags"].append("duplicate")
                o["flags"].append("canonical")
            move_op_of[iid] = o
            ops.append(o)
    for it in incoming:
        iid = it["id"]
        kind, reason, flags = intent[iid]
        if iid in move_op_of:
            continue
        if iid in already:
            lib = by_id[already[iid]]
            o = _op(it, "keep", None, "identical file already in library at %s"
                    % lib.get("rel_path"), flags + ["duplicate"])
            o["canonical"] = lib.get("abs_path")
            ops.append(o)
            continue
        if iid in canonical_of and kind in ("place", "hold_cat"):
            canon = by_id[canonical_of[iid]]
            canon_path = canon.get("abs_path") if canon.get("origin") == "library" \
                else dest_of.get(canon["id"])
            if it.get("kit_id") in kits_by_id and kind == "place" and iid in dest_of:
                o = _op(it, "link", dest_of[iid],
                        "kit member duplicates %s; hard link keeps the kit complete"
                        % (canon.get("rel_path")), flags + ["duplicate"])
                o["link_to"] = canon_path
                o["canonical"] = canon_path
                dep = move_op_of.get(canon["id"])
                o["depends_on"] = dep["id"] if dep else None
                if iid in collided:
                    o["flags"].append("name-collision")
            else:
                o = _op(it, "keep", None, "exact duplicate of %s; left in place"
                        % canon.get("rel_path"), flags + ["duplicate"])
                o["canonical"] = canon_path
            ops.append(o)
            continue
        if kind in ("hold", "hold_cat"):
            ops.append(_op(it, "hold", None, reason, flags))
        elif kind == "exclude":
            ops.append(_op(it, "exclude", None, reason, flags))
        elif kind == "keep":
            ops.append(_op(it, "keep", None, reason, flags))
        else:   # place without destination (should not happen)
            ops.append(_op(it, "hold", None, "no destination could be determined", flags))

    ops.sort(key=lambda o: (ACTION_ORDER[o["action"]],
                            (o.get("dest") or o.get("source") or "").casefold(), o["id"]))

    # ---- 5. summary
    by_action = defaultdict(int)
    for o in ops:
        by_action[o["action"]] += 1
    avoided = sum(o.get("size") or 0 for o in ops
                  if o["action"] == "link" or (o["action"] == "keep" and o.get("canonical")))
    summary = {
        "total_files": len(incoming),
        "total_bytes": sum(i.get("size") or 0 for i in incoming),
        "audio_files": sum(1 for i in incoming if i.get("kind") == "audio"),
        "packs": len({i.get("pack") for i in incoming if i.get("pack")}),
        "kits": len(kits_by_id),
        "duplicate_groups": dup_group_count,
        "duplicate_bytes_avoided": avoided,
        "unknown_formats": sum(1 for i in incoming if i.get("kind") == "other" or
                               (i.get("kind") == "audio" and not i.get("audio"))),
        "collisions": len(collided),
        "by_action": dict(sorted(by_action.items())),
        "held": by_action.get("hold", 0),
    }
    return {"plan_hash": plan_hash(ops),
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "root": root, "dest_root": dest_root, "ops": ops, "summary": summary}


def _op(it, action, dest, reason, flags):
    return {"id": op_id(action, it["id"], dest), "action": action, "item_id": it["id"],
            "source": it.get("abs_path"), "dest": dest, "link_to": None,
            "depends_on": None, "canonical": None, "sha256": it.get("sha256"),
            "size": it.get("size") or 0, "mtime_ns": it.get("mtime_ns") or 0,
            "kit_id": it.get("kit_id"), "category": _category(it) if it.get("kind") == "audio"
            else (PRESETS_MIDI if it.get("kind") in ("midi", "preset") else None),
            "reason": reason, "flags": list(dict.fromkeys(flags))}

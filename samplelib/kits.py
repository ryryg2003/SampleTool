"""Construction kit / trackout detection (read-only).

A kit is a directory subtree (root_rel) and ALL incoming files under it.
Detection walks each pack top-down; the outermost qualifying folder wins and
kits never nest. A pack folder itself only becomes a kit when the whole pack
looks like one single kit.
"""
from __future__ import annotations

import gzip
import posixpath
import re
import zlib
from collections import Counter, defaultdict

from . import classify
from .models import field
from .models import kit_id as make_kit_id

NAME_RE = re.compile(
    r"(?:^| )(?:construction kits?|construction|constructions|stems?|trackouts?|track outs?"
    r"|song starters?|songstarters?|kits?)(?= |$)")
NOT_KIT_RE = re.compile(r"(?:^| )(?:drum|drums|sound|sounds|sample|samples|preset|presets|"
                        r"midi|one shot|one shots|oneshot|loop|loops|fx|vocal|percussion|"
                        r"808)\s+kits?(?= |$)|(?:^| )drumkits?(?= |$)")
MAX_ALS_XML = 64 * 1024 * 1024
MAX_KIT_AUDIO_FOR_PACK = 400


def _bpm(it):
    m = (it.get("meta") or {}).get("bpm") or {}
    return m.get("value") if isinstance(m, dict) else None


def _key(it):
    m = (it.get("meta") or {}).get("key") or {}
    return m.get("value") if isinstance(m, dict) else None


def _inst(it):
    m = (it.get("meta") or {}).get("instrument") or {}
    return m.get("value") if isinstance(m, dict) else None


def _loop(it):
    m = (it.get("meta") or {}).get("loop_type") or {}
    return m.get("value") if isinstance(m, dict) else None


def _consensus(values, min_share=0.6):
    vals = [v for v in values if v is not None]
    if not vals:
        return None, 0, 0
    if isinstance(vals[0], float) or isinstance(vals[0], int):
        c = Counter(round(float(v), 1) for v in vals)
    else:
        c = Counter(vals)
    top, n = c.most_common(1)[0]
    if n / len(vals) >= min_share:
        return top, n, len(vals)
    return None, n, len(vals)


def _name_match(dirname: str):
    w = classify.norm_words(dirname)
    if NOT_KIT_RE.search(w):
        w2 = NOT_KIT_RE.sub(" ", w)
        return bool(NAME_RE.search(w2))
    return bool(NAME_RE.search(w))


def _prefix(stem_words: list) -> str:
    out = []
    for w in stem_words:
        if w.isdigit() or classify.parse_key(w) or w == "bpm":
            break
        if classify.find_instrument(w)[0] or classify.find_loop_word(w):
            break
        out.append(w)
    return " ".join(out)


def _prefix_group(audio_items):
    """Return (prefix, members) when one shared-prefix stem group qualifies:
    >=3 files, >=2 different instruments, consistent BPM/key with at least one
    known, covering >=60% of the given audio. Else (None, [])."""
    groups = defaultdict(list)
    for it in audio_items:
        p = _prefix(classify.norm_words(classify.stem_of(it.get("name", ""))).split())
        if len(p) >= 3:
            groups[p].append(it)
    good = []
    for p, mem in groups.items():
        if len(mem) < 3:
            continue
        insts = {_inst(m) for m in mem if _inst(m)}
        if len(insts) < 2:
            continue
        bpms = {round(float(b), 1) for b in (_bpm(m) for m in mem) if b is not None}
        keys = {k for k in (_key(m) for m in mem) if k}
        if len(bpms) > 1 or len(keys) > 1 or (not bpms and not keys):
            continue
        good.append((p, mem))
    if len(good) != 1:
        return None, []
    p, mem = good[0]
    if len(mem) < 0.6 * len(audio_items):
        return None, []
    return p, mem


class _Tree:
    def __init__(self, items):
        self.files = defaultdict(list)      # dir -> direct items
        self.children = defaultdict(set)    # dir -> child dirs
        self.sub = defaultdict(list)        # dir -> all items in subtree
        for it in items:
            parts = it["rel_path"].split("/")
            d = "/".join(parts[:-1])
            self.files[d].append(it)
            for i in range(len(parts) - 1):
                anc = "/".join(parts[:i + 1])
                self.sub[anc].append(it)
                if i > 0:
                    self.children["/".join(parts[:i])].add(anc)
        for v in self.sub.values():
            v.sort(key=lambda x: x["rel_path"].casefold())


def _audio(items):
    return [i for i in items if i.get("kind") == "audio"]


def _support(sub, dirname):
    """Evidence that a name-matched folder really is a kit (not a one-shot set)."""
    reasons = []
    aud = _audio(sub)
    if len(aud) < 2:
        return []
    if any(i.get("kind") == "project" for i in sub):
        reasons.append("contains a DAW project file")
    if any(i.get("kind") == "midi" for i in sub):
        reasons.append("contains MIDI files")
    bpm, n, tot = _consensus([_bpm(i) for i in aud])
    if bpm is not None and n >= 2:
        reasons.append("members share %g BPM (%d/%d)" % (bpm, n, tot))
    key, n, tot = _consensus([_key(i) for i in aud])
    if key is not None and n >= 2:
        reasons.append("members share key %s (%d/%d)" % (key, n, tot))
    loops = sum(1 for i in aud if _loop(i) == "loop")
    if loops > len(aud) / 2:
        reasons.append("mostly loops/stems (%d/%d)" % (loops, len(aud)))
    return reasons


def _kitlike(tree, d):
    sub = tree.sub.get(d, [])
    if len(_audio(sub)) < 2:
        return False
    name = d.rsplit("/", 1)[-1]
    if _name_match(name) and _support(sub, name):
        return True
    if _prefix_group(_audio(sub))[0]:
        return True
    if _distinctive_name(name):
        return True
    gen = classify.norm_words(name) in classify.GENERIC_FOLDERS
    bpm, n, _t = _consensus([_bpm(i) for i in _audio(sub)], 0.8)
    return bool(not gen and bpm is not None and n >= 2 and
                any(i.get("kind") in ("midi", "project") for i in sub))


def _generic_child(parent, c):
    pw = set(classify.norm_words(parent.rsplit("/", 1)[-1]).split())
    w = [x for x in classify.norm_words(c.rsplit("/", 1)[-1]).split()
         if x not in pw and not x.isdigit()]
    return not w or " ".join(w) in classify.GENERIC_FOLDERS or \
        all(x in classify.GENERIC_FOLDERS for x in w)


def _distinctive_name(name):
    """Folder name carries a tempo/key plus a real title, e.g. 'Sunset 90 F#m'."""
    strong_bpm = any(st in ("explicit", "keyed") for _v, st in classify.bpm_candidates(name))
    if not strong_bpm and classify.parse_key(name) is None:
        return False
    words = []
    for w in classify.norm_words(name).split():
        if w.isdigit() or w in ("bpm", "key", "min", "maj", "minor", "major", "m") or \
                len(w) <= 1 or classify.parse_key(w.capitalize()) or \
                w in classify.GENERIC_FOLDERS:
            continue
        words.append(w)
    return bool(words)


def _is_container(tree, d):
    """A folder whose child folders are each a kit (e.g. 'Construction Kits')."""
    name = d.rsplit("/", 1)[-1]
    if _name_match(name) and len(_audio(tree.files.get(d, []))) < 2:
        kids = [c for c in tree.children.get(d, ())
                if len(_audio(tree.sub.get(c, []))) >= 2 and not _generic_child(d, c)]
        # One distinctively named kit folder inside "Construction Kits" is enough:
        # many packs ship a single kit in such a container. Generic children
        # ("Stems", "MIDI") never count, so a kit-named folder holding only
        # those is still the kit itself.
        if len(kids) >= 1:
            return True
    kids = [c for c in sorted(tree.children.get(d, ())) if _kitlike(tree, c)]
    if len(kids) < 2:
        return False
    bpms, keys = set(), set()
    for c in kids:
        aud = _audio(tree.sub[c])
        b = _consensus([_bpm(i) for i in aud])[0]
        k = _consensus([_key(i) for i in aud])[0]
        if b is not None:
            bpms.add(b)
        if k is not None:
            keys.add(k)
    names_generic = all(_generic_child(d, c) for c in kids)
    return len(bpms) > 1 or len(keys) > 1 or not names_generic


def _qualify(tree, d, depth, container=None):
    """Return (evidence, confidence) when d is a kit root, else None."""
    sub = tree.sub.get(d, [])
    aud = _audio(sub)
    if len(aud) < 2 or _is_container(tree, d):
        return None
    name = d.rsplit("/", 1)[-1]
    evidence, conf = [], 0.0
    if _name_match(name):
        sup = _support(sub, name)
        if sup:
            evidence.append("folder name '%s' indicates a kit/stems/trackout" % name)
            evidence.extend(sup)
            conf = 0.8
    p, mem = _prefix_group(aud)
    if p:
        # the kit root is the folder that actually holds the group, not an ancestor
        dirs = {m["rel_path"].rsplit("/", 1)[0] for m in mem}
        common = posixpath.commonpath(list(dirs)) if dirs else d
        if common.casefold() != d.casefold():
            p, mem = None, []
        elif depth == 1 and len(mem) < 0.9 * len(aud):
            p, mem = None, []
    if p:
        insts = sorted({_inst(m) for m in mem if _inst(m)})
        evidence.append("%d audio files share prefix '%s' with matching BPM/key across "
                        "%d parts (%s)" % (len(mem), p, len(insts), ", ".join(insts)))
        conf = 0.9 if conf else 0.7
    if not evidence and container and not _generic_child(container, d):
        evidence.append("subfolder of kit container '%s'" % container.rsplit("/", 1)[-1])
        conf = 0.75
    if not evidence and _distinctive_name(name) and depth > 1:
        sup = _support(sub, name)
        insts = {_inst(i) for i in aud if _inst(i)}
        if sup or len(insts) >= 2:
            evidence.append("folder name '%s' carries tempo/key of a song kit" % name)
            evidence.extend(sup)
            conf = 0.65
    if not evidence:
        return None
    if depth == 1:
        # the pack itself: only when it is one single kit
        if len(aud) > MAX_KIT_AUDIO_FOR_PACK:
            return None
        bpm, n, tot = _consensus([_bpm(i) for i in aud], 0.7)
        if not p and bpm is None:
            return None
        evidence.append("whole pack folder treated as one kit")
        conf = min(conf, 0.6)
    return evidence, conf


def _als_refs(path):
    """Yield (relative_path or None, absolute_path or None) per FileRef; (refs, truncated)."""
    with open(path, "rb") as f:
        head = f.read(2)
    opener = gzip.open if head == b"\x1f\x8b" else open
    data = b""
    truncated = False
    with opener(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            data += chunk
            if len(data) > MAX_ALS_XML:
                truncated = True
                break
    text = data.decode("utf-8", "replace")
    refs = []
    for m in re.finditer(r"<FileRef>(.*?)</FileRef>", text, re.S):
        blk = m.group(1)
        rel = re.search(r'<RelativePath\s+Value="([^"]*)"', blk)
        ab = re.search(r'<Path\s+Value="([^"]*)"', blk)
        rel_v = _unxml(rel.group(1)) if rel else None
        ab_v = _unxml(ab.group(1)) if ab else None
        if not rel_v and not ab_v:
            # Live 9 style: <RelativePathElement Dir="x"/>... + <Name Value="f.wav"/>
            dirs = re.findall(r'<RelativePathElement[^>]*Dir="([^"]*)"', blk)
            nm = re.search(r'<Name\s+Value="([^"]*)"', blk)
            if nm and nm.group(1):
                rel_v = "/".join([_unxml(x) for x in dirs] + [_unxml(nm.group(1))])
        if rel_v or ab_v:
            refs.append((rel_v, ab_v))
    return refs, truncated


def _unxml(s):
    return (s.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&amp;", "&"))


def _norm_rel(p):
    p = posixpath.normpath(p.replace("\\", "/"))
    return "" if p == "." else p


def _check_project(proj, kit_root, by_rel_cf, root_abs_cf):
    ext = proj.get("ext", "")
    if ext != ".als":
        return {"check": "project-sample-refs:" + proj["name"], "performed": False,
                "result": "not-checked",
                "detail": "%s references were not parsed; the kit's relative layout is "
                          "preserved so relative sample paths keep resolving" % ext}, None
    try:
        refs, truncated = _als_refs(proj["abs_path"])
    except (OSError, EOFError, zlib.error, gzip.BadGzipFile) as e:
        return {"check": "project-sample-refs:" + proj["name"], "performed": False,
                "result": "warning", "detail": "could not read Ableton set: %s" % e}, None
    proj_dir = proj["rel_path"].rsplit("/", 1)[0]
    kr = kit_root.casefold() + "/"
    inside = outside = missing = 0
    out_examples = []
    for rel_v, ab_v in refs:
        target = None
        if rel_v:
            cand = _norm_rel(proj_dir + "/" + rel_v)
            if not cand.startswith("..") and cand.casefold() in by_rel_cf:
                target = cand
        if target is None and ab_v and root_abs_cf:
            a = ab_v.replace("\\", "/")
            if a.casefold().startswith(root_abs_cf + "/"):
                cand = _norm_rel(a[len(root_abs_cf) + 1:])
                if cand.casefold() in by_rel_cf:
                    target = cand
        if target is None:
            # library content (Core Library / Packs / user library) or already missing
            missing += 1
            continue
        if (target.casefold() + "/").startswith(kr) or target.casefold().startswith(kr):
            inside += 1
        else:
            outside += 1
            if len(out_examples) < 3:
                out_examples.append(target)
    detail = "%d sample refs inside kit, %d outside kit, %d not in the collection " \
             "(library/missing; unaffected by the move)" % (inside, outside, missing)
    if truncated:
        detail += "; project XML exceeded read cap, partially checked"
    hold = None
    if outside:
        detail += "; outside: " + ", ".join(out_examples)
        hold = "project %s references files outside the kit folder (%s)" % (
            proj["name"], ", ".join(out_examples))
    res = "warning" if (outside or truncated) else "ok"
    return {"check": "project-sample-refs:" + proj["name"], "performed": True,
            "result": res, "detail": detail}, hold


def _root_abs(items):
    for it in items:
        a, r = it.get("abs_path", ""), it.get("rel_path", "")
        a2 = a.replace("\\", "/")
        if r and a2.casefold().endswith("/" + r.casefold()):
            return a2[:len(a2) - len(r) - 1].casefold()
    return None


def detect_kits(items: list) -> list:
    incoming = [it for it in items if it.get("origin", "incoming") == "incoming"]
    for it in incoming:
        it["kit_id"] = None
    tree = _Tree(incoming)
    by_rel_cf = {it["rel_path"].casefold(): it for it in incoming}
    root_abs_cf = _root_abs(incoming)
    kits = []

    def walk(d, depth, container=None):
        q = _qualify(tree, d, depth, container)
        if q:
            kits.append(_make_kit(d, q, tree.sub[d], by_rel_cf, root_abs_cf))
            return
        cont = d if _is_container(tree, d) else None
        for c in sorted(tree.children.get(d, ()), key=str.casefold):
            walk(c, depth + 1, cont)

    packs = sorted({it["rel_path"].split("/")[0] for it in incoming
                    if "/" in it["rel_path"]}, key=str.casefold)
    for p in packs:
        walk(p, 1)
    kits.sort(key=lambda k: k["root_rel"].casefold())
    return kits


def _make_kit(d, q, members, by_rel_cf, root_abs_cf):
    evidence, conf = q
    name = d.rsplit("/", 1)[-1]
    pack = d.split("/")[0]
    makers = [((m.get("meta") or {}).get("manufacturer") or {}).get("value") for m in members]
    maker, _n, _t = _consensus(makers, 0.5)
    if maker is None:
        maker, _c = classify.manufacturer_from_pack(pack)
    aud = _audio(members)
    bpm, _n, _t = _consensus([_bpm(i) for i in aud])
    key, _n, _t = _consensus([_key(i) for i in aud])
    kid = make_kit_id(d, name)
    checks = []
    midi = [m for m in members if m.get("kind") == "midi"]
    checks.append({"check": "midi-present", "performed": True, "result": "ok",
                   "detail": "%d MIDI file(s) travel with the kit" % len(midi)})
    holds = []
    for proj in members:
        if proj.get("kind") == "project":
            rc, hold = _check_project(proj, d, by_rel_cf, root_abs_cf)
            checks.append(rc)
            if hold:
                holds.append(hold)
    for m in members:
        m["kit_id"] = kid
        if m.get("kind") == "audio":
            _inherit(m, bpm, key)
    return {"id": kid, "name": name, "manufacturer": maker, "pack": pack, "root_rel": d,
            "member_ids": [m["id"] for m in members],
            "bpm": float(bpm) if bpm is not None else None, "key": key,
            "evidence": evidence, "confidence": round(conf, 2),
            "held": bool(holds), "hold_reason": "; ".join(holds) if holds else None,
            "excluded": False, "ref_checks": checks, "manual": False}


def _inherit(m, bpm, key):
    """Stems inherit the kit's tempo/key; in a tempo'd kit they default to loops."""
    meta = m.get("meta")
    if not isinstance(meta, dict) or not meta:
        return
    f = meta.get("bpm") or {}
    if bpm is not None and f.get("value") is None and f.get("source") != "manual":
        meta["bpm"] = field(float(bpm), "inference", 0.6)
    f = meta.get("key") or {}
    if key is not None and f.get("value") is None and f.get("source") != "manual":
        meta["key"] = field(key, "inference", 0.6)
    lt = meta.get("loop_type") or {}
    if bpm is not None and lt.get("source") in (None, "inference") and \
            lt.get("value") != "loop" and _inst(m) not in ("808",):
        meta["loop_type"] = field("loop", "inference", 0.5)
        inst = _inst(m)
        cat = meta.get("category") or {}
        group = classify.INSTRUMENT_GROUP.get(inst)
        if group and cat.get("source") != "manual":
            ic = (meta.get("instrument") or {}).get("confidence")
            conf = min(0.5, 1.0 if ic is None else ic)
            meta["category"] = field(classify._category("loop", group), "inference", conf)

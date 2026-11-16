"""Mix matching: which samples go well together (tempo, key, bars, downbeat).

Pure functions over a Store's items. Read-only.
"""
from __future__ import annotations

from . import analysis

ROLE_BY_CATEGORY = {
    "One-Shots/Drums": "drums", "Loops/Drum Loops": "drums",
    "One-Shots/Bass & 808s": "bass", "Loops/Bass Loops": "bass",
    "One-Shots/Instruments": "melody", "Loops/Melody Loops": "melody",
    "One-Shots/FX & Vocals": "vocal-fx", "Loops/Vocal & FX Loops": "vocal-fx",
}
ROLES = ("drums", "bass", "melody", "vocal-fx")
_SCALE = {False: (0, 2, 4, 5, 7, 9, 11), True: (0, 2, 3, 5, 7, 8, 10)}
W_TEMPO, W_KEY, W_BARS, W_GRID = 0.45, 0.4, 0.08, 0.07


def _mv(it, k):
    f = (it.get("meta") or {}).get(k)
    return f.get("value") if isinstance(f, dict) else None


def role_of(it):
    return ROLE_BY_CATEGORY.get(_mv(it, "category"))


def _is_loop(it):
    return _mv(it, "loop_type") != "one-shot"


def _tempo_val(a, b):
    if not a or not b:
        return 0.4
    d = min(abs(a - b) / a, abs(a - 2 * b) / a, abs(a - 0.5 * b) / a)
    if abs(a - b) / a <= 0.02:
        return 1.0
    if d <= 0.02:
        return 0.8
    return 0.5 if d <= 0.06 else 0.0


def tempo_compat(a, b):
    """-> (score, reason) for two tempos in BPM."""
    if not a or not b:
        return 0.4, "tempo unknown"
    for m, label in ((1, "same tempo"), (2, "half-time"), (0.5, "double-time")):
        if abs(a - b * m) / a <= 0.02:
            if m == 1:
                return 1.0, "%s (%g BPM)" % (label, round(b))
            return 0.8, "%s (%g vs %g BPM)" % (label, round(a), round(b))
    if min(abs(a - b * m) / a for m in (0.5, 1, 2)) <= 0.06:
        return 0.5, "close tempo (%g vs %g BPM), needs a small stretch" % (round(a), round(b))
    return 0.0, "tempo clash (%g vs %g BPM)" % (round(a), round(b))


def note_in_key(root, key):
    """A one-shot's root note against a scale key -> (score, reason)."""
    r, k = analysis.norm_key(root), analysis.norm_key(key)
    if not r or not k:
        return 0.4, "key unknown"
    r = r.rstrip("m")
    minor = k.endswith("m")
    tonic = analysis.NOTES.index(k[:-1] if minor else k)
    iv = (analysis.NOTES.index(r) - tonic) % 12
    if iv == 0:
        return 1.0, "root %s is the tonic of %s" % (r, k)
    if iv in _SCALE[minor]:
        return 0.8, "%s is in %s" % (r, k)
    return 0.1, "%s is outside %s" % (r, k)


class F:
    """Matching features of one item, read once per store generation."""
    __slots__ = ("it", "role", "loop", "bpm", "key", "bars", "grid", "kit", "pack")

    def __init__(self, it):
        an = it.get("analysis") or {}
        self.it, self.role, self.loop = it, role_of(it), _is_loop(it)
        self.bpm, self.key = _mv(it, "bpm"), analysis.norm_key(_mv(it, "key"))
        self.bars, self.grid = an.get("bars"), an.get("on_grid")
        self.kit, self.pack = it.get("kit_id"), it.get("pack")


def features(store):
    """-> {id: F} for suggestible items, rebuilt when the store changes."""
    tag = (store.gen, len(store.items), id(store.items))
    cached = getattr(store, "_match_feats", None)
    if cached and cached[0] == tag:
        return cached[1]
    feats = {it["id"]: F(it) for it in store.items
             if it.get("kind") == "audio" and not it.get("excluded") and role_of(it)}
    store._match_feats = (tag, feats)
    return feats


def _key_score(a, b):
    if not a.key or not b.key:
        return 0.4, "key unknown"
    oa, ob = not a.loop, not b.loop
    if oa and ob:
        same = a.key.rstrip("m") == b.key.rstrip("m")
        return (1.0, "same root note") if same else (0.4, "different root notes")
    if oa or ob:
        return note_in_key(a.key if oa else b.key, b.key if oa else a.key)
    return analysis.key_compat(a.key, b.key)


def _fast_score(a, b):
    """score_pair's number without building reasons (hot loop)."""
    total = weight = 0.0
    if a.loop and b.loop:
        total += W_TEMPO * _tempo_val(a.bpm, b.bpm)
        weight += W_TEMPO
    if a.role != "drums" and b.role != "drums":
        if a.loop and b.loop and a.key and b.key:
            k = analysis.key_compat(a.key, b.key)[0]
        else:
            k = _key_score(a, b)[0]
        total += W_KEY * k
        weight += W_KEY
    if a.bars and b.bars:
        q = max(a.bars, b.bars) / min(a.bars, b.bars)
        total += W_BARS * (1.0 if q.is_integer() and int(q) & (int(q) - 1) == 0 else 0.3)
        weight += W_BARS
    if a.grid is not None and b.grid is not None:
        total += W_GRID * (1.0 if a.grid and b.grid else 0.4)
        weight += W_GRID
    score = total / weight if weight else 0.3
    if a.kit and a.kit == b.kit:
        score += 0.05
    elif a.pack and a.pack == b.pack:
        score += 0.03
    return min(1.0, score)


def score_pair(a, b):
    """-> (score 0..1, reasons) for how well F b goes with F a."""
    total = weight = 0.0
    reasons = []
    if a.loop and b.loop:
        s, r = tempo_compat(a.bpm, b.bpm)
        total += W_TEMPO * s
        weight += W_TEMPO
        reasons.append(r)
    if a.role != "drums" and b.role != "drums":
        s, r = _key_score(a, b)
        total += W_KEY * s
        weight += W_KEY
        reasons.append(r)
    if a.bars and b.bars:
        hi, lo = max(a.bars, b.bars), min(a.bars, b.bars)
        q = hi / lo
        fits = q.is_integer() and int(q) & (int(q) - 1) == 0
        total += W_BARS * (1.0 if fits else 0.3)
        weight += W_BARS
        if fits:
            reasons.append("both %g bars" % lo if lo == hi else "%g and %g bars line up" % (lo, hi))
    if a.grid is not None and b.grid is not None:
        both = a.grid and b.grid
        total += W_GRID * (1.0 if both else 0.4)
        weight += W_GRID
        if both:
            reasons.append("both start on the downbeat")
    score = total / weight if weight else 0.3
    if a.kit and a.kit == b.kit:
        score += 0.05
        reasons.append("same kit")
    elif a.pack and a.pack == b.pack:
        score += 0.03
        reasons.append("same pack")
    return min(1.0, score), reasons


def ranked(store, src, role=None, min_score=0.3, feats=None, limit=None):
    """-> [(score, reasons, item)] best first; never src, its duplicates or excluded items."""
    feats = feats if feats is not None else features(store)
    a = feats.get(src["id"]) or F(src)
    near = set(src.get("near_dupes") or [])
    sha = src.get("sha256")
    out = []
    for fid, b in feats.items():
        if b is a or fid == src["id"] or fid in near or (role and b.role != role):
            continue
        if sha and b.it.get("sha256") == sha:
            continue
        s = _fast_score(a, b)
        if not role and a.role and b.role == a.role:
            s *= 0.85  # without a filter, favor parts that complement the source
        if s >= min_score:
            out.append((round(s, 3), b))
    out.sort(key=lambda t: (-t[0], t[1].it["name"].casefold()))
    if limit is not None:
        out = out[:limit]
    return [(s, score_pair(a, b)[1], b.it) for s, b in out]


def matches(store, item_id, role=None, limit=30):
    with store.lock:
        src = store.by_id[item_id]
        return [{"row": store.row(it), "score": s, "reasons": r}
                for s, r, it in ranked(store, src, role, limit=max(1, min(200, limit)))]


# ------------------------------------------------------------------ Beat Starter
SLOTS = ("drums", "bass", "melody")


def _target(key, bpm):
    t = F.__new__(F)
    t.it, t.role, t.loop = None, "target", True
    t.bpm, t.key = bpm, analysis.norm_key(key)
    t.bars = t.grid = t.kit = t.pack = None
    return t


def starter(store, key=None, bpm=None, locked=None, seed=None, avoid=None):
    """Pick a drum loop, bass and melody that fit together.

    Locked slots ({slot: item id}) are kept and set key/tempo when none are
    given. avoid ({slot: id}) skips that pick when another fits (reroll).
    Same arguments and seed -> same result."""
    import random
    if seed is None:
        seed = random.randrange(1, 1_000_000)
    rng = random.Random(seed)
    locked = {k: v for k, v in (locked or {}).items() if k in SLOTS and v}
    with store.lock:
        feats = features(store)
        pools = {r: sorted((f for f in feats.values() if f.loop and f.role == r),
                           key=lambda f: f.it["id"]) for r in SLOTS}
        fixed = {slot: feats.get(i) or F(store.by_id[i]) for slot, i in locked.items()}
        key = analysis.norm_key(key) if key else None
        if not key:
            for slot in ("melody", "bass"):
                if slot in fixed and fixed[slot].key:
                    key = fixed[slot].key
                    break
        if not bpm:
            bpm = next((f.bpm for f in fixed.values() if f.bpm), None)
        if not key or not bpm:
            seeds = [f for f in pools["melody"] + pools["bass"] if f.key and f.bpm
                     and (not key or analysis.key_compat(f.key, key)[0] >= 0.9)
                     and (not bpm or _tempo_val(bpm, f.bpm) >= 0.8)]
            if seeds:
                pick = rng.choice(seeds)
                key, bpm = key or pick.key, bpm or pick.bpm
                if pick.role not in fixed:
                    fixed[pick.role] = pick
        target = _target(key, bpm)
        slots, missing, options = {}, {}, {}
        avoid = avoid or {}
        for slot in SLOTS:
            if slot in fixed:
                f = fixed[slot]
            else:
                scored = sorted(((_fast_score(target, f), f) for f in pools[slot]),
                                key=lambda t: (-t[0], t[1].it["id"]))
                good = [f for s, f in scored if s >= 0.75][:8]
                options[slot] = len(good)
                fresh = [f for f in good if f.it["id"] != avoid.get(slot)]
                f = rng.choice(fresh or good) if good else None
            if f is None:
                slots[slot] = None
                missing[slot] = "No %s loops fit %s at %s BPM" % (
                    slot, key or "any key", ("%g" % bpm) if bpm else "any tempo")
                continue
            s, reasons = score_pair(target, f)
            slots[slot] = {"row": store.row(f.it), "score": round(s, 3), "reasons": reasons,
                           "locked": slot in locked}
        return {"key": key, "bpm": bpm, "seed": seed, "slots": slots, "missing": missing,
                "options": options}

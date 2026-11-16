"""Metadata enrichment and classification (read-only).

enrich(items, progress, cancel) fills, in place, for every item:
  audio, tags_raw, meta (every models.META_KEYS entry as a Field), warnings,
  near_dupes.
Evidence order: tag > filename > folder > inference/analysis. Unknown values
stay None; uncertain categories get low confidence so the planner holds them.
"""
from __future__ import annotations

import functools
import re
from collections import Counter, defaultdict

from . import analysis, audio_meta
from .models import CATEGORIES, META_KEYS, field

# ------------------------------------------------------------------ text utils
_SEP_RE = re.compile(r"[\s_\-.,()\[\]{}+&@!~=]+")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_DIGIT_RE = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


def stem_of(name: str) -> str:
    i = name.rfind(".")
    return name[:i] if i > 0 else name


@functools.lru_cache(maxsize=65536)
def norm_words(text: str) -> str:
    """'OpenHat_01-Dark' -> 'open hat 01 dark'."""
    if not text:
        return ""
    t = _CAMEL_RE.sub(" ", text)
    t = _DIGIT_RE.sub(" ", t)
    return " ".join(w for w in _SEP_RE.split(t.lower()) if w)


_PHRASE_RE: dict = {}


def _has_phrase(words: str, phrase: str) -> int:
    """Return index (>=0) of phrase as whole words in words, else -1."""
    if phrase not in words:
        return -1
    rx = _PHRASE_RE.get(phrase)
    if rx is None:
        rx = _PHRASE_RE[phrase] = re.compile(r"(?:^| )" + re.escape(phrase) + r"(?= |$)")
    m = rx.search(words)
    return m.start() if m else -1


# ------------------------------------------------------------------ BPM
_BPM_EXPLICIT = re.compile(
    r"(?<![\d.])(\d{2,3}(?:\.\d{1,2})?)\s*[-_ ]?\s*bpm(?![a-z])"
    r"|(?<![a-z])bpm\s*[-_: ]?\s*(\d{2,3}(?:\.\d{1,2})?)(?![\d])", re.I)
# bare number delimited by separators; excludes leading zeros ("01"), numbers
# glued to letters ("24bit", "44k", "v2"), %, decimals, and 4+ digit numbers.
_BPM_BARE = re.compile(r"(?:^|(?<=[\s_\-\[\]()]))([1-9]\d{1,2})(?=$|[\s_\-\[\]()])")
_BARE_BLOCKERS = {"vol", "volume", "pt", "part", "no", "num", "number", "take", "kit",
                  "track", "pack", "chapter", "ep", "lp", "version", "ver", "v",
                  "x", "bar", "bars", "step", "steps", "beat"}
BPM_MIN, BPM_MAX = 50.0, 220.0


def bpm_candidates(text: str) -> list:
    """[(bpm, strength)]; strength 'explicit' (has 'bpm'), 'keyed' (next to a key) or 'bare'."""
    if not text:
        return []
    out = []
    for m in _BPM_EXPLICIT.finditer(text):
        v = float(m.group(1) or m.group(2))
        if BPM_MIN <= v <= BPM_MAX:
            out.append((v, "explicit"))
    if out:
        return out
    for m in _BPM_BARE.finditer(text):
        v = float(m.group(1))
        if not (BPM_MIN <= v <= BPM_MAX):
            continue
        before = norm_words(text[:m.start()]).split()
        if before and before[-1] in _BARE_BLOCKERS:
            continue
        after = text[m.end():].lstrip(" _-[]()")
        prev = text[:m.start()].rstrip(" _-[]()")
        keyed = any(c[1] == "strong" and c[2] == 0 for c in _key_candidates(after)) or \
            any(c[1] == "strong" and c[3] == len(prev) for c in _key_candidates(prev))
        out.append((v, "keyed" if keyed else "bare"))
    return out


def parse_bpm(text: str) -> float | None:
    """BPM from free text; None when absent or ambiguous."""
    c = bpm_candidates(text)
    if not c:
        return None
    vals = {v for v, _s in c}
    if len(vals) > 1:
        return None
    return c[0][0]


# ------------------------------------------------------------------ Key
_ACC = r"(?P<acc>#|♯|b|♭|[-_ ]?sharp|[-_ ]?flat)?"
_MODE = r"(?P<mode>[-_ ]?(?:minor|major|min|maj)|m)?"
_KEY_RE = re.compile(r"(?:^|(?<=[\s_\-\[\]().,]))(?P<note>[A-Ga-g])" + _ACC + _MODE +
                     r"(?=$|[\s_\-\[\]().,])", re.I)
_KEY_WORD_BEFORE = {"key", "in", "scale"}


def _key_candidates(text: str) -> list:
    """[(key, strength)] strength 'strong' (accidental or mode) or 'weak' (bare letter)."""
    out = []
    if not text:
        return out
    for m in _KEY_RE.finditer(text):
        note, acc, mode = m.group("note"), m.group("acc") or "", m.group("mode") or ""
        if mode == "M" or acc == "B":
            continue  # "FM", "AB": not keys
        acc_c = acc.strip("-_ ").lower()
        mode_c = mode.strip("-_ ").lower()
        if note.islower() and not (acc_c in ("#", "♯", "sharp") or mode_c in
                                   ("min", "minor", "maj", "major")):
            continue
        if note.islower() and acc_c == "b":
            continue
        a = "#" if acc_c in ("#", "♯", "sharp") else ("b" if acc_c in ("b", "♭", "flat") else "")
        minor = mode_c in ("m", "min", "minor")
        key = note.upper() + a + ("m" if minor else "")
        if a or mode_c:
            out.append((key, "strong", m.start(), m.end()))
        else:
            out.append((key, "weak", m.start(), m.end()))
    return out


def parse_key(text: str) -> str | None:
    """Musical key from free text: 'F#m', 'C', 'Bb', 'C#m'... or None."""
    cands = _key_candidates(text)
    strong = [c for c in cands if c[1] == "strong"]
    if strong:
        keys = {c[0] for c in strong}
        return strong[0][0] if len(keys) == 1 else None
    # bare natural letter only next to a "key" word or a BPM-like number
    for key, _s, a, b in cands:
        before = norm_words(text[:a]).split()
        after = norm_words(text[b:]).split()
        near_num = (before and before[-1].isdigit() and before[-1][0] != "0"
                    and BPM_MIN <= float(before[-1]) <= BPM_MAX) or \
                   (after and after[0].isdigit() and after[0][0] != "0"
                    and BPM_MIN <= float(after[0]) <= BPM_MAX) or \
                   (after and after[0] == "bpm") or (before and before[-1] == "bpm")
        key_word = (before and before[-1] in _KEY_WORD_BEFORE) or before[-2:] == ["key", "of"]
        if key_word or near_num:
            return key
    return None


# ------------------------------------------------------------------ vocab
# phrase -> (instrument, group, priority). Groups: drums, bass, melodic, fxvox
_V = []


def _add(group, prio, inst, *phrases):
    for p in phrases:
        _V.append((p, inst, group, prio + (5 if " " in p else 0)))


_add("drums", 90, "open hat", "open hat", "open hihat", "open hi hat", "ohh", "openhat")
_add("drums", 90, "closed hat", "closed hat", "closed hihat", "closed hi hat", "chh")
_add("drums", 88, "hi-hat", "hi hat", "hihat", "hihats", "hat", "hats", "hh")
_add("drums", 90, "kick", "kick", "kicks", "kik", "bd", "bass drum", "bassdrum")
_add("drums", 90, "snare", "snare", "snares", "snr")
_add("drums", 90, "clap", "clap", "claps")
_add("drums", 89, "rim", "rim", "rims", "rimshot", "rimshots")
_add("drums", 88, "perc", "perc", "percs", "percussion", "shaker", "shakers", "conga",
     "congas", "bongo", "bongos", "tamb", "tambourine", "cowbell", "clave", "block")
_add("drums", 89, "tom", "tom", "toms")
_add("drums", 89, "cymbal", "cymbal", "cymbals")
_add("drums", 89, "crash", "crash", "crashes")
_add("drums", 89, "ride", "ride", "rides")
_add("drums", 89, "snap", "snap", "snaps")
_add("bass", 80, "808", "808", "808s")
_add("bass", 78, "sub", "sub", "subs", "sub bass")
_add("bass", 78, "reese", "reese")
_add("bass", 55, "bass", "bass", "basses", "bassline", "basslines")
_add("melodic", 50, "synth", "synth", "synths")
_add("melodic", 60, "lead", "lead", "leads")
_add("melodic", 60, "pad", "pad", "pads")
_add("melodic", 60, "pluck", "pluck", "plucks")
_add("melodic", 55, "keys", "keys")
_add("melodic", 62, "piano", "piano", "pianos")
_add("melodic", 62, "rhodes", "rhodes", "epiano", "e piano", "electric piano")
_add("melodic", 62, "guitar", "guitar", "guitars", "gtr")
_add("melodic", 62, "strings", "strings", "string", "violin", "cello", "orchestra")
_add("melodic", 62, "brass", "brass", "horn", "horns", "trumpet", "sax", "saxophone")
_add("melodic", 62, "bell", "bell", "bells")
_add("melodic", 58, "chord", "chord", "chords")
_add("melodic", 58, "arp", "arp", "arps")
_add("melodic", 50, "melody", "melody", "melodies", "mel", "melodic", "music", "instrument")
_add("melodic", 62, "flute", "flute", "flutes")
_add("melodic", 62, "organ", "organ", "organs")
_add("fxvox", 70, "vocal", "vox", "vocal", "vocals", "vocal chop", "vocal chops", "chop",
     "chops", "voice", "acapella", "acappella")
_add("fxvox", 72, "chant", "chant", "chants")
_add("fxvox", 72, "adlib", "adlib", "adlibs", "ad lib", "ad libs")
_add("fxvox", 75, "riser", "riser", "risers", "uplifter", "uplifters")
_add("fxvox", 75, "impact", "impact", "impacts", "boom", "booms")
_add("fxvox", 75, "sweep", "sweep", "sweeps")
_add("fxvox", 75, "transition", "transition", "transitions", "downlifter", "downlifters",
     "whoosh")
_add("fxvox", 75, "foley", "foley")
_add("fxvox", 74, "texture", "texture", "textures", "atmos", "atmosphere", "ambience",
     "noise", "drone")
_add("fxvox", 45, "fx", "fx", "sfx", "effect", "effects")
_add("drums", 85, "drum loop", "drum loop", "drum loops", "drumloop", "drumloops")
_add("drums", 85, "top loop", "top loop", "top loops", "tops")
_add("drums", 84, "fill", "fill", "fills")
_add("drums", 82, "break", "break", "breaks", "breakbeat", "breakbeats")
_add("drums", 40, "drums", "drums", "drum", "beat", "beats", "groove", "grooves")

DRUM_HITS = {"open hat", "closed hat", "hi-hat", "kick", "snare", "clap", "rim", "perc",
             "tom", "cymbal", "crash", "ride", "snap"}
LOOP_IMPLYING = {"drum loop", "top loop", "fill", "break"}
ONESHOT_TYPICAL = {"open hat", "closed hat", "hi-hat", "kick", "snare", "clap", "rim", "perc",
                   "tom", "cymbal", "crash", "ride", "snap", "impact", "riser", "sweep",
                   "transition", "foley", "fx"}
INSTRUMENT_GROUP = {}
for _p, _i, _g, _pr in _V:
    INSTRUMENT_GROUP[_i] = _g

LOOP_WORDS = ("loop", "loops", "loopz")
ONESHOT_WORDS = ("one shot", "one shots", "oneshot", "oneshots", "one-shot", "hit", "hits",
                 "shot", "shots", "single", "singles")

GENRES = [
    ("drum and bass", "DnB"), ("drum & bass", "DnB"), ("drum n bass", "DnB"), ("dnb", "DnB"),
    ("d&b", "DnB"), ("tech house", "Tech House"), ("deep house", "Deep House"),
    ("future house", "Future House"), ("progressive house", "Progressive House"),
    ("future bass", "Future Bass"), ("uk garage", "UK Garage"), ("jersey club", "Jersey Club"),
    ("boom bap", "Boom Bap"), ("hip hop", "Hip Hop"), ("hiphop", "Hip Hop"),
    ("lo fi", "Lo-Fi"), ("lofi", "Lo-Fi"), ("r&b", "R&B"), ("rnb", "R&B"), ("r & b", "R&B"),
    ("trap", "Trap"), ("drill", "Drill"), ("house", "House"), ("techno", "Techno"),
    ("dubstep", "Dubstep"), ("edm", "EDM"), ("pop", "Pop"), ("afrobeats", "Afrobeats"),
    ("afrobeat", "Afrobeats"), ("afro", "Afrobeats"), ("amapiano", "Amapiano"),
    ("reggaeton", "Reggaeton"), ("dancehall", "Dancehall"), ("phonk", "Phonk"),
    ("ambient", "Ambient"), ("cinematic", "Cinematic"), ("garage", "Garage"),
    ("jungle", "Jungle"), ("trance", "Trance"), ("rage", "Rage"), ("pluggnb", "Pluggnb"),
    ("plugg", "Plugg"), ("synthwave", "Synthwave"), ("hyperpop", "Hyperpop"),
    ("jazz", "Jazz"), ("soul", "Soul"), ("funk", "Funk"), ("latin", "Latin"),
    ("rock", "Rock"), ("disco", "Disco"), ("grime", "Grime"), ("hardstyle", "Hardstyle"),
]
DESCRIPTORS = {"dark", "bright", "hard", "soft", "punchy", "distorted", "clean", "wet",
               "dry", "analog", "vintage", "reverse", "reversed", "short", "long", "tight",
               "deep", "warm", "airy", "dirty", "heavy", "acoustic", "electric", "processed",
               "saturated", "stereo", "mono", "wide", "crisp", "fat", "layered", "chopped",
               "pitched", "vinyl", "tape", "gritty", "metallic", "organic", "lofi", "glitch",
               "sliced", "filtered", "ambient", "live", "muted", "sustain", "staccato",
               "legato", "dusty", "sidechain", "808", "909", "707", "trap", "drill"}

TAG_BPM = ("TBPM", "BPM", "TEMPO", "TXXX:BPM", "TXXX:TEMPO")
TAG_KEY = ("TKEY", "KEY", "INITIALKEY", "TXXX:KEY", "TXXX:INITIALKEY")
TAG_GENRE = ("TCON", "GENRE", "IGNR")
TAG_ARTIST = ("IART", "TPE1", "ARTIST", "AUTH", "TPE2", "ALBUMARTIST")

GENERIC_FOLDERS = {"drums", "melody", "melodies", "bass", "stems", "stem", "midi", "loops",
                   "one shots", "oneshots", "fx", "vocals", "vox", "instruments", "music",
                   "wav", "wavs", "audio", "samples", "sounds", "mp3", "flac", "aiff",
                   "presets", "files", "full", "project", "projects", "bounces",
                   "drum stems", "one shot", "keys", "synths", "guitars", "chords",
                   "percussion", "perc", "tops", "fills", "music loops", "drum loops"}


def _tag(tags: dict, names) -> str | None:
    up = {str(k).upper(): v for k, v in tags.items()}
    for n in names:
        v = up.get(n)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def find_instrument(words: str):
    """Best (instrument, group, priority, all_hits) in normalized words."""
    hits = []
    for phrase, inst, group, prio in _V:
        i = _has_phrase(words, phrase)
        if i >= 0:
            hits.append((prio, -i, inst, group))
    if not hits:
        return None, None, 0, []
    hits.sort(reverse=True)
    # "808 Kick"/"808 Clap": the drum word wins over 808 (handled by priority)
    best = hits[0]
    return best[2], best[3], best[0], [h[2] for h in hits]


_GENRES_N = [(norm_words(p), n) for p, n in GENRES]
_ONESHOT_N = [norm_words(w) for w in ONESHOT_WORDS]


def find_loop_word(words: str):
    for w in LOOP_WORDS:
        if _has_phrase(words, w) >= 0:
            return "loop"
    for w in _ONESHOT_N:
        if _has_phrase(words, w) >= 0:
            return "one-shot"
    return None


def find_genre(words: str):
    for phrase, name in _GENRES_N:
        if _has_phrase(words, phrase) >= 0:
            return name
    return None


def manufacturer_from_pack(pack: str | None):
    """('Maker', confidence) from 'Maker - Pack' or 'Maker_Pack', else (None, None)."""
    if not pack:
        return None, None
    if " - " in pack:
        maker = pack.split(" - ", 1)[0].strip()
        if maker and len(maker.split()) <= 5:
            return maker, 0.7
    if "_" in pack and " " not in pack.split("_", 1)[0]:
        maker = pack.split("_", 1)[0].strip()
        nw = norm_words(maker)
        if maker and len(maker) >= 2 and not maker.isdigit() and find_genre(nw) is None \
                and find_instrument(nw)[0] is None and find_loop_word(nw) is None:
            return maker, 0.5
    return None, None


def _context(item):
    parts = (item.get("rel_path") or item.get("name") or "").split("/")
    name = item.get("name") or parts[-1]
    if item.get("origin") == "library":
        pack = None
        folders = parts[1:-1]
    else:
        pack = item.get("pack")
        folders = parts[1:-1] if pack and len(parts) > 1 and parts[0] == pack else parts[:-1]
    return pack, folders, stem_of(name)


# ------------------------------------------------------------------ enrich one
def classify_item(item: dict) -> dict:
    """Compute meta Fields for one item (uses item['audio'] and item['tags_raw'])."""
    kind = item.get("kind")
    tags = item.get("tags_raw") or {}
    audio = item.get("audio") or {}
    pack, folders, stem = _context(item)
    fname_words = norm_words(stem)
    folder_words = [norm_words(f) for f in folders]  # outermost -> innermost
    pack_words = norm_words(pack or "")
    meta = {k: field() for k in META_KEYS}
    meta["tags"] = field([], None, None)
    acid = tags.get("acid") if isinstance(tags.get("acid"), dict) else None

    # manufacturer (all kinds)
    art = _tag(tags, TAG_ARTIST)
    if art:
        meta["manufacturer"] = field(art, "tag", None)
    else:
        mk, conf = manufacturer_from_pack(pack)
        if mk:
            meta["manufacturer"] = field(mk, "folder", conf)

    # genre (all kinds)
    g = _tag(tags, TAG_GENRE)
    if g:
        g = re.sub(r"^\(\d+\)\s*", "", g).strip()
        if g and not g.isdigit():
            meta["genre"] = field(find_genre(norm_words(g)) or g, "tag", None)
    if meta["genre"]["value"] is None:
        gf = find_genre(fname_words)
        if gf:
            meta["genre"] = field(gf, "filename", 0.6)
        else:
            for fw in reversed(folder_words):
                gf = find_genre(fw)
                if gf:
                    meta["genre"] = field(gf, "folder", 0.6)
                    break
            if meta["genre"]["value"] is None:
                gf = find_genre(pack_words)
                if gf:
                    meta["genre"] = field(gf, "folder", 0.5)

    if kind in ("midi", "preset"):
        meta["category"] = field("Presets & MIDI", "inference", 0.95)
        _fill_bpm_key(meta, tags, acid, stem, folders, None)
        inst, _g, _p, _hits = find_instrument(fname_words)
        if inst:
            meta["instrument"] = field(inst, "filename", 0.7)
        meta["tags"] = _descriptors(fname_words, folder_words, meta)
        return meta
    if kind != "audio":
        meta["tags"] = _descriptors(fname_words, folder_words, meta)
        return meta
    lib_cat = _library_category(item)

    # instrument: filename > folder (innermost first)
    inst, group, _prio, hits = find_instrument(fname_words)
    if inst:
        meta["instrument"] = field(inst, "filename", 0.8)
    else:
        for fw in reversed(folder_words):
            inst, group, _prio, hits = find_instrument(fw)
            if inst:
                meta["instrument"] = field(inst, "folder", 0.65)
                break
    # filename hit confirmed by a folder of the same family -> more confident
    if inst and meta["instrument"]["source"] == "filename":
        if any(find_instrument(fw)[1] == group for fw in folder_words):
            meta["instrument"]["confidence"] = 0.85

    # loop type
    lt = None
    if acid is not None:
        if acid.get("one_shot"):
            lt = field("one-shot", "tag", None)
        elif (acid.get("beats") or 0) > 0:
            lt = field("loop", "tag", None)
    if lt is None:
        w = find_loop_word(fname_words)
        if w:
            lt = field(w, "filename", 0.85)
    if lt is None:
        for fw in reversed(folder_words):
            w = find_loop_word(fw)
            if w:
                lt = field(w, "folder", 0.75)
                break
    dur = audio.get("duration_s")
    name_bpm = _filename_has_bpm(stem)
    if lt is None and inst:
        src = meta["instrument"]["source"]
        if inst in LOOP_IMPLYING:
            lt = field("loop", src, 0.7)
        elif inst == "808":
            # a long sustained 808 is still a one-shot when its name/folder says 808
            lt = field("one-shot", src, 0.7)
        elif name_bpm:
            # "Vocal Hook 128 BPM Gm": a tempo in the name means a loop
            lt = field("loop", "inference", 0.65)
        elif inst in ONESHOT_TYPICAL:
            lt = field("one-shot", "inference", 0.65)
        elif dur is not None and dur <= 2.5:
            lt = field("one-shot", "inference", 0.6)
    if lt is None and dur is not None:
        if dur < 1.5:
            lt = field("one-shot", "inference", 0.4)
        elif dur >= 4.0 and _filename_has_bpm(stem):
            lt = field("loop", "inference", 0.45)
    an = item.get("analysis") or {}
    if lt is None and an.get("bars"):
        lt = field("loop", "analysis", 0.6)
    if lt is not None:
        meta["loop_type"] = lt

    _fill_bpm_key(meta, tags, acid, stem, folders, meta["loop_type"]["value"])
    # measured audio is the last evidence source
    if meta["bpm"]["value"] is None and an.get("bpm"):
        meta["bpm"] = field(an["bpm"], "analysis", an.get("bpm_conf"))
    if meta["key"]["value"] is None and an.get("key"):
        meta["key"] = field(an["key"], "analysis", an.get("key_conf"))

    # BPM evidence implies loop when still unknown (weak)
    if meta["loop_type"]["value"] is None and meta["bpm"]["value"] is not None:
        meta["loop_type"] = field("loop", "inference", 0.45)

    # category
    lt_v = meta["loop_type"]["value"]
    if lib_cat:
        meta["category"] = field(lib_cat, "folder", 0.9)
    elif lt_v and group:
        cat = _category(lt_v, group)
        c1 = meta["loop_type"]["confidence"]
        c2 = meta["instrument"]["confidence"]
        conf = min(1.0 if c1 is None else c1, 1.0 if c2 is None else c2)
        meta["category"] = field(cat, "inference", round(conf, 3))
    meta["tags"] = _descriptors(fname_words, folder_words, meta, hits)
    return meta


def _library_category(item):
    if item.get("origin") != "library":
        return None
    parts = (item.get("rel_path") or "").split("/")
    for i in range(len(parts) - 1):
        cand = "/".join(parts[i:i + 2])
        if cand in CATEGORIES:
            return cand
    return None


def _filename_has_bpm(stem: str) -> bool:
    return any(s in ("explicit", "keyed") for _v, s in bpm_candidates(stem))


def _category(loop_type: str, group: str) -> str | None:
    if loop_type == "one-shot":
        m = {"drums": "One-Shots/Drums", "bass": "One-Shots/Bass & 808s",
             "melodic": "One-Shots/Instruments", "fxvox": "One-Shots/FX & Vocals"}
    else:
        m = {"drums": "Loops/Drum Loops", "bass": "Loops/Bass Loops",
             "melodic": "Loops/Melody Loops", "fxvox": "Loops/Vocal & FX Loops"}
    c = m.get(group)
    return c if c in CATEGORIES else None


def _fill_bpm_key(meta, tags, acid, stem, folders, loop_type):
    t = _tag(tags, TAG_BPM)
    bpm = None
    if t:
        try:
            v = float(t.replace(",", ".").split()[0])
            if 20 <= v <= 400:
                bpm = field(round(v, 2), "tag", None)
        except (ValueError, IndexError):
            pass
    if bpm is None and acid and not acid.get("one_shot") and (acid.get("beats") or 0) > 0:
        tempo = acid.get("tempo")
        if tempo and 20 <= tempo <= 400:
            bpm = field(round(float(tempo), 2), "tag", None)
    if bpm is None:
        c = bpm_candidates(stem)
        vals = {v for v, _s in c}
        if len(vals) == 1:
            strength = c[0][1]
            if strength == "explicit":
                bpm = field(c[0][0], "filename", 0.95)
            elif strength == "keyed":
                # "Melody_140_Am": a number next to a key is a tempo
                bpm = field(c[0][0], "filename", 0.85)
            elif loop_type != "one-shot":
                # bare numbers on one-shots are usually sample numbering
                bpm = field(c[0][0], "filename", 0.6)
    if bpm is None:
        for f in reversed(folders):
            c = [x for x in bpm_candidates(f) if x[1] in ("explicit", "keyed")]
            if len({v for v, _s in c}) == 1:
                bpm = field(c[0][0], "folder", 0.7)
                break
    if bpm is not None:
        meta["bpm"] = bpm

    key = None
    t = _tag(tags, TAG_KEY)
    if t:
        k = parse_key(t) or parse_key(t.replace("min", "m"))
        if k:
            key = field(k, "tag", None)
    if key is None:
        ik = _tag(tags, ("IKEY",))
        if ik and _KEY_RE.fullmatch(ik.strip()) and parse_key(ik.strip()):
            key = field(parse_key(ik.strip()), "tag", 0.8)
    if key is None:
        k = parse_key(stem)
        if k:
            key = field(k, "filename", 0.85)
    if key is None:
        for f in reversed(folders):
            k = parse_key(f)
            if k:
                key = field(k, "folder", 0.6)
                break
    if key is None and acid and acid.get("root_note_set") and acid.get("root_note_name"):
        key = field(acid["root_note_name"], "tag", 0.5)
    if key is not None:
        meta["key"] = key


def _descriptors(fname_words, folder_words, meta, hits=()):
    out = []
    src = None
    chosen = meta["instrument"]["value"]
    for h in hits:
        if h != chosen and h not in out:
            out.append(h)
            src = src or "filename"
    for w in fname_words.split():
        if w in DESCRIPTORS and w not in out and w != chosen:
            out.append(w)
            src = src or "filename"
    for fw in folder_words:
        for w in fw.split():
            if w in DESCRIPTORS and w not in out and w != chosen:
                out.append(w)
                src = src or "folder"
    return field(out, src, 0.6 if out else None)


# ------------------------------------------------------------------ near dupes
_COPY_RE = re.compile(r"(\s*\(\d+\)|\s*copy(\s*\d+)?|\s*-\s*copy)$", re.I)


def _norm_stem(name: str) -> str:
    s = _COPY_RE.sub("", stem_of(name))
    return " ".join(norm_words(s).split())


def _pack_free_stem(item) -> str:
    words = _norm_stem(item.get("name", "")).split()
    drop = set(norm_words(item.get("pack") or "").split())
    m = (item.get("meta") or {}).get("manufacturer") or {}
    if isinstance(m, dict) and m.get("value"):
        drop |= set(norm_words(str(m["value"])).split())
    kept = [w for w in words if w not in drop]
    return " ".join(kept) or " ".join(words)


def find_near_dupes(items: list) -> None:
    audio = [it for it in items if it.get("kind") == "audio" and it.get("sha256")]
    for it in items:
        it["near_dupes"] = []
    pairs = set()

    def link(a, b):
        if a["sha256"] != b["sha256"]:
            pairs.add((a["id"], b["id"]) if a["id"] < b["id"] else (b["id"], a["id"]))

    by_stem = defaultdict(list)
    for it in audio:
        d = (it.get("audio") or {}).get("duration_s")
        if d is not None:
            by_stem[_norm_stem(it.get("name", ""))].append(it)
    for grp in by_stem.values():
        grp.sort(key=lambda x: (x["audio"]["duration_s"], x["id"]))
        for i, a in enumerate(grp):
            for b in grp[i + 1:]:
                if b["audio"]["duration_s"] - a["audio"]["duration_s"] > 0.005:
                    break
                link(a, b)
    by_pf = defaultdict(list)
    for it in audio:
        by_pf[_pack_free_stem(it)].append(it)
    for grp in by_pf.values():
        grp.sort(key=lambda x: (x.get("size") or 0, x["id"]))
        for i, a in enumerate(grp):
            sa = a.get("size") or 0
            for b in grp[i + 1:]:
                sb = b.get("size") or 0
                if sb - sa > max(sa, 1) * 0.005:
                    break
                link(a, b)
    byid = {it["id"]: it for it in items}
    for a, b in sorted(pairs):
        byid[a]["near_dupes"].append(b)
        byid[b]["near_dupes"].append(a)


# ------------------------------------------------------------------ enrich
def _prog(progress, **kw):
    if progress:
        try:
            progress(dict(job="scan", running=True, **kw))
        except Exception:  # progress reporting must never break enrichment
            pass


def _reclassify(it: dict) -> None:
    old = it.get("meta") or {}
    meta = classify_item(it)
    for k in META_KEYS:  # never clobber manual edits
        if isinstance(old.get(k), dict) and old[k].get("source") == "manual":
            meta[k] = old[k]
    it["meta"] = meta


def _analysis_want(it: dict):
    """Which measurements an item needs, or None to skip it."""
    if it.get("kind") != "audio" or (it.get("ext") or "").lower() not in analysis.DECODABLE:
        return None
    meta = it.get("meta") or {}
    lt = (meta.get("loop_type") or {}).get("value")
    cat = (meta.get("category") or {}).get("value") or ""
    if lt == "one-shot":
        if cat in ("One-Shots/Bass & 808s", "One-Shots/Instruments"):
            return frozenset({"root"})
        return None
    if cat == "Loops/Drum Loops":
        return frozenset({"tempo"})
    return frozenset({"tempo", "key"})


_SAYS = {"tag": "Tags say", "filename": "Filename says", "folder": "Folder says",
         "manual": "Your edit says"}


def _mismatch_warnings(it: dict, want) -> list:
    an, meta, out = it.get("analysis") or {}, it.get("meta") or {}, []
    b = meta.get("bpm") or {}
    if an.get("bpm") and b.get("value") and b.get("source") != "analysis":
        if min(abs(an["bpm"] * m - b["value"]) / b["value"] for m in (0.5, 1, 2)) > 0.03:
            out.append("%s %g BPM, audio measures %g" % (_SAYS.get(b["source"], "Metadata says"),
                                                       b["value"], round(an["bpm"])))
    k = meta.get("key") or {}
    if an.get("key") and k.get("value") and k.get("source") != "analysis":
        if "root" in want:
            a, e = analysis.norm_key(an["key"]), analysis.norm_key(k["value"])
            bad = bool(e) and a != e.rstrip("m")
        else:
            bad = analysis.key_compat(an["key"], k["value"])[0] < 0.9
        if bad:
            out.append("%s %s, audio measures %s" % (_SAYS.get(k["source"], "Metadata says"),
                                                   k["value"], an["key"]))
    return out


def analyze_items(items: list, cache: dict, progress=None, cancel=None,
                  analyzer=None) -> dict:
    """Measure tempo/key/bars for decodable audio, re-classify those items.

    cache maps sha256|version|want -> result. Returns the cache restricted to
    the entries used by this run (stale entries drop out)."""
    analyzer = analyzer or analysis.analyze
    todo = [(it, w) for it in items for w in [_analysis_want(it)] if w]
    used: dict = {}
    for n, (it, want) in enumerate(todo):
        if cancel is not None and cancel.is_set():
            break
        if n % 10 == 0:
            _prog(progress, phase="analyzing", done=n, total=len(todo),
                  current=it.get("rel_path", ""))
        ck = "%s|%d|%s" % (it.get("sha256"), analysis.ANALYSIS_VERSION, ",".join(sorted(want))) \
            if it.get("sha256") else None
        if ck and (ck in used or ck in cache):
            res, warns = used.get(ck) or cache[ck], []
        else:
            try:
                res, warns = analyzer(it.get("abs_path", ""), it.get("ext", ""), want)
            except Exception as e:  # analysis must never break a scan
                res, warns = None, ["audio analysis failed: %s" % e]
        if ck and res is not None:
            used[ck] = res
        if res is None:
            it["warnings"] = list(it.get("warnings") or []) + \
                [w for w in warns if w not in (it.get("warnings") or [])]
            continue
        it["analysis"] = res
        _reclassify(it)
        ws = list(it.get("warnings") or [])
        it["warnings"] = ws + [w for w in _mismatch_warnings(it, want) if w not in ws]
    _prog(progress, phase="analyzing", done=len(todo), total=len(todo))
    return used


def enrich(items: list, progress=None, cancel=None) -> None:
    total = len(items)
    audio_items = [it for it in items if it.get("kind") == "audio"]
    for n, it in enumerate(audio_items):
        if cancel is not None and cancel.is_set():
            return
        if n % 25 == 0:
            _prog(progress, phase="metadata", done=n, total=len(audio_items),
                  current=it.get("rel_path", ""))
        info, tags, warns = audio_meta.read_audio(it.get("abs_path", ""), it.get("ext", ""))
        it["audio"] = info
        merged = dict(it.get("tags_raw") or {})
        merged.update(tags)
        it["tags_raw"] = merged
        w = list(it.get("warnings") or [])
        for x in warns:
            if x not in w:
                w.append(x)
        it["warnings"] = w
    _prog(progress, phase="metadata", done=len(audio_items), total=len(audio_items))
    for n, it in enumerate(items):
        if cancel is not None and cancel.is_set():
            return
        if n % 200 == 0:
            _prog(progress, phase="classifying", done=n, total=total,
                  current=it.get("rel_path", ""))
        _reclassify(it)
        it.setdefault("warnings", [])
    find_near_dupes(items)
    _prog(progress, phase="classifying", done=total, total=total)

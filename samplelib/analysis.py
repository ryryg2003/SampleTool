"""Read-only audio analysis: tempo, bars, downbeat and key (stdlib only).

analyze(path, ext, want) -> (result | None, warnings)

Decodes uncompressed WAV and AIFF/AIFC, at most the first MAX_SECONDS, mixed
to mono and decimated to about 11 kHz. Tempo comes from an onset-strength
envelope scored by autocorrelation, with a bar-length prior for loops; key
comes from a Goertzel chromagram correlated with Krumhansl key profiles.
Failures return (None, [warning]); silence returns a result full of None.
"""
from __future__ import annotations

import array
import functools
import math
import operator
import struct
import sys

_mul = operator.mul

ANALYSIS_VERSION = 2
MAX_SECONDS = 30.0
DECODABLE = {".wav", ".aif", ".aiff", ".aifc"}
TARGET_FS = 11025
HOP = 256
BPM_LO, BPM_HI = 60.0, 200.0

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLATS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#", "Cb": "B", "Fb": "E"}
_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


# ------------------------------------------------------------------ decoding
def _ext80(b: bytes) -> float:
    exp, mant = struct.unpack(">HQ", b[:10])
    if exp == 0 and mant == 0:
        return 0.0
    return mant * 2.0 ** ((exp & 0x7FFF) - 16383 - 63)


def _to_mono(data: bytes, ch: int, bits: int, enc: str, big: bool, fs_in: int):
    """PCM bytes -> decimated mono floats in -1..1. enc: 'int', 'uint8', 'float'."""
    if enc == "float":
        a = array.array("f" if bits == 32 else "d")
        scale = 1.0
    elif bits == 8:
        a = array.array("B" if enc == "uint8" else "b")
        scale = 1 / 128
    elif bits == 16:
        a = array.array("h")
        scale = 1 / 32768
    elif bits == 24:
        n = len(data) // 3
        out = bytearray(4 * n)
        order = (2, 1, 0) if big else (0, 1, 2)
        for j, o in enumerate(order):
            out[1 + j::4] = data[o:3 * n:3]  # bytes slicing; a strided memoryview is ~4x slower
        data, big = bytes(out), False
        a = array.array("i")
        scale = 1 / 2147483648
    elif bits == 32:
        a = array.array("i")
        scale = 1 / 2147483648
    else:
        return None
    usable = len(data) - len(data) % (a.itemsize * ch)
    a.frombytes(data[:usable])
    if big != (sys.byteorder == "big") and a.itemsize > 1:
        a.byteswap()
    if enc == "uint8":
        off = 128 * ch
    else:
        off = 0
    d = max(1, round(fs_in / TARGET_FS))
    step = d * ch
    k = scale / step
    n = len(a) // step
    # Mono mix and decimation in one: output i is the mean of the step raw samples
    # a[i*step : (i+1)*step] (every channel of d frames). Zipping the step strided
    # slices runs at C speed instead of one Python-level sum() per output sample.
    acc = list(map(sum, zip(*[a[j:n * step:step] for j in range(step)])))
    base = off * d
    if base:
        x = [(v - base) * k for v in acc]
    else:
        x = list(map(float(k).__mul__, acc))
    return x, fs_in / d


def _read_wav(f, size):
    hdr = f.read(12)
    if len(hdr) < 12 or hdr[:4] != b"RIFF" or hdr[8:12] != b"WAVE":
        return None
    fmt = None
    pos = 12
    while pos + 8 <= size:
        f.seek(pos)
        ch = f.read(8)
        if len(ch) < 8:
            return None
        cid, csize = ch[:4], struct.unpack("<I", ch[4:])[0]
        if cid == b"fmt ":
            d = f.read(min(csize, 64))
            if len(d) < 16:
                return None
            tag, chn, sr, _br, _al, bits = struct.unpack("<HHIIHH", d[:16])
            if tag == 0xFFFE and len(d) >= 26:
                tag = struct.unpack("<H", d[24:26])[0]
            fmt = (tag, chn, sr, bits)
        elif cid == b"data":
            if fmt is None:
                return None
            tag, chn, sr, bits = fmt
            if tag not in (1, 3) or not chn or not sr:
                return None
            enc = "float" if tag == 3 else ("uint8" if bits == 8 else "int")
            avail = min(csize, size - pos - 8)
            return f, pos + 8, avail, chn, sr, bits, enc, False
        pos += 8 + csize + (csize & 1)
    return None


def _read_aiff(f, size):
    hdr = f.read(12)
    if len(hdr) < 12 or hdr[:4] != b"FORM" or hdr[8:12] not in (b"AIFF", b"AIFC"):
        return None
    comm = None
    pos = 12
    while pos + 8 <= size:
        f.seek(pos)
        ch = f.read(8)
        if len(ch) < 8:
            return None
        cid, csize = ch[:4], struct.unpack(">I", ch[4:])[0]
        if cid == b"COMM":
            d = f.read(min(csize, 64))
            if len(d) < 18:
                return None
            chn, _frames, bits = struct.unpack(">hIh", d[:8])
            sr = _ext80(d[8:18])
            comp = d[18:22] if len(d) >= 22 else b"NONE"
            comm = (chn, int(sr), bits, comp)
        elif cid == b"SSND":
            if comm is None:
                return None
            chn, sr, bits, comp = comm
            big, enc = True, "int"
            if comp == b"sowt":
                big = False
            elif comp in (b"fl32", b"FL32"):
                enc, bits = "float", 32
            elif comp in (b"fl64", b"FL64"):
                enc, bits = "float", 64
            elif comp not in (b"NONE", b"twos"):
                return None
            off = struct.unpack(">I", f.read(4))[0]
            start = pos + 16 + off
            avail = min(csize - 8 - off, size - start)
            return f, start, avail, chn, sr, bits, enc, big
        pos += 8 + csize + (csize & 1)
    return None


def decode(path: str, ext: str, max_s: float = MAX_SECONDS):
    """-> (mono samples, sample rate, full duration in s) or None if undecodable."""
    ext = ext.lower()
    if ext not in DECODABLE:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(0)
            r = _read_wav(f, size) if ext == ".wav" else _read_aiff(f, size)
            if r is None:
                return None
            _f, start, avail, chn, sr, bits, enc, big = r
            if chn <= 0 or sr <= 0 or avail <= 0 or bits not in (8, 16, 24, 32, 64):
                return None
            align = chn * bits // 8
            dur = (avail // align) / sr
            f.seek(start)
            data = f.read(min(avail, int(max_s * sr) * align))
    except (OSError, struct.error, ValueError, OverflowError):
        return None
    out = _to_mono(data, chn, bits, enc, big, sr)
    if out is None or len(out[0]) < 64:
        return None
    return out[0], out[1], dur


# ------------------------------------------------------------------ tempo
def _envelope(x):
    prev = 0.0
    env = []
    for i in range(0, len(x) - HOP + 1, HOP):
        seg = x[i:i + HOP]
        e = math.sqrt(sum(map(_mul, seg, seg)) / HOP)
        env.append(e - prev if e > prev else 0.0)
        prev = e
    if not env:
        return env
    m = sum(env) / len(env)
    return [v - m for v in env]


def _ac(env, lag):
    """Normalized autocorrelation at a fractional lag (linear interpolation)."""
    n = len(env)
    lo = int(lag)
    fr = lag - lo
    if lo + 1 >= n:
        return 0.0
    s = 0.0
    for i in range(n - lo - 1):
        s += env[i] * (env[i + lo] * (1 - fr) + env[i + lo + 1] * fr)
    return s / (n - lo - 1)


class _AC:
    """_ac(env, lag) for many lags, fast: each whole-lag product sum is computed
    once (at C speed) and reused, then interpolated exactly like _ac."""

    def __init__(self, env):
        self.env, self.n, self.raw = env, len(env), {}

    def _r(self, lag):
        r = self.raw.get(lag)
        if r is None:
            env = self.env
            r = self.raw[lag] = sum(map(_mul, env, env[lag:]))
        return r

    def __call__(self, lag):
        n, lo = self.n, int(lag)
        fr = lag - lo
        if lo + 1 >= n:
            return 0.0
        # _ac sums i < n-lo-1: the lag-lo sum minus its last term, and the full lag-(lo+1) sum.
        a = self._r(lo) - self.env[n - lo - 1] * self.env[n - 1]
        return ((1 - fr) * a + fr * self._r(lo + 1)) / (n - lo - 1)


def _tempo_score(env, frame_rate, bpm, ac=None):
    ac = ac or (lambda lag: _ac(env, lag))
    beat = 60.0 * frame_rate / bpm
    total, used = 0.0, 0
    for k in (1, 2, 4):
        if beat * k < len(env) * 0.6:
            total += ac(beat * k)
            used += 1
    if not used:
        return 0.0
    prior = math.exp(-0.5 * (math.log2(bpm / 120.0)) ** 2)
    return total / used * (0.5 + 0.5 * prior)


def _tempo(x, fs, dur):
    env = _envelope(x)
    frame_rate = fs / HOP
    if len(env) < 16:
        return None, None
    ac = _AC(env)
    var = ac(0)
    if var <= 1e-12:
        return None, None
    best, best_s = None, -1.0
    b = BPM_LO
    while b <= BPM_HI:
        s = _tempo_score(env, frame_rate, b, ac)
        if s > best_s:
            best, best_s = b, s
        b += 0.5
    for d in (-0.4, -0.3, -0.2, -0.1, 0.1, 0.2, 0.3, 0.4):
        s = _tempo_score(env, frame_rate, best + d, ac)
        if s > best_s:
            best, best_s = best + d, s
    if best_s <= 0:
        return None, None
    # Bar-length prior: a trimmed loop is a whole number of 4/4 bars long.
    fits = [240.0 * bars / dur for bars in (1, 2, 4, 8, 16, 32)]
    fits = [f for f in fits if BPM_LO <= f <= BPM_HI]
    scored = [(_tempo_score(env, frame_rate, f, ac), f) for f in fits]
    for s, f in sorted(scored, reverse=True):
        near = min(abs(f - best * m) / f for m in (0.5, 1.0, 2.0))
        if near < 0.03 and s >= 0.6 * best_s:
            return round(f, 2), 0.85
    conf = max(0.2, min(0.6, best_s / var))
    return round(best, 1), round(conf, 2)


# ------------------------------------------------------------------ key
def _goertzel_power(seg, freq, fs):
    c = 2.0 * math.cos(2.0 * math.pi * freq / fs)
    s1 = s2 = 0.0
    for v in seg:
        s0 = v + c * s1 - s2
        s2 = s1
        s1 = s0
    return s1 * s1 + s2 * s2 - c * s1 * s2


def _chroma(x, fs, midi_lo, midi_hi, frame, frames):
    chroma = [0.0] * 12
    if len(x) < frame:
        starts = [0]
        frame = len(x)
    else:
        span = len(x) - frame
        starts = [int(span * i / max(1, frames - 1)) for i in range(frames)] if frames > 1 else [0]
    for st in starts:
        seg = x[st:st + frame]
        for m in range(midi_lo, midi_hi + 1):
            f = 440.0 * 2 ** ((m - 69) / 12)
            if f >= fs / 2:
                break
            chroma[m % 12] += math.sqrt(max(0.0, _goertzel_power(seg, f, fs)))
    return chroma


def _corr(a, b):
    ma, mb = sum(a) / 12, sum(b) / 12
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else 0.0


def _key(x, fs):
    half = x[::2]  # ~5.5 kHz is enough below MIDI 84
    chroma = _chroma(half, fs / 2, 40, 83, 2048, 6)
    if max(chroma) <= 0:
        return None, None
    scores = []
    for t in range(12):
        rot = chroma[t:] + chroma[:t]
        scores.append((_corr(rot, _MAJOR), NOTES[t]))
        scores.append((_corr(rot, _MINOR), NOTES[t] + "m"))
    scores.sort(reverse=True)
    best, second = scores[0][0], scores[1][0]
    if best < 0.5:
        return None, None
    conf = max(0.3, min(0.9, 0.5 + (best - second) * 4))
    return scores[0][1], round(conf, 2)


def _root(x, fs):
    seg = x[:int(fs * 1.5)]
    chroma = _chroma(seg, fs, 24, 72, len(seg), 1)
    top = max(chroma)
    if top <= 0:
        return None, None
    i = chroma.index(top)
    rest = sorted(chroma, reverse=True)[1]
    return NOTES[i], round(max(0.3, min(0.9, 1 - rest / top)), 2)


def rms_db(x):
    """RMS loudness in dBFS (0 = full scale square wave), one decimal."""
    if not x:
        return None
    ms = sum(map(_mul, x, x)) / len(x)
    return round(10 * math.log10(ms), 1) if ms > 1e-12 else None


# ------------------------------------------------------------------ public
def analyze(path: str, ext: str, want=frozenset({"tempo", "key"})):
    """want: "tempo" (bpm, bars, downbeat), "key" (scale key), "root" (one-shot pitch).
    Loudness (rms_db, dBFS over the decoded audio) is always measured."""
    dec = decode(path, ext)
    if dec is None:
        return None, ["audio analysis: could not decode %s" % (ext or "file")]
    x, fs, dur = dec
    res = {"v": ANALYSIS_VERSION, "bpm": None, "bpm_conf": None, "bars": None,
           "on_grid": None, "downbeat_ms": None, "key": None, "key_conf": None,
           "rms_db": None}
    peak = max(max(x), -min(x)) if x else 0.0
    if peak < 1e-3:
        return res, []
    res["rms_db"] = rms_db(x)
    first = next(i for i, v in enumerate(x) if abs(v) >= 0.1 * peak)
    res["downbeat_ms"] = round(first / fs * 1000, 1)
    if "tempo" in want and dur >= 1.0:
        body = dur - first / fs  # bars count from the first onset
        bpm, conf = _tempo(x, fs, body)
        res["bpm"], res["bpm_conf"] = bpm, conf
        if bpm:
            bars = body * bpm / 240.0
            q = 1.0 if bars >= 1 else 0.25
            nearest = round(bars / q) * q
            if nearest and abs(bars - nearest) <= 0.02 * nearest:
                res["bars"] = nearest if q < 1 else int(nearest)
            res["on_grid"] = res["bars"] is not None and res["downbeat_ms"] <= 20
    if "key" in want:
        res["key"], res["key_conf"] = _key(x, fs)
    elif "root" in want:
        res["key"], res["key_conf"] = _root(x, fs)
    return res, []


def analyze_job(path: str, ext: str, want):
    """analyze() for a worker process: never raises, so one bad file cannot stop a pool."""
    try:
        return analyze(path, ext, want)
    except Exception as e:  # noqa: BLE001
        return None, ["audio analysis failed: %s" % e]


# ------------------------------------------------------------------ keys
@functools.lru_cache(maxsize=256)
def norm_key(key):
    if not key:
        return None
    k = key.strip()
    minor = k.endswith("m")
    base = k[:-1] if minor else k
    base = _FLATS.get(base, base)
    if base not in NOTES:
        return None
    return base + ("m" if minor else "")


@functools.lru_cache(maxsize=256)
def camelot(key):
    """Camelot wheel code ("8A" = A minor, "8B" = C major) or None."""
    k = norm_key(key)
    if not k:
        return None
    minor = k.endswith("m")
    pc = NOTES.index(k[:-1] if minor else k)
    if minor:
        pc = (pc + 3) % 12  # relative major shares the number
    num = ((pc * 7) % 12 + 7) % 12 + 1  # C major = 8B
    return "%d%s" % (num, "A" if minor else "B")


@functools.lru_cache(maxsize=4096)
def relative_key(key):
    """Am -> C, C -> Am; None when unknown."""
    k = norm_key(key)
    if not k:
        return None
    if k.endswith("m"):
        return NOTES[(NOTES.index(k[:-1]) + 3) % 12]
    return NOTES[(NOTES.index(k) - 3) % 12] + "m"


@functools.lru_cache(maxsize=4096)
def key_compat(a, b):
    """-> (score 0..1, reason) for two keys."""
    ca, cb = camelot(a), camelot(b)
    if not ca or not cb:
        return 0.4, "key unknown"
    if ca == cb:
        return 1.0, "same key"
    na, la = int(ca[:-1]), ca[-1]
    nb, lb = int(cb[:-1]), cb[-1]
    if na == nb:
        return 0.9, "relative major/minor"
    if la == lb and (na - nb) % 12 in (1, 11):
        return 0.7, "neighbor key (Camelot %s/%s)" % (ca, cb)
    return 0.1, "keys clash (%s vs %s)" % (norm_key(a), norm_key(b))

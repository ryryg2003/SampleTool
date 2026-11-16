"""AIFF/AIFC -> WAV streaming conversion and waveform peaks (stdlib only).

OWNER: media feature. Everything here is strictly read-only against the
collection: files are opened "rb" and never written. The only writes are the
peak cache files, which live in the app data dir (``peaks_cache_*``).

AIFF previews
    ``probe_aiff(path)`` reads the COMM/SSND headers and returns a ``PcmSource``
    describing where the sample data lives and how it is encoded. The browser
    gets a *virtual* WAV file: a 44-byte header followed by the converted data.
    Its exact size is known up front (``wav_size``), so Content-Length and HTTP
    Range requests work. ``wav_range(src, path, start, end)`` yields the bytes
    [start, end] of that virtual file, converting only the needed region of the
    SSND data (aligned to sample boundaries) in bounded chunks.

    Supported: AIFF 8/16/24/32-bit PCM (big-endian), AIFC ``NONE``/``twos``/
    ``in24``/``in32`` (big-endian PCM), ``sowt`` (little-endian PCM), ``raw ``
    (unsigned 8-bit), ``fl32``/``FL32`` (32-bit float). 8-bit AIFF is signed and
    becomes unsigned 8-bit WAV; wider samples are byte-swapped to little-endian.
    Anything else raises ``Unsupported`` with a readable reason.

Peaks
    ``compute_peaks(path, ext, n)`` returns ``{n, peaks: [[min, max], ...],
    duration_s}`` normalized to -1..1 for WAV (PCM 8/16/24/32, float 32/64,
    WAVE_FORMAT_EXTENSIBLE) and AIFF/AIFC. Work is bounded: at most
    ``PEAK_READ_BUDGET`` bytes are read, sampled in blocks spread across the
    file, so long files stay fast.
"""
from __future__ import annotations

import array
import json
import os
import struct
import sys
import time
from dataclasses import dataclass
from typing import Iterator, Optional

AIFF_EXTS = {".aif", ".aiff", ".aifc"}
WAV_EXTS = {".wav", ".wave"}
REX_EXTS = {".rex", ".rx2"}
WAV_HEADER_SIZE = 44
CHUNK_BYTES = 1 << 16           # streaming chunk (rounded to whole frames)
PEAK_READ_BUDGET = 3 << 20      # max bytes read to compute one waveform
PEAK_SUBBLOCKS = 4              # blocks sampled per bin when striding
MAX_HEADER_CHUNKS = 512

_BE_PCM = {"NONE", "twos", "in24", "in32", "lpcm"}
_FLOAT_BE = {"fl32", "FL32"}


class Unsupported(Exception):
    """The file cannot be converted / analysed; ``str(e)`` is user-facing."""


@dataclass
class PcmSource:
    channels: int
    frames: int
    sample_rate: float
    width: int            # bytes per sample in the file (and in the WAV output)
    bits: int             # bits per sample as declared
    encoding: str         # "s_be", "s_le", "u8", "s8", "f_be", "f_le"
    data_offset: int      # absolute file offset of the first sample byte
    data_len: int         # bytes of sample data available (whole frames)

    @property
    def frame_bytes(self) -> int:
        return self.channels * self.width

    @property
    def is_float(self) -> bool:
        return self.encoding.startswith("f")

    @property
    def duration_s(self) -> Optional[float]:
        if self.sample_rate > 0:
            return self.frames / self.sample_rate
        return None


def preview_kind(it: dict) -> str:
    """Row-time preview value. AIFF auditions through the WAV converter, so it is
    "native" even for inventories saved by older versions ("unsupported")."""
    if (it.get("ext") or "").lower() in AIFF_EXTS:
        return "native"
    return it.get("preview", "none")


# --------------------------------------------------------------- AIFF parse
def _ext80(b: bytes) -> float:
    exp, hi, lo = struct.unpack(">HII", b)
    sign = -1.0 if exp & 0x8000 else 1.0
    exp &= 0x7FFF
    if exp == 0 and hi == 0 and lo == 0:
        return 0.0
    if exp == 0x7FFF:
        return float("nan")
    return sign * ((hi << 32) | lo) * (2.0 ** (exp - 16383 - 63))


def probe_aiff(path: str) -> PcmSource:
    size = os.path.getsize(path)
    with open(path, "rb") as f:  # read-only
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[:4] != b"FORM" or hdr[8:12] not in (b"AIFF", b"AIFC"):
            raise Unsupported("Not an AIFF/AIFC file (no FORM/AIFF header).")
        aifc = hdr[8:12] == b"AIFC"
        pos, comm, ssnd = 12, None, None
        for _ in range(MAX_HEADER_CHUNKS):
            if pos + 8 > size:
                break
            f.seek(pos)
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid, csize = ch[:4], struct.unpack(">I", ch[4:])[0]
            if cid == b"COMM":
                d = f.read(min(csize, 512))
                if len(d) < 18:
                    raise Unsupported("AIFF COMM chunk is too short.")
                chs, frames, bits = struct.unpack(">hIh", d[:8])
                comp = "NONE"
                if aifc:
                    if len(d) < 22:
                        raise Unsupported("AIFC COMM chunk has no compression type.")
                    comp = d[18:22].decode("latin-1")
                comm = (chs, frames, bits, _ext80(d[8:18]), comp)
            elif cid == b"SSND":
                d = f.read(8)
                if len(d) < 8:
                    raise Unsupported("AIFF SSND chunk is truncated.")
                off = struct.unpack(">I", d[:4])[0]
                start = pos + 16 + off
                avail = min(pos + 8 + csize, size) - start
                ssnd = (start, max(0, avail))
            if comm and ssnd:
                break
            pos += 8 + csize + (csize & 1)
    if comm is None:
        raise Unsupported("AIFF COMM chunk is missing.")
    chs, frames, bits, sr, comp = comm
    if chs <= 0 or not (1 <= bits <= 32):
        raise Unsupported(f"AIFF with {chs} channels and {bits}-bit samples is not supported.")
    if not (sr == sr) or sr <= 0:
        raise Unsupported("AIFF sample rate is invalid.")
    width = (bits + 7) // 8
    if comp in _BE_PCM:
        enc = "s8" if width == 1 else "s_be"
    elif comp == "sowt":
        enc = "s8" if width == 1 else "s_le"
    elif comp == "raw ":
        if width != 1:
            raise Unsupported("AIFC 'raw ' is only supported for 8-bit audio.")
        enc = "u8"
    elif comp in _FLOAT_BE:
        enc, width, bits = "f_be", 4, 32
    else:
        raise Unsupported(f"AIFC compression '{comp.strip() or comp}' is not supported for preview. "
                          "Open it in your DAW.")
    if ssnd is None:
        start, avail = 0, 0
    else:
        start, avail = ssnd
    fb = chs * width
    n = min(frames, avail // fb)  # tolerate truncated files: whole frames only
    return PcmSource(chs, n, sr, width, bits, enc, start, n * fb)


# ---------------------------------------------------------- WAV conversion
def wav_size(src: PcmSource) -> int:
    return WAV_HEADER_SIZE + src.data_len


def wav_header(src: PcmSource) -> bytes:
    fmt_tag = 3 if src.is_float else 1
    sr = int(round(src.sample_rate))
    ba = src.frame_bytes
    return (b"RIFF" + struct.pack("<I", 36 + src.data_len) + b"WAVE" +
            b"fmt " + struct.pack("<IHHIIHH", 16, fmt_tag, src.channels, sr, sr * ba, ba, src.width * 8) +
            b"data" + struct.pack("<I", src.data_len))


_S8_TO_U8 = bytes((i + 128) & 0xFF for i in range(256))


def _swap(d: bytes, w: int) -> bytes:
    """Reverse byte order of every w-byte word (len(d) must be a multiple of w)."""
    if w == 1:
        return d
    out = bytearray(len(d))
    for k in range(w):
        out[k::w] = d[w - 1 - k::w]
    return bytes(out)


def to_wav_bytes(src: PcmSource, d: bytes) -> bytes:
    """Convert aligned sample bytes from the file encoding to WAV encoding."""
    e = src.encoding
    if e == "s8":
        return d.translate(_S8_TO_U8)
    if e in ("s_be", "f_be"):
        return _swap(d, src.width)
    return d  # s_le, f_le, u8


def parse_range(header: Optional[str], size: int):
    """-> (status, start, end) with end inclusive, or None when unsatisfiable (416).
    Malformed headers are ignored (200, whole body), as the stock handler does."""
    import re
    if size <= 0:
        return (200, 0, -1) if not header else None
    if not header:
        return 200, 0, size - 1
    m = re.match(r"\s*bytes=(\d*)-(\d*)\s*$", header)
    if not m or (not m.group(1) and not m.group(2)):
        return 200, 0, size - 1
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:
        n = int(m.group(2))
        if n == 0:
            return None
        start, end = max(0, size - n), size - 1
    if start >= size or start > end:
        return None
    return 206, start, end


def wav_range(src: PcmSource, path: str, start: int, end: int,
              chunk: int = CHUNK_BYTES) -> Iterator[bytes]:
    """Yield bytes [start, end] (inclusive) of the virtual WAV for ``src``."""
    total = wav_size(src)
    end = min(end, total - 1)
    if start > end:
        return
    if start < WAV_HEADER_SIZE:
        yield wav_header(src)[start:min(end + 1, WAV_HEADER_SIZE)]
    ds = max(start, WAV_HEADER_SIZE) - WAV_HEADER_SIZE
    de = end + 1 - WAV_HEADER_SIZE  # exclusive
    if de <= ds:
        return
    w = src.width
    a0 = ds - ds % w
    a1 = min(src.data_len, -(-de // w) * w)
    step = max(w, chunk - chunk % w)
    with open(path, "rb") as f:  # read-only
        f.seek(src.data_offset + a0)
        pos = a0
        while pos < a1:
            want = min(step, a1 - pos)
            d = f.read(want)
            if len(d) < want:  # file shrank under us: pad with silence to keep length honest
                d += b"\0" * (want - len(d))
            out = to_wav_bytes(src, d)
            lo = ds - pos if pos < ds else 0
            hi = len(out) - max(0, pos + want - de)
            yield out[lo:hi]
            pos += want


def convert_aiff_to_wav(path: str) -> bytes:
    """Whole-file conversion (tests / small files only)."""
    src = probe_aiff(path)
    return b"".join(wav_range(src, path, 0, wav_size(src) - 1))


# ---------------------------------------------------------------- WAV parse
def probe_wav(path: str) -> PcmSource:
    size = os.path.getsize(path)
    with open(path, "rb") as f:  # read-only
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[:4] not in (b"RIFF", b"RIFX") or hdr[8:12] != b"WAVE":
            raise Unsupported("Not a RIFF/WAVE file.")
        if hdr[:4] == b"RIFX":
            raise Unsupported("Big-endian RIFX WAV is not supported.")
        pos, fmt, data = 12, None, None
        for _ in range(MAX_HEADER_CHUNKS):
            if pos + 8 > size:
                break
            f.seek(pos)
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid, csize = ch[:4], struct.unpack("<I", ch[4:])[0]
            if cid == b"fmt ":
                fmt = f.read(min(csize, 64))
            elif cid == b"data":
                data = (pos + 8, max(0, min(csize, size - pos - 8)))
            if fmt and data:
                break
            pos += 8 + csize + (csize & 1)
    if not fmt or len(fmt) < 16:
        raise Unsupported("WAV fmt chunk is missing.")
    if data is None:
        raise Unsupported("WAV data chunk is missing.")
    tag, chs, sr, _br, align, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack("<H", fmt[24:26])[0]  # SubFormat GUID's first two bytes
    if chs <= 0:
        raise Unsupported("WAV has no channels.")
    if tag == 1:
        width = (bits + 7) // 8
        if not 1 <= width <= 4:
            raise Unsupported(f"{bits}-bit PCM WAV is not supported.")
        enc = "u8" if width == 1 else "s_le"
    elif tag == 3:
        if bits not in (32, 64):
            raise Unsupported(f"{bits}-bit float WAV is not supported.")
        width, enc = bits // 8, "f_le"
    else:
        names = {2: "MS ADPCM", 6: "A-law", 7: "mu-law", 0x11: "IMA ADPCM", 0x55: "MP3"}
        raise Unsupported(f"WAV encoding {names.get(tag, hex(tag))} has no waveform.")
    if align and align >= chs * width:
        width = align // chs if align % chs == 0 else width
    fb = chs * width
    start, avail = data
    n = avail // fb
    return PcmSource(chs, n, float(sr), width, bits, enc, start, n * fb)


# -------------------------------------------------------------------- peaks
_I32 = next(t for t in ("i", "l") if array.array(t).itemsize == 4)
_BIG = sys.byteorder == "big"


def _decode(src: PcmSource, d: bytes) -> tuple[array.array, float, float]:
    """-> (values, scale, offset) so sample = (v - offset) / scale."""
    w, e = src.width, src.encoding
    if e == "u8":
        return array.array("B", d), 128.0, 128.0
    if e == "s8":
        return array.array("b", d), 128.0, 0.0
    le = d if e in ("s_le", "f_le") else _swap(d, w)
    if e.startswith("f"):
        a = array.array("f" if w == 4 else "d", le)
        if _BIG:
            a.byteswap()
        return a, 1.0, 0.0
    if w == 2:
        a = array.array("h", le)
    elif w == 4:
        a = array.array(_I32, le)
    else:  # 24-bit: widen to 32 by putting a zero low byte in front
        n = len(le) // 3
        b = bytearray(4 * n)
        b[1::4] = le[0::3]
        b[2::4] = le[1::3]
        b[3::4] = le[2::3]
        a = array.array(_I32, bytes(b))
        w = 4
    if _BIG:
        a.byteswap()
    return a, float(1 << (8 * w - 1)), 0.0


def _clamp(x: float) -> float:
    if x != x:
        return 0.0
    return -1.0 if x < -1.0 else 1.0 if x > 1.0 else x


def source_for(path: str, ext: str) -> PcmSource:
    ext = (ext or "").lower()
    if ext in AIFF_EXTS:
        return probe_aiff(path)
    if ext in WAV_EXTS:
        return probe_wav(path)
    raise Unsupported(unsupported_reason(ext))


def unsupported_reason(ext: str) -> str:
    ext = (ext or "").lower()
    if ext in REX_EXTS:
        return "REX/RX2 loops can't be previewed here. Open the file in your DAW."
    if ext:
        return f"No waveform for {ext.lstrip('.').upper()} files."
    return "No waveform for this file."


def compute_peaks(path: str, ext: str, n: int = 800, budget: int = PEAK_READ_BUDGET) -> dict:
    """Waveform peaks; raises Unsupported. Reads at most ~``budget`` bytes."""
    src = source_for(path, ext)
    n = max(1, int(n))
    fb = src.frame_bytes
    frames = src.frames
    bins = min(n, frames) if frames else 0
    peaks: list = []
    if bins:
        full = src.data_len <= budget
        per_bin = max(fb, (budget // bins) // fb * fb)
        with open(path, "rb") as f:  # read-only
            for i in range(bins):
                f0 = frames * i // bins
                f1 = max(f0 + 1, frames * (i + 1) // bins)
                blen = (f1 - f0) * fb
                if full or blen <= per_bin:
                    spans = [(f0 * fb, blen)]
                else:  # stride: a few blocks spread across the bin
                    k = PEAK_SUBBLOCKS
                    sub = max(fb, (per_bin // k) // fb * fb)
                    gap = (blen - sub) // max(1, k - 1) // fb * fb
                    spans = [(f0 * fb + j * gap, sub) for j in range(k)]
                lo, hi = 0.0, 0.0
                first = True
                for off, ln in spans:
                    f.seek(src.data_offset + off)
                    d = f.read(ln)
                    d = d[:len(d) - len(d) % src.width]
                    if not d:
                        continue
                    a, scale, zero = _decode(src, d)
                    mn, mx = (min(a) - zero) / scale, (max(a) - zero) / scale
                    if first:
                        lo, hi, first = mn, mx, False
                    else:
                        lo, hi = min(lo, mn), max(hi, mx)
                peaks.append([round(_clamp(lo), 4), round(_clamp(hi), 4)])
    dur = src.duration_s
    return {"n": len(peaks), "peaks": peaks,
            "duration_s": round(dur, 6) if dur is not None else None,
            "channels": src.channels, "sample_rate": int(round(src.sample_rate))}


# -------------------------------------------------------------- peak cache
def peaks_cache_path(data_dir: str, sha256: str, n: int) -> str:
    sha = "".join(c for c in sha256.lower() if c in "0123456789abcdef")
    if len(sha) != 64:
        raise ValueError("bad sha256")
    return os.path.join(data_dir, "peaks", sha[:2], f"{sha}-{int(n)}.json")


def peaks_cache_read(data_dir: str, sha256: Optional[str], n: int) -> Optional[dict]:
    if not sha256:
        return None
    try:
        with open(peaks_cache_path(data_dir, sha256, n), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) and ("peaks" in d or d.get("unsupported")) else None
    except (OSError, ValueError):
        return None


def peaks_cache_write(data_dir: str, sha256: Optional[str], n: int, d: dict) -> None:
    """Atomic write into <data_dir>/peaks only. Failures are ignored (cache)."""
    if not sha256:
        return
    try:
        p = peaks_cache_path(data_dir, sha256, n)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{os.getpid()}.{time.monotonic_ns()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, separators=(",", ":"))
        os.replace(tmp, p)
    except (OSError, ValueError):
        pass

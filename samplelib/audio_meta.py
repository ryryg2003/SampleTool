"""Bounded, read-only audio header parsing (stdlib only).

read_audio(path, ext) -> (AudioInfo | None, tags_raw, warnings)

Never reads whole files: every parser reads small header windows, skips audio
payload chunks with seek(), and stops after MAX_READ bytes of metadata.
Malformed input produces warnings, never exceptions.

tags_raw keys are the native tag ids ("INAM", "TBPM", "BPM", ...) as found in
the file, plus structured entries "acid" and "smpl" for WAV chunks.
"""
from __future__ import annotations

import os
import struct

MAX_READ = 4 * 1024 * 1024        # total metadata bytes read per file
MAX_CHUNK = 1 * 1024 * 1024       # largest single metadata chunk we will load
MAX_CHUNKS = 512                  # chunk iteration cap

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_note_name(n: int) -> str:
    return NOTE_NAMES[int(n) % 12]


class _Reader:
    """File wrapper that enforces a total read budget."""

    def __init__(self, f, size: int):
        self.f = f
        self.size = size
        self.budget = MAX_READ

    def read(self, n: int) -> bytes:
        n = max(0, min(n, self.budget))
        data = self.f.read(n)
        self.budget -= len(data)
        return data

    def seek(self, pos: int, whence: int = 0):
        self.f.seek(pos, whence)

    def tell(self) -> int:
        return self.f.tell()


def _text(b: bytes) -> str:
    b = b.split(b"\x00", 1)[0]
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return b.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return ""


def _put(tags: dict, key: str, value):
    if value in (None, ""):
        return
    if key in tags and isinstance(tags[key], str) and isinstance(value, str) \
            and value not in tags[key].split(" / "):
        tags[key] = tags[key] + " / " + value
    elif key not in tags:
        tags[key] = value


# ---------------------------------------------------------------- WAV / RF64
def _parse_wav(r: _Reader, info: dict, tags: dict, warns: list):
    hdr = r.read(12)
    if len(hdr) < 12 or hdr[8:12] != b"WAVE":
        warns.append("not a RIFF/WAVE file")
        return None
    magic = hdr[:4]
    if magic not in (b"RIFF", b"RF64", b"BW64"):
        warns.append("unsupported RIFF variant %r" % magic)
        return None
    rf64 = magic != b"RIFF"
    ds64_data = None
    fmt = None
    data_size = None
    pos = 12
    for _ in range(MAX_CHUNKS):
        if pos + 8 > r.size:
            break
        r.seek(pos)
        ch = r.read(8)
        if len(ch) < 8:
            break
        cid, csize = ch[:4], struct.unpack("<I", ch[4:])[0]
        body_pos = pos + 8
        if cid == b"ds64" and csize >= 24:
            d = r.read(min(csize, 64))
            if len(d) >= 16:
                ds64_data = struct.unpack("<Q", d[8:16])[0]
        elif cid == b"data":
            if rf64 and csize == 0xFFFFFFFF:
                if ds64_data is None:
                    warns.append("RF64 without ds64 size; duration unknown")
                    break
                csize = ds64_data
            data_size = min(csize, max(0, r.size - body_pos))
            if csize > r.size - body_pos:
                warns.append("data chunk truncated")
        elif cid == b"fmt ":
            d = r.read(min(csize, 4096))
            if len(d) >= 16:
                tag, ch_n, sr, _br, align, bits = struct.unpack("<HHIIHH", d[:16])
                if tag == 0xFFFE and len(d) >= 40:
                    valid = struct.unpack("<H", d[18:20])[0]
                    tag = struct.unpack("<H", d[24:26])[0]
                    if valid:
                        bits = valid
                fmt = {"tag": tag, "channels": ch_n, "sample_rate": sr,
                       "align": align, "bits": bits}
            else:
                warns.append("fmt chunk too short")
        elif cid == b"LIST" and csize <= MAX_CHUNK:
            d = r.read(csize)
            if d[:4] == b"INFO":
                _parse_info(d[4:], tags)
        elif cid == b"acid" and csize >= 24:
            d = r.read(24)
            if len(d) == 24:
                flags, root, _u1, _u2, beats, mden, mnum, tempo = \
                    struct.unpack("<IHHfIHHf", d)
                acid = {"flags": flags, "one_shot": bool(flags & 0x01),
                        "root_note_set": bool(flags & 0x02),
                        "root_note": root, "beats": beats,
                        "meter": "%d/%d" % (mnum, mden) if mden else None,
                        "tempo": round(float(tempo), 3) if tempo == tempo else None}
                if acid["root_note_set"] and 0 <= root < 128:
                    acid["root_note_name"] = midi_note_name(root)
                tags["acid"] = acid
        elif cid == b"smpl" and csize >= 36:
            d = r.read(min(csize, 36 + 24 * 64))
            if len(d) >= 36:
                vals = struct.unpack("<9I", d[:36])
                n_loops = vals[7]
                loops = []
                off = 36
                for _i in range(min(n_loops, 64)):
                    if off + 24 > len(d):
                        break
                    _cue, ltype, start, end, _fr, count = struct.unpack("<6I", d[off:off + 24])
                    loops.append({"type": ltype, "start": start, "end": end,
                                  "play_count": count})
                    off += 24
                tags["smpl"] = {"unity_note": vals[3], "loops": loops}
        elif cid in (b"id3 ", b"ID3 ") and csize <= MAX_CHUNK:
            d = r.read(csize)
            _parse_id3_bytes(d, tags, warns)
        if r.budget <= 0:
            warns.append("metadata read cap reached")
            break
        pos = body_pos + csize + (csize & 1)
    if fmt is None:
        warns.append("WAV fmt chunk missing")
        return None
    out = {"format": "wav", "sample_rate": fmt["sample_rate"],
           "channels": fmt["channels"], "bits": fmt["bits"]}
    if data_size is not None and fmt["align"] and fmt["sample_rate"]:
        frames = data_size // fmt["align"]
        out["frames"] = frames
        out["duration_s"] = round(frames / fmt["sample_rate"], 6)
    elif data_size is None:
        warns.append("WAV data chunk missing")
    info.update(out)
    return info


def _parse_info(d: bytes, tags: dict):
    off = 0
    while off + 8 <= len(d):
        sid = d[off:off + 4]
        ssz = struct.unpack("<I", d[off + 4:off + 8])[0]
        val = d[off + 8:off + 8 + ssz]
        try:
            key = sid.decode("ascii")
        except UnicodeDecodeError:
            break
        if key.isalnum():
            _put(tags, key, _text(val))
        off += 8 + ssz + (ssz & 1)


# ---------------------------------------------------------------- AIFF
def _ext80(b: bytes) -> float:
    exp, hi, lo = struct.unpack(">HII", b)
    sign = -1 if exp & 0x8000 else 1
    exp &= 0x7FFF
    if exp == 0 and hi == 0 and lo == 0:
        return 0.0
    if exp == 0x7FFF:
        return float("nan")
    mant = (hi << 32) | lo
    return sign * mant * (2.0 ** (exp - 16383 - 63))


def _parse_aiff(r: _Reader, info: dict, tags: dict, warns: list):
    hdr = r.read(12)
    if len(hdr) < 12 or hdr[:4] != b"FORM" or hdr[8:12] not in (b"AIFF", b"AIFC"):
        warns.append("not an AIFF/AIFC file")
        return None
    aifc = hdr[8:12] == b"AIFC"
    pos = 12
    comm = None
    for _ in range(MAX_CHUNKS):
        if pos + 8 > r.size:
            break
        r.seek(pos)
        ch = r.read(8)
        if len(ch) < 8:
            break
        cid, csize = ch[:4], struct.unpack(">I", ch[4:])[0]
        if cid == b"COMM":
            d = r.read(min(csize, 512))
            if len(d) >= 18:
                chs, frames, bits = struct.unpack(">hIh", d[:8])
                sr = _ext80(d[8:18])
                comm = {"channels": chs, "frames": frames, "bits": bits,
                        "sample_rate": sr}
                if aifc and len(d) >= 22:
                    comm["compression"] = d[18:22].decode("latin-1")
            else:
                warns.append("COMM chunk too short")
        elif cid in (b"NAME", b"AUTH", b"ANNO", b"(c) ") and csize <= MAX_CHUNK:
            _put(tags, cid.decode("latin-1").strip(), _text(r.read(csize)))
        elif cid in (b"ID3 ", b"id3 ") and csize <= MAX_CHUNK:
            _parse_id3_bytes(r.read(csize), tags, warns)
        if r.budget <= 0:
            break
        pos += 8 + csize + (csize & 1)
    if comm is None:
        warns.append("AIFF COMM chunk missing")
        return None
    sr = comm["sample_rate"]
    if not (sr == sr) or sr <= 0:
        warns.append("invalid AIFF sample rate")
        sr = 0
    info.update({"format": "aiff", "sample_rate": int(round(sr)),
                 "channels": comm["channels"], "bits": comm["bits"],
                 "frames": comm["frames"]})
    if sr:
        info["duration_s"] = round(comm["frames"] / sr, 6)
    return info


# ---------------------------------------------------------------- FLAC
def _vorbis_comments(d: bytes, tags: dict, warns: list):
    try:
        off = 0
        vlen = struct.unpack("<I", d[off:off + 4])[0]
        off += 4 + vlen
        n = struct.unpack("<I", d[off:off + 4])[0]
        off += 4
        for _ in range(min(n, 4096)):
            ln = struct.unpack("<I", d[off:off + 4])[0]
            off += 4
            s = d[off:off + ln].decode("utf-8", "replace")
            off += ln
            if "=" in s:
                k, v = s.split("=", 1)
                _put(tags, k.strip().upper(), v.strip())
    except struct.error:
        warns.append("truncated Vorbis comment block")


def _skip_id3_prefix(r: _Reader) -> int:
    r.seek(0)
    h = r.read(10)
    if len(h) == 10 and h[:3] == b"ID3":
        size = _syncsafe(h[6:10]) + 10 + (10 if h[5] & 0x10 else 0)
        return size
    return 0


def _parse_flac(r: _Reader, info: dict, tags: dict, warns: list):
    start = _skip_id3_prefix(r)
    r.seek(start)
    if r.read(4) != b"fLaC":
        warns.append("not a FLAC file")
        return None
    got = False
    for _ in range(MAX_CHUNKS):
        h = r.read(4)
        if len(h) < 4:
            break
        last = h[0] & 0x80
        btype = h[0] & 0x7F
        blen = int.from_bytes(h[1:4], "big")
        if btype == 0 and blen >= 34:
            d = r.read(34)
            if len(d) == 34:
                x = int.from_bytes(d[10:18], "big")
                sr = x >> 44
                chs = ((x >> 41) & 0x7) + 1
                bps = ((x >> 36) & 0x1F) + 1
                total = x & 0xFFFFFFFFF
                info.update({"format": "flac", "sample_rate": sr, "channels": chs,
                             "bits": bps, "frames": total})
                if sr and total:
                    info["duration_s"] = round(total / sr, 6)
                got = True
            r.seek(blen - 34, 1)
        elif btype == 4 and blen <= MAX_CHUNK:
            _vorbis_comments(r.read(blen), tags, warns)
        else:
            r.seek(blen, 1)
        if last or r.budget <= 0:
            break
    if not got:
        warns.append("FLAC STREAMINFO missing")
        return None
    return info


# ---------------------------------------------------------------- ID3 / MP3
def _syncsafe(b: bytes) -> int:
    return (b[0] & 0x7F) << 21 | (b[1] & 0x7F) << 14 | (b[2] & 0x7F) << 7 | (b[3] & 0x7F)


def _id3_text(enc: int, b: bytes) -> str:
    try:
        if enc == 0:
            s = b.decode("latin-1")
        elif enc == 1:
            s = b.decode("utf-16")
        elif enc == 2:
            s = b.decode("utf-16-be")
        else:
            s = b.decode("utf-8", "replace")
    except UnicodeDecodeError:
        s = b.decode("latin-1", "replace")
    return s.replace("\x00", " / ").strip(" /﻿")


def _split_terminated(enc: int, b: bytes):
    term = b"\x00\x00" if enc in (1, 2) else b"\x00"
    i = 0
    while True:
        j = b.find(term, i)
        if j < 0:
            return b, b""
        if len(term) == 2 and j % 2:
            i = j + 1
            continue
        return b[:j], b[j + len(term):]


def _parse_id3_bytes(d: bytes, tags: dict, warns: list) -> int:
    """Parse an ID3v2 tag at the start of d; returns total tag size (0 if none)."""
    if len(d) < 10 or d[:3] != b"ID3":
        return 0
    major, flags = d[3], d[5]
    size = _syncsafe(d[6:10])
    total = 10 + size + (10 if flags & 0x10 else 0)
    body = d[10:10 + size]
    if major not in (2, 3, 4):
        warns.append("unsupported ID3v2.%d" % major)
        return total
    if flags & 0x80 and major < 4:
        body = body.replace(b"\xff\x00", b"\xff")
    off = 0
    if flags & 0x40 and major >= 3 and len(body) >= 4:
        off = (_syncsafe(body[:4]) if major == 4 else struct.unpack(">I", body[:4])[0] + 4)
    while off < len(body):
        if major == 2:
            if off + 6 > len(body):
                break
            fid = body[off:off + 3]
            fsz = int.from_bytes(body[off + 3:off + 6], "big")
            off += 6
        else:
            if off + 10 > len(body):
                break
            fid = body[off:off + 4]
            fsz = _syncsafe(body[off + 4:off + 8]) if major == 4 \
                else struct.unpack(">I", body[off + 4:off + 8])[0]
            off += 10
        if not fid.strip(b"\x00") or fsz <= 0:
            break
        fd = body[off:off + fsz]
        off += fsz
        try:
            key = fid.decode("ascii")
        except UnicodeDecodeError:
            break
        key = {"TBP": "TBPM", "TKE": "TKEY", "TCO": "TCON", "TP1": "TPE1",
               "TT2": "TIT2", "TAL": "TALB", "COM": "COMM", "TXX": "TXXX"}.get(key, key)
        if not fd:
            continue
        enc = fd[0]
        if key == "TXXX":
            desc, val = _split_terminated(enc, fd[1:])
            _put(tags, "TXXX:" + _id3_text(enc, desc).upper(), _id3_text(enc, val))
        elif key.startswith("T"):
            _put(tags, key, _id3_text(enc, fd[1:]))
        elif key == "COMM" and len(fd) > 4:
            _desc, val = _split_terminated(enc, fd[4:])
            _put(tags, "COMM", _id3_text(enc, val))
    if off > len(body) or (size and len(d) < 10 + size):
        warns.append("ID3 tag truncated")
    return total


_BITRATES = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_BITRATES[(2, 3)] = _BITRATES[(2, 2)]
_SRATES = {1: [44100, 48000, 32000], 2: [22050, 24000, 16000], 25: [11025, 12000, 8000]}


def _mp3_header(b: bytes):
    if len(b) < 4 or b[0] != 0xFF or (b[1] & 0xE0) != 0xE0:
        return None
    ver_bits = (b[1] >> 3) & 3
    layer_bits = (b[1] >> 1) & 3
    if ver_bits == 1 or layer_bits == 0:
        return None
    ver = {3: 1, 2: 2, 0: 25}[ver_bits]
    layer = 4 - layer_bits
    br_i = b[2] >> 4
    sr_i = (b[2] >> 2) & 3
    if br_i in (0, 15) or sr_i == 3:
        return None
    pad = (b[2] >> 1) & 1
    mode = b[3] >> 6
    br = _BITRATES[(1 if ver == 1 else 2, layer)][br_i] * 1000
    sr = _SRATES[ver][sr_i]
    if layer == 1:
        spf = 384
        flen = (12 * br // sr + pad) * 4
    else:
        spf = 1152 if (layer == 2 or ver == 1) else 576
        flen = spf // 8 * br // sr + pad
    return {"version": ver, "layer": layer, "bitrate": br, "sample_rate": sr,
            "channels": 1 if mode == 3 else 2, "spf": spf, "frame_len": flen}


def _parse_mp3(r: _Reader, info: dict, tags: dict, warns: list):
    r.seek(0)
    head = r.read(10)
    audio_start = 0
    if head[:3] == b"ID3" and len(head) == 10:
        size = _syncsafe(head[6:10]) + 10 + (10 if head[5] & 0x10 else 0)
        if size <= MAX_CHUNK:
            r.seek(0)
            _parse_id3_bytes(r.read(size), tags, warns)
        else:
            warns.append("ID3 tag larger than read cap; tags skipped")
        audio_start = size
    r.seek(audio_start)
    buf = r.read(64 * 1024)
    hdr = None
    i = 0
    while True:
        i = buf.find(b"\xff", i)
        if i < 0 or i + 4 > len(buf):
            break
        h = _mp3_header(buf[i:i + 4])
        if h and h["frame_len"] > 0:
            nxt = i + h["frame_len"]
            # confirm with the following frame header when it is in the window
            if nxt + 4 > len(buf) or _mp3_header(buf[nxt:nxt + 4]):
                hdr = h
                break
        i += 1
    if not hdr:
        warns.append("no MPEG audio frame found")
        return None
    frame_pos = audio_start + i
    info.update({"format": "mp3", "sample_rate": hdr["sample_rate"],
                 "channels": hdr["channels"]})
    frames = None
    fb = buf[i:i + 200]
    if hdr["version"] == 1:
        side = 17 if hdr["channels"] == 1 else 32
    else:
        side = 9 if hdr["channels"] == 1 else 17
    x = 4 + side
    if fb[x:x + 4] in (b"Xing", b"Info") and len(fb) >= x + 12:
        flags = struct.unpack(">I", fb[x + 4:x + 8])[0]
        if flags & 1:
            frames = struct.unpack(">I", fb[x + 8:x + 12])[0]
            info["vbr"] = fb[x:x + 4] == b"Xing"
    elif fb[36:40] == b"VBRI" and len(fb) >= 54:
        frames = struct.unpack(">I", fb[50:54])[0]
        info["vbr"] = True
    if frames:
        info["frames"] = frames * hdr["spf"]
        info["duration_s"] = round(frames * hdr["spf"] / hdr["sample_rate"], 6)
    else:
        end = r.size
        try:
            r.seek(max(0, r.size - 128))
            if r.read(3) == b"TAG":
                end -= 128
        except OSError:
            pass
        if hdr["bitrate"]:
            info["duration_s"] = round(max(0, end - frame_pos) * 8 / hdr["bitrate"], 6)
            info["duration_estimated"] = True
    return info


# ---------------------------------------------------------------- OGG
def _ogg_packets(r: _Reader, limit: int = 3):
    r.seek(0)
    packets, cur = [], b""
    for _ in range(64):
        h = r.read(27)
        if len(h) < 27 or h[:4] != b"OggS":
            break
        nseg = h[26]
        table = r.read(nseg)
        for ln in table:
            cur += r.read(ln)
            if ln < 255:
                packets.append(cur)
                cur = b""
                if len(packets) >= limit:
                    return packets
        if len(cur) > MAX_CHUNK:
            break
    return packets


def _parse_ogg(r: _Reader, info: dict, tags: dict, warns: list):
    pk = _ogg_packets(r)
    if not pk:
        warns.append("not an Ogg file")
        return None
    sr = chs = None
    first = pk[0]
    if first[:7] == b"\x01vorbis" and len(first) >= 16:
        chs = first[11]
        sr = struct.unpack("<I", first[12:16])[0]
        info["format"] = "ogg"
        if len(pk) > 1 and pk[1][:7] == b"\x03vorbis":
            _vorbis_comments(pk[1][7:], tags, warns)
    elif first[:8] == b"OpusHead" and len(first) >= 19:
        chs = first[9]
        sr = 48000
        info["format"] = "opus"
        if len(pk) > 1 and pk[1][:8] == b"OpusTags":
            _vorbis_comments(pk[1][8:], tags, warns)
    else:
        warns.append("unsupported Ogg codec")
        return None
    info.update({"sample_rate": sr, "channels": chs})
    # duration: granule position of the last page (read only the file tail)
    tail_len = min(r.size, 65536)
    r.seek(r.size - tail_len)
    tail = r.f.read(tail_len)
    j = tail.rfind(b"OggS")
    if j >= 0 and j + 14 <= len(tail) and sr:
        gran = struct.unpack("<q", tail[j + 6:j + 14])[0]
        if gran > 0:
            info["frames"] = gran
            info["duration_s"] = round(gran / sr, 6)
    return info


_PARSERS = {".wav": _parse_wav, ".wave": _parse_wav, ".aif": _parse_aiff,
            ".aiff": _parse_aiff, ".aifc": _parse_aiff, ".flac": _parse_flac,
            ".mp3": _parse_mp3, ".ogg": _parse_ogg, ".opus": _parse_ogg}


def read_audio(path: str, ext: str):
    """Return (AudioInfo | None, tags_raw, warnings). Never raises."""
    ext = (ext or os.path.splitext(path)[1]).lower()
    tags: dict = {}
    warns: list = []
    parser = _PARSERS.get(ext)
    if parser is None:
        return None, {}, ["metadata not parsed for %s files" % (ext or "unknown")]
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            r = _Reader(f, size)
            info = parser(r, {}, tags, warns)
    except (OSError, ValueError, struct.error, IndexError, KeyError,
            OverflowError, ZeroDivisionError) as e:
        warns.append("header parse error: %s" % e)
        info = None
    if info is not None:
        info = {k: v for k, v in info.items() if v is not None}
    return info, tags, warns

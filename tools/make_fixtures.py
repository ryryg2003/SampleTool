"""Build a disposable synthetic "Sample Packs" tree for tests.

OWNER: QA. Standard library only (struct/wave/gzip). Never point this at a real
collection: it refuses to write into a non-empty folder unless --force is given.

Usage:
    python tools/make_fixtures.py <dest_dir> [--large N] [--force]

<dest_dir> becomes the collection root (it plays the role of E:\\Sample Packs).
A ``fixture_manifest.json`` describing the expectations (duplicate groups,
kits, collisions, ...) is written NEXT TO the tree (in the parent folder of
<dest_dir>), never inside it. ``build()`` returns the same manifest dict for
tests that import this module.

All relative paths in the manifest use "/" and are relative to <dest_dir>.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import random
import struct
import sys
import wave

SR = 44100
# Fixed, deterministic timestamps (2024-01-01 .. ) so hash caches are stable.
BASE_MTIME = 1704067200


# ---------------------------------------------------------------------------
# Audio generators
# ---------------------------------------------------------------------------
def _pcm16(samples) -> bytes:
    return b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in samples)


def sine(freq: float, dur: float, amp: float = 0.5, decay: float = 0.0, sr: int = SR):
    n = max(1, int(sr * dur))
    out = []
    for i in range(n):
        t = i / sr
        env = math.exp(-decay * t) if decay else 1.0
        out.append(amp * env * math.sin(2 * math.pi * freq * t))
    return out


def noise(dur: float, seed: int, amp: float = 0.4, decay: float = 0.0, sr: int = SR):
    rng = random.Random(seed)
    n = max(1, int(sr * dur))
    out = []
    for i in range(n):
        env = math.exp(-decay * i / sr) if decay else 1.0
        out.append(amp * env * (rng.random() * 2 - 1))
    return out


def kick(seed: int = 0, dur: float = 0.25):
    """Pitch-dropping sine. ``seed`` varies the start pitch so content differs."""
    n = int(SR * dur)
    f0 = 150 + seed * 7
    out, phase = [], 0.0
    for i in range(n):
        t = i / SR
        f = 45 + (f0 - 45) * math.exp(-t * 30)
        phase += 2 * math.pi * f / SR
        out.append(0.9 * math.exp(-t * 12) * math.sin(phase))
    return out


_PC = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8,
       "A": 9, "A#": 10, "B": 11}


def beat_loop(bpm: float, bars: int, key: str, offset_s: float = 0.0, sr: int = 22050):
    """Musical loop with known tempo and key: kick on every beat, offbeat hat,
    sustained triad of ``key`` ("Am", "F#") and a root bass note. ``offset_s``
    prepends silence (an off-grid trim)."""
    minor = key.endswith("m")
    root = _PC[key[:-1] if minor else key]
    triad = [root, root + (3 if minor else 4), root + 7]
    freqs = [440.0 * 2 ** ((60 + pc - 69) / 12) for pc in triad]  # C4 octave
    bass_f = 440.0 * 2 ** ((36 + root - 69) / 12)
    beat = 60.0 / bpm
    n = int(round(sr * beat * 4 * bars))
    rng = random.Random(bars * 1000 + int(bpm))
    out = []
    for i in range(n):
        t = i / sr
        tb = t % beat
        th = (t + beat / 2) % beat
        k = math.sin(2 * math.pi * (50 + 90 * math.exp(-tb * 40)) * tb) * math.exp(-tb * 18)
        h = (rng.random() * 2 - 1) * math.exp(-th * 60) * 0.25
        pad = sum(math.sin(2 * math.pi * f * t) for f in freqs) * 0.12
        bass = math.sin(2 * math.pi * bass_f * t) * 0.15
        out.append(0.55 * k + h + pad + bass)
    return [0.0] * int(sr * offset_s) + out


def wav_bytes(samples, sr: int = SR, channels: int = 1, extra_chunks: bytes = b"") -> bytes:
    """16-bit PCM WAV via the ``wave`` module, optional extra RIFF chunks appended."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        data = _pcm16(samples)
        if channels == 2:
            data = b"".join(data[i:i + 2] * 2 for i in range(0, len(data), 2))
        w.writeframes(data)
    raw = buf.getvalue()
    if extra_chunks:
        raw = raw + extra_chunks
        raw = raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:]
    return raw


def _chunk(cid: bytes, payload: bytes) -> bytes:
    pad = b"\0" if len(payload) % 2 else b""
    return cid + struct.pack("<I", len(payload)) + payload + pad


def list_info(**tags) -> bytes:
    """RIFF LIST/INFO chunk. Keys are 4-char INFO ids, e.g. INAM, IART, IGNR, ICMT."""
    body = b"INFO"
    for k, v in tags.items():
        body += _chunk(k.encode("ascii"), v.encode("utf-8") + b"\0")
    return _chunk(b"LIST", body)


NOTE_NUM = {"C": 48, "C#": 49, "D": 50, "D#": 51, "E": 52, "F": 53, "F#": 54, "G": 55,
            "G#": 56, "A": 57, "A#": 58, "B": 59}


def acid(bpm: float | None, root: str | None = None, one_shot: bool = False,
         beats: int = 8) -> bytes:
    """ACID chunk: flags, root note, ?, ?, beats, meter denom, meter num, tempo."""
    flags = 0x01 if one_shot else 0x00
    note = 0
    if root:
        flags |= 0x02
        note = NOTE_NUM[root]
    payload = struct.pack("<IHHfIHHf", flags, note, 0x8000, 0.0, beats, 4, 4,
                          float(bpm or 0.0))
    return _chunk(b"acid", payload)


def _ext80(x: float) -> bytes:
    """IEEE 754 80-bit extended (big-endian) for AIFF sample rate."""
    if x == 0:
        return b"\0" * 10
    exp = int(math.floor(math.log2(x)))
    mant = int(x / (2 ** exp) * (1 << 63))
    return struct.pack(">HQ", exp + 16383, mant)


def aiff_bytes(samples, sr: int = SR) -> bytes:
    data = b"".join(struct.pack(">h", max(-32767, min(32767, int(s * 32767)))) for s in samples)
    comm = struct.pack(">hIh", 1, len(samples), 16) + _ext80(float(sr))
    ssnd = struct.pack(">II", 0, 0) + data
    body = b"AIFF"
    for cid, p in ((b"COMM", comm), (b"SSND", ssnd)):
        body += cid + struct.pack(">I", len(p)) + p + (b"\0" if len(p) % 2 else b"")
    return b"FORM" + struct.pack(">I", len(body)) + body


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def mp3_bytes(frames: int = 12, **id3) -> bytes:
    """ID3v2.3 tag (TBPM/TKEY/TCON/TPE1/TIT2) + silent MPEG-1 Layer III frames."""
    tag = b""
    for fid, val in id3.items():
        payload = b"\x00" + val.encode("latin-1")
        tag += fid.encode("ascii") + struct.pack(">I", len(payload)) + b"\0\0" + payload
    header = b"ID3" + bytes([3, 0, 0]) + _syncsafe(len(tag))
    # MPEG1 L3 128 kbps 44.1 kHz, no padding, mono: 144*128000/44100 = 417 bytes
    frame = b"\xff\xfb\x90\xc4" + b"\0" * 413
    return header + tag + frame * frames


def midi_bytes(notes=(60, 64, 67), bpm: float = 120.0) -> bytes:
    tempo = int(60_000_000 / bpm)
    ev = b"\x00\xff\x51\x03" + tempo.to_bytes(3, "big")
    for n in notes:
        ev += bytes([0x00, 0x90, n, 100]) + b"\x83\x60" + bytes([0x80, n, 0])
    ev += b"\x00\xff\x2f\x00"
    trk = b"MTrk" + struct.pack(">I", len(ev)) + ev
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, 480) + trk


def als_bytes(refs: list[str], name: str) -> bytes:
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<Ableton MajorVersion="5" MinorVersion="11.0" Creator="Fixture">',
           f'  <LiveSet><Name Value="{name}"/>']
    for r in refs:
        xml.append(f'    <SampleRef><FileRef><Path Value="{r}"/>'
                   f'<RelativePath Value="{r}"/></FileRef></SampleRef>')
    xml.append("  </LiveSet>\n</Ableton>\n")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
        g.write("\n".join(xml).encode("utf-8"))
    return buf.getvalue()


def flp_bytes(seed: int) -> bytes:
    rng = random.Random(seed)
    return b"FLhd" + struct.pack("<IHHH", 6, 0, 4, 96) + b"FLdt" + struct.pack("<I", 64) + \
        bytes(rng.randrange(256) for _ in range(64))


def fxp_bytes(name: str) -> bytes:
    prog = name.encode("ascii")[:28].ljust(28, b"\0")
    params = b"".join(struct.pack(">f", i / 16) for i in range(16))
    body = b"FxCk" + struct.pack(">IIII", 1, 0x46697874, 1, 16) + prog + params
    return b"CcnK" + struct.pack(">I", len(body)) + body


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
# 1x1 baseline JPEG-ish bytes: SOI, APP0/JFIF, EOI. Not decoded by tests.
JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xd9"


def rex_bytes() -> bytes:
    body = b"REX2HEAD" + struct.pack(">I", 16) + bytes(range(16)) + b"SLCE" + struct.pack(">I", 8) + b"\0" * 8
    return b"CAT " + struct.pack(">I", len(body)) + body


# ---------------------------------------------------------------------------
# Tree definition
# ---------------------------------------------------------------------------
P1 = "Nightfall Audio - Midnight Trap"
P2 = "Lofi Lab - Dusty Keys"
P3 = "Prime Grooves - Bass Science"
P4 = "SoundWorks - FX Toolkit"
P5 = "Vox Supply - Vocal Hooks"
P6 = "Free Samples"  # no maker
MIDNIGHT = f"{P1}/Construction Kits/Midnight 140 Am"
SUNSET = f"{P2}/Construction Kits/Sunset 90 F#m"


def _content() -> dict:
    """rel_path -> bytes. Deterministic."""
    f: dict[str, bytes] = {}

    kick_a = wav_bytes(kick(0))
    kick_b = wav_bytes(kick(5))
    kick_c = wav_bytes(kick(9, 0.3))
    kick_lib = wav_bytes(kick(13, 0.2))
    hat = wav_bytes(noise(0.08, 11, decay=60))
    clap = wav_bytes(noise(0.15, 21, decay=25))
    kit_kick = wav_bytes(kick(3, 0.35), extra_chunks=list_info(INAM="Kit Kick"))
    kit_bass = wav_bytes(sine(55, 1.0, 0.6), extra_chunks=acid(140, "A", beats=4))
    impact = noise(0.5, 31, 0.6, decay=6)
    impact_near = list(impact)
    impact_near[1000] = -impact_near[1000] + 0.01  # one sample differs, same size

    # Pack 1: Nightfall Audio - Midnight Trap
    f[f"{P1}/One Shots/Kicks/Kick 01.wav"] = kick_a
    f[f"{P1}/One Shots/Snares/Snare Tight.wav"] = wav_bytes(noise(0.2, 2, decay=18))
    f[f"{P1}/One Shots/Hats/Closed Hat 03.wav"] = hat
    f[f"{P1}/One Shots/808s/808_C_Long.wav"] = wav_bytes(sine(32.70, 1.8, 0.8, decay=0.8))
    f[f"{P1}/Loops/Drum Loop 140bpm Am.wav"] = wav_bytes(
        noise(1.2, 3, decay=0.5), extra_chunks=acid(140, "A", beats=8)
        + list_info(INAM="Drum Loop", IART="Nightfall Audio", IGNR="Trap"))
    f[f"{MIDNIGHT}/Stems/Kick.wav"] = kit_kick
    f[f"{MIDNIGHT}/Stems/Bass.wav"] = kit_bass
    f[f"{MIDNIGHT}/Stems/Melody.wav"] = wav_bytes(sine(440, 1.0, 0.3), extra_chunks=acid(140, "A"))
    f[f"{MIDNIGHT}/MIDI/Melody.mid"] = midi_bytes((69, 72, 76), 140)
    f[f"{MIDNIGHT}/MIDI/Bass.mid"] = midi_bytes((45, 45, 48), 140)
    f[f"{MIDNIGHT}/Project.als"] = als_bytes(
        ["Stems/Kick.wav", "Stems/Bass.wav", "Stems/Melody.wav"], "Midnight 140 Am")
    f[f"{MIDNIGHT}/Artwork.jpg"] = JPG + b"midnight"
    f[f"{P1}/License.pdf"] = PDF

    # Pack 2: Lofi Lab - Dusty Keys
    f[f"{P2}/Loops/Dark Keys 90 BPM F#m.wav"] = wav_bytes(
        sine(185.0, 1.4, 0.4), extra_chunks=list_info(IGNR="Lo-Fi", ICMT="keys loop"))
    f[f"{P2}/One Shots/Kick 01.wav"] = kick_b  # same name, different content
    f[f"{P2}/One Shots/Vocal Chop Hey.wav"] = wav_bytes(sine(300, 0.3, 0.5, decay=8))
    f[f"{P2}/Presets/Warm Pad.fxp"] = fxp_bytes("Warm Pad")
    f[f"{P2}/readme.txt"] = b"Dusty Keys by Lofi Lab. Royalty free.\r\n"
    f[f"{SUNSET}/Stems/Keys.wav"] = wav_bytes(sine(370, 1.0, 0.3), extra_chunks=acid(90, "F#"))
    f[f"{SUNSET}/Stems/Kick.wav"] = kit_kick  # byte-identical to Midnight kit stem
    f[f"{SUNSET}/MIDI/Keys.mid"] = midi_bytes((66, 69, 73), 90)
    f[f"{SUNSET}/Project.flp"] = flp_bytes(7)
    f[f"{SUNSET}/Artwork.jpg"] = JPG + b"sunset"

    # Pack 3: Prime Grooves - Bass Science
    f[f"{P3}/Loops/Bass Loop 140 Am.wav"] = kit_bass  # identical to Midnight kit stem
    f[f"{P3}/One Shots/Bass Hit G.aif"] = aiff_bytes(sine(98, 0.6, 0.6, decay=4))
    f[f"{P3}/One Shots/Sub Bass C.wav"] = wav_bytes(sine(65.4, 0.9, 0.7, decay=2))
    f[f"{P3}/One Shots/Hat Closed Bright.wav"] = hat  # renamed duplicate of P1 hat

    # Pack 4: SoundWorks - FX Toolkit
    f[f"{P4}/FX/Riser 01.wav"] = wav_bytes([s * i / (SR * 1.5) for i, s in enumerate(noise(1.5, 41))])
    f[f"{P4}/FX/Impact Big.wav"] = wav_bytes(impact)
    f[f"{P4}/FX/Impact Big (1).wav"] = wav_bytes(impact_near)  # near-duplicate
    f[f"{P4}/Vocals/Vocal Chant 120bpm.mp3"] = mp3_bytes(
        TBPM="120", TKEY="Cm", TCON="Hip Hop", TPE1="SoundWorks", TIT2="Vocal Chant")
    f[f"{P4}/Weird/strange.xyz"] = b"XYZ\0" + bytes(range(64))
    f[f"{P4}/Legacy/Break 100.rex"] = rex_bytes()

    # Pack 5: Vox Supply - Vocal Hooks
    f[f"{P5}/Vocal Hook 128 BPM Gm.wav"] = wav_bytes(sine(196, 1.5, 0.4), channels=2)
    f[f"{P5}/Kick Punchy.wav"] = kick_a  # renamed duplicate of P1 Kick 01
    f[f"{P5}/Artwork.jpg"] = JPG + b"vox"

    # Pack 6: Free Samples (no manufacturer)
    f[f"{P6}/KICK 01.wav"] = kick_c  # case-variant same name, different content
    f[f"{P6}/drums/kick_hard.wav"] = kick_a  # another renamed duplicate
    f[f"{P6}/drums/snare_02.wav"] = wav_bytes(noise(0.18, 52, decay=20))
    f[f"{P6}/clap.wav"] = clap  # identical to an existing library file
    f[f"{P6}/perc loop 95bpm.wav"] = wav_bytes(noise(0.8, 61, 0.3))

    # Existing library (dest_root = <root>/Samples)
    f["Samples/One-Shots/Drums/Kick 01.wav"] = kick_lib  # name collision, different content
    f["Samples/One-Shots/Drums/Clap Existing.wav"] = clap
    f["Samples/Catalog/old.csv"] = b"path,bpm\r\nold.wav,120\r\n"  # must be ignored
    return f


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build(dest_dir: str, large: int = 0, force: bool = False,
          write_manifest: bool = True) -> dict:
    dest_dir = os.path.abspath(dest_dir)
    if os.path.isdir(dest_dir) and os.listdir(dest_dir) and not force:
        raise SystemExit(f"Refusing to write into non-empty folder {dest_dir} (use --force)")
    files = _content()
    bulk: list[str] = []
    packs = [P1, P2, P3, P4, P5, P6]
    names = ["Perc", "Rim", "Tom", "Shaker", "Snap", "Hat Open", "Kick", "Snare"]
    for i in range(large):
        rel = f"{packs[i % len(packs)]}/Bulk/{names[i % len(names)]} {i:05d}.wav"
        files[rel] = wav_bytes(noise(0.02, 100000 + i, decay=50))
        bulk.append(rel)

    for n, (rel, data) in enumerate(sorted(files.items())):
        p = os.path.join(dest_dir, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
        t = BASE_MTIME + n * 60
        os.utime(p, ns=(t * 10**9, t * 10**9))

    sha = {rel: _sha(b) for rel, b in files.items()}
    incoming = sorted(r for r in files if not r.startswith("Samples/"))
    library = sorted(r for r in files if r.startswith("Samples/") and not r.startswith("Samples/Catalog/"))
    groups: dict[str, list] = {}
    for r in incoming + library:
        groups.setdefault(sha[r], []).append(r)
    dup_groups = [{"sha256": h, "paths": sorted(ps)} for h, ps in groups.items() if len(ps) > 1]

    def kit(name, root_rel, pack):
        mem = sorted(r for r in incoming if r.startswith(root_rel + "/"))
        return {"name": name, "root_rel": root_rel, "pack": pack, "members": mem,
                "members_rel_to_kit": sorted(m[len(root_rel) + 1:] for m in mem)}

    manifest = {
        "root": dest_dir,
        "dest_root": os.path.join(dest_dir, "Samples"),
        "packs": packs,
        "packs_with_maker": {P1: "Nightfall Audio", P2: "Lofi Lab", P3: "Prime Grooves",
                             P4: "SoundWorks", P5: "Vox Supply"},
        "pack_without_maker": P6,
        "incoming": incoming,
        "incoming_count": len(incoming),
        "library": library,
        "ignored": ["Samples/Catalog/old.csv"],
        "sha256": sha,
        "dup_groups": dup_groups,
        "renamed_loose_dups": sorted([f"{P1}/One Shots/Kicks/Kick 01.wav", f"{P5}/Kick Punchy.wav",
                                      f"{P6}/drums/kick_hard.wav"]),
        "renamed_loose_dups_2": sorted([f"{P1}/One Shots/Hats/Closed Hat 03.wav",
                                        f"{P3}/One Shots/Hat Closed Bright.wav"]),
        "kit_kit_dup": sorted([f"{MIDNIGHT}/Stems/Kick.wav", f"{SUNSET}/Stems/Kick.wav"]),
        "kit_loose_dup": {"kit_member": f"{MIDNIGHT}/Stems/Bass.wav",
                          "loose": f"{P3}/Loops/Bass Loop 140 Am.wav"},
        "library_dup": {"library": "Samples/One-Shots/Drums/Clap Existing.wav",
                        "incoming": f"{P6}/clap.wav"},
        "same_name_diff_content": sorted([f"{P1}/One Shots/Kicks/Kick 01.wav",
                                          f"{P2}/One Shots/Kick 01.wav", f"{P6}/KICK 01.wav"]),
        "collisions": [{"library": "Samples/One-Shots/Drums/Kick 01.wav",
                        "incoming": sorted([f"{P1}/One Shots/Kicks/Kick 01.wav",
                                            f"{P2}/One Shots/Kick 01.wav", f"{P6}/KICK 01.wav"])}],
        "near_dupes": [[f"{P4}/FX/Impact Big.wav", f"{P4}/FX/Impact Big (1).wav"]],
        "kits": [kit("Midnight 140 Am", MIDNIGHT, P1), kit("Sunset 90 F#m", SUNSET, P2)],
        "unknown_formats": [f"{P4}/Weird/strange.xyz"],
        "expected_meta": {
            f"{P1}/Loops/Drum Loop 140bpm Am.wav": {"bpm": 140, "key": "Am", "loop_type": "loop"},
            f"{P2}/Loops/Dark Keys 90 BPM F#m.wav": {"bpm": 90, "key": "F#m", "loop_type": "loop"},
            f"{P5}/Vocal Hook 128 BPM Gm.wav": {"bpm": 128, "key": "Gm"},
            f"{P4}/Vocals/Vocal Chant 120bpm.mp3": {"bpm": 120},
            f"{P1}/One Shots/808s/808_C_Long.wav": {"loop_type": "one-shot",
                                                    "category": "One-Shots/Bass & 808s"},
        },
        "durations": {f"{P1}/One Shots/808s/808_C_Long.wav": 1.8},
        "plain_one_shots": [f"{P1}/One Shots/Snares/Snare Tight.wav",
                            f"{P3}/One Shots/Sub Bass C.wav",
                            f"{P6}/drums/snare_02.wav",
                            f"{P2}/One Shots/Vocal Chop Hey.wav"],
        "bulk": bulk,
    }
    if write_manifest:
        mpath = os.path.join(os.path.dirname(dest_dir), "fixture_manifest.json")
        with open(mpath, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2)
        manifest["manifest_path"] = mpath
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dest_dir")
    ap.add_argument("--large", type=int, default=0, help="add N extra small WAVs")
    ap.add_argument("--force", action="store_true", help="allow a non-empty dest_dir")
    a = ap.parse_args(argv)
    m = build(a.dest_dir, a.large, a.force)
    print(f"Fixture: {m['root']}")
    print(f"  incoming files: {m['incoming_count']}  library files: {len(m['library'])}"
          f"  duplicate groups: {len(m['dup_groups'])}  kits: {len(m['kits'])}")
    print(f"  manifest: {m.get('manifest_path')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

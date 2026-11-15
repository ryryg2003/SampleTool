"""Tests for audio_meta, classify, kits, planner, export (stdlib unittest only)."""
import csv
import gzip
import hashlib
import os
import shutil
import struct
import tempfile
import unittest

from samplelib import audio_meta, classify, export, kits, planner
from samplelib.models import META_KEYS, classify_kind, item_id


# --------------------------------------------------------------- builders
def chunk(cid, body, le=True):
    fmt = "<I" if le else ">I"
    pad = b"\x00" if len(body) & 1 else b""
    return cid + struct.pack(fmt, len(body)) + body + pad


def make_wav(frames=4410, sr=44100, ch=2, bits=16, acid=None, info=None, smpl=False,
             extensible=False):
    align = ch * bits // 8
    if extensible:
        fmt = struct.pack("<HHIIHH", 0xFFFE, ch, sr, sr * align, align, bits)
        fmt += struct.pack("<HHI", 22, bits, 3) + struct.pack("<H", 1) + b"\x00" * 14
    else:
        fmt = struct.pack("<HHIIHH", 1, ch, sr, sr * align, align, bits)
    body = b"WAVE" + chunk(b"fmt ", fmt)
    if acid is not None:
        flags, root, beats, tempo = acid
        body += chunk(b"acid", struct.pack("<IHHfIHHf", flags, root, 0x8000, 0.0, beats,
                                           4, 4, tempo))
    if info:
        sub = b"INFO" + b"".join(chunk(k.encode(), v.encode() + b"\x00")
                                 for k, v in info.items())
        body += chunk(b"LIST", sub)
    if smpl:
        body += chunk(b"smpl", struct.pack("<9I", 0, 0, 22675, 60, 0, 0, 0, 1, 0) +
                      struct.pack("<6I", 0, 0, 0, frames - 1, 0, 0))
    body += chunk(b"data", b"\x00" * (frames * align))
    return b"RIFF" + struct.pack("<I", len(body)) + body


def ext80(v):
    import math
    if v == 0:
        return b"\x00" * 10
    m, e = math.frexp(v)   # v = m * 2**e, 0.5<=m<1
    mant = int(m * (1 << 64))
    return struct.pack(">HQ", e - 1 + 16383, mant)


def make_aiff(frames=48000, sr=48000, ch=1, bits=24, aifc=False):
    comm = struct.pack(">hIh", ch, frames, bits) + ext80(sr)
    if aifc:
        comm += b"NONE" + b"\x0enot compressed\x00"
    ssnd = struct.pack(">II", 0, 0) + b"\x00" * (frames * ch * bits // 8)
    body = (b"AIFC" if aifc else b"AIFF") + chunk(b"COMM", comm, le=False) + \
        chunk(b"NAME", b"Pad C", le=False) + chunk(b"SSND", ssnd, le=False)
    return b"FORM" + struct.pack(">I", len(body)) + body


def make_flac(sr=44100, ch=2, bps=16, total=88200, comments=None):
    x = (sr << 44) | ((ch - 1) << 41) | ((bps - 1) << 36) | total
    si = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + x.to_bytes(8, "big") + b"\x00" * 16
    out = b"fLaC" + bytes([0]) + len(si).to_bytes(3, "big") + si
    vendor = b"test"
    vc = struct.pack("<I", len(vendor)) + vendor
    items = [("%s=%s" % kv).encode() for kv in (comments or {}).items()]
    vc += struct.pack("<I", len(items)) + b"".join(struct.pack("<I", len(i)) + i for i in items)
    out += bytes([0x84]) + len(vc).to_bytes(3, "big") + vc
    return out + b"\xff\xf8" + b"\x00" * 100


def syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3_frame(fid, text, v4=True):
    body = b"\x03" + text.encode("utf-8")
    size = syncsafe(len(body)) if v4 else struct.pack(">I", len(body))
    return fid.encode() + size + b"\x00\x00" + body


def make_mp3(frames=100, v4=True, xing=None, tags=None):
    fr = b"".join(id3_frame(k, v, v4) for k, v in (tags or {}).items())
    tag = b"ID3" + bytes([4 if v4 else 3, 0, 0]) + syncsafe(len(fr)) + fr
    # MPEG1 Layer III, 128 kbps, 44100 Hz, stereo, no padding -> 417 bytes/frame
    hdr = b"\xff\xfb\x90\x00"
    flen = 144 * 128000 // 44100
    audio = b""
    for i in range(frames):
        f = bytearray(hdr + b"\x00" * (flen - 4))
        if i == 0 and xing is not None:
            off = 4 + 32
            f[off:off + 12] = b"Xing" + struct.pack(">II", 1, xing)
        audio += bytes(f)
    return tag + audio


def ogg_page(packets, seq, granule=0, header_type=0):
    segs, data = [], b""
    for p in packets:
        n = len(p)
        while n >= 255:
            segs.append(255)
            n -= 255
        segs.append(n)
        data += p
    return (b"OggS" + bytes([0, header_type]) + struct.pack("<qIII", granule, 1, seq, 0) +
            bytes([len(segs)]) + bytes(segs) + data)


def make_ogg(sr=44100, ch=2, samples=88200, comments=None):
    ident = b"\x01vorbis" + struct.pack("<IBIiiiB", 0, ch, sr, 0, 128000, 0, 0xB8) + b"\x01"
    vendor = b"x"
    items = [("%s=%s" % kv).encode() for kv in (comments or {}).items()]
    com = b"\x03vorbis" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(items))
    com += b"".join(struct.pack("<I", len(i)) + i for i in items) + b"\x01"
    return (ogg_page([ident], 0, 0, 2) + ogg_page([com, b"\x05vorbis"], 1) +
            ogg_page([b"\x00" * 50], 2, samples, 4))


class Collection:
    """Temp collection root with helpers to add files and InventoryItems."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="slo-test-")
        self.dest = os.path.join(self.root, "Samples")
        self.items = []

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add(self, rel, data=b"", origin="incoming", **over):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        st = os.stat(path)
        name = rel.rsplit("/", 1)[-1]
        ext = os.path.splitext(name)[1].lower()
        lib_rel = rel
        it = {"id": item_id(origin, lib_rel), "origin": origin, "rel_path": lib_rel,
              "abs_path": path, "name": name, "ext": ext, "size": st.st_size,
              "mtime_ns": st.st_mtime_ns, "file_id": "%d:%d" % (st.st_dev, st.st_ino),
              "nlink": 1, "kind": classify_kind(ext),
              "sha256": hashlib.sha256(data).hexdigest(), "hash_error": None,
              "pack": rel.split("/")[0] if origin == "incoming" and "/" in rel else None,
              "tags_raw": {}, "meta": {}, "audio": None, "kit_id": None, "excluded": False,
              "preview": "none", "warnings": [], "near_dupes": []}
        it.update(over)
        self.items.append(it)
        return it

    def by_name(self, name):
        return [i for i in self.items if i["name"] == name]


def uniq(n):
    """Distinct WAV payloads with the same header layout."""
    return make_wav(frames=4410 + n)


# --------------------------------------------------------------- audio_meta
class TestAudioMeta(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_wav_acid_info_smpl(self):
        p = self.write("a.wav", make_wav(frames=88200, acid=(0x02, 57, 8, 140.0),
                                         info={"INAM": "Loop", "IART": "Cymatics",
                                               "IGNR": "Trap", "ICMT": "hi"}, smpl=True))
        info, tags, warns = audio_meta.read_audio(p, ".wav")
        self.assertEqual(info["sample_rate"], 44100)
        self.assertEqual(info["channels"], 2)
        self.assertEqual(info["bits"], 16)
        self.assertAlmostEqual(info["duration_s"], 2.0, places=3)
        self.assertEqual(tags["IART"], "Cymatics")
        self.assertEqual(tags["IGNR"], "Trap")
        self.assertFalse(tags["acid"]["one_shot"])
        self.assertEqual(tags["acid"]["beats"], 8)
        self.assertAlmostEqual(tags["acid"]["tempo"], 140.0)
        self.assertEqual(tags["acid"]["root_note_name"], "A")
        self.assertEqual(len(tags["smpl"]["loops"]), 1)
        self.assertEqual(warns, [])

    def test_wav_extensible(self):
        p = self.write("b.wav", make_wav(bits=24, extensible=True, frames=44100))
        info, _t, _w = audio_meta.read_audio(p, ".wav")
        self.assertEqual(info["bits"], 24)
        self.assertAlmostEqual(info["duration_s"], 1.0, places=3)

    def test_aiff_80bit_rate(self):
        p = self.write("c.aif", make_aiff())
        info, tags, _w = audio_meta.read_audio(p, ".aif")
        self.assertEqual(info["sample_rate"], 48000)
        self.assertEqual(info["bits"], 24)
        self.assertAlmostEqual(info["duration_s"], 1.0, places=3)
        self.assertEqual(tags["NAME"], "Pad C")
        p = self.write("d.aifc", make_aiff(sr=44100, frames=44100, aifc=True))
        info, _t, _w = audio_meta.read_audio(p, ".aifc")
        self.assertEqual(info["sample_rate"], 44100)

    def test_flac(self):
        p = self.write("e.flac", make_flac(comments={"BPM": "128", "KEY": "F#m",
                                                     "GENRE": "House", "ARTIST": "Maker"}))
        info, tags, _w = audio_meta.read_audio(p, ".flac")
        self.assertEqual((info["sample_rate"], info["channels"], info["bits"]), (44100, 2, 16))
        self.assertAlmostEqual(info["duration_s"], 2.0)
        self.assertEqual(tags["BPM"], "128")
        self.assertEqual(tags["KEY"], "F#m")

    def test_mp3_id3v24_cbr(self):
        p = self.write("f.mp3", make_mp3(tags={"TBPM": "150", "TKEY": "Am", "TCON": "Drill",
                                               "TPE1": "Maker", "TIT2": "Title"}))
        info, tags, _w = audio_meta.read_audio(p, ".mp3")
        self.assertEqual(tags["TBPM"], "150")
        self.assertEqual(tags["TKEY"], "Am")
        self.assertEqual(info["sample_rate"], 44100)
        # 100 frames * 417 bytes * 8 / 128000
        self.assertAlmostEqual(info["duration_s"], 100 * 417 * 8 / 128000, places=2)

    def test_mp3_id3v23_xing(self):
        p = self.write("g.mp3", make_mp3(v4=False, xing=1000, tags={"TBPM": "90"}))
        info, tags, _w = audio_meta.read_audio(p, ".mp3")
        self.assertEqual(tags["TBPM"], "90")
        self.assertAlmostEqual(info["duration_s"], 1000 * 1152 / 44100, places=3)

    def test_ogg(self):
        p = self.write("h.ogg", make_ogg(comments={"BPM": "100", "GENRE": "Lofi"}))
        info, tags, _w = audio_meta.read_audio(p, ".ogg")
        self.assertEqual(info["sample_rate"], 44100)
        self.assertAlmostEqual(info["duration_s"], 2.0)
        self.assertEqual(tags["BPM"], "100")

    def test_malformed_never_raises(self):
        good = make_wav(acid=(0, 60, 4, 120.0), info={"INAM": "x"})
        for i, data in enumerate([b"", b"RIFF", good[:30], b"RIFF\xff\xff\xff\xffWAVEjunk",
                                  b"ID3\x04\x00\x00\x7f\x7f\x7f\x7f", b"fLaC\x00",
                                  b"FORM\x00\x00\x00\x04AIFF", b"OggS" + b"\x00" * 10]):
            for ext in (".wav", ".mp3", ".flac", ".aif", ".ogg"):
                p = self.write("bad%d%s" % (i, ext), data)
                info, tags, warns = audio_meta.read_audio(p, ext)
                self.assertIsInstance(warns, list)
        info, tags, warns = audio_meta.read_audio(self.write("x.m4a", b"zz"), ".m4a")
        self.assertIsNone(info)
        self.assertTrue(warns)
        info, tags, warns = audio_meta.read_audio(os.path.join(self.d, "missing.wav"), ".wav")
        self.assertIsNone(info)
        self.assertTrue(warns)

    def test_rf64(self):
        fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
        ds64 = struct.pack("<QQQI", 0, 16000, 8000, 0)
        body = b"WAVE" + chunk(b"ds64", ds64) + chunk(b"fmt ", fmt) + b"data" + \
            struct.pack("<I", 0xFFFFFFFF) + b"\x00" * 16000
        p = self.write("r.wav", b"RF64" + struct.pack("<I", 0xFFFFFFFF) + body)
        info, _t, _w = audio_meta.read_audio(p, ".wav")
        self.assertAlmostEqual(info["duration_s"], 1.0)


# --------------------------------------------------------------- parsing
class TestParsing(unittest.TestCase):
    def test_bpm(self):
        cases = {"Loop_140bpm_Am": 140, "Melody_140_F#m": 140, "Pad 90 BPM C#min": 90,
                 "BPM128 dmin": 128, "Drill 142.5bpm": 142.5, "Trap (150) Dark": 150}
        for t, v in cases.items():
            self.assertEqual(classify.parse_bpm(t), v, t)

    def test_bpm_strength(self):
        self.assertEqual(classify.bpm_candidates("Night_Kick_140_Am"), [(140.0, "keyed")])
        self.assertEqual(classify.bpm_candidates("Kick 120"), [(120.0, "bare")])
        self.assertEqual(classify.bpm_candidates("Snare_02_Am"), [])

    def test_bpm_false_positives(self):
        for t in ["Kick_01", "Snare 44100 24bit", "Oracle Vol 2", "Kit 120", "Clap 24-bit",
                  "Hat 48k", "Perc v2", "Track 2020", "Loop 300", "Kick 05", "Fx 100%"]:
            self.assertIsNone(classify.parse_bpm(t), t)
        self.assertIsNone(classify.parse_bpm("Loop 120 to 140"))  # ambiguous

    def test_key(self):
        cases = {"Melody_140_F#m": "F#m", "Pad C#min": "C#m", "Bass Bb Major 120": "Bb",
                 "Am Loop": "Am", "Lead_Cmaj_Dark": "C", "Keys D minor": "Dm",
                 "Loop 140 A": "A", "Gb_min_Vox": "Gbm", "chords f# minor": "F#m",
                 "Key of E": "E"}
        for t, v in cases.items():
            self.assertEqual(classify.parse_key(t), v, t)

    def test_key_false_positives(self):
        for t in ["Amazing Kick", "FM Synth", "Kick A", "Vocal_Ebony", "Bass Loop",
                  "Deep House Chord", "Abstract Fx", "Dead Snare", "I am here", "Cmon Clap",
                  "Fx B", "Bell"]:
            self.assertIsNone(classify.parse_key(t), t)


# --------------------------------------------------------------- classify
class TestClassify(unittest.TestCase):
    def setUp(self):
        self.c = Collection()

    def tearDown(self):
        self.c.cleanup()

    def meta(self, it, k):
        return it["meta"][k]

    def test_enrich_provenance(self):
        c = self.c
        a = c.add("Cymatics - Oracle Vol 2/Loops/Oracle_Melody_140_F#m.wav",
                  make_wav(frames=88200))
        b = c.add("Cymatics - Oracle Vol 2/One Shots/Kicks/Kick 01.wav", uniq(1))
        d = c.add("Pack_X/Stuff/Dark 808 Long C.wav", make_wav(frames=44100 * 4))
        e = c.add("Trap Essentials/Loops/Thing.wav",
                  make_wav(frames=88200, acid=(0, 0, 8, 150.0),
                           info={"IART": "SoundCo", "IGNR": "Drill"}))
        f = c.add("Trap Essentials/MIDI/Chords 140 Am.mid", b"MThd")
        g = c.add("Trap Essentials/readme.pdf", b"%PDF")
        h = c.add("Trap Essentials/Weird/zzz.wav", make_wav(frames=44100 * 2 + 3))
        progress = []
        classify.enrich(c.items, progress.append)
        self.assertTrue(any(p.get("phase") == "metadata" for p in progress))
        self.assertTrue(any(p.get("phase") == "classifying" for p in progress))
        for it in c.items:
            for k in META_KEYS:
                self.assertIn(k, it["meta"])
                self.assertEqual(set(it["meta"][k]), {"value", "source", "confidence"})
        self.assertEqual(self.meta(a, "bpm")["value"], 140)
        self.assertEqual(self.meta(a, "bpm")["source"], "filename")
        self.assertEqual(self.meta(a, "key")["value"], "F#m")
        self.assertEqual(self.meta(a, "loop_type")["value"], "loop")
        self.assertEqual(self.meta(a, "loop_type")["source"], "folder")
        self.assertEqual(self.meta(a, "category")["value"], "Loops/Melody Loops")
        self.assertEqual(self.meta(a, "manufacturer")["value"], "Cymatics")
        self.assertEqual(self.meta(b, "instrument")["value"], "kick")
        self.assertEqual(self.meta(b, "category")["value"], "One-Shots/Drums")
        self.assertIsNone(self.meta(b, "bpm")["value"])
        self.assertEqual(self.meta(d, "loop_type")["value"], "one-shot")
        self.assertEqual(self.meta(d, "category")["value"], "One-Shots/Bass & 808s")
        self.assertEqual(self.meta(d, "manufacturer")["value"], "Pack")
        self.assertEqual(self.meta(e, "loop_type")["source"], "tag")
        self.assertEqual(self.meta(e, "bpm")["value"], 150)
        self.assertEqual(self.meta(e, "bpm")["source"], "tag")
        self.assertEqual(self.meta(e, "manufacturer")["value"], "SoundCo")
        self.assertEqual(self.meta(e, "genre")["value"], "Drill")
        self.assertIsNone(self.meta(e, "category")["value"])  # no instrument evidence
        self.assertEqual(self.meta(f, "category")["value"], "Presets & MIDI")
        self.assertIsNone(self.meta(f, "loop_type")["value"])
        self.assertIsNone(self.meta(g, "category")["value"])
        self.assertIsNone(self.meta(h, "category")["value"])
        self.assertEqual(self.meta(c.items[3], "genre")["value"], "Drill")

    def test_clear_evidence_not_held(self):
        c = self.c
        cases = {
            "P/FX/Impact Big.wav": ("One-Shots/FX & Vocals", make_wav(frames=44100 * 3)),
            "P/FX/Riser 01.wav": ("One-Shots/FX & Vocals", make_wav(frames=44100 * 6)),
            "P/Vocals/Vocal Hook 128 BPM Gm.wav": ("Loops/Vocal & FX Loops", uniq(3)),
            "P/Vocals/Vocal Chant 120bpm.mp3": ("Loops/Vocal & FX Loops", make_mp3()),
            "P/Drums/Break 100.rex": ("Loops/Drum Loops", b"REX2"),
        }
        its = {rel: c.add(rel, data) for rel, (_cat, data) in cases.items()}
        classify.enrich(c.items, None)
        for rel, (cat, _d) in cases.items():
            m = its[rel]["meta"]["category"]
            self.assertEqual(m["value"], cat, rel)
            self.assertGreaterEqual(m["confidence"], 0.6, rel)

    def test_acid_one_shot_flag(self):
        it = self.c.add("P/Loops/Snare Loop Thing.wav", make_wav(acid=(1, 60, 0, 0.0)))
        classify.enrich(self.c.items, None)
        self.assertEqual(it["meta"]["loop_type"]["value"], "one-shot")
        self.assertEqual(it["meta"]["loop_type"]["source"], "tag")

    def test_near_dupes(self):
        a = self.c.add("A/Kick Hard.wav", make_wav(frames=4410))
        b = self.c.add("B/Kick Hard.wav", make_wav(frames=4411))   # +0.02 ms
        x = self.c.add("C/Kick Hard.wav", make_wav(frames=8000))
        classify.enrich(self.c.items, None)
        self.assertIn(b["id"], a["near_dupes"])
        self.assertNotIn(x["id"], a["near_dupes"])

    def test_manual_fields_preserved(self):
        it = self.c.add("P/zzz.wav", make_wav())
        it["meta"] = {"category": {"value": "One-Shots/Drums", "source": "manual",
                                   "confidence": None}}
        classify.enrich(self.c.items, None)
        self.assertEqual(it["meta"]["category"]["source"], "manual")


# --------------------------------------------------------------- kits
def build_kit_collection(c, als_refs=None):
    """A pack with two construction kits, loose one-shots and loose loops."""
    base = "Cymatics - Oracle/Construction Kits"
    for n, (kit, bpm, key) in enumerate((("Kit 1 - Night", 140, "Am"),
                                         ("Kit 2 - Day", 120, "C#m"))):
        root = "%s/%s" % (base, kit)
        tag = kit.split(" - ")[1]
        c.add("%s/%s_Drums_%dbpm_%s.wav" % (root, tag, bpm, key), uniq(n + 1))
        c.add("%s/%s_Bass_%dbpm_%s.wav" % (root, tag, bpm, key), uniq(n + 60))
        c.add("%s/%s_Keys_%dbpm_%s.wav" % (root, tag, bpm, key), uniq(n + 120))
        c.add("%s/MIDI/%s_Keys.mid" % (root, tag), b"MThd" + tag.encode())
        c.add("%s/cover.png" % root, b"PNG" + tag.encode())
    c.add("Cymatics - Oracle/One Shots/Kicks/Kick 01.wav", uniq(300))
    c.add("Cymatics - Oracle/One Shots/Snares/Snare 01.wav", uniq(301))
    if als_refs is not None:
        root = "%s/Kit 1 - Night" % base
        xml = "<Ableton>" + "".join(
            '<FileRef><RelativePathType Value="3"/><RelativePath Value="%s"/>'
            '<Path Value=""/></FileRef>' % r for r in als_refs) + "</Ableton>"
        c.add("%s/Night.als" % root, gzip.compress(xml.encode()))


class TestKits(unittest.TestCase):
    def setUp(self):
        self.c = Collection()

    def tearDown(self):
        self.c.cleanup()

    def test_detect(self):
        build_kit_collection(self.c)
        classify.enrich(self.c.items, None)
        ks = kits.detect_kits(self.c.items)
        self.assertEqual(sorted(k["name"] for k in ks), ["Kit 1 - Night", "Kit 2 - Day"])
        k1 = [k for k in ks if k["name"] == "Kit 1 - Night"][0]
        self.assertEqual(len(k1["member_ids"]), 5)       # 3 audio + midi + png
        self.assertEqual(k1["bpm"], 140)
        self.assertEqual(k1["key"], "Am")
        self.assertEqual(k1["manufacturer"], "Cymatics")
        self.assertTrue(k1["evidence"])
        self.assertFalse(k1["held"])
        kick = self.c.by_name("Kick 01.wav")[0]
        self.assertIsNone(kick["kit_id"])
        png = [i for i in self.c.items if i["rel_path"].endswith("Kit 1 - Night/cover.png")][0]
        self.assertEqual(png["kit_id"], k1["id"])

    def test_prefix_rule_without_kit_name(self):
        for i, part in enumerate(("Drums", "Bass", "Lead", "Pad")):
            self.c.add("Some Pack/Midnight/Midnight_%s_128_Gm.wav" % part, uniq(i))
        self.c.add("Some Pack/Other/Kick 01.wav", uniq(50))
        classify.enrich(self.c.items, None)
        ks = kits.detect_kits(self.c.items)
        self.assertEqual([k["root_rel"] for k in ks], ["Some Pack/Midnight"])

    def test_drum_kit_pack_is_not_a_kit(self):
        for i, n in enumerate(("Kick 01", "Snare 02", "Hat 03", "Clap 04")):
            self.c.add("Big Drum Kit/%s.wav" % n, uniq(i))
        classify.enrich(self.c.items, None)
        self.assertEqual(kits.detect_kits(self.c.items), [])

    def test_container_children_are_kits(self):
        base = "Lofi Lab - Chill Pack/Construction Kits"
        n = 0
        for kit in ("Sunset 90 F#m", "Midnight 140 Am"):
            for f in ("Stems/Kick.wav", "Stems/Snare.wav", "Stems/Keys.wav", "Full Mix.wav"):
                n += 1
                self.c.add("%s/%s/%s" % (base, kit, f), uniq(n))
            self.c.add("%s/%s/MIDI/Keys.mid" % (base, kit), kit.encode())
        classify.enrich(self.c.items, None)
        ks = kits.detect_kits(self.c.items)
        self.assertEqual(sorted(k["name"] for k in ks), ["Midnight 140 Am", "Sunset 90 F#m"])
        sunset = [k for k in ks if k["name"] == "Sunset 90 F#m"][0]
        self.assertEqual(sunset["bpm"], 90)
        self.assertEqual(sunset["key"], "F#m")
        kick = [i for i in self.c.items if i["rel_path"] == base + "/Sunset 90 F#m/Stems/Kick.wav"][0]
        self.assertEqual(kick["meta"]["bpm"]["value"], 90)
        self.assertEqual(kick["meta"]["loop_type"]["value"], "loop")
        self.assertEqual(kick["meta"]["loop_type"]["source"], "inference")
        p = planner.build_plan(self.c.items, ks, self.c.root, self.c.dest)
        op = [o for o in p["ops"] if o["item_id"] == kick["id"]][0]
        self.assertEqual(op["dest"], os.path.join(self.c.dest, "Complete Kits",
                                                  "Lofi Lab - Sunset 90 F#m", "Stems", "Kick.wav"))

    def test_single_kit_inside_container(self):
        base = "Nightfall Audio - Midnight Trap/Construction Kits"
        for i, f in enumerate(("Stems/Kick.wav", "Stems/Bass.wav", "Stems/Melody.wav")):
            self.c.add("%s/Midnight 140 Am/%s" % (base, f), uniq(100 + i))
        self.c.add("%s/Midnight 140 Am/MIDI/Bass.mid" % base, b"m")
        classify.enrich(self.c.items, None)
        ks = kits.detect_kits(self.c.items)
        self.assertEqual([k["name"] for k in ks], ["Midnight 140 Am"])
        self.assertEqual(ks[0]["root_rel"], base + "/Midnight 140 Am")

    def test_kit_folder_with_only_generic_children_is_the_kit(self):
        base = "Maker - Pack/Construction Kit"
        for i, f in enumerate(("Stems/Kick 140.wav", "Stems/Bass 140.wav", "Stems/Lead 140.wav")):
            self.c.add("%s/%s" % (base, f), uniq(200 + i))
        self.c.add(base + "/MIDI/Lead.mid", b"m")
        classify.enrich(self.c.items, None)
        ks = kits.detect_kits(self.c.items)
        self.assertEqual([k["root_rel"] for k in ks], [base])

    def test_als_ref_checks(self):
        build_kit_collection(self.c, als_refs=["Night_Drums_140bpm_Am.wav",
                                               "../../One Shots/Kicks/Kick 01.wav",
                                               "Samples/Imported/gone.wav"])
        self.c.add("Cymatics - Oracle/Construction Kits/Kit 2 - Day/Day.flp", b"FLhd")
        classify.enrich(self.c.items, None)
        ks = kits.detect_kits(self.c.items)
        k1 = [k for k in ks if k["name"] == "Kit 1 - Night"][0]
        k2 = [k for k in ks if k["name"] == "Kit 2 - Day"][0]
        als = [r for r in k1["ref_checks"] if r["check"].endswith("Night.als")][0]
        self.assertTrue(als["performed"])
        self.assertIn("1 outside", als["detail"])
        self.assertTrue(k1["held"])
        self.assertIn("outside the kit", k1["hold_reason"])
        flp = [r for r in k2["ref_checks"] if r["check"].endswith("Day.flp")][0]
        self.assertFalse(flp["performed"])
        self.assertEqual(flp["result"], "not-checked")
        self.assertFalse(k2["held"])


# --------------------------------------------------------------- planner
class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.c = Collection()

    def tearDown(self):
        self.c.cleanup()

    def plan(self):
        classify.enrich(self.c.items, None)
        ks = kits.detect_kits(self.c.items)
        return ks, planner.build_plan(self.c.items, ks, self.c.root, self.c.dest)

    def ops_by_item(self, plan):
        out = {}
        for o in plan["ops"]:
            self.assertNotIn(o["item_id"], out, "item has two ops")
            out[o["item_id"]] = o
        return out

    def test_every_incoming_item_one_op_and_determinism(self):
        build_kit_collection(self.c)
        self.c.add("Cymatics - Oracle/readme.txt", b"hi")
        self.c.add("Cymatics - Oracle/Weird/zzz.wav", uniq(400))
        self.c.add("Samples/One-Shots/Drums/Old Kick.wav", uniq(500), origin="library")
        ks, p = self.plan()
        ops = self.ops_by_item(p)
        inc = [i for i in self.c.items if i["origin"] == "incoming"]
        self.assertEqual(set(ops), {i["id"] for i in inc})
        lib = [i for i in self.c.items if i["origin"] == "library"][0]
        self.assertNotIn(lib["id"], ops)
        p2 = planner.build_plan(list(reversed(self.c.items)), list(reversed(ks)),
                                self.c.root, self.c.dest)
        self.assertEqual(p["plan_hash"], p2["plan_hash"])
        acts = [o["action"] for o in p["ops"]]
        self.assertEqual(acts, sorted(acts, key=lambda a: planner.ACTION_ORDER[a]))
        txt = [o for o in p["ops"] if o["source"].endswith("readme.txt")][0]
        self.assertEqual(txt["action"], "keep")
        self.assertEqual(txt["reason"], "not a sample; left in place")
        weird = [o for o in p["ops"] if o["source"].endswith("zzz.wav")][0]
        self.assertEqual(weird["action"], "hold")
        self.assertIn("unknown category", weird["reason"])
        kick = ops[self.c.by_name("Kick 01.wav")[0]["id"]]
        self.assertEqual(kick["action"], "move")
        self.assertEqual(kick["dest"], os.path.join(self.c.dest, "One-Shots", "Drums",
                                                    "Kick 01.wav"))
        midi = ops[self.c.by_name("Night_Keys.mid")[0]["id"]]
        self.assertEqual(midi["dest"], os.path.join(
            self.c.dest, "Complete Kits", "Cymatics - Kit 1 - Night", "MIDI", "Night_Keys.mid"))
        s = p["summary"]
        self.assertEqual(s["total_files"], len(inc))
        self.assertEqual(s["kits"], 2)
        self.assertEqual(sum(s["by_action"].values()), len(inc))

    def test_renamed_exact_dupes(self):
        data = uniq(7)
        a = self.c.add("PackA/One Shots/Kick Hard.wav", data)
        b = self.c.add("PackB/Drums/Kick Big.wav", data)
        c = self.c.add("PackC/Kicks/Kick Copy.wav", data)
        ks, p = self.plan()
        ops = self.ops_by_item(p)
        self.assertEqual(ops[a["id"]]["action"], "move")
        self.assertEqual(ops[b["id"]]["action"], "keep")
        self.assertEqual(ops[c["id"]]["action"], "keep")
        self.assertEqual(ops[b["id"]]["canonical"], ops[a["id"]]["dest"])
        self.assertEqual(p["summary"]["duplicate_groups"], 1)
        self.assertEqual(p["summary"]["duplicate_bytes_avoided"], 2 * len(data))

    def test_kit_member_dupe_of_loose_becomes_canonical_and_link(self):
        build_kit_collection(self.c)
        night_drums = [i for i in self.c.items if i["name"].startswith("Night_Drums")][0]
        with open(night_drums["abs_path"], "rb") as f:
            data = f.read()
        loose = self.c.add("Cymatics - Oracle/Loops/Drum Loop 140bpm.wav", data)
        # same content inside the other kit as well
        other = self.c.add("Cymatics - Oracle/Construction Kits/Kit 2 - Day/Extra Drums.wav",
                           data)
        ks, p = self.plan()
        ops = self.ops_by_item(p)
        canon = ops[night_drums["id"]]
        self.assertEqual(canon["action"], "move")
        self.assertEqual(ops[loose["id"]]["action"], "keep")
        self.assertEqual(ops[loose["id"]]["canonical"], canon["dest"])
        ln = ops[other["id"]]
        self.assertEqual(ln["action"], "link")
        self.assertEqual(ln["link_to"], canon["dest"])
        self.assertEqual(ln["depends_on"], canon["id"])
        idx = [o["id"] for o in p["ops"]]
        self.assertLess(idx.index(canon["id"]), idx.index(ln["id"]))

    def test_library_canonical(self):
        data = uniq(9)
        lib = self.c.add("Samples/One-Shots/Drums/Snare A.wav", data, origin="library")
        inc = self.c.add("PackA/Snares/Snare A copy.wav", data)
        ks, p = self.plan()
        o = self.ops_by_item(p)[inc["id"]]
        self.assertEqual(o["action"], "keep")
        self.assertEqual(o["canonical"], lib["abs_path"])

    def test_name_collision_suffix(self):
        self.c.add("Samples/One-Shots/Drums/Kick 01.wav", uniq(1), origin="library")
        a = self.c.add("PackA/Kicks/Kick 01.wav", uniq(2))
        b = self.c.add("PackB/Kicks/Kick 01.wav", uniq(3))
        d = self.c.add("PackB/Kicks2/kick 01.wav", uniq(4))
        ks, p = self.plan()
        ops = self.ops_by_item(p)
        base = os.path.join(self.c.dest, "One-Shots", "Drums")
        self.assertEqual(ops[a["id"]]["dest"], os.path.join(base, "Kick 01 [PackA].wav"))
        self.assertEqual(ops[b["id"]]["dest"], os.path.join(base, "Kick 01 [PackB].wav"))
        self.assertEqual(ops[d["id"]]["dest"], os.path.join(base, "kick 01 [PackB 2].wav"))
        for x in (a, b, d):
            self.assertIn("name-collision", ops[x["id"]]["flags"])
        self.assertEqual(p["summary"]["collisions"], 3)

    def test_held_reasons(self):
        build_kit_collection(self.c, als_refs=["../../One Shots/Kicks/Kick 01.wav"])
        ex = self.c.add("PackA/Kicks/Kick X.wav", uniq(11), excluded=True)
        he = self.c.add("PackA/Kicks/Kick Y.wav", uniq(12))
        ks, _p = self.plan()
        he["sha256"], he["hash_error"] = None, "permission denied"
        lo = self.c.add("PackA/Kicks/Kick Z.wav", uniq(13))
        classify.enrich([lo], None)
        lo["meta"]["category"]["confidence"] = 0.3
        p = planner.build_plan(self.c.items, ks, self.c.root, self.c.dest)
        ops = self.ops_by_item(p)
        self.assertEqual(ops[ex["id"]]["action"], "exclude")
        self.assertEqual(ops[he["id"]]["action"], "hold")
        self.assertIn("hash error", ops[he["id"]]["reason"])
        self.assertEqual(ops[lo["id"]]["action"], "hold")
        self.assertIn("low confidence", ops[lo["id"]]["reason"])
        night = [i for i in self.c.items if i["name"].startswith("Night_Bass")][0]
        self.assertEqual(ops[night["id"]]["action"], "hold")
        self.assertIn("outside the kit", ops[night["id"]]["reason"])
        self.assertEqual(p["summary"]["held"], sum(1 for o in p["ops"] if o["action"] == "hold"))

    def test_sanitize(self):
        self.assertEqual(planner.sanitize('A<b>:c"?.'), "A_b__c__")
        self.assertEqual(planner.sanitize("CON"), "CON_")
        self.assertEqual(planner.sanitize("com1.wav"), "com1_.wav")
        self.assertEqual(planner.sanitize("Kit. . "), "Kit")
        kit = {"name": "Kit: 1?", "manufacturer": None, "pack": "Pack|X", "root_rel": "a"}
        self.assertEqual(planner.kit_dir_name(kit), "Pack_X - Kit_ 1_")
        kit = {"name": "Maker - Night Kit", "manufacturer": "Maker", "pack": "Maker - Night Kit"}
        self.assertEqual(planner.kit_dir_name(kit), "Maker - Night Kit")


# --------------------------------------------------------------- export
class TestExport(unittest.TestCase):
    def setUp(self):
        self.c = Collection()

    def tearDown(self):
        self.c.cleanup()

    def simulate(self, plan):
        """Minimal executor stand-in: hard link moves/links, record results."""
        results = []
        for o in plan["ops"]:
            if o["action"] == "move":
                os.makedirs(os.path.dirname(o["dest"]), exist_ok=True)
                os.link(o["source"], o["dest"])
                os.unlink(o["source"])
                st = "done"
            elif o["action"] == "link":
                os.makedirs(os.path.dirname(o["dest"]), exist_ok=True)
                os.link(o["link_to"], o["dest"])
                st = "done"
            else:
                st = "held" if o["action"] == "hold" else "skipped"
            results.append({"op_id": o["id"], "action": o["action"], "status": st,
                            "source": o["source"], "dest": o.get("dest"),
                            "timestamp": "2026-01-01T00:00:00", "error": None})
        return results

    def test_reports_and_verify(self):
        build_kit_collection(self.c)
        night = [i for i in self.c.items if i["name"].startswith("Night_Drums")][0]
        with open(night["abs_path"], "rb") as f:
            data = f.read()
        self.c.add("Cymatics - Oracle/Construction Kits/Kit 2 - Day/Extra Drums.wav", data)
        self.c.add("Other/Loops/Drum Loop 140bpm.wav", data)
        self.c.add("Other/readme.txt", b"x")
        classify.enrich(self.c.items, None)
        ks = kits.detect_kits(self.c.items)
        plan = planner.build_plan(self.c.items, ks, self.c.root, self.c.dest)
        results = self.simulate(plan)
        cat = os.path.join(self.c.dest, "Catalog")
        out = export.write_reports(cat, self.c.items, ks, plan, results)
        self.assertEqual(set(out), {"Sample_Catalog.csv", "Source_Path_Index.csv",
                                    "Duplicate_Report.csv", "Organization_Report.csv"})

        def read(name):
            with open(out[name], encoding="utf-8") as f:
                txt = f.read()
            self.assertTrue(txt.startswith("﻿"))
            return list(csv.DictReader(txt[1:].splitlines()))

        rows = read("Sample_Catalog.csv")
        for col in ("original_filename", "final_path", "category", "instrument", "bpm", "key",
                    "genre", "duration_s", "pack", "manufacturer", "kit", "sha256", "tags",
                    "bpm_source", "bpm_confidence", "category_source"):
            self.assertIn(col, rows[0])
        kick = [r for r in rows if r["original_filename"] == "Kick 01.wav"][0]
        self.assertEqual(kick["category"], "One-Shots/Drums")
        idx = read("Source_Path_Index.csv")
        self.assertEqual(len(idx), len(self.c.items))
        st = {r["status"] for r in idx}
        self.assertTrue({"moved", "hard-linked", "kept-duplicate", "kept"} <= st, st)
        self.assertTrue(all(r["verified"] == "yes" for r in idx if r["status"] == "moved"))
        dup = read("Duplicate_Report.csv")
        disp = {r["disposition"] for r in dup}
        self.assertIn("hard link to canonical", disp)
        self.assertIn("redundant copy left in place", disp)
        org = read("Organization_Report.csv")
        self.assertEqual(len(org), len(plan["ops"]))
        for col in ("source", "destination", "timestamp", "result", "error"):
            self.assertIn(col, org[0])
        v = export.verify_library(self.c.items, ks, plan, results, check_hash=True)
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["bytes_freed"], 0)
        self.assertEqual(v["bytes_avoided"], 2 * len(data))
        self.assertEqual(v["unique_hashes"], v["physical_files"])
        self.assertEqual(v["library_paths"], v["physical_files"] + 1)  # one hard link
        # a missing kit file is reported
        mv = [o for o in plan["ops"] if o["action"] == "move" and o["kit_id"]][0]
        os.unlink(mv["dest"])
        v = export.verify_library(self.c.items, ks, plan, results)
        self.assertFalse(v["ok"])
        self.assertTrue(v["kits_incomplete"])


if __name__ == "__main__":
    unittest.main()

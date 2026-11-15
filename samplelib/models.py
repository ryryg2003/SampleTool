"""Shared types and constants for Sample Library Organizer.

OWNER: lead. Other modules import from here; do not edit without the lead.

All records are plain JSON-serializable dicts so they can cross the HTTP API
and be persisted without conversion. The TypedDicts below document the shape.
Python 3.10+ standard library only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, Optional, TypedDict

# --------------------------------------------------------------------------
# Destination layout (relative to dest_root, which is <root>/Samples)
# --------------------------------------------------------------------------
ONE_SHOTS = "One-Shots"
LOOPS = "Loops"
KITS_DIR = "Complete Kits"
PRESETS_MIDI = "Presets & MIDI"
CATALOG_DIR = "Catalog"
DEST_DIRNAME = "Samples"

# Category keys are the relative destination folder, "/" separated.
CATEGORIES: list[str] = [
    "One-Shots/Drums",
    "One-Shots/Bass & 808s",
    "One-Shots/Instruments",
    "One-Shots/FX & Vocals",
    "Loops/Drum Loops",
    "Loops/Melody Loops",
    "Loops/Bass Loops",
    "Loops/Vocal & FX Loops",
    "Presets & MIDI",
]
UNSORTED = "Unsorted"  # category value when unknown; such items are HELD, not moved

# File kinds
Kind = Literal["audio", "midi", "project", "preset", "document", "image",
               "archive", "other"]

AUDIO_EXTS = {".wav", ".aif", ".aiff", ".aifc", ".opus", ".flac", ".mp3", ".ogg", ".m4a", ".wv", ".rex", ".rx2"}
MIDI_EXTS = {".mid", ".midi"}
PROJECT_EXTS = {".als", ".flp", ".logicx", ".ptx", ".cpr", ".rpp", ".song", ".bwproject", ".aup3", ".reason"}
PRESET_EXTS = {".fxp", ".fxb", ".nmsv", ".vital", ".vitalbank", ".h2p", ".adg", ".adv", ".agr",
               ".nki", ".nkm", ".sfz", ".sf2", ".exs", ".fst", ".mse", ".spf", ".ksd", ".xpm"}
DOCUMENT_EXTS = {".pdf", ".txt", ".rtf", ".md", ".html", ".htm", ".nfo", ".doc", ".docx"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz"}
# Formats the browser <audio> element plays natively in Edge/Chrome.
BROWSER_PLAYABLE = {".wav", ".mp3", ".ogg", ".opus", ".flac", ".m4a"}

# Metadata provenance
Source = Literal["tag", "filename", "folder", "inference", "analysis", "manual"]


class Field(TypedDict):
    value: Any              # None means unknown
    source: Optional[Source]
    confidence: Optional[float]  # 0..1, None when not applicable (tag/manual)


def field(value: Any = None, source: Optional[str] = None,
          confidence: Optional[float] = None) -> Field:
    return {"value": value, "source": source, "confidence": confidence}


META_KEYS = ["category", "instrument", "loop_type", "bpm", "key", "genre", "tags",
             "manufacturer"]
# loop_type: "one-shot" | "loop" | None
# bpm: float | None ; key: str like "F#m" / "C" | None ; tags: list[str]


class AudioInfo(TypedDict, total=False):
    format: str          # "wav", "aiff", "flac", "mp3", "ogg", ...
    sample_rate: int
    channels: int
    bits: int
    duration_s: float
    frames: int


class InventoryItem(TypedDict, total=False):
    id: str              # stable: item_id(rel_path)
    origin: Literal["incoming", "library"]  # library = already under Samples\
    rel_path: str        # relative to root, "/" separated
    abs_path: str        # native absolute path
    name: str            # filename with extension
    ext: str             # lowercased, with dot
    size: int
    mtime_ns: int
    file_id: Optional[str]   # "st_dev:st_ino" (NTFS file index on Windows)
    nlink: int
    kind: str            # Kind
    sha256: Optional[str]
    hash_error: Optional[str]
    pack: Optional[str]      # first path component under root (None for root-level files)
    audio: Optional[AudioInfo]
    tags_raw: dict           # embedded tags (RIFF INFO, ID3, Vorbis, acid chunk...)
    meta: dict               # META_KEYS -> Field
    kit_id: Optional[str]
    excluded: bool           # user excluded from organization
    preview: Literal["native", "unsupported", "none"]
    warnings: list           # human-readable strings
    near_dupes: list         # ids of near-duplicate items (reported only)


class RefCheck(TypedDict):
    check: str           # e.g. "midi-present", "project-sample-refs"
    performed: bool
    result: str          # "ok" | "warning" | "not-checked"
    detail: str


class Kit(TypedDict, total=False):
    id: str
    name: str
    manufacturer: Optional[str]
    pack: Optional[str]
    root_rel: str        # common source directory (relative to root, "/")
    member_ids: list     # InventoryItem ids, ALL members incl. supporting assets
    bpm: Optional[float]
    key: Optional[str]
    evidence: list       # human readable strings explaining detection
    confidence: float
    held: bool
    hold_reason: Optional[str]
    excluded: bool
    ref_checks: list     # RefCheck
    manual: bool         # boundaries edited by user


OpAction = Literal["move", "link", "keep", "hold", "exclude"]
# move    : move source -> dest (same volume: hard link + verify + unlink source)
# link    : create NTFS hard link at dest pointing at `link_to` (the canonical
#           destination path of another op, or an existing library file)
# keep    : leave source in place (redundant exact duplicate, non-organizable file)
# hold    : not executed; needs review (reason in `reason`)
# exclude : user excluded; not executed


class Operation(TypedDict, total=False):
    id: str              # stable: op_id(action, item_id, dest)
    action: str          # OpAction
    item_id: str
    source: str          # native absolute source path
    dest: Optional[str]  # native absolute destination path (move/link)
    link_to: Optional[str]   # for "link": native absolute existing path
    depends_on: Optional[str]  # op id that must be done first (link after move)
    canonical: Optional[str]   # for keep/link of duplicates: canonical final path
    sha256: Optional[str]
    size: int
    mtime_ns: int
    kit_id: Optional[str]
    category: Optional[str]
    reason: str          # why this action
    flags: list          # e.g. ["duplicate", "name-collision", "low-confidence"]


class PlanSummary(TypedDict, total=False):
    total_files: int
    total_bytes: int
    audio_files: int
    packs: int
    kits: int
    duplicate_groups: int
    duplicate_bytes_avoided: int
    unknown_formats: int
    collisions: int
    by_action: dict      # action -> count
    held: int


class Plan(TypedDict, total=False):
    plan_hash: str       # plan_hash(ops) - approval binds to this
    created: str         # ISO timestamp
    root: str
    dest_root: str
    ops: list            # Operation
    summary: PlanSummary


class OpResult(TypedDict, total=False):
    op_id: str
    action: str
    status: Literal["done", "held", "failed", "skipped", "already-done"]
    source: str
    dest: Optional[str]
    timestamp: str
    error: Optional[str]
    file_id: Optional[str]   # identity of the final file


class Progress(TypedDict, total=False):
    job: Literal["idle", "scan", "plan", "execute", "export"]
    phase: str           # "walking", "hashing", "metadata", "classifying", ...
    running: bool
    done: int
    total: int
    bytes_done: int
    bytes_total: int
    current: str
    warnings: int
    errors: list         # [{path, error}] capped
    message: str


# --------------------------------------------------------------------------
# Stable ids / hashing
# --------------------------------------------------------------------------
def _h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def item_id(origin: str, rel_path: str) -> str:
    return _h(origin + "|" + rel_path.casefold())


def kit_id(root_rel: str, name: str) -> str:
    return "k" + _h(root_rel.casefold() + "|" + name.casefold())


def op_id(action: str, item: str, dest: Optional[str]) -> str:
    return "o" + _h(f"{action}|{item}|{(dest or '').casefold()}")


def plan_hash(ops: list) -> str:
    """Hash of the executable content of a plan. Any change to any op changes it."""
    keys = ("id", "action", "item_id", "source", "dest", "link_to", "depends_on",
            "sha256", "size", "mtime_ns", "kit_id")
    canon = [{k: o.get(k) for k in keys} for o in ops]
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


def classify_kind(ext: str) -> str:
    e = ext.lower()
    if e in AUDIO_EXTS: return "audio"
    if e in MIDI_EXTS: return "midi"
    if e in PROJECT_EXTS: return "project"
    if e in PRESET_EXTS: return "preset"
    if e in DOCUMENT_EXTS: return "document"
    if e in IMAGE_EXTS: return "image"
    if e in ARCHIVE_EXTS: return "archive"
    return "other"

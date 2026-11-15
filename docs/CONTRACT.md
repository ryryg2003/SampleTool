# Shared contract (owned by lead)

Stack: Python 3.10+ **standard library only** backend (`samplelib/`), vanilla
HTML/CSS/JS UI (`ui/`, no build step, no CDN — must work offline), served by
`samplelib/server.py` on `127.0.0.1`. Shared record shapes live in
`samplelib/models.py`. All paths in records: `rel_path` uses `/`; `abs_path`,
`source`, `dest` are native absolute paths.

Golden rules
1. Scan, enrichment, kit detection, planning and every review interaction are
   **read-only** against the collection (open files `rb` only; never create,
   rename, touch or write anything under `root`). App state lives in the data
   dir (`%LOCALAPPDATA%\SampleOrganizer` on Windows, `~/.sample-organizer`
   elsewhere, override `--data-dir`).
2. Only `executor.execute()` writes to the collection, and only for a plan whose
   `plan_hash` the user approved. Reports are written to `Samples/Catalog` by
   `export.write_reports()` only after an approved run.
3. Never overwrite. Windows paths are case-insensitive: compare with
   `casefold()`.
4. Uncertain → leave value unknown (`None`) and/or HOLD. Never guess silently.

## Module interfaces

### Filesystem (owner: fs agent) — `winfs.py`, `inventory.py`, `executor.py`
```python
# winfs.py
def long_path(p: str) -> str                 # \\?\ prefix on Windows for long paths; identity elsewhere
def file_identity(p: str) -> str | None      # f"{st_dev}:{st_ino}" via os.stat
def is_reparse_point(p_or_entry) -> bool     # junctions, symlinks, other reparse points
def same_volume(a: str, b: str) -> bool
def path_key(p: str) -> str                  # normalized, casefolded key for collision checks
def is_within(child: str, parent: str) -> bool  # case-insensitive, normalized
def reveal_in_explorer(p: str) -> None       # explorer /select,"p" on Windows; no-op/xdg elsewhere

# inventory.py
def sha256_file(path: str, chunk: int = 1 << 20, cancel=None) -> str   # streaming
def scan(root: str, dest_root: str, progress, hash_cache: dict | None = None,
         cancel: threading.Event | None = None) -> dict
# returns {"items": [InventoryItem], "skipped": [{"path","reason"}], "hash_cache": dict}
# - walks root, EXCLUDING dest_root subtree from incoming (origin="incoming")
# - separately walks dest_root (if it exists) as origin="library" (so existing
#   files take part in duplicate/collision checks), but excludes dest_root/Catalog
# - does not follow reparse points (records them in skipped with reason)
# - fills: id, origin, rel_path, abs_path, name, ext, size, mtime_ns, file_id,
#   nlink, kind, sha256/hash_error, pack, tags_raw={}, meta={}, audio=None,
#   kit_id=None, excluded=False, preview, warnings=[], near_dupes=[]
#   (preview: "native" if ext in BROWSER_PLAYABLE, "unsupported" for other audio, "none" otherwise)
# - pack = first path component under root (for library items: None)
# - hash_cache key: path_key(abs)+"|"+size+"|"+mtime_ns -> sha256
# progress(dict) is called with Progress-shaped dicts (job="scan").

# executor.py
def execute(plan: Plan, journal_path: str, progress, cancel=None) -> list[OpResult]
def read_journal(journal_path: str) -> list[dict]
```
Executor semantics:
- Only `move` and `link` ops write. `keep/hold/exclude` produce OpResults with
  status `skipped`/`held` and no filesystem change.
- Before each write: revalidate source exists, is not a reparse point, size and
  mtime match, SHA-256 matches plan (re-hash), destination does not exist
  (case-insensitive), destination parent is not a reparse point, same volume.
  Any mismatch → status `held` with explanation (never write).
- move = create parent dirs → `os.link(src, dest)` → verify identity equal and
  dest hash → `os.unlink(src)`; if unlink fails (locked file) → remove the new
  dest link, status `held`, "source locked". If hard links are unsupported on
  the volume, fall back to copy into `dest + ".partial-<opid>"` in the dest dir,
  fsync, verify hash, `os.link(partial, dest)` (no-overwrite), unlink partial,
  then unlink source.
- link = `os.link(link_to, dest)` → verify identity equals link_to's and hash.
  If `depends_on` op not done → `held`.
- Journal: append-only JSONL, flush+fsync per record:
  `{"op_id","phase":"start"|"done"|"held"|"failed","ts",...}`. On re-run with the
  same journal, ops with a `done` record are reported `already-done` (after a
  cheap check that dest exists with same size) and are not repeated. An op with
  `start` but no terminal record is reconciled from the filesystem state
  (dest exists with right identity/hash and source gone → done; dest link exists
  and source still exists → finish/undo safely).
- Never deletes anything except: the source after verified move, its own
  `.partial-*` staging files, and a dest link it just created during rollback.

### Audio, kits, catalog (owner: catalog agent) — `audio_meta.py`, `classify.py`, `kits.py`, `planner.py`, `export.py`
```python
# audio_meta.py  (bounded header reads, read-only)
def read_audio(path: str, ext: str) -> tuple[AudioInfo | None, dict, list[str]]
#   (info, tags_raw, warnings). WAV (fmt/data/LIST INFO/acid/smpl), AIFF/AIFC,
#   FLAC (STREAMINFO + VORBIS_COMMENT), MP3 (ID3v2 TBPM/TKEY/TCON/TPE1 + duration
#   estimate), OGG Vorbis (ident + comments). Unknown → (None, {}, [warning]).

# classify.py
def enrich(items: list[InventoryItem], progress, cancel=None) -> None
#   in place: audio, tags_raw, meta (every META_KEY present as a Field), warnings,
#   near_dupes. Evidence order: tag > filename > folder > analysis/inference.
def parse_bpm(text: str) -> float | None
def parse_key(text: str) -> str | None

# kits.py
def detect_kits(items: list[InventoryItem]) -> list[Kit]   # sets item["kit_id"]

# planner.py
def propose_dest(item, kit: Kit | None, dest_root: str) -> str | None   # fast, for preview
def build_plan(items, kits, root: str, dest_root: str) -> Plan
#   - one physical copy per sha256 in the finished library
#   - canonical preference: existing library file > file inside a kit > first by path
#   - kit members that duplicate a canonical elsewhere → "link" (same volume) else hold kit
#   - loose redundant duplicates → "keep" with canonical set
#   - name collision with different content → suffix " [Pack]" then " [Pack 2]"...
#   - unknown category / hash error / excluded / held kit → hold/exclude
#   - every incoming item gets exactly one op (accounts for every source path)

# export.py
def write_reports(catalog_dir, items, kits, plan, results) -> dict   # name -> path
#   Sample_Catalog.csv, Source_Path_Index.csv, Duplicate_Report.csv, Organization_Report.csv
def verify_library(items, kits, plan, results) -> dict
#   catalog paths resolve, kit assets present, unique hashes vs physical files
#   (by file identity) reconcile, storage avoided vs space actually freed.
```

### Lead — `store.py`, `server.py`, `__main__.py`, `Launch Sample Organizer.bat`
Store applies manual edits (keyed by `rel_path`) on top of enriched items, so
edits survive rescans. Any edit marks the plan stale and clears approval.

## HTTP API (server.py) — JSON unless noted. Base `http://127.0.0.1:<port>`
| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/state` | | `{root, dest_root, data_dir, has_inventory, progress, plan: {plan_hash, created, stale, approved, summary} or null, execution: {ran, results_summary, reports, verification} or null}` |
| POST | `/api/scan` | `{root?}` | `{ok}` starts scan+enrich+kit detection job |
| GET | `/api/progress` | | `Progress` |
| POST | `/api/cancel` | | `{ok}` |
| GET | `/api/packs` | `q` | `[{name, manufacturer, files, audio, kits, bytes}]` |
| GET | `/api/facets` | | `{categories, instruments, keys, genres, manufacturers, packs, loop_types}` |
| GET | `/api/samples` | `q, pack, manufacturer, category, instrument, loop_type, key, genre, kit, kind, status, origin, bpm_min, bpm_max, sort(name/pack/bpm/key/duration/size/status), dir(asc/desc), offset, limit` | `{total, offset, items: [SampleRow]}` |
| GET | `/api/samples/<id>` | | `SampleRow` + `meta` (full Fields), `tags_raw`, `audio` |
| GET | `/api/kits` | `pack, q` | `[Kit + {member_count, bytes, proposed_root}]` |
| GET | `/api/kits/<id>` | | Kit + `members: [SampleRow]` |
| GET | `/api/audio/<id>` | Range header | raw bytes (read-only), Content-Type by ext |
| POST | `/api/items/edit` | `{ids:[...], changes:{category?, instrument?, loop_type?, bpm?, key?, genre?, tags?, manufacturer?, excluded?}}` | `{ok, updated, items:[SampleRow]}` |
| POST | `/api/kits/edit` | `{kit_id, action: "hold"/"release"/"exclude"/"include"/"rename"/"remove_members"/"add_members"/"dissolve", name?, ids?}` | `{ok, kit}` |
| POST | `/api/kits/create` | `{name, ids}` | `{ok, kit}` |
| POST | `/api/plan/build` | | `{ok}` (job "plan"; poll progress) |
| GET | `/api/plan` | `group(pack/kit/category/action/warning), action, flag, q, offset, limit` | `{plan_hash, stale, approved, summary, total, ops:[Operation + {name, pack, kit_name, rel_path}]}` |
| POST | `/api/plan/approve` | `{plan_hash}` | `{ok}` or 409 if hash != current or plan stale |
| POST | `/api/execute` | `{plan_hash}` | `{ok}` or 409 unless approved & matching; job "execute" then "export" |
| GET | `/api/log` | `status, q, offset, limit` | `{total, items:[OpResult + {name}] }` |
| POST | `/api/reveal` | `{id}` or `{path}` | `{ok}` |

SampleRow (flat, for the table):
`{id, origin, name, rel_path, abs_path, ext, size, kind, pack, manufacturer,
category, instrument, loop_type, bpm, key, genre, tags, duration_s, sample_rate,
channels, bits, sha256, kit_id, kit_name, excluded, preview, warnings,
review_status, proposed_dest, final_path, dup_count, prov}` where `prov` maps
each meta key to `{source, confidence}` and `review_status` is one of
`ok | needs-review | edited | held | excluded | done`.

Errors: non-2xx with `{error: "message"}`.

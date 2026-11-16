# Mix matching, audio analysis and Beat Starter: design

Date: 2026-11-16. Status: awaiting approval.

## Intent

The organizer already sorts a sample collection into a clean library. The next
step is to help while making music: find samples that work together (tempo,
key, bar length, downbeat) and hand over a matching drum + bass + melody set to
start a beat. The scan should measure what tags and filenames don't say.

Picked by the user from a suggestion list: features 1 (Goes well with),
2 (audio analysis in the scan), 4 (Beat Starter), plus a cleanup pass on the
existing code. Downloading from loop websites was dropped.

## Goals

1. **Audio analysis during the scan** estimates BPM, key, length in bars and
   whether the loop starts on the downbeat, for files the app can decode.
2. **"Goes well with"**: for any sample, a ranked list of compatible samples
   with plain reasons ("same key", "70 vs 140 BPM, half-time").
3. **Beat Starter**: pick key and BPM (or random), get matching drum loop,
   bass and melody; reroll any slot, lock a slot, save the set.
4. **Cleanup**: remove dead code and duplication, keep behavior identical,
   keep all tests green.

## Non-goals

- Decoding MP3, FLAC, OGG, M4A (no stdlib decoder). These keep tag/filename values.
- Time-stretching or pitch-shifting audio.
- "Sounds like this" similarity search.
- Downloading from websites.

## Anti-goals (failure even if it works)

- Analysis silently overriding a tag, filename or manual value.
- A first scan that becomes unusably slow, or a rescan that re-analyzes
  unchanged files.
- Any write into the sample collection (golden rule 1 stands).
- Suggestions that are just duplicates or near-duplicates of the chosen sample.

## Constraints

Standard library only, offline, Windows first (existing contract). Python 3.10+.

## Approaches considered

| Approach | Trade-off | Verdict |
|---|---|---|
| **A. Pure-Python analysis of WAV/AIFF, cached by SHA-256** | No install. Prototype: ~0.2 s per 8 s of audio. Misses compressed formats. | **Chosen** |
| B. Add numpy (+ a decoder) | Faster, covers more formats, but breaks the no-install rule and the double-click launch | Rejected |
| C. Matching from tags/filenames only, no analysis | Cheapest, but many loops have no BPM/key in name or tags | Rejected (user asked for analysis in the scan) |

Matching runs in memory over the existing store (no database); the user
handed over the how, so these are builder's calls.

## Design

### 1. `samplelib/analysis.py` (new)

- `analyze(path, ext, want: set) -> dict | None`. Decodes PCM WAV (8/16/24/32-bit
  int, 32-bit float, WAVE_FORMAT_EXTENSIBLE) and AIFF/AIFC (uncompressed),
  reading at most the first 30 s. Bounded reads, read-only, `rb` only.
- Downmix to mono, decimate to ~11 kHz.
- **Tempo**: onset-strength envelope (positive energy flux, ~23 ms hop),
  autocorrelation scored over 60–200 BPM. For loops, a strong prior from bar
  fit: candidate BPM = 240 × bars / duration for bars in {1, 2, 4, 8, 16};
  a candidate agreeing with the autocorrelation peak (or its half/double)
  wins. Output `bpm`, `bpm_conf` (0..1).
- **Bars**: duration × bpm / 240; `bars` is set when within 2% of a whole
  number (or of 0.5 / 0.25 for short phrases), else `None`.
- **Downbeat**: first strong onset time; `on_grid = True` when it is within
  ~20 ms of the start and bars is whole; `downbeat_ms` reported.
- **Key**: chroma via Goertzel filters (MIDI 36–83) on up to 8 frames,
  correlated against Krumhansl major/minor profiles. Output `key` ("Am",
  "F#"), `key_conf`. For one-shots (bass, 808, melodic) the strongest pitch
  class is reported as the root note.
- Returns `{"v": ANALYSIS_VERSION, "bpm", "bpm_conf", "bars", "on_grid",
  "downbeat_ms", "key", "key_conf"}`; any failure returns `None` plus a warning.

### 2. Scan integration

- New scan phase **"analyzing"** after classification, before kit detection.
- Analyzes: decodable audio that is a loop or of unknown loop type (tempo,
  bars, downbeat, key), and bass/melodic one-shots (key only). Drum and FX
  one-shots are skipped.
- **Cache** `analysis_cache.json.gz` in the data dir, keyed by
  `sha256 + "|" + ANALYSIS_VERSION`. Rescans of unchanged files do no audio work.
- Cancellable; progress reported like other phases.
- Results stored on the item as `item["analysis"]`.
- **Evidence order unchanged**: tag > filename > folder > analysis. Analysis
  fills `bpm`/`key` only when unknown (source `"analysis"`, its confidence).
  When analysis disagrees with an existing value (not counting half/double
  time for BPM, or relative key), add a warning such as
  "Filename says 140 BPM, audio measures 128". `loop_type` unknown + whole bars
  found → loop (source analysis).
- Manual edits still always win.

### 3. Matching `samplelib/matching.py` (new)

`matches(store, item_id, role=None, limit=30) -> list[{row, score, reasons}]`

Score components (each 0..1, weighted):
- **Tempo**: same within 2% → 1.0; half/double → 0.8; within 6% → 0.5;
  either unknown → neutral 0.4 and the reason says "tempo unknown".
  One-shots skip tempo.
- **Key**: same → 1.0; relative major/minor → 0.9; Camelot ±1 → 0.7;
  drums or unknown → neutral (drums don't clash).
- **Bars**: equal or one divides the other → bonus.
- **Downbeat**: both on grid → small bonus.
- **Same pack / same kit** → small bonus.
- **Role**: optional filter (drums / bass / melody / vocal-fx), derived from
  category. Without a filter, results favor groups different from the source
  (a melody suggests drums and bass first).
- Excluded: the item itself, exact duplicates (same SHA-256), near-duplicates,
  excluded items.
- Index items by role and tempo bucket at store reindex so a query stays well
  under 100 ms on 50k samples.

API: `GET /api/samples/<id>/matches?role=&limit=`.

### 4. Beat Starter

- `POST /api/starter` `{key?, bpm?, locked: {slot: id}, seed?}` →
  `{key, bpm, seed, slots: {drums, bass, melody}}`, each slot a SampleRow plus
  reasons, or `null` with "nothing matches" when the library has no fit.
- Picks loops only. If key/BPM are empty, picks them from a random melody
  loop. Locked slots are kept, and the others are chosen to match them.
  Reroll = same call with a new seed and the other slots locked.
- Saved sets live in the data dir (`starters.json`), never in the collection.
  `GET /api/starters`, `POST /api/starters` `{name, key, bpm, ids}`,
  `DELETE /api/starters/<id>`.

### 5. UI

- **Library detail panel**: "Goes well with" section with role chips
  (All / Drums / Bass / Melody / Vocal & FX), rows with play button, score bar
  and reasons. Clicking a row selects it in the library.
- Library table gains **Bars** and a downbeat marker column. BPM/key cells show
  an "estimated" style when the source is analysis.
- New rail tab **Beat Starter**: key and BPM pickers (with Random), three slot
  cards (drums, bass, melody) each with play, lock and reroll; **Play all**
  starts the three together, looped, through Web Audio; Save set; saved
  sets list.
- `ui/fixtures.js` gets matching fixture data so the UI smoke test covers both.

### 6. Cleanup pass

- Dead code, duplicated helpers, and inconsistent naming across `samplelib/`
  and `ui/`. No behavior change; all tests stay green before and after.
- README: describe the new features; mark `docs/FREE_SOUNDS_ADDON.md` as
  shelved instead of on the roadmap. Update `docs/CONTRACT.md` with the new
  fields and endpoints.

### 7. Tests

- `tools/make_fixtures.py` gains loops with known BPM, key and bar length
  (synth drums + chord), including trimmed-late (off-grid) and
  filename-disagrees cases.
- Unit: analysis accuracy (BPM within 1%, key correct or relative, bars and
  on_grid), decoder formats (16/24-bit, float, AIFF), cache hit on rescan,
  evidence order and warnings, matching scores and exclusions, starter
  locking/reroll determinism by seed.
- End-to-end HTTP tests for the new endpoints; UI smoke/flow updated.

## Left to the builder

Exact weights, thresholds and frame sizes; tune them against the fixtures.
Column placement and visual detail, following the existing UI style.

## Builder's calls (flag for review)

- Analysis cap of the first 30 s per file.
- Saved Beat Starter sets are stored in the app data folder only. They are not
  exported as files.
- Freesound doc kept but marked shelved, not deleted.

# Mix Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audio analysis in the scan, "Goes well with" matching, Beat Starter, cleanup.

**Architecture:** New pure modules `analysis.py` (decode + DSP) and `matching.py`
(scoring + starter); `server.py` wires a scan phase, a cache and endpoints;
`store.py` exposes rows and the matching index; vanilla JS UI adds a panel and a tab.

**Tech Stack:** Python 3.10+ stdlib, vanilla HTML/CSS/JS, unittest, Playwright smoke tests.

**Spec:** `docs/superpowers/specs/2026-11-16-mix-matching-design.md`

## Global Constraints

- Standard library only; offline; no CDN.
- Read-only against the collection (`rb` only). New state files live in the data dir.
- Evidence order tag > filename > folder > analysis; manual always wins.
- Analysis reads at most the first 30 s of a file.
- Cache key `sha256 + "|" + ANALYSIS_VERSION`.

## Review Focus

1. Corrupt/truncated/compressed (`AIFC sowt` ok, `ulaw` not) files: `analyze` returns None + warning, scan continues. Test in Task 1.
2. Silence or a single hit: no BPM, no key (not a bogus value). Test in Task 1.
3. Item with no sha256 (hash error): analyzed but not cached; no crash. Test in Task 2.
4. Library with zero loops for a slot: starter slot is `null` with a reason, not 500. Test in Task 4.
5. Manual BPM/key edit then rescan: the edit survives and analysis doesn't overwrite it. Test in Task 2.

---

### Task 1: `samplelib/analysis.py`

**Files:** Create `samplelib/analysis.py`, `tests/test_analysis.py`; modify `tools/make_fixtures.py` (add `beat_loop(bpm, bars, key, offset_s=0)` audio generator).

**Produces:**
- `ANALYSIS_VERSION: int = 1`; `DECODABLE = {".wav", ".aif", ".aiff", ".aifc"}`
- `decode(path, ext, max_s=30.0) -> tuple[list[float], int, float] | None` (mono samples, rate, full duration s)
- `analyze(path, ext, want=frozenset({"tempo","key"})) -> tuple[dict | None, list[str]]`;
  dict keys `v, bpm, bpm_conf, bars, on_grid, downbeat_ms, key, key_conf`
- `key_compat(a, b) -> tuple[float, str]` and `camelot(key) -> str | None` (shared by matching)

- [ ] Tests: 128 BPM 4-bar Am loop → bpm within 1%, bars==4, on_grid True, key in {"Am","C"}; 90 BPM 2-bar F# major → bpm within 1%; 24-bit and float32 WAV and AIFF decode; loop offset 150 ms → on_grid False; silence → bpm None, key None; truncated file → (None, [warning]); bass one-shot at A1 with want={"key"} → key "A"; `key_compat("Am","C")[0] == 0.9`, `("Am","Em")` == 0.7, same == 1.0.
- [ ] Run `python -m unittest tests.test_analysis` → FAIL.
- [ ] Implement per spec section 1 (onset flux + autocorrelation + bar-fit prior; Goertzel chroma + Krumhansl).
- [ ] Run → PASS. Commit.

### Task 2: Scan integration + cache

**Files:** Modify `samplelib/classify.py` (`apply_analysis(item) -> None`), `samplelib/server.py` (phase "analyzing" between `enrich` and `detect_kits`), `samplelib/store.py` (load/save `analysis_cache.json.gz`; SampleRow adds `bars`, `on_grid`); tests in `tests/test_analysis.py`.

**Consumes:** `analysis.analyze`. **Produces:** `item["analysis"]`, row fields `bars`, `on_grid`; `classify.analyze_items(items, cache, progress, cancel) -> dict` (returns updated cache).

- [ ] Tests: unknown BPM filled with source "analysis"; filename BPM 140 vs measured 128 → warning text contains "audio measures 128"; 70 vs 140 → no warning; manual edit survives rescan; second scan → zero `analyze` calls (patched counter); item without sha256 analyzed, not cached; drum one-shot not analyzed.
- [ ] FAIL → implement → PASS. Run full `python tests/run_all.py`. Commit.

### Task 3: Matching

**Files:** Create `samplelib/matching.py`; modify `samplelib/store.py` (`role_of(item) -> str|None` from category: drums/bass/melody/vocal-fx), `samplelib/server.py` (`GET /api/samples/<id>/matches?role=&limit=`); test `tests/test_matching.py`.

**Produces:** `matches(store, item_id, role=None, limit=30) -> list[{"row","score","reasons"}]`; `tempo_compat(a, b) -> tuple[float,str]`.

- [ ] Tests: same key+BPM ranks above relative key; 70 vs 140 scored 0.8 with reason "half-time"; duplicates, near-dupes, self, excluded never returned; role filter; melody source ranks drums/bass above other melodies when no role; endpoint 404 on unknown id.
- [ ] FAIL → implement → PASS. Commit.

### Task 4: Beat Starter

**Files:** `samplelib/matching.py` (`starter(store, key=None, bpm=None, locked=None, seed=None) -> dict`), `samplelib/store.py` (`starters.json` CRUD), `samplelib/server.py` (`POST /api/starter`, `GET/POST /api/starters`, `DELETE /api/starters/<id>`); tests in `tests/test_matching.py`.

- [ ] Tests: same seed → same result; locked slot kept and others match its key/BPM; no bass loops → `slots["bass"] is None` and `reasons` explains; saved set round-trips; delete works.
- [ ] FAIL → implement → PASS. Commit.

### Task 5: UI

**Files:** `ui/library.js` (Goes-well-with section, Bars + downbeat columns, "est." style), new `ui/starter.js`, `ui/index.html` (rail tab + script), `ui/app.css`, `ui/fixtures.js` (matches/starter fixture responses), `tests/ui_smoke.js`.

- [ ] Smoke test asserts: matches list renders in detail panel; Beat Starter shows 3 slot cards; reroll changes a slot in fixture mode.
- [ ] Implement; run `node tests/ui_smoke.js` and `node tests/ui_flow.js` → PASS; screenshot check. Commit.

### Task 6: Cleanup + docs

- [ ] Remove dead code/duplication found by review of `samplelib/` and `ui/` (no behavior change); full test suite green before and after.
- [ ] Update README (features, tests count), CONTRACT.md (fields, endpoints), mark FREE_SOUNDS_ADDON.md shelved. Commit and push.

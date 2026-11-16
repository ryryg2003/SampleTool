# v1.0 work checkpoint

Status as of 2026-11-16: **scaffold committed (6a162b4); five feature agents A-E running in worktrees.** If this session ends before they report, re-run each agent's task on top of the scaffold commit (prompts mirror the table below). This
file lets a new session resume without re-reading everything. Delete it when
v1.0.0 ships.

## Done so far
- Read README, CONTRACT, FREE_SOUNDS_ADDON, WINDOWS_MANUAL_CHECKS, CI workflow,
  every module in `samplelib/`, the UI shell and views, and the tests.
- Baseline on Linux (Python 3.13): `python tests/run_all.py` = 89 tests, PASS
  (Windows-only tests skipped). `tests/ui_smoke.js` (fixture mode) 49/49 PASS.
  `tests/ui_flow.js` ALL PASSED.
- Found one bug to fix during cleanup: `run_tests.bat` line 4 uses `>/dev/null`.
  On Windows that should be `>nul`.

## Facts about the code that the plan depends on
- `Store` (store.py) keeps edits keyed by `rel_path.casefold()+"|"+origin`.
  Favorites and collections must be keyed by **sha256** instead, so they
  follow a file after it moves.
- `Store.set_scan_result` deletes `execution.json.gz`, and `set_root` deletes
  plan/approval/execution/inventory. Undo needs its own persisted run history
  (`runs/<run_id>.json.gz`) so a later rescan cannot destroy the data undo needs.
- Journals live in `journal-<rootkey>-<planhash32>.jsonl` in the data dir.
- `executor.execute(plan, journal_path, progress, cancel)` already resumes from
  the journal. Ops are only `move`/`link` writers. **Undo should reuse it** by
  generating a reverse plan of the same shape:
  - a forward `move` (src→dest) becomes a reverse `move` (dest→src). It needs a
    new `root`/`dest_root` scoping, because `_check_source_ancestors` refuses
    sources inside dest_root and `_check_dest_ancestors` refuses dests outside
    dest_root. Plan: add an `undo` mode (plan field `"kind": "undo"`) where the
    source must be inside dest_root and the dest inside root but outside
    dest_root. Keep every other check: identity, SHA-256, no overwrite,
    journal.
  - a forward `link` becomes a reverse `unlink` action. It is new and only
    valid in undo plans. It removes the dest only if its identity still equals
    the link target's identity and the SHA-256 matches. Otherwise it is held.
  - Ordering: unlinks first, then moves back. Anything changed becomes `hold`
    with a reason, at plan-build time, and is revalidated at execute time.
- Plan hash: `models.plan_hash` covers id/action/item_id/source/dest/link_to/
  depends_on/sha256/size/mtime_ns/kit_id. Undo plans must use the same
  approve-by-hash path (`Store.approve`, `/api/execute`).
- Audio serving is `Handler._audio` (Range support, read-only). AIFF→WAV goes
  there as a new branch, and the Content-Length must be computed up front from
  the header (frames × channels × bytes) so Range still works.
- `inventory.scan` already reuses `hash_cache`. "New since last run" needs the
  previous inventory's set of `(path_key, size, mtime_ns)` or sha256 saved at
  scan time, so each item can be marked `is_new`.
- The UI is vanilla JS. `index.html` loads fixtures.js, core.js, vtable.js,
  library.js, review.js and app.js. Fixture mode (`?fixture=1`) mocks every
  endpoint in `ui/fixtures.js`, so new endpoints need fixture handlers or
  ui_smoke breaks.
- `ui/vtable.js` is the virtual table that every list uses.

## Work plan (execute in this order)
Lead owns `models.py`, `CONTRACT.md`, `server.py` routes, `index.html` shell
and `store.py` integration. Parallel worktree agents own new modules:

| Agent | Owns (new files) | Feature |
|---|---|---|
| A | `samplelib/undo.py`, executor undo mode, `tests/test_undo.py` | 1. Undo |
| B | `samplelib/audiocodec.py` (AIFF→WAV stream + peaks + cache), `tests/test_audio.py`, `ui/wave.js` | 2 + 3. AIFF preview, waveform |
| C | `samplelib/collections.py`, `tests/test_collections.py`, `ui/collections.js` | 4. Favorites and collections, plus 6. incremental "new" flags |
| D | `samplelib/freesound.py`, `tests/test_freesound.py`, `ui/findsounds.js` | 7. Freesound (verify current API docs first) |
| E | `samplelib/settings.py`, `ui/settings.js`, `tools/make_icon.py`, `.bat` files, single-instance and idle shutdown | 8 + 9. Settings and Windows polish |

After merging: 5 (drag to DAW: DownloadURL plus a PowerShell Set-Clipboard
fallback), 10 (cleanup: no behavior change, tests before and after each
commit), 11 (README rewrite, CHANGELOG, `samplelib/__init__.py` `__version__ =
"1.0.0"`, `tools/build_release.py`, GitHub release v1.0.0). Then a fresh
read-only reviewer subagent covers executor/planner/store/server/undo/settings,
followed by fixes, a CI run on Windows and Linux, and a read of the Windows
logs and screenshots.

## Rules that must not be broken (from the brief)
No new runtime deps. Scan and review stay read-only. Nothing is overwritten
(case-insensitive). Moves are link, then verify identity + SHA-256, then
unlink. Execute only by approved hash, and any edit clears approval. The
journal resumes. Redundant dupes stay in place. Every new writer gets dry-run,
review and approve. Tests use only synthetic temp fixtures. Never touch E:\.

## How to resume
Start a new session on branch `claude/pensive-albattani-xcadya` and say:
"Resume the v1.0 work from docs/V1_CHECKPOINT.md, using the original brief."

## Session log
- First usage-limit stop: all five agents stopped mid-work. The container restarted and their
  uncommitted files were lost, so no feature code survived. Their local branches
  `worktree-agent-*` survive restarts. Agents were resumed with an order to make a WIP commit after
  every step.
- To resume after another stop: check `git worktree list` and each `worktree-agent-*`
  branch's commits since 6a162b4, then continue or merge from there. Pending release
  test: the lead parks it at `docs/pending_test_release.py` (move to tests/ later) until "Create Desktop
  Shortcut.bat" and CHANGELOG.md exist (it checks for both).

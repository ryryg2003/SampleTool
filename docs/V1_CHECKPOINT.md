# v1.0 work checkpoint

Branch `claude/pensive-albattani-xcadya`. Delete this file when v1.0.0 ships.

## Status (2026-11-16): features 1-9 built and merged; release work remains
Local results on Linux, Python 3.13:
- `python tests/run_all.py`: 191 tests, 179 passed, 12 skipped (Windows-only), 0 failed.
- `ui_smoke.js` (fixture mode): 120/120 passed.
- `ui_flow.js`: all passed. It covers the scan → run → undo flow, favorites, saved search and AIFF audition.
- Windows CI on the latest push has not been checked yet.

Merged features, each with its own module and tests:
1. Undo: `undo.py`, `api_undo.py`, executor undo mode with an `unlink` action, run history in `<data>/runs/`, `tests/test_undo.py`.
2. AIFF preview and 3. waveform, plus 5. drag to DAW: `audiocodec.py`, `api_media.py`, `ui/media.js`, `tests/test_media.py`.
   - Drag sets DownloadURL data and has a "Copy file to clipboard" (PowerShell) fallback.
   - Neither has been tested in real Edge, a real DAW or real Windows.
4. Favorites and collections and 6. new packs: `collections.py`, `incremental.py`, `api_collections.py`, `ui/collections.js`, `tests/test_collections.py`.
7. Freesound: `freesound.py`, `api_freesound.py`, `ui/findsounds.js`, `tests/test_freesound.py`, with the docs verified live.
   - Downloads use OAuth2 with PKCE, or a preview-MP3 fallback.
   - The planner moves `<sound>.license.txt` along with its sound (`planner._attach_sidecars`).
8. Settings and 9. Windows polish: `settings.py`, `api_settings.py`, `winapp.py`, `ui/settings.js`, `tools/make_icon.py`, `ui/app.ico`, `Create Desktop Shortcut.bat`, idle exit, single instance, `tests/test_settings.py`.

Architecture: `server.py` loads `samplelib/api_<name>.py` extensions (see `EXTENSION_NAMES`). The UI registers views and features through `App.registerView`, `App.features` and `App.keyHandlers` (core.js). Fixture-mode routes go through `window.FixtureExt`.

## Remaining steps, in order
1. **CI:** confirm the latest push is green on Windows and Linux (`.github/workflows/tests.yml`).
   - Read the Windows logs to confirm `test_windows.py` and the new `WindowsDesktopTest` ran, and that `ui_flow.js` ran.
   - Download the screenshot artifacts and fix any visual or overflow issues.
2. **Docs/CONTRACT.md:** add every new endpoint. Each `samplelib/api_*.py` has its routes in `get()`/`post()`.
   - Add `tests/test_contract.py`, which checks that every route string in `server.py` and `api_*.py` appears in CONTRACT.md.
3. **Cleanup (feature 10):**
   - Docstrings and type hints on public functions.
   - Remove dead code and unused CSS and JS.
   - No behavior change; run tests before and after each commit.
4. **Review:** have a fresh read-only reviewer subagent check `undo.py`, `executor.py`, `planner.py` (sidecars and only-new), `incremental.py`, `store.py`, `server.py` and settings root switching. Fix findings and add regression tests.
5. **Docs:** rewrite README for a non-technical producer, following the brief's outline, and add CHANGELOG.md.
6. **Release:**
   - Move `docs/pending_test_release.py` to `tests/test_release.py`. It needs `CHANGELOG.md` to exist, and `tools/build_release.py` INCLUDE_FILES must list it.
   - Run `python tools/build_release.py`.
   - Create GitHub release v1.0.0 with the zip.
   - Version: `samplelib/__init__.py` (`__version__ = "1.0.0"`). It is shown in the keys/help dialog.

## Known risks to look at in review
- Single-instance focus, desktop shortcut, idle shutdown and the PowerShell clipboard are untested on real Windows (see `tests/WINDOWS_MANUAL_CHECKS.md` section 9).
- Freesound: localhost OAuth redirect acceptance is unverified, and the "paste the code" fallback exists. If port 8765 is busy, the redirect URL changes.
- The new-files baseline is a single file, so switching folders and back resets "new".

## How to resume
In a new session on this branch, say: "Resume the v1.0 work from docs/V1_CHECKPOINT.md, using the original brief."

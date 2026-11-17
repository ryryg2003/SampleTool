# Project notes for Claude

## UI design reference

All UI work (new screens, restyles, components, mockups) must follow the
Prism Aurora reference in `docs/UI_PLAN.md` and
`docs/ui-plan/prism-aurora-reference.png`. Use its color tokens, category
colors, shapes and motion rules. Do not introduce other themes unless the
user asks for one.

## Project rules

- Python standard library only, offline, no installs. Windows 10/11; keep
  `Launch Sample Organizer.bat` and `Create Desktop Shortcut.bat` working.
- Scanning never writes to the sample folders. App data lives in
  `%LOCALAPPDATA%\SampleOrganizer`; updates must never lose it.
- Before finishing: `python tests/run_all.py`, `node tests/ui_smoke.js` in
  fixture mode, and `node tests/ui_flow.js` all pass. Add tests for new
  behavior; never delete or weaken a test just to make it pass.
- Matching is strict by default: tempo within 0.5% (half/double counts),
  bass and melody in the same or relative key, drums ignore key, loop lengths
  divide evenly. No match leaves the slot empty with a reason. The Loose
  toggle keeps near matches.
- Genre comes only from tags, filenames, folders and pack names. Never from
  audio.
- Each release: bump `samplelib/__init__.py` `__version__`, update
  `CHANGELOG.md` and `README.md`. New top-level dev-only files or folders get
  an `export-ignore` line in `.gitattributes` so Download ZIP stays app-only.
- Work on a branch from main, one commit per step, finish with one pull
  request into main.
- No commercial samples committed.

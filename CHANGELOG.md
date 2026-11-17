# Changelog

## 3.1.0

A first-time user can run it with no setup.

**No install**
- The app brings its own Python: the official Windows embeddable package
  (Python 3.14.10, x64) from python.org is in the `python` folder. Both
  launchers use it and fall back to an installed Python 3.10+ only if it is
  missing.
- Settings shows which Python runs the app.

**First run**
- No more `E:\Sample Packs` default. A fresh install opens on three steps:
  pick your sample folder, scan it, Try Beat Starter.
- A folder picker (drives, Music, Documents, Desktop, Downloads, subfolders,
  audio-file count) on the welcome screen, the Scan form and Settings.
  Pasting a path still works.
- The scan summary leads with Try Beat Starter.

**Faster, clearer scans**
- Tempo and key are measured about 5x faster: the number crunching runs at
  C speed and several files are measured at once (one per spare CPU core, up
  to six). Results are the same as before.
- Scan progress shows "Step n of 5" and the time left in the current step,
  also in the status bar.

**Look**
- Library filters fit one row at 1366 px. Maker, status and the toggles
  moved into a **More** panel that shows how many are on.
- Settings has a Help and setup panel in the slot that was empty.

**Docs**
- README: AIFF previews do work (converted to WAV as they play); only REX
  cannot be previewed.

## 3.0.0

One final version that combines every feature branch.

**Better matches**
- Beat Starter and "Goes well with" now only pair loops that fit exactly:
  tempo within 0.5% (half or double time counts), bass and melody in the same
  or relative key, drums ignore key, loop lengths that divide evenly.
- When nothing fits, a slot stays empty with a plain reason instead of a
  near miss. The older, looser rules are a **Loose** toggle (off by default).
- Beat Starter has a genre picker that prefers loops of that genre.

**Play all**
- All loops start on one shared clock and restart together every cycle.
- Each loop is nudged to the set tempo with playback speed, so 79 and 79.3 BPM
  loops stay in sync (within 10 ms after 8 bars).
- Volumes are balanced using measured loudness.

**Scanning**
- Drums, FX and noise show "none" for key when the measured key is unsure,
  instead of a guess.
- Loudness is measured for every WAV and AIFF sample.
- Genre comes only from tags, file names, folders and pack names; a pack's
  genre spreads to its other samples as "From pack". Genre is never guessed
  from audio. Run one scan after updating to fill in the new values.

**Library**
- New filters: Compatible keys, Include half/double, Verified only. They can
  be saved in smart collections.

**Look**
- Prism Aurora styling on every screen, including Settings.

**Removed**
- Find Sounds (Freesound) and its API key setting.
- Old planning notes, checkpoint docs and screenshot dumps. GitHub
  "Download ZIP" now contains only the app.

## 1.0.0

Undo, AIFF preview, waveform, drag to DAW, collections, Settings, desktop
shortcut, mix matching and the first Beat Starter.

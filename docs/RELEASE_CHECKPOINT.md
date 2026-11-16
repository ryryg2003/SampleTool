# Release checkpoint (v3.0.0 work in progress)

Branch: `claude/friendly-pascal-m5w20n` (main already held all three feature
branches via PR #2, so work started from main).

Done (one commit each):
1. Freesound removed; Settings uses the Prism Aurora header card.
2. Strict matching (0.5% tempo, same/relative key, drums ignore key, bars
   divide evenly), empty slot with reason, Loose toggle, Bm/79 regression test.
3. Play all on one shared clock, playbackRate to set tempo, RMS gain
   (ui/mixclock.js; tested in ui_smoke).
4. Scan: "no key" for drums/FX/noise at low key confidence, loudness
   (rms_db, analysis v2), genre "from pack" spread; never from audio.

Left:
5. Filters: genre, key + "compatible keys", tempo + "include half/double",
   verified only; Beat Starter genre picker (backend `genre=` already works).
6. Clean-up + .gitattributes export-ignore; list removed files.
7. Version 3.0.0, README quick-start, CHANGELOG.md.
8. PR "v3.0.0: final release" into main.

Tests at this checkpoint: Python 207 passed / 12 skipped (Windows-only),
ui_smoke 115/115, ui_flow all passed.

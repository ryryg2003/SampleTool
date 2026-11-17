# Sample Library Organizer 3.1.0

A Windows app that sorts your sample packs into one clean, searchable library
and finds loops that sound good together. It runs on your own PC, works
offline, and never changes a file until you approve a plan.

## Quick start

Nothing to install. The app brings its own copy of Python.

1. **Download the app.** On the GitHub page click the green **Code** button,
   then **Download ZIP**. Unzip it somewhere permanent, for example
   `C:\Tools\SampleOrganizer`. Keep every file, including the `python`
   folder.
2. **Start it.** Double-click `Launch Sample Organizer.bat`. The app opens in
   its own window. Leave the small console window open while you use it.
   If Windows asks whether to run the file, choose **Run**.
3. **Follow the three steps** on the welcome screen:
   - **Pick your sample folder.** Click **Choose folder…** and open folders
     until you are in the one that holds your packs, then **Use this folder**.
     You can also paste a path.
   - **Scan it.** Scanning only reads files; it never writes to your sample
     folders. The progress bar shows which step it is on and the time left.
   - **Try Beat Starter.** Pick a key, tempo and genre, click **New set**,
     then **Play all**.
4. **Browse and play** in **Library**. Space plays, arrow keys move.
5. **Organize (optional).** In **Review**, build the plan, check it, approve
   it, then run it. Files are moved into `Samples` inside your packs folder.

Want a desktop icon? Double-click `Create Desktop Shortcut.bat` once. The icon
starts the app without the console window.

If you already have Python 3.10 or newer installed, the app still uses its
own copy. It falls back to yours only if the `python` folder is missing.

## Update to a new version without losing anything

Your scans, edits, favorites, collections, saved sets and settings live in
`%LOCALAPPDATA%\SampleOrganizer`, not in the app folder. To update:

1. Close the app.
2. Download the new ZIP and unzip it **over the old app folder** (or into a
   new folder, then run `Create Desktop Shortcut.bat` there again).
3. Open the app. Everything is where you left it. Run **Start read-only
   scan** once on the Scan screen to pick up new measurements.

To see that folder, press Win+R, type `%LOCALAPPDATA%\SampleOrganizer` and
press Enter. Back it up if you like; deleting it resets the app.

## Finding samples that go together

- **Goes well with** (side panel in Library) and **Beat Starter** only pair
  loops that will sound right with no stretching: tempo within 0.5% (half or
  double time counts), bass and melody in the same key or its relative
  major/minor (Am with C), and loop lengths that divide evenly. Drums ignore
  key. When nothing fits, the slot stays empty and says why.
- **Loose** (a toggle in both places, off by default) also allows neighbor
  keys and tempos a few percent apart.
- **Play all** starts every loop on one shared clock, nudges each to the set
  tempo (the pitch change is too small to hear) and evens out their volume.
- **Genre** comes only from tags, file names, folders and pack names
  ("From pack"). The app never guesses genre from audio. Drums, FX and noise
  show "none" for key instead of a guess.
- **Filters:** category, instrument, type, key, genre and tempo sit in one
  row. **More** holds maker, status, **Compatible keys**, **Include
  half/double**, **Verified only** (values from tags, names, folders or your
  edits, with no audio disagreement), new files and all file types.

## Scan speed

Measuring tempo and key is the slow part of a scan. The app measures several
files at once, one per spare processor core (up to six), and remembers every
result, so a rescan only measures new or changed files.

## Safety

- Scanning and browsing never change your files.
- Nothing is overwritten. Each move is verified (file identity and SHA-256)
  before the original is removed. Locked or changed files are held.
- Every run is logged in **Move Log** and can be undone.

## Known limits

- Tempo, key and loudness are measured for WAV and AIFF only. MP3, FLAC and
  OGG use their tags and file names.
- Previews play WAV, AIFF, MP3, FLAC and OGG. AIFF is converted to WAV as it
  plays; compressed AIFC (such as ima4 or u-law) is not. REX files cannot be
  previewed; use Show in Explorer or drag the file to your DAW.
- The bundled Python is for 64-bit (x64) Windows 10 and 11. Windows 11 on ARM
  runs it through x64 emulation.

## For developers

Run `python tests/run_all.py`, then with the app running in fixture mode
`node tests/ui_smoke.js "http://127.0.0.1:8765/?fixture=1"` and
`node tests/ui_flow.js` (Node + Playwright, test tooling only). UI work follows
`docs/UI_PLAN.md`. `python tools/update_bundled_python.py [VERSION]` refreshes
the bundled Python from python.org and checks its published SHA-256. See
`CHANGELOG.md` for what changed.

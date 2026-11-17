# Sample Library Organizer 3.0.0

A Windows app that sorts your sample packs into one clean, searchable library
and finds loops that sound good together. It runs on your own PC, works
offline, and never changes a file until you approve a plan.

## Quick start

1. **Install Python** (one time). Get Python 3.10 or newer from
   https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** in
   the installer. Nothing else is needed.
2. **Download the app.** On the GitHub page click the green **Code** button,
   then **Download ZIP**. Unzip it somewhere permanent, for example
   `C:\Tools\SampleOrganizer`.
3. **Make a desktop icon.** Double-click `Create Desktop Shortcut.bat` once.
   From then on, open the app with the **Sample Organizer** icon on your
   desktop.
4. **Scan.** On the **Scan** screen, pick the folder that holds your packs
   (default `E:\Sample Packs`) and click **Start read-only scan**. Scanning only
   reads files; it never writes to your sample folders.
5. **Browse and play** in **Library**. Space plays, arrow keys move.
6. **Make a beat** in **Starter**: pick a key, tempo and genre, click
   **New set**, then **Play all**.
7. **Organize (optional).** In **Review**, build the plan, check it, approve
   it, then run it. Files are moved into `Samples` inside your packs folder.

If the icon does not start, double-click `Launch Sample Organizer.bat`. It
shows any error in a console window.

## Update to a new version without losing anything

Your scans, edits, favorites, collections, saved sets and settings live in
`%LOCALAPPDATA%\SampleOrganizer`, not in the app folder. To update:

1. Close the app.
2. Download the new ZIP and unzip it **over the old app folder** (or into a
   new folder, then run `Create Desktop Shortcut.bat` there again).
3. Open the app. Everything is where you left it. Run **Start read-only
   scan** once on the Scan screen to pick up the new key and loudness
   measurements.

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
- **Filters:** genre, key with **Compatible keys**, tempo with
  **Include half/double**, and **Verified only** (values from tags, names,
  folders or your edits, with no audio disagreement).

## Safety

- Scanning and browsing never change your files.
- Nothing is overwritten. Each move is verified (file identity and SHA-256)
  before the original is removed. Locked or changed files are held.
- Every run is logged in **Move Log** and can be undone.

## Known limits

- Tempo, key and loudness are measured for WAV and AIFF only. MP3, FLAC and
  OGG use their tags and file names.
- The browser preview cannot play AIFF or REX; use Reveal in Explorer or drag
  the file to your DAW.

## For developers

Run `python tests/run_all.py`, then with the app running in fixture mode
`node tests/ui_smoke.js "http://127.0.0.1:8765/?fixture=1"` and
`node tests/ui_flow.js` (Node + Playwright, test tooling only). UI work follows
`docs/UI_PLAN.md`. See `CHANGELOG.md` for what changed.

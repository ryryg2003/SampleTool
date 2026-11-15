# Sample Library Organizer

A local Windows app that turns `E:\Sample Packs` into a clean, searchable
library at `E:\Sample Packs\Samples`. Scanning and review are read-only. Your
files change only after you approve a specific plan inside the app.

## Requirements

- Windows 10 or 11 with the library on an NTFS drive (hard links need NTFS).
- Python 3.10 or newer. Get it from https://www.python.org/downloads/ (tick
  "Add python.exe to PATH") or run `winget install Python.Python.3.12`.
  Nothing else to install: the app uses only the Python standard library.
- Microsoft Edge (preinstalled on Windows) for the app window. Any browser works.

## Launch

1. Download this repository (green **Code** button, then **Download ZIP**) and
   unzip it anywhere, for example `C:\Tools\SampleTool`.
2. Double-click `Launch Sample Organizer.bat`.
3. A console window starts the local server and an app window opens at
   `http://127.0.0.1:8765/`. Close the console window to quit.

If your packs are not in `E:\Sample Packs`, edit the `set "ROOT=..."` line in
the `.bat` file, or change the folder on the Scan screen.

## First read-only scan

1. Open **Scan**, confirm the folder, and click **Start read-only scan**.
   The scan walks every file, computes SHA-256 hashes, reads audio headers,
   classifies samples and detects construction kits. It only opens files for
   reading. The `Samples` folder is inventoried separately so existing files
   take part in duplicate and collision checks, but it is never treated as
   incoming material.
2. Browse in **Library**: search packs by name or maker, filter and sort,
   audition with Space and the arrow keys, fix categories and metadata.
3. Open **Review**, build the plan, and inspect it by pack, kit, category or
   warning. Every source path appears exactly once with an action:
   `move`, `link` (NTFS hard link for identical content needed in more than
   one kit), `keep` (stays where it is), `hold` (needs your attention) or
   `exclude`.
4. Approve the exact plan, then run it. Any edit after approval clears the
   approval and you review the revised plan again.
5. **Move Log** shows every operation. Reports are written to
   `Samples\Catalog` after a run.

App state (scan cache, edits, plan, approval, journal) lives in
`%LOCALAPPDATA%\SampleOrganizer`, never in your sample folders.

## Safety model

- Nothing is overwritten. Destination checks are case-insensitive.
- Each move is a hard link to the destination, verified by file identity
  and SHA-256, then removal of the source. Locked files roll back and are held.
- Files that changed after approval are held, not moved.
- Junctions and other reparse points are never followed.
- A per-operation journal lets an interrupted run resume without repeating
  completed operations: launch again and run the same approved plan.
- Redundant duplicate source files stay in place. Nothing is deleted except a
  source after its verified move.

## Tests

`run_tests.bat` (Windows) or `python tests/run_all.py` runs the unit and
end-to-end suites against disposable synthetic fixtures.
`tests/WINDOWS_MANUAL_CHECKS.md` lists the checks that need real NTFS.

# Windows / NTFS manual checks

**Now automated on real Windows/NTFS** (`tests/test_windows.py`, run by GitHub
Actions on `windows-latest` with Python 3.10 and 3.13, plus `tests/ui_flow.js`):
case-insensitive collisions, exclusive and read-shared file locks, junctions not
followed, hard-link identity (`st_ino`, `st_nlink`, `fsutil hardlink list`),
paths over 260 characters, `Samples` excluded case-insensitively, and the full
scan → edit → approve → run flow in the UI. The checks below are still useful on
**your** PC (your drive, your antivirus, your DAW holding files open, the Edge
app window), and are the ones CI cannot reproduce.

Run them on the real Windows machine, **only against a disposable fixture** in
`%TEMP%\SampleOrganizerTest`.
Never point any of these commands at `E:\` or the real collection.

All commands are PowerShell unless marked `cmd`. Run from the repo folder.

## 0. Setup (fresh fixture + isolated app data)

```powershell
$T = "$env:TEMP\SampleOrganizerTest"
Remove-Item -Recurse -Force $T -ErrorAction SilentlyContinue
py -3 tools\make_fixtures.py "$T\Sample Packs"
$P = "$T\Sample Packs"; $D = "$T\appdata"
py -3 -m samplelib --root "$P" --data-dir "$D" --port 8799
```
Leave that window open; the app opens at `http://127.0.0.1:8799/`.
`run_tests.bat` should also pass on this machine (expect 0 failures).

## 1. Case-insensitive collision on NTFS
The fixture already has `Samples\One-Shots\Drums\Kick 01.wav` (library) and
incoming `Kick 01.wav` (two packs) plus `Free Samples\KICK 01.wav`, all with
different content. Make the library casing differ too:
```powershell
$lib = "$P\Samples\One-Shots\Drums"
Rename-Item "$lib\Kick 01.wav" "tmp-kick.wav"; Rename-Item "$lib\tmp-kick.wav" "kick 01.WAV"
$h0 = (Get-FileHash "$lib\kick 01.WAV").Hash
```
Scan, build plan. Expect each incoming kick gets its own suffixed destination
(`Kick 01 [Pack].wav` style); none equals `kick 01.WAV` in any casing.
After execute: `(Get-FileHash "$lib\kick 01.WAV").Hash -eq $h0` is `True` and
`Get-ChildItem $lib -Filter "kick 01*"` shows distinct names only.

## 2. Real locked file (held, nothing lost)
After approving the plan, in a **second** PowerShell window lock one source
exclusively and keep it locked while you click Execute:
```powershell
$f = "$env:TEMP\SampleOrganizerTest\Sample Packs\Free Samples\drums\snare_02.wav"
$h = [System.IO.File]::Open($f, 'Open', 'Read', 'None')   # FileShare.None
Read-Host "Locked. Run Execute in the app, then press Enter to release"
$h.Close()
```
Expect: that op is `held` with a "locked"/"in use" reason; the source still
exists with the same hash; no file at its planned destination; every other op
done. Run Execute again after releasing: it completes, earlier ops show
`already-done`.

## 3. Junction is not followed
```cmd
mkdir "%TEMP%\SampleOrganizerTest\Outside"
copy "%TEMP%\SampleOrganizerTest\Sample Packs\Free Samples\clap.wav" "%TEMP%\SampleOrganizerTest\Outside\outside.wav"
mklink /J "%TEMP%\SampleOrganizerTest\Sample Packs\Junction Pack" "%TEMP%\SampleOrganizerTest\Outside"
```
Rescan. Expect: no item from `Junction Pack` in the table, a "Skipped ...
reparse point" entry in progress errors, and after execute
`%TEMP%\SampleOrganizerTest\Outside\outside.wav` still exists untouched.
Also try a junction as a destination parent (after approve, before execute):
```cmd
rmdir "%TEMP%\SampleOrganizerTest\Sample Packs\Samples\Loops" 2>nul
mklink /J "%TEMP%\SampleOrganizerTest\Sample Packs\Samples\Loops" "%TEMP%\SampleOrganizerTest\Outside"
```
Expect: ops into `Samples\Loops` are held ("reparse point"), nothing written to `Outside`.
Remove junctions with `rmdir` (never `del /s`).

## 4. Hard-link identity
After a run, pick the kit stem that was linked (`Stems\Kick.wav` in both kits):
```cmd
fsutil hardlink list "%TEMP%\SampleOrganizerTest\Sample Packs\Samples\Complete Kits\<kit folder>\Stems\Kick.wav"
```
Expect both kit paths listed (same file). In PowerShell:
```powershell
Get-ChildItem -Recurse "$P\Samples" -File | Get-FileHash | Group-Object Hash | Where-Object Count -gt 1 |
  ForEach-Object { $_.Group.Path | ForEach-Object { fsutil hardlink list $_ }; "---" }
```
Every group must list the same set of links (one physical copy per hash).
`Sample_Catalog.csv` / `Duplicate_Report.csv` in `Samples\Catalog` should agree.

## 5. Long paths (> 260 characters)
```powershell
$deep = "$P\LongPath Maker - Long Pack\" + (("Very Long Folder Name 0123456789 " * 3).Trim() + "\") * 4
New-Item -ItemType Directory -Force "\\?\$deep" | Out-Null
Copy-Item "$P\Free Samples\perc loop 95bpm.wav" "\\?\$deep\Long Perc Loop 95bpm.wav"
("$deep\Long Perc Loop 95bpm.wav").Length   # must be > 260
```
Rescan, plan, execute. Expect the file is inventoried (no hash error), moves
correctly, and a deep kit destination also works. If the destination would be
> 260 chars and `LongPathsEnabled` is 0, the op must be held with a reason,
not fail silently.

## 6. Edge app window launch
Double-click `Launch Sample Organizer.bat` (edit it temporarily, or run
`py -3 -m samplelib --root "%TEMP%\SampleOrganizerTest\Sample Packs" --data-dir "%TEMP%\SampleOrganizerTest\appdata"`).
Expect an Edge `--app` window (no tabs/address bar), title shows the app.
Running it a second time must reuse the running server (message "already
running"), not start a second one. Closing the console stops the server.

## 7. Keyboard audition
In the sample table: arrow Up/Down moves the selection and auditions the
row; Space plays/stops; playback never blocks typing in the search box; `.aif`
and `.rex` rows show "preview unsupported" instead of an error. Check Range
requests in DevTools Network tab: `206 Partial Content`, and after auditioning
`Get-ChildItem -Recurse $P | Where-Object LastWriteTime -gt (Get-Date).AddMinutes(-5)`
returns nothing (no file touched by previews).

## 8. Read-only proof on NTFS
```powershell
function Snap { Get-ChildItem -Recurse -Force $P | Select-Object FullName, Length, LastWriteTimeUtc, @{n='H';e={if(!$_.PSIsContainer){(Get-FileHash $_.FullName).Hash}}} }
$before = Snap
# scan, browse, audition, edit, build plan (do NOT approve/execute)
Compare-Object $before (Snap) -Property FullName, Length, LastWriteTimeUtc, H   # must print nothing
```

## 9. Desktop shortcut, single window, idle quit, Settings
Automated: `tests/test_settings.py` (`WindowsDesktopTest`) runs
`Create Desktop Shortcut.bat` into a temp folder and checks the `.lnk` target,
arguments, working folder and icon; it also calls the window lookup. What only
a person at the desktop can confirm:

1. Run `Create Desktop Shortcut.bat` once. A "Sample Organizer" icon (amber bars
   on a dark square) appears on the desktop, including a OneDrive-redirected one.
2. Double-click it. No console window appears; the Edge app window opens and
   the taskbar/title bar show the app. `%LOCALAPPDATA%\SampleOrganizer\logs\app.log`
   gets the startup lines (collection, data folder, URL).
3. Minimise the window and double-click the shortcut again. The **same** window is
   restored and comes to the front, a toast says the app is already open, and
   no second window or second `pythonw.exe` appears (Task Manager). Repeat with the
   window behind another app instead of minimised. If Windows only flashes the
   taskbar button instead of raising the window, note it (foreground lock).
4. Close the window. Within about 3 minutes `pythonw.exe` exits (log line
   "shutting down"). Start a long scan, close the window during the scan: the
   process stays until the scan ends, then quits about 3 minutes later.
   Repeat with an approved run: it must never quit mid-run.
5. Put the PC to sleep with the window open for over 3 minutes, wake it: the app
   keeps working (the idle clock restarts after a sleep).
6. Settings: change the default volume and density, close and reopen from the
   shortcut; both stick. Save port 8790, restart: the URL uses 8790. Switch the
   collection folder to `%TEMP%\SampleOrganizerTest\Sample Packs`: the
   confirmation says the plan/approval are cleared, the Scan view shows the scan.
   While a scan runs the Switch button is disabled with an explanation.
7. `Launch Sample Organizer.bat` still opens a console showing the same messages
   (troubleshooting view) and uses the saved folder.

## Cleanup
```powershell
Get-ChildItem $T -Recurse -Attributes ReparsePoint | ForEach-Object { cmd /c rmdir "$($_.FullName)" }
Remove-Item -Recurse -Force "$env:TEMP\SampleOrganizerTest"
```

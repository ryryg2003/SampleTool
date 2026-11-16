@echo off
rem Console version of Sample Organizer, for troubleshooting: messages and errors
rem show in this window (they also go to %LOCALAPPDATA%\SampleOrganizer\logs\app.log).
rem For everyday use run "Create Desktop Shortcut.bat" once and use the desktop icon.
setlocal
title Sample Organizer console
cd /d "%~dp0"

rem Collection folder: leave empty to use the one saved in Settings
rem (first run: E:\Sample Packs). Set it to force a folder, e.g.
rem   set "ROOT=E:\Sample Packs"
set "ROOT="

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10 or newer is required but was not found.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
  echo or run:  winget install Python.Python.3.12
  pause
  exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo Your Python is older than 3.10. Please install a newer version from python.org.
  pause
  exit /b 1
)

echo Sample Organizer console (troubleshooting view). Closing this window quits the app.
if defined ROOT (
  %PY% -m samplelib --root "%ROOT%" %*
) else (
  %PY% -m samplelib %*
)
if errorlevel 1 pause

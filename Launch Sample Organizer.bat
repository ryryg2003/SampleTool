@echo off
rem Starts Sample Organizer. Double-click this file: the app opens in its own window
rem and this console shows messages and errors for troubleshooting (they also go to
rem %LOCALAPPDATA%\SampleOrganizer\logs\app.log). Closing the console quits the app.
rem "Create Desktop Shortcut.bat" makes a desktop icon that runs without a console.
rem
rem Python: the copy in the "python" folder next to this file is used (nothing to
rem install). If that folder is missing or cannot run, an installed Python 3.10+
rem is used instead.
setlocal
title Sample Organizer
cd /d "%~dp0"

rem Collection folder: leave empty to use the one saved in the app (the first run
rem asks you to pick it). Set it to force a folder, e.g.
rem   set "ROOT=D:\Music\Sample Packs"
set "ROOT="

set "PY="
if exist "%~dp0python\python.exe" (
  "%~dp0python\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY="%~dp0python\python.exe""
)
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo The bundled Python in the "python" folder is missing or could not start,
  echo and no installed Python was found.
  echo Download the app again and unzip all of it, or install Python 3.10 or newer
  echo from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^).
  pause
  exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo Your Python is older than 3.10. Please install a newer version from python.org.
  pause
  exit /b 1
)

echo Sample Organizer is starting. Closing this window quits the app.
if defined ROOT (
  %PY% -m samplelib --root "%ROOT%" %*
) else (
  %PY% -m samplelib %*
)
if errorlevel 1 pause

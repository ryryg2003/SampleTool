@echo off
setlocal
title Sample Library Organizer
cd /d "%~dp0"

rem Collection folder. Change this line if your packs live elsewhere.
set "ROOT=E:\Sample Packs"

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

%PY% -m samplelib --root "%ROOT%"
if errorlevel 1 pause

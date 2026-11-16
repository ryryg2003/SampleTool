@echo off
rem Creates a "Sample Organizer" shortcut on your desktop.
rem The shortcut starts the app without a console window (pythonw.exe) and uses
rem the collection folder saved in Settings. Logs go to
rem   %LOCALAPPDATA%\SampleOrganizer\logs\app.log
rem Uses only Windows PowerShell (built in). Nothing is downloaded.
setlocal
cd /d "%~dp0"
set "REPO=%~dp0"
set "REPO=%REPO:~0,-1%"

set "PYEXE="
set "PYW="
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if not defined PYEXE for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%P"
if defined PYEXE (
  "%PYEXE%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
  if errorlevel 1 (
    echo Your Python is older than 3.10. Please install a newer version from python.org.
    goto :fail
  )
  set "PYW=%PYEXE:python.exe=pythonw.exe%"
)
if defined PYW if not exist "%PYW%" set "PYW="
if not defined PYW for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW (
  echo Could not find pythonw.exe. Install Python 3.10 or newer from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH"^) or run:  winget install Python.Python.3.12
  goto :fail
)

rem SLO_SHORTCUT_DIR overrides the destination folder (used by the tests).
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "$d = $env:SLO_SHORTCUT_DIR; if (-not $d) { $d = [Environment]::GetFolderPath('Desktop') };" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Sample Organizer.lnk'));" ^
  "$s.TargetPath = $env:PYW; $s.Arguments = '-m samplelib'; $s.WorkingDirectory = $env:REPO;" ^
  "$s.IconLocation = (Join-Path $env:REPO 'ui\app.ico') + ',0'; $s.Description = 'Sample Library Organizer';" ^
  "$s.Save(); Write-Host ('Created ' + $s.FullName)"
if errorlevel 1 (
  echo Could not create the shortcut.
  goto :fail
)
echo.
echo Double-click "Sample Organizer" on your desktop to start the app.
echo It runs without a console window and quits by itself a few minutes after you close it
echo ^(never while a task is running^). For troubleshooting use "Launch Sample Organizer.bat".
if not defined SLO_NO_PAUSE pause
exit /b 0

:fail
if not defined SLO_NO_PAUSE pause
exit /b 1

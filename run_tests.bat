@echo off
rem Runs the Sample Library Organizer test suite (synthetic temp fixtures only).
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tests\run_all.py %*
) else (
  python tests\run_all.py %*
)
set RC=%errorlevel%
echo.
if not "%RC%"=="0" echo Some tests failed. Exit code %RC%.
pause
exit /b %RC%

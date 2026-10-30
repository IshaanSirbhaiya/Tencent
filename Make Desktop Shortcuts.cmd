@echo off
rem Double-click once: puts the Start, Stop, Reset and Offline buttons on the Desktop.
rem Walnut DataBridge Demo Day kit: docs\finals\demo_day\DEMO_DAY_RUNBOOK.md
rem Optional flags: --dry-run (show what would happen), --windowed, --no-browser, --rebuild
title Walnut DataBridge - Shortcuts
cd /d "%~dp0"
set "ARGS=%*"
if defined ARGS set "ARGS=%ARGS:--fresh=-Fresh%"
if defined ARGS set "ARGS=%ARGS:--dry-run=-DryRun%"
if defined ARGS set "ARGS=%ARGS:--no-browser=-NoBrowser%"
if defined ARGS set "ARGS=%ARGS:--no-warm=-NoWarm%"
if defined ARGS set "ARGS=%ARGS:--windowed=-Windowed%"
if defined ARGS set "ARGS=%ARGS:--rebuild=-Rebuild%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0databridge\scripts\demo_day.ps1" shortcuts %ARGS%
set RC=%ERRORLEVEL%
echo.
echo Press any key to close this window. Closing it does NOT stop the demo.
pause >nul
exit /b %RC%

@echo off
rem Double-click: back to a known clean state (asks whether to reseed the history too).
rem Walnut DataBridge Demo Day kit: docs\finals\demo_day\DEMO_DAY_RUNBOOK.md
rem Optional flags: --dry-run (show what would happen), --windowed, --no-browser, --rebuild, --fresh
title Walnut DataBridge - Reset
cd /d "%~dp0"
set "ARGS=%*"
if defined ARGS set "ARGS=%ARGS:--fresh=-Fresh%"
if defined ARGS set "ARGS=%ARGS:--dry-run=-DryRun%"
if defined ARGS set "ARGS=%ARGS:--no-browser=-NoBrowser%"
if defined ARGS set "ARGS=%ARGS:--no-warm=-NoWarm%"
if defined ARGS set "ARGS=%ARGS:--windowed=-Windowed%"
if defined ARGS set "ARGS=%ARGS:--rebuild=-Rebuild%"
if not defined ARGS (
  echo Reset brings the demo back to a clean state: Simulator reset, everyone signed out,
  echo the scripted questions warmed again. Takes about 30 seconds.
  echo.
  choice /c YN /n /t 15 /d N /m "Also WIPE and RESEED the demo history (about 3 minutes)? Y/N, default N in 15 s: "
  if errorlevel 2 (set "ARGS=") else (set "ARGS=-Fresh")
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0databridge\scripts\demo_day.ps1" reset %ARGS%
set RC=%ERRORLEVEL%
echo.
echo Press any key to close this window. Closing it does NOT stop the demo.
pause >nul
exit /b %RC%

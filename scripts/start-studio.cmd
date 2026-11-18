@echo off
rem One-click start for Project V: starts Docker services, control plane, media worker and dashboard,
rem then opens the dashboard. Double-click it (or the "Project V Studio" desktop shortcut).
title Project V Studio
echo Starting Project V Studio... (Docker Desktop must be running)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 (
  echo.
  echo Something did not start. Check the messages above, then press a key to close.
  pause >nul
  exit /b 1
)
start "" http://127.0.0.1:5173
echo.
echo Studio is up and the dashboard is open. You can close this window.
timeout /t 8 >nul

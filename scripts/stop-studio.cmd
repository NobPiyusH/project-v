@echo off
rem One-click stop for Project V (control plane, worker, dashboard; Docker services stay up).
title Stop Project V Studio
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
echo.
echo Project V Studio stopped. You can close this window.
timeout /t 6 >nul

@echo off
cd /d "%~dp0"
title guladuo - TUI
".venv\Scripts\python.exe" run.py %*
echo.
pause

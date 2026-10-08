@echo off
cd /d "%~dp0"
title chat.py - one-shot LLM
".venv\Scripts\python.exe" chat.py
echo.
pause

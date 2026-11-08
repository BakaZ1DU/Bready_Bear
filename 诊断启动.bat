@echo off
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting Bready Bear in console mode...
"%PY%" app.py
echo.
pause

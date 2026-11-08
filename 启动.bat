@echo off
setlocal
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "PYW=%~dp0runtime\pythonw.exe"
if not exist "%PYW%" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
"%PYW%" -c "import tkinter, PIL, requests" >nul 2>nul
if errorlevel 1 (
  echo [Bready Bear] Missing tkinter / Pillow / requests.
  echo Run install_deps.bat first.
  pause
  exit /b 1
)
start "" /D "%~dp0" "%PYW%" "%~dp0app.py"
exit /b 0

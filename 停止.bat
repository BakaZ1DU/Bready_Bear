@echo off
echo Stopping Bready Bear...
powershell -NoProfile -Command "Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*Bready Bear*' } | Stop-Process -Force"
echo Done.
pause

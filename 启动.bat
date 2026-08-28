@echo off
title PetCare Health Server - KEEP THIS WINDOW OPEN DURING DEMO
cd /d "%~dp0backend"
echo.
echo  [1/2] Starting local server...
echo  [2/2] Browser will open in 3 seconds: http://127.0.0.1:8000
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:8000"
echo.
.venv\Scripts\python.exe main.py
echo.
echo  Server stopped. Close this window or press any key.
pause >nul

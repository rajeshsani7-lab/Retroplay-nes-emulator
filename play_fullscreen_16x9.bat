@echo off
title RetroPlay NES Emulator - 16:9 Fullscreen
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    set "PYW=.venv\Scripts\pythonw.exe"
    goto :launch
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" (
    set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    goto :launch
)
where pyw >nul 2>&1 && set "PYW=pyw -3" && goto :launch
where pythonw >nul 2>&1 && set "PYW=pythonw" && goto :launch

echo Python not found. Run play.bat first for automatic setup.
pause
exit /b 1

:launch
if not "%~1"=="" (
    start "" %PYW% main.py "%~1" --fullscreen
) else (
    start "" %PYW% main.py --fullscreen
)

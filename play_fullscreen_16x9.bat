@echo off
title RetroPlay NES Emulator - 16:9 Fullscreen
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYCMD=.venv\Scripts\python.exe"
    goto :launch
)
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :launch
)
where py >nul 2>&1 && set "PYCMD=py -3" && goto :launch
where python >nul 2>&1 && set "PYCMD=python" && goto :launch

echo Python not found. Run play.bat first for automatic setup.
pause
exit /b 1

:launch
if not "%~1"=="" (
    "%PYCMD%" main.py "%~1" --fullscreen
) else (
    "%PYCMD%" main.py --fullscreen
)
if not %errorlevel%==0 pause

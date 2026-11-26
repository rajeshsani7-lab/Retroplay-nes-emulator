@echo off
title RetroPlay NES Emulator - 16:9 Fullscreen
cd /d "%~dp0"

echo ====================================================
echo   RetroPlay NES Emulator - 16:9 Fullscreen
echo ====================================================
echo.
echo Hotkeys:
echo   F11 = Toggle Fullscreen / Windowed
echo   F10 = Toggle 16:9 Widescreen / 4:3 Aspect
echo   ESC = Quit
echo   F5  = Reset  ^|  F7/F8 = Save/Load State
echo.

REM 1. Try Python 3.12 direct executable in AppData
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYCMD=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    goto :found
)

REM 2. Try Python 3.12 via py launcher
py -3.12 -c "import numpy, pygame" >nul 2>&1
if %errorlevel%==0 (
    set "PYCMD=py -3.12"
    goto :found
)

REM 3. Try standard python in PATH
python -c "import numpy, pygame" >nul 2>&1
if %errorlevel%==0 (
    set "PYCMD=python"
    goto :found
)

REM 4. Try default py launcher
py -c "import numpy, pygame" >nul 2>&1
if %errorlevel%==0 (
    set "PYCMD=py"
    goto :found
)

echo ERROR: Python with numpy, pygame-ce, or numba was not found.
echo Please run: pip install -r requirements.txt
pause
exit /b 1

:found
if not "%~1"=="" (
    %PYCMD% main.py "%~1" --fullscreen
    goto :end
)

%PYCMD% main.py --fullscreen

:end
pause

@echo off
title RetroPlay NES Emulator
cd /d "%~dp0"

echo =============================================
echo   RetroPlay NES Emulator - Launcher
echo =============================================
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

echo ERROR: Python with numpy or pygame-ce was not found.
echo Please run:
echo   py -3.12 -m pip install numpy pygame-ce
echo.
pause
exit /b 1

:found
REM Check if a ROM was passed as argument
if not "%~1"=="" (
    %PYCMD% main.py "%~1" --scale 3
    goto :end
)

REM Otherwise show ROM picker
echo ROMs available in .\roms\
echo.
%PYCMD% main.py --scale 3

:end
pause

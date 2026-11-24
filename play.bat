@echo off
title RetroPlay NES Emulator
cd /d "%~dp0"

echo =============================================
echo   RetroPlay NES Emulator - Launcher
echo =============================================
echo.

REM Try Python 3.12 first (most common system default)
py -3.12 -c "import numpy, pygame" >nul 2>&1
if %errorlevel%==0 (
    set PYVER=-3.12
    goto :found
)

REM Try Python 3.14
py -3.14 -c "import numpy, pygame" >nul 2>&1
if %errorlevel%==0 (
    set PYVER=-3.14
    goto :found
)

REM Try default py
py -c "import numpy, pygame" >nul 2>&1
if %errorlevel%==0 (
    set PYVER=
    goto :found
)

echo ERROR: numpy or pygame-ce not found.
echo Please run this command first:
echo   py -m pip install numpy pygame-ce
echo.
pause
exit /b 1

:found
REM Check if a ROM was passed as argument
if not "%~1"=="" (
    py %PYVER% main.py "%~1" --scale 3
    goto :end
)

REM Otherwise show ROM picker
echo ROMs available in .\roms\
echo.
py %PYVER% main.py --scale 3

:end
pause

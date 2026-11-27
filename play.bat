@echo off
title RetroPlay NES Emulator
cd /d "%~dp0"

REM 1. Prefer local .venv if already set up
if exist ".venv\Scripts\python.exe" (
    set "PYCMD=.venv\Scripts\python.exe"
    set "PYW=.venv\Scripts\pythonw.exe"
    goto :check_deps
)

REM 2. Find system Python
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "BASE_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    set "BASE_PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    goto :have_base
)
where py >nul 2>&1
if %errorlevel%==0 (
    set "BASE_PY=py -3"
    set "BASE_PYW=pyw -3"
    goto :have_base
)
where python >nul 2>&1
if %errorlevel%==0 (
    set "BASE_PY=python"
    set "BASE_PYW=pythonw"
    goto :have_base
)

echo ERROR: Python 3.10+ was not found on this PC.
echo Download Python from: https://www.python.org/downloads/
echo (Important: check "Add Python to PATH" during install)
echo.
pause
exit /b 1

:have_base
"%BASE_PY%" -c "import pygame, numpy, numba" >nul 2>&1
if %errorlevel%==0 (
    set "PYCMD=%BASE_PY%"
    set "PYW=%BASE_PYW%"
    goto :launch
)

echo First-time setup: creating local environment and installing dependencies...
"%BASE_PY%" -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo Failed to create .venv. Please run: pip install -r requirements.txt
    pause
    exit /b 1
)
set "PYCMD=.venv\Scripts\python.exe"
set "PYW=.venv\Scripts\pythonw.exe"
"%PYCMD%" -m pip install --upgrade pip >nul 2>&1
"%PYCMD%" -m pip install -r requirements.txt
if not %errorlevel%==0 (
    echo Dependency install failed. Check your internet connection and try again.
    pause
    exit /b 1
)
goto :launch

:check_deps
"%PYCMD%" -c "import pygame, numpy, numba" >nul 2>&1
if not %errorlevel%==0 (
    echo Installing missing dependencies...
    "%PYCMD%" -m pip install -r requirements.txt
)

:launch
if exist "%PYW%" (
    if not "%~1"=="" (
        start "" "%PYW%" main.py "%~1"
    ) else (
        start "" "%PYW%" main.py
    )
    exit /b 0
)
where "%PYW%" >nul 2>&1
if %errorlevel%==0 (
    if not "%~1"=="" (
        start "" %PYW% main.py "%~1"
    ) else (
        start "" %PYW% main.py
    )
    exit /b 0
)
if not "%~1"=="" (
    "%PYCMD%" main.py "%~1"
) else (
    "%PYCMD%" main.py
)

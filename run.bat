@echo off
chcp 65001 >nul
title SRS // BT-7274 Vanguard AI Assistant
cd /d "%~dp0"

echo ======================================================================
echo    SRS // BT-7274 Vanguard-Class Titan AI Assistant (Windows)
echo    Protocol 3: Protect the Pilot
echo ======================================================================
echo.

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

rem 1. Check for local virtual environment
if exist "venv\Scripts\python.exe" (
    echo [SYS] Using virtual environment (venv)...
    "venv\Scripts\python.exe" main.py
    goto :handle_exit
)

if exist ".venv\Scripts\python.exe" (
    echo [SYS] Using virtual environment (.venv)...
    ".venv\Scripts\python.exe" main.py
    goto :handle_exit
)

rem 2. Check for py launcher
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [SYS] Using Python launcher (py)...
    py -3 main.py
    goto :handle_exit
)

rem 3. Check for system python
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [SYS] Using system Python...
    python main.py
    goto :handle_exit
)

echo [ERROR] No Python interpreter found on your system!
echo Please install Python 3.11 or 3.12 from https://www.python.org/
echo (Make sure to check "Add Python to PATH" during installation).
echo.
pause
exit /b 1

:handle_exit
if %ERRORLEVEL% neq 0 (
    echo.
    echo ======================================================================
    echo [ALERT] BT-7274 exited with error code %ERRORLEVEL%.
    echo If dependencies are missing, run 'setup.bat' to install requirements.
    echo ======================================================================
    pause
)

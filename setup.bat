@echo off
chcp 65001 >nul
title SRS // BT-7274 Setup (Windows)
cd /d "%~dp0"

echo ======================================================================
echo    SRS // BT-7274 Automated Windows Setup
echo    Vanguard-Class Neural Link Environment Installer
echo ======================================================================
echo.

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

rem Find suitable Python command
set PY_CMD=
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PY_CMD=py -3
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set PY_CMD=python
    )
)

if "%PY_CMD%"=="" (
    echo [ERROR] Python not found on your system!
    echo Please install Python (3.11 or 3.12 recommended) from https://www.python.org/
    echo Remember to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [1/4] Found Python. Checking version...
%PY_CMD% -c "import sys; print('Python version:', sys.version.split()[0]); sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] BT-7274 requires Python 3.11 or newer.
    pause
    exit /b 1
)

echo.
echo [2/4] Setting up Python virtual environment (venv)...
if not exist "venv\Scripts\python.exe" (
    %PY_CMD% -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Virtual environment created successfully.
) else (
    echo Existing virtual environment found.
)

echo.
echo [3/4] Installing / Upgrading required Windows dependencies...
"venv\Scripts\python.exe" -m pip install --upgrade pip
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if %ERRORLEVEL% neq 0 (
    echo.
    echo [WARNING] Some dependencies encountered an issue during install.
    echo Retrying with pre-compiled wheels...
    "venv\Scripts\python.exe" -m pip install --only-binary :all: pywin32 pycaw comtypes
)

echo.
echo [4/4] Installing browser automation engines (Playwright Chromium)...
"venv\Scripts\python.exe" -m playwright install chromium firefox

echo.
echo ======================================================================
echo    [SUCCESS] BT-7274 Windows Setup Complete!
echo    Neural Link is ready. Double-click 'run.bat' to launch the assistant.
echo ======================================================================
echo.
pause

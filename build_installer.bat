@echo off
REM ==============================================================================
REM BT-7274 Native Windows Build & Inno Setup Packaging Script
REM Builds standalone executable and packages BT-7274-Setup.exe
REM ==============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==================================================================
echo   BT-7274 Native Windows Build & Installer Pipeline
echo ==================================================================

echo [1/3] Verifying Python environment...
python --version
if errorlevel 1 (
    echo ERROR: Python is not found on PATH!
    pause
    exit /b 1
)

echo [2/3] Compiling standalone Windows executable...
python build_windows.py --clean
if errorlevel 1 (
    echo ERROR: Standalone build failed!
    pause
    exit /b 1
)

echo [3/3] Compiling Inno Setup installer...
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "!ISCC!" (
    set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
)

if exist "!ISCC!" (
    "!ISCC!" installer.iss
    if errorlevel 1 (
        echo ERROR: Inno Setup compilation failed!
        pause
        exit /b 1
    )
    echo ==================================================================
    echo SUCCESS: Installer created at Output\BT-7274-Setup.exe
    echo ==================================================================
) else (
    echo WARNING: Inno Setup (ISCC.exe) was not found at standard paths.
    echo Standalone binary is ready at dist\BT-7274\BT-7274.exe
)

pause

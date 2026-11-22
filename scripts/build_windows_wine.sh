#!/usr/bin/env bash
# ==============================================================================
# BT-7274 Isolated Wine Build Script for Linux
# Builds Windows Standalone Executable & Setup.exe from Linux via Wine.
#
# GUARANTEE: Strictly isolated in /tmp/bt7274_wine_build.
#            NEVER touches ~/.wine, ~/.wine_luatools, or any existing prefix.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Strictly isolated Wine prefix
export WINEPREFIX="/tmp/bt7274_wine_build"
export WINEDEBUG="-all"
export WINEARCH="win64"

echo "=================================================================="
echo "  BT-7274 Windows Build Pipeline (Wine Sandbox)"
echo "=================================================================="
echo "Project Root: ${REPO_ROOT}"
echo "Isolated Prefix: ${WINEPREFIX}"

# Safety guard: ensure WINEPREFIX is strictly /tmp/bt7274_wine_build
if [[ "${WINEPREFIX}" != "/tmp/bt7274_wine_build" ]]; then
    echo "ERROR: Safety check failed! WINEPREFIX is not /tmp/bt7274_wine_build" >&2
    exit 1
fi

if ! command -v wine &>/dev/null; then
    echo "ERROR: 'wine' command not found. Please install Wine or use GitHub Actions." >&2
    exit 1
fi

# Clean previous build artifacts in repo
rm -rf "${REPO_ROOT}/build" "${REPO_ROOT}/dist" "${REPO_ROOT}/Output"

echo "Initialising isolated Wine prefix at ${WINEPREFIX}..."
mkdir -p "${WINEPREFIX}"
wineboot --init 2>/dev/null || true

PYTHON_VER="3.11.8"
PYTHON_INSTALLER="/tmp/python-${PYTHON_VER}-amd64.exe"
PYTHON_WIN="C:\\Python311\\python.exe"

# Install Windows Python into the isolated prefix if not present
if [ ! -f "${WINEPREFIX}/drive_c/Python311/python.exe" ]; then
    echo "Downloading Windows Python ${PYTHON_VER} installer..."
    curl -fsSL "https://www.python.org/ftp/python/${PYTHON_VER}/python-${PYTHON_VER}-amd64.exe" -o "${PYTHON_INSTALLER}"

    echo "Installing Windows Python into isolated Wine environment..."
    wine "${PYTHON_INSTALLER}" /quiet InstallAllUsers=1 PrependPath=1 TargetDir="C:\\Python311"
    rm -f "${PYTHON_INSTALLER}"
fi

# Verify Python
echo "Verifying Python in isolated prefix..."
wine "${PYTHON_WIN}" --version

# Install dependencies inside Wine
echo "Installing application dependencies inside isolated Wine environment..."
wine "${PYTHON_WIN}" -m pip install --upgrade pip
wine "${PYTHON_WIN}" -m pip install -r "${REPO_ROOT}/requirements.txt"
wine "${PYTHON_WIN}" -m pip install pyinstaller

# Run build_windows.py inside Wine
echo "Compiling standalone Windows executable..."
cd "${REPO_ROOT}"
wine "${PYTHON_WIN}" build_windows.py --compiler pyinstaller

# Verify standalone executable
if [ ! -f "${REPO_ROOT}/dist/BT-7274/BT-7274.exe" ]; then
    echo "ERROR: Standalone BT-7274.exe was not created!" >&2
    exit 1
fi
echo "✅ Standalone Windows executable successfully built at dist/BT-7274/BT-7274.exe"

# Check for Inno Setup (iscc)
ISCC_CMD=""
if command -v iscc &>/dev/null; then
    ISCC_CMD="iscc"
elif [ -f "${WINEPREFIX}/drive_c/Program Files (x86)/Inno Setup 6/ISCC.exe" ]; then
    ISCC_CMD="wine C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"
fi

if [ -n "${ISCC_CMD}" ]; then
    echo "Compiling Inno Setup installer..."
    mkdir -p "${REPO_ROOT}/Output"
    ${ISCC_CMD} "${REPO_ROOT}/installer.iss"
    if [ -f "${REPO_ROOT}/Output/BT-7274-Setup.exe" ]; then
        echo "✅ Output/BT-7274-Setup.exe created successfully!"
    fi
else
    echo "INFO: Inno Setup compiler (iscc) not found in Wine or Linux path."
    echo "      You can install Inno Setup 6 into the prefix or run the GitHub Actions workflow."
fi

echo "=================================================================="
echo "  Build Complete! Standalone folder is ready at: dist/BT-7274"
echo "=================================================================="

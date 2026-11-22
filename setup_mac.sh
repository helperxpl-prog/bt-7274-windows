#!/usr/bin/env bash
# ==============================================================================
# SRS // BT-7274 Vanguard-Class Titan AI Assistant (macOS Setup Script)
# Protocol 3: Protect the Pilot
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "======================================================================"
echo "   SRS // BT-7274 Automated macOS Setup"
echo "   Vanguard-Class Neural Link Environment Installer"
echo "======================================================================"
echo ""

# 1. Check for Homebrew
if command -v brew >/dev/null 2>&1; then
    echo "[1/4] Found Homebrew package manager."
    echo "      Installing required C-libraries (portaudio, opencv, tesseract, cliclick)..."
    brew install portaudio opencv tesseract cliclick || true
else
    echo "[1/4] Homebrew not found. For audio & OCR libraries, Homebrew is recommended:"
    echo "      /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
fi

# 2. Check Python 3.10+
PY_CMD=""
for cmd in python3.11 python3.12 python3.10 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 10 ]; then
            PY_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PY_CMD" ]; then
    echo "[ERROR] Python 3.10+ not found! Please install via Homebrew:"
    echo "        brew install python@3.11"
    exit 1
fi

echo ""
echo "[2/4] Found $PY_CMD ($VER). Setting up virtual environment (venv)..."
if [ ! -d "venv" ]; then
    "$PY_CMD" -m venv venv
    echo "      Virtual environment created at $DIR/venv"
else
    echo "      Existing virtual environment found."
fi

# 3. Upgrade pip and install dependencies
echo ""
echo "[3/4] Installing dependencies from requirements.txt..."
"$DIR/venv/bin/pip" install --upgrade pip setuptools wheel
"$DIR/venv/bin/pip" install -r "$DIR/requirements.txt" || {
    echo "[WARN] Standard install had warnings. Installing essential fallback packages..."
    "$DIR/venv/bin/pip" install sounddevice numpy opencv-python Pillow edge-tts google-genai
}

# 4. Permissions check & summary
echo ""
echo "[4/4] Verifying environment & permissions..."
chmod +x "$DIR/start_bt_mac.sh" "$DIR/BT-7274.command" 2>/dev/null || true

echo ""
echo "======================================================================"
echo "   BT-7274 macOS SETUP COMPLETE!"
echo "======================================================================"
echo "   To launch BT-7274 on macOS:"
echo "     1. Terminal:        ./start_bt_mac.sh"
echo "     2. Finder:          Double-click 'BT-7274.command'"
echo ""
echo "   macOS Permission Notice:"
echo "     - Accessibility:    Required for mouse gesture control and hotkeys"
echo "                         (System Settings -> Privacy & Security -> Accessibility)"
echo "     - Camera:           Required for webcam gesture recognition (AVFoundation)"
echo "                         (System Settings -> Privacy & Security -> Camera)"
echo "     - Microphone:       Required for voice input (CoreAudio)"
echo "                         (System Settings -> Privacy & Security -> Microphone)"
echo "======================================================================"

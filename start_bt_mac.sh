#!/usr/bin/env bash
# ==============================================================================
# SRS // BT-7274 Vanguard-Class Titan AI Assistant (macOS Launcher)
# Protocol 3: Protect the Pilot
# ==============================================================================
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "======================================================================"
echo "   SRS // BT-7274 Vanguard-Class Titan AI Assistant (macOS)"
echo "   Platform: Darwin ($(uname -m))"
echo "   Protocol 3: Protect the Pilot"
echo "======================================================================"
echo ""

export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

# 1. Locate Python virtual environment
PY_BIN=""
if [ -f "$DIR/venv/bin/python" ]; then
    echo "[SYS] Using virtual environment (venv)..."
    PY_BIN="$DIR/venv/bin/python"
elif [ -f "$DIR/.venv/bin/python" ]; then
    echo "[SYS] Using virtual environment (.venv)..."
    PY_BIN="$DIR/.venv/bin/python"
else
    # Check system python3
    if command -v python3 >/dev/null 2>&1; then
        PY_BIN="python3"
        echo "[SYS] Virtual environment not found; using system python3..."
        echo "[TIP] For best results on macOS, run './setup_mac.sh' first."
    else
        echo "[ERROR] python3 not found! Please install Python 3.11+ using Homebrew:"
        echo "        brew install python@3.11"
        exit 1
    fi
fi

# 2. Check macOS Accessibility Permissions (for gesture & window control)
echo "[SYS] Checking macOS Accessibility permissions..."
"$PY_BIN" -c "
import sys
try:
    from core.mac_input import is_accessibility_trusted
    if not is_accessibility_trusted():
        print('[WARN] macOS Accessibility permission is not yet granted for this terminal/app.')
        print('       Mouse clicks and window switching require Accessibility access.')
        print('       Grant access in: System Settings -> Privacy & Security -> Accessibility.')
    else:
        print('[SYS] macOS Accessibility permissions: GRANTED.')
except Exception as e:
    pass
" 2>/dev/null || true

# 3. Launch BT-7274
echo ""
echo "[SYS] Initializing Titan Neural Link & Vanguard HUD..."
echo "======================================================================"
exec "$PY_BIN" "$DIR/main.py" "$@"

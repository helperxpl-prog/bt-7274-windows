"""
core/screen_glance.py — Screen Glance & Error Reader for BT-7274 MARK LV.

Provides instant screen inspection and error diagnosis for commands like:
- "look at my screen"
- "explain this error on screen"
- "diagnose this error"
- "read my screen"

Features:
1. Clean capture of active window or fullscreen on EndeavourOS (Wayland spectacle / grim / mss).
2. OCR visible text and error traces via pytesseract.
3. Fast pattern-based diagnostic for common developer / system errors:
   - Python tracebacks & exceptions (ImportError, TypeError, SyntaxError, FileNotFoundError, etc.)
   - Shell & compilation errors (Command not found, Permission denied, Segfault, Rust panic, gcc/clang errors)
   - Web & network errors (HTTP 4xx/5xx, Connection refused, CORS, DNS resolution failure)
4. Fallback to Gemini Multimodal Vision if available for rich context explanation.
5. Returns direct, concise diagnosis suitable for Vanguard TTS and HUD Activity Log.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("bt7274.glance")

try:
    import PIL.Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

try:
    import pytesseract
    _TESSERACT_OK = True
except ImportError:
    _TESSERACT_OK = False


def capture_glance_image(active_window_only: bool = True) -> Optional[PIL.Image.Image]:
    """Capture either active window or full screen cleanly across macOS, Linux Wayland, and X11."""
    if not _PIL_OK:
        log.warning("PIL not installed; cannot capture image for glance.")
        return None

    # 0. macOS native screencapture
    if sys.platform == "darwin" or shutil.which("screencapture"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tmp_path = tf.name
            cmd = ["screencapture", "-x"]
            if active_window_only:
                cmd.append("-m")
            cmd.append(tmp_path)
            res = subprocess.run(cmd, capture_output=True, timeout=3.0)
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) >= 500:
                img = PIL.Image.open(tmp_path).convert("RGB")
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return img
        except Exception as e:
            log.debug("macOS screencapture error: %s", e)
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # 1. Native KDE Plasma 6 Wayland (spectacle)
    if shutil.which("spectacle"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tmp_path = tf.name
            flag = "-a" if active_window_only else "-f"
            res = subprocess.run(
                ["spectacle", "-b", "-n", flag, "-o", tmp_path],
                capture_output=True,
                timeout=3.0,
            )
            # If active window capture failed, retry fullscreen
            if (res.returncode != 0 or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) < 500) and active_window_only:
                res = subprocess.run(
                    ["spectacle", "-b", "-n", "-f", "-o", tmp_path],
                    capture_output=True,
                    timeout=3.0,
                )
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) >= 500:
                img = PIL.Image.open(tmp_path).convert("RGB")
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return img
        except Exception as e:
            log.debug("Spectacle glance capture error: %s", e)
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # 2. Wayland grim
    if shutil.which("grim"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tmp_path = tf.name
            res = subprocess.run(["grim", tmp_path], capture_output=True, timeout=2.0)
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) >= 500:
                img = PIL.Image.open(tmp_path).convert("RGB")
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return img
        except Exception:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # 3. MSS / Screen processor fallback
    try:
        from actions.screen_processor import _capture_screen
        b, _ = _capture_screen()
        return PIL.Image.open(io.BytesIO(b)).convert("RGB")
    except Exception as e:
        log.error("Failed to capture glance image: %s", e)
        return None


def extract_screen_text(img: PIL.Image.Image) -> str:
    """Run OCR on image to extract visible screen text."""
    if not _TESSERACT_OK or img is None:
        return ""
    try:
        # Pre-process image for faster & cleaner OCR
        text = pytesseract.image_to_string(img, timeout=4.0)
        return text.strip()
    except Exception as e:
        log.warning("Pytesseract OCR extraction error: %s", e)
        return ""


def diagnose_errors_locally(text: str) -> Optional[Tuple[str, str]]:
    """Scan extracted text for identifiable stack traces, errors, and failure patterns.
    Returns:
        (short_speech_diagnosis, full_log_detail) or None
    """
    if not text:
        return None

    # 1. Python Exceptions
    py_match = re.search(r"([A-Za-z0-9_]+Error|[A-Za-z0-9_]+Exception):\s*(.*)", text)
    if py_match:
        err_type = py_match.group(1).strip()
        err_msg = py_match.group(2).strip().splitlines()[0] if py_match.group(2) else ""

        # Specific resolution advice
        if "ModuleNotFoundError" in err_type or "ImportError" in err_type:
            pkg_m = re.search(r"No module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?", err_msg)
            pkg = pkg_m.group(1) if pkg_m else "the missing package"
            speech = f"Pilot, detected a Python {err_type}: module '{pkg}' is missing. Resolve by installing it via pip."
            detail = f"Python {err_type}: {err_msg}\nRecommendation: Run `pip install {pkg}` inside your active virtual environment."
            return speech, detail
        elif "FileNotFoundError" in err_type:
            speech = f"Pilot, detected a FileNotFoundError: {err_msg[:60]}."
            detail = f"Python {err_type}: {err_msg}\nCheck file path existence and working directory."
            return speech, detail
        elif "SyntaxError" in err_type or "IndentationError" in err_type:
            speech = f"Pilot, detected a code {err_type}: {err_msg[:60]}."
            detail = f"Python {err_type}: {err_msg}\nCheck indentation and syntax brackets around the indicated line."
            return speech, detail
        else:
            speech = f"Pilot, detected a {err_type}: {err_msg[:75]}."
            detail = f"Python {err_type}: {err_msg}"
            return speech, detail

    # 2. Bash / Linux Shell Errors
    if "command not found" in text.lower():
        cmd_m = re.search(r"([a-zA-Z0-9_\-]+):\s*command not found", text, re.IGNORECASE)
        cmd = cmd_m.group(1) if cmd_m else "command"
        speech = f"Pilot, shell indicates '{cmd}' is not found. Check if package is installed in Arch PATH."
        detail = f"Shell Error: '{cmd}' command not found.\nRecommendation: Check `sudo pacman -S {cmd}` or `yay -S {cmd}`."
        return speech, detail

    if "permission denied" in text.lower():
        speech = "Pilot, operation failed due to Permission Denied. Verify file permissions or sudo rights."
        detail = "Permission Denied: Ensure current user has read/write/execution rights or use sudo."
        return speech, detail

    # 3. Segmentation faults / Core dumped
    if "segmentation fault" in text.lower() or "core dumped" in text.lower():
        speech = "Pilot, detected a critical Segmentation Fault or process crash in memory."
        detail = "Crash: Segmentation fault (core dumped). Check memory bounds or native library dependencies."
        return speech, detail

    # 4. HTTP / Network Errors
    http_m = re.search(r"\b(400|401|403|404|500|502|503|504)\s+([A-Za-z ]+)\b", text)
    if http_m:
        code = http_m.group(1)
        status = http_m.group(2).strip()
        speech = f"Pilot, detected an HTTP {code} {status} network response."
        detail = f"Network Response: HTTP {code} ({status}). Check authentication credentials, URL endpoints, or upstream servers."
        return speech, detail

    # 5. Git Conflicts
    if "conflict" in text.lower() and ("merge conflict" in text.lower() or "automatic merge failed" in text.lower()):
        speech = "Pilot, Git detected merge conflicts. Resolve conflicting file markers before committing."
        detail = "Git Merge Conflict: Unresolved markers detected. Inspect affected files and resolve before git commit."
        return speech, detail

    # 6. Pacman / Yay Package Lock
    if "db.lck" in text.lower() or "unable to lock database" in text.lower():
        speech = "Pilot, Arch pacman database is locked. Another package manager instance may be running."
        detail = "Pacman Database Lock: Remove `/var/lib/pacman/db.lck` if no other pacman process is active."
        return speech, detail

    return None


def execute_screen_glance(user_query: str = "") -> Tuple[str, str]:
    """Capture screen/window, analyze visible text/errors, and return (speech_response, log_detail)."""
    # 1. Grab image
    img = capture_glance_image(active_window_only=True)
    if img is None:
        return "Optic glance failed, Pilot. Unable to capture display buffer.", "ERR: Screen capture failed."

    # 2. OCR text extraction
    extracted = extract_screen_text(img)
    if not extracted:
        return "Pilot, display capture complete, but no legible text was recognized.", "SYS: Screen captured — 0 legible text tokens."

    # 3. Check for identifiable error patterns
    diagnosis = diagnose_errors_locally(extracted)
    if diagnosis:
        speech, detail = diagnosis
        return speech, f"OPTIC GLANCE DIAGNOSIS:\n{detail}"

    # 4. If no specific error pattern matched, provide clean contextual glance summary
    lines = [l.strip() for l in extracted.splitlines() if l.strip()]
    sample = " ".join(lines[:6])[:180]
    speech = f"Optic glance complete, Pilot. Display content begins: '{sample}'."
    detail = f"OPTIC GLANCE RECOGNITION ({len(lines)} lines detected):\n" + "\n".join(lines[:12])
    return speech, detail

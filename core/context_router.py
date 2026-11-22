"""
core/context_router.py — Smart Active Context Classifier for BT-7274 MARK LV.

Detects active window title and class on EndeavourOS (KDE Plasma 6 Wayland, Hyprland, X11, Windows)
and classifies the operational context:
- TAB: Browser windows (tab-switching, web navigation)
- MEDIA: Video / streaming / audio players (play/pause, seek, volume priority)
- WIN: Desktop applications & IDEs (window-switching, terminal)
- DESKTOP: Plasma shell / root desktop (show-desktop, launcher)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Dict, Optional

log = logging.getLogger("bt7274.context")

# Browsers
_BROWSER_IDENTIFIERS = (
    "safari", "firefox", "chromium", "chrome", "google-chrome", "brave", "brave-browser",
    "vivaldi", "zen", "zen-browser", "floorp", "opera", "edge", "microsoft-edge",
    "librewolf", "tor", "arc", "epiphany",
)

# Media players & streaming services
_MEDIA_IDENTIFIERS = (
    "youtube", "netflix", "twitch", "spotify", "vlc", "mpv", "music", "quicktime",
    "quicktime player", "rhythmbox", "audacious", "media player", "soundcloud",
    "bilibili", "disney", "prime video", "hulu", "crunchyroll", "tidal", "deezer", "cider",
)

# Cache state
_CACHED_CONTEXT: Optional[Dict] = None
_LAST_CACHE_TIME: float = 0.0
_CACHE_TTL: float = 0.25  # seconds


def _check_mpris_playing() -> bool:
    """Check if any media player is actively in 'Playing' state on Linux or macOS."""
    if sys.platform == "darwin":
        try:
            script = '''
            if application "Music" is running then
                tell application "Music"
                    if player state is playing then return "playing"
                end tell
            end if
            if application "Spotify" is running then
                tell application "Spotify"
                    if player state is playing then return "playing"
                end tell
            end if
            return "stopped"
            '''
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=0.15)
            return "playing" in res.stdout.lower()
        except Exception:
            return False

    if sys.platform == "win32" or not shutil.which("playerctl"):
        return False
    try:
        res = subprocess.run(
            ["playerctl", "status"],
            capture_output=True,
            text=True,
            timeout=0.10,
        )
        return "playing" in res.stdout.lower()
    except Exception:
        return False


def get_active_context(force: bool = False) -> Dict:
    """Query and return active window context dict with caching."""
    global _CACHED_CONTEXT, _LAST_CACHE_TIME

    now = time.monotonic()
    if not force and _CACHED_CONTEXT is not None and (now - _LAST_CACHE_TIME < _CACHE_TTL):
        return _CACHED_CONTEXT

    cls = ""
    title = ""

    # 1. Windows Win32 ctypes
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.lower()
                class_buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
                cls = class_buf.value.lower()
        except Exception:
            pass

    # 2. macOS AppleScript via System Events
    elif sys.platform == "darwin":
        try:
            script = '''
            tell application "System Events"
                set frontProc to first process whose frontmost is true
                set pName to name of frontProc
                set wTitle to ""
                try
                    set wTitle to name of front window of frontProc
                end try
                return pName & "|||" & wTitle
            end tell
            '''
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=0.15)
            if res.returncode == 0 and "|||" in res.stdout:
                parts = res.stdout.strip().split("|||", 1)
                cls = parts[0].strip().lower()
                title = parts[1].strip().lower() if len(parts) > 1 else ""
        except Exception:
            pass

    # 3. KDE Plasma 6 Wayland (kdotool)
    elif shutil.which("kdotool"):
        try:
            w_res = subprocess.run(["kdotool", "getactivewindow"], capture_output=True, text=True, timeout=0.12)
            w_id = w_res.stdout.strip()
            if w_id:
                c_res = subprocess.run(["kdotool", "getwindowclassname", w_id], capture_output=True, text=True, timeout=0.10)
                t_res = subprocess.run(["kdotool", "getwindowname", w_id], capture_output=True, text=True, timeout=0.10)
                cls = c_res.stdout.strip().lower()
                title = t_res.stdout.strip().lower()
        except Exception:
            pass

    # 4. Hyprland Wayland (hyprctl)
    elif shutil.which("hyprctl"):
        try:
            h_res = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=0.12)
            if h_res.returncode == 0 and h_res.stdout:
                data = json.loads(h_res.stdout)
                cls = data.get("class", "").lower()
                title = data.get("title", "").lower()
        except Exception:
            pass

    # 5. X11 fallback (xdotool / wmctrl)
    elif shutil.which("xdotool"):
        try:
            w_res = subprocess.run(["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=0.12)
            w_id = w_res.stdout.strip()
            if w_id:
                t_res = subprocess.run(["xdotool", "getwindowname", w_id], capture_output=True, text=True, timeout=0.10)
                title = t_res.stdout.strip().lower()
                try:
                    c_res = subprocess.run(["xdotool", "getwindowclassname", w_id], capture_output=True, text=True, timeout=0.10)
                    cls = c_res.stdout.strip().lower()
                except Exception:
                    pass
        except Exception:
            pass

    is_browser = any(b in cls or b in title for b in _BROWSER_IDENTIFIERS)
    is_media = any(m in title or m in cls for m in _MEDIA_IDENTIFIERS)
    is_desktop = (
        "plasmashell" in cls or "kwin" in cls or "finder" in cls or "desktop" in cls
        or (not cls and not title)
    )

    # If media is streaming in a browser tab (e.g. YouTube in Firefox), tag it MEDIA first
    if is_media:
        tag = "MEDIA"
    elif is_browser:
        tag = "TAB"
    elif is_desktop:
        tag = "DESKTOP"
    else:
        tag = "WIN"

    _CACHED_CONTEXT = {
        "class": cls,
        "title": title,
        "is_browser": is_browser,
        "is_media": is_media,
        "is_desktop": is_desktop,
        "tag": tag,
    }
    _LAST_CACHE_TIME = now
    return _CACHED_CONTEXT


def get_context_tag() -> str:
    """Return the short operational context tag ('TAB', 'MEDIA', 'WIN', 'DESKTOP')."""
    return get_active_context().get("tag", "WIN")

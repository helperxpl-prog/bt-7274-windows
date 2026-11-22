"""
core/mac_input.py — Native macOS Input & Window Control Backend for BT-7274.

Provides native CoreGraphics / Quartz input simulation via ctypes for macOS,
handling cursor navigation, mouse clicks, drags, wheel scrolling, and modifier
keys without external binary requirements, with fallbacks to `cliclick` and `osascript`.
Also exposes Accessibility permission validation via `AXIsProcessTrusted()`.
"""
from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
from typing import Optional, Tuple

log = logging.getLogger("bt7274.mac_input")

_IS_MAC = sys.platform == "darwin"
_CG = None
_AS = None

# CoreGraphics Event Constants
kCGHIDEventTap = 0
kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGEventRightMouseDown = 3
kCGEventRightMouseUp = 4
kCGEventMouseMoved = 5
kCGEventLeftMouseDragged = 6
kCGEventRightMouseDragged = 7
kCGEventScrollWheel = 22
kCGEventKeyDown = 10
kCGEventKeyUp = 11

# Modifier flags
kCGEventFlagMaskCommand = 0x00100000
kCGEventFlagMaskShift   = 0x00020000
kCGEventFlagMaskAlphaShift = 0x00010000
kCGEventFlagMaskAlternate  = 0x00080000
kCGEventFlagMaskControl    = 0x00040000

# macOS Virtual Keycodes (subset used by assistant)
KEY_TAB = 48
KEY_SPACE = 49
KEY_RETURN = 36
KEY_ESCAPE = 53
KEY_COMMAND = 55
KEY_SHIFT = 56
KEY_OPTION = 58
KEY_CONTROL = 59
KEY_LEFT_BRACKET = 33
KEY_RIGHT_BRACKET = 30
KEY_F11 = 103

class CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

if _IS_MAC:
    try:
        _AS = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        _CG = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")

        # Function signatures
        _CG.CGEventCreateMouseEvent.restype = ctypes.c_void_p
        _CG.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, CGPoint, ctypes.c_uint32]

        _CG.CGEventCreateScrollWheelEvent2.restype = ctypes.c_void_p
        _CG.CGEventCreateScrollWheelEvent2.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32]

        _CG.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        _CG.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]

        _CG.CGEventSetFlags.restype = None
        _CG.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]

        _CG.CGEventPost.restype = None
        _CG.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]

        _CG.CFRelease.restype = None
        _CG.CFRelease.argtypes = [ctypes.c_void_p]

        _CG.CGEventGetLocation.restype = CGPoint
        _CG.CGEventGetLocation.argtypes = [ctypes.c_void_p]

        _CG.CGEventCreate.restype = ctypes.c_void_p
        _CG.CGEventCreate.argtypes = [ctypes.c_void_p]

        _AS.AXIsProcessTrusted.restype = ctypes.c_bool
        _AS.AXIsProcessTrusted.argtypes = []
    except Exception as e:
        log.warning(f"Could not initialize macOS CoreGraphics libraries: {e}")


def is_accessibility_trusted() -> bool:
    """Return True if macOS Accessibility permissions are granted to the running process."""
    if not _IS_MAC or _AS is None:
        return True
    try:
        return bool(_AS.AXIsProcessTrusted())
    except Exception:
        return False


def get_cursor_position() -> Tuple[float, float]:
    """Get current global cursor position (x, y) on macOS."""
    if _IS_MAC and _CG:
        try:
            ev = _CG.CGEventCreate(None)
            if ev:
                pt = _CG.CGEventGetLocation(ev)
                _CG.CFRelease(ev)
                return float(pt.x), float(pt.y)
        except Exception:
            pass

    # Fallback to pyautogui
    try:
        import pyautogui
        p = pyautogui.position()
        return float(p[0]), float(p[1])
    except Exception:
        return 0.0, 0.0


def send_relative_move(dx: int, dy: int) -> bool:
    """Move cursor by relative offset (dx, dy)."""
    if dx == 0 and dy == 0:
        return True

    # 1. CoreGraphics native
    if _IS_MAC and _CG:
        try:
            cx, cy = get_cursor_position()
            target = CGPoint(cx + dx, cy + dy)
            ev = _CG.CGEventCreateMouseEvent(None, kCGEventMouseMoved, target, 0)
            if ev:
                _CG.CGEventPost(kCGHIDEventTap, ev)
                _CG.CFRelease(ev)
                return True
        except Exception as e:
            log.debug(f"CoreGraphics move failed: {e}")

    # 2. cliclick CLI fallback
    if shutil.which("cliclick"):
        try:
            sign_x = f"+{dx}" if dx >= 0 else str(dx)
            sign_y = f"+{dy}" if dy >= 0 else str(dy)
            subprocess.run(["cliclick", f"m:{sign_x},{sign_y}"], check=False, timeout=0.15)
            return True
        except Exception:
            pass

    # 3. PyAutoGUI fallback
    try:
        import pyautogui
        pyautogui.moveRel(dx, dy, _pause=False)
        return True
    except Exception:
        return False


def send_left_click() -> bool:
    """Send left mouse click at current cursor position."""
    if _IS_MAC and _CG:
        try:
            cx, cy = get_cursor_position()
            pt = CGPoint(cx, cy)
            down = _CG.CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, pt, 0)
            up = _CG.CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, pt, 0)
            if down and up:
                _CG.CGEventPost(kCGHIDEventTap, down)
                _CG.CGEventPost(kCGHIDEventTap, up)
                _CG.CFRelease(down)
                _CG.CFRelease(up)
                return True
        except Exception as e:
            log.debug(f"CoreGraphics click failed: {e}")

    if shutil.which("cliclick"):
        try:
            subprocess.run(["cliclick", "c:."], check=False, timeout=0.15)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.click(button="left", _pause=False)
        return True
    except Exception:
        return False


def send_mouse_down() -> bool:
    """Press and hold left mouse button (start drag)."""
    if _IS_MAC and _CG:
        try:
            cx, cy = get_cursor_position()
            pt = CGPoint(cx, cy)
            down = _CG.CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, pt, 0)
            if down:
                _CG.CGEventPost(kCGHIDEventTap, down)
                _CG.CFRelease(down)
                return True
        except Exception as e:
            log.debug(f"CoreGraphics mouseDown failed: {e}")

    if shutil.which("cliclick"):
        try:
            subprocess.run(["cliclick", "dd:."], check=False, timeout=0.15)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.mouseDown(button="left", _pause=False)
        return True
    except Exception:
        return False


def send_mouse_up() -> bool:
    """Release held left mouse button (end drag)."""
    if _IS_MAC and _CG:
        try:
            cx, cy = get_cursor_position()
            pt = CGPoint(cx, cy)
            up = _CG.CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, pt, 0)
            if up:
                _CG.CGEventPost(kCGHIDEventTap, up)
                _CG.CFRelease(up)
                return True
        except Exception as e:
            log.debug(f"CoreGraphics mouseUp failed: {e}")

    if shutil.which("cliclick"):
        try:
            subprocess.run(["cliclick", "du:."], check=False, timeout=0.15)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.mouseUp(button="left", _pause=False)
        return True
    except Exception:
        return False


def send_scroll(direction: int, amount: int = 5) -> bool:
    """Send mouse wheel scroll (direction: 1 for up, -1 for down)."""
    delta = int(direction * amount)
    if _IS_MAC and _CG:
        try:
            ev = _CG.CGEventCreateScrollWheelEvent2(None, 0, 1, delta, 0)
            if ev:
                _CG.CGEventPost(kCGHIDEventTap, ev)
                _CG.CFRelease(ev)
                return True
        except Exception as e:
            log.debug(f"CoreGraphics scroll failed: {e}")

    try:
        import pyautogui
        pyautogui.scroll(delta * 20, _pause=False)
        return True
    except Exception:
        return False


def send_hotkey(key_code: int, flags: int = 0) -> bool:
    """Send a virtual key event with optional modifier flags."""
    if _IS_MAC and _CG:
        try:
            down = _CG.CGEventCreateKeyboardEvent(None, key_code, True)
            up = _CG.CGEventCreateKeyboardEvent(None, key_code, False)
            if flags:
                _CG.CGEventSetFlags(down, flags)
                _CG.CGEventSetFlags(up, flags)
            if down and up:
                _CG.CGEventPost(kCGHIDEventTap, down)
                _CG.CGEventPost(kCGHIDEventTap, up)
                _CG.CFRelease(down)
                _CG.CFRelease(up)
                return True
        except Exception as e:
            log.debug(f"CoreGraphics hotkey failed: {e}")
    return False


def send_command_tab() -> bool:
    """Trigger macOS Command+Tab application switcher."""
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to key code 48 using command down'],
            check=False,
            timeout=0.25,
        )
        return True
    except Exception:
        return send_hotkey(KEY_TAB, kCGEventFlagMaskCommand)


def send_tab_switch(forward: bool = True) -> bool:
    """Trigger browser tab switch on macOS (Command+Option+Right/Left or Command+Shift+]/[)."""
    try:
        direction_key = 30 if forward else 33  # ']' or '['
        flags = kCGEventFlagMaskCommand | kCGEventFlagMaskShift
        if send_hotkey(direction_key, flags):
            return True
    except Exception:
        pass

    try:
        script = (
            'tell application "System Events" to key code 30 using {command down, shift down}'
            if forward else
            'tell application "System Events" to key code 33 using {command down, shift down}'
        )
        subprocess.run(["osascript", "-e", script], check=False, timeout=0.25)
        return True
    except Exception:
        return False


def send_media_control(action: str) -> bool:
    """
    Control media playback on macOS via AppleScript (Music, Spotify, QuickTime).
    action: 'playpause', 'next', 'previous'
    """
    script = f'''
    try
        tell application "Music"
            if it is running then
                if "{action}" is "playpause" then playpause
                if "{action}" is "next" then next track
                if "{action}" is "previous" then previous track
                return
            end if
        end tell
    end try
    try
        tell application "Spotify"
            if it is running then
                if "{action}" is "playpause" then playpause
                if "{action}" is "next" then next track
                if "{action}" is "previous" then previous track
                return
            end if
        end tell
    end try
    '''
    try:
        res = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=0.35)
        return res.returncode == 0
    except Exception as e:
        log.debug(f"Media control AppleScript failed: {e}")
        return False


def emergency_release() -> None:
    """Immediately release held mouse buttons and clear modifier flags on macOS."""
    send_mouse_up()
    if _IS_MAC and _CG:
        try:
            cx, cy = get_cursor_position()
            pt = CGPoint(cx, cy)
            right_up = _CG.CGEventCreateMouseEvent(None, kCGEventRightMouseUp, pt, 1)
            if right_up:
                _CG.CGEventPost(kCGHIDEventTap, right_up)
                _CG.CFRelease(right_up)
        except Exception:
            pass

    if shutil.which("cliclick"):
        try:
            subprocess.run(["cliclick", "du:."], check=False, timeout=0.15)
        except Exception:
            pass

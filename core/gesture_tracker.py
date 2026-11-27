"""
core/gesture_tracker.py — BT-7274 Real-Time Webcam Hand Gesture Controller

Low-latency, low-resource hand tracking using OpenCV + MediaPipe Hands.
Runs smoothly on CPU/integrated GPU at ~30 FPS (640x480).
When stopped or standby is active, releases camera completely (0% CPU, /dev/video0 freed).

Gestures:
1. Closed Fist -> Reopen Hand Fully: Show Desktop (Win+D).
2. Closed Fist (held 1.8s): Disable gesture tracking.
3. Thumbs Up (held 0.45s): Play / Pause media (YouTube / system via MPRIS + key K).
4. 4 Fingers Extended (Thumb Folded): Switch tabs (Ctrl+Tab).
5. Pinky Only Extended: Switch tabs (Ctrl+Tab).
6. Open Palm (held 0.40s): Left click.
7. 2 Fingers Extended (Peace Sign): Vertical mouse scroll (up/down).
8. Pinch (Thumb + 1st Finger): Left click (Hold pinch = Hold Left Mouse Button Down / Drag).
9. 1 Finger Extended (Index Pointer): Silky relative mouse movement with 1-Euro adaptive low-pass filter.
10. 3 Fingers Extended (Index + Middle + Ring) + Horizontal Swipe: Switch Windows (Alt+Tab / Alt+Shift+Tab).
11. Thumb + Pinky Extended (Shaka) + Vertical Motion: System Volume Up/Down (wpctl).
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

try:
    import pyautogui
    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = False
    _HAVE_PYAUTOGUI = True
except Exception:
    pyautogui = None
    _HAVE_PYAUTOGUI = False

# MediaPipe detection: modern Tasks API or legacy solutions API
_HAVE_MEDIAPIPE = False
_USE_TASKS_API = False
_MP_LANDMARKER = None
_MP_HANDS_LEGACY = None

# MediaPipe Hand Joint Connections for Visual Skeleton Rendering
_HAND_CONNECTIONS = (
    # Thumb
    (0, 1), (1, 2), (2, 3), (3, 4),
    # Index
    (0, 5), (5, 6), (6, 7), (7, 8),
    # Middle
    (0, 9), (9, 10), (10, 11), (11, 12),
    # Ring
    (0, 13), (13, 14), (14, 15), (15, 16),
    # Pinky
    (0, 17), (17, 18), (18, 19), (19, 20),
    # Knuckle palm bridges
    (5, 9), (9, 13), (13, 17)
)


class OneEuroFilter:
    """
    Adaptive low-pass filter (Casiez et al., CHI 2012).
    Eliminates high-frequency sensor noise and anatomical micro-tremors when stationary,
    while removing lag and latency during rapid sweeping gestures.
    """
    def __init__(self, t0: float, x0: float, min_cutoff: float = 0.85, beta: float = 0.035, d_cutoff: float = 1.0):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.x_prev = float(x0)
        self.dx_prev = 0.0
        self.t_prev = float(t0)

    def filter(self, t: float, x: float) -> float:
        dt = max(1e-4, t - self.t_prev)
        self.t_prev = t
        a_d = self._alpha(dt, self.d_cutoff)
        dx = (x - self.x_prev) / dt
        dx_hat = a_d * dx + (1.0 - a_d) * self.dx_prev
        self.dx_prev = dx_hat
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = self._alpha(dt, cutoff)
        x_hat = a * x + (1.0 - a) * self.x_prev
        self.x_prev = x_hat
        return x_hat

    def reset(self, t0: float, x0: float) -> None:
        self.x_prev = float(x0)
        self.dx_prev = 0.0
        self.t_prev = float(t0)

    @staticmethod
    def _alpha(dt: float, cutoff: float) -> float:
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)


try:
    import mediapipe as mp
    if hasattr(mp, "tasks") and hasattr(mp.tasks, "vision"):
        _HAVE_MEDIAPIPE = True
        _USE_TASKS_API = True
    elif hasattr(mp, "solutions") and hasattr(mp.solutions, "hands"):
        _HAVE_MEDIAPIPE = True
        _MP_HANDS_LEGACY = mp.solutions.hands
except Exception:
    _HAVE_MEDIAPIPE = False


def _find_model_path() -> Optional[str]:
    # Look for hand_landmarker.task in config/, core/, or /tmp
    candidates = [
        Path(__file__).resolve().parent.parent / "config" / "hand_landmarker.task",
        Path(__file__).resolve().parent / "hand_landmarker.task",
        Path("/tmp/hand_landmarker.task"),
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 1000000:
            return str(p)
    return None


GESTURE_PROFILES = {
    "precision": {
        "name": "PRECISION",
        "min_cutoff": 0.12,
        "beta": 0.008,
        "sens_base": 1.10,
        "sens_max": 2.0,
        "scroll_threshold": 0.016,
        "scroll_cooldown": 0.06,
        "hold_mult": 1.25,
        "flick_threshold": 0.10,
    },
    "balanced": {
        "name": "BALANCED",
        "min_cutoff": 0.22,
        "beta": 0.014,
        "sens_base": 1.65,
        "sens_max": 2.8,
        "scroll_threshold": 0.012,
        "scroll_cooldown": 0.04,
        "hold_mult": 1.00,
        "flick_threshold": 0.08,
    },
    "snap": {
        "name": "SNAP/FLICK",
        "min_cutoff": 0.40,
        "beta": 0.035,
        "sens_base": 2.40,
        "sens_max": 3.6,
        "scroll_threshold": 0.008,
        "scroll_cooldown": 0.025,
        "hold_mult": 0.75,
        "flick_threshold": 0.05,
    },
}


class GestureTracker:
    def __init__(
        self,
        camera_index: int = 0,
        on_feedback: Optional[Callable[[str], None]] = None,
        on_swipe: Optional[Callable[[str], None]] = None,
        on_pan: Optional[Callable[[float, float], None]] = None,
        on_finger_pointer: Optional[Callable[[float, float, str], None]] = None,
        on_frame: Optional[Callable[[np.ndarray], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_disable: Optional[Callable[[], None]] = None,
    ):
        self.camera_index = camera_index
        self._current_profile: str = "balanced"
        self.on_feedback = on_feedback
        self.on_swipe = on_swipe
        self.on_pan = on_pan
        self.on_finger_pointer = on_finger_pointer
        self.on_frame = on_frame
        self.on_log = on_log
        self.on_disable = on_disable

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cap: Optional[cv2.VideoCapture] = None

        # Screen size for cursor mapping
        self._screen_w = 1920
        self._screen_h = 1080
        if _HAVE_PYAUTOGUI:
            try:
                sz = pyautogui.size()
                self._screen_w = sz.width
                self._screen_h = sz.height
            except Exception:
                pass

        # Exponential moving average (EMA) smoothing state for cursor
        self._smooth_x: Optional[float] = None
        self._smooth_y: Optional[float] = None
        self._alpha = 0.35  # EMA weight: lower = smoother, higher = more responsive
        self._dead_zone = 0.0055  # normalized coordinate dead-zone threshold

        # Gesture cooldowns & debounce timers
        self._last_click_time = 0.0
        self._click_cooldown = 0.50
        self._is_pinching: bool = False
        self._is_mouse_down: bool = False
        self._open_palm_start_time: Optional[float] = None
        self._open_palm_hold_duration = 0.40

        self._last_media_time = 0.0
        self._media_cooldown = 0.85
        self._palm_hold_start = 0.0

        self._last_track_time = 0.0
        self._track_cooldown = 0.50
        self._last_3f_x: Optional[float] = None
        self._last_3f_y: Optional[float] = None
        self._last_3f_time = 0.0

        self._last_vol_time = 0.0
        self._vol_cooldown = 0.18
        self._last_shaka_y: Optional[float] = None

        self._prev_scroll_y: Optional[float] = None
        self._last_scroll_time = 0.0
        self._2f_pts: list[tuple[float, float, float]] = []
        self._pinky_pts: list[tuple[float, float, float]] = []
        self._last_swipe_time: float = 0.0
        self._2f_grace_frames: int = 0
        self._pinky_grace_frames: int = 0
        self._pinky_hold_start: Optional[float] = None

        # Relative mouse cursor tracking state with 1-Euro adaptive low-pass filter
        self._filter_x: Optional[OneEuroFilter] = None
        self._filter_y: Optional[OneEuroFilter] = None
        self._prev_filt_x: Optional[float] = None
        self._prev_filt_y: Optional[float] = None
        self._sub_px_x: float = 0.0
        self._sub_px_y: float = 0.0
        self._smooth_dx: float = 0.0
        self._smooth_dy: float = 0.0
        self._pointer_lost_frames: int = 0
        self._prev_point_x: Optional[float] = None
        self._prev_point_y: Optional[float] = None

        # Thumbs up & closed fist timers
        self._last_thumbs_time = 0.0
        self._thumbs_cooldown = 0.65
        self._thumbs_start_time: Optional[float] = None
        self._thumbs_hold_duration = 0.30
        self._thumbs_grace_frames: int = 0
        self._last_tab_time = 0.0
        self._tab_cooldown = 0.55
        self._tab_start_time: Optional[float] = None
        self._tab_hold_duration = 0.35
        self._last_ctrl_tab_time = 0.0
        self._ctrl_tab_cooldown = 0.50
        self._fist_start_time: Optional[float] = None
        self._fist_hold_duration = 1.80
        self._fist_grace_frames: int = 0
        self._fist_seen_frames: int = 0
        self._fist_armed: bool = False
        self._fist_armed_t: float = 0.0
        self._last_desktop_time: float = 0.0
        self._desktop_cooldown: float = 0.90
        self._hold_progress: float = 0.0
        self._hold_label: str = ""
        self._detected_gesture_name: str = "SEARCHING"

        # Context-adaptive active window tracking
        self._3f_grace_frames: int = 0
        self._cached_ctx: dict = {"class": "", "title": "", "is_browser": False, "is_media": False, "tag": "DESKTOP"}
        self._last_ctx_time: float = 0.0

        # Virtual camera loopback broadcast (v4l2loopback /dev/video10)
        self._vcam_fd: Optional[int] = None
        self._vcam_available: bool = os.path.exists("/dev/video10")
        self._reading_from_vcam: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    def get_profile(self) -> str:
        return getattr(self, "_current_profile", "balanced")

    def set_profile(self, profile: str) -> bool:
        p = (profile or "").strip().lower()
        if p in GESTURE_PROFILES:
            self._current_profile = p
            self._filter_x = None
            self._filter_y = None
            prof_name = GESTURE_PROFILES[p]["name"]
            self._trigger_feedback(f"◈ GESTURE PROFILE: {prof_name} 🎯")
            return True
        return False

    def cycle_profile(self) -> str:
        keys = list(GESTURE_PROFILES.keys())
        cur = self.get_profile()
        idx = keys.index(cur) if cur in keys else 0
        next_p = keys[(idx + 1) % len(keys)]
        self.set_profile(next_p)
        return next_p

    def emergency_release(self) -> None:
        """Emergency kill-switch: release any held mouse buttons, clear pinch, release modifiers."""
        self._release_pinch()
        if getattr(self, "_is_mouse_down", False):
            self._is_mouse_down = False
            self._send_mouse_up()
        if sys.platform == "darwin":
            try:
                from core.mac_input import emergency_release as mac_release
                mac_release()
            except Exception:
                pass
        elif sys.platform != "win32":
            try:
                subprocess.run(["ydotool", "key", "125:0", "29:0", "56:0", "42:0"], check=False, timeout=0.15)
            except Exception:
                pass
            if shutil.which("xdotool"):
                try:
                    subprocess.run(["xdotool", "keyup", "Super", "Control", "Alt", "Shift"], check=False, timeout=0.15)
                except Exception:
                    pass
        self._filter_x = None
        self._filter_y = None
        self._sub_px_x = 0.0
        self._sub_px_y = 0.0
        self._smooth_dx = 0.0
        self._smooth_dy = 0.0

    def _trigger_feedback(self, text: str) -> None:
        if self.on_feedback:
            try:
                self.on_feedback(text)
            except Exception:
                pass

    def _send_relative_move(self, dx: int, dy: int) -> None:
        """Move cursor relative to current position (Windows native Win32 + macOS CoreGraphics + Wayland ydotool + X11 xdotool + PyAutoGUI fallback)."""
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.mouse_event(0x0001, int(dx), int(dy), 0, 0)
                return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.moveRel(dx, dy, _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_relative_move as mac_move
                if mac_move(dx, dy):
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.moveRel(dx, dy, _pause=False)
                except Exception:
                    pass
            return

        # 1. Wayland ydotool
        try:
            res = subprocess.run(
                ["ydotool", "mousemove", "--", str(dx), str(dy)],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. X11 xdotool fallback
        if shutil.which("xdotool"):
            try:
                res = subprocess.run(
                    ["xdotool", "mousemove_relative", "--", str(dx), str(dy)],
                    capture_output=True,
                    timeout=0.15,
                )
                if res.returncode == 0:
                    return
            except Exception:
                pass

        # 3. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.moveRel(dx, dy, _pause=False)
            except Exception:
                pass

    def _update_relative_cursor(self, now: float, raw_px: float, raw_py: float) -> None:
        """Update 1-Euro filtered cursor position and move mouse relatively using profile parameters."""
        prof = GESTURE_PROFILES.get(getattr(self, "_current_profile", "balanced"), GESTURE_PROFILES["balanced"])
        min_cutoff = prof.get("min_cutoff", 0.22)
        beta = prof.get("beta", 0.014)
        sens_base = prof.get("sens_base", 1.65)
        sens_max = prof.get("sens_max", 2.8)

        if self._filter_x is None or self._prev_filt_x is None:
            self._filter_x = OneEuroFilter(now, raw_px, min_cutoff=min_cutoff, beta=beta, d_cutoff=1.0)
            self._filter_y = OneEuroFilter(now, raw_py, min_cutoff=min_cutoff, beta=beta, d_cutoff=1.0)
            self._prev_filt_x = raw_px
            self._prev_filt_y = raw_py
            self._sub_px_x = 0.0
            self._sub_px_y = 0.0
            self._smooth_dx = 0.0
            self._smooth_dy = 0.0
        else:
            filt_px = self._filter_x.filter(now, raw_px)
            filt_py = self._filter_y.filter(now, raw_py)

            dx = filt_px - self._prev_filt_x
            dy = filt_py - self._prev_filt_y
            self._prev_filt_x = filt_px
            self._prev_filt_y = filt_py

            mag = math.hypot(dx, dy)
            dead_zone = 0.0010
            if mag < dead_zone:
                scale = (mag / dead_zone) ** 2.0
                effective_mag = mag * scale
            else:
                effective_mag = mag

            if effective_mag > 1e-6:
                ux = dx / mag
                uy = dy / mag

                speed_gain = min(sens_max, 1.15 + (effective_mag * 35.0) ** 1.1)
                sens = sens_base * speed_gain

                target_dx = ux * effective_mag * sens * self._screen_w
                target_dy = uy * effective_mag * sens * self._screen_h

                self._smooth_dx = self._smooth_dx * 0.40 + target_dx * 0.60
                self._smooth_dy = self._smooth_dy * 0.40 + target_dy * 0.60

                move_x = self._smooth_dx + self._sub_px_x
                move_y = self._smooth_dy + self._sub_px_y

                step_x = int(move_x)
                step_y = int(move_y)
                self._sub_px_x = move_x - step_x
                self._sub_px_y = move_y - step_y

                if step_x != 0 or step_y != 0:
                    self._send_relative_move(step_x, step_y)

    def _send_left_click(self) -> None:
        """Trigger Left Click (Windows native Win32 + macOS CoreGraphics + Wayland ydotool + X11 xdotool + PyAutoGUI fallback)."""
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.mouse_event(0x0002 | 0x0004, 0, 0, 0, 0)
                return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.click(button="left", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_left_click as mac_click
                if mac_click():
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.click(button="left", _pause=False)
                except Exception:
                    pass
            return

        # 1. Wayland ydotool
        try:
            res = subprocess.run(
                ["ydotool", "click", "0xC0"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. X11 xdotool fallback
        if shutil.which("xdotool"):
            try:
                res = subprocess.run(["xdotool", "click", "1"], capture_output=True, timeout=0.15)
                if res.returncode == 0:
                    return
            except Exception:
                pass

        # 3. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.click(button="left", _pause=False)
            except Exception:
                pass

    def _send_mouse_down(self) -> None:
        """Press and hold Left Mouse Button down (Windows native Win32 + macOS CoreGraphics + Wayland ydotool + X11 xdotool + PyAutoGUI fallback)."""
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
                return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.mouseDown(button="left", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_mouse_down as mac_down
                if mac_down():
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.mouseDown(button="left", _pause=False)
                except Exception:
                    pass
            return

        # 1. Wayland ydotool
        try:
            res = subprocess.run(
                ["ydotool", "click", "0x40"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. X11 xdotool fallback
        if shutil.which("xdotool"):
            try:
                res = subprocess.run(["xdotool", "mousedown", "1"], capture_output=True, timeout=0.15)
                if res.returncode == 0:
                    return
            except Exception:
                pass

        # 3. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.mouseDown(button="left", _pause=False)
            except Exception:
                pass

    def _send_mouse_up(self) -> None:
        """Release Left Mouse Button (Windows native Win32 + macOS CoreGraphics + Wayland ydotool + X11 xdotool + PyAutoGUI fallback)."""
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
                return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.mouseUp(button="left", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_mouse_up as mac_up
                if mac_up():
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.mouseUp(button="left", _pause=False)
                except Exception:
                    pass
            return

        # 1. Wayland ydotool
        try:
            res = subprocess.run(
                ["ydotool", "click", "0x80"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. X11 xdotool fallback
        if shutil.which("xdotool"):
            try:
                res = subprocess.run(["xdotool", "mouseup", "1"], capture_output=True, timeout=0.15)
                if res.returncode == 0:
                    return
            except Exception:
                pass

        # 3. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.mouseUp(button="left", _pause=False)
            except Exception:
                pass
            try:
                pyautogui.mouseUp(button="left", _pause=False)
            except Exception:
                pass

    def _release_pinch(self) -> None:
        """Release pinch state and release held left mouse button."""
        self._is_pinching = False
        if self._is_mouse_down:
            self._is_mouse_down = False
            self._send_mouse_up()
            self._trigger_feedback("◈ GESTURE: PINCH RELEASE (MOUSE UP) 🖐")

    def _send_alt_tab(self) -> None:
        """Trigger Alt+Tab application/window switch (Windows native + macOS Command+Tab + KDE Plasma 6 KWin DBus + Wayland ydotool + PyAutoGUI fallback)."""
        if sys.platform == "win32":
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("alt", "tab", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_command_tab
                if send_command_tab():
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("command", "tab", _pause=False)
                except Exception:
                    pass
            return

        # 1. Native KDE Plasma 6 Wayland shortcut via KWin (instant, direct compositor window switch)
        try:
            res = subprocess.run(
                ["qdbus6", "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Walk Through Windows"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. Native Wayland ydotool: Alt down (56:1), Tab down (15:1), Tab up (15:0), Alt up (56:0)
        try:
            res = subprocess.run(
                ["ydotool", "key", "56:1", "15:1", "15:0", "56:0"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 3. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.hotkey("alt", "tab", _pause=False)
            except Exception:
                pass

    _send_switch_window = _send_alt_tab

    def _send_alt_shift_tab(self) -> None:
        """Trigger Alt+Shift+Tab application/window switch reverse (Windows native + KDE Plasma 6 KWin DBus + Wayland ydotool + PyAutoGUI fallback)."""
        if sys.platform == "win32":
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("alt", "shift", "tab", _pause=False)
                except Exception:
                    pass
            return

        # 1. Native KDE Plasma 6 Wayland shortcut via KWin
        try:
            res = subprocess.run(
                ["qdbus6", "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Walk Through Windows (Reverse)"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. Native Wayland ydotool: Alt down (56:1), Shift down (42:1), Tab down (15:1), Tab up (15:0), Shift up (42:0), Alt up (56:0)
        try:
            res = subprocess.run(
                ["ydotool", "key", "56:1", "42:1", "15:1", "15:0", "42:0", "56:0"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 3. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.hotkey("alt", "shift", "tab", _pause=False)
            except Exception:
                pass

    _send_switch_window_prev = _send_alt_shift_tab

    def _send_show_desktop(self) -> None:
        """Trigger Win+D (Show Desktop) across Windows, KDE Plasma 6 Wayland, X11, and PyAutoGUI."""
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                VK_LWIN = 0x5B
                VK_D = 0x44
                KEYEVENTF_KEYUP = 0x0002
                user32.keybd_event(VK_LWIN, 0, 0, 0)
                user32.keybd_event(VK_D, 0, 0, 0)
                user32.keybd_event(VK_D, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
                return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("win", "d", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                subprocess.run(
                    ["osascript", "-e", 'tell application "System Events" to key code 103'],
                    check=False,
                    timeout=0.25,
                )
                return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("f11", _pause=False)
                except Exception:
                    pass
            return

        # 0. Hyprland Wayland compositor support
        desktop_env = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if "hyprland" in desktop_env or os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            try:
                res = subprocess.run(
                    ["hyprctl", "dispatch", "togglespecialworkspace"],
                    capture_output=True,
                    timeout=0.15,
                )
                if res.returncode == 0:
                    return
            except Exception:
                pass

        # 1. Native KDE Plasma 6 Wayland DBus (EndeavourOS / Arch / Fedora / Ubuntu KDE)
        try:
            res = subprocess.run(
                ["qdbus6", "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Show Desktop"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        try:
            res = subprocess.run(
                ["qdbus", "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Show Desktop"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. Native Wayland ydotool (Super: 125, D: 32)
        try:
            res = subprocess.run(
                ["ydotool", "key", "125:1", "32:1", "32:0", "125:0"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 3. X11 xdotool
        try:
            res = subprocess.run(
                ["xdotool", "key", "super+d"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 4. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.hotkey("super", "d", _pause=False)
            except Exception:
                try:
                    pyautogui.hotkey("win", "d", _pause=False)
                except Exception:
                    pass

    def _send_ctrl_tab(self) -> None:
        """Trigger Ctrl+Tab browser/app tab switch (Windows native + macOS Command+Shift+] + Wayland ydotool + PyAutoGUI fallback)."""
        if sys.platform == "win32":
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("ctrl", "tab", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_tab_switch
                if send_tab_switch(forward=True):
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("command", "shift", "]", _pause=False)
                except Exception:
                    pass
            return

        # 1. Native Wayland ydotool: Ctrl down (29:1), Tab down (15:1), Tab up (15:0), Ctrl up (29:0)
        try:
            res = subprocess.run(
                ["ydotool", "key", "29:1", "15:1", "15:0", "29:0"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.hotkey("ctrl", "tab", _pause=False)
            except Exception:
                pass

    _send_switch_tab = _send_ctrl_tab

    def _send_ctrl_shift_tab(self) -> None:
        """Trigger Ctrl+Shift+Tab browser/app tab switch reverse (Windows + macOS Command+Shift+[ + Wayland ydotool + PyAutoGUI)."""
        if sys.platform == "win32":
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("ctrl", "shift", "tab", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_tab_switch
                if send_tab_switch(forward=False):
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("command", "shift", "[", _pause=False)
                except Exception:
                    pass
            return

        # 1. Native Wayland ydotool: Ctrl down (29:1), Shift down (42:1), Tab down (15:1), Tab up (15:0), Shift up (42:0), Ctrl up (29:0)
        try:
            res = subprocess.run(
                ["ydotool", "key", "29:1", "42:1", "15:1", "15:0", "42:0", "29:0"],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.hotkey("ctrl", "shift", "tab", _pause=False)
            except Exception:
                pass

    _send_switch_tab_prev = _send_ctrl_shift_tab

    def _get_active_context(self) -> dict:
        """Detect active focused window context (delegating to core.context_router with local fallback)."""
        try:
            from core.context_router import get_active_context
            return get_active_context()
        except Exception:
            pass

        now = time.monotonic()
        if hasattr(self, "_cached_ctx") and (now - getattr(self, "_last_ctx_time", 0.0) < 0.30):
            return self._cached_ctx

        cls, title = "", ""
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
        # 1. KDE Plasma 6 Wayland (kdotool)
        elif shutil.which("kdotool"):
            try:
                w_id = subprocess.check_output(["kdotool", "getactivewindow"], timeout=0.10).decode().strip()
                if w_id:
                    cls = subprocess.check_output(["kdotool", "getwindowclassname", w_id], timeout=0.10).decode().strip().lower()
                    title = subprocess.check_output(["kdotool", "getwindowname", w_id], timeout=0.10).decode().strip().lower()
            except Exception:
                pass
        # 2. Hyprland Wayland (hyprctl)
        elif shutil.which("hyprctl"):
            try:
                out = subprocess.check_output(["hyprctl", "activewindow", "-j"], timeout=0.10).decode()
                import json
                data = json.loads(out)
                cls = data.get("class", "").lower()
                title = data.get("title", "").lower()
            except Exception:
                pass
        # 3. X11 fallback (xdotool)
        elif shutil.which("xdotool"):
            try:
                w_id = subprocess.check_output(["xdotool", "getactivewindow"], timeout=0.10).decode().strip()
                if w_id:
                    title = subprocess.check_output(["xdotool", "getwindowname", w_id], timeout=0.10).decode().strip().lower()
                    try:
                        cls = subprocess.check_output(["xdotool", "getwindowclassname", w_id], timeout=0.10).decode().strip().lower()
                    except Exception:
                        pass
            except Exception:
                pass

        is_browser = any(b in cls or b in title for b in ("firefox", "chromium", "chrome", "brave", "vivaldi", "zen", "opera", "edge", "librewolf", "tor", "arc"))
        is_media = any(m in title or m in cls for m in ("youtube", "netflix", "twitch", "spotify", "vlc", "mpv", "media player", "soundcloud", "bilibili"))
        tag = "MEDIA" if is_media else ("BROWSER" if is_browser else "DESKTOP")

        self._cached_ctx = {
            "class": cls,
            "title": title,
            "is_browser": is_browser,
            "is_media": is_media,
            "tag": tag,
        }
        self._last_ctx_time = now
        return self._cached_ctx

    def _send_next_video(self) -> None:
        """Advance to next video/track (Windows native + macOS Music/Spotify + playerctl next + hardware multimedia KEY_NEXTSONG 163 + YouTube Shift+N)."""
        if sys.platform == "win32":
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.press("nexttrack", _pause=False)
                except Exception:
                    pass
                try:
                    pyautogui.hotkey("shift", "n", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_media_control
                if send_media_control("next"):
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("shift", "n", _pause=False)
                except Exception:
                    pass
            return

        # 1. MPRIS playerctl next
        try:
            subprocess.run(["playerctl", "next"], check=False, timeout=0.15)
        except Exception:
            pass

        # 2. Linux evdev hardware multimedia key KEY_NEXTSONG (163) via ydotool
        try:
            subprocess.run(["ydotool", "key", "-d", "20", "163:1", "163:0"], check=False, timeout=0.15)
        except Exception:
            pass

        # 3. YouTube web next video shortcut (Shift+N: key 42 + 49 with 25ms delay)
        try:
            subprocess.run(["ydotool", "key", "-d", "25", "42:1", "49:1", "49:0", "42:0"], check=False, timeout=0.20)
        except Exception:
            pass

        # 4. PyAutoGUI fallbacks
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.hotkey("shift", "n", _pause=False)
            except Exception:
                pass
            try:
                pyautogui.press("nexttrack", _pause=False)
            except Exception:
                pass

    def _send_prev_video(self) -> None:
        """Return to previous video/track (Windows native + macOS Music/Spotify + playerctl previous + hardware multimedia KEY_PREVIOUSSONG 165 + YouTube Shift+P)."""
        if sys.platform == "win32":
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.press("prevtrack", _pause=False)
                except Exception:
                    pass
                try:
                    pyautogui.hotkey("shift", "p", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_media_control
                if send_media_control("previous"):
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.hotkey("shift", "p", _pause=False)
                except Exception:
                    pass
            return

        # 1. MPRIS playerctl previous
        try:
            subprocess.run(["playerctl", "previous"], check=False, timeout=0.15)
        except Exception:
            pass

        # 2. Linux evdev hardware multimedia key KEY_PREVIOUSSONG (165) via ydotool
        try:
            subprocess.run(["ydotool", "key", "-d", "20", "165:1", "165:0"], check=False, timeout=0.15)
        except Exception:
            pass

        # 3. YouTube web prev video shortcut (Shift+P: key 42 + 25 with 25ms delay)
        try:
            subprocess.run(["ydotool", "key", "-d", "25", "42:1", "25:1", "25:0", "42:0"], check=False, timeout=0.20)
        except Exception:
            pass

        # 4. PyAutoGUI fallbacks
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.hotkey("shift", "p", _pause=False)
            except Exception:
                pass
            try:
                pyautogui.press("prevtrack", _pause=False)
            except Exception:
                pass

    def _send_scroll(self, direction: int) -> None:
        """Trigger mouse wheel scroll (Windows native Win32 + macOS CoreGraphics + Wayland ydotool + X11 xdotool + PyAutoGUI fallback)."""
        if sys.platform == "win32":
            try:
                import ctypes
                delta = 120 if direction > 0 else -120
                ctypes.windll.user32.mouse_event(0x0800, 0, 0, delta, 0)
                return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.scroll(140 if direction > 0 else -140, _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_scroll as mac_scroll
                if mac_scroll(direction):
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.scroll(140 if direction > 0 else -140, _pause=False)
                except Exception:
                    pass
            return

        wheel_y = 1 if direction > 0 else -1
        # 1. Native Wayland ydotool
        try:
            res = subprocess.run(
                ["ydotool", "mousemove", "-w", "-x", "0", "-y", str(wheel_y)],
                capture_output=True,
                timeout=0.15,
            )
            if res.returncode == 0:
                return
        except Exception:
            pass

        # 2. X11 xdotool fallback (button 4 = up, button 5 = down)
        if shutil.which("xdotool"):
            btn = "4" if direction > 0 else "5"
            try:
                res = subprocess.run(["xdotool", "click", btn], capture_output=True, timeout=0.15)
                if res.returncode == 0:
                    return
            except Exception:
                pass

        # 3. PyAutoGUI fallback
        if _HAVE_PYAUTOGUI:
            try:
                pyautogui.scroll(140 if direction > 0 else -140, _pause=False)
            except Exception:
                pass

    def _send_play_pause(self) -> None:
        """Toggle media playback (YouTube, Spotify, etc.) via MPRIS and/or key synthetic fallback."""
        if sys.platform == "win32":
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.press("playpause", _pause=False)
                except Exception:
                    pass
                try:
                    pyautogui.press("k", _pause=False)
                except Exception:
                    pass
            return

        if sys.platform == "darwin":
            try:
                from core.mac_input import send_media_control
                if send_media_control("playpause"):
                    return
            except Exception:
                pass
            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.press("playpause", _pause=False)
                except Exception:
                    pass
                try:
                    pyautogui.press("k", _pause=False)
                except Exception:
                    pass
            return

        handled = False
        try:
            res = subprocess.run(
                ["playerctl", "play-pause"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=0.25,
            )
            if res.returncode == 0:
                handled = True
        except Exception:
            pass

        if not handled:
            # 1. Hardware Media Play/Pause (KEY_PLAYPAUSE = 164)
            try:
                subprocess.run(["ydotool", "key", "164:1", "164:0"], check=False, timeout=0.15)
            except Exception:
                pass

            # 2. Universal YouTube play/pause shortcut 'k' (KEY_K = 37)
            # NEVER send Space (KEY_SPACE=57) — Space opens focused links (e.g. YouTube Mixes) and scrolls!
            try:
                subprocess.run(["ydotool", "key", "37:1", "37:0"], check=False, timeout=0.15)
            except Exception:
                pass

            if _HAVE_PYAUTOGUI:
                try:
                    pyautogui.press("k", _pause=False)
                except Exception:
                    pass

    def _broadcast_vcam_frame(self, frame) -> None:
        """Mirror current camera frame to /dev/video10 (v4l2loopback) so other apps can stream concurrently."""
        if sys.platform in ("win32", "darwin") or not self._vcam_available or self._reading_from_vcam or frame is None:
            return
        try:
            if self._vcam_fd is None:
                if os.path.exists("/dev/video10"):
                    self._vcam_fd = os.open("/dev/video10", os.O_WRONLY | os.O_NONBLOCK)
                else:
                    self._vcam_available = False
                    return
            h, w = frame.shape[:2]
            if (w, h) != (640, 480):
                frame = cv2.resize(frame, (640, 480))
            bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
            os.write(self._vcam_fd, bgra.tobytes())
        except (BlockingIOError, InterruptedError):
            pass
        except Exception:
            if self._vcam_fd is not None:
                try:
                    os.close(self._vcam_fd)
                except Exception:
                    pass
                self._vcam_fd = None

    def start(self) -> bool:
        if self._running:
            return True

        if sys.platform == "darwin":
            try:
                from core.mac_input import is_accessibility_trusted
                if not is_accessibility_trusted():
                    if self.on_log:
                        self.on_log("WARN: macOS Accessibility permissions required for mouse & keyboard control. Enable in System Settings -> Privacy & Security -> Accessibility.")
            except Exception:
                pass

        if not _HAVE_MEDIAPIPE:
            if self.on_log:
                self.on_log("ERR: mediapipe is not available.")
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="bt-gesture-tracker"
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        self.emergency_release()
        cur = threading.current_thread()
        if self._thread and self._thread.is_alive() and self._thread != cur:
            try:
                self._thread.join(timeout=0.35)
            except Exception:
                pass
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._thread = None
        if self._vcam_fd is not None:
            try:
                os.close(self._vcam_fd)
            except Exception:
                pass
            self._vcam_fd = None
        self._smooth_x = None
        self._smooth_y = None
        self._smooth_dx = 0.0
        self._smooth_dy = 0.0
        self._pointer_lost_frames = 0
        self._prev_point_x = None
        self._prev_point_y = None
        self._filter_x = None
        self._filter_y = None
        self._release_pinch()
        self._fist_start_time = None
        self._fist_grace_frames = 0
        self._fist_armed = False
        self._fist_seen_frames = 0
        self._thumbs_start_time = None
        self._tab_start_time = None
        self._open_palm_start_time = None
        self._hold_progress = 0.0
        self._hold_label = ""

    def _worker_loop(self) -> None:
        try:
            self._reading_from_vcam = False
            try:
                from core.camera_devices import open_camera
                self._cap, self._reading_from_vcam = open_camera(
                    self.camera_index, width=640, height=480, fps=30
                )
                if self._reading_from_vcam and self.on_log:
                    self.on_log("SYS: Primary camera busy; attached to virtual loopback (/dev/video10).")
            except Exception:
                self._cap = cv2.VideoCapture(self.camera_index)

            if self._cap is None or not self._cap.isOpened():
                self._running = False
                if self.on_log:
                    self.on_log("ERR: Cannot open webcam device for gesture control (camera busy or disconnected).")
                return

            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self._cap.set(cv2.CAP_PROP_FPS, 30)

            if _USE_TASKS_API:
                self._run_tasks_loop()
            elif _MP_HANDS_LEGACY:
                self._run_legacy_loop()
            else:
                self._running = False
        except Exception as e:
            if self.on_log:
                self.on_log(f"ERR: Gesture tracker loop failed: {e}")
        finally:
            if self._cap:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None
            if self._vcam_fd is not None:
                try:
                    os.close(self._vcam_fd)
                except Exception:
                    pass
                self._vcam_fd = None
            self._running = False
            self._thread = None

    def _run_tasks_loop(self) -> None:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        model_path = _find_model_path()
        if not model_path:
            if self.on_log:
                self.on_log("ERR: hand_landmarker.task model file not found in config/.")
            return

        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            num_hands=1,
            min_hand_detection_confidence=0.70,
            min_tracking_confidence=0.70,
        )
        try:
            detector = vision.HandLandmarker.create_from_options(options)
        except Exception as e:
            if self.on_log:
                self.on_log(f"ERR: Failed to initialize HandLandmarker detector: {e}")
            return

        try:
            while self._running:
                if not self._cap:
                    break
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    time.sleep(0.02)
                    continue

                # Mirror to virtual camera loopback (/dev/video10) so other apps can stream concurrently
                self._broadcast_vcam_frame(frame)

                # Mirror horizontally for natural pointer control
                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

                try:
                    detection_result = detector.detect(mp_image)
                    if detection_result.hand_landmarks:
                        landmarks = detection_result.hand_landmarks[0]
                        self._process_hand_landmarks(landmarks)
                        annotated = self._render_annotated_frame(
                            frame, landmarks, self._detected_gesture_name, self._hold_progress, self._hold_label
                        )
                    else:
                        self._on_no_hand()
                        annotated = self._render_annotated_frame(frame, None, "SEARCHING FOR HAND...", 0.0, "")

                    if self.on_frame:
                        try:
                            self.on_frame(annotated)
                        except Exception:
                            pass
                except Exception:
                    time.sleep(0.01)
                    continue
        finally:
            try:
                detector.close()
            except Exception:
                pass

    def _run_legacy_loop(self) -> None:
        try:
            hands = _MP_HANDS_LEGACY.Hands(
                static_image_mode=False,
                max_num_hands=1,
                min_detection_confidence=0.70,
                min_tracking_confidence=0.70,
            )
        except Exception as e:
            if self.on_log:
                self.on_log(f"ERR: Failed to initialize MediaPipe Hands: {e}")
            return

        try:
            while self._running:
                if not self._cap:
                    break
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    time.sleep(0.02)
                    continue

                # Mirror to virtual camera loopback (/dev/video10) so other apps can stream concurrently
                self._broadcast_vcam_frame(frame)

                frame = cv2.flip(frame, 1)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                try:
                    res = hands.process(rgb)
                    if res.multi_hand_landmarks:
                        landmarks = res.multi_hand_landmarks[0].landmark
                        self._process_hand_landmarks(landmarks)
                        annotated = self._render_annotated_frame(
                            frame, landmarks, self._detected_gesture_name, self._hold_progress, self._hold_label
                        )
                    else:
                        self._on_no_hand()
                        annotated = self._render_annotated_frame(frame, None, "SEARCHING FOR HAND...", 0.0, "")

                    if self.on_frame:
                        try:
                            self.on_frame(annotated)
                        except Exception:
                            pass
                except Exception:
                    time.sleep(0.01)
                    continue
        finally:
            try:
                hands.close()
            except Exception:
                pass

    def _render_annotated_frame(
        self, frame: np.ndarray, landmarks, gesture_text: str, hold_progress: float = 0.0, hold_label: str = ""
    ) -> np.ndarray:
        """Render glowing cyan hand skeleton and tactical HUD feedback badge on video feed."""
        annotated = frame.copy()
        h, w = frame.shape[:2]

        if landmarks is not None:
            # Map normalized coords to pixel coordinates
            pts = []
            for lm in landmarks:
                px = max(0, min(w - 1, int(lm.x * w)))
                py = max(0, min(h - 1, int(lm.y * h)))
                pts.append((px, py))

            # Draw bones (cyan)
            for i1, i2 in _HAND_CONNECTIONS:
                if i1 < len(pts) and i2 < len(pts):
                    cv2.line(annotated, pts[i1], pts[i2], (255, 230, 0), 2, cv2.LINE_AA)

            # Draw joints
            for idx, pt in enumerate(pts):
                if idx in (4, 8, 12, 16, 20):  # Fingertips (amber/orange tactical marker)
                    cv2.circle(annotated, pt, 5, (0, 140, 255), -1, cv2.LINE_AA)
                    cv2.circle(annotated, pt, 7, (0, 230, 255), 1, cv2.LINE_AA)
                elif idx == 0:  # Wrist base
                    cv2.circle(annotated, pt, 5, (255, 200, 0), -1, cv2.LINE_AA)
                else:  # Finger joint
                    cv2.circle(annotated, pt, 3, (255, 230, 0), -1, cv2.LINE_AA)

            # Draw tactical pinch targeting reticle when user pinches thumb + index
            if self._is_pinching and len(pts) > 8:
                mx = (pts[4][0] + pts[8][0]) // 2
                my = (pts[4][1] + pts[8][1]) // 2
                reticle_col = (0, 165, 255) if self._is_mouse_down else (0, 255, 255)
                cv2.circle(annotated, (mx, my), 11, reticle_col, 2, cv2.LINE_AA)
                cv2.circle(annotated, (mx, my), 4, reticle_col, -1, cv2.LINE_AA)
                reticle_lbl = "HOLD DOWN" if self._is_mouse_down else "CLICK"
                cv2.putText(annotated, reticle_lbl, (mx + 16, my + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, reticle_col, 1, cv2.LINE_AA)

        # Tactical HUD Header Banner (semi-transparent dark bar)
        banner_h = 28
        cv2.rectangle(annotated, (0, 0), (w, banner_h), (8, 14, 20), -1)
        cv2.line(annotated, (0, banner_h), (w, banner_h), (255, 230, 0), 1)

        ctx = self._get_active_context()
        tag = ctx.get("tag", "SYS")
        prof_name = GESTURE_PROFILES.get(getattr(self, "_current_profile", "balanced"), {}).get("name", "BALANCED")

        if hold_progress > 0.05 and hold_label:
            pct = int(min(1.0, hold_progress) * 100)
            txt = f"[{tag} | {prof_name}] {hold_label}: {pct}%"
            # Visual transition from alert amber to confirmation cyan
            bar_color = (0, 165, 255) if pct < 85 else (0, 255, 255)
            cv2.putText(annotated, txt, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.44, bar_color, 1, cv2.LINE_AA)
            bar_w = int((w - 16) * min(1.0, hold_progress))
            cv2.rectangle(annotated, (8, 22), (8 + bar_w, 25), bar_color, -1)
        else:
            color = (255, 230, 0) if landmarks is not None else (140, 160, 180)
            txt = f"AI: [{tag} | {prof_name}] {gesture_text}"
            cv2.putText(annotated, txt, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.44, color, 1, cv2.LINE_AA)

        return annotated

    def _on_no_hand(self) -> None:
        self._filter_x = None
        self._filter_y = None
        self._prev_filt_x = None
        self._prev_filt_y = None
        self._sub_px_x = 0.0
        self._sub_px_y = 0.0
        self._smooth_dx = 0.0
        self._smooth_dy = 0.0
        self._pointer_lost_frames = 0
        self._prev_point_x = None
        self._prev_point_y = None
        self._release_pinch()
        self._open_palm_start_time = None
        self._tab_start_time = None
        self._palm_hold_start = 0.0
        self._prev_scroll_y = None
        self._last_3f_x = None
        self._last_3f_y = None
        self._last_shaka_y = None
        self._thumbs_start_time = None
        self._fist_start_time = None
        self._fist_grace_frames = 0
        if (time.monotonic() - getattr(self, '_fist_armed_t', 0.0)) > 1.0:
            self._fist_armed = False
            self._fist_seen_frames = 0
        self._hold_progress = 0.0
        self._hold_label = ""
        self._detected_gesture_name = "SEARCHING"

    def _process_hand_landmarks(self, lms) -> None:
        """
        Evaluate landmarks and dispatch clean, non-conflicting gestures.
        Landmark indices:
          0: Wrist
          4: Thumb tip, 2: Thumb MCP
          8: Index tip, 6: Index PIP, 5: Index MCP
          12: Middle tip, 10: Middle PIP, 9: Middle MCP
          16: Ring tip, 14: Ring PIP, 13: Ring MCP
          20: Pinky tip, 18: Pinky PIP, 17: Pinky MCP
        """
        now = time.monotonic()

        # Reset per-frame hold progress
        self._hold_progress = 0.0
        self._hold_label = ""

        # Normalized coordinates
        wrist = lms[0]
        thumb_tip, thumb_ip, thumb_mcp = lms[4], lms[3], lms[2]
        idx_tip, idx_pip, idx_mcp = lms[8], lms[6], lms[5]
        mid_tip, mid_pip, mid_mcp = lms[12], lms[10], lms[9]
        rng_tip, rng_pip, rng_mcp = lms[16], lms[14], lms[13]
        pnk_tip, pnk_pip, pnk_mcp = lms[20], lms[18], lms[17]

        def dist(p1, p2) -> float:
            return math.hypot(p1.x - p2.x, p1.y - p2.y)

        # Scale reference: Palm size (Wrist to Middle MCP)
        palm_size = dist(wrist, mid_mcp)
        if palm_size < 0.03:
            self._on_no_hand()
            self._detected_gesture_name = "SEARCHING"
            return

        # Scale- and rotation-invariant curling & extension tests
        # A finger is curled if its tip is tucked close to MCP/palm and its radial distance from wrist is <= PIP
        def is_extended(tip, pip, mcp, ratio=1.12) -> bool:
            return (dist(tip, wrist) > dist(pip, wrist) * ratio) and (dist(tip, mcp) > palm_size * 0.65)

        # A finger is curled if it is folded inward toward palm/MCP or retracted radially
        def is_curled(tip, pip, mcp) -> bool:
            return (dist(tip, mcp) < palm_size * 0.75) or (dist(tip, wrist) <= dist(pip, wrist) * 1.06)

        idx_curled = is_curled(idx_tip, idx_pip, idx_mcp)
        mid_curled = is_curled(mid_tip, mid_pip, mid_mcp)
        rng_curled = is_curled(rng_tip, rng_pip, rng_mcp)
        pnk_curled = is_curled(pnk_tip, pnk_pip, pnk_mcp)

        idx_ext = is_extended(idx_tip, idx_pip, idx_mcp, 1.10)
        mid_ext = is_extended(mid_tip, mid_pip, mid_mcp, 1.10)
        rng_ext = is_extended(rng_tip, rng_pip, rng_mcp, 1.08)   # Natural biomechanical tolerance for ring finger
        pnk_ext = is_extended(pnk_tip, pnk_pip, pnk_mcp, 1.08)

        # Count how many of the 4 fingers are curled into palm
        curled_count = sum([idx_curled, mid_curled, rng_curled, pnk_curled])
        no_fingers_ext = (not idx_ext and not mid_ext and not rng_ext and not pnk_ext)
        four_main_ext = idx_ext and mid_ext and rng_ext and pnk_ext

        # Thumb extension tests:
        # 1. Thumb extended away from palm center
        thumb_ext = (dist(thumb_tip, mid_mcp) > palm_size * 0.58) and (dist(thumb_tip, thumb_mcp) > palm_size * 0.45)

        # 2. Thumb pointing high UP into the air (Thumbs Up gesture)
        thumb_is_up = (
            thumb_ext and
            (thumb_tip.y < idx_mcp.y - palm_size * 0.20) and
            (thumb_tip.y < thumb_mcp.y - palm_size * 0.30)
        )

        # 3. Strict Thumb Tucked / Closed against knuckles & palm (Closed Fist)
        # Distance from thumb tip (landmark 4) to index MCP (5) or middle MCP (9) or index PIP (6)
        # must be small relative to hand scale (palm_size), AND thumb is not pointing up, AND thumb is not extended
        thumb_fist_closed = (
            not thumb_is_up and not thumb_ext and (
                dist(thumb_tip, idx_mcp) < palm_size * 0.50 or
                dist(thumb_tip, mid_mcp) < palm_size * 0.55 or
                dist(thumb_tip, idx_pip) < palm_size * 0.50
            )
        )

        # ── GESTURE 0A: CLOSED FIST -> ARM SHOW DESKTOP (WIN+D) / HOLD TO DISABLE ──
        # Strict: ALL 4 fingers curled + no fingers extended + thumb strictly closed against knuckles/palm
        is_closed_fist = no_fingers_ext and (curled_count == 4) and thumb_fist_closed

        if is_closed_fist:
            self._detected_gesture_name = "CLOSED FIST"
            self._fist_grace_frames = 5  # Allow up to 5 frames (~160ms) of camera/landmark noise
            self._prev_scroll_y = None
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None
            self._release_pinch()

            if self._fist_start_time is None:
                self._fist_start_time = now
            elapsed = now - self._fist_start_time

            self._fist_seen_frames = getattr(self, '_fist_seen_frames', 0) + 1
            if elapsed >= 0.25 and self._fist_seen_frames >= 7:  # Confirmed solid closed fist (~250-350ms)
                self._fist_armed = True
                self._fist_armed_t = now

            self._hold_progress = min(1.0, elapsed / self._fist_hold_duration)
            pct = int(self._hold_progress * 100)
            self._hold_label = "FIST ARMED: REOPEN FOR DESKTOP" if self._fist_armed else "HOLDING CLOSED FIST"

            if 0.08 <= elapsed < self._fist_hold_duration:
                if self._fist_armed:
                    self._trigger_feedback(f"◈ FIST ARMED — REOPEN FOR DESKTOP (HOLD TO DISABLE {pct}%) ✊")
                else:
                    self._trigger_feedback(f"◈ CLOSING FIST ({pct}%) ✊")
            elif elapsed >= self._fist_hold_duration:
                self._fist_start_time = None
                self._fist_grace_frames = 0
                self._fist_armed = False
                self._fist_seen_frames = 0
                self._trigger_feedback("◈ GESTURE: CLOSED FIST -> GESTURES OFF ✊")
                self._running = False
                if self.on_disable:
                    try:
                        self.on_disable()
                    except Exception:
                        pass
                return
            return
        else:
            # Strict Clean State Machine: Transition from CLOSED_FIST -> fully extended OPEN_HAND
            # Both all 4 fingers AND the thumb must be extended! If thumb is tucked or closed, REJECT.
            is_strict_open_hand = four_main_ext and thumb_ext

            if getattr(self, '_fist_armed', False):
                if is_strict_open_hand:
                    if (now - getattr(self, '_fist_armed_t', 0.0)) <= 1.20:
                        if (now - getattr(self, '_last_desktop_time', 0.0)) > getattr(self, '_desktop_cooldown', 1.20):
                            self._last_desktop_time = now
                            self._fist_armed = False
                            self._fist_seen_frames = 0
                            self._fist_start_time = None
                            self._fist_grace_frames = 0
                            self._open_palm_start_time = None
                            self._send_show_desktop()
                            self._trigger_feedback("◈ GESTURE: FIST -> OPEN -> SHOW DESKTOP (WIN+D) 🖥️")
                            self._detected_gesture_name = "SHOW DESKTOP (WIN+D) 🖥️"
                            return
                    self._fist_armed = False
                    self._fist_seen_frames = 0
                elif (now - getattr(self, '_fist_armed_t', 0.0)) > 1.20:
                    self._fist_armed = False
                    self._fist_seen_frames = 0

            # If not reopening, check grace frames before dropping the fist hold timer
            if getattr(self, '_fist_grace_frames', 0) > 0 and self._fist_start_time is not None:
                self._fist_grace_frames -= 1
                elapsed = now - self._fist_start_time
                self._hold_progress = min(1.0, elapsed / self._fist_hold_duration)
                pct = int(self._hold_progress * 100)
                self._hold_label = "FIST ARMED: REOPEN FOR DESKTOP" if getattr(self, '_fist_armed', False) else "HOLDING CLOSED FIST"
                self._detected_gesture_name = "CLOSED FIST"
                if elapsed >= self._fist_hold_duration:
                    self._fist_start_time = None
                    self._fist_grace_frames = 0
                    self._fist_armed = False
                    self._fist_seen_frames = 0
                    self._trigger_feedback("◈ GESTURE: CLOSED FIST -> GESTURES OFF ✊")
                    self._running = False
                    if self.on_disable:
                        try:
                            self.on_disable()
                        except Exception:
                            pass
                    return
                return
            else:
                self._fist_start_time = None
                self._fist_grace_frames = 0
                self._fist_seen_frames = 0

        # ── GESTURE 0B: THUMBS UP -> PAUSE / UNPAUSE MEDIA (ROBUST & ADAPTIVE) ──
        # Index, Middle, Ring, Pinky are curled/not extended into a fist,
        # Thumb is extended UP!
        four_fingers_fist = no_fingers_ext and (curled_count >= 3)
        is_thumbs_up = four_fingers_fist and thumb_is_up and not thumb_fist_closed

        if is_thumbs_up:
            self._thumbs_grace_frames = 4  # Allow 4 frames of tracking jitter
            self._prev_scroll_y = None
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None
            self._release_pinch()

            if self._thumbs_start_time is None:
                self._thumbs_start_time = now
            elapsed = now - self._thumbs_start_time
            self._hold_progress = min(1.0, elapsed / self._thumbs_hold_duration)
            self._hold_label = "HOLD THUMBS UP (PLAY/PAUSE)"
            self._detected_gesture_name = "HOLDING THUMBS UP"

            if 0.08 <= elapsed < self._thumbs_hold_duration:
                self._trigger_feedback("◈ HOLD THUMBS UP TO PLAY/PAUSE...")
            elif elapsed >= self._thumbs_hold_duration:
                if (now - self._last_thumbs_time) > self._thumbs_cooldown:
                    self._last_thumbs_time = now
                    self._send_play_pause()
                    self._trigger_feedback("◈ GESTURE: THUMBS UP -> PLAY / PAUSE ⏯")
                self._thumbs_start_time = None
                self._thumbs_grace_frames = 0
            return
        else:
            if getattr(self, "_thumbs_grace_frames", 0) > 0:
                self._thumbs_grace_frames -= 1
                if self._thumbs_start_time is not None:
                    elapsed = now - self._thumbs_start_time
                    self._hold_progress = min(1.0, elapsed / self._thumbs_hold_duration)
                    self._hold_label = "HOLD THUMBS UP (PLAY/PAUSE)"
                    self._detected_gesture_name = "HOLDING THUMBS UP"
                    return
            else:
                self._thumbs_start_time = None

        # Check if four main fingers (Index, Middle, Ring, Pinky) are all extended
        four_main_ext = idx_ext and mid_ext and rng_ext and pnk_ext

        # Distinct Thumb States for 4 Fingers vs 5 Fingers with strict deadband isolation:
        # A. Thumb Tucked (4 Fingers / Alt+Tab):
        # Tip is tucked inward against palm / base of knuckles, not spread out
        thumb_is_tucked = (
            (dist(thumb_tip, idx_mcp) < palm_size * 0.38 or dist(thumb_tip, mid_mcp) < palm_size * 0.48 or dist(thumb_tip, idx_pip) < palm_size * 0.45)
            and dist(thumb_tip, wrist) < palm_size * 1.05
            and dist(thumb_tip, mid_mcp) < palm_size * 0.55
        )

        # B. Thumb Spread (5 Fingers / Open Palm Click):
        # Tip is stretched far outward away from knuckles, palm center, and index finger
        thumb_is_spread = (
            dist(thumb_tip, mid_mcp) > palm_size * 0.70 and
            dist(thumb_tip, idx_mcp) > palm_size * 0.52 and
            dist(thumb_tip, wrist) > palm_size * 0.80
        )

        # ── GESTURE 1A: 4 FINGERS EXTENDED (THUMB TUCKED) -> CTRL+TAB SWITCH TABS ─
        # All 4 fingers open EXCEPT thumb (Index, Middle, Ring, Pinky extended, Thumb curled against palm)
        is_4fingers_switch = four_main_ext and thumb_is_tucked and not thumb_is_spread
        if is_4fingers_switch:
            self._detected_gesture_name = "4 FINGERS (SWITCH TABS) 📑"
            self._prev_scroll_y = None
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None
            self._release_pinch()
            self._open_palm_start_time = None

            if self._tab_start_time is None:
                self._tab_start_time = now
            elapsed = now - self._tab_start_time
            self._hold_progress = min(1.0, elapsed / self._tab_hold_duration)
            self._hold_label = "HOLD 4 FINGERS (SWITCH TABS)"

            if 0.10 <= elapsed < self._tab_hold_duration:
                self._trigger_feedback("◈ HOLD 4 FINGERS TO SWITCH TABS...")
            elif elapsed >= self._tab_hold_duration:
                if (now - self._last_ctrl_tab_time) > self._ctrl_tab_cooldown:
                    self._last_ctrl_tab_time = now
                    self._send_ctrl_tab()
                    self._trigger_feedback("◈ GESTURE: 4 FINGERS -> SWITCH TABS (Ctrl+Tab) 📑")
                self._tab_start_time = None
            return
        else:
            self._tab_start_time = None

        # ── GESTURE 1B: PINKY ONLY -> SWITCH WINDOWS (ALT+TAB / ALT+SHIFT+TAB) ────
        # Pinky extended, other 4 fingers curled/folded (Index, Middle, Ring, Thumb curled)
        is_pinky_only_raw = pnk_ext and not idx_ext and not mid_ext and not rng_ext and not thumb_ext
        if is_pinky_only_raw:
            self._pinky_grace_frames = 3
            is_pinky_only = True
        elif getattr(self, "_pinky_grace_frames", 0) > 0:
            self._pinky_grace_frames -= 1
            is_pinky_only = True
        else:
            is_pinky_only = False

        if is_pinky_only:
            self._detected_gesture_name = "PINKY (SWITCH WINDOWS)"
            self._prev_scroll_y = None
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None
            self._release_pinch()
            self._tab_start_time = None
            self._open_palm_start_time = None

            curr_x = pnk_tip.x
            curr_y = pnk_tip.y
            if not hasattr(self, "_pinky_pts"):
                self._pinky_pts = []
            self._pinky_pts.append((now, curr_x, curr_y))
            self._pinky_pts = [(t, x, y) for (t, x, y) in self._pinky_pts if now - t <= 0.40]

            # Horizontal swipe tracking with pinky over history window
            if (now - self._last_track_time) > self._track_cooldown and len(self._pinky_pts) >= 2:
                for (pt_t, pt_x, pt_y) in self._pinky_pts:
                    if (now - pt_t) < 0.05:
                        continue
                    p_dx = curr_x - pt_x
                    p_dy = curr_y - pt_y
                    if abs(p_dx) >= 0.06 and abs(p_dx) > abs(p_dy) * 1.15:
                        if p_dx > 0.0:
                            self._last_track_time = now
                            self._send_alt_tab()
                            self._trigger_feedback("◈ GESTURE: PINKY SWIPE -> SWITCH WINDOW (Alt+Tab) 🪟")
                            self._pinky_pts.clear()
                            self._pinky_hold_start = None
                            return
                        else:
                            self._last_track_time = now
                            self._send_alt_shift_tab()
                            self._trigger_feedback("◈ GESTURE: PINKY SWIPE -> PREV WINDOW (Alt+Shift+Tab) 🪟")
                            self._pinky_pts.clear()
                            self._pinky_hold_start = None
                            return

            # If raised / held without swiping, trigger Alt+Tab with cooldown
            if (now - self._last_track_time) > self._track_cooldown:
                if getattr(self, "_pinky_hold_start", None) is None:
                    self._pinky_hold_start = now
                elif (now - self._pinky_hold_start) >= 0.20:
                    self._last_track_time = now
                    self._send_alt_tab()
                    self._trigger_feedback("◈ GESTURE: PINKY -> SWITCH WINDOW (Alt+Tab) 🪟")
                    self._pinky_hold_start = None
                    self._pinky_pts.clear()
            return
        else:
            self._pinky_grace_frames = 0
            if hasattr(self, "_pinky_pts"):
                self._pinky_pts.clear()
            self._pinky_hold_start = None

        # ── GESTURE 1C: FULLY OPEN PALM (HELD 0.35s) -> CONTEXT-ADAPTIVE ────
        # All 5 fingers extended. In media context: toggles Play/Pause. In desktop: Left Click.
        is_fully_open = four_main_ext and thumb_is_spread and not thumb_is_tucked
        if is_fully_open:
            ctx = self._get_active_context()
            is_media_mode = ctx.get("is_media", False)
            act_label = "PLAY/PAUSE" if is_media_mode else "CLICK"
            self._detected_gesture_name = f"OPEN PALM ({act_label})"
            self._prev_scroll_y = None
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None
            self._release_pinch()
            self._tab_start_time = None

            if self._open_palm_start_time is None:
                self._open_palm_start_time = now
            elapsed = now - self._open_palm_start_time
            self._hold_progress = min(1.0, elapsed / self._open_palm_hold_duration)
            self._hold_label = f"HOLD OPEN PALM ({act_label})"

            if 0.10 <= elapsed < self._open_palm_hold_duration:
                self._trigger_feedback(f"◈ HOLD OPEN PALM TO {act_label}...")
            elif elapsed >= self._open_palm_hold_duration:
                if is_media_mode:
                    if (now - self._last_media_time) > self._media_cooldown:
                        self._last_media_time = now
                        self._send_play_pause()
                        self._trigger_feedback("◈ GESTURE [MEDIA]: OPEN PALM -> PLAY / PAUSE ⏯")
                else:
                    if (now - self._last_click_time) > self._click_cooldown:
                        self._last_click_time = now
                        self._send_left_click()
                        self._trigger_feedback("◈ GESTURE: OPEN PALM CLICK 🖐")
                self._open_palm_start_time = None
            return
        else:
            self._open_palm_start_time = None

        # Deadband buffer: Four fingers extended but thumb in transition (neither clearly tucked nor spread)
        if four_main_ext:
            self._detected_gesture_name = "HAND TRANSITION (4/5 FINGERS)"
            self._prev_scroll_y = None
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None
            self._release_pinch()
            self._tab_start_time = None
            self._open_palm_start_time = None
            return

        # ── GESTURE 2: TWO-FINGER GESTURE (VERTICAL MOUSE SCROLL) ───────────────
        # Index + Middle extended, Ring + Pinky curled/not extended
        # Allow natural thumb posture (relaxed, tucked, or resting)
        is_2fingers_raw = idx_ext and mid_ext and not rng_ext and not pnk_ext
        if is_2fingers_raw:
            self._2f_grace_frames = 3
            is_2fingers = True
        elif getattr(self, "_2f_grace_frames", 0) > 0:
            self._2f_grace_frames -= 1
            is_2fingers = True
        else:
            is_2fingers = False

        if is_2fingers:
            self._detected_gesture_name = "TWO FINGERS (SCROLL)"
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None
            self._release_pinch()

            prof = GESTURE_PROFILES.get(getattr(self, "_current_profile", "balanced"), GESTURE_PROFILES["balanced"])
            scroll_thresh = prof.get("scroll_threshold", 0.012)
            scroll_cool = prof.get("scroll_cooldown", 0.04)

            curr_y = (idx_tip.y + mid_tip.y) / 2.0

            # Vertical Motion Detection (Up / Down -> MOUSE SCROLL)
            if self._prev_scroll_y is not None:
                dy = curr_y - self._prev_scroll_y
                if dy < -scroll_thresh:
                    if now - self._last_scroll_time > scroll_cool:
                        self._send_scroll(1)
                        self._last_scroll_time = now
                        self._trigger_feedback("◈ GESTURE: SCROLL UP ▲")
                    self._prev_scroll_y = curr_y
                elif dy > scroll_thresh:
                    if now - self._last_scroll_time > scroll_cool:
                        self._send_scroll(-1)
                        self._last_scroll_time = now
                        self._trigger_feedback("◈ GESTURE: SCROLL DOWN ▼")
                    self._prev_scroll_y = curr_y
                else:
                    self._prev_scroll_y = self._prev_scroll_y * 0.85 + curr_y * 0.15
            else:
                self._prev_scroll_y = curr_y
            return
        else:
            self._2f_grace_frames = 0
            self._prev_scroll_y = None

        # ── GESTURE 2B: PINCH (THUMB + 1ST FINGER) -> LEFT CLICK / HOLD DOWN ──
        # Pinching thumb tip and index (1st) finger tip together.
        # Quick pinch = Left Click; Holding pinch = Holds Left Mouse Button down (Drag / Select).
        pinch_dist = dist(thumb_tip, idx_tip)
        pinch_threshold = palm_size * 0.22
        release_threshold = palm_size * 0.30

        is_pinch = (
            pinch_dist < pinch_threshold and
            dist(thumb_tip, mid_tip) > palm_size * 0.18 and
            not is_closed_fist
        )

        if is_pinch:
            self._detected_gesture_name = "PINCH (HOLD DOWN)" if self._is_mouse_down else "PINCH (CLICK)"
            self._prev_scroll_y = None
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None

            if not self._is_pinching:
                self._is_pinching = True
                self._last_click_time = now
                if not self._is_mouse_down:
                    self._is_mouse_down = True
                    self._send_mouse_down()
                    self._trigger_feedback("◈ GESTURE: PINCH (HOLD MOUSE DOWN) 🎯")

            # Enable dragging while pinch is held
            pinch_px = (idx_tip.x + thumb_tip.x) / 2.0
            pinch_py = (idx_tip.y + thumb_tip.y) / 2.0
            self._update_relative_cursor(now, pinch_px, pinch_py)

            if self.on_finger_pointer:
                try:
                    self.on_finger_pointer(pinch_px, pinch_py, "drag" if self._is_mouse_down else "click")
                except Exception:
                    pass
            return
        elif pinch_dist > release_threshold:
            self._release_pinch()

        # ── GESTURE 3: POINTER MODE (RELATIVE MOUSE MOVEMENT) ───────────────
        # Index finger extended, Middle + Ring + Pinky curled
        is_pointing = (idx_ext or dist(idx_tip, idx_mcp) > palm_size * 0.70) and mid_curled and rng_curled and pnk_curled
        if is_pointing:
            self._release_pinch()
            self._pointer_lost_frames = 0
            self._prev_scroll_y = None
            self._last_3f_x = None
            self._last_3f_y = None
            self._last_shaka_y = None

            self._detected_gesture_name = "POINTER (AIMING)"

            # Blended index anchor: 40% tip + 40% PIP + 20% MCP gives rock-solid physical stabilization
            raw_px = idx_tip.x * 0.40 + idx_pip.x * 0.40 + idx_mcp.x * 0.20
            raw_py = idx_tip.y * 0.40 + idx_pip.y * 0.40 + idx_mcp.y * 0.20

            self._update_relative_cursor(now, raw_px, raw_py)

            if self.on_finger_pointer:
                try:
                    self.on_finger_pointer(raw_px, raw_py, "move")
                except Exception:
                    pass
            return
        else:
            # Grace persistence buffer: keep filter alive across transient frame losses
            self._pointer_lost_frames += 1
            if self._pointer_lost_frames >= 8:
                self._filter_x = None
                self._filter_y = None
                self._prev_filt_x = None
                self._prev_filt_y = None
                self._sub_px_x = 0.0
                self._sub_px_y = 0.0
                self._smooth_dx = 0.0
                self._smooth_dy = 0.0

        # ── GESTURE 4: 3 FINGERS + HORIZONTAL SWIPE (CONTEXT-ADAPTIVE) ───────
        # Index + Middle + Ring extended, Pinky CURLED, Thumb NOT extended
        is_3fingers_raw = (
            idx_ext and mid_ext and rng_ext and
            not pnk_ext and (pnk_curled or dist(pnk_tip, pnk_mcp) < palm_size * 0.85) and
            not thumb_ext
        )
        if is_3fingers_raw:
            self._3f_grace_frames = 4
        elif getattr(self, "_3f_grace_frames", 0) > 0:
            self._3f_grace_frames -= 1

        is_3fingers = is_3fingers_raw or (getattr(self, "_3f_grace_frames", 0) > 0 and self._last_3f_x is not None)

        if is_3fingers:
            ctx = self._get_active_context()
            tag = ctx.get("tag", "WIN")
            self._detected_gesture_name = f"3 FINGERS [{tag}] (SWIPE)"
            self._release_pinch()
            curr_x = mid_mcp.x
            curr_y = mid_mcp.y
            if self._last_3f_x is not None and (now - self._last_3f_time < 0.50):
                dx = curr_x - self._last_3f_x
                dy = curr_y - (self._last_3f_y if self._last_3f_y is not None else curr_y)
                dt = max(0.01, now - self._last_3f_time)
                vel_x = dx / dt

                prof = GESTURE_PROFILES.get(getattr(self, "_current_profile", "balanced"), GESTURE_PROFILES["balanced"])
                flick_thresh = prof.get("flick_threshold", 0.08)

                if (now - self._last_track_time) > self._track_cooldown:
                    # Require lateral displacement >= flick_thresh and horizontal dominance (|dx| > |dy| * 1.2)
                    if abs(dx) > flick_thresh and abs(dx) > abs(dy) * 1.2:
                        if dx > flick_thresh or vel_x > 0.60:
                            self._last_track_time = now
                            if tag == "MEDIA" or ctx.get("is_media"):
                                self._send_next_video()
                                self._trigger_feedback("◈ GESTURE [MEDIA]: NEXT TRACK ⏭")
                            elif tag == "TAB" or ctx.get("is_browser"):
                                self._send_ctrl_tab()
                                self._trigger_feedback("◈ GESTURE [TAB]: NEXT TAB (Ctrl+Tab) 📑")
                            else:
                                self._send_alt_tab()
                                self._trigger_feedback("◈ GESTURE [WIN]: SWITCH WINDOW (Alt+Tab) 🪟")
                            if self.on_swipe:
                                try:
                                    self.on_swipe("right")
                                except Exception:
                                    pass
                            self._last_3f_x = None
                            self._last_3f_y = None
                            self._3f_grace_frames = 0
                            return
                        elif dx < -flick_thresh or vel_x < -0.60:
                            self._last_track_time = now
                            if tag == "MEDIA" or ctx.get("is_media"):
                                self._send_prev_video()
                                self._trigger_feedback("◈ GESTURE [MEDIA]: PREV TRACK ⏮")
                            elif tag == "TAB" or ctx.get("is_browser"):
                                self._send_ctrl_shift_tab()
                                self._trigger_feedback("◈ GESTURE [TAB]: PREV TAB (Ctrl+Shift+Tab) 📑")
                            else:
                                self._send_alt_shift_tab()
                                self._trigger_feedback("◈ GESTURE [WIN]: PREV WINDOW (Alt+Shift+Tab) 🪟")
                            if self.on_swipe:
                                try:
                                    self.on_swipe("left")
                                except Exception:
                                    pass
                            self._last_3f_x = None
                            self._last_3f_y = None
                            self._3f_grace_frames = 0
                            return

            self._last_3f_x = curr_x
            self._last_3f_y = curr_y
            self._last_3f_time = now
            return
        else:
            self._last_3f_x = None
            self._last_3f_y = None

        # ── GESTURE 5: SHAKA / HANG LOOSE (PIPEWIRE VOLUME UP / DOWN) ────────
        # Thumb + Pinky extended, Index + Middle + Ring curled
        is_shaka = thumb_ext and pnk_ext and idx_curled and mid_curled and rng_curled
        if is_shaka:
            self._detected_gesture_name = "SHAKA (VOLUME)"
            self._release_pinch()
            curr_y = mid_mcp.y
            if self._last_shaka_y is not None:
                dy = curr_y - self._last_shaka_y
                if (now - self._last_vol_time) > self._vol_cooldown:
                    # Upward motion (dy < 0) -> Volume UP
                    if dy < -0.018:
                        self._last_vol_time = now
                        if sys.platform == "win32":
                            if _HAVE_PYAUTOGUI:
                                try:
                                    pyautogui.press("volumeup", _pause=False)
                                except Exception:
                                    pass
                        else:
                            try:
                                subprocess.run(
                                    ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+"],
                                    check=False
                                )
                            except Exception:
                                pass
                        self._trigger_feedback("◈ GESTURE: VOLUME 5%+ 🔊")
                        self._last_shaka_y = curr_y
                    # Downward motion (dy > 0) -> Volume DOWN
                    elif dy > 0.018:
                        self._last_vol_time = now
                        if sys.platform == "win32":
                            if _HAVE_PYAUTOGUI:
                                try:
                                    pyautogui.press("volumedown", _pause=False)
                                except Exception:
                                    pass
                        else:
                            try:
                                subprocess.run(
                                    ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"],
                                    check=False
                                )
                            except Exception:
                                pass
                        self._trigger_feedback("◈ GESTURE: VOLUME 5%- 🔉")
                        self._last_shaka_y = curr_y
            else:
                self._last_shaka_y = curr_y
            return
        else:
            self._last_shaka_y = None

        self._detected_gesture_name = "HAND DETECTED"

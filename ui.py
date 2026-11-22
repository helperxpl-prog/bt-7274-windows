from __future__ import annotations

import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QCursor, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QGuiApplication, QImage, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None

try:
    from core.gesture_tracker import GestureTracker
except Exception:
    GestureTracker = None


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = "SRS // BT-7274 — MARK LV"
APP_PROTOCOL = "PROTOCOL 3 // ONLINE"

_DEFAULT_W, _DEFAULT_H = 1024, 762
_MIN_W,     _MIN_H     = 880,  640
_LEFT_W  = 180
_RIGHT_W = 308

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#010810"
    PANEL     = "#020d18"
    PANEL2    = "#031220"
    BORDER    = "#10344c"
    BORDER_B  = "#144460"
    BORDER_A  = "#00e5ff"
    PRI       = "#00e5ff"
    PRI_DIM   = "#009bb3"
    PRI_GHO   = "#001a24"
    ACC       = "#ff9900"
    ACC2      = "#ffaa00"
    PURPLE    = "#a855f7"
    GREEN     = "#00ff88"
    GREEN_D   = "#00bb55"
    RED       = "#ff1744"
    MUTED_C   = "#ff7b00"
    TEXT      = "#e0f7fc"
    TEXT_DIM  = "#4a788c"
    TEXT_MED  = "#6ba3b8"
    WHITE     = "#ffffff"
    DARK      = "#01070e"
    BAR_BG    = "#041522"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


def _play_bt_hologram_sound():
    """Synthesize and play an instant BT holographic sci-fi opening sweep."""
    def _synth():
        try:
            if _OS == "Windows":
                import winsound
                import struct
                import math
                sr = 22050
                duration = 0.22
                n_samples = int(sr * duration)
                data = bytearray()
                for i in range(n_samples):
                    t = i / sr
                    f1 = 520 + 820 * (t / duration) ** 1.5
                    f2 = 1040 + 1300 * (t / duration) ** 1.2
                    env = (1.0 - (t / duration) ** 1.8) * min(1.0, t / 0.012)
                    val = 0.55 * math.sin(2 * math.pi * f1 * t) + 0.45 * math.sin(2 * math.pi * f2 * t)
                    sample = int(max(-32767, min(32767, val * env * 22000)))
                    data.extend(struct.pack('<h', sample))
                header = struct.pack(
                    '<4sI4s4sIHHIIHH4sI',
                    b'RIFF', 36 + len(data), b'WAVE',
                    b'fmt ', 16, 1, 1, sr, sr * 2, 2, 16,
                    b'data', len(data)
                )
                winsound.PlaySound(header + bytes(data), winsound.SND_MEMORY | winsound.SND_ASYNC)
        except Exception:
            pass
    threading.Thread(target=_synth, daemon=True, name="bt-holo-sound").start()


def _play_bt_transition_sound():
    """Synthesize and play an epic BT holographic fullscreen transition sweep & chime."""
    def _synth():
        try:
            if _OS == "Windows":
                import winsound
                import struct
                import math
                sr = 22050
                duration = 0.36
                n_samples = int(sr * duration)
                data = bytearray()
                for i in range(n_samples):
                    t = i / sr
                    # Ascending sci-fi power chord transition
                    f1 = 440 + 880 * math.sqrt(t / duration)
                    f2 = 880 + 1320 * (t / duration) ** 1.3
                    f3 = 1760 + 440 * math.sin(t * 30.0)
                    env = (1.0 - (t / duration) ** 1.4) * min(1.0, t / 0.008)
                    val = 0.40 * math.sin(2 * math.pi * f1 * t) + 0.35 * math.sin(2 * math.pi * f2 * t) + 0.25 * math.sin(2 * math.pi * f3 * t)
                    sample = int(max(-32767, min(32767, val * env * 24000)))
                    data.extend(struct.pack('<h', sample))
                header = struct.pack(
                    '<4sI4s4sIHHIIHH4sI',
                    b'RIFF', 36 + len(data), b'WAVE',
                    b'fmt ', 16, 1, 1, sr, sr * 2, 2, 16,
                    b'data', len(data)
                )
                winsound.PlaySound(header + bytes(data), winsound.SND_MEMORY | winsound.SND_ASYNC)
        except Exception:
            pass
    threading.Thread(target=_synth, daemon=True, name="bt-trans-sound").start()



# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        # Permanent BT-7274 Titan Optical Eye (animated human face completely removed)
        self._avatar = None
        self.hud_style = "core"
        self._core_phase = 0.0
        self._eye_rot = 0.0
        self._eye_rot_vel = 0.0

        # Vanguard Titan Optic Autofocus & Collimator Servo Dynamics
        self._servo_angle: float = 0.0
        self._target_servo_angle: float = 0.0
        self._servo_vel: float = 0.0
        self._next_autofocus_t: float = 0.0

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        # Hand Gesture Tracking State
        self.gesture_scale = 1.0
        self.gesture_banner_text = ""
        self.gesture_banner_alpha = 0.0

        # Finger Pointer Tracking State
        self._finger_pos: tuple[float, float] | None = None
        self._finger_action: str | None = None

        # Floating Holographic News & Intel Cards
        self.floating_news: list[dict] = []
        self._drag_card: dict | None = None
        self._drag_offset: tuple[float, float] = (0.0, 0.0)
        self._drag_start_pos: tuple[float, float] | None = None
        self._target_drag_x: float = 0.0
        self._target_drag_y: float = 0.0
        self._over_open: bool = False
        self._over_trash: bool = False
        self.on_card_click = None
        self.on_card_open_fullscreen = None

        self._bg_pix = None
        bg_path = CONFIG_DIR / "chassis_backdrop.png"
        if bg_path.exists():
            self._bg_pix = QPixmap(str(bg_path))
        self._logo_pix = None
        logo_path = CONFIG_DIR / "srs_delta_logo.png"
        if logo_path.exists():
            self._logo_pix = QPixmap(str(logo_path)).scaled(54, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

        # Low-Power Standby / Sleep Mode & Eye Dynamics
        self.sleeping: bool = False
        self._eye_openness: float = 1.0
        self._eye_target: float = 1.0
        self._eye_blink: float = 1.0
        self._eye_gaze_x: float = 0.0
        self._eye_gaze_y: float = 0.0
        self._target_gaze_x: float = 0.0
        self._target_gaze_y: float = 0.0
        self._next_saccade_t: float = 0.0
        self._blink_start_t: float = 0.0
        self._next_blink_t: float = 0.0
        self._close_start_t: float | None = None
        self._wake_start_t: float | None = None
        self._close_duration: float = 0.28
        self._wake_duration: float = 0.25
        self.on_wake_clicked = None

        self.setMouseTracking(True)
        self._mouse_pos: tuple[float, float] | None = None

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def set_floating_news(self, items: list[dict]) -> None:
        """Position holographic floating news boxes on left and right of HUD."""
        self.floating_news = []
        W, H = max(400, self.width()), max(300, self.height())
        card_w, card_h = 165.0, 72.0
        for i, it in enumerate(items[:6]):
            side = "left" if i % 2 == 0 else "right"
            row = i // 2
            if side == "left":
                x = 16.0
                y = 55.0 + row * 82.0
            else:
                x = max(16.0, float(W - card_w - 16.0))
                y = 55.0 + row * 82.0
            title = it.get("title", f"News #{i+1}")
            body = it.get("body", "")
            self.floating_news.append({
                "id": i,
                "title": title,
                "body": body,
                "x": x,
                "y": y,
                "w": card_w,
                "h": card_h,
                "side": side,
            })
        _play_bt_hologram_sound()
        self.show_gesture_feedback(f"◈ {len(self.floating_news)} NEWS WIDGETS SPAWNED")
        self.update()

    def mousePressEvent(self, event):
        if self.sleeping:
            if callable(self.on_wake_clicked):
                self.on_wake_clicked()
            return
        pos = event.position() if hasattr(event, 'position') else event.pos()
        px, py = pos.x(), pos.y()
        W, H = self.width(), self.height()

        # Trash Can Zone Click (Bottom-Right) - Click to clear all floating intel cards
        if self.floating_news and px >= W - 148 and H - 110 <= py <= H - 60:
            self.floating_news.clear()
            self._over_trash = False
            self._drag_card = None
            _play_bt_hologram_sound()
            self.show_gesture_feedback("◈ INTEL DISPOSED IN TRASH 🗑️")
            self.update()
            return

        for card in reversed(self.floating_news):
            rx, ry, rw, rh = card['x'], card['y'], card['w'], card['h']
            if rx <= px <= rx + rw and ry <= py <= ry + rh:
                if px >= rx + rw - 22 and py <= ry + 22:
                    self.floating_news.remove(card)
                    _play_bt_hologram_sound()
                    self.show_gesture_feedback("◈ NEWS CARD CLOSED ✕")
                    self.update()
                    return
                self._drag_card = card
                self._drag_start_pos = (px, py)
                self._drag_offset = (px - rx, py - ry)
                self._target_drag_x = card['x']
                self._target_drag_y = card['y']
                self._over_open = False
                self._over_trash = False
                self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position() if hasattr(event, 'position') else event.pos()
        px, py = pos.x(), pos.y()
        self._mouse_pos = (px, py)
        if self._drag_card:
            W, H = self.width(), self.height()
            self._target_drag_x = max(4.0, min(W - self._drag_card['w'] - 4.0, px - self._drag_offset[0]))
            self._target_drag_y = max(4.0, min(H - self._drag_card['h'] - 4.0, py - self._drag_offset[1]))
            self._over_open = (px <= 165 and H - 110 <= py <= H - 60)
            self._over_trash = (px >= W - 148 and H - 110 <= py <= H - 60)
            self.update()
            return
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._mouse_pos = None
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        pos = event.position() if hasattr(event, 'position') else event.pos()
        px, py = pos.x(), pos.y()
        if self._drag_card:
            card = self._drag_card
            self._drag_card = None
            W, H = self.width(), self.height()
            if px <= 165 and H - 110 <= py <= H - 60:
                _play_bt_transition_sound()
                self.show_gesture_feedback("◈ EXPANDED FULLSCREEN 📂")
                if callable(getattr(self, 'on_card_open_fullscreen', None)):
                    self.on_card_open_fullscreen(card)
                self._over_open = False
                self._over_trash = False
                self.update()
                return
            elif px >= W - 148 and H - 110 <= py <= H - 60:
                if card in self.floating_news:
                    self.floating_news.remove(card)
                _play_bt_hologram_sound()
                self.show_gesture_feedback("◈ DISPOSED IN TRASH 🗑️")
                self._over_open = False
                self._over_trash = False
                self.update()
                return
            self._over_open = False
            self._over_trash = False
            if self._drag_start_pos:
                dist = math.hypot(px - self._drag_start_pos[0], py - self._drag_start_pos[1])
                if dist < 6.0:
                    _play_bt_hologram_sound()
                    if callable(getattr(self, 'on_card_click', None)):
                        self.on_card_click(card)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def on_camera_finger_pointer(self, norm_x: float, norm_y: float, action: str) -> None:
        """
        Handle camera fingertip pointer events:
        - 'pick' (Index + Thumb Pinch 🤏): Smoothly grab & drag floating news cards.
        - 'release': Drops dragged card; expands if over OPEN ZONE, deletes if in TRASH ZONE.
        """
        W, H = max(400, self.width()), max(300, self.height())
        px = max(0.0, min(float(W), float(norm_x * W)))
        py = max(0.0, min(float(H), float(norm_y * H)))

        if action == "release":
            if self._drag_card:
                card = self._drag_card
                self._drag_card = None
                if px <= 165 and H - 110 <= py <= H - 60:
                    _play_bt_transition_sound()
                    self.show_gesture_feedback("◈ EXPANDED FULLSCREEN 📂")
                    if callable(getattr(self, 'on_card_open_fullscreen', None)):
                        self.on_card_open_fullscreen(card)
                elif px >= W - 148 and H - 110 <= py <= H - 60:
                    if card in self.floating_news:
                        self.floating_news.remove(card)
                    _play_bt_hologram_sound()
                    self.show_gesture_feedback("◈ DISPOSED IN TRASH 🗑️")
                self._over_open = False
                self._over_trash = False
            self._finger_pos = None
            self._finger_action = None
            self.update()
            return

        self._finger_pos = (px, py)
        self._finger_action = action

        if action == "pick":
            # Index + Thumb Pinch Pick (Smooth LERP Target)
            if not self._drag_card:
                for card in reversed(self.floating_news):
                    rx, ry, rw, rh = card['x'], card['y'], card['w'], card['h']
                    if rx - 20 <= px <= rx + rw + 20 and ry - 20 <= py <= ry + rh + 20:
                        self._drag_card = card
                        self._drag_offset = (px - rx, py - ry)
                        self._target_drag_x = card['x']
                        self._target_drag_y = card['y']
                        self._over_open = False
                        self._over_trash = False
                        break
            if self._drag_card:
                self._target_drag_x = max(4.0, min(W - self._drag_card['w'] - 4.0, px - self._drag_offset[0]))
                self._target_drag_y = max(4.0, min(H - self._drag_card['h'] - 4.0, py - self._drag_offset[1]))
                self._over_open = (px <= 165 and H - 110 <= py <= H - 60)
                self._over_trash = (px >= W - 148 and H - 110 <= py <= H - 60)

        self.update()

    def set_gesture_scale(self, scale: float) -> None:
        """Dynamically zoom the 3D Hologram / Arc Reactor with 2-hand gesture."""
        self.gesture_scale = max(0.45, min(2.8, float(scale)))
        self.update()

    def show_gesture_feedback(self, text: str) -> None:
        """Display glowing HUD holographic feedback on gesture action."""
        self.gesture_banner_text = text
        self.gesture_banner_alpha = 1.0
        self.update()

    def set_sleep_mode(self, sleeping: bool) -> None:
        """Toggle low-power standby mode on the HUD canvas with smooth Vanguard Titan eye animation."""
        self.sleeping = bool(sleeping)
        now = time.time()
        if self.sleeping:
            self.state = "STANDBY"
            self._eye_target = 0.0
            self._close_start_t = now
            self._wake_start_t = None
            self._close_duration = 0.65  # Smooth cinematic 3-point shutter close (~650ms)
            self._tmr.setInterval(16)   # 60 Hz smooth animation
        else:
            if self.state in ("STANDBY", "SLEEPING"):
                self.state = "LISTENING"
            self._eye_target = 1.0
            self._wake_start_t = now
            self._close_start_t = None
            self._wake_duration = 0.50   # Power-up aperture expansion (~500ms)
            self._tmr.setInterval(16)   # 60 Hz active rate
        self.update()

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        # ── Smooth Cinematic Eye Standby Close / Wake Animation ───────────────
        now = time.time()
        animating_eye = False
        if self.sleeping:
            if getattr(self, '_close_start_t', None) is not None:
                dt_close = now - self._close_start_t
                dur = getattr(self, '_close_duration', 0.65)
                if dt_close < dur:
                    phase = dt_close / dur
                    self._eye_openness = max(0.0, 0.5 * (1.0 + math.cos(phase * math.pi)))
                    animating_eye = True
                else:
                    self._eye_openness = 0.0
                    self._close_start_t = None
                    self._tmr.setInterval(200)  # ~5 FPS low-power standby breathing
            else:
                self._eye_openness = 0.0
        else:
            if getattr(self, '_wake_start_t', None) is not None:
                dt_wake = now - self._wake_start_t
                dur = getattr(self, '_wake_duration', 0.50)
                if dt_wake < dur:
                    phase = dt_wake / dur
                    self._eye_openness = min(1.0, 0.5 * (1.0 - math.cos(phase * math.pi)))
                    animating_eye = True
                else:
                    self._eye_openness = 1.0
                    self._wake_start_t = None
                    self._tmr.setInterval(16)  # 60 FPS
            else:
                self._eye_openness = 1.0

        if self.sleeping and not animating_eye:
            self._tick += 1
            if getattr(self, '_servo_angle', None) is not None:
                self._servo_angle = (self._servo_angle + 0.08) % 360.0
            if self._on_screen():
                self.update()
            return

        self._tick += 1

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # ── BT-7274 Titan Optic Lifelike Eye Dynamics ─────────────────────────
        if not self.sleeping:
            # 1. Periodic mechanical sensor shutter flutter/blink (every 8-14s)
            if now >= getattr(self, '_next_blink_t', 0.0):
                self._blink_start_t = now
                self._next_blink_t = now + random.uniform(8.0, 14.0)

            blink_dt = now - getattr(self, '_blink_start_t', 0.0)
            if 0.0 <= blink_dt < 0.20:
                # Smooth V-curve closure down to 0.08 and snap back
                blink_phase = math.sin(blink_dt / 0.20 * math.pi)
                self._eye_blink = 1.0 - 0.90 * blink_phase
            else:
                self._eye_blink = 1.0

            # 2. Gaze Targeting: Pilot cursor glance tracking + vocoder micro-tremors when speaking
            if getattr(self, '_mouse_pos', None) is not None:
                mx, my = self._mouse_pos
                W, H = self.width(), self.height()
                cx = W / 2.0
                eye_cy = H * 0.43
                mdx = (mx - cx) / max(1.0, W * 0.4)
                mdy = (my - eye_cy) / max(1.0, H * 0.4)
                base_gx = max(-5.0, min(5.0, mdx * 5.0))
                base_gy = max(-4.0, min(4.0, mdy * 4.0))
            else:
                base_gx = 0.0
                base_gy = 0.0

            if self.speaking or amp > 0.04:
                self._target_gaze_x = base_gx + math.sin(now * 38.0) * amp * 1.6
                self._target_gaze_y = base_gy + math.cos(now * 32.0) * amp * 1.3
            else:
                self._target_gaze_x = base_gx
                self._target_gaze_y = base_gy

            # Smooth mechanical servo damping
            self._eye_gaze_x += (self._target_gaze_x - self._eye_gaze_x) * 0.20
            self._eye_gaze_y += (self._target_gaze_y - self._eye_gaze_y) * 0.20
        else:
            self._eye_blink = 1.0
            self._eye_gaze_x = 0.0
            self._eye_gaze_y = 0.0

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now

        # Smooth LERP interpolation for dragged news card
        if self._drag_card:
            self._drag_card['x'] += (self._target_drag_x - self._drag_card['x']) * 0.40
            self._drag_card['y'] += (self._target_drag_y - self._drag_card['y']) * 0.40
        # Integrated phase & Vanguard Titan Optic Servo Dynamics
        self._core_phase += min(0.10, max(0.0, dt))
        if not self.sleeping:
            # Discrete stepped autofocus adjustments (Titanfall 2 Vanguard mechanics)
            if now >= self._next_autofocus_t:
                if self.speaking or amp > 0.05:
                    step_delay = random.uniform(0.6, 1.8)
                    delta_choices = (-45.0, -30.0, 30.0, 45.0, 60.0, -60.0)
                elif self.state in ("THINKING", "PROCESSING"):
                    step_delay = random.uniform(0.5, 1.2)
                    delta_choices = (-90.0, -45.0, 45.0, 90.0)
                else:
                    step_delay = random.uniform(3.5, 6.5)
                    delta_choices = (-30.0, 30.0, -45.0, 45.0, 0.0)
                self._next_autofocus_t = now + step_delay
                self._target_servo_angle = (self._target_servo_angle + random.choice(delta_choices)) % 360.0

            # Spring-damper robotic servo physics (quick snap, mechanical damping to rest)
            diff = (self._target_servo_angle - self._servo_angle)
            diff = (diff + 180.0) % 360.0 - 180.0  # shortest angular path
            spring_k = 38.0 if (self.speaking or self.state in ("THINKING", "PROCESSING")) else 24.0
            damping = 0.70
            self._servo_vel += (diff * spring_k) * dt
            self._servo_vel *= (damping ** (dt * 60.0))
            self._servo_angle = (self._servo_angle + self._servo_vel * dt) % 360.0

            # Vocoder audio jitter when BT is speaking
            if self.speaking or amp > 0.04:
                self._servo_angle += random.uniform(-0.6, 0.6) * amp
            self._eye_rot = self._servo_angle
        else:
            self._servo_vel *= 0.85

        if self.gesture_banner_alpha > 0.0:
            self.gesture_banner_alpha = max(0.0, self.gesture_banner_alpha - dt * 0.55)

        # Core breathing base target, lifted by the level.
        if now - self._last_t > (0.12 if self.speaking else 0.5):
            if self.speaking:
                self._base_scale = 1.03
                self._base_halo  = 122.0
            elif self.muted:
                self._base_scale = random.uniform(0.998, 1.002)
                self._base_halo  = random.uniform(15, 28)
            else:
                self._base_scale = random.uniform(1.001, 1.008)
                self._base_halo  = random.uniform(48, 68)
            self._last_t = now

        if self.muted:
            self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
        elif self.speaking:
            self._tgt_scale = self._base_scale + amp * 0.13
            self._tgt_halo  = self._base_halo  + amp * 95.0
        else:
            self._tgt_scale = self._base_scale + amp * 0.06
            self._tgt_halo  = self._base_halo  + amp * 75.0

        sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
        self._scale += (self._tgt_scale - self._scale) * sp
        self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or amp > 0.02
                  or self.state in ("THINKING", "PROCESSING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.



    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx = W / 2.0
        eye_cy = H * 0.43

        # 1. Background fill & faint blueprint wireframe
        p.fillRect(self.rect(), qcol("#020912"))
        if getattr(self, "_bg_pix", None) and not self._bg_pix.isNull():
            p.setOpacity(0.55)
            p.drawPixmap(self.rect(), self._bg_pix)
            p.setOpacity(1.0)

        # 2. Outer cyber border
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol("#144460"), 1.5))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 10, 10)

        # 3. Top Header Bar: CHASSIS: BT-7274 // VANGUARD-CLASS   ///
        p.setPen(QPen(qcol("#00e5ff"), 1.2))
        p.drawLine(QPointF(12, 14), QPointF(18, 14))
        p.drawLine(QPointF(12, 14), QPointF(12, 22))
        p.drawLine(QPointF(W - 12, 14), QPointF(W - 18, 14))
        p.drawLine(QPointF(W - 12, 14), QPointF(W - 12, 22))

        hdr_rect = QRectF(18, 10, 240, 22)
        p.setPen(QPen(qcol("#1a5b80"), 1.2))
        p.setBrush(QBrush(qcol("#041624")))
        p.drawRoundedRect(hdr_rect, 4, 4)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol("#00e5ff"), 1))
        p.drawText(hdr_rect, Qt.AlignmentFlag.AlignCenter, "CHASSIS: BT-7274 // VANGUARD-CLASS")

        # Diagonal accent notches ///
        nx = 275
        p.setPen(QPen(qcol("#00e5ff"), 2.2))
        for k in range(3):
            p.drawLine(QPointF(nx + k * 8, 26), QPointF(nx + k * 8 + 6, 14))

        # 4. Central Optic Core & 4 Crosshairs
        r = min(W * 0.38, H * 0.38) * self.gesture_scale
        servo_ang = getattr(self, '_servo_angle', 0.0)
        eye_open = getattr(self, '_eye_openness', 1.0)
        now = time.time()

        # Standby dormant breathing reactor pulse (period ~3.5s)
        standby_breath = 0.65 + 0.35 * math.sin(now * 1.8)

        # Dynamic Primary HUD Color: blends from Electric Cyan (#00e5ff) to Titan Standby Amber (#ffaa00)
        c_r = int(0 * eye_open + 255 * (1.0 - eye_open))
        c_g = int(229 * eye_open + 170 * (1.0 - eye_open))
        c_b = int(255 * eye_open + 0 * (1.0 - eye_open))
        hud_pri = QColor(c_r, c_g, c_b)

        # Reticle opacity dimming in standby
        reticle_dim = 0.40 + 0.60 * eye_open

        # Cardinal Crosshairs
        ch_dist = r * 1.13
        p.setPen(QPen(QColor(c_r, c_g, c_b, int(255 * reticle_dim)), 1.5))
        for ang in (0, 90, 180, 270):
            rad = math.radians(ang)
            px = cx + math.cos(rad) * ch_dist
            py = eye_cy + math.sin(rad) * ch_dist
            p.drawLine(QPointF(px - 5, py), QPointF(px + 5, py))
            p.drawLine(QPointF(px, py - 5), QPointF(px, py + 5))

        # Outermost Segmented Arc Ring (dimmed in standby)
        p.setPen(QPen(QColor(c_r, c_g, c_b, int(230 * reticle_dim)), 2.2))
        for a_deg in (15, 105, 195, 285):
            p.drawArc(QRectF(cx - r, eye_cy - r, r * 2, r * 2), int(a_deg * 16), int(60 * 16))

        # Outer Thin Guide Ring
        p.setPen(QPen(QColor(c_r, c_g, c_b, int(90 * reticle_dim)), 1.0))
        r_guide = r * 0.93
        p.drawEllipse(QRectF(cx - r_guide, eye_cy - r_guide, r_guide * 2, r_guide * 2))

        # Main Concentric Dial with Tick Marks
        r_dial = r * 0.81
        p.setPen(QPen(QColor(c_r, c_g, c_b, int(220 * reticle_dim)), 1.4))
        p.drawEllipse(QRectF(cx - r_dial, eye_cy - r_dial, r_dial * 2, r_dial * 2))

        for ti in range(72):
            deg = ti * 5 + servo_ang * (0.3 if not self.sleeping else 0.08)
            trad = math.radians(deg)
            is_cardinal = (ti % 18 == 0)
            is_major = (ti % 6 == 0)
            if is_cardinal:
                p.setPen(QPen(QColor(c_r, c_g, c_b, int(255 * reticle_dim)), 3.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(cx + math.cos(trad) * (r_dial - 13), eye_cy + math.sin(trad) * (r_dial - 13)),
                           QPointF(cx + math.cos(trad) * r_dial, eye_cy + math.sin(trad) * r_dial))
            elif is_major:
                p.setPen(QPen(QColor(c_r, c_g, c_b, int(200 * reticle_dim)), 1.8))
                p.drawLine(QPointF(cx + math.cos(trad) * (r_dial - 8), eye_cy + math.sin(trad) * (r_dial - 8)),
                           QPointF(cx + math.cos(trad) * r_dial, eye_cy + math.sin(trad) * r_dial))
            else:
                p.setPen(QPen(QColor(c_r, c_g, c_b, int(150 * reticle_dim)), 1.0))
                p.drawLine(QPointF(cx + math.cos(trad) * (r_dial - 5), eye_cy + math.sin(trad) * (r_dial - 5)),
                           QPointF(cx + math.cos(trad) * r_dial, eye_cy + math.sin(trad) * r_dial))

        # Inner dashed ring
        r_inner_ring = r * 0.66
        p.setPen(QPen(QColor(c_r, c_g, c_b, int(160 * reticle_dim)), 1.2, Qt.PenStyle.DashLine))
        p.drawEllipse(QRectF(cx - r_inner_ring, eye_cy - r_inner_ring, r_inner_ring * 2, r_inner_ring * 2))

        # Aperture Outer Bezel
        r_out = r * 0.56
        p.setPen(QPen(QColor(c_r, c_g, c_b, int(220 * reticle_dim)), 1.8))
        p.setBrush(QBrush(QColor("#020f1c")))
        p.drawEllipse(QRectF(cx - r_out, eye_cy - r_out, r_out * 2, r_out * 2))

        # Central Eye Housing (Dark 3D sphere bezel)
        r_housing = r * 0.35
        g_house = QRadialGradient(cx - 8, eye_cy - 8, r_housing)
        g_house.setColorAt(0.0, QColor("#041d34"))
        g_house.setColorAt(0.7, QColor("#021222"))
        g_house.setColorAt(1.0, QColor("#010a14"))
        p.setBrush(QBrush(g_house))
        p.setPen(QPen(QColor(c_r, c_g, c_b, int(150 * reticle_dim)), 1.5))
        p.drawEllipse(QRectF(cx - r_housing, eye_cy - r_housing, r_housing * 2, r_housing * 2))

        # Voice-Reactive Talking Modulation (Speech Amplitude & Phoneme Tremor)
        amp = self._amp_disp
        is_talking = (self.speaking and not self.sleeping) or (amp > 0.025 and not self.sleeping)
        speech_amp = amp if is_talking else 0.0
        if self.speaking and not self.sleeping:
            synth_mod = 0.14 + 0.10 * math.sin(now * 18.0) + 0.06 * math.cos(now * 28.0)
            speech_amp = max(speech_amp, synth_mod)

        # ── 3-Blade Mechanical Triangular Aperture Shutter & Optical Core ──
        # Shutter well radius matches the recessed housing
        r_shutter = r * 0.35
        shutter_circle = QPainterPath()
        shutter_circle.addEllipse(QRectF(cx - r_shutter, eye_cy - r_shutter, r_shutter * 2, r_shutter * 2))

        # Kinematic 3-Blade Triangular Aperture Calculation:
        # Open (eye_open = 1.0): Blades retracted outside perimeter (d >= r_shutter * 1.15)
        # Standby (eye_open = 0.0): Blades meet at central vertex (d = 0.0)
        twist_deg = (1.0 - eye_open) * 35.0
        d = r_shutter * 1.15 * eye_open

        # 3 vertices of the triangular aperture opening at 120° offsets
        v_pts = []
        for vi in range(3):
            v_ang = math.radians(vi * 120.0 - 90.0 + twist_deg)
            vx = cx + math.cos(v_ang) * (2.0 * d)
            vy = eye_cy + math.sin(v_ang) * (2.0 * d)
            v_pts.append(QPointF(vx, vy))

        tri_path = QPainterPath()
        tri_path.moveTo(v_pts[0])
        tri_path.lineTo(v_pts[1])
        tri_path.lineTo(v_pts[2])
        tri_path.closeSubpath()

        # A. LENS (Rendered inside the triangular aperture when open or transitioning)
        if eye_open > 0.001:
            aperture_path = shutter_circle.intersected(tri_path) if eye_open < 0.99 else shutter_circle
            p.save()
            p.setClipPath(aperture_path)

            # Organic optic focal breathing (continuous gentle focal dilation)
            idle_focal_breath = 0.038 * math.sin(now * 2.1) if not self.sleeping else 0.0

            # Power-up optic bloom flash when waking from standby
            wake_flash = 0.0
            if getattr(self, '_wake_start_t', None) is not None:
                w_dt = now - self._wake_start_t
                if 0.10 <= w_dt <= 0.50:
                    w_phase = (w_dt - 0.10) / 0.40
                    wake_flash = math.sin(w_phase * math.pi) * 0.50

            speech_pulse = speech_amp * 0.22 + idle_focal_breath + wake_flash * 0.20
            r_lens = r_shutter * (1.0 + speech_pulse * 0.35)
            g_lens = QRadialGradient(cx, eye_cy, r_lens)
            w_bloom = min(0.38, 0.14 + speech_amp * 0.14 + wake_flash * 0.18)
            g_lens.setColorAt(0.00, QColor("#ffffff"))
            g_lens.setColorAt(w_bloom, QColor(int(212 * eye_open + 255 * (1 - eye_open)),
                                           int(250 * eye_open + 230 * (1 - eye_open)),
                                           int(255 * eye_open + 180 * (1 - eye_open))))
            g_lens.setColorAt(0.35, QColor(c_r, c_g, c_b))
            g_lens.setColorAt(0.65, QColor(int(0 * eye_open + 200 * (1 - eye_open)),
                                           int(119 * eye_open + 80 * (1 - eye_open)),
                                           int(221 * eye_open + 0 * (1 - eye_open))))
            g_lens.setColorAt(0.90, QColor(int(0 * eye_open + 100 * (1 - eye_open)),
                                           int(51 * eye_open + 30 * (1 - eye_open)),
                                           int(119 * eye_open + 0 * (1 - eye_open))))
            g_lens.setColorAt(1.00, QColor("#001833"))
            p.setBrush(QBrush(g_lens))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx - r_lens, eye_cy - r_lens, r_lens * 2, r_lens * 2))

            # Sapphire Retainer Ring (pulses with voice modulation & idle breathing)
            r_inner_neon = r_shutter * 0.60 * (1.0 + speech_amp * 0.12 + idle_focal_breath * 0.3)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(c_r, c_g, c_b, int(min(255, 200 * eye_open + speech_amp * 55))), 1.5))
            p.drawEllipse(QRectF(cx - r_inner_neon, eye_cy - r_inner_neon, r_inner_neon * 2, r_inner_neon * 2))

            # Pupil Diode Hotspot coordinate
            gx = getattr(self, '_eye_gaze_x', 0.0) * eye_open
            gy = getattr(self, '_eye_gaze_y', 0.0) * eye_open
            if is_talking:
                gx += math.sin(now * 48.0) * speech_amp * 2.2
                gy += math.cos(now * 40.0) * speech_amp * 1.6

            # Acoustic Resonance Shockwaves (Titanfall 2 Voice Wavefronts radiating in lens)
            if speech_amp > 0.02 and eye_open > 0.10:
                for wave_i in range(3):
                    wave_phase = (now * 2.8 + wave_i * 0.33) % 1.0
                    wave_r = r_shutter * 0.15 + wave_phase * (r_inner_neon - r_shutter * 0.15)
                    wave_alpha = int(min(255, (1.0 - wave_phase) * speech_amp * 210 * eye_open))
                    if wave_alpha > 5:
                        p.setPen(QPen(QColor(0, 229, 255, wave_alpha), 1.4))
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.drawEllipse(QRectF(cx + gx - wave_r, eye_cy + gy - wave_r, wave_r * 2.0, wave_r * 2.0))

            # Inner Laser Collimator Ring
            r_collim = r_shutter * 0.36
            p.setPen(QPen(QColor(166, 247, 255, int(180 * eye_open)), 1.2))
            p.drawEllipse(QRectF(cx - r_collim, eye_cy - r_collim, r_collim * 2, r_collim * 2))

            # 6 Rotating Collimator Micro-Prism Marks (Vanguard Optic Stepper Accents)
            for pi in range(6):
                p_ang = math.radians(pi * 60.0 - servo_ang * 0.6)
                px1 = cx + math.cos(p_ang) * (r_collim - 3.5)
                py1 = eye_cy + math.sin(p_ang) * (r_collim - 3.5)
                px2 = cx + math.cos(p_ang) * (r_collim + 3.5)
                py2 = eye_cy + math.sin(p_ang) * (r_collim + 3.5)
                p.setPen(QPen(QColor(166, 247, 255, int(170 * eye_open)), 1.3))
                p.drawLine(QPointF(px1, py1), QPointF(px2, py2))

            # Dark pupil ring
            r_pup_ring = r_shutter * (0.16 + 0.08 * eye_open)
            p.setPen(QPen(QColor(0, 51, 102), 1.3))
            p.setBrush(QBrush(QColor("#001830")))
            p.drawEllipse(QRectF(cx - r_pup_ring, eye_cy - r_pup_ring, r_pup_ring * 2, r_pup_ring * 2))

            # Pupil Diode Hotspot
            r_diode = r_shutter * (0.12 + 0.04 * eye_open) * (1.0 + speech_amp * 0.85)
            g_diode = QRadialGradient(cx + gx, eye_cy + gy, r_diode)
            g_diode.setColorAt(0.0, QColor("#ffffff"))
            g_diode.setColorAt(0.55, QColor("#e0fcff"))
            g_diode.setColorAt(1.0, QColor(0, 229, 255, 0))
            p.setBrush(QBrush(g_diode))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx + gx - r_diode, eye_cy + gy - r_diode, r_diode * 2, r_diode * 2))

            # Horizontal Anamorphic Optic Lens Flare (Signature Titanfall 2 Lens Beam)
            if eye_open > 0.15:
                flare_w = r_shutter * (1.45 + speech_amp * 1.3 + wake_flash * 1.1)
                flare_h = max(2.5, 3.8 + speech_amp * 7.0 + wake_flash * 7.5)
                g_flare = QLinearGradient(cx + gx - flare_w, eye_cy + gy, cx + gx + flare_w, eye_cy + gy)
                fl_alpha = int(min(255, (115 + speech_amp * 135 + wake_flash * 135) * eye_open))
                g_flare.setColorAt(0.00, QColor(0, 229, 255, 0))
                g_flare.setColorAt(0.35, QColor(0, 229, 255, int(fl_alpha * 0.40)))
                g_flare.setColorAt(0.50, QColor(255, 255, 255, fl_alpha))
                g_flare.setColorAt(0.65, QColor(0, 229, 255, int(fl_alpha * 0.40)))
                g_flare.setColorAt(1.00, QColor(0, 229, 255, 0))
                p.setBrush(QBrush(g_flare))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx + gx - flare_w, eye_cy + gy - flare_h / 2.0, flare_w * 2.0, flare_h))

            # Thinking / Processing diagnostic scanner arc
            if self.state in ("THINKING", "PROCESSING") and not self.sleeping:
                scan_ang = (now * 280.0) % 360.0
                p.setPen(QPen(QColor(255, 170, 0, 220), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(QRectF(cx - r_shutter, eye_cy - r_shutter, r_shutter * 2, r_shutter * 2), int(scan_ang * 16), int(45 * 16))

            # Specular Arc Glint (Dynamic shimmer with eye movement)
            if eye_open > 0.15:
                spec_alpha = int(230 * eye_open)
                spec_shimmer = math.sin(now * 3.0) * 4.0
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, spec_alpha), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(QRectF(cx - r_inner_neon, eye_cy - r_inner_neon, r_inner_neon * 2, r_inner_neon * 2), int((105 + spec_shimmer) * 16), int(55 * 16))
                p.setPen(QPen(QColor(0, 229, 255, int(140 * eye_open)), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawArc(QRectF(cx - r_shutter * 0.82, eye_cy - r_shutter * 0.82, r_shutter * 1.64, r_shutter * 1.64), int(290 * 16), int(35 * 16))

            p.restore()

        # B. TITANIUM SHUTTER BLADES (3 Mechanical plates closing over the lens)
        if eye_open < 0.99:
            shutter_blades = shutter_circle.subtracted(tri_path) if eye_open > 0.001 else shutter_circle
            p.save()
            p.setClipPath(shutter_blades)

            # Titanium plate metallic surface
            g_titan = QRadialGradient(cx, eye_cy, r_shutter)
            g_titan.setColorAt(0.00, QColor(24, 34, 48, 255))
            g_titan.setColorAt(0.70, QColor(14, 22, 32, 255))
            g_titan.setColorAt(1.00, QColor(8, 12, 18, 255))
            p.setBrush(QBrush(g_titan))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx - r_shutter, eye_cy - r_shutter, r_shutter * 2, r_shutter * 2))

            # Concentric stress relief micro-groove on the plates
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(36, 50, 68, 200), 1.2))
            p.drawEllipse(QRectF(cx - r_shutter * 0.72, eye_cy - r_shutter * 0.72, r_shutter * 1.44, r_shutter * 1.44))

            p.restore()

            p.save()
            p.setClipPath(shutter_circle)
            if eye_open > 0.001:
                # 3 leading beveled blade edges
                p.setPen(QPen(QColor(8, 14, 22), 3.0))
                p.drawLine(v_pts[0], v_pts[1])
                p.drawLine(v_pts[1], v_pts[2])
                p.drawLine(v_pts[2], v_pts[0])

                # Chamfer reflection highlight on the leading blade edges
                p.setPen(QPen(QColor(0, 229, 255, int(180 * (1.0 - eye_open))), 1.6))
                p.drawLine(v_pts[0], v_pts[1])
                p.drawLine(v_pts[1], v_pts[2])
                p.drawLine(v_pts[2], v_pts[0])

                # Seams between the 3 overlapping plates radiating from triangle vertices to rim
                for vi in range(3):
                    v_ang = math.radians(vi * 120.0 - 90.0 + twist_deg)
                    vx = cx + math.cos(v_ang) * (2.0 * d)
                    vy = eye_cy + math.sin(v_ang) * (2.0 * d)
                    rx = cx + math.cos(v_ang) * r_shutter
                    ry = eye_cy + math.sin(v_ang) * r_shutter
                    p.setPen(QPen(QColor(6, 10, 16), 2.5))
                    p.drawLine(QPointF(vx, vy), QPointF(rx, ry))
                    p.setPen(QPen(QColor(40, 60, 80), 1.2))
                    p.drawLine(QPointF(vx, vy), QPointF(rx, ry))
            else:
                # Fully Closed: 3 radial armor seams meeting at the central vertex at 120°
                for vi in range(3):
                    v_ang = math.radians(vi * 120.0 - 90.0 + twist_deg)
                    rx = cx + math.cos(v_ang) * r_shutter
                    ry = eye_cy + math.sin(v_ang) * r_shutter
                    # Deep seam groove
                    p.setPen(QPen(QColor(4, 8, 12), 3.0))
                    p.drawLine(QPointF(cx, eye_cy), QPointF(rx, ry))
                    # Machined bevel edge
                    p.setPen(QPen(QColor(46, 68, 92), 1.2))
                    p.drawLine(QPointF(cx, eye_cy), QPointF(rx, ry))

                # Central 3-Point Mechanical Locking Hub
                hub_r = r_shutter * 0.22
                g_hub = QRadialGradient(cx, eye_cy, hub_r)
                g_hub.setColorAt(0.00, QColor(32, 46, 64))
                g_hub.setColorAt(0.75, QColor(16, 24, 34))
                g_hub.setColorAt(1.00, QColor(10, 14, 20))
                p.setBrush(QBrush(g_hub))
                p.setPen(QPen(QColor(60, 85, 115), 1.5))
                p.drawEllipse(QRectF(cx - hub_r, eye_cy - hub_r, hub_r * 2, hub_r * 2))

                # 3-point bolt accents on locking hub
                for bi in range(3):
                    bang = math.radians(bi * 120.0 - 30.0 + twist_deg)
                    bx = cx + math.cos(bang) * (hub_r * 0.65)
                    by = eye_cy + math.sin(bang) * (hub_r * 0.65)
                    p.setBrush(QBrush(QColor(8, 12, 16)))
                    p.setPen(QPen(QColor(50, 70, 95), 1.0))
                    p.drawEllipse(QRectF(bx - 2, by - 2, 4, 4))

                # Standby Low-Power Amber Beacon (Breathing core pulse)
                ember_r = hub_r * 0.40
                br = standby_breath
                g_ember = QRadialGradient(cx, eye_cy, ember_r)
                g_ember.setColorAt(0.0, QColor(255, 235, 190, int(240 * br)))
                g_ember.setColorAt(0.5, QColor(255, 170, 0, int(180 * br)))
                g_ember.setColorAt(1.0, QColor(180, 70, 0, 0))
                p.setBrush(QBrush(g_ember))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QRectF(cx - ember_r, eye_cy - ember_r, ember_r * 2, ember_r * 2))

            p.restore()

        # Retaining Bezel Ring around the shutter housing
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(hud_pri, 1.8 if eye_open > 0.05 else 1.2))
        p.drawEllipse(QRectF(cx - r_shutter, eye_cy - r_shutter, r_shutter * 2, r_shutter * 2))

        # Standby Radar Scanline Sweep (only active during standby)
        if self.sleeping:
            scan_phase = (now * 0.35) % 1.0  # sweeps down every ~2.8s
            scan_y = eye_cy - r * 0.9 + scan_phase * (r * 1.8)
            dy = abs(scan_y - eye_cy)
            if dy < r * 0.9:
                scan_half_w = math.sqrt((r * 0.9)**2 - dy**2)
                scan_grad = QLinearGradient(cx - scan_half_w, scan_y, cx + scan_half_w, scan_y)
                scan_grad.setColorAt(0.0, QColor(255, 170, 0, 0))
                scan_grad.setColorAt(0.5, QColor(255, 180, 0, int(70 * standby_breath)))
                scan_grad.setColorAt(1.0, QColor(255, 170, 0, 0))
                p.setPen(QPen(QBrush(scan_grad), 1.8))
                p.drawLine(QPointF(cx - scan_half_w, scan_y), QPointF(cx + scan_half_w, scan_y))

        # ── Holographic Hand Gesture Banner ──
        if self.gesture_banner_alpha > 0.02 and self.gesture_banner_text:
            p.save()
            gb_col = qcol(C.PRI, int(min(1.0, self.gesture_banner_alpha) * 255))
            gb_bg = qcol(C.PANEL2, int(min(1.0, self.gesture_banner_alpha) * 220))
            p.setPen(QPen(gb_col, 1.2))
            p.setBrush(QBrush(gb_bg))
            gb_rect = QRectF(cx - 150, 18, 300, 28)
            p.drawRoundedRect(gb_rect, 6, 6)
            p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            p.drawText(gb_rect, Qt.AlignmentFlag.AlignCenter, self.gesture_banner_text)
            p.restore()

        # ── Floating Holographic News & Intel Cards ──
        if self.floating_news:
            p.save()
            for card in self.floating_news:
                cx_c, cy_c, cw_c, ch_c = card['x'], card['y'], card['w'], card['h']
                is_dragged = (card == self._drag_card)
                card_rect = QRectF(cx_c, cy_c, cw_c, ch_c)
                p.setBrush(QBrush(qcol(C.PANEL, 215 if not is_dragged else 245)))
                p.setPen(QPen(qcol(C.PRI if is_dragged else C.BORDER_B, 220), 1.4 if not is_dragged else 2.0))
                p.drawRoundedRect(card_rect, 5, 5)

                # Corner brackets
                p.setPen(QPen(qcol(C.PRI, 255), 1.8))
                m = 4
                p.drawLine(QLineF(cx_c, cy_c + m, cx_c, cy_c))
                p.drawLine(QLineF(cx_c, cy_c, cx_c + m, cy_c))
                p.drawLine(QLineF(cx_c + cw_c - m, cy_c, cx_c + cw_c, cy_c))
                p.drawLine(QLineF(cx_c + cw_c, cy_c, cx_c + cw_c, cy_c + m))

                # Category Badge
                p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
                p.setPen(QPen(qcol(C.PRI_DIM, 255), 1))
                p.drawText(QRectF(cx_c + 7, cy_c + 4, cw_c - 26, 12), Qt.AlignmentFlag.AlignLeft, "◈ INTEL")

                # Close / Trash Button '✕'
                p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                p.setPen(QPen(qcol(C.RED, 220), 1))
                p.drawText(QRectF(cx_c + cw_c - 16, cy_c + 3, 12, 12), Qt.AlignmentFlag.AlignCenter, "✕")

                p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                p.setPen(QPen(qcol(C.WHITE, 240), 1))
                fm = p.fontMetrics()
                elided = fm.elidedText(card['title'], Qt.TextElideMode.ElideRight, int(cw_c - 14))
                p.drawText(QRectF(cx_c + 7, cy_c + 18, cw_c - 14, 34), Qt.TextFlag.TextWordWrap, elided)

            # ── Draw Dustbin / Trash Can Zone (Bottom-Right) ──
            trash_rect = QRectF(W - 148, H - 105, 134, 34)
            is_over_tr = getattr(self, '_over_trash', False)
            p.setBrush(QBrush(qcol(C.RED if is_over_tr else C.PANEL2, 160 if not is_over_tr else 235)))
            p.setPen(QPen(qcol(C.RED if is_over_tr else C.BORDER_B, 255 if is_over_tr else 180),
                          2.0 if is_over_tr else 1.2,
                          Qt.PenStyle.SolidLine if is_over_tr else Qt.PenStyle.DashLine))
            p.drawRoundedRect(trash_rect, 6, 6)
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.setPen(QPen(qcol(C.WHITE if is_over_tr else C.TEXT_DIM, 255), 1))
            p.drawText(trash_rect, Qt.AlignmentFlag.AlignCenter, "🗑️ DROP TO DELETE" if is_over_tr else "🗑️ TRASH ZONE")

            # ── Draw Open / Expand Zone (Bottom-Left) when dragging ──
            if self._drag_card is not None:
                open_rect = QRectF(16, H - 105, 140, 34)
                is_over_op = getattr(self, '_over_open', False)
                p.setBrush(QBrush(qcol(C.PRI if is_over_op else C.PANEL2, 160 if not is_over_op else 235)))
                p.setPen(QPen(qcol(C.PRI if is_over_op else C.BORDER_B, 255 if is_over_op else 180),
                              2.0 if is_over_op else 1.2,
                              Qt.PenStyle.SolidLine if is_over_op else Qt.PenStyle.DashLine))
                p.drawRoundedRect(open_rect, 6, 6)
                p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                p.setPen(QPen(qcol(C.WHITE if is_over_op else C.TEXT_DIM, 255), 1))
                p.drawText(open_rect, Qt.AlignmentFlag.AlignCenter, "📂 DROP TO EXPAND" if is_over_op else "📂 OPEN ZONE")
            p.restore()

        # ── Holographic Fingertip Cursor ──
        if getattr(self, '_finger_pos', None) is not None:
            fx, fy = self._finger_pos
            p.save()
            p.setPen(QPen(qcol("#00e5ff"), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(fx, fy), 10, 10)
            p.drawEllipse(QPointF(fx, fy), 3, 3)
            p.drawLine(QLineF(fx - 16, fy, fx - 5, fy))
            p.drawLine(QLineF(fx + 5, fy, fx + 16, fy))
            p.drawLine(QLineF(fx, fy - 16, fx, fy - 5))
            p.drawLine(QLineF(fx, fy + 5, fx, fy + 16))
            p.restore()

        # 5. Symmetrical Audio Waveforms & Status Pill
        sy = eye_cy + r + 36
        pill_w, pill_h = 150, 30
        pill_rect = QRectF(cx - pill_w / 2.0, sy - pill_h / 2.0, pill_w, pill_h)

        if self.muted:
            txt, col = "MUTED", qcol(C.MUTED_C)
        elif self.speaking:
            spk_alpha = int(210 + 45 * math.sin(now * 10.0))
            txt, col = "SPEAKING", QColor(0, 229, 255, spk_alpha)
        elif self.sleeping or self.state == "STANDBY":
            sb_alpha = int(180 + 75 * math.sin(now * 2.5))
            txt, col = "STANDBY", QColor(255, 170, 0, sb_alpha)
        elif self.state == "THINKING":
            txt, col = "THINKING", qcol(C.ACC2)
        elif self.state == "PROCESSING":
            txt, col = "PROCESSING", qcol(C.ACC2)
        elif self.state in ("LISTENING", "INITIALISING"):
            txt, col = "LISTENING", qcol(C.GREEN)
        else:
            txt, col = self.state.upper(), qcol(C.PRI)

        p.setBrush(QBrush(qcol("#031824")))
        p.setPen(QPen(col, 1.6))
        p.drawRoundedRect(pill_rect, 15, 15)

        # Dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col))
        p.drawEllipse(QPointF(cx - 45, sy), 5, 5)

        # Text
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.setPen(QPen(col, 1))
        p.drawText(QRectF(cx - 35, sy - 14, 100, 28), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, txt)

        # Flanking Waveforms (only appear when real sound / audio is detected or talking)
        wave_amp = max(self._amp_disp, speech_amp if self.speaking else 0.0)
        if wave_amp > 0.025:
            p.setPen(Qt.PenStyle.NoPen)
            n_bars, bw, gap = 11, 4, 3
            boosted = min(1.0, (wave_amp - 0.02) * 2.8)
            for i in range(n_bars):
                bx_l = cx - 85 - (n_bars - i) * (bw + gap)
                bx_r = cx + 85 + i * (bw + gap)
                arch = math.sin((i + 1) / (n_bars + 1.0) * math.pi)
                bar_jitter = 0.85 + 0.15 * math.sin(now * 20.0 + i * 0.8)
                hgt = int(max(3, boosted * 26.0 * (0.5 + 0.5 * arch) * bar_jitter))
                p.setBrush(QBrush(QColor("#00e5ff")))
                p.drawRoundedRect(QRectF(bx_l, sy - hgt / 2.0, bw, hgt), 1.5, 1.5)
                p.drawRoundedRect(QRectF(bx_r, sy - hgt / 2.0, bw, hgt), 1.5, 1.5)

        # 6. Bottom Protocols (Left)
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.ACC2), 1))
        prot_y = H - 58
        p.drawText(QRectF(22, prot_y, 300, 16), Qt.AlignmentFlag.AlignLeft, ">> PROTOCOL 1: LINK TO PILOT")
        p.drawText(QRectF(22, prot_y + 16, 300, 16), Qt.AlignmentFlag.AlignLeft, ">> PROTOCOL 2: UPHOLD THE MISSION")
        p.drawText(QRectF(22, prot_y + 32, 300, 16), Qt.AlignmentFlag.AlignLeft, ">> PROTOCOL 3: PROTECT THE PILOT")

        # 7. Bottom Right SRS Delta Logo
        if getattr(self, "_logo_pix", None) and not self._logo_pix.isNull():
            p.drawPixmap(int(W - 75), int(H - 68), self._logo_pix)

        p.end()   # end deterministically so the backing store never flushes an active painter


class NetSparkline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self._history = deque([12.0, 18.0, 10.0, 25.0, 48.0, 32.0, 16.0, 20.0, 40.0, 68.0, 50.0, 34.0, 26.0, 42.0, 30.0, 18.0, 14.0, 22.0, 40.0, 32.0], maxlen=24)

    def add_point(self, val: float):
        self._history.append(float(val))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        if len(self._history) < 2:
            return
        vals = list(self._history)
        max_v = max(max(vals), 1.0)
        step = W / (len(vals) - 1)
        path = QPainterPath()
        path.moveTo(0, H - (vals[0] / max_v) * (H - 5) - 2)
        for i in range(1, len(vals)):
            x = i * step
            y = H - (vals[i] / max_v) * (H - 5) - 2
            path.lineTo(x, y)

        fill_path = QPainterPath(path)
        fill_path.lineTo(W, H)
        fill_path.lineTo(0, H)
        fill_path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, QColor(0, 229, 255, 65))
        grad.setColorAt(1.0, QColor(0, 229, 255, 5))
        p.fillPath(fill_path, grad)

        p.setPen(QPen(QColor("#00e5ff"), 1.8))
        p.drawPath(path)
        p.end()


class MetricIcon(QWidget):
    def __init__(self, kind: str, is_red: bool = False, has_box: bool = True, parent=None):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.kind = kind
        self.is_red = is_red
        self.has_box = has_box

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive(): return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.has_box:
            if self.is_red:
                p.setBrush(QBrush(QColor("#280a14")))
                p.setPen(QPen(QColor("#551224"), 1))
            else:
                p.setBrush(QBrush(QColor("#082030")))
                p.setPen(QPen(QColor("#124866"), 1))
            p.drawRoundedRect(QRectF(0.5, 0.5, 17, 17), 3, 3)

        fg = QColor("#ff3366") if self.is_red else QColor("#00e5ff")
        p.setPen(QPen(fg, 1.2))

        if self.kind == "CPU":
            p.drawRect(QRectF(5, 5, 8, 8))
            p.drawLine(QPointF(6.5, 2), QPointF(6.5, 5))
            p.drawLine(QPointF(11.5, 2), QPointF(11.5, 5))
            p.drawLine(QPointF(6.5, 13), QPointF(6.5, 16))
            p.drawLine(QPointF(11.5, 13), QPointF(11.5, 16))
            p.drawLine(QPointF(2, 6.5), QPointF(5, 6.5))
            p.drawLine(QPointF(2, 11.5), QPointF(5, 11.5))
            p.drawLine(QPointF(13, 6.5), QPointF(16, 6.5))
            p.drawLine(QPointF(13, 11.5), QPointF(16, 11.5))
        elif self.kind == "RAM":
            p.drawRect(QRectF(3, 5, 12, 8))
            p.drawRect(QRectF(5, 7, 2.5, 4))
            p.drawRect(QRectF(10.5, 7, 2.5, 4))
            p.drawLine(QPointF(4, 13), QPointF(4, 14.5))
            p.drawLine(QPointF(7, 13), QPointF(7, 14.5))
            p.drawLine(QPointF(11, 13), QPointF(11, 14.5))
            p.drawLine(QPointF(14, 13), QPointF(14, 14.5))
        elif self.kind == "NET":
            p.drawLine(QPointF(6, 14), QPointF(6, 4))
            p.drawLine(QPointF(4, 6), QPointF(6, 4))
            p.drawLine(QPointF(8, 6), QPointF(6, 4))
            p.drawLine(QPointF(12, 4), QPointF(12, 14))
            p.drawLine(QPointF(10, 12), QPointF(12, 14))
            p.drawLine(QPointF(14, 12), QPointF(12, 14))
        elif self.kind == "GPU":
            p.drawRect(QRectF(3, 4, 12, 8))
            p.drawLine(QPointF(9, 12), QPointF(9, 14))
            p.drawLine(QPointF(6, 14), QPointF(12, 14))
        elif self.kind == "TEMP":
            p.drawRoundedRect(QRectF(7.5, 3, 3, 8), 1.5, 1.5)
            p.drawEllipse(QRectF(6, 9.5, 6, 6))
            p.setBrush(QBrush(fg))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(7.5, 11, 3, 3))
            p.drawRect(QRectF(8.2, 6, 1.6, 5))
        elif self.kind == "UPTIME":
            p.drawEllipse(QRectF(3.5, 4.5, 11, 11))
            p.drawLine(QPointF(9, 2), QPointF(9, 4.5))
            p.drawLine(QPointF(9, 10), QPointF(9, 6.5))
            p.drawLine(QPointF(9, 10), QPointF(12, 10))
        elif self.kind == "SYS_MONITOR":
            p.setBrush(QBrush(fg))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(1, 8, 3.5, 7), 1, 1)
            p.drawRoundedRect(QRectF(6.5, 2, 3.5, 13), 1, 1)
            p.drawRoundedRect(QRectF(12, 5, 3.5, 10), 1, 1)
        elif self.kind == "LOG":
            p.setBrush(QBrush(fg))
            p.drawEllipse(QRectF(1.5, 3.5, 2.5, 2.5))
            p.drawLine(QPointF(6, 4.5), QPointF(15, 4.5))
            p.drawEllipse(QRectF(1.5, 7.5, 2.5, 2.5))
            p.drawLine(QPointF(6, 8.5), QPointF(15, 8.5))
            p.drawEllipse(QRectF(1.5, 11.5, 2.5, 2.5))
            p.drawLine(QPointF(6, 12.5), QPointF(15, 12.5))
        elif self.kind == "CLOUD_UPLOAD":
            p.setBrush(Qt.BrushStyle.NoBrush)
            cp = QPainterPath()
            cp.moveTo(4, 13)
            cp.arcTo(QRectF(1, 7, 7, 7), 210, -180)
            cp.arcTo(QRectF(5, 3, 8, 8), 160, -170)
            cp.arcTo(QRectF(10, 6, 7, 7), 130, -170)
            cp.lineTo(4, 13)
            p.drawPath(cp)
            p.drawLine(QPointF(9, 14), QPointF(9, 8))
            p.drawLine(QPointF(6.5, 10.5), QPointF(9, 8))
            p.drawLine(QPointF(11.5, 10.5), QPointF(9, 8))
        elif self.kind == "COMMAND_INPUT":
            p.drawLine(QPointF(2, 4), QPointF(6, 8))
            p.drawLine(QPointF(6, 8), QPointF(2, 12))
            p.drawLine(QPointF(8, 12), QPointF(14, 12))
        elif self.kind == "LIGHTNING":
            p.setBrush(QBrush(fg))
            lp = QPainterPath()
            lp.moveTo(10, 1)
            lp.lineTo(4, 9)
            lp.lineTo(9, 9)
            lp.lineTo(6.5, 17)
            lp.lineTo(15, 8)
            lp.lineTo(10.5, 8)
            lp.closeSubpath()
            p.drawPath(lp)
        elif self.kind == "BELL":
            p.setBrush(Qt.BrushStyle.NoBrush)
            bp = QPainterPath()
            bp.moveTo(9, 2)
            bp.arcTo(QRectF(7.5, 1, 3, 3), 0, 180)
            bp.moveTo(9, 4)
            bp.quadTo(9, 7, 4, 13)
            bp.lineTo(14, 13)
            bp.quadTo(9, 7, 9, 4)
            p.drawPath(bp)
            p.drawLine(QPointF(3, 13), QPointF(15, 13))
            p.drawEllipse(QRectF(8, 13.5, 2, 2))
        elif self.kind == "PROTOCOL_DOC":
            p.setBrush(Qt.BrushStyle.NoBrush)
            dp = QPainterPath()
            dp.moveTo(3, 1)
            dp.lineTo(10, 1)
            dp.lineTo(15, 6)
            dp.lineTo(15, 17)
            dp.lineTo(3, 17)
            dp.closeSubpath()
            p.drawPath(dp)
            p.drawLine(QPointF(10, 1), QPointF(10, 6))
            p.drawLine(QPointF(10, 6), QPointF(15, 6))
            p.drawLine(QPointF(6, 9), QPointF(12, 9))
            p.drawLine(QPointF(6, 12), QPointF(12, 12))
            p.drawLine(QPointF(6, 15), QPointF(10, 15))
        p.end()


class SysMetricRow(QWidget):
    def __init__(self, label: str, icon_kind: str, bar_color: str, val_color: str = "#ffaa00", is_red_icon: bool = False, has_bar: bool = True, parent=None):
        super().__init__(parent)
        self._bar_color = bar_color
        self._pct = 0.0
        self._has_bar = has_bar

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(2)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        ic_box = MetricIcon(icon_kind, is_red=is_red_icon)
        top.addWidget(ic_box)

        lbl = QLabel(label)
        lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        lbl.setStyleSheet("color: #e0f7fc; background: transparent;")
        top.addWidget(lbl)
        top.addStretch()

        self._val_lbl = QLabel("0%")
        self._val_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._val_lbl.setStyleSheet(f"color: {val_color}; background: transparent;")
        top.addWidget(self._val_lbl)
        lay.addLayout(top)

        if has_bar:
            self._pbar = QProgressBar()
            self._pbar.setFixedHeight(4)
            self._pbar.setTextVisible(False)
            self._pbar.setRange(0, 100)
            self._pbar.setValue(0)
            self._pbar.setStyleSheet(f"""
                QProgressBar {{ background: #041522; border: none; border-radius: 2px; }}
                QProgressBar::chunk {{ background: {bar_color}; border-radius: 2px; }}
            """)
            lay.addWidget(self._pbar)
        else:
            self._pbar = None

    def set_value(self, pct: float, text: str):
        self._pct = max(0.0, min(100.0, float(pct)))
        self._val_lbl.setText(text)
        if self._pbar is not None:
            self._pbar.setValue(int(self._pct))


class SysMonitorCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QWidget#SysCard {
                background: #020d18;
                border: 1.5px solid #144460;
                border-radius: 8px;
            }
        """)
        self.setObjectName("SysCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(5)

        # Header: SYS MONITOR (clean vector icon)
        hdr = QHBoxLayout()
        h_icon = MetricIcon("SYS_MONITOR", has_box=False)
        hdr.addWidget(h_icon)
        h_lbl = QLabel("SYS MONITOR")
        h_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        h_lbl.setStyleSheet("color: #00e5ff; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(h_lbl)
        hdr.addStretch()
        lay.addLayout(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #10344c; margin: 0 0 2px 0;")
        lay.addWidget(sep)

        # CPU
        self.cpu_row = SysMetricRow("CPU", "CPU", "#00e5ff", "#ffaa00")
        self.cpu_row.set_value(12, "12%")
        lay.addWidget(self.cpu_row)

        # RAM
        self.ram_row = SysMetricRow("RAM", "RAM", "#a855f7", "#ffaa00")
        self.ram_row.set_value(58, "58%")
        lay.addWidget(self.ram_row)

        # NET with Sparkline
        net_top = QHBoxLayout()
        net_top.setSpacing(6)
        n_ic = MetricIcon("NET")
        net_top.addWidget(n_ic)
        n_lbl = QLabel("NET")
        n_lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        n_lbl.setStyleSheet("color: #e0f7fc; background: transparent;")
        net_top.addWidget(n_lbl)
        net_top.addStretch()
        self.net_val_lbl = QLabel("403KB/s")
        self.net_val_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self.net_val_lbl.setStyleSheet("color: #00e5ff; background: transparent;")
        net_top.addWidget(self.net_val_lbl)
        lay.addLayout(net_top)

        self.sparkline = NetSparkline()
        lay.addWidget(self.sparkline)

        # GPU (no progress bar, matching reference)
        self.gpu_row = SysMetricRow("GPU", "GPU", "#00e5ff", "#00e5ff", has_bar=False)
        self.gpu_row.set_value(0, "0%")
        lay.addWidget(self.gpu_row)

        # TEMP
        self.temp_row = SysMetricRow("TEMP", "TEMP", "#ff8800", "#ffaa00", is_red_icon=True)
        self.temp_row.set_value(77, "77°C")
        lay.addWidget(self.temp_row)

        # UPTIME
        up_box = QHBoxLayout()
        up_ic = MetricIcon("UPTIME")
        up_box.addWidget(up_ic)

        up_right = QVBoxLayout()
        up_right.setSpacing(0)
        u_lbl = QLabel("UPTIME")
        u_lbl.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
        u_lbl.setStyleSheet("color: #4a788c; background: transparent;")
        up_right.addWidget(u_lbl)

        self.uptime_clock = QLabel("00:29")
        self.uptime_clock.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        self.uptime_clock.setStyleSheet("color: #00e5ff; background: transparent;")
        up_right.addWidget(self.uptime_clock)

        self.ping_lbl = QLabel("PING  492")
        self.ping_lbl.setFont(QFont("Courier New", 6))
        self.ping_lbl.setStyleSheet("color: #4a788c; background: transparent;")
        up_right.addWidget(self.ping_lbl)
        up_box.addLayout(up_right)
        lay.addLayout(up_box)

        # Dynamic OS pill
        os_pill = QWidget()
        os_pill.setFixedHeight(24)
        os_pill.setStyleSheet("background: #041624; border: 1px solid #12425e; border-radius: 4px;")
        op_lay = QHBoxLayout(os_pill)
        op_lay.setContentsMargins(6, 0, 6, 0)
        op_lay.setSpacing(6)
        os_icon_lbl = QLabel()
        if _OS == "Windows":
            os_icon_lbl.setText("🪟")
            os_name = "WINDOWS"
        elif _OS == "Darwin":
            os_icon_lbl.setText("🍎")
            os_name = "MACOS"
        else:
            tux_path = CONFIG_DIR / "tux_icon.png"
            if tux_path.exists():
                pix = QPixmap(str(tux_path)).scaled(16, 16, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                os_icon_lbl.setPixmap(pix)
            else:
                os_icon_lbl.setText("🐧")
            os_name = "LINUX"
        os_icon_lbl.setStyleSheet("background: transparent;")
        op_lay.addWidget(os_icon_lbl)

        os_txt = QLabel(f'<span style="color: #6da2be;">OS</span>  <span style="color: #ffaa00;">{os_name}</span>')
        os_txt.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        os_txt.setStyleSheet("background: transparent;")
        op_lay.addWidget(os_txt)
        op_lay.addStretch()
        lay.addWidget(os_pill)


class StatusStackCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QWidget#StatusCard {
                background: #020d18;
                border: 1.5px solid #144460;
                border-radius: 8px;
            }
        """)
        self.setObjectName("StatusCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        items = [
            ("LIGHTNING", "AI CORE", "ACTIVE", True),
            ("BELL", "SEC", "CLEANED", False),
            ("PROTOCOL_DOC", "PROTOCOL 3", "LINKED", False),
        ]
        for icon_kind, title, val, is_highlight in items:
            tile = QWidget()
            tile.setFixedHeight(34)
            if is_highlight:
                tile.setStyleSheet("background: #041c30; border: 1.5px solid #00a8ff; border-radius: 4px;")
            else:
                tile.setStyleSheet("background: #031422; border: 1px solid #0f3850; border-radius: 4px;")
            t_lay = QHBoxLayout(tile)
            t_lay.setContentsMargins(8, 2, 8, 2)
            t_lay.setSpacing(8)

            ic_w = MetricIcon(icon_kind, has_box=False)
            t_lay.addWidget(ic_w)

            txt_col = QVBoxLayout()
            txt_col.setSpacing(0)
            t_top = QLabel(title)
            t_top.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            t_top.setStyleSheet("color: #ffffff; background: transparent;")
            txt_col.addWidget(t_top)

            t_val = QLabel(val)
            t_val.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            t_val.setStyleSheet("color: #00ff88; background: transparent;")
            txt_col.addWidget(t_val)
            t_lay.addLayout(txt_col)
            t_lay.addStretch()
            lay.addWidget(tile)


class CustomTitleBar(QWidget):
    def __init__(self, title="SRS // BT-7274 — PROTOCOL 3 // ONLINE", parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setStyleSheet("background: #01070e; border-bottom: 1px solid #0c2434;")
        self._parent = parent
        self._dragging = False
        self._drag_pos = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)

        srs_lbl = QLabel()
        srs_p = CONFIG_DIR / "srs_badge.png"
        if srs_p.exists():
            pix = QPixmap(str(srs_p)).scaled(24, 18, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            srs_lbl.setPixmap(pix)
        else:
            srs_lbl.setText("🔶")
        srs_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(srs_lbl)

        t_lbl = QLabel(title)
        t_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        t_lbl.setStyleSheet("color: #ffffff; background: transparent; letter-spacing: 1px;")
        lay.addWidget(t_lbl)
        lay.addStretch()

        b_css = "QPushButton { background: transparent; color: #88c0d0; border: none; font-size: 13px; font-weight: bold; width: 28px; height: 22px; } QPushButton:hover { background: #12344a; color: #00e5ff; }"
        self.min_btn = QPushButton("—")
        self.min_btn.setStyleSheet(b_css)
        self.min_btn.clicked.connect(lambda: parent.showMinimized() if parent else None)
        lay.addWidget(self.min_btn)

        self.max_btn = QPushButton("□")
        self.max_btn.setStyleSheet(b_css)
        self.max_btn.clicked.connect(self._toggle_max)
        lay.addWidget(self.max_btn)

        c_css = "QPushButton { background: transparent; color: #ff6b6b; border: none; font-size: 13px; font-weight: bold; width: 28px; height: 22px; } QPushButton:hover { background: #991b1b; color: #ffffff; }"
        self.close_btn = QPushButton("✕")
        self.close_btn.setStyleSheet(c_css)
        self.close_btn.clicked.connect(lambda: parent.close() if parent else None)
        lay.addWidget(self.close_btn)

    def _toggle_max(self):
        if not self._parent: return
        if self._parent.isMaximized():
            self._parent.showNormal()
        else:
            self._parent.showMaximized()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._parent:
            self._dragging = True
            self._drag_pos = e.globalPosition().toPoint() - self._parent.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._dragging and self._parent and self._drag_pos:
            self._parent.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._dragging = False
        e.accept()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()
            e.accept()


class FooterBracket(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(160, 16)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        pen = QPen(qcol("#144460"), 1.5)
        p.setPen(pen)
        p.drawLine(QPointF(4, 2), QPointF(20, H - 4))
        p.drawLine(QPointF(20, H - 4), QPointF(W - 20, H - 4))
        p.drawLine(QPointF(W - 20, H - 4), QPointF(W - 4, 2))

        p.setPen(QPen(qcol("#00e5ff"), 2.2))
        p.drawLine(QPointF(W / 2.0 - 26, H - 4), QPointF(W / 2.0 + 26, H - 4))
        p.end()


class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(30)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)

        bar_h   = 3
        bar_y   = H - bar_h - 4
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 2, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 1, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {C.PANEL};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG};
                width: 8px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B};
                border-radius: 4px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "bt"       # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                                                    self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("bt:") or tl.startswith("bt:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(70)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for BT-7274", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL))
        p.setBrush(QBrush(bg_col)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 6, 6)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2.0, H / 2.0
        p.save()
        p.setPen(QPen(qcol(C.PRI if hover else C.PRI_DIM), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        cp = QPainterPath()
        ccx, ccy = cx, cy - 12
        cp.moveTo(ccx - 9, ccy + 5)
        cp.arcTo(QRectF(ccx - 14, ccy - 2, 9, 9), 210, -180)
        cp.arcTo(QRectF(ccx - 9, ccy - 8, 12, 12), 160, -170)
        cp.arcTo(QRectF(ccx + 1, ccy - 4, 10, 10), 130, -170)
        cp.lineTo(ccx - 9, ccy + 5)
        p.drawPath(cp)
        p.drawLine(QPointF(ccx, ccy + 5), QPointF(ccx, ccy - 2))
        p.drawLine(QPointF(ccx - 3.5, ccy + 1.5), QPointF(ccx, ccy - 2))
        p.drawLine(QPointF(ccx + 3.5, ccy + 1.5), QPointF(ccx, ccy - 2))
        p.restore()

        p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE if hover else "#e0f7fc"), 1))
        p.drawText(QRectF(0, cy + 1, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here or click to browse")

        p.setFont(QFont("Segoe UI", 6))
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(QRectF(0, cy + 15, W, 12), Qt.AlignmentFlag.AlignCenter,
                   "Images • Audio • Video • Docs • Code")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2.0, H / 2.0
        p.setFont(QFont("Segoe UI Emoji", 16) if _OS == "Windows" else QFont("Segoe UI", 16))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 22, W, 24), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 4, W, 14), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 50
        p.setFont(QFont("Segoe UI Emoji", 16) if _OS == "Windows" else QFont("Arial", 16))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.12, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.12 + 16, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class WebcamPIPOverlay(QWidget):
    """
    Tactical Titanfall 2 PIP & Live Gesture Action HUD Overlay.
    Floats at the top center of the screen, staying on top of ALL windows across the desktop,
    providing real-time optical hand tracking telemetry and live action badges.
    """
    _W, _H = 260, 225

    def __init__(self, parent=None):
        super().__init__(None)  # Top-level floating window that stays on top across all apps
        self.setWindowTitle("BT_GESTURE_OVERLAY")
        self.setObjectName("WebcamPIPOverlay")
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop, True)
        self.setWindowOpacity(0.75)
        self.setStyleSheet(f"""
            WebcamPIPOverlay {{
                background: rgba(4, 10, 18, 120);
                border: 1px solid rgba(0, 229, 255, 0.55);
                border-radius: 8px;
            }}
        """)
        self.setFixedSize(self._W, self._H)
        self._user_dragged = False
        self._drag_pos = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        # Header bar
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(4)

        self.on_closed = None

        dot = QLabel("●")
        dot.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        dot.setStyleSheet("color: #00ff88; background: transparent;")
        hdr.addWidget(dot)

        title = QLabel("PILOT OPTIC // GESTURE HUD  ⋮⋮")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        title.setToolTip("Click and drag anywhere to move. Mouse wheel to adjust opacity.")
        hdr.addWidget(title)
        hdr.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._on_close_clicked)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        # Live Action & Gesture Status Badge (Always visible on top of all windows)
        self._action_lbl = QLabel("AI: [ GESTURE MONITOR ACTIVE ]")
        self._action_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._action_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._action_lbl.setStyleSheet("""
            QLabel {
                background: rgba(0, 24, 38, 140);
                border: 1px solid rgba(0, 229, 255, 0.50);
                border-radius: 3px;
                color: #00e5ff;
                padding: 2px 4px;
                letter-spacing: 0.5px;
            }
        """)
        self._action_lbl.setFixedHeight(22)
        lay.addWidget(self._action_lbl)

        # Video feed label (semi-transparent backdrop for hand landmarks & video)
        self._feed_lbl = QLabel()
        self._feed_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._feed_lbl.setStyleSheet("background: rgba(0, 8, 16, 80); border: 1px solid rgba(0, 229, 255, 0.25); border-radius: 4px;")
        self._feed_lbl.setFixedSize(self._W - 12, self._H - 60)
        lay.addWidget(self._feed_lbl)

        # Make every part of the overlay draggable with size-all cursor
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        for w in (self._feed_lbl, self._action_lbl, title, dot):
            w.mousePressEvent = self.mousePressEvent
            w.mouseMoveEvent = self.mouseMoveEvent
            w.mouseReleaseEvent = self.mouseReleaseEvent
            w.setCursor(Qt.CursorShape.SizeAllCursor)

        self.hide()

    def show_feedback(self, text: str) -> None:
        """Update live tactical gesture action text with bright visual flash."""
        self._action_lbl.setText(text)
        if any(k in text for k in ("GESTURE:", "CLICK", "TAB", "PLAY", "NEXT", "PREV", "VOL", "HOLD")):
            self._action_lbl.setStyleSheet("""
                QLabel {
                    background: rgba(45, 32, 0, 180);
                    border: 1px solid #ffcc00;
                    border-radius: 3px;
                    color: #ffcc00;
                    padding: 2px 4px;
                    letter-spacing: 0.5px;
                    font-weight: bold;
                }
            """)
            QTimer.singleShot(2600, self._reset_action_style)
        else:
            self._reset_action_style()

    def _reset_action_style(self) -> None:
        self._action_lbl.setStyleSheet("""
            QLabel {
                background: rgba(0, 24, 38, 140);
                border: 1px solid rgba(0, 229, 255, 0.50);
                border-radius: 3px;
                color: #00e5ff;
                padding: 2px 4px;
                letter-spacing: 0.5px;
            }
        """)

    def _on_close_clicked(self):
        if callable(self.on_closed):
            self.on_closed()
        else:
            self.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._user_dragged = True
            wh = self.windowHandle()
            if wh and hasattr(wh, "startSystemMove"):
                try:
                    if wh.startSystemMove():
                        event.accept()
                        return
                except Exception:
                    pass
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and getattr(self, '_drag_pos', None) is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            self._user_dragged = True
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    def wheelEvent(self, event):
        # Adjust transparency on mouse scroll (0.25 to 1.0)
        delta = event.angleDelta().y() / 1200.0
        new_op = max(0.25, min(1.0, self.windowOpacity() + delta))
        self.setWindowOpacity(new_op)
        event.accept()

    def update_frame(self, frame_bgr) -> None:
        """Render an annotated BGR numpy frame into the feed label without memory freezing."""
        try:
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            bytes_per_line = ch * w
            # .copy() creates an independent Qt buffer, decoupling from numpy thread memory
            qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
            pix = QPixmap.fromImage(qimg).scaled(
                self._feed_lbl.width(),
                self._feed_lbl.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.FastTransformation
            )
            self._feed_lbl.setPixmap(pix)
        except Exception:
            pass

    def update_pixmap(self, pix: QPixmap) -> None:
        try:
            scaled = pix.scaled(
                self._feed_lbl.width(),
                self._feed_lbl.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.FastTransformation
            )
            self._feed_lbl.setPixmap(scaled)
        except Exception:
            pass


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        key_header = QHBoxLayout()
        key_header.setSpacing(6)
        key_header.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                                   align=Qt.AlignmentFlag.AlignLeft))
        key_header.addStretch()

        studio_link = QLabel('<a href="https://aistudio.google.com/app/apikey" style="color: #00d4ff; text-decoration: none; font-weight: bold;">🔑 Get Free Key ↗</a>')
        studio_link.setFont(QFont("Courier New", 8))
        studio_link.setOpenExternalLinks(True)
        studio_link.setCursor(Qt.CursorShape.PointingHandCursor)
        studio_link.setStyleSheet("background: transparent;")
        studio_link.setToolTip("Open Google AI Studio in your default browser to generate a free Gemini API key")
        key_header.addWidget(studio_link)
        layout.addLayout(key_header)

        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("Paste Gemini API Key (AIza...)")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)

        help_lbl = QLabel('Get your free API key at <a href="https://aistudio.google.com/app/apikey" style="color: #00d4ff; text-decoration: underline;">Google AI Studio</a>')
        help_lbl.setFont(QFont("Courier New", 7))
        help_lbl.setOpenExternalLinks(True)
        help_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        help_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; padding-top: 1px;")
        layout.addWidget(help_lbl)
        layout.addSpacing(8)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Vanguard-Class Titan Cockpit HUD Settings Modal."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 560, 600

    def __init__(self, assistant_name="BT-7274", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", pip_enabled=False, gestures_enabled=False,
                 wake_enabled=False, ptt_enabled=False, autostart_enabled=False, brief_enabled=False,
                 parent=None):
        super().__init__(parent)
        self.setFixedSize(self._OW, self._OH)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            CustomizeOverlay {
                background: #010a14;
                border: 1.5px solid #00e5ff;
                border-radius: 8px;
            }
        """)

        # Callbacks
        self.on_preview = None
        self.on_pip_toggled = None
        self.on_gesture_toggled = None
        self.on_camera_changed = None
        self.on_audio_devices = None
        self.on_camera_dialog = None
        self.on_memory = None
        self.on_plugins = None
        self.on_plugin_settings = None
        self.on_remote = None
        self.on_fullscreen = None
        self.on_shortcut = None
        self.on_autostart = None
        self.on_wake_toggle = None
        self.on_ptt_toggle = None
        self.on_brief_toggle = None

        self._pip_enabled = pip_enabled
        self._gestures_enabled = gestures_enabled
        self._wake_enabled = wake_enabled
        self._ptt_enabled = ptt_enabled
        self._autostart_enabled = autostart_enabled
        self._brief_enabled = brief_enabled

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(18, 14, 18, 14)
        main_lay.setSpacing(10)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        hdr_info = QVBoxLayout()
        hdr_info.setSpacing(2)

        t_row = QHBoxLayout()
        t_row.setSpacing(6)
        gear_lbl = QLabel("⚙")
        gear_lbl.setFont(QFont("Segoe UI", 12))
        gear_lbl.setStyleSheet("color: #00e5ff; background: transparent;")
        t_row.addWidget(gear_lbl)

        title_lbl = QLabel("TITAN CONFIGURATION // VANGUARD-CLASS")
        title_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #ffffff; background: transparent; letter-spacing: 1.5px;")
        t_row.addWidget(title_lbl)
        t_row.addStretch()
        hdr_info.addLayout(t_row)

        sub_lbl = QLabel("SRS PROTOCOL LINK // HARDWARE TELEMETRY")
        sub_lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        sub_lbl.setStyleSheet("color: #ffaa00; background: transparent; letter-spacing: 1px;")
        hdr_info.addWidget(sub_lbl)
        hdr.addLayout(hdr_info)

        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton {
                background: #021220; color: #5a8fa8;
                border: 1px solid #144460; border-radius: 4px;
            }
            QPushButton:hover { color: #ff3366; border-color: #ff3366; background: rgba(255, 51, 102, 0.12); }
        """)
        close_btn.clicked.connect(self._cancel)
        hdr.addWidget(close_btn)
        main_lay.addLayout(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #0d3046;")
        main_lay.addWidget(sep)

        # ── Tab Navigation Bar ────────────────────────────────────────────────
        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        self._tab_btns: list[QPushButton] = []
        tabs_def = [
            ("◈ IDENTITY && THEMES", 0),
            ("◈ VOICE && AUDIO", 1),
            ("◈ SENSORS && VISION", 2),
            ("◈ SUBSYSTEMS", 3),
        ]
        for label, idx in tabs_def:
            tb = QPushButton(label)
            tb.setFixedHeight(28)
            tb.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            tb.setCursor(Qt.CursorShape.PointingHandCursor)
            tb.clicked.connect(lambda _=False, i=idx: self._switch_tab(i))
            self._tab_btns.append(tb)
            tab_row.addWidget(tb)
        main_lay.addLayout(tab_row)

        # ── Stacked Tab Container ─────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background: transparent;")
        main_lay.addWidget(self._stack, 1)

        # Build each tab
        self._build_tab_identity(assistant_name, user_name, ui_color)
        self._build_tab_voice(voice)
        self._build_tab_sensors()
        self._build_tab_subsystems()

        self._switch_tab(0)

        # ── Footer Action Bar ─────────────────────────────────────────────────
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background: #0d3046;")
        main_lay.addWidget(sep2)

        bot_row = QHBoxLayout()
        stat_lbl = QLabel("● NEURAL LINK // READY")
        stat_lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        stat_lbl.setStyleSheet("color: #00ff88; background: transparent; letter-spacing: 0.8px;")
        bot_row.addWidget(stat_lbl)
        bot_row.addStretch()

        apply_btn = QPushButton("▸  APPLY CONFIGURATION")
        apply_btn.setFixedSize(175, 32)
        apply_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 229, 255, 0.15); color: #00e5ff;
                border: 1.5px solid #00e5ff; border-radius: 4px;
                letter-spacing: 0.8px;
            }
            QPushButton:hover { background: #00e5ff; color: #000000; }
        """)
        apply_btn.clicked.connect(self._save)
        bot_row.addWidget(apply_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedSize(85, 32)
        cancel_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #021220; color: #5a8fa8;
                border: 1px solid #144460; border-radius: 4px;
            }
            QPushButton:hover { color: #ffffff; border-color: #5a8fa8; }
        """)
        cancel_btn.clicked.connect(self._cancel)
        bot_row.addWidget(cancel_btn)
        main_lay.addLayout(bot_row)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        # High-tech HUD corner brackets
        p.setPen(QPen(QColor("#00e5ff"), 1.8))
        bl = 14
        # TL
        p.drawLine(QPointF(4, 4 + bl), QPointF(4, 4))
        p.drawLine(QPointF(4, 4), QPointF(4 + bl, 4))
        # TR
        p.drawLine(QPointF(W - 4 - bl, 4), QPointF(W - 4, 4))
        p.drawLine(QPointF(W - 4, 4), QPointF(W - 4, 4 + bl))
        # BL
        p.drawLine(QPointF(4, H - 4 - bl), QPointF(4, H - 4))
        p.drawLine(QPointF(4, H - 4), QPointF(4 + bl, H - 4))
        # BR
        p.drawLine(QPointF(W - 4 - bl, H - 4), QPointF(W - 4, H - 4))
        p.drawLine(QPointF(W - 4, H - 4), QPointF(W - 4, H - 4 - bl))
        p.end()

    def _switch_tab(self, idx: int):
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._tab_btns):
            if i == idx:
                btn.setStyleSheet("""
                    QPushButton {
                        background: rgba(0, 229, 255, 0.16); color: #00e5ff;
                        border: 1.5px solid #00e5ff; border-radius: 4px;
                        letter-spacing: 0.5px;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background: #010a12; color: #5a8fa8;
                        border: 1px solid #0e283c; border-radius: 4px;
                        letter-spacing: 0.5px;
                    }
                    QPushButton:hover { color: #00e5ff; border-color: #00e5ff; background: rgba(0, 229, 255, 0.06); }
                """)

    def _lbl(self, txt, fs=8, bold=True, color="#5a8fa8", align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt)
        w.setAlignment(align)
        w.setFont(QFont("Segoe UI", fs, QFont.Weight.Bold if bold else QFont.Weight.DemiBold))
        w.setStyleSheet(f"color: {color}; background: transparent; letter-spacing: 0.5px;")
        return w

    def _input_style(self):
        return ("QLineEdit { background: #010e1a; color: #ffffff; "
                "border: 1px solid #144460; border-radius: 4px; padding: 4px 8px; font-weight: bold; font-family: 'Segoe UI'; font-size: 11px; } "
                "QLineEdit:focus { border: 1.5px solid #00e5ff; background: #021424; }")

    # ── TAB 1: IDENTITY & THEMES ──────────────────────────────────────────────
    def _build_tab_identity(self, assistant_name: str, user_name: str, ui_color: str):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(8)

        # Names row
        names_row = QHBoxLayout()
        names_row.setSpacing(12)

        # Assistant name
        an_box = QVBoxLayout()
        an_box.setSpacing(3)
        an_box.addWidget(self._lbl("TITAN CHASSIS CALL SIGN", 8))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFixedHeight(30)
        self._name_input.setStyleSheet(self._input_style())
        an_box.addWidget(self._name_input)
        names_row.addLayout(an_box)

        # Pilot name
        pn_box = QVBoxLayout()
        pn_box.setSpacing(3)
        pn_box.addWidget(self._lbl("PILOT CALL SIGN", 8))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g. Jack Cooper / Sir")
        self._user_input.setFixedHeight(30)
        self._user_input.setStyleSheet(self._input_style())
        pn_box.addWidget(self._user_input)
        names_row.addLayout(pn_box)
        lay.addLayout(names_row)

        # Preset Themes
        lay.addWidget(self._lbl("HUD THEME OPTICAL PRESETS", 8))
        th_row = QHBoxLayout()
        th_row.setSpacing(6)
        presets = [
            ("🤖 BT-7274 (CYAN)", "#00e5ff"),
            ("🔴 ULTRON (RED)", "#ff1744"),
            ("🟢 MATRIX (GREEN)", "#00ff88"),
            ("🟠 VANGUARD (AMBER)", "#ffaa00"),
        ]
        for p_label, p_hex in presets:
            pb = QPushButton(p_label)
            pb.setFixedHeight(28)
            pb.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            pb.setCursor(Qt.CursorShape.PointingHandCursor)
            pb.setStyleSheet(f"""
                QPushButton {{
                    background: #010e1a; color: {p_hex};
                    border: 1px solid {p_hex}66; border-radius: 4px;
                }}
                QPushButton:hover {{ background: {p_hex}22; border-color: {p_hex}; }}
            """)
            pb.clicked.connect(lambda _=False, hx=p_hex: self._set_color(hx))
            th_row.addWidget(pb)
        lay.addLayout(th_row)

        # Color Wheel & Custom Hex
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(self._lbl("CUSTOM HUD ACCENT OPTICS", 8))
        clr_hdr.addStretch()
        df_btn = QPushButton("RESET DEFAULT")
        df_btn.setFixedSize(92, 20)
        df_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #5a8fa8;
                border: 1px solid #144460; border-radius: 3px;
            }
            QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00e5ff   (custom hex code)")
        self._hex_input.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(self._input_style())
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addStretch()
        self._stack.addWidget(page)

    # ── TAB 2: VOICE & AUDIO ─────────────────────────────────────────────────
    def _build_tab_voice(self, voice: str):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(8)

        from memory.config_manager import (
            AVAILABLE_VOICES, DEFAULT_VOICE, get_tts_engine,
            get_elevenlabs_config, get_fish_audio_config,
        )

        lay.addWidget(self._lbl("SYNTHETIC NEURAL VOICE ENGINE", 8))
        self._sel_engine = get_tts_engine()
        self._engine_btns: dict[str, QPushButton] = {}
        eng_row = QHBoxLayout(); eng_row.setSpacing(6)
        for e_key, e_label in [
            ("gemini_live", "⚡ Gemini Live"),
            ("elevenlabs", "🎙 ElevenLabs"),
            ("fish_audio", "🐟 Fish Audio"),
        ]:
            eb = QPushButton(e_label)
            eb.setFixedHeight(30)
            eb.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            eb.setCursor(Qt.CursorShape.PointingHandCursor)
            eb.clicked.connect(lambda _=False, k=e_key: self._on_engine_pick(k))
            self._engine_btns[e_key] = eb
            eng_row.addWidget(eb)
        lay.addLayout(eng_row)

        # Gemini Voices container
        self._gemini_cont = QWidget()
        self._gemini_cont.setStyleSheet("background: transparent;")
        gem_lay = QVBoxLayout(self._gemini_cont)
        gem_lay.setContentsMargins(0, 4, 0, 4)
        gem_lay.setSpacing(4)
        gem_lay.addWidget(self._lbl("GEMINI LIVE VOICE PERSONALITY", 8))
        self._sel_voice = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        v_row = QHBoxLayout(); v_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            v_row.addWidget(b)
        gem_lay.addLayout(v_row)
        lay.addWidget(self._gemini_cont)

        # ElevenLabs container
        el_cfg = get_elevenlabs_config()
        self._eleven_cont = QWidget()
        self._eleven_cont.setStyleSheet("background: transparent;")
        el_lay = QVBoxLayout(self._eleven_cont)
        el_lay.setContentsMargins(0, 4, 0, 4)
        el_lay.setSpacing(4)
        el_hdr = QHBoxLayout()
        el_hdr.addWidget(self._lbl("ELEVENLABS CREDENTIALS & VOICE ID", 8))
        el_hdr.addStretch()
        el_link = QLabel('<a href="https://elevenlabs.io" style="color: #00e5ff; text-decoration: none; font-weight: bold; font-size: 10px;">Get Key ↗</a>')
        el_link.setOpenExternalLinks(True)
        el_hdr.addWidget(el_link)
        el_lay.addLayout(el_hdr)

        self._eleven_key_input = QLineEdit(el_cfg.get("api_key", ""))
        self._eleven_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._eleven_key_input.setPlaceholderText("ElevenLabs API Key (sk_...)")
        self._eleven_key_input.setFixedHeight(28)
        self._eleven_key_input.setStyleSheet(self._input_style())
        el_lay.addWidget(self._eleven_key_input)

        self._eleven_voice_input = QLineEdit(el_cfg.get("voice_id", ""))
        self._eleven_voice_input.setPlaceholderText("Voice Model ID (e.g. pNInz6obpgDQGcFmaJgB)")
        self._eleven_voice_input.setFixedHeight(28)
        self._eleven_voice_input.setStyleSheet(self._input_style())
        el_lay.addWidget(self._eleven_voice_input)
        lay.addWidget(self._eleven_cont)

        # Fish Audio container
        fish_cfg = get_fish_audio_config()
        self._fish_cont = QWidget()
        self._fish_cont.setStyleSheet("background: transparent;")
        fish_lay = QVBoxLayout(self._fish_cont)
        fish_lay.setContentsMargins(0, 4, 0, 4)
        fish_lay.setSpacing(4)
        fish_hdr = QHBoxLayout()
        fish_hdr.addWidget(self._lbl("FISH AUDIO CREDENTIALS & REFERENCE ID", 8))
        fish_hdr.addStretch()
        fish_link = QLabel('<a href="https://fish.audio" style="color: #00e5ff; text-decoration: none; font-weight: bold; font-size: 10px;">Get Key ↗</a>')
        fish_link.setOpenExternalLinks(True)
        fish_hdr.addWidget(fish_link)
        fish_lay.addLayout(fish_hdr)

        self._fish_key_input = QLineEdit(fish_cfg.get("api_key", ""))
        self._fish_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._fish_key_input.setPlaceholderText("Fish Audio API Key")
        self._fish_key_input.setFixedHeight(28)
        self._fish_key_input.setStyleSheet(self._input_style())
        fish_lay.addWidget(self._fish_key_input)

        self._fish_voice_input = QLineEdit(fish_cfg.get("voice_id", ""))
        self._fish_voice_input.setPlaceholderText("Voice Reference Model ID")
        self._fish_voice_input.setFixedHeight(28)
        self._fish_voice_input.setStyleSheet(self._input_style())
        fish_lay.addWidget(self._fish_voice_input)
        lay.addWidget(self._fish_cont)

        self._refresh_engine_ui()
        self._refresh_voice_btns()

        # Audio Hardware
        lay.addSpacing(6)
        lay.addWidget(self._lbl("HARDWARE AUDIO ROUTING", 8))
        self._audio_hw_btn = QPushButton("🎧  CONFIGURE AUDIO DEVICES (INPUT && OUTPUT)")
        self._audio_hw_btn.setFixedHeight(32)
        self._audio_hw_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._audio_hw_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._audio_hw_btn.setStyleSheet("""
            QPushButton {
                background: #010e1a; color: #00e5ff;
                border: 1px solid #144460; border-radius: 4px; padding: 0 12px;
                text-align: left;
            }
            QPushButton:hover { background: rgba(0, 229, 255, 0.12); border-color: #00e5ff; }
        """)
        self._audio_hw_btn.clicked.connect(self._trigger_audio_devices)
        lay.addWidget(self._audio_hw_btn)

        lay.addStretch()
        self._stack.addWidget(page)

    def _trigger_audio_devices(self):
        if callable(getattr(self, 'on_audio_devices', None)):
            self.on_audio_devices()

    # ── TAB 3: SENSORS & VISION ──────────────────────────────────────────────
    def _build_tab_sensors(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(8)

        # Camera Device selector
        cam_hdr = QHBoxLayout()
        cam_hdr.addWidget(self._lbl("OPTICAL SENSOR HARDWARE (CAMERA DEVICE)", 8))
        cam_hdr.addStretch()
        cam_ref_btn = QPushButton("↻ REFRESH")
        cam_ref_btn.setFixedSize(76, 20)
        cam_ref_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        cam_ref_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cam_ref_btn.setStyleSheet("""
            QPushButton { background: transparent; color: #5a8fa8;
                border: 1px solid #144460; border-radius: 3px; }
            QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }
        """)
        cam_ref_btn.clicked.connect(lambda: self._populate_cam_combo(force=True))
        cam_hdr.addWidget(cam_ref_btn)
        lay.addLayout(cam_hdr)

        self._cam_combo = QComboBox()
        self._cam_combo.setFont(QFont("Segoe UI", 9))
        self._cam_combo.setFixedHeight(30)
        self._cam_combo.setStyleSheet("""
            QComboBox { background: #010e1a; color: #ffffff;
                border: 1px solid #144460; border-radius: 4px; padding: 2px 8px; }
            QComboBox:hover { border-color: #00e5ff; }
            QComboBox QAbstractItemView { background: #010e1a; color: #ffffff;
                selection-background-color: rgba(0, 229, 255, 0.25); border: 1px solid #144460; }
        """)
        lay.addWidget(self._cam_combo)
        self._populate_cam_combo(force=False)

        # Vision toggles row
        lay.addSpacing(4)
        lay.addWidget(self._lbl("VISION & GESTURE TELEMETRY", 8))

        # PiP Feed Toggle
        pip_row = QHBoxLayout()
        pip_row.addWidget(self._lbl("PILOT WEBCAM FEED (TOP-CENTER PIP)", 8, color="#8bb0c0"))
        pip_row.addStretch()
        self._pip_chk = QPushButton("📷  FEED: " + ("ON" if self._pip_enabled else "OFF"))
        self._pip_chk.setFixedHeight(26)
        self._pip_chk.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._pip_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pip_chk.setCheckable(True)
        self._pip_chk.setChecked(self._pip_enabled)
        self._update_toggle_btn(self._pip_chk, self._pip_enabled)
        self._pip_chk.clicked.connect(self._on_pip_clicked)
        pip_row.addWidget(self._pip_chk)
        lay.addLayout(pip_row)

        # Gestures Toggle
        gest_row = QHBoxLayout()
        gest_row.addWidget(self._lbl("VISION HAND GESTURE CONTROL", 8, color="#8bb0c0"))
        gest_row.addStretch()
        self._gesture_chk = QPushButton("🖐  GESTURES: " + ("ON" if self._gestures_enabled else "OFF"))
        self._gesture_chk.setFixedHeight(26)
        self._gesture_chk.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._gesture_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gesture_chk.setCheckable(True)
        self._gesture_chk.setChecked(self._gestures_enabled)
        self._update_toggle_btn(self._gesture_chk, self._gestures_enabled)
        self._gesture_chk.clicked.connect(self._on_gesture_clicked)
        gest_row.addWidget(self._gesture_chk)
        lay.addLayout(gest_row)

        # Audio Sensor Controls
        lay.addSpacing(6)
        lay.addWidget(self._lbl("VOICE LINK & INPUT TRIGGERS", 8))

        # Wake Word Toggle
        wake_row = QHBoxLayout()
        wake_row.addWidget(self._lbl("WAKE WORD DETECTION (OPENWAKEWORD)", 8, color="#8bb0c0"))
        wake_row.addStretch()
        self._wake_chk = QPushButton("🎙  WAKE WORD: " + ("ON" if self._wake_enabled else "OFF"))
        self._wake_chk.setFixedHeight(26)
        self._wake_chk.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._wake_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_chk.setCheckable(True)
        self._wake_chk.setChecked(self._wake_enabled)
        self._update_toggle_btn(self._wake_chk, self._wake_enabled)
        self._wake_chk.clicked.connect(self._on_wake_clicked)
        wake_row.addWidget(self._wake_chk)
        lay.addLayout(wake_row)

        # Push to Talk
        ptt_row = QHBoxLayout()
        ptt_row.addWidget(self._lbl("PUSH-TO-TALK MODE (PTT)", 8, color="#8bb0c0"))
        ptt_row.addStretch()
        self._ptt_chk = QPushButton("🔘  PTT: " + ("ON" if self._ptt_enabled else "OFF"))
        self._ptt_chk.setFixedHeight(26)
        self._ptt_chk.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._ptt_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ptt_chk.setCheckable(True)
        self._ptt_chk.setChecked(self._ptt_enabled)
        self._update_toggle_btn(self._ptt_chk, self._ptt_enabled)
        self._ptt_chk.clicked.connect(self._on_ptt_clicked)
        ptt_row.addWidget(self._ptt_chk)
        lay.addLayout(ptt_row)

        # Morning Brief
        brf_row = QHBoxLayout()
        brf_row.addWidget(self._lbl("MORNING PROTOCOL SITREP BRIEF", 8, color="#8bb0c0"))
        brf_row.addStretch()
        self._brief_chk = QPushButton("☀  BRIEF: " + ("ON" if self._brief_enabled else "OFF"))
        self._brief_chk.setFixedHeight(26)
        self._brief_chk.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._brief_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_chk.setCheckable(True)
        self._brief_chk.setChecked(self._brief_enabled)
        self._update_toggle_btn(self._brief_chk, self._brief_enabled)
        self._brief_chk.clicked.connect(self._on_brief_clicked)
        brf_row.addWidget(self._brief_chk)
        lay.addLayout(brf_row)

        lay.addStretch()
        self._stack.addWidget(page)

    # ── TAB 4: TITAN SUBSYSTEMS ──────────────────────────────────────────────
    def _build_tab_subsystems(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 6, 4, 6)
        lay.setSpacing(8)

        lay.addWidget(self._lbl("NEURAL CORE & MEMORY MATRIX", 8))
        mem_btn = QPushButton("🧠  OPEN TITAN MEMORY DATABASE")
        mem_btn.setFixedHeight(30)
        mem_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet("""
            QPushButton {
                background: #010e1a; color: #00e5ff;
                border: 1px solid #144460; border-radius: 4px; padding: 0 12px;
                text-align: left;
            }
            QPushButton:hover { background: rgba(0, 229, 255, 0.12); border-color: #00e5ff; }
        """)
        mem_btn.clicked.connect(lambda: self.on_memory() if callable(self.on_memory) else None)
        lay.addWidget(mem_btn)

        lay.addSpacing(4)
        lay.addWidget(self._lbl("TITAN MODULAR EXTENSION PLUGINS", 8))
        plg_row = QHBoxLayout()
        plg_row.setSpacing(8)
        plg_btn = QPushButton("🧩  MANAGE PLUGINS")
        plg_btn.setFixedHeight(30)
        plg_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        plg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plg_btn.setStyleSheet("""
            QPushButton {
                background: #010e1a; color: #00e5ff;
                border: 1px solid #144460; border-radius: 4px;
            }
            QPushButton:hover { background: rgba(0, 229, 255, 0.12); border-color: #00e5ff; }
        """)
        plg_btn.clicked.connect(lambda: self.on_plugins() if callable(self.on_plugins) else None)
        plg_row.addWidget(plg_btn)

        plg_set_btn = QPushButton("⚙  PLUGIN SETTINGS")
        plg_set_btn.setFixedHeight(30)
        plg_set_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        plg_set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plg_set_btn.setStyleSheet("""
            QPushButton {
                background: #010e1a; color: #00e5ff;
                border: 1px solid #144460; border-radius: 4px;
            }
            QPushButton:hover { background: rgba(0, 229, 255, 0.12); border-color: #00e5ff; }
        """)
        plg_set_btn.clicked.connect(lambda: self.on_plugin_settings() if callable(self.on_plugin_settings) else None)
        plg_row.addWidget(plg_set_btn)
        lay.addLayout(plg_row)

        lay.addSpacing(4)
        lay.addWidget(self._lbl("REMOTE INTERFACE & DISPLAY", 8))
        rem_btn = QPushButton("◉  TITAN REMOTE CONTROL INTERFACE")
        rem_btn.setFixedHeight(30)
        rem_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        rem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        rem_btn.setStyleSheet("""
            QPushButton {
                background: #010e1a; color: #00e5ff;
                border: 1px solid #144460; border-radius: 4px; padding: 0 12px;
                text-align: left;
            }
            QPushButton:hover { background: rgba(0, 229, 255, 0.12); border-color: #00e5ff; }
        """)
        rem_btn.clicked.connect(lambda: self.on_remote() if callable(self.on_remote) else None)
        lay.addWidget(rem_btn)

        fs_btn = QPushButton("⛶  TOGGLE FULLSCREEN HUD  [F11]")
        fs_btn.setFixedHeight(30)
        fs_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet("""
            QPushButton {
                background: #010e1a; color: #00e5ff;
                border: 1px solid #144460; border-radius: 4px; padding: 0 12px;
                text-align: left;
            }
            QPushButton:hover { background: rgba(0, 229, 255, 0.12); border-color: #00e5ff; }
        """)
        fs_btn.clicked.connect(lambda: self.on_fullscreen() if callable(self.on_fullscreen) else None)
        lay.addWidget(fs_btn)

        lay.addSpacing(4)
        lay.addWidget(self._lbl("SYSTEM INTEGRATION", 8))
        sys_row = QHBoxLayout()
        sys_row.setSpacing(8)
        sc_btn = QPushButton("⊞  DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(30)
        sc_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet("""
            QPushButton {
                background: #010e1a; color: #5a8fa8;
                border: 1px solid #144460; border-radius: 4px;
            }
            QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }
        """)
        sc_btn.clicked.connect(lambda: self.on_shortcut() if callable(self.on_shortcut) else None)
        sys_row.addWidget(sc_btn)

        self._auto_btn = QPushButton("◉  AUTO-START: " + ("ON" if self._autostart_enabled else "OFF"))
        self._auto_btn.setFixedHeight(30)
        self._auto_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._auto_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_toggle_btn(self._auto_btn, self._autostart_enabled)
        self._auto_btn.clicked.connect(self._on_autostart_clicked)
        sys_row.addWidget(self._auto_btn)
        lay.addLayout(sys_row)

        lay.addStretch()
        self._stack.addWidget(page)

    def _update_toggle_btn(self, btn: QPushButton, state: bool):
        if state:
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(0, 255, 136, 0.12); color: #00ff88;
                    border: 1.5px solid #00ff88; border-radius: 4px; padding: 0 10px;
                }
                QPushButton:hover { background: rgba(0, 255, 136, 0.22); }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background: #010e1a; color: #5a8fa8;
                    border: 1px solid #144460; border-radius: 4px; padding: 0 10px;
                }
                QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }
            """)

    def _on_pip_clicked(self):
        self._pip_enabled = self._pip_chk.isChecked()
        self._pip_chk.setText("📷  FEED: " + ("ON" if self._pip_enabled else "OFF"))
        self._update_toggle_btn(self._pip_chk, self._pip_enabled)
        if callable(getattr(self, 'on_pip_toggled', None)):
            self.on_pip_toggled(self._pip_enabled)

    def _on_gesture_clicked(self):
        self._gestures_enabled = self._gesture_chk.isChecked()
        self._gesture_chk.setText("🖐  GESTURES: " + ("ON" if self._gestures_enabled else "OFF"))
        self._update_toggle_btn(self._gesture_chk, self._gestures_enabled)
        if callable(getattr(self, 'on_gesture_toggled', None)):
            self.on_gesture_toggled(self._gestures_enabled)

    def _on_wake_clicked(self):
        self._wake_enabled = self._wake_chk.isChecked()
        self._wake_chk.setText("🎙  WAKE WORD: " + ("ON" if self._wake_enabled else "OFF"))
        self._update_toggle_btn(self._wake_chk, self._wake_enabled)
        if callable(getattr(self, 'on_wake_toggle', None)):
            self.on_wake_toggle()

    def _on_ptt_clicked(self):
        self._ptt_enabled = self._ptt_chk.isChecked()
        self._ptt_chk.setText("🔘  PTT: " + ("ON" if self._ptt_enabled else "OFF"))
        self._update_toggle_btn(self._ptt_chk, self._ptt_enabled)
        if callable(getattr(self, 'on_ptt_toggle', None)):
            self.on_ptt_toggle()

    def _on_brief_clicked(self):
        self._brief_enabled = self._brief_chk.isChecked()
        self._brief_chk.setText("☀  BRIEF: " + ("ON" if self._brief_enabled else "OFF"))
        self._update_toggle_btn(self._brief_chk, self._brief_enabled)
        if callable(getattr(self, 'on_brief_toggle', None)):
            self.on_brief_toggle()

    def _on_autostart_clicked(self):
        self._autostart_enabled = not self._autostart_enabled
        self._auto_btn.setText("◉  AUTO-START: " + ("ON" if self._autostart_enabled else "OFF"))
        self._update_toggle_btn(self._auto_btn, self._autostart_enabled)
        if callable(getattr(self, 'on_autostart', None)):
            self.on_autostart()

    # ── Engine & Voice ────────────────────────────────────────────────────────
    def _on_engine_pick(self, engine_key: str):
        self._sel_engine = engine_key
        self._refresh_engine_ui()

    def _refresh_engine_ui(self):
        for k, btn in self._engine_btns.items():
            on = (k == self._sel_engine)
            if on:
                btn.setStyleSheet("""
                    QPushButton { background: rgba(0, 229, 255, 0.16); color: #00e5ff;
                        border: 1.5px solid #00e5ff; border-radius: 4px; font-weight: bold; }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton { background: #010e1a; color: #5a8fa8;
                        border: 1px solid #144460; border-radius: 4px; }
                    QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }
                """)
        self._gemini_cont.setVisible(self._sel_engine == "gemini_live")
        self._eleven_cont.setVisible(self._sel_engine == "elevenlabs")
        self._fish_cont.setVisible(self._sel_engine == "fish_audio")

    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet("""
                    QPushButton { background: rgba(0, 229, 255, 0.20); color: #00e5ff;
                        border: 1.5px solid #00e5ff; border-radius: 4px; font-weight: bold; }
                """)
            else:
                b.setStyleSheet("""
                    QPushButton { background: #010e1a; color: #5a8fa8;
                        border: 1px solid #144460; border-radius: 4px; }
                    QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }
                """)

    # ── Color Wheel ───────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    # ── Camera Combo ──────────────────────────────────────────────────────────
    def _populate_cam_combo(self, force: bool = False):
        try:
            from core.camera_devices import list_cameras
            from memory.config_manager import get_camera_index
            cur_idx = get_camera_index()
            self._cam_combo.clear()
            cams = list_cameras(force_refresh=force)
            found_match = False
            for c in cams:
                self._cam_combo.addItem(c["name"], c["index"])
                if c["index"] == cur_idx:
                    self._cam_combo.setCurrentIndex(self._cam_combo.count() - 1)
                    found_match = True
            if not found_match and cur_idx is not None:
                self._cam_combo.addItem(f"Camera {cur_idx} (saved)", cur_idx)
                self._cam_combo.setCurrentIndex(self._cam_combo.count() - 1)
        except Exception:
            self._cam_combo.addItem("Default Camera (0)", 0)

    def _cancel(self):
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        try:
            from memory.config_manager import (
                save_tts_engine, save_elevenlabs_config, save_fish_audio_config,
                get_camera_index, save_camera_index,
            )
            name = self._name_input.text().strip() or "BT-7274"
            user = self._user_input.text().strip()

            save_tts_engine(self._sel_engine)
            if self._sel_engine == "elevenlabs":
                save_elevenlabs_config(
                    self._eleven_key_input.text().strip(),
                    self._eleven_voice_input.text().strip(),
                )
            elif self._sel_engine == "fish_audio":
                save_fish_audio_config(
                    self._fish_key_input.text().strip(),
                    self._fish_voice_input.text().strip(),
                )

            cur_cam = self._cam_combo.currentData()
            if cur_cam is not None:
                cur_cam = int(cur_cam)
                if cur_cam != get_camera_index():
                    save_camera_index(cur_cam)
                    if callable(getattr(self, 'on_camera_changed', None)):
                        self.on_camera_changed(cur_cam)

            self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        except Exception as e:
            print(f"Error saving: {e}")
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone BT listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'BT can't hear me' is usually 'BT is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what BT-7274 hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what BT-7274 talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class CameraDeviceOverlay(_HudOverlay):
    """Choose which camera BT-7274 uses for vision, PiP feed, and gesture tracking."""

    picked = pyqtSignal(int)      # emitted after Apply, when camera changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CameraDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("📹  CAMERA DEVICE")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        cap_row = QHBoxLayout()
        cap_lbl = QLabel("PRIMARY WEBCAM — vision, PiP feed, and gesture tracking")
        cap_lbl.setFont(QFont("Courier New", 8))
        cap_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        cap_row.addWidget(cap_lbl)
        cap_row.addStretch()

        ref_btn = QPushButton("↻ REFRESH")
        ref_btn.setFixedSize(68, 20)
        ref_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        ref_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ref_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        ref_btn.clicked.connect(lambda: self._refresh_list(True))
        cap_row.addWidget(ref_btn)
        lay.addLayout(cap_row)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )
        self._cam_box = QComboBox()
        self._cam_box.setFont(QFont("Courier New", 9))
        self._cam_box.setFixedHeight(30)
        self._cam_box.setStyleSheet(_combo_css)
        lay.addWidget(self._cam_box)

        self._refresh_list(False)

        note = QLabel("Applying live-switches the active camera feed and gesture tracker.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _refresh_list(self, force: bool = False):
        from core.camera_devices import list_cameras
        from memory.config_manager import get_camera_index
        cur_idx = get_camera_index()
        self._cam_box.clear()
        cams = list_cameras(force_refresh=force)
        found_match = False
        for c in cams:
            self._cam_box.addItem(c["name"], c["index"])
            if c["index"] == cur_idx:
                self._cam_box.setCurrentIndex(self._cam_box.count() - 1)
                found_match = True
        if not found_match and cur_idx is not None:
            self._cam_box.addItem(f"Camera {cur_idx} (saved)", cur_idx)
            self._cam_box.setCurrentIndex(self._cam_box.count() - 1)

    def _apply(self):
        from memory.config_manager import get_camera_index, save_camera_index
        selected = self._cam_box.currentData()
        if selected is None:
            selected = 0
        selected = int(selected)
        old_idx = get_camera_index()
        save_camera_index(selected)
        self.hide()
        if selected != old_idx:
            self.picked.emit(selected)



class MemoryOverlay(_HudOverlay):
    """Everything BT has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        rows = all_entries_for_ui()

        hdr_row = QHBoxLayout(); hdr_row.setSpacing(6)
        hdr = QLabel("🧠  WHAT BT-7274 REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr_row.addWidget(hdr, 1)

        if rows:
            hdr_wipe = QPushButton("🗑  WIPE")
            hdr_wipe.setFixedSize(72, 22)
            hdr_wipe.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            hdr_wipe.setCursor(Qt.CursorShape.PointingHandCursor)
            hdr_wipe.setToolTip("Clear all stored personal info & memory facts")
            hdr_wipe.setStyleSheet(f"""
                QPushButton {{ background: rgba(255, 23, 68, 0.12); color: {C.RED};
                    border: 1px solid {C.RED}; border-radius: 3px; }}
                QPushButton:hover {{ background: rgba(255, 23, 68, 0.28); color: {C.WHITE}; }}
            """)
            hdr_wipe.clicked.connect(self._wipe_all)
            hdr_row.addWidget(hdr_wipe)

        self._lay.addLayout(hdr_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if rows:
            wipe = QPushButton("🗑  WIPE ALL MEMORY")
            wipe.setFixedHeight(30)
            wipe.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            wipe.setCursor(Qt.CursorShape.PointingHandCursor)
            wipe.setToolTip("Clear all stored personal info & memory facts")
            wipe.setStyleSheet(f"""
                QPushButton {{ background: rgba(255, 23, 68, 0.12); color: {C.RED};
                    border: 1px solid {C.RED}; border-radius: 3px; }}
                QPushButton:hover {{ background: rgba(255, 23, 68, 0.28); color: {C.WHITE}; }}
            """)
            wipe.clicked.connect(self._wipe_all)
            btn_row.addWidget(wipe)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        btn_row.addWidget(close)
        self._lay.addLayout(btn_row)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _wipe_all(self):
        try:
            from memory.memory_manager import save_memory, _empty_memory
            save_memory(_empty_memory())
        except Exception:
            pass
        QTimer.singleShot(0, self._rebuild)

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Disabled: clipboard detection popup removed to keep PROTOCOL lines unobstructed."""

    action_requested = pyqtSignal(str)
    _W, _H = 0, 0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hide()

    def show_clipboard(self, text: str):
        pass


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont("Courier New", 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont("Courier New", 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — BT-7274 ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class FullscreenNewsModal(QWidget):
    """
    Stunning Fullscreen BT Holographic Intelligence Overlay.
    Provides glassmorphic backdrop, glowing neon cybernetic borders, 
    large title, full formatted story text, inside navigation arrows, and close button.
    """
    closed = pyqtSignal()
    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            FullscreenNewsModal {{
                background: rgba(0, 8, 14, 0.96);
                border: 2px solid {C.PRI};
                border-radius: 8px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)

        # Header bar
        hdr = QHBoxLayout()
        icon = QLabel("◈")
        icon.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        icon.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(icon)

        title_badge = QLabel("J.A.R.V.I.S INTELLIGENCE ARCHIVE — FULLSCREEN INTEL")
        title_badge.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        title_badge.setStyleSheet(f"color: {C.PRI}; background: transparent; letter-spacing: 2px;")
        hdr.addWidget(title_badge)
        hdr.addStretch()

        self._counter_lbl = QLabel("[ 1 / 1 ]")
        self._counter_lbl.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        self._counter_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(self._counter_lbl)

        hdr.addSpacing(10)
        close_btn = QPushButton("✕  CLOSE (ESC)")
        close_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 51, 85, 0.15); color: {C.RED};
                border: 1px solid {C.RED}; border-radius: 4px; padding: 4px 10px;
            }}
            QPushButton:hover {{
                background: {C.RED}; color: #ffffff;
            }}
        """)
        close_btn.clicked.connect(self.hide_modal)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        # Main Article Title
        self._title_lbl = QLabel("")
        self._title_lbl.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent; padding: 4px 0;")
        self._title_lbl.setWordWrap(True)
        lay.addWidget(self._title_lbl)

        # Body row with inside navigation arrows
        body_row = QHBoxLayout()
        body_row.setSpacing(10)

        prev_btn = QPushButton("◀\n\nP\nR\nE\nV")
        prev_btn.setFixedWidth(38)
        prev_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        prev_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        prev_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 19, 30, 0.85); color: {C.PRI};
                border: 1px solid {C.BORDER_B}; border-radius: 5px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; color: {C.WHITE}; border-color: {C.PRI};
            }}
        """)
        prev_btn.clicked.connect(self.prev_requested.emit)
        body_row.addWidget(prev_btn)

        self._text_edit = QTextEdit()
        self._text_edit.setReadOnly(True)
        self._text_edit.setFont(QFont("Segoe UI", 11))
        self._text_edit.setStyleSheet(f"""
            QTextEdit {{
                background: rgba(0, 10, 18, 0.80);
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 6px;
                padding: 14px 18px;
                line-height: 1.6;
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 8px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 4px; min-height: 24px;
            }}
        """)
        body_row.addWidget(self._text_edit)

        next_btn = QPushButton("▶\n\nN\nE\nX\nT")
        next_btn.setFixedWidth(38)
        next_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        next_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        next_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 19, 30, 0.85); color: {C.PRI};
                border: 1px solid {C.BORDER_B}; border-radius: 5px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; color: {C.WHITE}; border-color: {C.PRI};
            }}
        """)
        next_btn.clicked.connect(self.next_requested.emit)
        body_row.addWidget(next_btn)

        lay.addLayout(body_row)
        self.hide()

    def show_article(self, title: str, body: str, index: int = 1, total: int = 1):
        self._title_lbl.setText(title)
        self._text_edit.setPlainText(body)
        self._text_edit.moveCursor(self._text_edit.textCursor().MoveOperation.Start)
        self._counter_lbl.setText(f"[ {index} / {total} ]")
        self.show()
        self.raise_()

    def hide_modal(self):
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload
    _gesture_swipe_sig = pyqtSignal(str)       # "left" | "right" | "up" | "down"
    _gesture_zoom_sig  = pyqtSignal(float)     # scale factor from 2-hand gesture
    _gesture_pan_sig   = pyqtSignal(float, float) # (dx, dy) avatar look pan
    _finger_pointer_sig = pyqtSignal(float, float, str) # (norm_x, norm_y, action: "move"|"open_2finger"|"release")
    _news_cmd_sig       = pyqtSignal(str, int)          # ("close"|"open", index)
    _pip_np_frame_sig   = pyqtSignal(object)            # BGR numpy frame for pilot PIP overlay
    _gesture_feedback_sig = pyqtSignal(str)           # thread-safe gesture action HUD banner
    _disable_gestures_sig = pyqtSignal()                # closed fist -> thread-safe gesture disable
    _sleep_sig          = pyqtSignal(bool)              # low-power standby mode toggle (thread-safe)
    _toggle_gestures_sig = pyqtSignal(object)          # thread-safe gesture toggle (bool | None)


    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "BT-7274").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"SRS // {_display} — {APP_PROTOCOL}")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop BT mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by BTLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by BTLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by BTLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self.on_sleep_toggle   = None   # callable: () -> bool
        self.on_wake_clicked   = None   # callable: () -> None
        self.enter_sleep_mode  = None   # callable: (reason, from_voice_tool) -> None
        self.exit_sleep_mode   = None   # callable: (reason) -> None
        self.toggle_sleep_mode = None   # callable: () -> bool
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None

        central = QWidget()
        central.setStyleSheet(f"background: {C.BG};")
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Custom Title Bar
        self._title_bar = CustomTitleBar(title=f"SRS // {_display} — {APP_PROTOCOL}", parent=self)
        root.addWidget(self._title_bar)

        # 2. Header Row
        root.addWidget(self._build_header())

        # 3. Main 3-Column Body
        body = QHBoxLayout()
        body.setContentsMargins(8, 4, 8, 4)
        body.setSpacing(8)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hud.on_card_click = self._on_floating_card_clicked
        self.hud.on_wake_clicked = self._on_hud_wake_clicked
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: {C.BORDER};
                height: 4px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_DIM};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        body.addWidget(self._center_split, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._gesture_swipe_sig.connect(self._on_gesture_swipe)
        self._gesture_zoom_sig.connect(self._on_gesture_zoom)
        self._gesture_pan_sig.connect(self._on_gesture_pan)
        self._finger_pointer_sig.connect(self.hud.on_camera_finger_pointer)
        self._disable_gestures_sig.connect(lambda: self._toggle_gestures(False))
        self._toggle_gestures_sig.connect(self._toggle_gestures)
        self._sleep_sig.connect(self.set_sleep_mode)
        self._gesture_tracker = None
        self._cam_stop = threading.Event()

        self._fullscreen_news_modal = FullscreenNewsModal(self.centralWidget())
        self._fullscreen_news_modal.prev_requested.connect(self._prev_fullscreen_news)
        self._fullscreen_news_modal.next_requested.connect(self._next_fullscreen_news)
        self.hud.on_card_open_fullscreen = self._open_fullscreen_news
        self._news_cmd_sig.connect(self._handle_news_cmd)

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Pilot live webcam PIP overlay (floats on top of all windows across desktop)
        self._pip_overlay = WebcamPIPOverlay(None)
        self._pip_overlay.on_closed = lambda: self._toggle_webcam_pip(False)
        self._pip_np_frame_sig.connect(self._on_pip_np_frame)
        self._gesture_feedback_sig.connect(self._on_gesture_feedback)

        # Clipboard popup disabled completely — keeps PROTOCOL 3 lines clean & unobstructed
        self._clipboard_panel = None

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._on_escape_pressed)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )
            if hasattr(self, '_pip_overlay') and self._pip_overlay.isVisible():
                self._pip_overlay.update_pixmap(px)

    def _on_pip_np_frame(self, frame_bgr) -> None:
        if hasattr(self, '_pip_overlay') and self._pip_overlay.isVisible():
            self._pip_overlay.update_frame(frame_bgr)

    def _on_gesture_feedback(self, text: str) -> None:
        self.hud.show_gesture_feedback(text)
        if hasattr(self, '_pip_overlay') and self._pip_overlay.isVisible():
            self._pip_overlay.show_feedback(text)

    def start_camera_stream(self, for_pip_only: bool = False) -> None:
        self._cam_stop.clear()
        self._cam_for_pip_only = for_pip_only
        if not for_pip_only:
            self._cam_stream_sig.emit(True)
        self._cam_thread = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        self._cam_thread.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            from memory.config_manager import get_camera_index
            cam_idx = get_camera_index()
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            reading_vcam = False
            if not cap.isOpened() and cam_idx != 0:
                cap = cv2.VideoCapture(0)
            if not cap.isOpened() and os.path.exists("/dev/video10"):
                cap = cv2.VideoCapture("/dev/video10")
                if cap.isOpened():
                    reading_vcam = True
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()

            vcam_fd = None
            if os.path.exists("/dev/video10") and not reading_vcam:
                try:
                    vcam_fd = os.open("/dev/video10", os.O_WRONLY | os.O_NONBLOCK)
                except Exception:
                    vcam_fd = None

            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    if vcam_fd is not None:
                        try:
                            h, w = frame.shape[:2]
                            v_fr = cv2.resize(frame, (640, 480)) if (w, h) != (640, 480) else frame
                            os.write(vcam_fd, cv2.cvtColor(v_fr, cv2.COLOR_BGR2BGRA).tobytes())
                        except Exception:
                            pass
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
            if vcam_fd is not None:
                try:
                    os.close(vcam_fd)
                except Exception:
                    pass
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            if not getattr(self, '_cam_for_pip_only', False):
                self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()
        ct = getattr(self, '_cam_thread', None)
        if ct and ct.is_alive() and ct != threading.current_thread():
            try:
                ct.join(timeout=0.35)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_bt_icon(out_path: Path) -> bool:
        """
        Render a BT titan core icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "bt-7274.ico"
        if not ico_path.exists():
            self._build_bt_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                is_frozen = getattr(sys, "frozen", False)
                if is_frozen:
                    target   = str(Path(sys.executable))
                    args     = ""
                    work_dir = str(Path(sys.executable).parent)
                else:
                    pythonw  = python.parent / "pythonw.exe"
                    target   = str(pythonw if pythonw.exists() else python)
                    args     = str(script)
                    work_dir = str(script.parent)
                lnk      = str(desktop / "BT-7274.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, args, work_dir, icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "BT-7274.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "BT-7274"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>BT-7274</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.bt.assistant</string>\n'
                    '  <key>CFBundleName</key><string>BT-7274</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "BT-7274.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=BT-7274\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        if hasattr(self, '_fullscreen_news_modal') and self._fullscreen_news_modal.isVisible():
            self._fullscreen_news_modal.setGeometry(
                12, 12,
                cw.width() - 24,
                cw.height() - 24,
            )
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()
        # Pilot live webcam PIP overlay — reposition if open
        if hasattr(self, '_pip_overlay') and self._pip_overlay.isVisible():
            self._reposition_pip()

    def closeEvent(self, event):
        if self._gesture_tracker:
            try:
                self._gesture_tracker.stop()
            except Exception:
                pass
            self._gesture_tracker = None
        self.stop_camera_stream()
        super().closeEvent(event)


    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        if hasattr(self, 'sys_card'):
            self.sys_card.cpu_row.set_value(cpu, f"{cpu:.0f}%")
        elif hasattr(self, '_bar_cpu'):
            self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        if hasattr(self, 'sys_card'):
            self.sys_card.ram_row.set_value(mem, f"{mem:.0f}%")
        elif hasattr(self, '_bar_mem'):
            self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        if hasattr(self, 'sys_card'):
            self.sys_card.sparkline.add_point(net * 100.0)
            self.sys_card.net_val_lbl.setText(net_str)
        elif hasattr(self, '_bar_net'):
            self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        gpu_str = f"{gpu:.0f}%" if gpu >= 0 else "0%"
        if hasattr(self, 'sys_card'):
            self.sys_card.gpu_row.set_value(gpu if gpu >= 0 else 0, gpu_str)
        elif hasattr(self, '_bar_gpu'):
            self._bar_gpu.set_value(gpu if gpu >= 0 else 0, gpu_str)

        # TMP
        tmp = snap["tmp"]
        tmp_pct = min(100, (tmp / 100) * 100) if tmp >= 0 else 0
        tmp_str = f"{tmp:.0f}°C" if tmp >= 0 else "N/A"
        if hasattr(self, 'sys_card'):
            self.sys_card.temp_row.set_value(tmp if tmp >= 0 else 0, tmp_str)
        elif hasattr(self, '_bar_tmp'):
            self._bar_tmp.set_value(tmp_pct, tmp_str)

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            if hasattr(self, 'sys_card'):
                self.sys_card.uptime_clock.setText(f"{h:02d}:{m:02d}")
            elif hasattr(self, '_uptime_lbl'):
                self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            if hasattr(self, 'sys_card'):
                self.sys_card.uptime_clock.setText("--:--")
            elif hasattr(self, '_uptime_lbl'):
                self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            if hasattr(self, 'sys_card') and hasattr(self.sys_card, 'ping_lbl'):
                self.sys_card.ping_lbl.setText(f"PING  {proc_count}")
            elif hasattr(self, '_proc_lbl'):
                self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            pass


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(48)
        w.setStyleSheet("background: #010810; border-bottom: 1px solid #0c2434;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(10)

        # 1. ● PROTOCOL 3 // ONLINE
        self._prot_btn = QFrame()
        self._prot_btn.setFixedHeight(28)
        self._prot_btn.setStyleSheet("""
            QFrame {
                background: rgba(0, 229, 255, 0.08);
                border: 1.5px solid #00e5ff;
                border-radius: 14px;
            }
        """)
        pb_lay = QHBoxLayout(self._prot_btn)
        pb_lay.setContentsMargins(12, 0, 14, 0)
        pb_lay.setSpacing(6)
        p_dot = QLabel("●")
        p_dot.setFont(QFont("Segoe UI", 8))
        p_dot.setStyleSheet("color: #00ff88; background: transparent; border: none;")
        pb_lay.addWidget(p_dot)
        p_txt = QLabel("PROTOCOL 3 // ONLINE")
        p_txt.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        p_txt.setStyleSheet("color: #00e5ff; background: transparent; border: none; letter-spacing: 1px;")
        pb_lay.addWidget(p_txt)
        lay.addWidget(self._prot_btn)

        # 2. 🖐 GESTURES: OFF
        self._gesture_btn = QPushButton("🖐  GESTURES: OFF")
        self._gesture_btn.setFixedHeight(28)
        self._gesture_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._gesture_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gesture_btn.setToolTip(
            "BT-7274 Optical Hand Gestures:\n"
            "• Fist -> Reopen Hand: Show Desktop (Win+D)\n"
            "• Hold Fist (1.8s): Turn Gestures Off\n"
            "• Thumbs Up: Play / Pause Media\n"
            "• Pinky / 4-Fingers: Switch Tabs (Ctrl+Tab)\n"
            "• 2-Finger Move: Vertical Mouse Scroll Up / Down\n"
            "• 1-Finger Index: Smooth Pointer Control\n"
            "• Pinch: Click / Drag\n"
            "• 3-Finger Swipe: Switch Windows (Alt+Tab)\n"
            "• Shaka: System Volume Up / Down"
        )
        self._gesture_btn.setStyleSheet("""
            QPushButton {
                background: #020d18; color: #6ba3b8;
                border: 1px solid #144460; border-radius: 14px; padding: 0 12px;
                letter-spacing: 1px;
            }
            QPushButton:hover { color: #00e5ff; border-color: #00e5ff; }
        """)
        self._gesture_btn.setCheckable(True)
        self._gesture_btn.clicked.connect(self._toggle_gestures)
        lay.addWidget(self._gesture_btn)

        # 2B. 🎯 GESTURE PROFILE TOGGLE (BALANCED / SNAP / PRECISION)
        self._gesture_prof_btn = QPushButton("🎯  BALANCED")
        self._gesture_prof_btn.setFixedHeight(28)
        self._gesture_prof_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._gesture_prof_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gesture_prof_btn.setToolTip("Switch Gesture Profile: Balanced ➔ Snap/Flick ➔ Precision")
        self._gesture_prof_btn.setStyleSheet("""
            QPushButton {
                background: #020d18; color: #00e5ff;
                border: 1px solid #144460; border-radius: 14px; padding: 0 10px;
                letter-spacing: 1px;
            }
            QPushButton:hover { background: rgba(0, 229, 255, 0.12); border-color: #00e5ff; }
        """)
        self._gesture_prof_btn.clicked.connect(self.cycle_gesture_profile)
        lay.addWidget(self._gesture_prof_btn)

        # 2C. ◈ CONTEXT TAG PILL (TAB / MEDIA / WIN / DESKTOP)
        self._context_tag_lbl = QLabel("◈ CTX: DESKTOP")
        self._context_tag_lbl.setFixedHeight(28)
        self._context_tag_lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._context_tag_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._context_tag_lbl.setStyleSheet("""
            QLabel {
                background: rgba(0, 229, 255, 0.06);
                color: #5a8fa8;
                border: 1px solid #0c2434;
                border-radius: 14px;
                padding: 0 10px;
                letter-spacing: 1px;
            }
        """)
        lay.addWidget(self._context_tag_lbl)

        # 3. ⚡ STANDBY
        self._standby_btn = QPushButton("⚡  STANDBY")
        self._standby_btn.setFixedHeight(28)
        self._standby_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._standby_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._standby_btn.setToolTip("Toggle Low-Power Sleep / Standby Mode")
        self._standby_btn.setStyleSheet("""
            QPushButton {
                background: #020d18; color: #6ba3b8;
                border: 1px solid #144460; border-radius: 14px; padding: 0 12px;
                letter-spacing: 1px;
            }
            QPushButton:hover { color: #ffaa00; border-color: #ffaa00; }
            QPushButton:checked {
                background: #281704; color: #ffaa00;
                border: 1.5px solid #ffaa00;
            }
        """)
        self._standby_btn.setCheckable(True)
        self._standby_btn.clicked.connect(self._toggle_standby_btn)
        lay.addWidget(self._standby_btn)

        # 4. ⚙ SETTINGS
        self._settings_btn = QPushButton("⚙  SETTINGS")
        self._settings_btn.setFixedHeight(28)
        self._settings_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.setToolTip("Configure Vanguard Titan Subsystems & Hardware")
        self._settings_btn.setStyleSheet("""
            QPushButton {
                background: #020d18; color: #6ba3b8;
                border: 1px solid #144460; border-radius: 14px; padding: 0 12px;
                letter-spacing: 1px;
            }
            QPushButton:hover { color: #00e5ff; border-color: #00e5ff; background: rgba(0, 229, 255, 0.10); }
        """)
        self._settings_btn.clicked.connect(self._open_customize)
        lay.addWidget(self._settings_btn)

        lay.addStretch()

        # Center: Title
        mid = QVBoxLayout()
        mid.setSpacing(0)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setFont(QFont("Segoe UI", 18, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet("color: #ffffff; background: transparent; letter-spacing: 2px;")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mid.addWidget(self._title_lbl)

        self._sub_lbl = QLabel("VANGUARD-CLASS TITAN // NEURAL LINK")
        self._sub_lbl.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._sub_lbl.setStyleSheet("color: #ffaa00; background: transparent; letter-spacing: 1px;")
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)

        lay.addStretch()

        # Right: Digital Clock & Date
        right_col = QVBoxLayout()
        right_col.setSpacing(0)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet("color: #00e5ff; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)

        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Segoe UI", 7))
        self._date_lbl.setStyleSheet("color: #5a8fa8; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)

        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%I:%M:%S %p"))
        self._date_lbl.setText(time.strftime("%a, %d %b %Y"))
        if hasattr(self, "_context_tag_lbl"):
            try:
                from core.context_router import get_context_tag
                tag = get_context_tag()
                self._context_tag_lbl.setText(f"◈ CTX: {tag}")
                if tag == "MEDIA":
                    self._context_tag_lbl.setStyleSheet("""
                        QLabel {
                            background: rgba(255, 170, 0, 0.12);
                            color: #ffaa00;
                            border: 1px solid #ffaa00;
                            border-radius: 14px;
                            padding: 0 10px;
                            letter-spacing: 1px;
                        }
                    """)
                elif tag == "TAB":
                    self._context_tag_lbl.setStyleSheet("""
                        QLabel {
                            background: rgba(0, 229, 255, 0.12);
                            color: #00e5ff;
                            border: 1px solid #00e5ff;
                            border-radius: 14px;
                            padding: 0 10px;
                            letter-spacing: 1px;
                        }
                    """)
                else:
                    self._context_tag_lbl.setStyleSheet("""
                        QLabel {
                            background: rgba(0, 229, 255, 0.06);
                            color: #5a8fa8;
                            border: 1px solid #0c2434;
                            border-radius: 14px;
                            padding: 0 10px;
                            letter-spacing: 1px;
                        }
                    """)
            except Exception:
                pass

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet("background: transparent; border: none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.sys_card = SysMonitorCard()
        lay.addWidget(self.sys_card)

        self.status_card = StatusStackCard()
        lay.addWidget(self.status_card)
        lay.addStretch()

        # Backward compatibility aliases
        self._bar_cpu = self.sys_card.cpu_row
        self._bar_mem = self.sys_card.ram_row
        self._bar_net = self.sys_card.sparkline
        self._bar_gpu = self.sys_card.gpu_row
        self._bar_tmp = self.sys_card.temp_row
        self._uptime_lbl = self.sys_card.uptime_clock
        self._proc_lbl = self.sys_card.ping_lbl

        return w
    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet("background: transparent; border: none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # Card 1: Activity Log
        log_card = QWidget()
        log_card.setObjectName("LogCard")
        log_card.setStyleSheet("""
            QWidget#LogCard {
                background: #020d18;
                border: 1.5px solid #144460;
                border-radius: 8px;
            }
        """)
        lc_lay = QVBoxLayout(log_card)
        lc_lay.setContentsMargins(10, 8, 10, 8)
        lc_lay.setSpacing(6)

        l_hdr = QHBoxLayout()
        l_ic = MetricIcon("LOG", has_box=False)
        l_hdr.addWidget(l_ic)
        l_title = QLabel("ACTIVITY LOG")
        l_title.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        l_title.setStyleSheet("color: #00e5ff; background: transparent; letter-spacing: 1px;")
        l_hdr.addWidget(l_title)
        l_hdr.addStretch()

        self._clear_btn = QPushButton("CLEAR")
        self._clear_btn.setFixedHeight(18)
        self._clear_btn.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent; color: #00e5ff;
                border: 1px solid #144460; border-radius: 3px; padding: 0 8px;
            }
            QPushButton:hover { background: #082030; border-color: #00e5ff; }
        """)
        l_hdr.addWidget(self._clear_btn)
        lc_lay.addLayout(l_hdr)

        self._log = LogWidget()
        self._log.setStyleSheet("""
            QTextEdit {
                background: #01070e; color: #00e5ff;
                border: 1px solid #10344c; border-radius: 4px;
                font-family: 'Courier New'; font-size: 8pt;
                padding: 4px;
            }
            QScrollBar:vertical {
                background: #01070e; width: 6px; border: none;
            }
            QScrollBar::handle:vertical {
                background: #144460; border-radius: 3px; min-height: 20px;
            }
        """)
        self._log.setPlainText(f"[{time.strftime('%I:%M:%S')}] SYS: BT-7274 NEURAL LINK ESTABLISHED.")
        self._clear_btn.clicked.connect(self._log.clear)
        lc_lay.addWidget(self._log)
        lay.addWidget(log_card, stretch=1)

        # Card 2: File Upload
        file_card = QWidget()
        file_card.setObjectName("FileCard")
        file_card.setStyleSheet("""
            QWidget#FileCard {
                background: #020d18;
                border: 1.5px solid #144460;
                border-radius: 8px;
            }
        """)
        fc_lay = QVBoxLayout(file_card)
        fc_lay.setContentsMargins(10, 8, 10, 8)
        fc_lay.setSpacing(5)

        f_hdr = QHBoxLayout()
        f_ic = MetricIcon("CLOUD_UPLOAD", has_box=False)
        f_hdr.addWidget(f_ic)
        f_title = QLabel("FILE UPLOAD")
        f_title.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        f_title.setStyleSheet("color: #00e5ff; background: transparent; letter-spacing: 1px;")
        f_hdr.addWidget(f_title)
        f_hdr.addStretch()
        fc_lay.addLayout(f_hdr)

        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        fc_lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("- NO FILE LOADED")
        self._file_hint.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
        self._file_hint.setStyleSheet("color: #4a788c; background: transparent;")
        fc_lay.addWidget(self._file_hint)
        lay.addWidget(file_card)

        # Card 3: Command Input
        cmd_card = QWidget()
        cmd_card.setObjectName("CmdCard")
        cmd_card.setStyleSheet("""
            QWidget#CmdCard {
                background: #020d18;
                border: 1.5px solid #144460;
                border-radius: 8px;
            }
        """)
        cc_lay = QVBoxLayout(cmd_card)
        cc_lay.setContentsMargins(10, 8, 10, 8)
        cc_lay.setSpacing(6)

        c_hdr = QHBoxLayout()
        c_ic = MetricIcon("COMMAND_INPUT", has_box=False)
        c_hdr.addWidget(c_ic)
        c_title = QLabel("COMMAND INPUT")
        c_title.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        c_title.setStyleSheet("color: #00e5ff; background: transparent; letter-spacing: 1px;")
        c_hdr.addWidget(c_title)
        c_hdr.addStretch()
        cc_lay.addLayout(c_hdr)

        # Input row
        inp_row = QHBoxLayout()
        inp_row.setSpacing(4)
        self._input = QLineEdit()
        self._input.setFixedHeight(28)
        self._input.setPlaceholderText("Type a command or question...")
        self._input.setFont(QFont("Segoe UI", 8))
        self._input.setStyleSheet("""
            QLineEdit {
                background: #010a14; color: #e0f7fc;
                border: 1px solid #144460; border-radius: 4px;
                padding: 2px 8px;
            }
            QLineEdit:focus { border: 1px solid #00e5ff; }
        """)
        self._input.returnPressed.connect(self._send)
        inp_row.addWidget(self._input)

        self._send_btn = QPushButton("»")
        self._send_btn.setFixedSize(28, 28)
        self._send_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send_btn.setStyleSheet("""
            QPushButton {
                background: #00e5ff; color: #010a14;
                border: none; border-radius: 4px; font-weight: bold;
            }
            QPushButton:hover { background: #66f2ff; }
        """)
        self._send_btn.clicked.connect(self._send)
        inp_row.addWidget(self._send_btn)
        cc_lay.addLayout(inp_row)

        self._interrupt_btn = QPushButton("■  INTERRUPT (ESC)")
        self._interrupt_btn.setFixedHeight(28)
        self._interrupt_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._interrupt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._interrupt_btn.setStyleSheet("""
            QPushButton {
                background: rgba(255, 23, 68, 0.12); color: #ff4d6d;
                border: 1.5px solid #ff1744; border-radius: 4px; letter-spacing: 1px;
            }
            QPushButton:hover { background: rgba(255, 23, 68, 0.25); color: #ffffff; }
        """)
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        cc_lay.addWidget(self._interrupt_btn)

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(28)
        self._mute_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.setStyleSheet("""
            QPushButton {
                background: rgba(0, 255, 136, 0.12); color: #00ff88;
                border: 1.5px solid #00ff88; border-radius: 4px; letter-spacing: 1px;
            }
            QPushButton:hover { background: rgba(0, 255, 136, 0.25); }
        """)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        cc_lay.addWidget(self._mute_btn)

        lay.addWidget(cmd_card)
        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        fs_btn = QPushButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont("Courier New", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        self._pip_drawer_btn = QPushButton("📷  WEBCAM FEED: OFF")
        self._pip_drawer_btn.setFixedHeight(26)
        self._pip_drawer_btn.setFont(QFont("Courier New", 7))
        self._pip_drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pip_drawer_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._pip_drawer_btn.clicked.connect(lambda: self._toggle_webcam_pip())
        lay.addWidget(self._pip_drawer_btn)

        self._gesture_drawer_btn = QPushButton("🖐  GESTURES: OFF")
        self._gesture_drawer_btn.setFixedHeight(26)
        self._gesture_drawer_btn.setFont(QFont("Courier New", 7))
        self._gesture_drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gesture_drawer_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._gesture_drawer_btn.clicked.connect(lambda: self._toggle_gestures())
        lay.addWidget(self._gesture_drawer_btn)

        self._brief_btn = QPushButton()
        self._brief_btn.setFixedHeight(26)
        self._brief_btn.setFont(QFont("Courier New", 7))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        # ── Wake word ──────────────────────────────────────────────────────────
        self._wake_btn = QPushButton()
        self._wake_btn.setFixedHeight(26)
        self._wake_btn.setFont(QFont("Courier New", 7))
        self._wake_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        lay.addWidget(self._wake_btn)

        self._wake_sleep_btn = QPushButton()
        self._wake_sleep_btn.setFixedHeight(26)
        self._wake_sleep_btn.setFont(QFont("Courier New", 7))
        self._wake_sleep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        lay.addWidget(self._wake_sleep_btn)
        # Neutral placeholder now; the real state (which may load the model to
        # check readiness) is resolved lazily the first time the drawer opens.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = QPushButton()
        self._ptt_btn.setFixedHeight(26)
        self._ptt_btn.setFont(QFont("Courier New", 7))
        self._ptt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        lay.addWidget(self._ptt_btn)

        self._refresh_talk_btns()

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        cam_dev_btn = QPushButton("📹  CAMERA DEVICE")
        cam_dev_btn.setFixedHeight(26)
        cam_dev_btn.setFont(QFont("Courier New", 7))
        cam_dev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cam_dev_btn.setStyleSheet(_BTN_STYLE_DIM)
        cam_dev_btn.clicked.connect(self._open_camera_devices)
        lay.addWidget(cam_dev_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        w.adjustSize()
        return w

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._refresh_wake_btns()   # resolve wake state on open (lazy)
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _position_quick_drawer(self):
        if not hasattr(self, '_quick_drawer'):
            return
        _W = 220
        self._quick_drawer.setFixedWidth(_W)
        self._quick_drawer.adjustSize()
        self._quick_drawer.setGeometry(12, 54, _W, self._quick_drawer.sizeHint().height())

    def _update_gesture_btn_ui(self, active: bool):
        if hasattr(self, '_gesture_btn'):
            self._gesture_btn.blockSignals(True)
            self._gesture_btn.setChecked(active)
            self._gesture_btn.setText("◈ GESTURES: ON" if active else "◈ GESTURES: OFF")
            self._gesture_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {'#002b3d' if active else C.PANEL2};
                    color: {C.PRI if active else C.TEXT_MED};
                    border: 1px solid {C.PRI if active else C.BORDER_B};
                    border-radius: 2px; padding: 0 10px;
                    letter-spacing: 1px; font-weight: bold;
                }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
            """)
            self._gesture_btn.blockSignals(False)

        if hasattr(self, '_gesture_drawer_btn'):
            self._gesture_drawer_btn.setText("🖐  GESTURES: " + ("ON" if active else "OFF"))
            self._gesture_drawer_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI_GHO if active else 'transparent'};
                    color: {C.PRI if active else C.TEXT_MED};
                    border: 1px solid {C.PRI if active else C.BORDER};
                    border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
            """)

        if hasattr(self, '_customize_overlay') and self._customize_overlay and self._customize_overlay.isVisible():
            if hasattr(self._customize_overlay, '_gesture_chk'):
                self._customize_overlay._gesture_chk.blockSignals(True)
                self._customize_overlay._gesture_chk.setChecked(active)
                self._customize_overlay._gesture_chk.setText("🖐  GESTURES: " + ("ON" if active else "OFF"))
                self._customize_overlay._gesture_chk.setStyleSheet(f"""
                    QPushButton {{
                        background: {C.PRI_GHO if active else 'transparent'};
                        color: {C.PRI if active else C.TEXT_MED};
                        border: 1px solid {C.PRI if active else C.BORDER};
                        border-radius: 4px; padding: 0 10px;
                    }}
                """)
                self._customize_overlay._gesture_chk.blockSignals(False)

    def _toggle_gestures(self, checked: bool = None):
        if checked is None:
            is_active = bool(self._gesture_tracker and self._gesture_tracker.is_running)
            checked = not is_active
        if checked:
            try:
                from core.gesture_tracker import GestureTracker as _GT
            except Exception:
                _GT = GestureTracker

            if _GT is None:
                self._log.append_log("ERR: Gesture Tracker not available (opencv/mediapipe missing).")
                self._update_gesture_btn_ui(False)
                return
            from memory.config_manager import get_camera_index
            cam_idx = get_camera_index()
            
            # Stop standalone pip/camera stream so GestureTracker can grab camera exclusively
            self.stop_camera_stream()

            self._gesture_tracker = _GT(
                camera_index=cam_idx,
                on_feedback=lambda txt: self._gesture_feedback_sig.emit(txt),
                on_swipe=lambda d: self._gesture_swipe_sig.emit(d),
                on_pan=lambda dx, dy: self._gesture_pan_sig.emit(dx, dy),
                on_finger_pointer=lambda x, y, act: self._finger_pointer_sig.emit(x, y, act),
                on_frame=lambda fr: self._pip_np_frame_sig.emit(fr),
                on_log=lambda txt: self._log.append_log(txt),
                on_disable=lambda: self._disable_gestures_sig.emit(),
            )
            if hasattr(self, "_current_gesture_profile"):
                self._gesture_tracker.set_profile(self._current_gesture_profile)
            if self._gesture_tracker.start():
                self.hud.show_gesture_feedback("◈ GESTURE TRACKING ONLINE")
                self._log.append_log("SYS: BT-7274 Gesture Tracking online (Fist->Open Desktop, Pointer, Click, 2F-Scroll/Skip, ThumbsUp-Play/Pause, ClosedFist-Off, 3F-Switch, Shaka-Vol).")
                self._update_gesture_btn_ui(True)
            else:
                self._gesture_tracker = None
                self._log.append_log("ERR: Failed to open camera for gesture tracking.")
                self._update_gesture_btn_ui(False)
        else:
            gt = self._gesture_tracker
            self._gesture_tracker = None
            if gt:
                try:
                    gt.emergency_release()
                except Exception:
                    pass
                threading.Thread(target=gt.stop, daemon=True, name="stop-gesture-tracker").start()
            self.hud.show_gesture_feedback("◈ GESTURE TRACKING OFF")
            self._log.append_log("SYS: Gesture Tracking stopped.")
            self._update_gesture_btn_ui(False)
            if hasattr(self, '_pip_overlay'):
                self._toggle_webcam_pip(False)
            if hasattr(self, '_pip_overlay') and self._pip_overlay.isVisible():
                self.start_camera_stream(for_pip_only=True)

    def _reposition_pip(self, force: bool = False) -> None:
        if not hasattr(self, '_pip_overlay'):
            return
        if getattr(self._pip_overlay, '_user_dragged', False) and not force:
            return
        pw, ph = WebcamPIPOverlay._W, WebcamPIPOverlay._H
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + max(0, (geo.width() - pw) // 2)
            y = geo.y() + 8
        else:
            cw = self.centralWidget()
            x = max(0, ((cw.width() if cw else 1920) - pw) // 2)
            y = 58
        self._pip_overlay.setGeometry(x, y, pw, ph)

    def _toggle_webcam_pip(self, enable: bool = None) -> bool:
        """Toggle or set the top-center PIP pilot webcam feed."""
        if not hasattr(self, '_pip_overlay'):
            return False
        if enable is None:
            enable = not self._pip_overlay.isVisible()

        if enable:
            self._reposition_pip(force=not getattr(self._pip_overlay, '_user_dragged', False))
            self._pip_overlay.show()
            self._pip_overlay.raise_()
            QTimer.singleShot(40, lambda: self._reposition_pip(force=not getattr(self._pip_overlay, '_user_dragged', False)))
            gt_active = bool(self._gesture_tracker is not None)
            if not gt_active and (self._cam_stop.is_set() or not hasattr(self, '_cam_for_pip_only')):
                self.start_camera_stream(for_pip_only=True)
        else:
            self._pip_overlay.hide()
            gt_active = bool(self._gesture_tracker and self._gesture_tracker.is_running)
            if not gt_active:
                self.stop_camera_stream()

        if hasattr(self, '_pip_drawer_btn'):
            self._pip_drawer_btn.setText("📷  WEBCAM FEED: " + ("ON" if enable else "OFF"))
            self._pip_drawer_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI_GHO if enable else 'transparent'};
                    color: {C.PRI if enable else C.TEXT_MED};
                    border: 1px solid {C.PRI if enable else C.BORDER};
                    border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
            """)
        return enable

    def toggle_gestures(self, enable: bool = None) -> bool:
        """Thread-safe toggle or set gesture tracking state from voice/commands/plugins."""
        from PyQt6.QtCore import QThread
        if self.thread() == QThread.currentThread():
            self._toggle_gestures(enable)
        else:
            self._toggle_gestures_sig.emit(enable)
        return bool(enable) if enable is not None else bool(self._gesture_tracker and self._gesture_tracker.is_running)

    def cycle_gesture_profile(self) -> str:
        """Cycle gesture profile across Balanced -> Snap/Flick -> Precision."""
        try:
            from core.gesture_tracker import GESTURE_PROFILES
            keys = list(GESTURE_PROFILES.keys())
        except Exception:
            keys = ["balanced", "snap", "precision"]
        cur = self.get_gesture_profile()
        idx = keys.index(cur) if cur in keys else 0
        next_p = keys[(idx + 1) % len(keys)]
        self.set_gesture_profile(next_p)
        return next_p

    def set_gesture_profile(self, profile: str) -> bool:
        """Set gesture profile on gesture tracker and update HUD button."""
        p = (profile or "").strip().lower()
        self._current_gesture_profile = p
        if hasattr(self, "_gesture_tracker") and self._gesture_tracker:
            try:
                self._gesture_tracker.set_profile(p)
            except Exception:
                pass
        if hasattr(self, "_gesture_prof_btn"):
            p_display = p.upper()
            if p == "snap":
                p_display = "SNAP/FLICK"
            self._gesture_prof_btn.setText(f"🎯  {p_display}")
        self._log.append_log(f"SYS: Gesture sensitivity profile set to {p.upper()}.")
        return True

    def get_gesture_profile(self) -> str:
        """Get active gesture profile."""
        if hasattr(self, "_gesture_tracker") and self._gesture_tracker:
            try:
                return self._gesture_tracker.get_profile()
            except Exception:
                pass
        return getattr(self, "_current_gesture_profile", "balanced")

    def _on_hud_wake_clicked(self):
        if callable(self.on_wake_clicked):
            self.on_wake_clicked()
        elif callable(self.exit_sleep_mode):
            self.exit_sleep_mode(reason="HUD tapped")
        else:
            self.set_sleep_mode(False)

    def _toggle_standby_btn(self):
        if callable(self.on_sleep_toggle):
            self.on_sleep_toggle()
        elif callable(self.toggle_sleep_mode):
            self.toggle_sleep_mode()
        else:
            self.set_sleep_mode(not self.hud.sleeping)

    def set_sleep_mode(self, sleeping: bool):
        sleeping = bool(sleeping)
        self.hud.set_sleep_mode(sleeping)
        if hasattr(self, '_standby_btn'):
            self._standby_btn.setChecked(sleeping)
            self._standby_btn.setText("⚡ ASLEEP" if sleeping else "⚡ STANDBY")
        if sleeping:
            self._metric_tmr.setInterval(10000)
            if getattr(self, '_gesture_tracker', None):
                try:
                    self._gesture_tracker.emergency_release()
                except Exception:
                    pass
                if self._gesture_tracker.is_running:
                    try:
                        self._toggle_gestures(False)
                    except Exception:
                        pass
            if getattr(self, '_pip_overlay', None) and self._pip_overlay.isVisible():
                self._pip_was_open = True
                self._toggle_webcam_pip(False)
            else:
                self._pip_was_open = False
        else:
            self._metric_tmr.setInterval(2000)
            if getattr(self, '_pip_was_open', False):
                self._toggle_webcam_pip(True)
                self._pip_was_open = False

    _THEME_PALETTES = [
        ("#00e5ff", "CYAN BT-7274"),
        ("#ff7b00", "SRS AMBER"),
        ("#ff3355", "CRIMSON PROTOCOL"),
        ("#00ff88", "MATRIX EMERALD"),
        ("#a855f7", "CYBER VIOLET"),
        ("#38bdf8", "TITAN BLUE"),
    ]

    def _on_gesture_swipe(self, direction: str):
        """Hand swipe triggers tactical servo glance and feedback."""
        direction = (direction or "").lower()
        if direction in ("left", "prev"):
            self.hud.glance(-1.0, 0.0, hold=0.6)
            self.hud.show_gesture_feedback("◈ TRACK PREV ⏮")
        elif direction in ("right", "next"):
            self.hud.glance(1.0, 0.0, hold=0.6)
            self.hud.show_gesture_feedback("◈ TRACK NEXT ⏭")
        else:
            self.hud.glance(0.0, -1.0, hold=0.5)
            self.hud.show_gesture_feedback("◈ TACTICAL OPTIC SCAN")
        _play_bt_hologram_sound()

    def _on_gesture_zoom(self, scale: float):
        pass

    def _on_gesture_pan(self, dx: float, dy: float):
        self.hud.glance(dx * 0.85, -dy * 0.85, hold=0.35)

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d14; color: {C.WHITE};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 3px 7px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = QPushButton("▸")
        send.setFixedSize(30, 30)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.setCursor(Qt.CursorShape.PointingHandCursor)
        send.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible holographic glassmorphic news & briefing carousel panel.
        Contains inside-border navigation arrows (◀ / ▶) and prominent close cross (✕).
        """
        w = QWidget()
        w.setObjectName("ContentPanel")
        w.setStyleSheet(f"""
            QWidget#ContentPanel {{
                background: {C.PANEL};
                border-top: 2px solid {C.PRI_DIM};
                border-bottom: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(4)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(8)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("NEWS & BRIEFINGS")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)

        hdr.addStretch()

        # Carousel Page Indicator
        self._carousel_counter_lbl = QLabel("[ 1 / 1 ]")
        self._carousel_counter_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._carousel_counter_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(self._carousel_counter_lbl)

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        # Trash / Clear Info Button
        self._content_trash_btn = QPushButton("🗑")
        self._content_trash_btn.setFont(QFont("Segoe UI Emoji", 9) if _OS == "Windows" else QFont("Segoe UI", 9))
        self._content_trash_btn.setFixedSize(22, 22)
        self._content_trash_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._content_trash_btn.setToolTip("Discard & Clear Info (or Swipe Down)")
        self._content_trash_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; background: rgba(255, 23, 68, 0.15); }}
        """)
        self._content_trash_btn.clicked.connect(self._trash_content_panel)
        hdr.addWidget(self._content_trash_btn)

        # Prominent Close Cross Button
        self._content_close_btn = QPushButton("✕")
        self._content_close_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self._content_close_btn.setFixedSize(22, 22)
        self._content_close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._content_close_btn.setToolTip("Close News Panel (or Swipe Down)")
        self._content_close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; background: rgba(255, 51, 85, 0.15); }}
        """)
        self._content_close_btn.clicked.connect(self._close_content_panel)
        hdr.addWidget(self._content_close_btn)
        lay.addLayout(hdr)

        # ── body row with inside-border navigation arrows ────────────────────
        body_row = QHBoxLayout(); body_row.setSpacing(6)

        # Inside Left Border Arrow (◀ PREV)
        self._nav_left_btn = QPushButton("◀")
        self._nav_left_btn.setFixedWidth(28)
        self._nav_left_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._nav_left_btn.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        self._nav_left_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_left_btn.setToolTip("Previous News (or Swipe Right)")
        self._nav_left_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 13, 20, 0.80); color: {C.PRI};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{
                color: {C.WHITE}; border-color: {C.PRI}; background: {C.PRI_GHO};
            }}
        """)
        self._nav_left_btn.clicked.connect(self._prev_news_item)
        body_row.addWidget(self._nav_left_btn)

        # Middle Text Display Area (Transparent / Glassmorphic)
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 9))
        self._content_display.setMinimumHeight(70)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: {C.DARK};
                color: {C.TEXT};
                border: 1px solid {C.BORDER};
                border-radius: 4px;
                padding: 6px 10px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: {C.BG}; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.BORDER_B}; border-radius: 3px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        body_row.addWidget(self._content_display)

        # Inside Right Border Arrow (NEXT ▶)
        self._nav_right_btn = QPushButton("▶")
        self._nav_right_btn.setFixedWidth(28)
        self._nav_right_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._nav_right_btn.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        self._nav_right_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav_right_btn.setToolTip("Next News (or Swipe Left)")
        self._nav_right_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(0, 13, 20, 0.80); color: {C.PRI};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{
                color: {C.WHITE}; border-color: {C.PRI}; background: {C.PRI_GHO};
            }}
        """)
        self._nav_right_btn.clicked.connect(self._next_news_item)
        body_row.addWidget(self._nav_right_btn)

        lay.addLayout(body_row)
        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and displays news carousel with sci-fi sound."""
        import time as _time
        _play_bt_hologram_sound()
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))

        raw = (text or "").strip()
        items = []
        # Parse by numbered items, bullets, or paragraphs
        parts = re.split(r'\n\s*(?:(?:\d+[\.\)]|\-|\*|•|##+)\s+)', raw)
        if len(parts) > 1:
            for p in parts:
                p_str = p.strip()
                if p_str:
                    items.append(p_str)
        else:
            paragraphs = [p.strip() for p in raw.split("\n\n") if p.strip()]
            if len(paragraphs) > 1:
                items = paragraphs
            else:
                items = [raw] if raw else ["No news items received."]

        self._news_items = items
        self._current_news_idx = 0
        self._update_news_card()

        # Spawn floating rectangular news widgets on left & right of HUD with arrows
        structured_cards = []
        for i, it in enumerate(items[:6]):
            lines = [l.strip(" #*-•") for l in it.strip().splitlines() if l.strip()]
            t = lines[0] if lines else f"News #{i+1}"
            b = it.strip()
            structured_cards.append({"title": t, "body": b})
        self.hud.set_floating_news(structured_cards)

        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        self._content_panel.raise_()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 230, 120), 230])

    def _on_floating_card_clicked(self, card: dict):
        title = card.get("title", "NEWS")
        body = card.get("body", "")
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_display.setPlainText(body)
        self._content_panel.show()
        self._content_panel.raise_()
        self.hud.show_gesture_feedback(f"◈ OPENED: {title[:18]} ↗")
        self.hud.glance(0.0, -0.6, hold=1.0)


    def _update_news_card(self):
        if not hasattr(self, '_news_items') or not self._news_items:
            self._content_display.setPlainText("")
            if hasattr(self, '_carousel_counter_lbl'):
                self._carousel_counter_lbl.setText("[ 0 / 0 ]")
            return
        total = len(self._news_items)
        idx = max(0, min(total - 1, self._current_news_idx))
        self._current_news_idx = idx
        if hasattr(self, '_carousel_counter_lbl'):
            self._carousel_counter_lbl.setText(f"[ {idx + 1} / {total} ]")
        item_text = self._news_items[idx]
        self._content_display.setPlainText(item_text)
        self._content_display.moveCursor(self._content_display.textCursor().MoveOperation.Start)

    def _next_news_item(self):
        if hasattr(self, '_news_items') and len(self._news_items) > 1:
            self._current_news_idx = (self._current_news_idx + 1) % len(self._news_items)
            self._update_news_card()
            _play_bt_hologram_sound()
            self.hud.show_gesture_feedback(f"◈ NEWS [{self._current_news_idx + 1}/{len(self._news_items)}] ▶")
            self.hud.glance(1.0, -0.4, hold=0.5)

    def _prev_news_item(self):
        if hasattr(self, '_news_items') and len(self._news_items) > 1:
            self._current_news_idx = (self._current_news_idx - 1) % len(self._news_items)
            self._update_news_card()
            _play_bt_hologram_sound()
            self.hud.show_gesture_feedback(f"◈ NEWS [{self._current_news_idx + 1}/{len(self._news_items)}] ◀")
            self.hud.glance(-1.0, -0.4, hold=0.5)

    def _on_escape_pressed(self):
        if hasattr(self, '_fullscreen_news_modal') and self._fullscreen_news_modal.isVisible():
            self._fullscreen_news_modal.hide_modal()
            return
        if hasattr(self, '_content_panel') and self._content_panel.isVisible():
            self._close_content_panel()
            return
        self._do_interrupt()

    def _open_fullscreen_news(self, card: dict):
        title = card.get("title", "INTELLIGENCE BRIEFING")
        body = card.get("body", "")
        idx = 1
        total = 1
        if hasattr(self, '_news_items') and self._news_items:
            total = len(self._news_items)
            for i, it in enumerate(self._news_items):
                if title.lower() in it.lower() or it.lower() in title.lower():
                    idx = i + 1
                    break
        cw = self.centralWidget()
        self._fullscreen_news_modal.setGeometry(12, 12, cw.width() - 24, cw.height() - 24)
        self._fullscreen_news_modal.show_article(title, body, idx, total)
        self.hud.glance(0.0, -0.6, hold=1.2)

    def _next_fullscreen_news(self):
        if hasattr(self, '_news_items') and len(self._news_items) > 1:
            self._current_news_idx = (self._current_news_idx + 1) % len(self._news_items)
            self._update_news_card()
            item_text = self._news_items[self._current_news_idx]
            lines = [l.strip(" #*-•") for l in item_text.splitlines() if l.strip()]
            t = lines[0] if lines else f"News #{self._current_news_idx+1}"
            self._fullscreen_news_modal.show_article(t, item_text, self._current_news_idx + 1, len(self._news_items))
            _play_bt_transition_sound()

    def _prev_fullscreen_news(self):
        if hasattr(self, '_news_items') and len(self._news_items) > 1:
            self._current_news_idx = (self._current_news_idx - 1) % len(self._news_items)
            self._update_news_card()
            item_text = self._news_items[self._current_news_idx]
            lines = [l.strip(" #*-•") for l in item_text.splitlines() if l.strip()]
            t = lines[0] if lines else f"News #{self._current_news_idx+1}"
            self._fullscreen_news_modal.show_article(t, item_text, self._current_news_idx + 1, len(self._news_items))
            _play_bt_transition_sound()

    def _handle_news_cmd(self, action: str, index: int = 0):
        if action == "close":
            self.close_all_news()
        elif action == "open":
            self.open_news_fullscreen(index)

    def close_all_news(self):
        self.floating_news = [] if hasattr(self, 'floating_news') else []
        self.hud.floating_news = []
        if hasattr(self, '_fullscreen_news_modal'):
            self._fullscreen_news_modal.hide_modal()
        if hasattr(self, '_content_panel'):
            self._content_panel.hide()
        _play_bt_hologram_sound()
        self.hud.show_gesture_feedback("◈ ALL NEWS CLOSED ✕")
        self.hud.update()

    def open_news_fullscreen(self, index: int = 0) -> bool:
        if not hasattr(self, '_news_items') or not self._news_items:
            return False
        idx = max(0, min(len(self._news_items) - 1, index))
        self._current_news_idx = idx
        item_text = self._news_items[idx]
        lines = [l.strip(" #*-•") for l in item_text.splitlines() if l.strip()]
        title = lines[0] if lines else f"News #{idx+1}"
        cw = self.centralWidget()
        self._fullscreen_news_modal.setGeometry(12, 12, cw.width() - 24, cw.height() - 24)
        self._fullscreen_news_modal.show_article(title, item_text, idx + 1, len(self._news_items))
        _play_bt_transition_sound()
        self.hud.show_gesture_feedback(f"◈ FULLSCREEN: {title[:16]} 📂")
        self.hud.glance(0.0, -0.6, hold=1.2)
        return True

    def _close_content_panel(self):
        if hasattr(self, '_content_panel'):
            self._content_panel.hide()
            self.hud.show_gesture_feedback("◈ NEWS CLOSED ✕")

    def _trash_content_panel(self):
        if hasattr(self, '_content_panel'):
            self._content_panel.hide()
        self._news_items = []
        if hasattr(self, '_content_display'):
            self._content_display.clear()
        if hasattr(self, 'hud') and hasattr(self.hud, 'floating_news'):
            self.hud.floating_news.clear()
            self.hud.update()
        _play_bt_hologram_sound()
        self.hud.show_gesture_feedback("◈ INFO DISCARDED 🗑️")


    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: BT says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message BT reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Courier New", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("QuizPanel")
        w.setStyleSheet(f"""
            QWidget#QuizPanel {{
                background: {C.PANEL};
                border-top: 1px solid {C.BORDER_B};
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 7, 12, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Courier New", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Courier New", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Courier New", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are BT's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to BT as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(30)
        w.setStyleSheet("background: #01070e; border-top: 1px solid #0c2434;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(8)

        # [F1] Help   [F11] Toggle Fullscreen
        f1_lbl = QLabel("F1")
        f1_lbl.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
        f1_lbl.setStyleSheet("background: #031422; color: #00e5ff; border: 1px solid #144460; border-radius: 2px; padding: 1px 3px;")
        lay.addWidget(f1_lbl)

        h_lbl = QLabel("Help")
        h_lbl.setFont(QFont("Segoe UI", 7))
        h_lbl.setStyleSheet("color: #4a788c; background: transparent;")
        lay.addWidget(h_lbl)

        lay.addSpacing(6)

        f11_lbl = QLabel("F11")
        f11_lbl.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
        f11_lbl.setStyleSheet("background: #031422; color: #00e5ff; border: 1px solid #144460; border-radius: 2px; padding: 1px 3px;")
        lay.addWidget(f11_lbl)

        fs_lbl = QLabel("Toggle Fullscreen")
        fs_lbl.setFont(QFont("Segoe UI", 7))
        fs_lbl.setStyleSheet("color: #4a788c; background: transparent;")
        lay.addWidget(fs_lbl)

        lay.addStretch()

        # Center beveled bracket
        self._footer_bracket = FooterBracket()
        lay.addWidget(self._footer_bracket)

        lay.addStretch()

        # Volume / Speaker
        vol_btn = QPushButton("🔊")
        vol_btn.setFixedSize(24, 24)
        vol_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        vol_btn.setStyleSheet("QPushButton { background: transparent; color: #6ba3b8; border: none; font-size: 12px; } QPushButton:hover { color: #00e5ff; }")
        vol_btn.clicked.connect(self._toggle_mute)
        lay.addWidget(vol_btn)

        # Settings
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(24, 24)
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Configuration")
        self._drawer_btn.setStyleSheet("QPushButton { background: transparent; color: #6ba3b8; border: none; font-size: 13px; } QPushButton:hover { color: #00e5ff; }")
        self._drawer_btn.clicked.connect(self._open_customize)
        lay.addWidget(self._drawer_btn)

        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"- {icon} {p.name} ({size})")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "BT_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.bt.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "bt-7274.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    try:
                        winreg.DeleteValue(reg, "BT_AI")
                    except Exception:
                        pass
                else:
                    is_frozen = getattr(sys, "frozen", False)
                    if is_frozen:
                        run_cmd = f'\"{sys.executable}\"'
                    else:
                        pythonw = Path(sys.executable).parent / "pythonw.exe"
                        exe = str(pythonw if pythonw.exists() else sys.executable)
                        run_cmd = f'\"{exe}\" \"{script}\"'
                    winreg.SetValueEx(reg, "BT_AI", 0, winreg.REG_SZ, run_cmd)
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.bt.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.bt.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "bt-7274.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before BTLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    def _refresh_hud_btn(self):
        pass

    def _toggle_hud_style(self):
        pass

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through BTLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        is_gestures = bool(getattr(self, '_gesture_tracker', None) and self._gesture_tracker.is_running)
        wake_on = self._wake_state().get("enabled", False)
        from memory.config_manager import get_push_to_talk_enabled
        ptt_on = get_push_to_talk_enabled()
        auto_on = self._check_autostart()
        brief_on = bool(cfg.get("morning_brief_enabled", False))

        ov = CustomizeOverlay(
            assistant_name=cfg.get("assistant_name", "BT-7274") or "BT-7274",
            user_name=cfg.get("user_name", ""),
            ui_color=cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            voice=cfg.get("voice_name", ""),
            pip_enabled=bool(getattr(self, '_pip_overlay', None) and self._pip_overlay.isVisible()),
            gestures_enabled=is_gestures,
            wake_enabled=wake_on,
            ptt_enabled=ptt_on,
            autostart_enabled=auto_on,
            brief_enabled=brief_on,
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.on_pip_toggled = self._toggle_webcam_pip
        ov.on_gesture_toggled = lambda en: self._toggle_gestures(en)
        ov.on_camera_changed = self._on_camera_device_applied
        ov.on_audio_devices = self._open_audio_devices
        ov.on_memory = self._open_memory_panel
        ov.on_plugins = self._open_plugin_manager
        ov.on_plugin_settings = self._open_plugin_settings
        ov.on_remote = self._open_remote
        ov.on_fullscreen = self._toggle_fullscreen
        ov.on_shortcut = self._create_desktop_shortcut
        ov.on_autostart = self._toggle_autostart
        ov.on_wake_toggle = self._toggle_wake_word
        ov.on_ptt_toggle = self._toggle_ptt
        ov.on_brief_toggle = self._toggle_brief
        ov.saved.connect(self._apply_name_update)
        ov.show()
        ov.raise_()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "BT-7274"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"SRS // {display} — {APP_PROTOCOL}")
        self._title_lbl.setText(display)
        if "BT" in display or "7274" in display:
            self._sub_lbl.setText("VANGUARD-CLASS TITAN // NEURAL LINK")
        elif display in ("BT", "BT-7274"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("VANGUARD-CLASS TITAN // NEURAL LINK")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Camera device ────────────────────────────────────────────────────────

    def _open_camera_devices(self):
        ov = CameraDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_camera_device_applied)
        self._centre_overlay(ov)
        self._camera_overlay = ov

    def _on_camera_device_applied(self, new_idx: int):
        from core.camera_devices import get_camera_name
        name = get_camera_name(new_idx)
        self._log.append_log(f"SYS: Camera device updated to index {new_idx} ({name}).")
        self.hud.show_gesture_feedback(f"◈ CAM: {new_idx}")

        # Restart active camera feed if currently streaming
        if hasattr(self, '_cam_thread') and self._cam_thread and self._cam_thread.is_alive():
            self.stop_camera_stream()
            time.sleep(0.08)
            pip_only = getattr(self, '_cam_for_pip_only', False)
            self.start_camera_stream(for_pip_only=pip_only)

        # Restart gesture tracker if currently running
        if getattr(self, '_gesture_tracker', None) and self._gesture_tracker.is_running:
            self._toggle_gestures(False)
            time.sleep(0.08)
            self._toggle_gestures(True)

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence (Disabled — keeps PROTOCOL 3 clean) ─────────────────

    def _on_clipboard_changed(self):
        pass

    def _show_clipboard_panel(self, text: str):
        pass

    def _position_clipboard_panel(self):
        pass

    def _on_clipboard_action(self, cmd: str):
        pass

    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if getattr(self, "_gesture_tracker", None):
            try:
                self._gesture_tracker.emergency_release()
            except Exception:
                pass
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140800; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 480, 430
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "BT-7274") or "BT-7274"
        self._log.append_log(f"SYS: BT-7274 MARK LV ONLINE. OS={os_name.upper()}.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class BTUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for BT's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        try:
            self._win.notify_phone_connected()
        except Exception:
            pass

    def set_state(self, state: str):
        try:
            self._win._state_sig.emit(state)
        except Exception:
            pass

    def write_log(self, text: str):
        try:
            self._win._log_sig.emit(text)
        except Exception:
            pass

    def wait_for_api_key(self):
        while not getattr(self._win, '_ready', False):
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        try:
            self._win._content_sig.emit(title[:48], text[:4000])
        except Exception:
            pass

    def close_all_news(self) -> None:
        """Thread-safe: close and dismiss all news widgets from screen."""
        try:
            self._win._news_cmd_sig.emit("close", 0)
        except Exception:
            pass

    def open_news_fullscreen(self, index: int = 0) -> None:
        """Thread-safe: open the specified news card in full screen."""
        try:
            self._win._news_cmd_sig.emit("open", int(index))
        except Exception:
            pass

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "BT should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")

    def toggle_webcam_pip(self, enable: bool = None) -> bool:
        """Thread-safe: toggle or set the top-center PIP pilot webcam feed."""
        return self._win._toggle_webcam_pip(enable)

    def toggle_gestures(self, enable: bool = None) -> bool:
        """Thread-safe: toggle or set gesture tracking state."""
        try:
            return self._win.toggle_gestures(enable)
        except Exception:
            return False

    def set_sleep_mode(self, sleeping: bool) -> None:
        """Thread-safe: toggle low-power standby / sleep mode."""
        try:
            self._win._sleep_sig.emit(bool(sleeping))
        except Exception:
            pass

    @property
    def on_sleep_toggle(self):
        return getattr(self._win, "on_sleep_toggle", None)

    @on_sleep_toggle.setter
    def on_sleep_toggle(self, cb):
        self._win.on_sleep_toggle = cb

    @property
    def on_wake_clicked(self):
        return getattr(self._win, "on_wake_clicked", None)

    @on_wake_clicked.setter
    def on_wake_clicked(self, cb):
        self._win.on_wake_clicked = cb

    @property
    def enter_sleep_mode(self):
        return getattr(self._win, "enter_sleep_mode", None)

    @enter_sleep_mode.setter
    def enter_sleep_mode(self, cb):
        self._win.enter_sleep_mode = cb

    @property
    def exit_sleep_mode(self):
        return getattr(self._win, "exit_sleep_mode", None)

    @exit_sleep_mode.setter
    def exit_sleep_mode(self, cb):
        self._win.exit_sleep_mode = cb

    @property
    def toggle_sleep_mode(self):
        return getattr(self._win, "toggle_sleep_mode", None)

    @toggle_sleep_mode.setter
    def toggle_sleep_mode(self, cb):
        self._win.toggle_sleep_mode = cb

    @property
    def is_sleep_mode(self):
        return getattr(self._win, "is_sleep_mode", None)

    @is_sleep_mode.setter
    def is_sleep_mode(self, cb):
        self._win.is_sleep_mode = cb


# Backward-compatible alias
# BTUI is the primary interface


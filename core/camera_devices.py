"""
core/camera_devices.py — Discover, enumerate, and switch video cameras for BT-7274.

WHY
    BT-7274 uses cameras for visual perception (actions/screen_processor.py),
    top-center PiP feed (ui.py WebcamPIPOverlay), and hand gesture tracking (core/gesture_tracker.py).
    When a pilot plugs in an external USB camera or has multiple video inputs,
    they need a reliable way to view, select, and switch between cameras without
    manual configuration file editing or restarting the app.

PLATFORM SUPPORT
    Linux:
        1. Enumerates via v4l2-ctl (querying capabilities to exclude metadata
           nodes and loopback sink devices like /dev/video10).
        2. Fallback to /sys/class/video4linux sysfs inspection.
    Windows & macOS:
        Probes camera indices using OpenCV VideoCapture.
    Cached for fast HUD rendering; refreshable on demand.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import threading
import time
from typing import Any

_cache: list[dict[str, Any]] | None = None
_cache_time: float = 0.0
_cache_lock = threading.Lock()
_CACHE_TTL = 4.0  # seconds


def _get_windows_camera_names() -> list[str]:
    """Query friendly camera device names on Windows via WMI / PowerShell."""
    import sys
    if sys.platform != "win32":
        return []
    # 1. Try wmi package if installed (fast, no subprocess)
    try:
        import wmi  # type: ignore
        c = wmi.WMI()
        names = [
            str(dev.Name).strip()
            for dev in c.Win32_PnPEntity()
            if getattr(dev, "PNPClass", "") in ("Camera", "Image") and getattr(dev, "Name", None)
        ]
        if names:
            return names
    except Exception:
        pass
    # 2. PowerShell CIM fallback (no window flash)
    try:
        ps_cmd = (
            "Get-CimInstance Win32_PnPEntity "
            "| Where-Object { $_.PNPClass -in 'Camera','Image' -and $_.Status -eq 'OK' } "
            "| Select-Object -ExpandProperty Name"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=2.5,
            creationflags=flags,
        )
        if res.returncode == 0 and res.stdout.strip():
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    return []


def _clean_camera_label(raw: str, dev_path: str = "") -> str:
    """Format a clean, readable name from v4l2 or sysfs output."""
    s = re.sub(r"\s*\(usb-[^)]+\)", "", raw).strip()
    if ":" in s:
        parts = [p.strip() for p in s.split(":", 1)]
        p0_low = parts[0].lower()
        p1_low = parts[1].lower()
        if p0_low.startswith(p1_low[:4]) or p1_low.startswith(p0_low[:4]):
            s = parts[0]
        else:
            s = f"{parts[0]} - {parts[1]}"
    if dev_path and not dev_path.isdigit():
        s = f"{s} ({dev_path})"
    return s


def list_cameras(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return a list of available video capture cameras.

    Each entry is a dict:
        {"index": int, "name": str, "path": str}
    """
    global _cache, _cache_time

    now = time.time()
    with _cache_lock:
        if not force_refresh and _cache is not None and (now - _cache_time < _CACHE_TTL):
            return list(_cache)

    cameras: list[dict[str, Any]] = []
    seen_indices: set[int] = set()

    # 1. Linux v4l2-ctl enumeration (most accurate capabilities check)
    if shutil.which("v4l2-ctl"):
        try:
            res = subprocess.run(
                ["v4l2-ctl", "--list-devices"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res.returncode == 0:
                lines = res.stdout.strip().split("\n")
                cur_label = ""
                for line in lines:
                    if not line.startswith("\t") and line.strip():
                        cur_label = line.strip().rstrip(":")
                    elif line.startswith("\t"):
                        dev = line.strip()
                        m = re.search(r"video(\d+)", dev)
                        if m:
                            idx = int(m.group(1))
                            if idx in seen_indices:
                                continue
                            try:
                                cap_res = subprocess.run(
                                    ["v4l2-ctl", f"--device={dev}", "--all"],
                                    capture_output=True,
                                    text=True,
                                    timeout=1.0,
                                )
                                out = cap_res.stdout
                                caps_part = out.split("Device Caps")[1] if "Device Caps" in out else out
                                # Must have Video Capture, not purely metadata or video output
                                if (
                                    "Video Capture" in caps_part
                                    and "Metadata Capture" not in caps_part
                                    and "Video Output" not in caps_part
                                ):
                                    clean_name = _clean_camera_label(cur_label or f"Camera {idx}", dev)
                                    cameras.append({"index": idx, "name": clean_name, "path": dev})
                                    seen_indices.add(idx)
                            except Exception:
                                pass
        except Exception:
            pass

    # 2. Linux /sys/class/video4linux fallback
    if not cameras and os.path.exists("/sys/class/video4linux"):
        for p in sorted(
            glob.glob("/sys/class/video4linux/video*"),
            key=lambda s: int(re.search(r"\d+", s).group() if re.search(r"\d+", s) else 0),
        ):
            try:
                base = os.path.basename(p)
                m = re.search(r"\d+", base)
                if not m:
                    continue
                idx = int(m.group())
                if idx in seen_indices:
                    continue
                name_file = os.path.join(p, "name")
                index_file = os.path.join(p, "index")
                raw_name = (
                    open(name_file, encoding="utf-8").read().strip()
                    if os.path.exists(name_file)
                    else f"Camera {idx}"
                )
                stream_idx = (
                    open(index_file, encoding="utf-8").read().strip()
                    if os.path.exists(index_file)
                    else "0"
                )
                low_name = raw_name.lower()
                # Exclude secondary metadata streams (index != 0) and virtual loopback/broadcast sinks
                if stream_idx == "0" and "loopback" not in low_name and "broadcast" not in low_name:
                    dev_path = f"/dev/{base}"
                    clean_name = _clean_camera_label(raw_name, dev_path)
                    cameras.append({"index": idx, "name": clean_name, "path": dev_path})
                    seen_indices.add(idx)
            except Exception:
                pass

    # 3. Windows / macOS / generic OpenCV probe
    if not cameras:
        try:
            import cv2
            import sys

            if sys.platform == "win32":
                win_names = _get_windows_camera_names()
                backends: list[int] = []
                for attr in ("CAP_DSHOW", "CAP_MSMF", "CAP_ANY"):
                    b = getattr(cv2, attr, None)
                    if b is not None and b not in backends:
                        backends.append(b)
                if not backends:
                    backends = [cv2.CAP_ANY]

                for i in range(6):
                    opened_any = False
                    for b in backends:
                        cap = None
                        try:
                            cap = cv2.VideoCapture(i, b)
                            if cap.isOpened():
                                opened_any = True
                                ret, frame = cap.read()
                                if ret and frame is not None:
                                    label = win_names[i] if i < len(win_names) else f"Camera {i}"
                                    cameras.append({"index": i, "name": label, "path": str(i)})
                                    seen_indices.add(i)
                                    opened_any = False
                                    break
                        except Exception:
                            pass
                        finally:
                            if cap is not None:
                                try:
                                    cap.release()
                                except Exception:
                                    pass
                    if opened_any and i not in seen_indices:
                        label = win_names[i] if i < len(win_names) else f"Camera {i}"
                        cameras.append({"index": i, "name": label, "path": str(i)})
                        seen_indices.add(i)
            else:
                cap_backend = (
                    getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY)
                    if sys.platform == "darwin"
                    else cv2.CAP_ANY
                )
                for i in range(4):
                    cap = cv2.VideoCapture(i, cap_backend)
                    if cap.isOpened():
                        cameras.append({"index": i, "name": f"Camera {i}", "path": str(i)})
                        cap.release()
                        seen_indices.add(i)
        except Exception:
            pass

    # Final fallback if absolutely nothing responds
    if not cameras:
        import sys
        default_label = "Default Camera" if sys.platform in ("darwin", "win32") else "Default Camera (/dev/video0)"
        dev_path = "0" if sys.platform in ("darwin", "win32") else "/dev/video0"
        cameras = [{"index": 0, "name": default_label, "path": dev_path}]

    cameras.sort(key=lambda c: c["index"])

    with _cache_lock:
        _cache = list(cameras)
        _cache_time = now

    return list(cameras)


def get_camera_name(index: int) -> str:
    """Return friendly display name for camera index."""
    cams = list_cameras()
    for c in cams:
        if c["index"] == index:
            return c["name"]
    return f"Camera {index}"


def switch_camera(target_index: int | None = None) -> int:
    """Switch camera to target_index, or cycle to next available camera.

    Persists selection to api_keys.json and returns the chosen index.
    """
    from memory.config_manager import get_camera_index, save_camera_index

    cams = list_cameras(force_refresh=True)
    if not cams:
        return 0

    if target_index is not None:
        save_camera_index(target_index)
        return target_index

    cur_idx = get_camera_index()
    indices = [c["index"] for c in cams]

    if cur_idx in indices:
        next_pos = (indices.index(cur_idx) + 1) % len(indices)
        chosen = indices[next_pos]
    else:
        chosen = indices[0]

    save_camera_index(chosen)
    return chosen


def open_camera(
    index: int = 0,
    width: int = 0,
    height: int = 0,
    fps: int = 0,
) -> tuple[Any, bool]:
    """Open a camera with OS-appropriate backend fallbacks.

    Returns (cv2.VideoCapture | None, reading_from_vcam: bool).
    """
    import sys
    import cv2

    reading_vcam = False
    cap = None

    def _try_open(idx: Any, backend: int | None = None) -> Any:
        try:
            c = cv2.VideoCapture(idx) if backend is None else cv2.VideoCapture(idx, backend)
            if c is not None and c.isOpened():
                return c
            if c is not None:
                c.release()
        except Exception:
            pass
        return None

    indices_to_try = [int(index)]
    if int(index) != 0:
        indices_to_try.append(0)

    if sys.platform == "win32":
        backends: list[int] = []
        for attr in ("CAP_DSHOW", "CAP_MSMF", "CAP_ANY"):
            b = getattr(cv2, attr, None)
            if b is not None and b not in backends:
                backends.append(b)
        if not backends:
            backends = [cv2.CAP_ANY]

        for idx in indices_to_try:
            for b in backends:
                c = _try_open(idx, b)
                if c is not None:
                    cap = c
                    break
            if cap is not None:
                break
    elif sys.platform == "darwin":
        backends = []
        for attr in ("CAP_AVFOUNDATION", "CAP_ANY"):
            b = getattr(cv2, attr, None)
            if b is not None and b not in backends:
                backends.append(b)
        for idx in indices_to_try:
            for b in backends:
                c = _try_open(idx, b)
                if c is not None:
                    cap = c
                    break
            if cap is not None:
                break
    else:
        for idx in indices_to_try:
            c = _try_open(idx)
            if c is not None:
                cap = c
                break
        if cap is None and os.path.exists("/dev/video10"):
            c = _try_open("/dev/video10")
            if c is not None:
                cap = c
                reading_vcam = True

    if cap is not None:
        if width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if fps > 0:
            cap.set(cv2.CAP_PROP_FPS, fps)

    return cap, reading_vcam

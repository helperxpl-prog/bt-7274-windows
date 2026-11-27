"""
Screen & webcam capture for BT vision.

Provides the two capture entry points main.py uses — `_capture_screen()` and
`_capture_camera()` — plus their helpers (compression, camera auto-detection,
config access). main.py grabs a frame here on demand, then injects it into the
main Gemini Live session; there is no separate vision session here.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

try:
    import cv2
    _CV2 = True
except ImportError:
    _CV2 = False

try:
    import mss
    import mss.tools
    _MSS = True
except ImportError:
    _MSS = False

try:
    import PIL.Image
    _PIL = True
except ImportError:
    _PIL = False


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE        = _base_dir()
_CONFIG_PATH = _BASE / "config" / "api_keys.json"


def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config_key(key: str, value) -> None:
    try:
        cfg = _load_config()
        cfg[key] = value
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    except Exception as e:
        print(f"[Vision] ⚠️  Could not save config key '{key}': {e}")


def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()


_IMG_MAX_W = 1280
_IMG_MAX_H = 720
_JPEG_Q    = 82


def _compress(img_bytes: bytes, source_format: str = "PNG") -> tuple[bytes, str]:
    if not _PIL:
        return img_bytes, f"image/{source_format.lower()}"

    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q, optimize=False)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        print(f"[Vision] ⚠️  Image compress failed: {e}")
        return img_bytes, f"image/{source_format.lower()}"


def _capture_screen() -> tuple[bytes, str]:
    """Capture screen cleanly, using native macOS screencapture or KDE spectacle/grim on Wayland with mss fallback."""
    # 0. Native macOS screencapture
    if sys.platform == "darwin" or shutil.which("screencapture"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tmp_path = tf.name
            res = subprocess.run(["screencapture", "-x", "-m", tmp_path], capture_output=True, timeout=3.0)
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
                with open(tmp_path, "rb") as f:
                    png_data = f.read()
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return _compress(png_data, "PNG")
        except Exception as e:
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
            res = subprocess.run(
                ["spectacle", "-b", "-n", "-f", "-o", tmp_path],
                capture_output=True,
                timeout=3.0,
            )
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
                with open(tmp_path, "rb") as f:
                    png_data = f.read()
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return _compress(png_data, "PNG")
        except Exception as e:
            print(f"[Vision] ⚠️ spectacle screen capture failed: {e}")
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
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
                with open(tmp_path, "rb") as f:
                    png_data = f.read()
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                return _compress(png_data, "PNG")
        except Exception:
            if 'tmp_path' in locals() and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    # 3. MSS fallback (Windows, X11)
    if not _MSS:
        raise RuntimeError("Neither spectacle/grim nor mss is available for screen capture.")

    with mss.mss() as sct:
        monitors = sct.monitors          # [0] = all combined, [1..n] = real screens
        target   = monitors[1] if len(monitors) > 1 else monitors[0]
        shot     = sct.grab(target)
        png      = mss.tools.to_png(shot.rgb, shot.size)

    return _compress(png, "PNG")


def _cv2_backend() -> int:
    """Return the best OpenCV camera backend for the current OS."""
    if not _CV2:
        return 0
    if sys.platform == "darwin" or _get_os() in ("mac", "darwin"):
        return getattr(cv2, "CAP_AVFOUNDATION", cv2.CAP_ANY)
    if sys.platform == "win32" or _get_os() == "windows":
        return getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY)
    return cv2.CAP_ANY


def _probe_camera(index: int, backend: int, warmup: int = 5) -> bool:

    if not _CV2:
        return False
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return False
    for _ in range(warmup):
        cap.read()
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return False
    return bool(np.mean(frame) > 8)


def _detect_camera_index() -> int:
    try:
        from core.camera_devices import list_cameras
        cams = list_cameras()
        if cams:
            chosen = int(cams[0]["index"])
            print(f"[Vision] ✅ Camera detected via camera_devices at index {chosen} ({cams[0]['name']})")
            _save_config_key("camera_index", chosen)
            return chosen
    except Exception:
        pass

    backend = _cv2_backend()
    print("[Vision] 🔍 Auto-detecting camera...")
    for idx in range(6):
        if _probe_camera(idx, backend):
            print(f"[Vision] ✅ Camera found at index {idx}")
            _save_config_key("camera_index", idx)
            return idx
        print(f"[Vision] ⚠️  Camera index {idx}: no usable frame")

    print("[Vision] ⚠️  No camera found — defaulting to index 0")
    _save_config_key("camera_index", 0)
    return 0


def _get_camera_index() -> int:
    cfg = _load_config()
    if "camera_index" in cfg:
        return int(cfg["camera_index"])
    return _detect_camera_index()


def _capture_camera() -> tuple[bytes, str]:
    if not _CV2:
        raise RuntimeError("OpenCV (cv2) is not installed. Run: pip install opencv-python")

    index = _get_camera_index()
    try:
        from core.camera_devices import open_camera
        cap, _ = open_camera(index)
    except Exception:
        cap = cv2.VideoCapture(index, _cv2_backend())

    if cap is None or not cap.isOpened():
        if cap is not None:
            cap.release()
        raise RuntimeError(f"Camera index {index} could not be opened.")

    for _ in range(10):
        cap.read()

    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Camera returned no frame.")

    if _PIL:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = PIL.Image.fromarray(rgb)
        img.thumbnail((_IMG_MAX_W, _IMG_MAX_H), PIL.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=_JPEG_Q)
        return buf.getvalue(), "image/jpeg"

    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_Q])
    return buf.tobytes(), "image/jpeg"

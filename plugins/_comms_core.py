"""
_comms_core.py - Shared send-only input engine for BT-7274 messaging plugins.

Target: EndeavourOS / Arch, KDE Plasma on Wayland.

* Input injection  : ydotool (writes to /dev/uinput via the ydotoold socket)
* Window focusing  : kdotool (KWin scripting), falling back to wmctrl
* No OCR, no clipboard, no pyautogui. Strictly send-only.

Requirements:
    sudo pacman -S ydotool wmctrl
    yay -S kdotool
    systemctl --user enable --now ydotool     # ydotoold must be running
    (your user needs access to /dev/uinput, e.g. the `input` group or a udev rule)
"""
from __future__ import annotations

import contextlib
import logging
import os
import random
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import List, Optional, Sequence, Tuple, Union

log = logging.getLogger("bt7274.comms")

Key = Union[str, int]


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class CommsError(RuntimeError):
    """Base class for all comms engine failures."""


class YdotoolError(CommsError):
    """ydotool missing, socket unreachable, or a ydotool call failed."""


class FocusError(CommsError):
    """Target window could not be found or focused."""


class AbortedError(CommsError):
    """Typing was aborted via TacticalCommsEngine.abort()."""


# --------------------------------------------------------------------------- #
# Key codes (Linux input-event-codes.h)
# --------------------------------------------------------------------------- #
KEYCODES = {
    "esc": 1, "escape": 1,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "backspace": 14, "tab": 15,
    "enter": 28, "return": 28,
    "ctrl": 29, "control": 29, "leftctrl": 29,
    "shift": 42, "leftshift": 42,
    "alt": 56, "leftalt": 56,
    "space": 57,
    "f11": 87,
    "slash": 53, "/": 53,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "home": 102, "up": 103, "pageup": 104, "pgup": 104,
    "left": 105, "right": 106, "end": 107, "down": 108,
    "pagedown": 109, "pgdn": 109, "delete": 111,
    "rightctrl": 97, "rightalt": 100, "meta": 125, "super": 125, "win": 125,
}

# Mapping from Linux evdev keycodes / names to Windows Virtual-Key (VK) codes.
_EVDEV_TO_VK = {
    1: 0x1B,    # VK_ESCAPE
    2: 0x31, 3: 0x32, 4: 0x33, 5: 0x34, 6: 0x35, 7: 0x36, 8: 0x37, 9: 0x38, 10: 0x39, 11: 0x30,
    14: 0x08,   # VK_BACK
    15: 0x09,   # VK_TAB
    28: 0x0D,   # VK_RETURN
    29: 0xA2,   # VK_LCONTROL
    42: 0xA0,   # VK_LSHIFT
    56: 0xA4,   # VK_LMENU (Alt)
    57: 0x20,   # VK_SPACE
    53: 0xBF,   # VK_OEM_2 ('/')
    87: 0x7A,   # VK_F11
    97: 0xA3,   # VK_RCONTROL
    100: 0xA5,  # VK_RMENU
    102: 0x24,  # VK_HOME
    103: 0x26,  # VK_UP
    104: 0x21,  # VK_PRIOR (PageUp)
    105: 0x25,  # VK_LEFT
    106: 0x27,  # VK_RIGHT
    107: 0x23,  # VK_END
    108: 0x28,  # VK_DOWN
    109: 0x22,  # VK_NEXT (PageDown)
    111: 0x2E,  # VK_DELETE
    125: 0x5B,  # VK_LWIN
    163: 0xB0,  # VK_MEDIA_NEXT_TRACK
    164: 0xB3,  # VK_MEDIA_PLAY_PAUSE
    165: 0xB1,  # VK_MEDIA_PREV_TRACK
    166: 0xB2,  # VK_MEDIA_STOP
    # Letters A-Z (evdev -> ASCII/VK)
    16: ord("Q"), 17: ord("W"), 18: ord("E"), 19: ord("R"), 20: ord("T"),
    21: ord("Y"), 22: ord("U"), 23: ord("I"), 24: ord("O"), 25: ord("P"),
    30: ord("A"), 31: ord("S"), 32: ord("D"), 33: ord("F"), 34: ord("G"),
    35: ord("H"), 36: ord("J"), 37: ord("K"), 38: ord("L"),
    44: ord("Z"), 45: ord("X"), 46: ord("C"), 47: ord("V"), 48: ord("B"),
    49: ord("N"), 50: ord("M"),
}

_WIN_EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E,
    0xA3, 0xA5, 0x5B, 0x5C, 0xB0, 0xB1, 0xB2, 0xB3,
}

# Typographic characters ydotool cannot type -> ASCII equivalents.
_UNICODE_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ",
})

# Extra pause (seconds, min/max) after punctuation.
_PUNCT_PAUSE = {
    ",": (0.12, 0.30), ";": (0.15, 0.35), ":": (0.15, 0.35),
    ".": (0.25, 0.55), "!": (0.25, 0.55), "?": (0.25, 0.55),
    "-": (0.03, 0.10), ")": (0.04, 0.12),
}


# --------------------------------------------------------------------------- #
# Windows Win32 native input & window management (zero external dependencies)
# --------------------------------------------------------------------------- #
def _win32_send_inputs(inputs_list: list) -> None:
    import ctypes
    from ctypes import wintypes

    INPUT_KEYBOARD = 1

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

    arr = (INPUT * len(inputs_list))()
    for i, (vk, scan, flags) in enumerate(inputs_list):
        arr[i].type = INPUT_KEYBOARD
        arr[i].union.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)

    sent = ctypes.windll.user32.SendInput(len(inputs_list), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs_list):
        raise CommsError(f"Win32 SendInput only sent {sent}/{len(inputs_list)} events.")


def win32_send_vk_chord(*vks: int) -> None:
    """Press and release a chord of Windows Virtual-Key codes."""
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    events = []
    for vk in vks:
        ext = KEYEVENTF_EXTENDEDKEY if vk in _WIN_EXTENDED_VKS else 0
        events.append((vk, 0, ext))
    for vk in reversed(vks):
        ext = KEYEVENTF_EXTENDEDKEY if vk in _WIN_EXTENDED_VKS else 0
        events.append((vk, 0, ext | KEYEVENTF_KEYUP))
    _win32_send_inputs(events)


def win32_type_text(text: str) -> int:
    """Type arbitrary Unicode text directly via Win32 KEYEVENTF_UNICODE (no clipboard)."""
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_KEYUP = 0x0002
    events = []
    count = 0
    for ch in text:
        code = ord(ch)
        events.append((0, code, KEYEVENTF_UNICODE))
        events.append((0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        count += 1
        # Send in batches of 32 chars so we don't overflow input buffer
        if len(events) >= 64:
            _win32_send_inputs(events)
            events.clear()
    if events:
        _win32_send_inputs(events)
    return count


def win32_list_windows() -> List[Tuple[int, str, str]]:
    """Return list of (hwnd, title, class_name) for visible top-level Windows windows."""
    import ctypes
    user32 = ctypes.windll.user32
    results: List[Tuple[int, str, str]] = []

    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                tbuf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, tbuf, length + 1)
                cbuf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cbuf, 256)
                title = tbuf.value.strip()
                if title:
                    results.append((int(hwnd), title, cbuf.value.strip()))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return results


def win32_get_window_title(hwnd: int) -> str:
    import ctypes
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def win32_focus_window(hwnd: int, settle: float = 0.25) -> bool:
    """Restore if minimized and bring hwnd to foreground reliably on Windows."""
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_RESTORE = 9

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    fg_hwnd = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
    cur_tid = kernel32.GetCurrentThreadId()

    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)

    time.sleep(settle)
    return int(user32.GetForegroundWindow()) == int(hwnd)


class TacticalCommsEngine:
    """Cross-platform typing, key injection and window focusing (Win32 on Windows, ydotool/kdotool on Linux)."""

    def __init__(
        self,
        ydotool_bin: str = "ydotool",
        *,
        mean_delay: float = 0.075,
        jitter: float = 0.030,
        hesitation_chance: float = 0.04,
        auto_start_daemon: bool = True,
    ) -> None:
        self.mean_delay = mean_delay
        self.jitter = jitter
        self.hesitation_chance = hesitation_chance
        self._rng = random.Random()
        self._abort = threading.Event()
        self._lock = threading.RLock()
        self._is_windows = sys.platform == "win32"

        if self._is_windows:
            self.ydotool_bin = "win32-sendinput"
            self.socket_path = "win32"
            self.env = os.environ.copy()
            log.info("TacticalCommsEngine initialized with native Win32 SendInput.")
            return

        path = shutil.which(ydotool_bin)
        if not path:
            raise YdotoolError("ydotool not found in PATH (sudo pacman -S ydotool).")
        self.ydotool_bin = path

        self.socket_path = self.discover_socket(auto_start=auto_start_daemon)
        self.env = os.environ.copy()
        self.env["YDOTOOL_SOCKET"] = self.socket_path
        log.info("ydotool socket bound: %s", self.socket_path)

    # ------------------------------------------------------------------ #
    # Session control
    # ------------------------------------------------------------------ #
    @contextlib.contextmanager
    def session(self):
        """Serialise sends so two commands never interleave keystrokes."""
        with self._lock:
            self._abort.clear()
            yield self

    def abort(self) -> None:
        """Thread-safe: stop any typing in progress."""
        self._abort.set()

    def pause(self, low: float, high: Optional[float] = None) -> None:
        """Sleep for a random duration in [low, high] (or exactly low)."""
        time.sleep(low if high is None else self._rng.uniform(low, high))

    # ------------------------------------------------------------------ #
    # Socket discovery
    # ------------------------------------------------------------------ #
    @staticmethod
    def _socket_candidates() -> List[str]:
        raw = [
            os.environ.get("YDOTOOL_SOCKET"),
            os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), ".ydotool_socket")
            if os.environ.get("XDG_RUNTIME_DIR") else None,
            f"/run/user/{os.getuid()}/.ydotool_socket",
            "/run/user/1000/.ydotool_socket",
            "/tmp/.ydotool_socket",
        ]
        seen, out = set(), []
        for p in raw:
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _find_socket(self) -> Optional[str]:
        for path in self._socket_candidates():
            try:
                if stat.S_ISSOCK(os.stat(path).st_mode):
                    if not os.access(path, os.W_OK):
                        raise YdotoolError(
                            f"ydotool socket {path} exists but is not writable by "
                            f"uid {os.getuid()} (check ydotoold's --socket-perm / user)."
                        )
                    return path
            except FileNotFoundError:
                continue
            except PermissionError as exc:
                raise YdotoolError(f"No permission to inspect {path}: {exc}") from exc
        return None

    def discover_socket(self, auto_start: bool = True) -> str:
        found = self._find_socket()
        if found:
            return found

        if auto_start and shutil.which("systemctl"):
            log.warning("No ydotool socket found; trying `systemctl --user start ydotool`.")
            subprocess.run(
                ["systemctl", "--user", "start", "ydotool"],
                capture_output=True, text=True, check=False,
            )
            for _ in range(10):
                time.sleep(0.2)
                found = self._find_socket()
                if found:
                    return found

        raise YdotoolError(
            "ydotool socket not found. Start the daemon "
            "(`systemctl --user enable --now ydotool` or run `ydotoold`). "
            "Searched: " + ", ".join(self._socket_candidates())
        )

    # ------------------------------------------------------------------ #
    # ydotool wrapper
    # ------------------------------------------------------------------ #
    def _ydotool(self, *args: object, timeout: float = 10.0) -> None:
        if self._is_windows:
            if args and args[0] == "key":
                down_vks: List[int] = []
                for token in args[1:]:
                    s = str(token)
                    if ":" in s:
                        code_str, state = s.split(":", 1)
                        code = int(code_str)
                        vk = _EVDEV_TO_VK.get(code)
                        if vk is not None and state == "1":
                            down_vks.append(vk)
                if down_vks:
                    win32_send_vk_chord(*down_vks)
            elif args and args[0] == "type":
                # Find text after "--"
                str_args = [str(a) for a in args[1:]]
                if "--" in str_args:
                    idx = str_args.index("--")
                    text = " ".join(str_args[idx + 1:])
                else:
                    text = str_args[-1] if str_args else ""
                if text:
                    win32_type_text(text)
            return

        cmd = [self.ydotool_bin, *(str(a) for a in args)]
        try:
            proc = subprocess.run(
                cmd, env=self.env, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise YdotoolError(f"ydotool {args[0]} timed out after {timeout}s.") from exc
        except OSError as exc:
            raise YdotoolError(f"Could not execute ydotool: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise YdotoolError(f"ydotool {args[0]} failed (rc={proc.returncode}): {detail}")

    # ------------------------------------------------------------------ #
    # Raw key injection
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_key(key: Key) -> int:
        if isinstance(key, int):
            return key
        name = key.strip().lower()
        if name in KEYCODES:
            return KEYCODES[name]
        if name.isdigit():
            return int(name)
        raise CommsError(f"Unknown key '{key}'.")

    def press_keys(self, *keys: Key) -> None:
        """
        Press keys as a chord: down in order, release in reverse.
            press_keys("ctrl", "alt", "slash")   # Ctrl+Alt+/
            press_keys("ctrl+a")                 # '+' shorthand also works
            press_keys(14)                       # raw keycode (Backspace)
        """
        flat: List[Key] = []
        for k in keys:
            if isinstance(k, str) and "+" in k and k.strip() != "+":
                flat.extend(part for part in k.split("+") if part)
            else:
                flat.append(k)
        codes = [self._resolve_key(k) for k in flat]
        if not codes:
            raise CommsError("press_keys() called with no keys.")
        if self._is_windows:
            vks = []
            for c in codes:
                vk = _EVDEV_TO_VK.get(c)
                if vk is None:
                    raise CommsError(f"No Windows VK mapping for keycode {c}.")
                vks.append(vk)
            win32_send_vk_chord(*vks)
            return
        seq = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
        self._ydotool("key", *seq)

    def press_enter(self) -> None:
        self.press_keys("enter")

    def press_shift_enter(self) -> None:
        self.press_keys("shift", "enter")

    def clear_field(self) -> None:
        """Select-all + Backspace in the focused text field."""
        self.press_keys("ctrl", "a")
        self.pause(0.05, 0.12)
        self.press_keys("backspace")

    # ------------------------------------------------------------------ #
    # Human-like typing
    # ------------------------------------------------------------------ #
    def human_type(self, text: str, pace: float = 1.0) -> int:
        """
        Fast direct typing via ydotool — zero clipboard usage.
        Newlines become Shift+Enter so multi-line messages are preserved.
        """
        text = text.translate(_UNICODE_MAP).replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        typed = 0

        for i, line in enumerate(lines):
            if self._abort.is_set():
                raise AbortedError("Typing aborted.")

            if self._is_windows:
                if line:
                    typed += win32_type_text(line)
            else:
                clean = "".join(ch for ch in line if ch.isascii() and ch.isprintable())
                if clean:
                    self._ydotool("type", "-d", "1", "-H", "1", "--", clean)
                    typed += len(clean)

            if i < len(lines) - 1:
                self.press_shift_enter()
                time.sleep(0.04)

        return typed

    # ------------------------------------------------------------------ #
    # Window focusing (kdotool -> wmctrl)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _capture(cmd: Sequence[str], timeout: float = 5.0) -> Tuple[int, str]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, ""
        except OSError as exc:
            log.debug("Command %s failed: %s", cmd[0], exc)
            return 127, ""
        return proc.returncode, proc.stdout or ""

    def focus_window(
        self,
        names: Sequence[str] = (),
        classes: Sequence[str] = (),
        settle: float = 0.45,
        verify: bool = True,
    ) -> str:
        """
        Bring a window to the foreground. `classes` are tried before `names`
        (class is more stable than the title). Returns a description of what
        was focused; raises FocusError if nothing could be focused.
        """
        if not names and not classes:
            raise FocusError("focus_window() needs at least one name or class pattern.")

        if self._is_windows:
            return self._focus_win32(names, classes, settle, verify)

        errors: List[str] = []
        if shutil.which("kdotool"):
            try:
                return self._focus_kdotool(names, classes, settle, verify)
            except FocusError as exc:
                errors.append(str(exc))
                log.warning("kdotool focus failed: %s", exc)
        else:
            errors.append("kdotool not installed")

        if shutil.which("wmctrl"):
            try:
                return self._focus_wmctrl(names, classes, settle)
            except FocusError as exc:
                errors.append(str(exc))
        else:
            errors.append("wmctrl not installed")

        raise FocusError("Could not focus target window: " + "; ".join(errors))

    def _focus_win32(
        self, names: Sequence[str], classes: Sequence[str], settle: float, verify: bool
    ) -> str:
        windows = win32_list_windows()
        patterns_cls = [c.lower() for c in classes]
        patterns_name = [n.lower() for n in names]

        matched_hwnd = None
        matched_title = ""
        # Match by title or class (on Windows, Electron apps like Discord/Vesktop/Chrome have generic class Chrome_WidgetWin_1, so check both)
        for pat in patterns_name + patterns_cls:
            for hwnd, title, cls in windows:
                if pat in title.lower() or pat in cls.lower():
                    matched_hwnd = hwnd
                    matched_title = title
                    break
            if matched_hwnd:
                break

        if not matched_hwnd:
            raise FocusError(f"win32: no window matched {list(names) + list(classes)}")

        ok = win32_focus_window(matched_hwnd, settle=settle)
        if verify and not ok:
            log.warning("win32: SetForegroundWindow did not report active match for %s; proceeding.", matched_title)
        return f"win32:{matched_hwnd}:{matched_title}"

    def _focus_kdotool(
        self, names: Sequence[str], classes: Sequence[str], settle: float, verify: bool
    ) -> str:
        ids: List[str] = []
        for flag, patterns in (("--class", classes), ("--name", names)):
            for pattern in patterns:
                rc, out = self._capture(["kdotool", "search", flag, pattern])
                if rc != 0:
                    continue
                for line in out.splitlines():
                    wid = line.strip()
                    if wid and wid not in ids:
                        ids.append(wid)
        if not ids:
            raise FocusError(f"kdotool: no window matched {list(classes) + list(names)}")

        for wid in ids:
            rc, _ = self._capture(["kdotool", "windowactivate", wid])
            if rc != 0:
                continue
            time.sleep(settle)
            if not verify:
                return f"kdotool:{wid}"
            rc, active = self._capture(["kdotool", "getactivewindow"])
            if rc != 0:
                log.warning("Could not verify active window; proceeding on trust.")
                return f"kdotool:{wid}"
            if active.strip() == wid:
                return f"kdotool:{wid}"
        raise FocusError("kdotool: matching window(s) found but none took focus.")

    def _focus_wmctrl(self, names: Sequence[str], classes: Sequence[str], settle: float) -> str:
        # Note: wmctrl only sees X11/XWayland windows.
        attempts = [["wmctrl", "-x", "-a", c] for c in classes] + \
                   [["wmctrl", "-a", n] for n in names]
        for cmd in attempts:
            rc, _ = self._capture(cmd)
            if rc == 0:
                time.sleep(settle)
                return f"wmctrl:{cmd[-1]}"
        raise FocusError("wmctrl: no window matched.")


# --------------------------------------------------------------------------- #
# Shared singleton
# --------------------------------------------------------------------------- #
_engine: Optional[TacticalCommsEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> TacticalCommsEngine:
    """Lazily build one engine shared by every comms plugin."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = TacticalCommsEngine()
        return _engine


if __name__ == "__main__":
    # Diagnostics:  python -m plugins._comms_core
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for tool in ("ydotool", "kdotool", "wmctrl"):
        print(f"{tool:8s}: {shutil.which(tool) or 'NOT FOUND'}")
    try:
        eng = TacticalCommsEngine()
        print("socket  :", eng.socket_path)
    except CommsError as exc:
        print("ERROR   :", exc)

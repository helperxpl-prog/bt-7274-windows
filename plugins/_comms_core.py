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
    "backspace": 14, "tab": 15,
    "enter": 28, "return": 28,
    "ctrl": 29, "control": 29, "leftctrl": 29,
    "shift": 42, "leftshift": 42,
    "alt": 56, "leftalt": 56,
    "space": 57,
    "slash": 53, "/": 53,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24, "p": 25,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "home": 102, "up": 103, "end": 107, "down": 108, "delete": 111,
    "rightctrl": 97, "rightalt": 100, "meta": 125, "super": 125,
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


class TacticalCommsEngine:
    """Wayland-safe typing, key injection and window focusing."""

    def __init__(
        self,
        ydotool_bin: str = "ydotool",
        *,
        mean_delay: float = 0.075,
        jitter: float = 0.030,
        hesitation_chance: float = 0.04,
        auto_start_daemon: bool = True,
    ) -> None:
        path = shutil.which(ydotool_bin)
        if not path:
            raise YdotoolError("ydotool not found in PATH (sudo pacman -S ydotool).")
        self.ydotool_bin = path
        self.mean_delay = mean_delay
        self.jitter = jitter
        self.hesitation_chance = hesitation_chance
        self._rng = random.Random()
        self._abort = threading.Event()
        self._lock = threading.RLock()

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

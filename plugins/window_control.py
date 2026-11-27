import shutil
import subprocess
import sys

PLUGIN = {
    "name": "window_control",
    "description": (
        "Manages and switches desktop windows (Windows Win32 & Linux KDE Plasma). "
        "Supports listing open windows, focusing/switching to an app by partial name or title, "
        "Alt+Tab cycling, minimizing, maximizing, toggling fullscreen, closing windows, "
        "and snapping windows to the left or right half of the screen."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "One of: 'focus' (switch to window), 'list' (show all open windows), "
                    "'alt_tab' (switch to previous window), 'minimize', 'maximize', "
                    "'fullscreen', 'close', 'snap_left', 'snap_right', or 'show_desktop'."
                )
            },
            "target": {
                "type": "STRING",
                "description": (
                    "Partial window title or app name (e.g., 'vesktop', 'konsole', 'zen', "
                    "'firefox', 'steam', 'minecraft', 'code'). If omitted for window actions, "
                    "applies to the currently active window."
                )
            }
        },
        "required": ["action"]
    }
}

# Friendly aliases so natural speech matches window classes/titles reliably
APP_ALIASES = {
    "terminal": "konsole" if sys.platform != "win32" else "terminal",
    "discord": "discord" if sys.platform == "win32" else "vesktop",
    "browser": "chrome" if sys.platform == "win32" else "zen",
    "vscode": "visual studio code" if sys.platform == "win32" else "code",
    "files": "file explorer" if sys.platform == "win32" else "dolphin",
    "settings": "settings" if sys.platform == "win32" else "systemsettings",
}


def _run_windows(action: str, target: str) -> str:
    import ctypes
    from plugins._comms_core import (
        win32_focus_window,
        win32_get_window_title,
        win32_list_windows,
        win32_send_vk_chord,
    )

    user32 = ctypes.windll.user32
    SW_MINIMIZE = 6
    SW_MAXIMIZE = 3
    WM_CLOSE = 0x0010

    if action == "list":
        wins = win32_list_windows()
        if not wins:
            return "No active desktop windows detected, Pilot."
        return "Open windows:\n" + "\n".join(f"- {title}" for _, title, _ in wins)

    if action == "alt_tab":
        win32_send_vk_chord(0xA4, 0x09)  # Alt+Tab
        return "Switched to previous window."

    if action == "show_desktop":
        win32_send_vk_chord(0x5B, ord("D"))  # Win+D
        return "Toggled desktop view."

    hwnd = 0
    title = ""
    if target:
        q = APP_ALIASES.get(target.lower(), target.lower())
        for h, t, c in win32_list_windows():
            if q in t.lower() or q in c.lower():
                hwnd, title = h, t
                break
        if not hwnd and target.lower() == "discord":
            for h, t, c in win32_list_windows():
                if "vesktop" in t.lower():
                    hwnd, title = h, t
                    break
        if not hwnd and target.lower() == "browser":
            for b in ("chrome", "edge", "firefox", "brave", "zen", "opera"):
                for h, t, _ in win32_list_windows():
                    if b in t.lower():
                        hwnd, title = h, t
                        break
                if hwnd:
                    break
        if not hwnd:
            return f"No open window matching '{target}' found, Pilot."
    else:
        hwnd = int(user32.GetForegroundWindow())
        if not hwnd:
            return "Could not detect active window."
        title = win32_get_window_title(hwnd)

    if action == "focus":
        win32_focus_window(hwnd)
        return f"Focused '{title}', Pilot."

    if action == "minimize":
        user32.ShowWindow(hwnd, SW_MINIMIZE)
        return f"Minimized '{title}'."

    if action == "maximize":
        win32_focus_window(hwnd, settle=0.1)
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        return f"Maximized '{title}'."

    if action == "close":
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return f"Closed '{title}'."

    win32_focus_window(hwnd, settle=0.15)

    if action == "fullscreen":
        win32_send_vk_chord(0x7A)  # F11
        return f"Toggled fullscreen on '{title}'."

    if action == "snap_left":
        win32_send_vk_chord(0x5B, 0x25)  # Win+Left
        return f"Snapped '{title}' to left half."

    if action == "snap_right":
        win32_send_vk_chord(0x5B, 0x27)  # Win+Right
        return f"Snapped '{title}' to right half."

    return f"Unknown window action: {action}"


def _run(args: list[str]) -> tuple[bool, str]:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=3)
        return res.returncode == 0, (res.stdout or res.stderr or "").strip()
    except Exception as e:
        return False, str(e)


def _list_windows() -> list[tuple[str, str]]:
    """Returns list of (window_id, window_title) for all open KDE windows."""
    ok, out = _run(["kdotool", "search", "--name", ".*"])
    if not ok or not out:
        return []
    windows = []
    for wid in out.splitlines():
        wid = wid.strip()
        if not wid:
            continue
        t_ok, title = _run(["kdotool", "getwindowname", wid])
        if t_ok and title:
            windows.append((wid, title))
    return windows


def _find_window_id(target: str) -> tuple[str, str]:
    """Finds best matching (window_id, window_title) by case-insensitive name or class."""
    query = APP_ALIASES.get(target.lower(), target.lower())

    # 1. Try matching window title first
    for wid, title in _list_windows():
        if query in title.lower():
            return wid, title

    # 2. Fallback to matching KDE window class name
    ok, out = _run(["kdotool", "search", "--class", query])
    if ok and out:
        wid = out.splitlines()[0].strip()
        _, title = _run(["kdotool", "getwindowname", wid])
        return wid, title or query

    return "", ""


def run(parameters: dict, **kwargs) -> str:
    params = parameters or {}
    action = str(params.get("action", "focus")).strip().lower()
    target = str(params.get("target", "")).strip()

    if sys.platform == "win32":
        return _run_windows(action, target)

    if not shutil.which("kdotool"):
        return "kdotool is not installed. Run: yay -S kdotool"

    if action == "list":
        wins = _list_windows()
        if not wins:
            return "No active desktop windows detected, Pilot."
        return "Open windows:\n" + "\n".join(f"- {title}" for _, title in wins)

    if action == "alt_tab":
        if shutil.which("ydotool"):
            # 56=LAlt, 15=Tab
            _run(["ydotool", "key", "56:1", "15:1", "15:0", "56:0"])
            return "Switched to previous window."
        return "ydotool required for alt_tab."

    if action == "show_desktop":
        if shutil.which("ydotool"):
            # Meta+D (125=LeftMeta, 32=D)
            _run(["ydotool", "key", "125:1", "32:1", "32:0", "125:0"])
            return "Toggled KDE desktop view."
        return "ydotool required for show_desktop."

    # Resolve target window ID (or grab active window if target wasn't specified)
    if target:
        wid, title = _find_window_id(target)
        if not wid:
            return f"No open window matching '{target}' found, Pilot."
    else:
        ok, wid = _run(["kdotool", "getactivewindow"])
        if not ok or not wid:
            return "Could not detect active window."
        _, title = _run(["kdotool", "getwindowname", wid])

    if action == "focus":
        ok, err = _run(["kdotool", "windowactivate", wid])
        return f"Focused '{title}', Pilot." if ok else f"Failed to focus window: {err}"

    if action == "minimize":
        ok, err = _run(["kdotool", "windowminimize", wid])
        return f"Minimized '{title}'." if ok else f"Minimize failed: {err}"

    if action == "close":
        ok, err = _run(["kdotool", "windowclose", wid])
        return f"Closed '{title}'." if ok else f"Close failed: {err}"

    # Actions that require focusing the window first and using KWin shortcuts
    _run(["kdotool", "windowactivate", wid])

    if not shutil.which("ydotool"):
        return f"Focused '{title}', but ydotool is missing for window tiling/state keys."

    if action == "maximize":
        # Meta+PgUp (125=LeftMeta, 104=PageUp) in KDE Plasma
        _run(["ydotool", "key", "125:1", "104:1", "104:0", "125:0"])
        return f"Maximized '{title}'."

    if action == "fullscreen":
        # F11 (87=F11)
        _run(["ydotool", "key", "87:1", "87:0"])
        return f"Toggled fullscreen on '{title}'."

    if action == "snap_left":
        # Meta+Left (125=LeftMeta, 105=Left)
        _run(["ydotool", "key", "125:1", "105:1", "105:0", "125:0"])
        return f"Snapped '{title}' to left half."

    if action == "snap_right":
        # Meta+Right (125=LeftMeta, 106=Right)
        _run(["ydotool", "key", "125:1", "106:1", "106:0", "125:0"])
        return f"Snapped '{title}' to right half."

    return f"Unknown window action: {action}"

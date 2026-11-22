import shutil
import subprocess

PLUGIN = {
    "name": "window_control",
    "description": (
        "Manages and switches desktop windows on KDE Plasma (EndeavourOS). "
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

# Friendly aliases so natural speech matches KDE window classes/titles reliably
APP_ALIASES = {
    "terminal": "konsole",
    "discord": "vesktop",
    "browser": "zen",
    "vscode": "code",
    "files": "dolphin",
    "settings": "systemsettings",
}


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
    if not shutil.which("kdotool"):
        return "kdotool is not installed. Run: yay -S kdotool"

    params = parameters or {}
    action = str(params.get("action", "focus")).strip().lower()
    target = str(params.get("target", "")).strip()

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

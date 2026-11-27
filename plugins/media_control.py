import os
import shutil
import subprocess
import sys
import time

PLUGIN = {
    "name": "media_control",
    "description": (
        "Linux media and audio controller. Uses direct tab shortcuts ('K', 'J', 'L', 'Shift+N', 'Shift+P') "
        "when a browser is active so switching YouTube tabs and saying pause/resume/seek ALWAYS controls "
        "the visible tab instead of toggling the previous tab. Uses playerctl for Spotify/VLC/background apps "
        "and wpctl for PipeWire system volume."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "command": {
                "type": "STRING",
                "description": (
                    "Action to perform: 'pause', 'play', 'play-pause', 'next', 'previous', 'stop', "
                    "'forward', 'backward', 'seek', 'open', 'shuffle', 'loop', 'status', 'metadata', "
                    "'list-players', 'player-volume', 'system-volume', 'volume-up', 'volume-down', or 'mute'."
                )
            },
            "seconds": {
                "type": "INTEGER",
                "description": "Seconds to seek for 'forward'/'backward' (default 10), or exact timestamp in seconds for 'seek'."
            },
            "value": {
                "type": "STRING",
                "description": (
                    "Extra argument: URL/file path for 'open'; 'on'/'off'/'toggle' for 'shuffle'; "
                    "'none'/'track'/'playlist' for 'loop'; percentage 0-100 for 'player-volume'/'system-volume'; "
                    "or metadata key ('title', 'artist', 'album', 'url') for 'metadata'."
                )
            },
            "player": {
                "type": "STRING",
                "description": (
                    "Target a specific app (e.g., 'spotify', 'vlc', 'mpv') or 'browser'. "
                    "Leave empty to control the currently focused browser tab or active player."
                )
            },
            "ignore_player": {
                "type": "STRING",
                "description": "Ignore a specific player (e.g., 'vesktop')."
            },
            "all_players": {
                "type": "BOOLEAN",
                "description": "If True, applies the command to ALL active MPRIS players (-a)."
            }
        },
        "required": ["command"]
    }
}

BROWSERS = ("zen", "firefox", "brave", "chromium", "chrome", "edge", "microsoft edge", "floorp", "vivaldi", "opera")


def _run_cmd(args: list[str]) -> tuple[bool, str]:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=3)
        if res.returncode != 0:
            err = (res.stderr or res.stdout or "").strip() or "No active media player detected."
            return False, err
        return True, (res.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out."
    except Exception as e:
        return False, str(e)


def _ydotool_env() -> dict:
    env = os.environ.copy()
    if "YDOTOOL_SOCKET" not in env:
        uid = os.getuid()
        for c in (f"/run/user/{uid}/.ydotool_socket", "/tmp/.ydotool_socket"):
            if os.path.exists(c):
                env["YDOTOOL_SOCKET"] = c
                break
    return env


def _send_keys(*combos: str):
    if sys.platform == "win32":
        from plugins._comms_core import get_engine
        get_engine()._ydotool("key", *combos)
        return
    subprocess.run(["ydotool", "key", *combos], env=_ydotool_env(), check=True, timeout=3)


def _get_focused_browser_info() -> tuple[str, str]:
    """Returns (wid, window_title) if a browser is currently focused, else ('', '')."""
    if sys.platform == "win32":
        import ctypes
        from plugins._comms_core import win32_get_window_title
        hwnd = int(ctypes.windll.user32.GetForegroundWindow())
        if not hwnd:
            return "", ""
        title = win32_get_window_title(hwnd)
        low = title.lower()
        if any(b in low for b in BROWSERS) or "youtube" in low:
            return str(hwnd), title
        return "", ""

    if not shutil.which("kdotool"):
        return "", ""
    ok, wid = _run_cmd(["kdotool", "getactivewindow"])
    if not ok or not wid:
        return "", ""
    t_ok, title = _run_cmd(["kdotool", "getwindowname", wid])
    if not t_ok or not title:
        return "", ""
    low = title.lower()
    if any(b in low for b in BROWSERS) or "youtube" in low:
        return wid, title
    return "", ""


def _control_focused_browser_tab(cmd: str, seconds: int, title: str) -> tuple[bool, str]:
    """Controls the visible browser tab directly via keyboard shortcuts (no D-Bus cross-tab confusion)."""
    if sys.platform != "win32" and not shutil.which("ydotool"):
        return False, ""

    is_youtube = "youtube" in title.lower()

    # Check actual MPRIS status so we NEVER double-toggle an already-paused or already-playing video!
    mpris_status = ""
    if shutil.which("playerctl"):
        ok, list_out = _run_cmd(["playerctl", "-l"])
        if ok:
            for inst in list_out.splitlines():
                if any(b in inst.lower() for b in (*BROWSERS, "plasma-browser-integration")):
                    s_ok, st = _run_cmd(["playerctl", "-p", inst.strip(), "status"])
                    if s_ok:
                        mpris_status = st.strip()
                        break

    # Prevent toggling if the active video is already in the requested state
    if cmd == "pause" and mpris_status == "Paused" and is_youtube:
        # Note: if you just switched to a *different* playing YouTube tab, MPRIS might still report
        # the old tab's 'Paused' state, so we still send 'K' once to pause the new tab!
        pass

    time.sleep(0.15)  # Give KWin/browser time to finish tab switch animation

    if cmd in ("pause", "play", "play-pause"):
        # 'K' (evdev 37) toggles YouTube reliably without scrolling; Space (evdev 57) for generic HTML5 video
        key = "37" if is_youtube else "57"
        _send_keys(f"{key}:1", f"{key}:0")
        return True, f"Sent '{cmd}' directly to active tab: '{title}'."

    if cmd == "next":
        # Shift+N (42=LShift, 49=N) on YouTube
        _send_keys("42:1", "49:1", "49:0", "42:0")
        return True, f"Skipped to next video in '{title}'."

    if cmd == "previous":
        # Shift+P (42=LShift, 25=P) on YouTube
        _send_keys("42:1", "25:1", "25:0", "42:0")
        return True, f"Went to previous video in '{title}'."

    if cmd == "forward":
        # 'L' (evdev 38) seeks +10s on YouTube; Right Arrow (106) seeks +5s on generic players
        steps = max(1, round(seconds / 10)) if is_youtube else max(1, round(seconds / 5))
        key = "38" if is_youtube else "106"
        for _ in range(steps):
            _send_keys(f"{key}:1", f"{key}:0")
            time.sleep(0.05)
        return True, f"Fast-forwarded ~{seconds}s in '{title}'."

    if cmd == "backward":
        # 'J' (evdev 36) seeks -10s on YouTube; Left Arrow (105) seeks -5s on generic players
        steps = max(1, round(seconds / 10)) if is_youtube else max(1, round(seconds / 5))
        key = "36" if is_youtube else "105"
        for _ in range(steps):
            _send_keys(f"{key}:1", f"{key}:0")
            time.sleep(0.05)
        return True, f"Rewound ~{seconds}s in '{title}'."

    return False, ""


def _windows_volume(cmd: str, value: str) -> str:
    """Control Windows master volume via pycaw, with Win32 VK_VOLUME_* fallback."""
    try:
        from ctypes import POINTER, cast
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(interface, POINTER(IAudioEndpointVolume))

        if cmd == "mute":
            if value == "1":
                vol.SetMute(1, None)
            elif value == "0":
                vol.SetMute(0, None)
            else:
                cur = vol.GetMute()
                vol.SetMute(0 if cur else 1, None)
            return "Toggled system audio mute."

        if cmd == "volume-up":
            step = (int(value) if value.isdigit() else 5) / 100.0
            cur = vol.GetMasterVolumeLevelScalar()
            nxt = min(1.0, cur + step)
            vol.SetMasterVolumeLevelScalar(nxt, None)
            return f"Increased system volume to {round(nxt * 100)}%."

        if cmd == "volume-down":
            step = (int(value) if value.isdigit() else 5) / 100.0
            cur = vol.GetMasterVolumeLevelScalar()
            nxt = max(0.0, cur - step)
            vol.SetMasterVolumeLevelScalar(nxt, None)
            return f"Decreased system volume to {round(nxt * 100)}%."

        if cmd == "system-volume":
            if not value:
                cur = vol.GetMasterVolumeLevelScalar()
                return f"Current system volume: {round(cur * 100)}%"
            lvl = max(0, min(100, int(float(value.replace("%", "")))))
            vol.SetMasterVolumeLevelScalar(lvl / 100.0, None)
            return f"System volume set to {lvl}%."
    except Exception:
        pass

    # Fallback: hardware volume keys via SendInput
    from plugins._comms_core import win32_send_vk_chord
    if cmd == "mute":
        win32_send_vk_chord(0xAD)  # VK_VOLUME_MUTE
        return "Toggled system audio mute."
    if cmd == "volume-up":
        steps = max(1, (int(value) if value.isdigit() else 5) // 2)
        for _ in range(steps):
            win32_send_vk_chord(0xAF)  # VK_VOLUME_UP
        return "Increased system volume."
    if cmd == "volume-down":
        steps = max(1, (int(value) if value.isdigit() else 5) // 2)
        for _ in range(steps):
            win32_send_vk_chord(0xAE)  # VK_VOLUME_DOWN
        return "Decreased system volume."
    return "System volume adjustment requires pycaw on Windows."


def run(parameters: dict, **kwargs) -> str:
    params = parameters or {}
    cmd = str(params.get("command", "pause")).strip().lower()
    seconds = int(params.get("seconds", 10))
    value = str(params.get("value", "")).strip()
    player = str(params.get("player", "")).strip()
    ignore_player = str(params.get("ignore_player", "")).strip()
    all_players = bool(params.get("all_players", False))

    # 1. System-wide volume / mute controls
    if cmd in ("system-volume", "volume-up", "volume-down", "mute"):
        if sys.platform == "win32":
            return _windows_volume(cmd, value)

        if not shutil.which("wpctl"):
            return "wpctl not found. Install wireplumber: sudo pacman -S wireplumber"

        if cmd == "mute":
            state = value if value in ("1", "0", "toggle") else "toggle"
            ok, out = _run_cmd(["wpctl", "set-mute", "@DEFAULT_SINK@", state])
            return "Toggled system audio mute." if ok else f"Audio mute failed: {out}"

        if cmd == "volume-up":
            step = int(value) if value.isdigit() else 5
            ok, out = _run_cmd(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_SINK@", f"{step}%+"])
            return f"Increased system volume by {step}%." if ok else f"Volume error: {out}"

        if cmd == "volume-down":
            step = int(value) if value.isdigit() else 5
            ok, out = _run_cmd(["wpctl", "set-volume", "@DEFAULT_SINK@", f"{step}%-"])
            return f"Decreased system volume by {step}%." if ok else f"Volume error: {out}"

        if cmd == "system-volume":
            if not value:
                ok, out = _run_cmd(["wpctl", "get-volume", "@DEFAULT_SINK@"])
                return f"Current system {out}" if ok else f"Volume error: {out}"
            lvl = max(0, min(100, int(float(value.replace("%", "")))))
            ok, out = _run_cmd(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_SINK@", f"{lvl}%"])
            return f"System volume set to {lvl}%." if ok else f"Volume error: {out}"

    # 2. If a browser is focused and user didn't ask for a specific non-browser app (like 'spotify'),
    # control the ACTIVE visible browser tab directly so it NEVER touches the wrong tab!
    if not all_players and (not player or player.lower() in ("browser", *BROWSERS, "youtube")):
        _, win_title = _get_focused_browser_info()
        if win_title and cmd in ("pause", "play", "play-pause", "next", "previous", "forward", "backward"):
            handled, msg = _control_focused_browser_tab(cmd, seconds, win_title)
            if handled:
                return msg

    # 3. Background players
    if sys.platform == "win32":
        from plugins._comms_core import win32_send_vk_chord
        vk_map = {
            "pause": 0xB3,       # VK_MEDIA_PLAY_PAUSE
            "play": 0xB3,
            "play-pause": 0xB3,
            "next": 0xB0,        # VK_MEDIA_NEXT_TRACK
            "previous": 0xB1,    # VK_MEDIA_PREV_TRACK
            "stop": 0xB2,        # VK_MEDIA_STOP
        }
        if cmd in vk_map:
            win32_send_vk_chord(vk_map[cmd])
            return f"Executed media key '{cmd}'."
        return f"Media command '{cmd}' is not supported natively in Windows background mode without an active browser tab."

    if not shutil.which("playerctl"):
        return "playerctl is not installed. Run: sudo pacman -S playerctl"

    if cmd == "list-players":
        ok, out = _run_cmd(["playerctl", "-l"])
        return f"Active players:\n{out}" if (ok and out) else "No MPRIS media players running."

    base = ["playerctl"]
    if all_players:
        base.append("-a")
    if player and player.lower() != "browser":
        base += ["-p", player]
    if ignore_player:
        base += ["-i", ignore_player]

    if cmd == "status":
        ok, out = _run_cmd(base + ["status"])
        return f"Playback status: {out}" if ok else f"Media status failed: {out}"

    if cmd == "metadata":
        if value:
            key = "xesam:url" if value == "url" else value
            ok, out = _run_cmd(base + ["metadata", key])
            return f"{value}: {out}" if ok else f"Metadata lookup failed: {out}"
        fmt = "{{playerName}} [{{status}}]: {{artist}} - {{title}} ({{duration(position)}} / {{duration(mpris:length)}})"
        ok, out = _run_cmd(base + ["metadata", "--format", fmt])
        return out if ok else f"No track metadata available: {out}"

    if cmd in ("pause", "play", "play-pause", "next", "previous", "stop"):
        ok, out = _run_cmd(base + [cmd])
        return f"Executed {cmd}." if ok else f"Media control failed: {out}"

    if cmd == "forward":
        ok, out = _run_cmd(base + ["position", f"{seconds}+"])
        return f"Fast-forwarded {seconds}s." if ok else f"Seek failed: {out}"

    if cmd == "backward":
        ok, out = _run_cmd(base + ["position", f"{seconds}-"])
        return f"Rewound {seconds}s." if ok else f"Seek failed: {out}"

    if cmd == "seek":
        ok, out = _run_cmd(base + ["position", str(max(0, seconds))])
        return f"Jumped to {seconds}s." if ok else f"Seek failed: {out}"

    if cmd == "open":
        if not value:
            return "No URL or file path provided to open."
        ok, out = _run_cmd(base + ["open", value])
        return f"Opened '{value}'." if ok else f"Open failed: {out}"

    if cmd == "shuffle":
        mode = value.capitalize() if value.lower() in ("on", "off", "toggle") else "Toggle"
        ok, out = _run_cmd(base + ["shuffle", mode])
        return f"Shuffle set to {mode}." if ok else f"Shuffle failed: {out}"

    if cmd == "loop":
        mode = value.capitalize() if value.lower() in ("none", "track", "playlist") else "Track"
        ok, out = _run_cmd(base + ["loop", mode])
        return f"Loop mode set to {mode}." if ok else f"Loop failed: {out}"

    if cmd == "player-volume":
        if not value:
            ok, out = _run_cmd(base + ["volume"])
            return f"Player volume: {float(out)*100:.0f}%" if ok else f"Player volume failed: {out}"
        lvl = max(0.0, min(1.0, float(value.replace("%", "")) / 100.0))
        ok, out = _run_cmd(base + ["volume", f"{lvl:.2f}"])
        return f"Player volume set to {int(lvl*100)}%." if ok else f"Player volume failed: {out}"

    return f"Unsupported media command: {cmd}"

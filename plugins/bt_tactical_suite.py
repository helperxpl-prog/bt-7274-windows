#!/usr/bin/env python3
"""
BT-7274 Tactical Desktop Suite (EndeavourOS / KDE Plasma 6 / Wayland + PipeWire)
Universal run(*args, **kwargs) adapter compatible with any BT dispatcher signature.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import date, datetime
from pathlib import Path

_IS_WIN = sys.platform == "win32"


def _cmd(args: list[str], timeout: int = 8) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = (res.stdout or res.stderr or "").strip()
        return res.returncode == 0, out
    except FileNotFoundError:
        return False, f"Missing binary: {args[0]}"
    except subprocess.TimeoutExpired:
        return False, f"Command timed out: {' '.join(args)}"
    except Exception as exc:
        return False, str(exc)


APP_ALIASES: dict[str, list[str]] = (
    {
        "discord": ["Discord.exe"],
        "vesktop": ["Vesktop.exe"],
        "browser": ["chrome.exe"],
        "chrome": ["chrome.exe"],
        "edge": ["msedge.exe"],
        "firefox": ["firefox.exe"],
        "brave": ["brave.exe"],
        "terminal": ["wt.exe"],
        "cmd": ["cmd.exe"],
        "powershell": ["powershell.exe"],
        "files": ["explorer.exe"],
        "explorer": ["explorer.exe"],
        "steam": ["steam.exe"],
        "spotify": ["Spotify.exe"],
        "code": ["code.cmd"],
        "vscode": ["code.cmd"],
        "settings": ["ms-settings:"],
        "monitor": ["taskmgr.exe"],
        "calculator": ["calc.exe"],
        "notepad": ["notepad.exe"],
    }
    if _IS_WIN
    else {
        "discord": ["vesktop"],
        "vesktop": ["vesktop"],
        "browser": ["zen-browser"],
        "zen": ["zen-browser"],
        "firefox": ["firefox"],
        "brave": ["brave"],
        "chromium": ["chromium"],
        "terminal": ["konsole"],
        "konsole": ["konsole"],
        "files": ["dolphin"],
        "dolphin": ["dolphin"],
        "steam": ["steam"],
        "spotify": ["spotify"],
        "code": ["code"],
        "vscode": ["code"],
        "settings": ["systemsettings"],
        "monitor": ["plasma-systemmonitor"],
    }
)


def _win_notify(title: str, msg: str) -> None:
    try:
        from win10toast import ToastNotifier
        ToastNotifier().show_toast(title, msg, duration=5, threaded=True)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0x40)
        except Exception:
            pass


def _launch_app(target: str) -> str:
    key = target.strip().lower()
    if _IS_WIN:
        alias = APP_ALIASES.get(key)
        if alias and alias[0].endswith(":"):
            try:
                os.startfile(alias[0])
                return f"Affirmative. Opening {target}."
            except Exception as exc:
                return f"Failed to launch {target}: {exc}"
        exe = alias[0] if alias else target.strip()
        try:
            os.startfile(exe)
            return f"Affirmative. Launching {target}."
        except Exception:
            binary = shutil.which(exe) or shutil.which(key)
            if binary:
                subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return f"Affirmative. Launching {target}."
            return f"Negative, Pilot. Could not locate '{target}'."

    cmd = APP_ALIASES.get(key)
    if not cmd:
        binary = shutil.which(key)
        if not binary:
            for qdbus_bin in ("qdbus6", "qdbus"):
                if shutil.which(qdbus_bin):
                    try:
                        subprocess.Popen(
                            [qdbus_bin, "org.kde.krunner", "/App", "org.kde.krunner.App.query", target],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        return f"Affirmative. Searching for '{target}' via KRunner."
                    except Exception:
                        pass
            return f"Negative, Pilot. Could not locate executable '{target}'."
        cmd = [binary]
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"Affirmative. Launching {target}."
    except Exception as exc:
        return f"Failed to launch {target}: {exc}"


def _close_app(target: str) -> str:
    if _IS_WIN:
        key = target.strip().lower()
        alias = APP_ALIASES.get(key)
        exe = alias[0] if alias else (key if key.endswith(".exe") else f"{key}.exe")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        res = subprocess.run(["taskkill", "/IM", exe, "/F"], capture_output=True, text=True, creationflags=flags)
        return f"Target process '{target}' terminated." if res.returncode == 0 else f"No active process matching '{target}'."

    proc = "vesktop" if target.strip().lower() == "discord" else target.strip().lower()
    ok, _ = _cmd(["pkill", "-i", "-f", proc])
    return f"Target process '{target}' terminated." if ok else f"No active process matching '{target}'."


def _set_volume(val: str) -> str:
    if _IS_WIN:
        from plugins.media_control import _windows_volume
        v = val.strip().lower().replace("%", "")
        if v in {"mute", "unmute", "toggle"}:
            return _windows_volume("mute", {"mute": "1", "unmute": "0", "toggle": "toggle"}[v])
        if v.startswith("+"):
            return _windows_volume("volume-up", v[1:])
        if v.startswith("-"):
            return _windows_volume("volume-down", v[1:])
        if v.isdigit():
            return _windows_volume("system-volume", v)
        return "Specify a volume percentage (0-100), +10/-10, or mute/unmute."

    v = val.strip().lower().replace("%", "")
    if v in {"mute", "unmute", "toggle"}:
        flag = {"mute": "1", "unmute": "0", "toggle": "toggle"}[v]
        ok, out = _cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag])
        return f"Audio mute state set to {v}." if ok else f"Audio error: {out}"
    if v.startswith("+") or v.startswith("-"):
        sign, num = v[0], v[1:]
        if not num.isdigit():
            return "Invalid volume step."
        ok, out = _cmd(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", f"{num}%{sign}"])
        return f"Volume adjusted by {sign}{num}%." if ok else f"Audio error: {out}"
    if v.isdigit():
        pct = max(0, min(100, int(v)))
        ok, out = _cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{pct}%"])
        return f"Master audio output set to {pct}%." if ok else f"Audio error: {out}"
    return "Specify a volume percentage (0-100), +10/-10, or mute/unmute."


def _mic_control(state: str) -> str:
    v = state.strip().lower() or "toggle"
    flag = {"mute": "1", "unmute": "0", "toggle": "toggle"}.get(v, "toggle")
    ok, out = _cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", flag])
    return f"Comms microphone set to {v}." if ok else f"Mic error: {out}"


def _media_control(act: str) -> str:
    if _IS_WIN:
        from plugins.media_control import run as _mc_run
        a = act.strip().lower()
        cmd = "play-pause" if a == "toggle" else a
        return _mc_run({"command": cmd})

    a = act.strip().lower()
    mapping = {
        "play": ["playerctl", "play"],
        "pause": ["playerctl", "pause"],
        "toggle": ["playerctl", "play-pause"],
        "next": ["playerctl", "next"],
        "previous": ["playerctl", "previous"],
        "status": ["playerctl", "metadata", "--format", "{{status}}: {{artist}} - {{title}}"],
    }
    cmd = mapping.get(a)
    if not cmd:
        return "Valid media actions: play, pause, toggle, next, previous, status."
    ok, out = _cmd(cmd)
    if not ok:
        return "No active media stream detected."
    return out if a == "status" else f"Media command '{a}' executed."


def _telemetry() -> str:
    report: list[str] = []
    if _IS_WIN:
        try:
            import psutil
            mem = psutil.virtual_memory()
            report.append(f"RAM: {mem.used / (1024**3):.1f}/{mem.total / (1024**3):.1f} GB")
            bat = psutil.sensors_battery()
            if bat is not None:
                status = "Charging" if bat.power_plugged else "Discharging"
                report.append(f"Battery: {bat.percent:.0f}% ({status})")
        except Exception:
            pass
    else:
        try:
            meminfo = Path("/proc/meminfo").read_text()
            total_kb = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1))
            avail_kb = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1))
            report.append(f"RAM: {(total_kb - avail_kb) / (1024**2):.1f}/{total_kb / (1024**2):.1f} GB")
        except Exception:
            pass
        for bat in Path("/sys/class/power_supply").glob("BAT*"):
            report.append(f"Battery: {(bat / 'capacity').read_text().strip()}% ({(bat / 'status').read_text().strip()})")
            break
    if shutil.which("nvidia-smi"):
        ok, gpu = _cmd(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"])
        if ok and gpu:
            u, t = [x.strip() for x in gpu.split(",")]
            report.append(f"GPU: {u}% @ {t}°C")
    if not _IS_WIN and shutil.which("powerprofilesctl"):
        ok, prof = _cmd(["powerprofilesctl", "get"])
        if ok and prof:
            report.append(f"Mode: {prof}")
    if not _IS_WIN and shutil.which("nmcli"):
        ok, wifi = _cmd(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
        if ok:
            for line in wifi.splitlines():
                if line.startswith("yes:"):
                    report.append(f"Wi-Fi: {line.split(':', 1)[1]}")
                    break
    return " | ".join(report) if report else "All chassis systems nominal."


def _kde_action(act: str) -> str:
    a = act.strip().lower()
    if _IS_WIN:
        import ctypes
        if a == "lock":
            ctypes.windll.user32.LockWorkStation()
            return "Workstation locked, Pilot."
        if a == "sleep":
            ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
            return "Entering standby."
        if a == "overview":
            from plugins._comms_core import win32_send_vk_chord
            win32_send_vk_chord(0x5B, 0x09)  # Win+Tab
            return "Opened Windows Task View."
        return f"Action '{a}' is not available on Windows."

    if a == "lock":
        ok, _ = _cmd(["loginctl", "lock-session"])
        return "Workstation locked, Pilot." if ok else "Failed to lock session."
    if a == "sleep":
        ok, _ = _cmd(["systemctl", "suspend"])
        return "Entering standby." if ok else "Suspend failed."
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if not qdbus:
        return "qt6-tools (qdbus6) missing."
    cmds = {
        "overview": [qdbus, "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Overview"],
        "night_light": [qdbus, "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Toggle Night Color"],
        "dnd": [qdbus, "org.kde.kglobalaccel", "/component/plasmashell", "invokeShortcut", "toggle do not disturb"],
    }
    cmd = cmds.get(a)
    if not cmd:
        return "Valid KDE actions: overview, lock, sleep, night_light, dnd."
    ok, out = _cmd(cmd)
    return f"KDE action '{a}' executed." if ok else f"KDE error: {out}"


def _workout_tracker(mode: str, note: str = "") -> str:
    state_file = Path.home() / ".local" / "share" / "bt_workout_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    data = {"last_workout": "", "history": []}
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    if mode.strip().lower() == "log":
        data["last_workout"] = today_str
        data["history"].append({"date": today_str, "notes": note or "Calisthenics session"})
        state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return f"Workout logged for {today_str}. Tomorrow is a rest day, Pilot."

    if datetime.now().hour >= 23:
        return "Curfew active (past 23:00). Stand down and get sleep recovery."

    last = data.get("last_workout", "")
    if not last:
        return "No session logged yet. Today is cleared for training: incline push-ups, chair dips, backpack curls, planks."
    days = (date.today() - date.fromisoformat(last)).days
    if days == 0:
        return "Training already completed today. Focus on hydration and protein."
    if days == 1:
        return f"Last session was yesterday ({last}). Today is your 1-day rest cycle."
    return f"Recovery complete ({days} days since {last}). Today is a training day, Pilot."


PLUGIN = {
    "name": "bt_tactical_suite",
    "description": (
        "All-in-one EndeavourOS & KDE Plasma tactical controller for BT-7274. "
        "Supports launching/closing apps, PipeWire volume/mic mute, media playback, "
        "system telemetry (RAM, battery, RTX 3050 temp, Wi-Fi), brightness, power profiles, "
        "Wi-Fi/Bluetooth toggles, KDE actions (overview, lock, sleep, night_light, dnd), "
        "screenshots, Wayland clipboard, fast file search, tactical notes, timers, "
        "workspace presets (study, gaming, coding), and calisthenics rest-day tracking."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "One of: 'launch_app', 'close_app', 'preset', 'volume', 'mic', 'media', "
                    "'telemetry', 'brightness', 'power_profile', 'wifi', 'bluetooth', 'kde', "
                    "'screenshot', 'clipboard_get', 'clipboard_set', 'find_file', "
                    "'note_add', 'note_read', 'timer', 'search', 'workout_status', 'workout_log'."
                ),
            },
            "target": {
                "type": "STRING",
                "description": (
                    "App name, preset ('study'/'gaming'/'coding'), volume % ('50', '+10', 'mute'), "
                    "media action ('toggle', 'next', 'status'), brightness %, power mode, "
                    "'on'/'off', KDE action, file search query, minutes, or note text."
                ),
            },
        },
        "required": ["action"],
    },
}


def run(*args, **kwargs) -> str:
    """Accepts (params_dict, player, session_memory), (params_dict,), or (**kwargs)."""
    try:
        params: dict = {}
        if args and isinstance(args[0], dict):
            params.update(args[0])
        elif args and isinstance(args[0], str):
            params["action"] = args[0]
            if len(args) > 1 and isinstance(args[1], str):
                params["target"] = args[1]
        params.update(kwargs)

        act = str(params.get("action") or params.get("command") or "").strip().lower()
        val = str(params.get("target") or params.get("value") or params.get("query") or "").strip()

        if act == "launch_app":
            return _launch_app(val)
        if act == "close_app":
            return _close_app(val)
        if act == "preset":
            m = val.lower()
            if m == "study":
                webbrowser.open("https://www.ck12.org")
                def _study_done():
                    time.sleep(25 * 60)
                    if _IS_WIN:
                        _win_notify("BT-7274 // STUDY", "25-minute block complete, Pilot.")
                    else:
                        _cmd(["notify-send", "-u", "critical", "BT-7274 // STUDY", "25-minute block complete, Pilot."])
                threading.Thread(target=_study_done, daemon=True).start()
                return "Study protocol engaged: CK-12 opened and 25-minute timer active."
            if m == "gaming":
                if not _IS_WIN:
                    _cmd(["powerprofilesctl", "set", "performance"])
                _launch_app("steam")
                return "Gaming protocol engaged: Steam launching."
            if m == "coding":
                _launch_app("code")
                _launch_app("terminal" if _IS_WIN else "konsole")
                return "Engineering protocol engaged: VS Code and Terminal online."
            return "Valid presets: study, gaming, coding."
        if act == "volume":
            return _set_volume(val)
        if act == "mic":
            return _mic_control(val)
        if act == "media":
            return _media_control(val or "toggle")
        if act == "telemetry":
            return _telemetry()
        if act == "brightness":
            pct = max(5, min(100, int(re.sub(r"\D", "", val) or 50)))
            if _IS_WIN:
                try:
                    import wmi  # type: ignore
                    c = wmi.WMI(namespace="wmi")
                    methods = c.WmiMonitorBrightnessMethods()[0]
                    methods.WmiSetBrightness(pct, 0)
                    return f"Display luminance set to {pct}%."
                except Exception as exc:
                    return f"Brightness error: {exc}"
            ok, out = _cmd(["brightnessctl", "set", f"{pct}%"])
            return f"Display luminance set to {pct}%." if ok else f"Brightness error: {out}"
        if act == "power_profile":
            p = val.lower() if val.lower() in {"performance", "balanced", "power-saver"} else "balanced"
            ok, out = _cmd(["powerprofilesctl", "set", p])
            return f"Power mode set to {p}." if ok else f"Power error: {out}"
        if act == "wifi":
            s = "on" if val.lower() in {"on", "enable", "true", "1"} else "off"
            ok, out = _cmd(["nmcli", "radio", "wifi", s])
            return f"Wi-Fi turned {s}." if ok else f"Wi-Fi error: {out}"
        if act == "bluetooth":
            s = "on" if val.lower() in {"on", "enable", "true", "1"} else "off"
            ok, out = _cmd(["bluetoothctl", "power", s])
            return f"Bluetooth turned {s}." if ok else f"Bluetooth error: {out}"
        if act == "kde":
            return _kde_action(val)
        if act == "screenshot":
            out_dir = Path.home() / "Pictures" / "BT_Captures"
            out_dir.mkdir(parents=True, exist_ok=True)
            fp = out_dir / f"bt_capture_{int(time.time())}.png"
            if _IS_WIN:
                try:
                    import mss
                    import mss.tools
                    with mss.mss() as sct:
                        shot = sct.grab(sct.monitors[0])
                        mss.tools.to_png(shot.rgb, shot.size, output=str(fp))
                    return f"Visual capture saved to {fp}."
                except Exception as exc:
                    return f"Capture failed: {exc}"
            flag = {"fullscreen": "-f", "active": "-a", "region": "-r"}.get(val.lower(), "-f")
            ok, out = _cmd(["spectacle", "-b", "-n", flag, "-o", str(fp)], timeout=15)
            return f"Visual capture saved to {fp}." if ok else f"Capture failed: {out}"
        if act == "clipboard_get":
            if _IS_WIN:
                try:
                    import pyperclip
                    out = pyperclip.paste()
                    return out if out else "Clipboard is empty."
                except Exception as exc:
                    return f"Clipboard error: {exc}"
            ok, out = _cmd(["wl-paste", "--no-newline"])
            return out if ok and out else "Clipboard is empty."
        if act == "clipboard_set":
            if _IS_WIN:
                try:
                    import pyperclip
                    pyperclip.copy(val)
                    return "Copied to clipboard."
                except Exception as exc:
                    return f"Clipboard error: {exc}"
            subprocess.run(["wl-copy"], input=val, text=True, check=False)
            return "Copied to clipboard."
        if act == "find_file":
            if _IS_WIN:
                matches = []
                q_low = val.lower()
                for root, _, files in os.walk(Path.home()):
                    depth = len(Path(root).relative_to(Path.home()).parts)
                    if depth > 4:
                        continue
                    for fn in files:
                        if q_low in fn.lower():
                            matches.append(os.path.join(root, fn))
                            if len(matches) >= 6:
                                break
                    if len(matches) >= 6:
                        break
                return "\n".join(matches) if matches else f"No files matching '{val}' found."
            cmd = ["fd", "-i", "-H", "--max-results", "6", val, str(Path.home())] if shutil.which("fd") else ["find", str(Path.home()), "-maxdepth", "4", "-iname", f"*{val}*"]
            ok, out = _cmd(cmd)
            return out if ok and out else f"No files matching '{val}' found."
        if act == "note_add":
            nf = Path.home() / "Documents" / "bt_notes.md"
            nf.parent.mkdir(parents=True, exist_ok=True)
            with nf.open("a", encoding="utf-8") as f:
                f.write(f"- [{time.strftime('%Y-%m-%d %H:%M')}] {val}\n")
            return "Logged to ~/Documents/bt_notes.md."
        if act == "note_read":
            nf = Path.home() / "Documents" / "bt_notes.md"
            return nf.read_text(encoding="utf-8")[-1500:] if nf.exists() else "Tactical log is empty."
        if act == "timer":
            mins = float(re.sub(r"[^\d.]", "", val) or 5)
            def _timer_done():
                time.sleep(max(1, int(mins * 60)))
                if _IS_WIN:
                    _win_notify("BT-7274 // TIMER", f"{mins} minute timer complete, Pilot.")
                else:
                    _cmd(["notify-send", "-u", "critical", "BT-7274 // TIMER", f"{mins} minute timer complete, Pilot."])
            threading.Thread(target=_timer_done, daemon=True).start()
            return f"Countdown initialized for {mins} minute(s)."
        if act == "search":
            webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote_plus(val)}")
            return f"Opening browser search for '{val}'."
        if act == "workout_status":
            return _workout_tracker("status")
        if act == "workout_log":
            return _workout_tracker("log", val)

        return f"Unknown tactical action '{act}'."
    except Exception as exc:
        return f"Tactical suite error: {exc}"

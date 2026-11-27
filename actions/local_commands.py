"""
actions/local_commands.py — High-Speed Zero-Token Local Command Router for BT-7274 MARK LV.

Handles fast, repetitive commands directly in Python in < 5ms without calling the LLM API:
- Volume: mute, unmute, volume up/down, set volume to N%
- Media: play, pause, toggle, next song/track, previous song/track
- Desktop: show desktop, minimize all
- Gestures: gestures on/off, gesture profile <precision|balanced|snap|flick>
- System: system stats, battery, gpu stats, clear log
- Screen Glance: look at my screen, explain this error on screen
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("bt7274.local_commands")


def _run_cmd(cmd: list[str], timeout: float = 0.5) -> tuple[bool, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode == 0, (res.stdout or res.stderr or "").strip()
    except Exception as e:
        return False, str(e)


def _get_system_stats() -> Tuple[str, str]:
    """Gather quick CPU, RAM, Battery, and GPU metrics without heavy external libraries."""
    lines = []
    speech_parts = []

    if sys.platform == "darwin":
        # 1. CPU Load
        try:
            load = f"{os.getloadavg()[0]:.2f}"
            lines.append(f"CPU Load (1m): {load}")
            speech_parts.append(f"CPU load is {load}")
        except Exception:
            pass

        # 2. RAM
        try:
            ok, total_bytes_str = _run_cmd(["sysctl", "-n", "hw.memsize"])
            if ok and total_bytes_str.isdigit():
                total_bytes = int(total_bytes_str)
                total_gb = total_bytes / (1024**3)
                ok_vm, vm_out = _run_cmd(["vm_stat"])
                if ok_vm:
                    pages = {}
                    page_size = 4096
                    for line in vm_out.splitlines():
                        if "page size of" in line:
                            m = re.search(r"page size of (\d+) bytes", line)
                            if m:
                                page_size = int(m.group(1))
                        elif ":" in line:
                            k, v = line.split(":", 1)
                            digits = re.sub(r"[^\d]", "", v)
                            if digits:
                                pages[k.strip()] = int(digits)
                    free_pages = pages.get("Pages free", 0) + pages.get("Pages speculative", 0)
                    free_bytes = free_pages * page_size
                    used_bytes = max(0, total_bytes - free_bytes)
                    used_pct = int((used_bytes / total_bytes) * 100)
                    used_gb = used_bytes / (1024**3)
                    lines.append(f"RAM Usage: {used_pct}% ({used_gb:.1f} GB / {total_gb:.1f} GB)")
                    speech_parts.append(f"RAM is at {used_pct}%")
        except Exception:
            pass

        # 3. Battery
        try:
            ok, batt_out = _run_cmd(["pmset", "-g", "batt"])
            if ok and "%" in batt_out:
                m = re.search(r"(\d+)%", batt_out)
                if m:
                    cap = m.group(1)
                    state = "Charging" if "charging" in batt_out.lower() else "Discharging"
                    if "finishing charge" in batt_out.lower() or "charged" in batt_out.lower():
                        state = "Charged"
                    lines.append(f"Battery: {cap}% ({state})")
                    speech_parts.append(f"Battery is at {cap}% ({state})")
        except Exception:
            pass

        # 4. Apple Silicon / Mac Processor
        try:
            ok, chip_out = _run_cmd(["sysctl", "-n", "machdep.cpu.brand_string"])
            if ok and chip_out:
                lines.append(f"Apple Silicon / CPU: {chip_out}")
        except Exception:
            pass

    else:
        # 1. CPU & RAM (Linux)
        try:
            with open("/proc/loadavg", "r") as f:
                load = f.read().split()[0]
                lines.append(f"CPU Load (1m): {load}")
                speech_parts.append(f"CPU load is {load}")
        except Exception:
            pass

        try:
            with open("/proc/meminfo", "r") as f:
                mem = {}
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        mem[parts[0].strip()] = int(parts[1].split()[0])
                total = mem.get("MemTotal", 1)
                avail = mem.get("MemAvailable", 0)
                used_pct = int(((total - avail) / total) * 100)
                lines.append(f"RAM Usage: {used_pct}% ({((total - avail)/1024/1024):.1f} GB / {(total/1024/1024):.1f} GB)")
                speech_parts.append(f"RAM is at {used_pct}%")
        except Exception:
            pass

        # 2. Battery
        bat_path = Path("/sys/class/power_supply/BAT0") or Path("/sys/class/power_supply/BAT1")
        if not bat_path.exists():
            for p in Path("/sys/class/power_supply").glob("BAT*"):
                bat_path = p
                break
        if bat_path.exists() and (bat_path / "capacity").exists():
            try:
                cap = (bat_path / "capacity").read_text().strip()
                stat = (bat_path / "status").read_text().strip() if (bat_path / "status").exists() else "Discharging"
                lines.append(f"Battery: {cap}% ({stat})")
                speech_parts.append(f"Battery is at {cap}% ({stat})")
            except Exception:
                pass

        # 3. GPU (NVIDIA nvidia-smi if available)
        if shutil.which("nvidia-smi"):
            ok, out = _run_cmd([
                "nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits"
            ], timeout=0.8)
            if ok and out:
                try:
                    u, t, m_used, m_tot = [x.strip() for x in out.splitlines()[0].split(",")]
                    lines.append(f"NVIDIA GPU: {u}% util @ {t}°C | VRAM: {m_used} MB / {m_tot} MB")
                    speech_parts.append(f"GPU at {u}% utilization and {t} degrees Celsius")
                except Exception:
                    pass

    detail = "SYSTEM TELEMETRY REPORT:\n" + "\n".join(lines)
    speech = f"Pilot, system telemetry: {', '.join(speech_parts)}."
    return speech, detail


def handle_local_command(text: str, ui=None, bt=None) -> Optional[Tuple[str, str]]:
    """Inspect text for local zero-token patterns.
    Returns:
        (speech_response, log_detail) or None if not a local command.
    """
    raw = (text or "").strip()
    low = raw.lower()

    if not low:
        return None

    # ── 1. Screen Glance / Error Diagnosis ─────────────────────────────────
    if any(p in low for p in (
        "look at my screen", "read my screen", "what's on my screen", "whats on my screen",
        "glance at screen", "screen glance", "explain this error", "diagnose error",
        "diagnose this error", "check screen for error", "read the error on screen",
        "explain error on screen", "explain this error on screen"
    )):
        try:
            from core.screen_glance import execute_screen_glance
            speech, detail = execute_screen_glance(user_query=raw)
            return speech, detail
        except Exception as e:
            log.error("Screen glance failed: %e", e)
            return "Optic glance encountered an internal error, Pilot.", f"ERR: Glance failed: {e}"

    # ── 2. Volume & Audio Muting (Windows pycaw / macOS CoreAudio / Linux PipeWire wpctl)
    if low in ("mute", "silence", "mute audio", "mute sound", "mute volume"):
        if sys.platform == "win32":
            from plugins.media_control import _windows_volume
            _windows_volume("mute", "1")
            return "Master audio muted, Pilot.", "SYS: Master audio muted (Windows)."
        if sys.platform == "darwin":
            _run_cmd(["osascript", "-e", "set volume output muted true"])
            return "Master audio muted, Pilot.", "SYS: Master audio muted via CoreAudio / osascript."
        _run_cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"])
        return "Master audio muted, Pilot.", "SYS: Master audio sink muted via PipeWire."

    if low in ("unmute", "unmute audio", "unmute sound", "unmute volume"):
        if sys.platform == "win32":
            from plugins.media_control import _windows_volume
            _windows_volume("mute", "0")
            return "Master audio unmuted, Pilot.", "SYS: Master audio unmuted (Windows)."
        if sys.platform == "darwin":
            _run_cmd(["osascript", "-e", "set volume output muted false"])
            return "Master audio unmuted, Pilot.", "SYS: Master audio unmuted via CoreAudio / osascript."
        _run_cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"])
        return "Master audio unmuted, Pilot.", "SYS: Master audio sink unmuted via PipeWire."

    if low in ("volume up", "increase volume", "louder", "turn it up", "sound up"):
        if sys.platform == "win32":
            from plugins.media_control import _windows_volume
            _windows_volume("volume-up", "5")
            return "Master audio output increased by 5%, Pilot.", "SYS: Volume +5% (Windows)."
        if sys.platform == "darwin":
            _run_cmd(["osascript", "-e", "set volume output volume ((output volume of (get volume settings)) + 5)"])
            return "Master audio output increased by 5%, Pilot.", "SYS: Volume +5% (osascript)."
        _run_cmd(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "5%+"])
        return "Master audio output increased by 5%, Pilot.", "SYS: Volume +5% (wpctl)."

    if low in ("volume down", "decrease volume", "softer", "turn it down", "sound down", "lower volume"):
        if sys.platform == "win32":
            from plugins.media_control import _windows_volume
            _windows_volume("volume-down", "5")
            return "Master audio output decreased by 5%, Pilot.", "SYS: Volume -5% (Windows)."
        if sys.platform == "darwin":
            _run_cmd(["osascript", "-e", "set volume output volume ((output volume of (get volume settings)) - 5)"])
            return "Master audio output decreased by 5%, Pilot.", "SYS: Volume -5% (osascript)."
        _run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%-"])
        return "Master audio output decreased by 5%, Pilot.", "SYS: Volume -5% (wpctl)."

    vol_m = re.search(r"(?:set\s+volume(?:\s+to)?|volume)\s+(\d{1,3})\s*%?", low)
    if vol_m:
        val = max(0, min(100, int(vol_m.group(1))))
        if sys.platform == "win32":
            from plugins.media_control import _windows_volume
            _windows_volume("system-volume", str(val))
            return f"Master audio volume set to {val}%, Pilot.", f"SYS: Volume set to {val}% (Windows)."
        if sys.platform == "darwin":
            _run_cmd(["osascript", "-e", f"set volume output volume {val}"])
            return f"Master audio volume set to {val}%, Pilot.", f"SYS: Volume set to {val}% (osascript)."
        ratio = f"{val/100:.2f}"
        _run_cmd(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", ratio])
        return f"Master audio volume set to {val}%, Pilot.", f"SYS: Volume set to {val}% ({ratio})."

    # ── 3. Mood-Aware YouTube Mix & Playlist Launch ─────────────────────────
    if any(p in low for p in (
        "play my mix", "play my mixes", "my mix", "my mixes", "open my mix",
        "open my mixes", "play mix", "play a mix", "play me a mix", "mood mix",
        "play mood mix", "play something based on my mood", "play music based on my mood",
        "music based on my mood", "play based on my mood", "match my mood",
        "vibe mix", "youtube mix", "play youtube mix", "play playlist",
        "play a playlist", "play something chill", "play something hype",
        "play something upbeat", "play something to focus"
    )):
        try:
            from core.mood_detector import detect_mood
            from actions.youtube_video import _handle_mood_mix

            mood_res = detect_mood(text=raw)
            spoken_text = mood_res.spoken_announcement
            log_res = _handle_mood_mix({"query": raw}, player=ui, speak=None)
            return spoken_text, f"SYS: {log_res}"
        except Exception as e:
            log.error("Local mood mix launch failed: %s", e)
            return "Failed to launch mood mix, Pilot.", f"ERR: Mood mix failed: {e}"

    # ── 4. Media Controls (Windows VK_MEDIA / macOS AppleScript / Linux MPRIS playerctl)
    if low in ("media play", "media pause", "toggle media", "play music", "pause music", "pause song", "resume music", "toggle song"):
        if sys.platform == "win32":
            from plugins._comms_core import win32_send_vk_chord
            win32_send_vk_chord(0xB3)  # VK_MEDIA_PLAY_PAUSE
            return "Media playback toggled, Pilot.", "SYS: Media playback toggled (Windows)."
        if sys.platform == "darwin":
            try:
                from core.mac_input import send_media_control
                send_media_control("playpause")
            except Exception:
                pass
            return "Media playback toggled, Pilot.", "SYS: Media playback toggled (macOS)."
        if shutil.which("playerctl"):
            _run_cmd(["playerctl", "play-pause"])
        return "Media playback toggled, Pilot.", "SYS: Media playback toggled (playerctl play-pause)."

    if low in ("next song", "next track", "skip song", "skip track", "next video"):
        if sys.platform == "win32":
            from plugins._comms_core import win32_send_vk_chord
            win32_send_vk_chord(0xB0)  # VK_MEDIA_NEXT_TRACK
            return "Skipped to next track, Pilot.", "SYS: Media track skipped forward (Windows)."
        if sys.platform == "darwin":
            try:
                from core.mac_input import send_media_control
                send_media_control("next")
            except Exception:
                pass
            return "Skipped to next track, Pilot.", "SYS: Media track skipped forward (macOS)."
        if shutil.which("playerctl"):
            _run_cmd(["playerctl", "next"])
        return "Skipped to next track, Pilot.", "SYS: Media track skipped forward."

    if low in ("previous song", "prev song", "previous track", "prev track"):
        if sys.platform == "win32":
            from plugins._comms_core import win32_send_vk_chord
            win32_send_vk_chord(0xB1)  # VK_MEDIA_PREV_TRACK
            return "Returned to previous track, Pilot.", "SYS: Media track returned to previous (Windows)."
        if sys.platform == "darwin":
            try:
                from core.mac_input import send_media_control
                send_media_control("previous")
            except Exception:
                pass
            return "Returned to previous track, Pilot.", "SYS: Media track returned to previous (macOS)."
        if shutil.which("playerctl"):
            _run_cmd(["playerctl", "previous"])
        return "Returned to previous track, Pilot.", "SYS: Media track returned to previous."

    # ── 5. Desktop Navigation (Windows Win+D / macOS F11 / KDE Plasma 6) ─────
    if low in ("show desktop", "minimize all", "minimize all windows", "desktop view", "go to desktop"):
        if sys.platform == "win32":
            from plugins._comms_core import win32_send_vk_chord
            win32_send_vk_chord(0x5B, ord("D"))  # Win+D
            return "Desktop workspace displayed, Pilot.", "SYS: Desktop workspace shown (Windows Win+D)."
        if sys.platform == "darwin":
            _run_cmd(["osascript", "-e", 'tell application "System Events" to key code 103'])
            return "Desktop workspace displayed, Pilot.", "SYS: Desktop workspace shown (macOS F11)."
        handled = False
        if shutil.which("qdbus6"):
            ok, _ = _run_cmd(["qdbus6", "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Show Desktop"])
            if ok:
                handled = True
        if not handled and shutil.which("qdbus"):
            ok, _ = _run_cmd(["qdbus", "org.kde.kglobalaccel", "/component/kwin", "invokeShortcut", "Show Desktop"])
            if ok:
                handled = True
        if not handled and shutil.which("ydotool"):
            _run_cmd(["ydotool", "key", "125:1", "32:1", "32:0", "125:0"])
        return "Desktop workspace displayed, Pilot.", "SYS: Desktop workspace shown (Show Desktop)."

    # ── 5. Gesture Profiles & Controls ─────────────────────────────────────
    prof_m = re.search(r"(?:set\s+)?gesture\s+profile\s+(?:to\s+)?([a-zA-Z/]+)", low)
    if prof_m or low in ("precision gestures", "balanced gestures", "snap gestures", "flick gestures"):
        prof_name = prof_m.group(1).strip() if prof_m else low.split()[0]
        if prof_name in ("flick", "flicks"):
            prof_name = "snap"
        if ui and hasattr(ui, "set_gesture_profile"):
            ui.set_gesture_profile(prof_name)
        return f"Gesture sensitivity profile updated to {prof_name.upper()}, Pilot.", f"SYS: Gesture profile set to {prof_name.upper()}."

    if low in ("cycle gesture profile", "switch gesture profile", "next gesture profile", "toggle gesture profile"):
        applied = "BALANCED"
        if ui and hasattr(ui, "cycle_gesture_profile"):
            applied = ui.cycle_gesture_profile()
        return f"Gesture profile cycled to {str(applied).upper()}, Pilot.", f"SYS: Gesture profile cycled to {str(applied).upper()}."

    # ── 6. System Telemetry & Log Management ───────────────────────────────
    if low in ("system stats", "system status", "telemetry report", "hardware stats", "hardware status", "stats", "battery status", "battery level", "gpu stats", "gpu status"):
        speech, detail = _get_system_stats()
        return speech, detail

    if low in ("clear log", "clear logs", "clear activity log", "wipe log", "wipe activity log"):
        if ui and hasattr(ui, "clear_activity_log"):
            ui.clear_activity_log()
        elif ui and hasattr(ui, "_log") and hasattr(ui._log, "clear_log"):
            ui._log.clear_log()
        return "Activity log cleared, Pilot.", "SYS: Activity log cleared."

    return None

import time
import subprocess
import platform
import shutil
import os
import glob
import re
import shlex
from typing import Callable, Optional, Tuple

class LaunchResult(tuple):
    """Result tuple (success, message) that behaves as a boolean and a 2-tuple."""
    def __new__(cls, success: bool, message: str):
        return super().__new__(cls, (bool(success), str(message)))

    @property
    def success(self) -> bool:
        return self[0]

    @property
    def message(self) -> str:
        return self[1]

    def __bool__(self) -> bool:
        return bool(self[0])

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "zen":                {"Windows": "zen",                     "Darwin": "zen",                  "Linux": "zen-browser"},
    "zen browser":        {"Windows": "zen",                     "Darwin": "zen",                  "Linux": "zen-browser"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "browser":            {"Windows": "chrome",                  "Darwin": "Safari",               "Linux": "zen-browser" if shutil.which("zen-browser") else "firefox"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "whatsapp web":       {"Windows": "https://web.whatsapp.com", "Darwin": "https://web.whatsapp.com", "Linux": "whatsapp"},
    "web whatsapp":       {"Windows": "https://web.whatsapp.com", "Darwin": "https://web.whatsapp.com", "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "vesktop" if shutil.which("vesktop") else "discord"},
    "vesktop":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "vesktop"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "konsole" if shutil.which("konsole") else "x-terminal-emulator"},
    "konsole":            {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "konsole"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "dolphin" if shutil.which("dolphin") else "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "dolphin" if shutil.which("dolphin") else "nautilus"},
    "files":              {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "dolphin" if shutil.which("dolphin") else "nautilus"},
    "dolphin":            {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "dolphin"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "plasma-systemmonitor" if shutil.which("plasma-systemmonitor") else "gnome-system-monitor"},
    "system monitor":     {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "plasma-systemmonitor" if shutil.which("plasma-systemmonitor") else "gnome-system-monitor"},
    "monitor":            {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "plasma-systemmonitor" if shutil.which("plasma-systemmonitor") else "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Settings",      "Linux": "systemsettings" if shutil.which("systemsettings") else "gnome-control-center"},
    "system settings":    {"Windows": "ms-settings:",            "Darwin": "System Settings",      "Linux": "systemsettings" if shutil.which("systemsettings") else "gnome-control-center"},
    "system preferences": {"Windows": "ms-settings:",            "Darwin": "System Settings",      "Linux": "systemsettings" if shutil.which("systemsettings") else "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _launch_windows(app_name: str) -> bool:

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _find_macos_app(app_name: str) -> Optional[str]:
    """Search standard macOS application directories for matching .app bundles."""
    target = app_name.lower().strip()
    if target.endswith(".app"):
        target = target[:-4]

    search_dirs = [
        "/Applications",
        "/System/Applications",
        "/System/Applications/Utilities",
        "/Applications/Utilities",
        os.path.expanduser("~/Applications"),
    ]

    candidates = []
    for sdir in search_dirs:
        if not os.path.isdir(sdir):
            continue
        try:
            for item in os.listdir(sdir):
                if item.endswith(".app"):
                    base = item[:-4].lower()
                    full = os.path.join(sdir, item)
                    candidates.append((base, full))
        except Exception:
            pass

    # 1. Exact base match
    for base, full in candidates:
        if base == target:
            return full

    # 2. Starts with target
    for base, full in candidates:
        if base.startswith(target):
            return full

    # 3. Substring match
    for base, full in candidates:
        if target in base:
            return full

    return None


def _launch_macos(app_name: str) -> bool:
    """Launch an application on macOS via open -a, app path discovery, AppleScript, or Spotlight."""
    # 1. Try direct open -a <app_name>
    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    # 2. Search standard macOS app directories for matching .app bundle
    app_path = _find_macos_app(app_name)
    if app_path:
        try:
            result = subprocess.run(["open", app_path], capture_output=True, timeout=8)
            if result.returncode == 0:
                time.sleep(1.0)
                return True
        except Exception:
            pass

    # 3. Try AppleScript activate
    try:
        res = subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            capture_output=True,
            timeout=5,
        )
        if res.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    # 4. Check CLI binary on PATH
    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    # 5. Fallback: Spotlight search via Command+Space
    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


# ── Dedicated Open Handlers ──────────────────────────────────────────────────

def _open_whatsapp_dedicated(target: str) -> LaunchResult:
    """Dedicated handler for WhatsApp and WhatsApp Web (avoids duplicate browser/tabs)."""
    try:
        from core.whatsapp_comms import get_whatsapp_comms
        comms = get_whatsapp_comms()
        ok_f, status_f, speech_f = comms.window.focus_whatsapp_window()
        if ok_f:
            return LaunchResult(True, speech_f or "Brought existing WhatsApp window to focus.")
        ok_o, msg_o = comms.window.open_whatsapp_web()
        return LaunchResult(ok_o, msg_o or "Opened WhatsApp Web.")
    except Exception:
        try:
            import webbrowser
            webbrowser.open("https://web.whatsapp.com")
            return LaunchResult(True, "Opened WhatsApp Web in default browser.")
        except Exception as e:
            return LaunchResult(False, f"Failed to open WhatsApp Web: {e}")


def _open_url_dedicated(target: str) -> LaunchResult:
    """Dedicated handler for direct web URLs and web protocols."""
    url = target if target.startswith(("http://", "https://")) else f"https://{target}"
    try:
        import webbrowser
        webbrowser.open(url)
        return LaunchResult(True, f"Opened URL {url} in browser.")
    except Exception as e:
        return LaunchResult(False, f"Failed to open URL {url}: {e}")


_DEDICATED_OPEN_HANDLERS: dict[str, Callable[[str], LaunchResult]] = {
    "whatsapp": _open_whatsapp_dedicated,
    "whatsapp web": _open_whatsapp_dedicated,
    "web whatsapp": _open_whatsapp_dedicated,
    "whatsapp-web": _open_whatsapp_dedicated,
    "https://web.whatsapp.com": _open_whatsapp_dedicated,
}


def register_dedicated_handler(app_names: list[str] | str, handler: Callable[[str], LaunchResult]) -> None:
    """Register a dedicated application opening handler."""
    if isinstance(app_names, str):
        app_names = [app_names]
    for name in app_names:
        _DEDICATED_OPEN_HANDLERS[name.lower().strip()] = handler


def get_dedicated_handler(app_name: str) -> Optional[Callable[[str], LaunchResult]]:
    """Lookup a dedicated handler for the given app or target."""
    raw = (app_name or "").strip()
    low = raw.lower()
    if low in _DEDICATED_OPEN_HANDLERS:
        return _DEDICATED_OPEN_HANDLERS[low]
    if raw.startswith(("http://", "https://", "www.")):
        return _open_url_dedicated
    for key, handler in _DEDICATED_OPEN_HANDLERS.items():
        if key == low or (len(key) >= 4 and key in low):
            return handler
    return None


# ── KRunner Launch Flow (KDE Plasma) ─────────────────────────────────────────

def _clean_krunner_query(raw: str) -> str:
    """Sanitize and format application query for KRunner typing without stray characters."""
    if not raw:
        return ""
    q = raw.strip()
    # Strip quotes, backticks, brackets
    q = q.strip('"\'`')
    # Strip stray '%' or noise
    q = q.replace("%", "").strip()
    low = q.lower()
    # Check aliases for clean desktop name
    if low in _APP_ALIASES:
        linux_target = _APP_ALIASES[low].get("Linux", "")
        if linux_target and not linux_target.startswith("http"):
            if "--" in linux_target:
                return linux_target.replace("--", "")
            return linux_target
    return q


def _send_krunner_enter() -> bool:
    """Send Enter/Return key to KRunner with multi-tier fallback (ydotool -> xdotool -> pyautogui)."""
    # 1. Native Wayland ydotool (Key 28 = Enter)
    try:
        res = subprocess.run(
            ["ydotool", "key", "-d", "30", "28:1", "28:0"],
            capture_output=True,
            timeout=0.35,
        )
        if res.returncode == 0:
            return True
    except Exception:
        pass

    # 2. X11 / XWayland xdotool
    if shutil.which("xdotool"):
        try:
            res = subprocess.run(
                ["xdotool", "key", "Return"],
                capture_output=True,
                timeout=0.35,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass

    # 3. PyAutoGUI fallback
    try:
        import pyautogui
        pyautogui.press("enter")
        return True
    except Exception:
        pass

    return False


def _launch_krunner(app_name: str, confirm_enter: bool = True) -> LaunchResult:
    """Open/launch application through KDE KRunner.
    - Clean query typing (sanitized, unescaped)
    - Optional Enter/launch confirmation
    - Multi-tier D-Bus (qdbus6/qdbus) & CLI (krunner) execution
    """
    clean_query = _clean_krunner_query(app_name)
    if not clean_query:
        return LaunchResult(False, "Empty query for KRunner.")

    # Locate KRunner D-Bus tool or executable
    qdbus_bin = shutil.which("qdbus6") or shutil.which("qdbus")
    krunner_bin = shutil.which("krunner")

    if not qdbus_bin and not krunner_bin:
        return LaunchResult(False, "KRunner is not installed or available on this system.")

    krunner_opened = False

    # 1. Primary D-Bus method: org.kde.krunner.App.query
    if qdbus_bin:
        try:
            res = subprocess.run(
                [qdbus_bin, "org.kde.krunner", "/App", "org.kde.krunner.App.query", clean_query],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if res.returncode == 0:
                krunner_opened = True
        except Exception as e:
            print(f"[open_app] KRunner D-Bus query failed: {e}")

    # 2. Secondary CLI method: krunner <clean_query>
    if not krunner_opened and krunner_bin:
        try:
            subprocess.Popen(
                [krunner_bin, clean_query],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            krunner_opened = True
        except Exception as e:
            print(f"[open_app] KRunner CLI launch failed: {e}")

    if not krunner_opened:
        return LaunchResult(False, "Failed to send query to KRunner via D-Bus or CLI.")

    # If caller requested search display only (no Enter confirmation)
    if not confirm_enter:
        return LaunchResult(True, f"Opened KRunner search for '{clean_query}'.")

    # Wait briefly (~0.25s) for KRunner results to populate
    time.sleep(0.25)

    # Ensure KRunner window is active before sending Enter
    if shutil.which("kdotool"):
        try:
            w_res = subprocess.run(["kdotool", "search", "--class", "krunner"], capture_output=True, text=True, timeout=0.20)
            wids = [w.strip() for w in w_res.stdout.splitlines() if w.strip()]
            if wids:
                subprocess.run(["kdotool", "windowactivate", wids[0]], capture_output=True, timeout=0.20)
        except Exception:
            pass

    # Automatically press Enter to launch the top match cleanly
    sent = _send_krunner_enter()
    if not sent:
        return LaunchResult(False, "Failed to send Enter key confirmation to KRunner.")

    # Brief delay to allow launch to initiate cleanly
    time.sleep(0.25)
    return LaunchResult(True, f"Launched '{clean_query}' via KRunner.")


# ── Linux Fallback Launchers ─────────────────────────────────────────────────

_LINUX_TERMINAL_FALLBACKS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]


def _launch_linux_fallbacks(app_name: str, allow_menu: bool = False) -> LaunchResult:
    """Fallback launching methods when KRunner is unavailable or failed to launch."""
    q = app_name.strip().lower()

    # Fallback 1: Terminal emulators
    if q in ("x-terminal-emulator", "gnome-terminal", "terminal", "konsole"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    return LaunchResult(True, f"Opened terminal ({term}).")
                except Exception:
                    continue

    # Fallback 2: Check installed Flatpak apps directly (e.g., Sober -> org.vinegarhq.Sober)
    if shutil.which("flatpak"):
        try:
            res = subprocess.run(
                ["flatpak", "list", "--app", "--columns=name,application"],
                capture_output=True, text=True, timeout=3
            )
            if res.returncode == 0:
                exact_fp, partial_fp = None, None
                for line in res.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        fp_name, fp_id = parts[0].strip().lower(), parts[1].strip()
                        if q == fp_name or q == fp_id.lower() or fp_id.lower().endswith("." + q):
                            exact_fp = fp_id
                            break
                        elif q in fp_name and partial_fp is None:
                            partial_fp = fp_id
                chosen_fp = exact_fp or partial_fp
                if chosen_fp:
                    subprocess.Popen(
                        ["flatpak", "run", chosen_fp],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
                    )
                    return LaunchResult(True, f"Launched Flatpak app ({chosen_fp}).")
        except Exception:
            pass

    # Fallback 3: Scan all .desktop files across XDG_DATA_DIRS + Flatpak paths
    xdg_dirs = [os.path.join(d, "applications") for d in os.environ.get("XDG_DATA_DIRS", "").split(":") if d]
    search_dirs = list(dict.fromkeys([
        os.path.expanduser("~/.local/share/applications"),
        os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
        "/var/lib/flatpak/exports/share/applications",
        "/usr/share/applications",
        *xdg_dirs,
    ]))
    exact_id, partial_id = None, None
    exact_path, partial_path = None, None
    for d in search_dirs:
        for path in glob.glob(os.path.join(d, "*.desktop")):
            did = os.path.basename(path)
            stem = did[:-8].lower()
            if q == stem or stem.endswith("." + q):
                exact_id = did
                exact_path = path
                break
            try:
                with open(path, "r", errors="ignore") as f:
                    for line in f:
                        if line.startswith("Name="):
                            title = line.split("=", 1)[1].strip().lower()
                            if q == title:
                                exact_id = did
                                exact_path = path
                            elif q in title and partial_id is None:
                                partial_id = did
                                partial_path = path
                            break
            except OSError:
                continue
        if exact_id:
            break

    chosen_id = exact_id or partial_id
    chosen_path = exact_path or partial_path
    if chosen_id:
        for cmd in (["gtk-launch", chosen_id], ["kioclient", "exec", chosen_id]):
            if shutil.which(cmd[0]):
                try:
                    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    return LaunchResult(True, f"Launched desktop application ({chosen_id}).")
                except Exception:
                    continue
        if chosen_path and os.path.exists(chosen_path):
            try:
                with open(chosen_path, "r", errors="ignore") as f:
                    for line in f:
                        if line.startswith("Exec="):
                            raw_exec = line.split("=", 1)[1].strip()
                            clean_exec = re.sub(r"%[a-zA-Z]", "", raw_exec).strip()
                            if clean_exec:
                                subprocess.Popen(shlex.split(clean_exec), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                                return LaunchResult(True, f"Launched application from desktop entry ({chosen_id or app_name}).")
                            break
            except Exception:
                pass

    # Fallback 4: Direct binary on PATH
    binary = (
        shutil.which(app_name)
        or shutil.which(q)
        or shutil.which(q.replace(" ", "-"))
        or shutil.which(q.replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return LaunchResult(True, f"Launched binary ({binary}).")
        except Exception:
            pass

    # Fallback 5: Plasma Application Launcher menu (only if KRunner is unavailable on system)
    if allow_menu:
        qdbus_bin = shutil.which("qdbus6") or shutil.which("qdbus")
        if qdbus_bin:
            try:
                subprocess.Popen(
                    [qdbus_bin, "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.activateLauncherMenu"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                )
                return LaunchResult(True, "Opened Plasma Application Launcher menu.")
            except Exception:
                pass

    return LaunchResult(False, f"Could not find or launch '{app_name}' via fallbacks.")


def _launch_linux(app_name: str, confirm_enter: bool = True) -> LaunchResult:
    """Launch application on Linux / KDE Plasma.
    Uses dedicated open handler if one exists; otherwise always opens through KRunner.
    Falls back to desktop file / Flatpak / binary / launcher menu if KRunner is unavailable.
    """
    q = (app_name or "").strip()

    # 1. Dedicated open handler check
    handler = get_dedicated_handler(q)
    if handler is not None:
        try:
            res = handler(q)
            if res:
                return res
        except Exception as e:
            print(f"[open_app] Dedicated handler failed for '{q}': {e}")

    # 2. KDE Plasma: Launch via KRunner
    is_kde = (
        "kde" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        or "plasma" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        or shutil.which("krunner") is not None
        or shutil.which("qdbus6") is not None
    )

    if is_kde:
        res = _launch_krunner(q, confirm_enter=confirm_enter)
        if res:
            return res
        print(f"[open_app] KRunner launch failed or unavailable ({res.message}); trying native fallbacks...")

    # 3. Fallbacks if KRunner is not available or failed
    return _launch_linux_fallbacks(q, allow_menu=not is_kde)


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def focus_existing_app(app_name: str):
    """Check if the requested application is ALREADY running and open on desktop.
    If open, brings the existing window to the front instead of reopening a duplicate.
    """
    q = app_name.strip().lower()
    normalized = _normalize(app_name).lower()

    # Special handling for WhatsApp (checks dedicated window or open browser tab)
    if q in ("whatsapp", "whatsapp web", "web whatsapp", "whatsapp-web", "https://web.whatsapp.com"):
        try:
            from core.whatsapp_comms import get_whatsapp_comms
            comms = get_whatsapp_comms()
            ok, status_log, speech_msg = comms.window.focus_whatsapp_window()
            if ok:
                return speech_msg
            ok_open, msg_open = comms.window.open_whatsapp_web()
            return msg_open
        except Exception:
            pass

    # Build search terms for window class and title matching
    synonyms = {q, normalized}
    if "discord" in q or "discord" in normalized:
        synonyms.update(["vesktop", "discord"])
    if "vesktop" in q or "vesktop" in normalized:
        synonyms.update(["vesktop", "discord"])
    if "code" in q:
        synonyms.update(["code", "visual studio code", "vscodium"])
    if "terminal" in q or "console" in q:
        synonyms.update(["konsole", "terminal", "xterm", "kitty", "alacritty"])
    if "browser" in q or "web" in q:
        synonyms.update(["firefox", "chrome", "google-chrome", "chromium", "brave", "zen"])
    if "file" in q or "explorer" in q:
        synonyms.update(["dolphin", "nautilus", "explorer"])
    if "music" in q or "spotify" in q:
        synonyms.update(["spotify"])
    if "steam" in q:
        synonyms.update(["steam"])
    if "sober" in q:
        synonyms.update(["sober", "org.vinegarhq.sober"])

    # 1. Wayland / KDE Plasma 6 (kdotool)
    if shutil.which("kdotool"):
        try:
            res = subprocess.run(["kdotool", "search", ".*"], capture_output=True, text=True, timeout=0.35)
            wids = res.stdout.strip().splitlines()
            for wid in wids:
                wid = wid.strip()
                if not wid:
                    continue
                name = subprocess.run(["kdotool", "getwindowname", wid], capture_output=True, text=True, timeout=0.2).stdout.strip().lower()
                cls = subprocess.run(["kdotool", "getwindowclassname", wid], capture_output=True, text=True, timeout=0.2).stdout.strip().lower()
                for syn in synonyms:
                    if syn in cls or (len(syn) >= 3 and syn in name):
                        if any(bad in name for bad in ("search", "wa web plus")):
                            continue
                        # Found matching window -> activate it!
                        subprocess.run(["kdotool", "windowactivate", wid], capture_output=True, timeout=0.3)
                        return f"Brought existing {app_name} window to focus."
        except Exception:
            pass

    # 2. X11 (wmctrl)
    if shutil.which("wmctrl"):
        try:
            res = subprocess.run(["wmctrl", "-l", "-x"], capture_output=True, text=True, timeout=0.35)
            for line in res.stdout.splitlines():
                line_low = line.lower()
                for syn in synonyms:
                    if syn in line_low:
                        parts = line.split(None, 4)
                        if parts:
                            subprocess.run(["wmctrl", "-i", "-a", parts[0]], capture_output=True, timeout=0.3)
                            return f"Brought existing {app_name} window to focus."
        except Exception:
            pass

    # 3. Windows (ctypes Win32)
    if _SYSTEM == "Windows":
        try:
            import ctypes
            found_hwnd = None
            def enum_handler(hwnd, _):
                nonlocal found_hwnd
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                        title = buff.value.lower()
                        for syn in synonyms:
                            if syn in title:
                                found_hwnd = hwnd
                                return False
                return True

            CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
            ctypes.windll.user32.EnumWindows(CMPFUNC(enum_handler), 0)
            if found_hwnd:
                ctypes.windll.user32.ShowWindow(found_hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(found_hwnd)
                return f"Brought existing {app_name} window to focus."
        except Exception:
            pass

    # 4. macOS (AppleScript / System Events)
    if _SYSTEM == "Darwin":
        try:
            safe_targets = [q] + [s for s in synonyms if len(s) >= 3 and s != q]
            for target in safe_targets:
                clean_target = target.replace('"', '\\"')
                script = f'''
                tell application "System Events"
                    set procList to name of every process where background only is false
                    repeat with procName in procList
                        set pName to (procName as text)
                        set pLow to (procName as text)
                        ignoring case
                            if pLow contains "{clean_target}" or "{clean_target}" contains pLow then
                                tell application pName to activate
                                return pName
                            end if
                        end ignoring
                    end repeat
                end tell
                return ""
                '''
                res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=1.0)
                matched = res.stdout.strip()
                if matched:
                    return f"Brought existing {matched} window to focus."
        except Exception:
            pass

    return None

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    app_name = params.get("app_name", "").strip()
    confirm_enter = params.get("confirm_enter", True)
    if isinstance(confirm_enter, str):
        confirm_enter = confirm_enter.lower() not in ("false", "0", "no")

    if not app_name:
        return "No application name provided."

    # 1. ALWAYS check if application is ALREADY OPEN -> focus existing instance!
    already_open_msg = focus_existing_app(app_name)
    if already_open_msg:
        print(f"[open_app] Existing instance found for '{app_name}': {already_open_msg}")
        if player:
            player.write_log(f"[open_app] {already_open_msg}")
        return already_open_msg

    # 2. Only if NOT already open, launch a new instance
    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    if _SYSTEM == "Linux":
        res = _launch_linux(app_name, confirm_enter=confirm_enter)
        if res:
            return res.message or f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            res_n = _launch_linux(normalized, confirm_enter=confirm_enter)
            if res_n:
                return res_n.message or f"Opened {app_name}."
        return (
            f"Could not confirm that {app_name} launched via KRunner or desktop fallbacks: {res.message}"
        )

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    try:
        if launcher(normalized):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Opened {app_name}."
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "open_app",
    "description": (
        "Opens any application or program on the computer directly. On KDE Plasma Linux, uses dedicated open handlers if available (e.g. WhatsApp), "
        "and otherwise always opens/launches the app through KRunner with clean query typing and launch confirmation. "
        "Use this whenever the user asks to open, launch, or start any application or program. "
        "NEVER open browser links or navigate to websites when the user asks to open an app — only launch native apps directly or via KRunner."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "app_name": {
                "type": "STRING",
                "description": "Exact name of the application (e.g. 'Discord', 'Spotify', 'Steam', 'Chrome', 'WhatsApp', 'Sober', 'Dolphin')"
            },
            "confirm_enter": {
                "type": "BOOLEAN",
                "description": "Whether to press Enter in KRunner to automatically launch the top matching application. Defaults to true."
            }
        },
        "required": [
            "app_name"
        ]
    },
    "handler": open_app,
}

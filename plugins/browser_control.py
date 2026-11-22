import os
import shutil
import subprocess
import time

PLUGIN = {
    "name": "browser_tab_control",
    "description": (
        "Focuses the already-open web browser on KDE Wayland and switches tabs reliably: "
        "jump to tab 1-9 (including 'first' or 'last'), cycle next/previous tab, find an open tab "
        "by title keyword (e.g., 'youtube', 'github', 'discord'), open/close/reopen tabs, "
        "navigate to a URL/search, refresh, go back/forward, or type text."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "Action to perform: 'tab_number' (jump to specific tab 1-9, 'first', or 'last'), "
                    "'find_tab' (switch to an open tab matching 'value'), 'next_tab', 'prev_tab', "
                    "'new_tab', 'close_tab', 'reopen_tab', 'navigate', 'type_text', 'refresh', 'back', 'forward'."
                )
            },
            "value": {
                "type": "STRING",
                "description": (
                    "Tab position ('1'-'9', 'first', 'last') for 'tab_number'; "
                    "keyword in tab title (e.g. 'youtube') for 'find_tab'; "
                    "URL/search query for 'navigate'/'new_tab'; or text for 'type_text'."
                )
            },
            "press_enter": {
                "type": "BOOLEAN",
                "description": "Hit Enter after typing or navigating (default True)."
            }
        },
        "required": ["action"]
    }
}

NUM_KEYS = {
    "1": 2, "first": 2, "one": 2,
    "2": 3, "second": 3, "two": 3,
    "3": 4, "third": 4, "three": 4,
    "4": 5, "fourth": 5, "four": 5,
    "5": 6, "fifth": 6, "five": 6,
    "6": 7, "sixth": 7, "six": 7,
    "7": 8, "seventh": 8, "seven": 8,
    "8": 9, "eighth": 9, "eight": 9,
    "9": 10, "last": 10, "nine": 10,
}
BROWSERS = ("zen", "firefox", "brave", "chromium", "chrome", "floorp", "vivaldi")


def _cmd(args: list[str]) -> tuple[bool, str]:
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=3)
        return res.returncode == 0, (res.stdout or res.stderr or "").strip()
    except Exception as e:
        return False, str(e)


def _ydotool_env() -> dict:
    env = os.environ.copy()
    if "YDOTOOL_SOCKET" not in env:
        uid = os.getuid()
        for candidate in (f"/run/user/{uid}/.ydotool_socket", "/tmp/.ydotool_socket"):
            if os.path.exists(candidate):
                env["YDOTOOL_SOCKET"] = candidate
                break
    return env


def _keys(*combos: str):
    subprocess.run(["ydotool", "key", *combos], env=_ydotool_env(), check=True, timeout=3)


def _focus_browser() -> tuple[str, str]:
    target_wid = ""
    target_name = ""

    ok, out = _cmd(["kdotool", "search", "--name", ".*"])
    if ok and out:
        for wid in out.splitlines():
            wid = wid.strip()
            if not wid:
                continue
            t_ok, title = _cmd(["kdotool", "getwindowname", wid])
            if t_ok and title:
                for b in BROWSERS:
                    if b in title.lower():
                        target_wid, target_name = wid, b
                        break
            if target_wid:
                break

    if not target_wid:
        for b in BROWSERS:
            c_ok, c_out = _cmd(["kdotool", "search", "--class", b])
            if c_ok and c_out:
                target_wid, target_name = c_out.splitlines()[0].strip(), b
                break

    if not target_wid:
        return "", ""

    for _ in range(8):
        _cmd(["kdotool", "windowactivate", target_wid])
        time.sleep(0.12)
        a_ok, active = _cmd(["kdotool", "getactivewindow"])
        if a_ok and active.strip() == target_wid:
            time.sleep(0.15)
            break

    return target_name, target_wid


def _paste(text: str, enter: bool = True):
    subprocess.run(
        ["wl-copy", "--type", "text/plain;charset=utf-8"],
        input=text.encode("utf-8"),
        check=True,
        timeout=2,
    )
    time.sleep(0.12)
    _keys("29:1", "47:1", "47:0", "29:0")
    if enter:
        time.sleep(0.12)
        _keys("28:1", "28:0")


def _find_open_tab(wid: str, query: str) -> tuple[bool, str]:
    q = query.lower().strip()
    _, initial_title = _cmd(["kdotool", "getwindowname", wid])

    # If user explicitly says "first tab", jump straight to #1
    if q in NUM_KEYS:
        return False, ""

    # Step through open tabs (even if initial tab matches, step once so "switch to youtube tab"
    # moves to the OTHER youtube tab if you're already on one)
    seen_titles = [initial_title]
    for step in range(25):
        _keys("29:1", "109:1", "109:0", "29:0")  # Ctrl+PageDown
        time.sleep(0.28)
        _, current_title = _cmd(["kdotool", "getwindowname", wid])

        if q in current_title.lower():
            return True, f"Switched to open tab: '{current_title}'."

        # Only treat as full wrap-around if we've moved at least 2 steps and returned to initial_title
        if step >= 1 and current_title == initial_title:
            break
        seen_titles.append(current_title)

    # Fallback: if the initial tab was the ONLY matching tab, stay on it
    if q in initial_title.lower():
        return True, f"Already on the only matching tab: '{initial_title}'."

    return False, f"Could not find an open tab matching '{query}'."


def run(parameters: dict, **kwargs) -> str:
    for tool in ("ydotool", "wl-copy", "kdotool"):
        if not shutil.which(tool):
            return f"Missing '{tool}'. Install ydotool, wl-clipboard, and kdotool."

    params = parameters or {}
    action = str(params.get("action", "")).strip().lower()
    value = str(params.get("value", "")).strip()
    press_enter = bool(params.get("press_enter", True))

    browser, wid = _focus_browser()
    if not browser:
        return "No open browser window detected on the desktop, Pilot."

    is_gecko = browser in ("zen", "firefox", "floorp")

    try:
        # Handle "first tab", "tab 1", etc. even if the LLM passed action='find_tab' with value='first'
        val_clean = value.lower().replace("tab", "").strip()
        if action == "tab_number" or val_clean in NUM_KEYS:
            key_code = NUM_KEYS.get(val_clean)
            if not key_code:
                return "Specify a tab number from 1 to 9 (or 'first' / 'last')."
            # Alt+1..9 on Zen/Firefox (56=LAlt), Ctrl+1..9 on Chromium/Brave (29=LCtrl)
            mod = "56" if is_gecko else "29"
            _keys(f"{mod}:1", f"{key_code}:1", f"{key_code}:0", f"{mod}:0")
            time.sleep(0.2)
            _, new_title = _cmd(["kdotool", "getwindowname", wid])
            return f"Switched to tab {val_clean}: '{new_title}'."

        if action in ("find_tab", "search_tab"):
            if not value:
                return "Specify which open tab you want to switch to, Pilot."
            _, msg = _find_open_tab(wid, value)
            return msg

        if action == "next_tab":
            _keys("29:1", "109:1", "109:0", "29:0")
            time.sleep(0.2)
            _, new_title = _cmd(["kdotool", "getwindowname", wid])
            return f"Switched to next tab: '{new_title}'."

        if action == "prev_tab":
            _keys("29:1", "104:1", "104:0", "29:0")
            time.sleep(0.2)
            _, new_title = _cmd(["kdotool", "getwindowname", wid])
            return f"Switched to previous tab: '{new_title}'."

        if action == "new_tab":
            _keys("29:1", "20:1", "20:0", "29:0")
            if value:
                time.sleep(0.2)
                _paste(value, enter=press_enter)
            return f"Opened new tab in {browser}{f' -> {value}' if value else ''}."

        if action == "close_tab":
            _keys("29:1", "17:1", "17:0", "29:0")
            return "Closed current tab."

        if action == "reopen_tab":
            _keys("29:1", "42:1", "20:1", "20:0", "42:0", "29:0")
            return "Reopened last closed tab."

        if action == "navigate":
            if not value:
                return "Provide a URL or search query to navigate to."
            _keys("29:1", "38:1", "38:0", "29:0")
            time.sleep(0.2)
            _paste(value, enter=press_enter)
            return f"Navigated to '{value}'."

        if action == "type_text":
            if not value:
                return "No text provided to type."
            _paste(value, enter=press_enter)
            return f"Typed '{value}' into browser."

        if action == "refresh":
            _keys("29:1", "19:1", "19:0", "29:0")
            return "Refreshed page."

        if action == "back":
            _keys("56:1", "105:1", "105:0", "56:0")
            return "Went back one page."

        if action == "forward":
            _keys("56:1", "106:1", "106:0", "56:0")
            return "Went forward one page."

        return f"Unsupported browser action: {action}"
    except Exception as e:
        return f"Browser control error: {e}"

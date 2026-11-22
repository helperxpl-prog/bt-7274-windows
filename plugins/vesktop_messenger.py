import os
import shutil
import subprocess
import time

PLUGIN = {
    "name": "vesktop_messenger",
    "description": (
        "Reliably focuses or launches Vesktop on KDE Wayland, navigates to a friend's DM or server channel "
        "via Quick Switcher (or types directly into the currently open chat), and sends or drafts a message."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "recipient": {
                "type": "STRING",
                "description": (
                    "Friend's username, display name, or channel name (e.g., '@senetixgaming' or '#general'). "
                    "Leave empty or set to 'current' if the chat is already open."
                )
            },
            "message": {
                "type": "STRING",
                "description": "The exact message text to type into Vesktop."
            },
            "auto_send": {
                "type": "BOOLEAN",
                "description": "True to press Enter and send immediately; False to leave staged in the text box. Default True when Pilot says 'send' or 'tell'."
            }
        },
        "required": ["message"]
    }
}

# Add nicknames -> exact Discord usernames here so Quick Switcher always hits #1
ALIASES = {
    "senetix": "@senetixgaming",
    "senetixgaming": "@senetixgaming",
}


def _cmd(args: list[str], input_bytes: bytes | None = None, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        res = subprocess.run(
            args,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
        )
        out = (res.stdout or b"").decode("utf-8", errors="replace").strip()
        err = (res.stderr or b"").decode("utf-8", errors="replace").strip()
        return res.returncode == 0, out or err
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


def _find_vesktop_wid() -> str:
    # 1. Match by window class first (works even when title is a DM name like "@friend - Discord")
    for cls in ("vesktop", "Vesktop", "discord"):
        ok, out = _cmd(["kdotool", "search", "--class", cls])
        if ok and out:
            return out.splitlines()[0].strip()

    # 2. Fallback to window title search
    for name in ("Vesktop", "Discord"):
        ok, out = _cmd(["kdotool", "search", "--name", name])
        if ok and out:
            return out.splitlines()[0].strip()

    return ""


def _ensure_vesktop_focused() -> tuple[bool, str]:
    wid = _find_vesktop_wid()

    # If minimized to tray or not open, launch/unhide Vesktop
    if not wid:
        if not shutil.which("vesktop"):
            return False, "Vesktop is not running and 'vesktop' binary was not found."
        subprocess.Popen(["vesktop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(16):
            time.sleep(0.5)
            wid = _find_vesktop_wid()
            if wid:
                break
        if not wid:
            return False, "Launched Vesktop, but window did not appear in time."

    # Activate and wait until KWin actually reports Vesktop as the active window
    for _ in range(8):
        _cmd(["kdotool", "windowactivate", wid])
        time.sleep(0.15)
        ok, active_wid = _cmd(["kdotool", "getactivewindow"])
        if ok and active_wid.strip() == wid:
            time.sleep(0.15)  # Let Electron settle focus
            return True, wid

    return True, wid


def _type_text(text: str):
    """Direct fast typing via ydotool with ZERO clipboard usage."""
    if not text:
        return
    clean = "".join(ch for ch in text if ch.isascii() and ch.isprintable())
    if clean:
        subprocess.run(
            ["ydotool", "type", "-d", "1", "-H", "1", "--", clean],
            check=True,
            timeout=5,
        )


def run(parameters: dict, **kwargs) -> str:
    for tool in ("ydotool", "kdotool"):
        if not shutil.which(tool):
            return f"Missing '{tool}'. Install ydotool and kdotool."

    params = parameters or {}
    raw_recipient = str(params.get("recipient", "")).strip()
    message = str(params.get("message", "")).strip()
    auto_send = bool(params.get("auto_send", True))

    if not message:
        return "No message content provided, Pilot."

    try:
        ok, info = _ensure_vesktop_focused()
        if not ok:
            return info

        # Clear any lingering popups/modals in Vesktop first (Escape = 1)
        _keys("1:1", "1:0")
        time.sleep(0.12)

        use_quick_switcher = raw_recipient and raw_recipient.lower() not in ("current", "here", "this", "active")

        if use_quick_switcher:
            target = ALIASES.get(raw_recipient.lower(), raw_recipient)
            # Prefix '@' if user gave a plain username without '#' or '@' so Discord prioritizes DMs
            if not target.startswith(("@", "#", "!")):
                target = f"@{target}"

            # Open Quick Switcher: Ctrl+K (29=LCtrl, 37=K)
            _keys("29:1", "37:1", "37:0", "29:0")
            time.sleep(0.25)

            # Clear Quick Switcher input (Ctrl+A -> Backspace) just in case
            _keys("29:1", "30:1", "30:0", "29:0", "14:1", "14:0")
            time.sleep(0.08)

            _type_text(target)
            # Wait for Discord's fuzzy search list to filter before hitting Enter
            time.sleep(0.3)
            # Enter (28=Enter)
            _keys("28:1", "28:0")
            # Wait for chat history & message input box to mount and auto-focus
            time.sleep(0.35)

        # Type the actual message directly with zero clipboard
        _type_text(message)

        if auto_send:
            time.sleep(0.1)
            _keys("28:1", "28:0")
            status = f"Sent to {raw_recipient or 'active chat'} on Vesktop, Pilot."
        else:
            status = f"Staged message for {raw_recipient or 'active chat'} in Vesktop."

        return status

    except Exception as e:
        return f"Vesktop comms error: {e}"

    finally:
        if old_clip:
            subprocess.run(
                ["wl-copy", "--type", "text/plain;charset=utf-8"],
                input=old_clip.encode("utf-8"),
                stderr=subprocess.DEVNULL,
            )

import shutil
import subprocess
import sys
import time

PLUGIN = {
    "name": "vesktop_messenger",
    "description": "Focuses Vesktop on KDE Wayland, jumps to a friend or channel via Quick Switcher (Ctrl+K), and types a message.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "recipient": {
                "type": "STRING",
                "description": "Username or channel name to search in Vesktop Quick Switcher."
            },
            "message": {
                "type": "STRING",
                "description": "The exact text to type into the chat box."
            },
            "auto_send": {
                "type": "BOOLEAN",
                "description": "Whether to press Enter immediately after typing. Default False so Pilot can review."
            }
        },
        "required": ["recipient", "message"]
    }
}


def _paste(text: str, press_enter: bool = False):
    subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.1)
    # 29=LCtrl, 47=V
    subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True)
    if press_enter:
        time.sleep(0.1)
        # 28=Enter
        subprocess.run(["ydotool", "key", "28:1", "28:0"], check=True)


def run(parameters: dict, **kwargs) -> str:
    if sys.platform == "win32":
        from plugins.vesktop_messenger import run as _win_run
        return _win_run(parameters, **kwargs)

    for tool in ("ydotool", "wl-copy", "kdotool"):
        if not shutil.which(tool):
            return f"Missing dependency '{tool}'. Install ydotool, wl-clipboard, and kdotool."

    params = parameters or {}
    recipient = str(params.get("recipient", "")).strip()
    message = str(params.get("message", "")).strip()
    auto_send = bool(params.get("auto_send", False))

    try:
        res = subprocess.run(
            ["kdotool", "search", "--name", "Vesktop", "windowactivate"],
            capture_output=True, text=True
        )
        if res.returncode != 0 or not res.stdout.strip():
            return "Vesktop window not found on desktop, Pilot."
        time.sleep(0.4)

        # Ctrl+K (29=LCtrl, 37=K)
        subprocess.run(["ydotool", "key", "29:1", "37:1", "37:0", "29:0"], check=True)
        time.sleep(0.3)

        _paste(recipient, press_enter=True)
        time.sleep(0.5)

        _paste(message, press_enter=auto_send)
        if auto_send:
            return f"Transmission sent to {recipient}, Pilot."
        return f"Message staged for {recipient} in Vesktop. Ready for review, Pilot."
    except Exception as e:
        return f"Comms failure accessing Vesktop: {e}"

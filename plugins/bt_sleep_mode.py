"""
bt_sleep_mode.py - Low-Power Sleep / Standby Mode plugin for BT-7274.
Triggered ONLY when the pilot explicitly asks BT-7274 to enter standby or sleep.
"""
from __future__ import annotations
import threading

PLUGIN = {
    "name": "bt_sleep_mode",
    "description": (
        "Put BT-7274 into low-power standby mode. "
        "Trigger ONLY when the pilot explicitly asks the assistant BT-7274 itself to enter standby or go to sleep "
        "(e.g. 'BT standby', 'enter standby mode', 'go on standby', 'put BT on standby', 'stand by', 'BT go to sleep', 'sleep mode'). "
        "CRITICAL RESPONSE INSTRUCTION: When calling this tool or responding to entering standby mode, say ONLY 'standby mode active' and absolutely nothing else. "
        "Do NOT call this when the user asks to pause music, media, or video playback (use media_control for that). "
        "Do NOT call this for shutting down or exiting the application (use shutdown_bt for that)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "reason": {
                "type": "STRING",
                "description": "Optional reason for entering standby, e.g. 'pilot request'.",
            },
        },
        "required": [],
    },
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    reason = params.get("reason", "pilot request")
    handler = None
    if player:
        if hasattr(player, "enter_sleep_mode") and callable(player.enter_sleep_mode):
            handler = player.enter_sleep_mode
        elif hasattr(player, "_win") and hasattr(player._win, "enter_sleep_mode") and callable(player._win.enter_sleep_mode):
            handler = player._win.enter_sleep_mode
        elif hasattr(player, "set_sleep_mode") and callable(player.set_sleep_mode):
            handler = lambda **kw: player.set_sleep_mode(True)

    if handler:
        threading.Thread(
            target=handler,
            kwargs={"reason": reason, "from_voice_tool": True},
            daemon=True,
            name="bt-enter-sleep"
        ).start()
    return "standby mode active"

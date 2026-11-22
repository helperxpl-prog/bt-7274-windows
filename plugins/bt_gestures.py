"""
bt_gestures.py - Real-Time Webcam Hand Gesture & Pilot Optic PIP Controller plugin for BT-7274.
Triggered when the pilot asks to enable, disable, or toggle webcam hand gestures or the PIP video feed.
"""
from __future__ import annotations

PLUGIN = {
    "name": "bt_gestures",
    "description": (
        "Control webcam hand gesture tracking. Call this IMMEDIATELY when the user asks to "
        "enable, start, turn on, disable, stop, turn off, or toggle hand gestures or gesture controls "
        "(e.g. 'turn on gestures', 'turn gestures on', 'enable gestures', 'gestures on', 'start gesture tracking', "
        "'stop gestures', 'turn off gestures', 'turn gestures off', 'disable gestures', 'gestures off')."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: 'enable' (turn on), 'disable' (turn off), or 'toggle'.",
            },
            "target": {
                "type": "STRING",
                "description": "Target: 'gestures' (default) or 'pip_feed'.",
            },
        },
        "required": [],
    },
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    raw_text = " ".join(str(v).lower() for v in params.values())
    action = str(params.get("action", "")).lower().strip()
    target = str(params.get("target", "gestures")).lower().strip()

    target_state = None
    if any(k in raw_text or k in action for k in ("disable", "turn off", "off", "stop", "deactivate", "false", "close")):
        target_state = False
    elif any(k in raw_text or k in action for k in ("enable", "turn on", "on", "start", "activate", "true", "open")):
        target_state = True

    if any(k in target or k in raw_text for k in ("pip_feed", "pip", "webcam_feed", "video_feed", "feed")):
        if player and hasattr(player, "ui") and hasattr(player.ui, "toggle_webcam_pip"):
            res = player.ui.toggle_webcam_pip(target_state)
            state_str = "ONLINE" if res else "OFFLINE"
            return f"Pilot optic PIP video feed is now {state_str}, Pilot."
        elif player and hasattr(player, "toggle_webcam_pip"):
            res = player.toggle_webcam_pip(target_state)
            state_str = "ONLINE" if res else "OFFLINE"
            return f"Pilot optic PIP video feed is now {state_str}, Pilot."

    # Gesture control target
    handler = None
    if player and hasattr(player, "toggle_gestures") and callable(player.toggle_gestures):
        handler = player.toggle_gestures
    elif player and hasattr(player, "ui") and hasattr(player.ui, "toggle_gestures") and callable(player.ui.toggle_gestures):
        handler = player.ui.toggle_gestures
    elif player and hasattr(player, "_win") and hasattr(player._win, "toggle_gestures") and callable(player._win.toggle_gestures):
        handler = player._win.toggle_gestures

    if handler:
        res = handler(target_state)
        final_state = target_state if target_state is not None else res
        state_str = "ACTIVE" if final_state else "OFFLINE"
        return f"Webcam gesture tracking is now {state_str}, Pilot."

    return "Gesture tracking interface unavailable."

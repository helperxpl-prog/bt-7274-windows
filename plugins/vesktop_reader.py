"""
vesktop_reader.py - Vesktop (Discord) Send-Only & Reply plugin for BT-7274.

Strictly Send & Reply only: reading chats is disabled for privacy and operational security.
Voice Chat (VC) audio piping is omitted per pilot directive.

Flow: focus window -> [Ctrl+K Quick Switcher -> type target -> Enter] -> type message -> Enter.
If no `target` is given, the message is sent directly into whichever chat is currently open.
"""
from __future__ import annotations

import logging

from plugins._comms_core import AbortedError, CommsError, TacticalCommsEngine, get_engine

log = logging.getLogger("bt7274.vesktop")

PLUGIN = {
    "name": "vesktop_send_message",
    "description": (
        "Send a Discord message through the Vesktop app, optionally to a specific "
        "channel or user chosen via the Quick Switcher, or reply to the currently active chat. "
        "Strictly send and reply only: reading chats is disabled for pilot privacy."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "target": {
                "type": "STRING",
                "description": (
                    "Channel or user to open first (Quick Switcher search text). "
                    "Leave empty to send directly to the currently open chat."
                ),
            },
            "message": {
                "type": "STRING",
                "description": "The message text to send.",
            },
            "action": {
                "type": "STRING",
                "description": (
                    "'send' (default) sends the message. 'search' only opens the Quick "
                    "Switcher and types the target name, without selecting or sending."
                ),
            },
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
}

WINDOW_CLASSES = ("vesktop",)
WINDOW_NAMES = ("Vesktop", "Discord")

SEARCH_SHORTCUT = "ctrl+k"
RESET_WITH_ESC = True

FOCUS_SETTLE = 0.5
AFTER_SWITCHER_OPEN = (0.55, 0.85)
SWITCHER_RESULT_WAIT = (1.4, 2.0)
AFTER_SWITCH = (0.8, 1.1)

_SEND_ACTIONS = {"send", "message", "msg", "text", "dm", "reply", "respond"}
_SEARCH_ACTIONS = {"search", "find", "lookup"}
_READ_ACTIONS = {"read", "fetch", "check", "get"}


def _send(engine: TacticalCommsEngine, target: str, message: str,
          shortcut: str, search_only: bool = False) -> None:
    with engine.session():
        engine.focus_window(WINDOW_NAMES, WINDOW_CLASSES, settle=FOCUS_SETTLE)
        engine.pause(0.15, 0.30)

        if target:
            if RESET_WITH_ESC:
                engine.press_keys("esc")
                engine.pause(0.20, 0.35)
            engine.press_keys(shortcut)                # Quick Switcher
            engine.pause(*AFTER_SWITCHER_OPEN)
            engine.clear_field()
            engine.pause(0.10, 0.20)
            engine.human_type(target, pace=1.2)
            if search_only:
                return                                 # leave the switcher open on screen
            engine.pause(*SWITCHER_RESULT_WAIT)
            engine.press_enter()
            engine.pause(*AFTER_SWITCH)

        engine.human_type(message)
        engine.pause(0.25, 0.55)
        engine.press_enter()


def execute(action="send", target="", message="", **kwargs) -> str:
    """Core logic. Always returns a status string; never raises."""
    action = (action or "send").strip().lower()

    if action in _READ_ACTIONS:
        return "Pilot, Discord chat reading is disabled for your privacy. I am authorized only to send messages or reply."

    search_only = action in _SEARCH_ACTIONS
    if not search_only and action not in _SEND_ACTIONS:
        return f"Vesktop plugin is send-only; unsupported action '{action}', Pilot."

    target = " ".join(
        str(target or kwargs.get("channel") or kwargs.get("user")
            or kwargs.get("contact") or kwargs.get("to") or "").split()
    )
    message = str(message or kwargs.get("text") or kwargs.get("body") or "").strip()
    shortcut = str(kwargs.get("search_shortcut") or SEARCH_SHORTCUT)

    if search_only and not target:
        return "Vesktop: no target specified to search for, Pilot."
    if not search_only and not message:
        return "Vesktop: no message specified to send, Pilot."

    try:
        _send(get_engine(), target, message, shortcut, search_only)
        if search_only:
            return f"Typed '{target}' into the Vesktop Quick Switcher, Pilot."
        where = target if target else "the currently open chat"
        return f"Message dispatched to {where} on Vesktop, Pilot."
    except AbortedError:
        return "Vesktop send aborted, Pilot."
    except CommsError as exc:
        log.error("Vesktop send failed: %s", exc)
        return f"Vesktop send failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - plugin boundary, must not crash assistant
        log.exception("Unexpected Vesktop plugin error")
        return f"Vesktop plugin error: {exc}"


def run(parameters, player=None, session_memory=None) -> str:
    """Entrypoint called by core/plugin_loader.py."""
    params = {str(k): v for k, v in (parameters or {}).items()}
    return execute(**params)

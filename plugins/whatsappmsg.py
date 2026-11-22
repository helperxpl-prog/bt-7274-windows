"""
whatsappmsg.py - WhatsApp (Desktop / Web) Comm-Link plugin for BT-7274.

Capabilities:
- Open WhatsApp Web (launch in default browser or bring existing window to focus)
- Locate WhatsApp window across browsers (Firefox, Chrome, Brave, Zen) and desktop wrappers
- Send messages to contacts (with smart self-contact resolution: 'myself' -> 'You')
- Reply to the currently active chat
- Strictly Send & Reply only: reading chats is disabled for privacy and operational security.
"""
from __future__ import annotations

import logging
from typing import Optional

from core.whatsapp_comms import get_whatsapp_comms, resolve_contact

log = logging.getLogger("bt7274.whatsapp")

PLUGIN = {
    "name": "whatsapp_send_message",
    "description": (
        "Control WhatsApp: open WhatsApp Web in the browser, locate an open WhatsApp window, "
        "send a message to a contact (or to yourself), or reply to the currently active chat. "
        "Strictly send and reply only: reading chats is disabled for pilot privacy."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "'open' (or 'launch') opens WhatsApp Web in the browser or focuses it. "
                    "'locate' (or 'find') locates WhatsApp if open in a browser/desktop window and focuses it. "
                    "'send' (default) searches for the contact and sends the message. "
                    "'reply' sends the message directly into the currently active WhatsApp chat. "
                    "'search' opens WhatsApp search and types the contact name without sending."
                ),
            },
            "contact": {
                "type": "STRING",
                "description": (
                    "Name of the contact or group. "
                    "CRITICAL: When the user asks to message 'themselves', 'myself', 'me', or 'you', "
                    "pass contact='You' because WhatsApp names the self-chat 'You' (Message yourself)."
                ),
            },
            "message": {
                "type": "STRING",
                "description": "The message text to send or reply with.",
            },
        },
        "required": [],
    },
    "behavior": "NON_BLOCKING",
}

_OPEN_ACTIONS = {"open", "launch", "start", "web", "load"}
_LOCATE_ACTIONS = {"locate", "find", "focus", "show", "switch"}
_SEND_ACTIONS = {"send", "message", "msg", "text", "dm"}
_REPLY_ACTIONS = {"reply", "respond", "answer"}
_SEARCH_ACTIONS = {"search", "lookup"}
_READ_ACTIONS = {"read", "fetch", "check", "get"}


def execute(action="send", contact="", message="", **kwargs) -> str:
    """Core logic. Always returns a status string; never raises."""
    action = (action or "send").strip().lower()

    if action in _READ_ACTIONS:
        return "Pilot, WhatsApp chat reading is disabled for your privacy. I am only authorized to send messages or reply to active chats."

    contact = " ".join(
        str(contact or kwargs.get("target") or kwargs.get("name") or kwargs.get("to") or "").split()
    )
    message = str(message or kwargs.get("text") or kwargs.get("body") or "").strip()
    shortcut = str(kwargs.get("search_shortcut") or "ctrl+alt+slash")

    # Resolve self-referential contacts ('myself', 'me', 'self', 'you' -> 'You')
    contact, is_self = resolve_contact(contact)

    comms = get_whatsapp_comms()

    try:
        if action in _OPEN_ACTIONS:
            return comms.open_whatsapp()

        if action in _LOCATE_ACTIONS:
            return comms.locate_whatsapp()

        if action in _REPLY_ACTIONS:
            if not message:
                return "WhatsApp: no message specified to reply with, Pilot."
            if contact:
                return comms.send_message(contact, message, search_shortcut=shortcut)
            return comms.reply_message(message)

        if action in _SEARCH_ACTIONS:
            if not contact:
                return "WhatsApp: no contact specified to search, Pilot."
            return comms.search_contact(contact, search_shortcut=shortcut)

        if action in _SEND_ACTIONS:
            if not contact:
                return "WhatsApp: no contact specified to send message to, Pilot."
            if not message:
                return "WhatsApp: no message specified to send, Pilot."
            return comms.send_message(contact, message, search_shortcut=shortcut)

        return f"WhatsApp plugin: unsupported action '{action}', Pilot."

    except Exception as exc:  # noqa: BLE001 - plugin boundary, must not crash assistant
        log.exception("Unexpected WhatsApp plugin error")
        return f"WhatsApp plugin error: {exc}"


def run(parameters, player=None, session_memory=None) -> str:
    """Entrypoint called by core/plugin_loader.py."""
    params = {str(k): v for k, v in (parameters or {}).items()}
    return execute(**params)

"""
core/whatsapp_comms.py — BT-7274 Native WhatsApp Comm-Link Subsystem

Architecture:
1. Smart Multi-Tier Lookup (Zero Duplicate Spawning):
   - Tier 1: Check active desktop windows and processes on EndeavourOS (KDE Plasma 6 / Wayland / X11)
     for any window with WhatsApp title or class (WhatsApp, web.whatsapp.com, zapzap, whatsie).
   - Tier 2: Multi-Browser Background Tab Scan (Firefox recovery.jsonlz4, Chrome/Chromium/Brave/Edge Tabs_*).
     If found in a background browser tab, activates the browser and uses native tab-switching
     (Firefox: Ctrl+L -> 'whatsapp' -> Down -> Enter; Chrome: Ctrl+Shift+A -> 'whatsapp' -> Enter)
     instead of spawning a duplicate tab.
   - Tier 3: Clean Launch Fallback (ONLY if truly not open anywhere). Launches web.whatsapp.com
     or native wrapper and polls briefly until focused.
2. Intelligent Self-Contact Resolution:
   - Maps 'myself', 'me', 'self', 'you', 'my number', 'notes to self' to WhatsApp's native 'You'.
   - Down-arrow result selection prevents accidental misfires into previously open chats.
3. Tactical Telemetry & Safe Error Reporting:
   - Standardized status logs: WHATSAPP: FOCUSED EXISTING, WHATSAPP: LAUNCHED, or clear diagnostics.
4. Privacy & Security by Design:
   - Strictly send/reply/open only. Message reading and chat interception are disabled.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Optional, Tuple

log = logging.getLogger("bt7274.whatsapp_comms")


def resolve_contact(contact: str) -> Tuple[str, bool]:
    """Intelligently resolve contact names.
    Returns:
        (resolved_name, is_self_chat)
    """
    raw = (contact or "").strip()
    if not raw:
        return "", False

    low = raw.lower()

    # Strip conversational prefixes
    for prefix in (
        "message to ", "send to ", "send a message to ", "message ",
        "text to ", "text ", "dm to ", "dm ", "to ", "contact "
    ):
        if low.startswith(prefix):
            raw = raw[len(prefix):].strip()
            low = raw.lower()
            break

    # Strip conversational suffixes
    for suffix in (" on whatsapp", " in whatsapp", " via whatsapp", " whatsapp"):
        if low.endswith(suffix):
            raw = raw[:-len(suffix)].strip()
            low = raw.lower()
            break

    # Self / Assistant targets -> Map to WhatsApp's official native self-chat "You"
    SELF_SYNONYMS = {
        "myself", "me", "self", "you", "yourself",
        "my number", "my own number", "my own chat", "my chat",
        "notes to self", "saved messages", "message myself",
        "to myself", "to me", "to self", "to you",
        "bt", "bt-7274", "bt7274", "assistant", "ai",
        "you/myself", "myself/you"
    }

    if low in SELF_SYNONYMS or "myself" in low or "message myself" in low:
        return "You", True

    return raw, False


class WhatsAppWindowController:
    """Controls WhatsApp desktop/web windows for safe messaging, replies, and window management."""

    WINDOW_CLASSES = (
        "whatsapp", "zapzap", "whatsie", "chrome-web.whatsapp.com__-Default",
    )
    WINDOW_NAMES = ("WhatsApp", ".*[Ww]hats[Aa]pp.*", "web.whatsapp.com")

    def __init__(self):
        pass

    @staticmethod
    def _is_valid_whatsapp_title(name: str) -> bool:
        """Verify window title belongs to genuine WhatsApp Web/Desktop (not a search engine or extension)."""
        if not name:
            return False
        name_low = name.lower()
        if not ("whatsapp" in name_low or "web.whatsapp.com" in name_low):
            return False
        # Reject search results, browser search engines, extensions (e.g. WA Web Plus for WhatsApp - Search)
        if any(bad in name_low for bad in ("search", "wa web plus", "google", "duckduckgo", "bing", "yahoo")):
            return False
        return True

    # ── Tier 1: Check Active Windows & Processes ────────────────────────────

    def find_active_whatsapp_window(self) -> Tuple[bool, str, str]:
        """Find an already-open window displaying WhatsApp via kdotool, wmctrl, xdotool, or Win32.
        Returns:
            (found, window_id, window_title)
        """
        # 1. KDE Plasma 6 Wayland (kdotool)
        if shutil.which("kdotool"):
            try:
                # Query all active windows directly
                res_all = subprocess.run(
                    ["kdotool", "search", ".*"],
                    capture_output=True, text=True, timeout=0.30
                )
                for line in res_all.stdout.strip().splitlines():
                    wid = line.strip()
                    if not wid:
                        continue
                    name_res = subprocess.run(["kdotool", "getwindowname", wid], capture_output=True, text=True, timeout=0.15)
                    cls_res = subprocess.run(["kdotool", "getwindowclassname", wid], capture_output=True, text=True, timeout=0.15)
                    name = name_res.stdout.strip()
                    cls = cls_res.stdout.strip().lower()

                    # Match dedicated native wrapper class OR verified genuine title
                    if any(c in cls for c in ("whatsapp", "zapzap", "whatsie", "chrome-web.whatsapp.com__-default")):
                        if not any(bad in name.lower() for bad in ("search", "wa web plus")):
                            return True, wid, name
                    elif self._is_valid_whatsapp_title(name):
                        return True, wid, name
            except Exception as e:
                log.debug("kdotool find_active_whatsapp_window error: %s", e)

        # 2. X11 / Wayland xdotool
        if shutil.which("xdotool"):
            try:
                res = subprocess.run(
                    ["xdotool", "search", "--name", "(?i)whatsapp"],
                    capture_output=True, text=True, timeout=0.25
                )
                for line in res.stdout.strip().splitlines():
                    wid = line.strip()
                    if wid:
                        name_res = subprocess.run(["xdotool", "getwindowname", wid], capture_output=True, text=True, timeout=0.2)
                        name = name_res.stdout.strip()
                        if self._is_valid_whatsapp_title(name):
                            return True, wid, name
            except Exception:
                pass

        # 3. X11 wmctrl
        if shutil.which("wmctrl"):
            try:
                res = subprocess.run(
                    ["wmctrl", "-l", "-x"],
                    capture_output=True, text=True, timeout=0.25
                )
                for line in res.stdout.splitlines():
                    parts = line.split(None, 4)
                    if parts and len(parts) > 4:
                        title = parts[4]
                        if self._is_valid_whatsapp_title(title):
                            return True, parts[0], title
            except Exception:
                pass

        # 4. Windows (ctypes Win32)
        if sys.platform == "win32":
            try:
                import ctypes
                found_hwnd = None
                found_title = ""

                def enum_handler(hwnd, _):
                    nonlocal found_hwnd, found_title
                    if ctypes.windll.user32.IsWindowVisible(hwnd):
                        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                        if length > 0:
                            buff = ctypes.create_unicode_buffer(length + 1)
                            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                            title = buff.value
                            if self._is_valid_whatsapp_title(title):
                                found_hwnd = hwnd
                                found_title = title
                                return False
                    return True

                CMPFUNC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
                ctypes.windll.user32.EnumWindows(CMPFUNC(enum_handler), 0)
                if found_hwnd:
                    return True, str(found_hwnd), found_title
            except Exception:
                pass

        return False, "", ""

    # ── Tier 2: Check Open Browser Tabs (Firefox / Chrome / Brave) ─────────

    def find_browser_whatsapp_tab(self) -> Tuple[bool, str, str]:
        """Scan open browser sessions for background WhatsApp tabs.
        Returns:
            (found, browser_type, browser_window_id)
        """
        # 1. Check Firefox session recovery stores
        firefox_patterns = [
            os.path.expanduser("~/.config/mozilla/firefox/*/sessionstore-backups/recovery.jsonlz4"),
            os.path.expanduser("~/.config/mozilla/firefox/*/sessionstore-backups/recovery.baklz4"),
            os.path.expanduser("~/.config/mozilla/firefox/*/sessionstore-backups/previous.jsonlz4"),
            os.path.expanduser("~/.mozilla/firefox/*/sessionstore-backups/*.jsonlz4"),
        ]
        firefox_has_whatsapp = False
        for pat in firefox_patterns:
            for f in glob.glob(pat):
                try:
                    with open(f, "rb") as fp:
                        data = fp.read()
                        if b"web.whatsapp.com" in data:
                            firefox_has_whatsapp = True
                            break
                except Exception:
                    pass
            if firefox_has_whatsapp:
                break

        if firefox_has_whatsapp:
            wid = self._find_browser_window_id("firefox")
            if wid:
                return True, "firefox", wid

        # 2. Check Chromium / Google Chrome / Brave / Edge session stores
        chromium_patterns = [
            os.path.expanduser("~/.config/google-chrome/*/Sessions/Tabs_*"),
            os.path.expanduser("~/.config/chromium/*/Sessions/Tabs_*"),
            os.path.expanduser("~/.config/BraveSoftware/Brave-Browser/*/Sessions/Tabs_*"),
            os.path.expanduser("~/.config/microsoft-edge/*/Sessions/Tabs_*"),
        ]
        for pat in chromium_patterns:
            for f in glob.glob(pat):
                try:
                    with open(f, "rb") as fp:
                        data = fp.read()
                        if b"web.whatsapp.com" in data or b"https://web.whatsapp.com" in data:
                            b_type = "brave" if "Brave" in f else ("chrome" if "google-chrome" in f else "chromium")
                            wid = self._find_browser_window_id(b_type)
                            if wid:
                                return True, b_type, wid
                except Exception:
                    pass

        return False, "", ""

    def _find_browser_window_id(self, browser_name: str) -> Optional[str]:
        """Locate the window ID of an open browser."""
        b_low = browser_name.lower()
        if shutil.which("kdotool"):
            try:
                res = subprocess.run(["kdotool", "search", ".*"], capture_output=True, text=True, timeout=0.25)
                for wid in res.stdout.strip().splitlines():
                    wid = wid.strip()
                    if not wid:
                        continue
                    cls = subprocess.run(["kdotool", "getwindowclassname", wid], capture_output=True, text=True, timeout=0.15).stdout.strip().lower()
                    name = subprocess.run(["kdotool", "getwindowname", wid], capture_output=True, text=True, timeout=0.15).stdout.strip().lower()
                    if b_low in cls or b_low in name:
                        return wid
            except Exception:
                pass

        if shutil.which("wmctrl"):
            try:
                res = subprocess.run(["wmctrl", "-l", "-x"], capture_output=True, text=True, timeout=0.25)
                for line in res.stdout.splitlines():
                    if b_low in line.lower():
                        parts = line.split(None, 4)
                        if parts:
                            return parts[0]
            except Exception:
                pass

        return None

    def _activate_window_id(self, wid: str, settle: float = 0.25) -> bool:
        """Activate and raise a specific window ID."""
        if not wid:
            return False

        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(wid)
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
                time.sleep(settle)
                return True
            except Exception:
                pass

        if shutil.which("kdotool"):
            try:
                res = subprocess.run(["kdotool", "windowactivate", wid], capture_output=True, timeout=0.35)
                if res.returncode == 0:
                    time.sleep(settle)
                    return True
            except Exception:
                pass

        if shutil.which("wmctrl"):
            try:
                res = subprocess.run(["wmctrl", "-i", "-a", wid], capture_output=True, timeout=0.35)
                if res.returncode == 0:
                    time.sleep(settle)
                    return True
            except Exception:
                pass

        if shutil.which("xdotool"):
            try:
                res = subprocess.run(["xdotool", "windowactivate", wid], capture_output=True, timeout=0.35)
                if res.returncode == 0:
                    time.sleep(settle)
                    return True
            except Exception:
                pass

        return False

    # ── Master Focus Flow: Tier 1 -> Tier 2 (Zero Duplicate Tabs) ──────────

    def focus_whatsapp_window(self, settle: float = 0.30) -> Tuple[bool, str, str]:
        """Focus WhatsApp if open in an active window OR background browser tab.
        Returns:
            (success, tactical_status_log, user_speech_message)
        """
        # Step 1: Check active desktop windows displaying WhatsApp
        found, wid, title = self.find_active_whatsapp_window()
        if found:
            self._activate_window_id(wid, settle=settle)
            status_log = f"WHATSAPP: FOCUSED EXISTING [Window: '{title}']"
            speech_msg = f"WhatsApp located in window '{title}' and brought to focus, Pilot."
            log.info(status_log)
            return True, status_log, speech_msg

        # Step 2: Check background browser tabs and switch tab cleanly via direct tab cycling
        has_tab, b_type, b_wid = self.find_browser_whatsapp_tab()
        if has_tab and b_wid:
            self._activate_window_id(b_wid, settle=settle)
            # Direct tab cycling via Ctrl+PageDown (KEY_LEFTCTRL=29, KEY_PAGEDOWN=109).
            # This completely bypasses the address bar, preventing stray characters ('%')
            # and avoiding extension/search hijacking (e.g. WA Web Plus).
            try:
                if sys.platform == "win32":
                    import pyautogui
                    for step in range(25):
                        found_step, wid_step, title_step = self.find_active_whatsapp_window()
                        if found_step:
                            status_log = f"WHATSAPP: FOCUSED EXISTING [Switched {b_type.title()} Tab: '{title_step}']"
                            speech_msg = f"WhatsApp located in your open {b_type.title()} tab; switched and focused, Pilot."
                            log.info(status_log)
                            return True, status_log, speech_msg
                        pyautogui.hotkey("ctrl", "pagedown")
                        time.sleep(0.18)
                else:
                    from plugins._comms_core import get_engine
                    engine = get_engine()
                    with engine.session():
                        for step in range(25):
                            found_step, wid_step, title_step = self.find_active_whatsapp_window()
                            if found_step:
                                status_log = f"WHATSAPP: FOCUSED EXISTING [Switched {b_type.title()} Tab: '{title_step}']"
                                speech_msg = f"WhatsApp located in your open {b_type.title()} tab; switched and focused, Pilot."
                                log.info(status_log)
                                return True, status_log, speech_msg
                            # Step to next tab (Ctrl+PageDown: 29=Ctrl, 109=PageDown)
                            engine._ydotool("key", "29:1", "109:1", "109:0", "29:0")
                            time.sleep(0.18)
            except Exception as e:
                log.warning("Tab cycle attempt encountered error: %s", e)

        return False, "WHATSAPP: NOT OPEN", "WhatsApp does not appear to be open in any browser or desktop window, Pilot."

    # Backwards-compatible alias for existing callers
    def focus_window(self, settle: float = 0.30) -> Tuple[bool, str]:
        ok, status_log, speech_msg = self.focus_whatsapp_window(settle=settle)
        return ok, speech_msg

    def locate_whatsapp(self) -> Tuple[bool, str]:
        """Locate WhatsApp and bring it to focus."""
        ok, status_log, speech_msg = self.focus_whatsapp_window()
        return ok, speech_msg

    # ── Tier 3: Smart Launch Fallback (ONLY if truly not open anywhere) ──────

    def open_whatsapp_web(self) -> Tuple[bool, str]:
        """Open WhatsApp Web, ONLY launching a new tab if truly not open anywhere."""
        # 1. ALWAYS check existing windows & tabs first -> focus if found!
        ok, status_log, speech_msg = self.focus_whatsapp_window()
        if ok:
            return True, speech_msg

        # 2. Check for native Linux desktop wrapper / Flatpak before opening browser
        for bin_name in ("whatsapp-for-linux", "zapzap", "whatsie", "whatsapp"):
            path = shutil.which(bin_name)
            if path:
                try:
                    subprocess.Popen([path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                    time.sleep(1.8)
                    ok_new, _, msg_new = self.focus_whatsapp_window(settle=0.20)
                    status_log = f"WHATSAPP: LAUNCHED [Native: {bin_name}]"
                    log.info(status_log)
                    return True, f"Launched {bin_name} desktop client, Pilot."
                except Exception:
                    pass

        # 3. Launch WhatsApp Web in default browser
        url = "https://web.whatsapp.com"
        try:
            webbrowser.open(url)
        except Exception as e:
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                return False, f"Could not launch browser for WhatsApp Web: {e}"

        # 4. Wait briefly (up to 2.5s) for new window/tab to appear and register focus
        for _ in range(8):
            time.sleep(0.30)
            ok_new, _, msg_new = self.focus_whatsapp_window(settle=0.15)
            if ok_new:
                status_log = f"WHATSAPP: LAUNCHED [Target: web.whatsapp.com]"
                log.info(status_log)
                return True, "WhatsApp Web launched in your browser and brought to focus, Pilot."

        status_log = "WHATSAPP: LAUNCHED [Target: web.whatsapp.com]"
        log.info(status_log)
        return True, "WhatsApp Web launched in your browser, Pilot. Please verify your login or scan the QR code."

    # ── Send & Reply Automation ─────────────────────────────────────────────

    def send_message(
        self,
        contact: str,
        message: str,
        search_shortcut: str = "ctrl+alt+slash",
        search_only: bool = False,
        auto_open: bool = True,
    ) -> Tuple[bool, str]:
        """Search for a contact and send a message (or just search)."""
        resolved_contact, is_self = resolve_contact(contact)
        message = (message or "").strip()

        if not resolved_contact:
            return False, "WhatsApp error: No contact specified, Pilot."
        if not search_only and not message:
            return False, "WhatsApp error: No message body provided, Pilot."

        # Check existing windows & tabs first
        ok, status_log, speech_msg = self.focus_whatsapp_window(settle=0.25)
        if not ok:
            if auto_open:
                self.open_whatsapp_web()
                time.sleep(1.2)
                ok, _, _ = self.focus_whatsapp_window(settle=0.25)
                if not ok:
                    return False, "WhatsApp Web has been launched in your browser, Pilot. Please ensure you are logged in, then repeat your transmission."
            else:
                return False, "WhatsApp window not found. Please say 'Open WhatsApp' or open WhatsApp Web in your browser, Pilot."

        from plugins._comms_core import AbortedError, CommsError, get_engine
        try:
            engine = get_engine()
            with engine.session():
                engine.focus_window(self.WINDOW_NAMES, self.WINDOW_CLASSES, settle=0.25)
                engine.pause(0.15, 0.25)

                # Reset UI state with Esc
                engine.press_keys("esc")
                engine.pause(0.15, 0.25)

                # Focus search box
                engine.press_keys(search_shortcut)
                engine.pause(0.25, 0.40)
                engine.clear_field()
                engine.pause(0.10, 0.20)

                # Type contact query (e.g. "You" for self)
                engine.human_type(resolved_contact, pace=1.2)

                if search_only:
                    label = "yourself ('You')" if is_self else resolved_contact
                    return True, f"Typed '{label}' into WhatsApp search, Pilot."

                # Allow WhatsApp Web asynchronous search filter to render
                engine.pause(0.70, 1.05)

                # Move selection into top search result card
                engine.press_keys("down")
                engine.pause(0.20, 0.35)

                # Open selected chat
                engine.press_enter()
                engine.pause(0.50, 0.75)

                # Type message
                engine.human_type(message, pace=1.0)
                engine.pause(0.25, 0.45)

                # Send message
                engine.press_enter()

            destination = "yourself (Notes to Self)" if is_self else resolved_contact
            return True, f"Message dispatched to {destination} on WhatsApp, Pilot."
        except AbortedError:
            return False, "WhatsApp transmission was aborted, Pilot."
        except CommsError as e:
            return False, f"WhatsApp transmission failed: {e}"
        except Exception as e:
            return False, f"WhatsApp comms error: {e}"

    def reply_to_active(self, message: str, auto_open: bool = True) -> Tuple[bool, str]:
        """Type and send into the currently active WhatsApp chat composer."""
        message = (message or "").strip()
        if not message:
            return False, "WhatsApp reply error: Message body is empty, Pilot."

        # Check existing windows & tabs first
        ok, status_log, speech_msg = self.focus_whatsapp_window(settle=0.20)
        if not ok:
            if auto_open:
                self.open_whatsapp_web()
                return False, "WhatsApp Web has been opened in your browser, Pilot. Please select the chat you wish to reply to."
            return False, "WhatsApp window not found. Say 'Open WhatsApp' to launch WhatsApp Web, Pilot."

        from plugins._comms_core import AbortedError, CommsError, get_engine
        try:
            engine = get_engine()
            with engine.session():
                engine.focus_window(self.WINDOW_NAMES, self.WINDOW_CLASSES, settle=0.20)
                engine.pause(0.15, 0.25)
                engine.human_type(message, pace=1.0)
                engine.pause(0.20, 0.35)
                engine.press_enter()
            return True, "Reply transmitted to active WhatsApp chat, Pilot."
        except AbortedError:
            return False, "WhatsApp reply was aborted, Pilot."
        except CommsError as e:
            return False, f"WhatsApp reply failed: {e}"
        except Exception as e:
            return False, f"WhatsApp reply error: {e}"


# Singleton comm-link service instance
_COMM_LINK_INSTANCE: Optional[WhatsAppCommLink] = None


class WhatsAppCommLink:
    """BT-7274 Native WhatsApp Comm-Link Subsystem."""

    def __init__(self):
        self.window = WhatsAppWindowController()

    def open_whatsapp(self) -> str:
        ok, status = self.window.open_whatsapp_web()
        return status

    def locate_whatsapp(self) -> str:
        ok, status = self.window.locate_whatsapp()
        return status

    def read_messages(self, contact: Optional[str] = None, limit: int = 5) -> str:
        """Chat reading is disabled per pilot directives."""
        return "Pilot, WhatsApp chat reading is disabled for privacy and operational security. I am only authorized to send transmissions or reply."

    def send_message(self, contact: str, message: str, search_shortcut: str = "ctrl+alt+slash") -> str:
        ok, status = self.window.send_message(contact, message, search_shortcut=search_shortcut, search_only=False)
        return status

    def search_contact(self, contact: str, search_shortcut: str = "ctrl+alt+slash") -> str:
        ok, status = self.window.send_message(contact, "", search_shortcut=search_shortcut, search_only=True)
        return status

    def reply_message(self, message: str) -> str:
        ok, status = self.window.reply_to_active(message)
        return status


def get_whatsapp_comms() -> WhatsAppCommLink:
    global _COMM_LINK_INSTANCE
    if _COMM_LINK_INSTANCE is None:
        _COMM_LINK_INSTANCE = WhatsAppCommLink()
    return _COMM_LINK_INSTANCE

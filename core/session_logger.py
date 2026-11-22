"""
core/session_logger.py — Structured Rotating Session Logger for BT-7274 MARK LV.

Writes rotating logs to ~/.local/share/bt7274/logs/bt7274.log (5 MB max, 3 backups).
Ensures safe directory creation, clean formatting, and proper flush/cleanup on shutdown.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

if sys.platform == "darwin":
    _LOG_DIR = Path.home() / "Library" / "Application Support" / "BT-7274" / "logs"
elif sys.platform == "win32":
    _LOG_DIR = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "BT-7274" / "logs"
else:
    _LOG_DIR = Path.home() / ".local" / "share" / "bt7274" / "logs"

_LOG_FILE = _LOG_DIR / "bt7274.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3

_INITIALIZED = False
_FILE_HANDLER: Optional[RotatingFileHandler] = None


def get_log_dir() -> Path:
    """Return the base directory for BT-7274 logs, creating it if needed."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[SessionLogger] ⚠️ Failed creating log dir {_LOG_DIR}: {e}")
    return _LOG_DIR


def setup_session_logger(name: str = "bt7274", level: int = logging.INFO) -> logging.Logger:
    """Configure or retrieve the rotating file logger for BT-7274."""
    global _INITIALIZED, _FILE_HANDLER

    log_dir = get_log_dir()
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not _INITIALIZED:
        try:
            log_path = log_dir / "bt7274.log"
            _FILE_HANDLER = RotatingFileHandler(
                str(log_path),
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
                delay=True,
            )
            formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            _FILE_HANDLER.setFormatter(formatter)
            _FILE_HANDLER.setLevel(level)

            # Attach to root logger so all child modules (core, actions, plugins) write to it
            root_log = logging.getLogger()
            # Avoid duplicate handlers if re-run
            if not any(isinstance(h, RotatingFileHandler) for h in root_log.handlers):
                root_log.addHandler(_FILE_HANDLER)

            _INITIALIZED = True
            logger.info("═" * 60)
            logger.info("BT-7274 MARK LV Session Logger Initialized. Logfile: %s", log_path)
            logger.info("═" * 60)
        except Exception as e:
            print(f"[SessionLogger] ⚠️ Failed setting up rotating file handler: {e}")

    return logger


def flush_logs() -> None:
    """Flush and close active log handlers cleanly before process exit."""
    global _FILE_HANDLER
    try:
        if _FILE_HANDLER:
            _FILE_HANDLER.flush()
            _FILE_HANDLER.close()
    except Exception:
        pass

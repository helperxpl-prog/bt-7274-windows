#!/usr/bin/env python3
"""
BT-7274 Windows Standalone Build Script
Compiles BT-7274 into a standalone Windows executable distribution without bundling secrets,
.venv, or cache directories, and without leaving raw .py source files exposed.

Usage:
    python build_windows.py [--compiler nuitka|pyinstaller] [--clean]
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BUILD_DIR = ROOT_DIR / "build"
DIST_DIR = ROOT_DIR / "dist"
APP_NAME = "BT-7274"
APP_DIST = DIST_DIR / APP_NAME
ICON_PATH = ROOT_DIR / "config" / "bt-7274.ico"


def log(msg: str):
    print(f"[Build] {msg}", flush=True)


def clean_build_artifacts():
    log("Cleaning previous build artifacts...")
    for d in (BUILD_DIR, DIST_DIR):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    for p in ROOT_DIR.glob("*.spec"):
        try:
            p.unlink()
        except OSError:
            pass


def stage_non_secret_assets(target_dir: Path):
    """
    Copies required data assets (prompts, static html, icons, models) to the distribution
    folder while strictly excluding secrets (api_keys.json), .venv, and cache files.
    """
    log("Staging non-secret application assets...")

    # 1. Config directory
    dist_config = target_dir / "config"
    dist_config.mkdir(parents=True, exist_ok=True)

    src_config = ROOT_DIR / "config"
    for item in src_config.iterdir():
        if item.name in ("api_keys.json", "__pycache__"):
            continue
        if item.is_file():
            shutil.copy2(item, dist_config / item.name)

    # Ensure api_keys.example.json exists in config
    example_cfg = src_config / "api_keys.example.json"
    if example_cfg.exists():
        shutil.copy2(example_cfg, dist_config / "api_keys.example.json")

    # SSL certs for local dashboard
    src_certs = src_config / "certs"
    if src_certs.exists():
        dist_certs = dist_config / "certs"
        dist_certs.mkdir(parents=True, exist_ok=True)
        for cert_file in src_certs.iterdir():
            if cert_file.is_file():
                shutil.copy2(cert_file, dist_certs / cert_file.name)

    # 2. Core assets
    dist_core = target_dir / "core"
    dist_core.mkdir(parents=True, exist_ok=True)
    prompt_file = ROOT_DIR / "core" / "prompt.txt"
    if prompt_file.exists():
        shutil.copy2(prompt_file, dist_core / "prompt.txt")

    # 3. Dashboard static assets
    src_dash = ROOT_DIR / "dashboard" / "static"
    if src_dash.exists():
        dist_dash = target_dir / "dashboard" / "static"
        dist_dash.mkdir(parents=True, exist_ok=True)
        for sfile in src_dash.iterdir():
            if sfile.is_file():
                shutil.copy2(sfile, dist_dash / sfile.name)

    # 4. Memory baseline
    dist_mem = target_dir / "memory"
    dist_mem.mkdir(parents=True, exist_ok=True)
    long_term_base = ROOT_DIR / "memory" / "long_term.base.json"
    if long_term_base.exists():
        shutil.copy2(long_term_base, dist_mem / "long_term.base.json")

    # 5. Empty drop-in plugins folder for optional runtime user add-ons
    (target_dir / "plugins").mkdir(parents=True, exist_ok=True)
    (target_dir / "actions").mkdir(parents=True, exist_ok=True)


def build_with_nuitka() -> bool:
    log("Building standalone Windows executable with Nuitka...")
    try:
        import nuitka  # type: ignore
    except ImportError:
        log("Nuitka is not installed in current Python environment.")
        return False

    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={ICON_PATH}",
        f"--output-filename={APP_NAME}.exe",
        f"--output-dir={DIST_DIR}",
        "--include-package=actions",
        "--include-package=core",
        "--include-package=plugins",
        "--include-package=memory",
        "--enable-plugin=pyqt6",
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-unittest-mode=nofollow",
        "--nofollow-import-to=docx,matplotlib,cv2,mediapipe,playwright,sounddevice,scipy,torch,PIL",
        "--follow-imports",
        str(ROOT_DIR / "main.py"),
    ]

    log(f"Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        log("Nuitka build failed.")
        return False

    # Nuitka creates dist/main.dist -> rename to dist/BT-7274
    nuitka_out = DIST_DIR / "main.dist"
    if nuitka_out.exists():
        if APP_DIST.exists():
            shutil.rmtree(APP_DIST)
        nuitka_out.rename(APP_DIST)

    stage_non_secret_assets(APP_DIST)
    return True


def build_with_pyinstaller() -> bool:
    log("Building standalone Windows executable with PyInstaller...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        f"--name={APP_NAME}",
        f"--icon={ICON_PATH}",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
        "--collect-all=actions",
        "--collect-all=core",
        "--collect-all=plugins",
        "--collect-all=memory",
        "--collect-all=google.genai",
        "--collect-submodules=fastapi",
        "--collect-submodules=uvicorn",
        "--hidden-import=PyQt6.QtCore",
        "--hidden-import=PyQt6.QtGui",
        "--hidden-import=PyQt6.QtWidgets",
        "--hidden-import=comtypes",
        "--hidden-import=pycaw",
        "--hidden-import=win10toast",
        "--hidden-import=winreg",
        "--hidden-import=wmi",
        "--hidden-import=pywinauto",
        "--hidden-import=win32gui",
        "--hidden-import=win32con",
        "--hidden-import=win32api",
        "--hidden-import=pythoncom",
    ]

    # Safely include package metadata ONLY if the package is installed in the current environment
    metadata_candidates = ["google-genai", "tqdm", "regex", "requests", "packaging"]
    for pkg_name in metadata_candidates:
        try:
            import importlib.metadata
            importlib.metadata.distribution(pkg_name)
            cmd.append(f"--copy-metadata={pkg_name}")
        except Exception:
            pass

    cmd.append(str(ROOT_DIR / "main.py"))

    log(f"Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        log("PyInstaller build failed.")
        return False

    stage_non_secret_assets(APP_DIST)
    return True


def sanitize_distribution():
    """Ensure no raw .py files or secrets leaked into the distribution."""
    log("Verifying distribution hygiene...")
    leaked_py = []
    # Check that root files and core/actions/plugins don't contain raw .py files in dist
    for p in APP_DIST.rglob("*.py"):
        # We allow nothing except potential empty drop-ins if specified
        leaked_py.append(p)

    for p in leaked_py:
        try:
            p.unlink()
        except OSError:
            pass

    secret_key = APP_DIST / "config" / "api_keys.json"
    if secret_key.exists():
        log("WARNING: api_keys.json found in dist! Removing immediately.")
        secret_key.unlink()

    exe_file = APP_DIST / f"{APP_NAME}.exe"
    if not exe_file.exists():
        # Fallback check if main.exe was produced
        main_exe = APP_DIST / "main.exe"
        if main_exe.exists():
            main_exe.rename(exe_file)

    if not exe_file.exists():
        raise FileNotFoundError(f"Expected executable not found at {exe_file}")

    log(f"Distribution successfully verified at: {APP_DIST}")
    log(f"Executable: {exe_file}")


def main():
    parser = argparse.ArgumentParser(description="Build BT-7274 standalone Windows distribution.")
    parser.add_argument("--compiler", choices=["pyinstaller", "nuitka", "auto"], default="pyinstaller",
                        help="Compiler to use (defaults to pyinstaller)")
    parser.add_argument("--clean", action="store_true", help="Clean build directories before starting")
    args = parser.parse_args()

    clean_build_artifacts()

    compiler = args.compiler
    if compiler == "auto":
        compiler = "pyinstaller"

    log(f"Selected compiler: {compiler}")

    success = False
    if compiler == "nuitka":
        success = build_with_nuitka()
        if not success:
            log("Attempting fallback to PyInstaller...")
            clean_build_artifacts()
            success = build_with_pyinstaller()
    else:
        success = build_with_pyinstaller()

    if not success:
        log("Build failed.")
        sys.exit(1)

    sanitize_distribution()
    log("Build process complete.")


if __name__ == "__main__":
    main()

# SRS // BT-7274 MARK LV — macOS Platform Architecture & Setup Guide

> **Protocol 3: Protect the Pilot.**
> BT-7274 runs natively on macOS (Apple Silicon M1/M2/M3/M4 & Intel x86_64), preserving the complete Vanguard HUD interface, 3-point optic eye standby animations, voice neural link, gesture control, and local command routing without Linux or KDE dependencies.

---

## 1. Quick Start

### Option A: Double-Click Launcher (Finder)
Double-click `BT-7274.command` in the project root directory. This opens Terminal and executes `start_bt_mac.sh`.

### Option B: Terminal Command
```bash
./start_bt_mac.sh
```

---

## 2. Prerequisites & Installation

### Step 1: Install Homebrew (if not already installed)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### Step 2: Install System Dependencies
```bash
brew install python@3.11 portaudio opencv tesseract cliclick
```

### Step 3: Run Automated macOS Setup
```bash
./setup_mac.sh
```
This script automatically:
1. Creates a Python virtual environment at `./venv`.
2. Installs required Python packages from `requirements.txt`.
3. Verifies audio, camera, and input packages.
4. Marks launchers (`BT-7274.command`, `start_bt_mac.sh`) as executable.

---

## 3. macOS Security & Privacy Permissions

macOS requires explicit user permissions in **System Settings** for assistive features:

### 1. Accessibility (Required for Gesture & Mouse Control)
- **Path:** `System Settings` → `Privacy & Security` → `Accessibility`
- **Action:** Toggle **ON** the terminal application you use to run BT-7274 (e.g. `Terminal`, `iTerm2`, `VSCode`, or `Alacritty`).
- *Why:* CoreGraphics event simulation (`CGEventPost`) requires trusted accessibility status to move the cursor, click, drag, scroll, and switch windows.

### 2. Camera (Required for Hand Gesture Tracking)
- **Path:** `System Settings` → `Privacy & Security` → `Camera`
- **Action:** Toggle **ON** your terminal application when prompted on first launch.
- *Why:* OpenCV uses Apple's `AVFoundation` backend (`cv2.CAP_AVFOUNDATION`) to capture frames for MediaPipe hand tracking.

### 3. Microphone (Required for Voice Input)
- **Path:** `System Settings` → `Privacy & Security` → `Microphone`
- **Action:** Toggle **ON** your terminal application when prompted on first launch.
- *Why:* `sounddevice` and `pyaudio` record user voice commands via CoreAudio.

### 4. Screen Recording (Optional, for Screen Glance & Error Reader)
- **Path:** `System Settings` → `Privacy & Security` → `Screen Recording`
- **Action:** Toggle **ON** your terminal application if you use commands like *"look at my screen"* or *"explain this error on screen"*.
- *Why:* macOS native `/usr/sbin/screencapture` requires screen recording authorization to capture active windows or full displays.

---

## 4. macOS Platform Architecture Details

| Subsystem | Linux / KDE Wayland | macOS Equivalent (`sys.platform == "darwin"`) |
| :--- | :--- | :--- |
| **App Launching** | KRunner D-Bus / desktop entry | `open -a "<App>"` with fuzzy `.app` index search in `/Applications` & `~/Applications` |
| **Window Focusing** | `kdotool windowactivate` | AppleScript `tell application "<App>" to activate` without duplicate instances |
| **Input Simulation** | `ydotool` / `xdotool` | Native macOS `CoreGraphics` via `ctypes` (`CGEventPost`) with `cliclick` fallback |
| **Window Switch** | `Alt+Tab` | `Command+Tab` via `send_command_tab()` |
| **Tab Switch** | `Ctrl+Tab` / `Ctrl+Shift+Tab` | `Command+Shift+]` / `Command+Shift+[` via `send_tab_switch()` |
| **Show Desktop** | Super+D / KWin D-Bus | macOS F11 (`key code 103`) / Finder activate |
| **Audio & TTS** | PipeWire `wpctl` / EdgeTTS | CoreAudio volume control via `osascript`, native `say` (`MacSayTTSEngine`) & EdgeTTS |
| **Webcam / Optic** | V4L2 (`/dev/video0`) | Apple `AVFoundation` (`cv2.CAP_AVFOUNDATION`) with clean standby shutdown |
| **Screen Glance** | Spectacle / Grim | Native macOS `/usr/sbin/screencapture` |
| **Logs Path** | `~/.local/share/bt7274/logs` | `~/Library/Application Support/BT-7274/logs` |
| **Browser History** | `~/.mozilla/firefox` | `~/Library/Application Support/Firefox/Profiles` |

---

## 5. Troubleshooting

- **Permission Denied for Input Events:**
  Ensure your terminal app is checked under `System Settings` → `Privacy & Security` → `Accessibility`. If you changed terminal apps or updated macOS, remove and re-add the app to the list.
- **Audio Device Issues:**
  Run `brew install portaudio` and reinstall `sounddevice` (`pip install --force-reinstall sounddevice`).
- **Webcam Fails to Open:**
  Verify no other app (FaceTime, Photo Booth, Zoom) has exclusive lock on the camera, and verify Camera permissions in System Settings.

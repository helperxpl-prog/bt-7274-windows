# bt-7274-Windows
### SRS // BT-7274 Vanguard-Class Titan AI Assistant (Windows Edition)
> **Protocol 3: Protect the Pilot.**
> Fully optimized native Windows build supporting Windows 10 and Windows 11 x64 (`sys.platform == "win32"`), featuring standalone executable and Inno Setup installer packaging.

---

## ⚡ Quick Start on Windows

### 1. Pre-built Windows Installer
Download and run **`BT-7274-Setup.exe`** from GitHub Releases or generated in `Output/`.
- Installs BT-7274 cleanly into `%LOCALAPPDATA%\BT-7274` or `Program Files`.
- Automatically sets up Start Menu and Desktop shortcuts.
- Safely initializes your configuration without overwriting existing pilot memory.
- Includes a clean uninstaller.

### 2. Standalone Portable Executable
Extract `BT-7274-Portable-Windows.zip` or run `dist\BT-7274\BT-7274.exe` directly with zero system installation required.

### 3. Running from Source
```bat
setup.bat
run.bat
```
Or manually using standard Python:
```bat
python -m venv venv
call venv\Scripts\activate.bat
pip install -r requirements.txt
python main.py
```

---

## 🛠️ Standalone Build & Inno Setup Packaging

### One-Click Native Build
Double-click or run:
```bat
build_installer.bat
```

### Python Build Orchestrator
```bat
python build_windows.py --clean
```
- Compiles with **Nuitka** into native machine code (or falls back to PyInstaller).
- Never exposes raw `.py` source files.
- Strictly excludes pilot secrets (`config/api_keys.json`), `.venv`, and cache files.

### Inno Setup Installer Generation
Compile `installer.iss` using Inno Setup 6 (`ISCC.exe`):
```bat
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```
Generates `Output\BT-7274-Setup.exe`.

---

## 🚀 GitHub Actions CI/CD

Automated native Windows CI workflow is included in `.github/workflows/build-windows.yml`:
- Runs natively on `windows-latest`.
- Compiles standalone binary with Nuitka / PyInstaller.
- Packages `BT-7274-Setup.exe` with Inno Setup.
- Uploads setup installer and portable zip as downloadable artifacts.

---

## 🌟 Windows Platform Highlights

- **Direct Win32 Window Focus & App Launcher**: Native Windows `win32gui`, `win32con`, and `win32process` integration for instant app activation without duplicate windows.
- **Native Audio Device Routing**: WASAPI device enumeration and direct speaker/mic selection via `pycaw` and `comtypes`.
- **Hardware Telemetry**: Direct NVIDIA GPU readout via NVML without subprocess console overhead.
- **Native Autostart**: User-toggleable Windows registry run key (`Software\Microsoft\Windows\CurrentVersion\Run -> BT_AI`).
- **MediaPipe Hand Gesture Tracking**: Offline camera gesture processing via OpenCV and MediaPipe.
- **Local Dashboard**: HTTPS web dashboard and mobile pairing via local self-signed SSL.

---

## 📜 License
Distributed under the MIT License. See [LICENSE](LICENSE) for details.

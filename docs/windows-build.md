# Building TypeTrace for Windows

This guide explains how to build the self-contained Windows executable (`TypeTrace.exe`) from source.

## Prerequisites & Installation

Building requires Windows and Python 3.10+. Install TypeTrace with packaging dependencies:

```powershell
python -m pip install -e ".[build]"
```
For Google Docs replay from a source installation, install Playwright's Chromium browser:

```powershell
python -m playwright install chromium
```
Note: The standalone executable does not require bundled browser binaries; it can use your installed Google Chrome or Microsoft Edge browser.

## Building the Executable

Run the build automation script:

```powershell
python build_exe.py
```
Options:

- `--clean`: Clears the PyInstaller cache before compiling.
- `--debug-console`: Builds an executable with an attached console window for debugging.

The build outputs:

- `dist\TypeTrace.exe` — Self-contained windowed application.
- `dist\TypeTrace.exe.sha256` — SHA-256 checksum file alongside the executable.
- `dist\build-info.json` — Source revision and exact build dependency versions.

## Runtime & Data Paths

The executable runs without an existing Python environment. User data is stored under `%LOCALAPPDATA%\TypeTrace`:

- `logs/` — Standard output, error streams, and crash logs (`typetrace-stderr.log`).
- `output/` — Default destination for generated datasets and records.
- `browser_profile/` — Persistent browser profile retaining Google Docs login sessions.

## Replay & Controls

- **Desktop Replay**: Types directly into your existing editor window (Notepad, VS Code, etc.) with a 5-second preparation countdown.
- **Google Docs Replay**: Controls your local Google Chrome or Microsoft Edge browser via DevTools Protocol with the browser sandbox enabled.
- **Stopping**: Use `Esc` during desktop replay or click "Stop replay". Desktop replay also stops when another window gains focus.
- **Self-Test**: Run `TypeTrace.exe --self-test report.json` to verify runtime imports, Tkinter, and offline generation without dispatching keystrokes.

For more details on replay features and development workflows, see the [Replay Guide](replay.md) and [Development Guide](development.md).

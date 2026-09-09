"""TypeTrace - Standalone Windows GUI Launcher.

Copyright (c) 2026 Project TypeTrace
Licensed under the MIT License. See LICENSE for details.

Dedicated entrypoint for the frozen Windows executable.
Handles Windows windowed-mode runtime quirks (sys.stderr / sys.stdout is None
when built with console=False), redirects errors to per-user logs under
%LOCALAPPDATA%\\TypeTrace, supports an offline self-test switch, and invokes
gui.main().
"""

import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

import presets


class SafeStream(io.TextIOBase):
    """A safe text stream replacement for sys.stdout/sys.stderr when frozen.

    In a Windows GUI application built with PyInstaller (console=False),
    sys.stdout and sys.stderr are set to None. Any attempt to write to them
    will raise AttributeError. This wrapper safely writes output to a user-writable
    log file without raising exceptions.
    """

    def __init__(self, log_path: Optional[Path] = None, name: str = "SafeStream"):
        super().__init__()
        self._name = name
        self._file = None
        if log_path is not None:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                self._file = open(log_path, mode="a", encoding="utf-8", errors="replace")
            except OSError:
                self._file = None

    def write(self, s: str) -> int:
        if not s:
            return 0
        if self._file is not None:
            try:
                self._file.write(s)
                self._file.flush()
            except OSError:
                pass
        return len(s)

    def flush(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
            except OSError:
                pass

    def isatty(self) -> bool:
        return False

    def reconfigure(self, **kwargs) -> None:
        if self._file is not None:
            try:
                self._file.reconfigure(**kwargs)
            except Exception:
                pass

    @property
    def encoding(self) -> str:
        return "utf-8"

    @property
    def errors(self) -> str:
        return "replace"

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        super().close()


def sanitize_frozen_environment() -> Path:
    """Ensure standard streams and writable paths exist in windowed mode."""
    log_dir = presets.get_app_data_dir() / "logs"

    if sys.stdout is None:
        sys.stdout = SafeStream(log_dir / "typetrace-stdout.log", name="stdout")

    if sys.stderr is None:
        sys.stderr = SafeStream(log_dir / "typetrace-stderr.log", name="stderr")

    if sys.stdin is None:
        sys.stdin = io.StringIO("")

    return log_dir


def run_self_test(output_path: Path) -> int:
    """Harmless offline self-test verifying runtime dependencies and generation.

    Checks:
    - Frozen execution status.
    - Imports: numpy, tkinter, pynput.keyboard, playwright.sync_api.
    - Offline record generation with default parameters and fixed sample text.
    - Verification of final text via keystroke buffer reconstruction.
    - Tkinter root creation and destruction.

    Does not perform OS input dispatch or launch any browser.
    Returns 0 on success, 1 on failure, and writes full evidence to output_path.
    """
    evidence: Dict[str, Any] = {
        "frozen": getattr(sys, "frozen", False),
        "executable": sys.executable,
        "python_version": sys.version,
        "imports": {},
        "generation": {},
        "tkinter": {},
        "success": False,
    }
    success = True
    errors = []

    # 1. Import verification
    target_modules = [
        "numpy",
        "tkinter",
        "pynput.keyboard",
        "playwright.sync_api",
    ]
    for mod_name in target_modules:
        try:
            mod = __import__(mod_name, fromlist=["*"])
            evidence["imports"][mod_name] = {
                "available": True,
                "version": getattr(mod, "__version__", "available"),
            }
        except Exception as exc:
            success = False
            evidence["imports"][mod_name] = {
                "available": False,
                "error": str(exc),
            }
            errors.append(f"Import {mod_name} failed: {exc}")

    # 2. Record generation and keystroke verification
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as driver:
            # Complete a driver round trip without a browser or network request.
            context = driver.request.new_context()
            context.dispose()
            evidence["playwright_driver"] = driver.chromium.name == "chromium"
    except Exception as exc:
        success = False
        errors.append(f"Playwright driver failed: {exc}")

    sample_text = (
        "TypeTrace self-test verification: keystroke reconstruction, timing models, "
        "and offline generation are operational."
    )
    try:
        import main

        record = main.generate_full_output(sample_text, seed=42)
        keystrokes = record.get("keystrokes", [])

        # Reconstruct text by applying keystrokes sequentially to an empty buffer
        buffer = []
        for event in keystrokes:
            kind = event.get("kind")
            if kind == "backspace":
                if buffer:
                    buffer.pop()
            elif kind == "key":
                char = event.get("char")
                if char is not None:
                    buffer.append(char)

        reconstructed = "".join(buffer)
        matches = (reconstructed == sample_text)
        if not matches:
            success = False
            errors.append(
                f"Keystroke reconstruction mismatch: produced {len(reconstructed)} chars, expected {len(sample_text)}"
            )

        evidence["generation"] = {
            "sample_text": sample_text,
            "keystroke_count": len(keystrokes),
            "reconstructed_text_matches": matches,
            "statistics": record.get("statistics", {}),
        }
    except Exception as exc:
        success = False
        evidence["generation"] = {
            "error": str(exc),
            "reconstructed_text_matches": False,
        }
        errors.append(f"Generation check failed: {exc}")

    # 3. Tkinter initialization and destruction test
    try:
        import tkinter as tk

        import gui

        root = tk.Tk()
        root.withdraw()
        app = gui.Application(root)
        evidence["setups"] = []
        for preset in presets.list_presets():
            app.setup.set(preset.name)
            app._apply_setup()
            gui.collect_parameters(app._raw_parameters())
            gui.collect_emit_options(app._raw_emit_options())
            evidence["setups"].append(preset.slug)
        root.update_idletasks()
        root.destroy()
        evidence["tkinter"] = {
            "initialized": True,
            "destroyed": True,
        }
    except Exception as exc:
        success = False
        evidence["tkinter"] = {
            "initialized": False,
            "error": str(exc),
        }
        errors.append(f"Tkinter root test failed: {exc}")

    evidence["success"] = success
    if errors:
        evidence["errors"] = errors

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    except Exception as exc:
        print(f"ERROR: Failed to write self-test report to {output_path}: {exc}", file=sys.stderr)
        return 1

    return 0 if success else 1


def main() -> int:
    """Entry point for the packaged TypeTrace application."""
    log_dir = sanitize_frozen_environment()

    # Check for --self-test OUTPUT_JSON switch
    if "--self-test" in sys.argv:
        idx = sys.argv.index("--self-test")
        if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--"):
            output_json = Path(sys.argv[idx + 1])
        else:
            output_json = log_dir / "self-test-result.json"
        return run_self_test(output_json)

    try:
        import gui

        return gui.main()
    except Exception:
        crash_log = log_dir / "crash.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write("\n=== TypeTrace Crash Log ===\n")
                traceback.print_exc(file=f)
        except OSError:
            pass
        if sys.stderr is not None:
            traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

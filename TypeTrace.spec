# -*- mode: python ; coding: utf-8 -*-
# TypeTrace - PyInstaller Spec File
# Copyright (c) 2026 Project TypeTrace
# Licensed under the MIT License. See LICENSE for details.

import os
from PyInstaller.utils.hooks import collect_data_files

# Base repository root directory
repo_root = os.path.abspath(SPECPATH)

# Data files to bundle in the onefile executable
datas = [
    (os.path.join(repo_root, "LICENSE"), "."),
    (os.path.join(repo_root, "README.md"), "."),
]

# Playwright driver support: bundles the driver/cli without browser binaries or user cookies
datas += collect_data_files("playwright", include_py_files=False)

# Hidden imports required across emitters and dynamic dependencies
hiddenimports = [
    # NumPy
    "numpy",
    "numpy.random",
    # Tkinter
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    # Pynput (desktop emitter with Win32 backends)
    "pynput",
    "pynput.keyboard",
    "pynput.keyboard._win32",
    "pynput.mouse",
    "pynput.mouse._win32",
    # Playwright API (Google Docs emitter)
    "playwright",
    "playwright.sync_api",
    "playwright._impl._api_structures",
    # Core TypeTrace modules
    "launcher",
    "gui",
    "main",
    "presets",
    "pipeline",
    "replay",
    "timing_engine",
    "macro_scripter",
    "error_models",
    "emit_common",
    "desktop_emitter",
    "docs_emitter",
]

# Exclude developer, testing, and linting tooling from the final binary
excludes = [
    "pytest",
    "_pytest",
    "ruff",
    "IPython",
    "jupyter",
]

a = Analysis(
    [os.path.join(repo_root, "launcher.py")],
    pathex=[repo_root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

console_mode = os.environ.get("TYPETRACE_CONSOLE", "0").strip().lower() in ("1", "true", "yes")

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TypeTrace",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=console_mode,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

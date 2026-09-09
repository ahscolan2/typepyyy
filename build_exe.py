"""TypeTrace - Single-File Windows Executable Builder.

Copyright (c) 2026 Project TypeTrace
Licensed under the MIT License. See LICENSE for details.

Automation script to compile TypeTrace into a self-contained onefile windowed
Windows executable using PyInstaller and TypeTrace.spec.

Usage:
    python build_exe.py
    python build_exe.py --clean
    python build_exe.py --debug-console
"""

import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path


REQUIRED_MODULES = ["numpy", "wcwidth", "tkinter", "pynput", "playwright", "PyInstaller"]


def check_prerequisites() -> bool:
    """Verify Windows OS, interpreter version, and required build dependencies."""
    print("=== TypeTrace Windows Build Diagnostics ===")
    print(f"Platform: {sys.platform}")
    print(f"Python interpreter: {sys.executable} (version {sys.version.split()[0]})")

    if sys.platform != "win32":
        print("ERROR: Building TypeTrace standalone executable requires Windows.", file=sys.stderr)
        return False

    if sys.version_info < (3, 10):
        print("ERROR: TypeTrace requires Python 3.10 or newer.", file=sys.stderr)
        return False

    missing = []
    for mod in REQUIRED_MODULES:
        if importlib.util.find_spec(mod) is None:
            missing.append(mod)
        else:
            print(f"  [OK] Required module '{mod}' found.")

    if missing:
        print(
            f"ERROR: Missing required dependencies: {', '.join(missing)}",
            file=sys.stderr,
        )
        print('Install them via: python -m pip install -e ".[build]"', file=sys.stderr)
        return False

    return True


def compute_sha256(file_path: Path) -> str:
    """Compute the SHA-256 hexadecimal hash of a file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_executable(
    spec_path: Path,
    dist_dir: Path,
    work_dir: Path,
    clean: bool = False,
    debug_console: bool = False,
) -> int:
    """Invoke PyInstaller to build TypeTrace.exe from the spec file."""
    if not spec_path.exists():
        print(f"ERROR: Spec file not found at {spec_path}", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(spec_path),
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--noconfirm",
    ]

    # Pass --clean directly to PyInstaller without recursive directory deletion
    if clean:
        cmd.append("--clean")

    env = os.environ.copy()
    if debug_console:
        env["TYPETRACE_CONSOLE"] = "1"
        print("Building with debug console enabled (TYPETRACE_CONSOLE=1).")
    else:
        env["TYPETRACE_CONSOLE"] = "0"
        print("Building windowed executable (console=False).")

    print("\nExecuting PyInstaller command:")
    print(" ".join(cmd))
    print("-" * 50)

    result = subprocess.run(cmd, env=env)
    print("-" * 50)

    if result.returncode != 0:
        print(f"ERROR: PyInstaller failed with exit code {result.returncode}", file=sys.stderr)
        return result.returncode

    exe_path = dist_dir / "TypeTrace.exe"
    if not exe_path.exists():
        print(f"ERROR: Build completed but {exe_path} was not found.", file=sys.stderr)
        return 1

    digest = compute_sha256(exe_path)
    checksum_path = dist_dir / "TypeTrace.exe.sha256"
    checksum_path.write_text(f"{digest} *TypeTrace.exe\n", encoding="utf-8")
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=spec_path.parent,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"], cwd=spec_path.parent,
            capture_output=True,
        ).returncode != 0
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None  # Source archives need no Git installation.
    build_info = {
        "source_commit": revision,
        "source_modified": dirty,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "sha256": digest,
        "dependencies": {
            package: importlib.metadata.version(package)
            for package in ("numpy", "wcwidth", "pynput", "playwright", "pyinstaller")
        },
    }
    (dist_dir / "build-info.json").write_text(json.dumps(build_info, indent=2) + "\n", encoding="utf-8")

    size_mb = exe_path.stat().st_size / (1024 * 1024)
    print("\nSUCCESS: Built standalone executable:")
    print(f"  Path:     {exe_path.resolve()}")
    print(f"  Size:     {size_mb:.2f} MB")
    print(f"  SHA256:   {digest}")
    print(f"  Checksum: {checksum_path.resolve()}")
    print("\nRuntime Notes:")
    print("  - Launching TypeTrace.exe runs the Tkinter GUI (gui.main()).")
    print("  - Application logs and browser profiles are stored in:")
    print(r"    %LOCALAPPDATA%\TypeTrace")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build self-contained Windows executable for TypeTrace."
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Pass --clean to PyInstaller to clear its build cache.",
    )
    parser.add_argument(
        "--debug-console",
        action="store_true",
        help="Build with an attached console window for debugging.",
    )
    parser.add_argument(
        "--spec",
        default="TypeTrace.spec",
        help="Path to the PyInstaller spec file (default: TypeTrace.spec).",
    )
    parser.add_argument(
        "--dist-dir",
        default="dist",
        help="Directory where compiled executable will be placed (default: dist).",
    )
    parser.add_argument(
        "--work-dir",
        default="build",
        help="Directory for temporary compilation files (default: build).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check prerequisites without running the build.",
    )

    args = parser.parse_args()

    if not check_prerequisites():
        return 1

    if args.check_only:
        print("\nPrerequisites check completed successfully.")
        return 0

    repo_root = Path(__file__).parent.resolve()
    spec_path = (repo_root / args.spec).resolve()
    dist_dir = (repo_root / args.dist_dir).resolve()
    work_dir = (repo_root / args.work_dir).resolve()

    return build_executable(
        spec_path=spec_path,
        dist_dir=dist_dir,
        work_dir=work_dir,
        clean=args.clean,
        debug_console=args.debug_console,
    )


if __name__ == "__main__":
    sys.exit(main())

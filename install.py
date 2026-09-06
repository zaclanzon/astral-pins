#!/usr/bin/env python3
"""Install user launchers that run this checkout with the selected Python."""
import argparse
import os
from pathlib import Path
import shlex
import sys


def check_dependencies():
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Astral Pins requires Linux.")
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required.")
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_foreign("cairo")
        from gi.repository import Gtk
        import cairo
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            "GTK4, PyGObject, or the Cairo bindings are missing for "
            f"{sys.executable}.\nRun sh setup.sh --demo to install distro dependencies, "
            "or use your system Python with the matching GTK/Cairo bindings.\n"
            f"Details: {exc}"
        ) from exc


def desktop_quote(value):
    # Desktop Entry escaping is separate from shell quoting. Escape backslashes
    # twice: once for the Exec quoted argument, once for the string value.
    value = str(value).replace("%", "%%")
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\') + '"'


def install(source, prefix, data_home, python):
    source = Path(source).resolve()
    if not (source / "astral_pins.py").is_file():
        raise RuntimeError("Run install.py from a complete Astral Pins checkout.")
    launcher = Path(prefix).absolute() / "bin" / "astral-pins"
    desktop = Path(data_home).absolute() / "applications" / "astral-pins.desktop"
    for path in (source, launcher, desktop, Path(python)):
        if any(char in str(path) for char in ('\n', '\r', '\0')):
            raise RuntimeError("Installation paths cannot contain line breaks or NUL bytes.")
    launcher.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        '#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' ' +
        shlex.quote(str(source / "astral_pins.py")) + ' "$@"\n'
    )
    launcher.chmod(0o755)
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=Astral Pins\n"
        "Comment=Per-pin power monitoring for ASUS ROG Astral cards\n"
        f"Exec={desktop_quote(launcher)}\nIcon=utilities-system-monitor\n"
        "Terminal=false\nCategories=System;Monitor;\nKeywords=gpu;power;i2c;astral;\n"
    )
    return launcher, desktop


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check dependencies without installing")
    parser.add_argument("--prefix", type=Path, help="install under PREFIX/bin and PREFIX/share (default: ~/.local, with XDG_DATA_HOME respected)")
    args = parser.parse_args()
    try:
        check_dependencies()
        print(f"Dependencies OK: {sys.executable}")
        if args.check:
            return 0
        prefix = args.prefix.expanduser() if args.prefix else Path.home() / ".local"
        data_home = prefix / "share"
        if args.prefix is None and os.environ.get("XDG_DATA_HOME"):
            candidate = Path(os.environ["XDG_DATA_HOME"])
            if candidate.is_absolute():
                data_home = candidate
        launcher, desktop = install(Path(__file__).parent, prefix, data_home, sys.executable)
    except (RuntimeError, OSError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Command: {launcher}\nDesktop entry: {desktop}")
    print("This checkout remains the app source. Rerun the installer if you move it.")
    if str(launcher.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"Add {launcher.parent} to PATH, or run the command using its full path.")
    print(f"Preview: {shlex.quote(str(launcher))} --demo")
    return 0


if __name__ == "__main__":
    sys.exit(main())

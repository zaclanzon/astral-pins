#!/usr/bin/env python3
"""Install user launchers that run this checkout with the selected Python."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
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
    app_id = "dev.zac.astralpins"
    icon_source = source / "contrib/icons/hicolor/scalable/apps" / f"{app_id}.svg"
    if not (source / "astral_pins.py").is_file() or not icon_source.is_file():
        raise RuntimeError("Run install.py from a complete Astral Pins checkout.")
    launcher = Path(prefix).absolute() / "bin" / "astral-pins"
    desktop = Path(data_home).absolute() / "applications" / f"{app_id}.desktop"
    icon = Path(data_home).absolute() / "icons/hicolor/scalable/apps" / f"{app_id}.svg"
    for path in (source, launcher, desktop, Path(python)):
        if any(char in str(path) for char in ('\n', '\r', '\0')):
            raise RuntimeError("Installation paths cannot contain line breaks or NUL bytes.")
    launcher.parent.mkdir(parents=True, exist_ok=True)
    desktop.parent.mkdir(parents=True, exist_ok=True)
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon_source, icon)
    launcher.write_text(
        '#!/bin/sh\nexec ' + shlex.quote(str(python)) + ' ' +
        shlex.quote(str(source / "astral_pins.py")) + ' "$@"\n'
    )
    launcher.chmod(0o755)
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=Astral Pins\n"
        "Comment=Per-pin power monitoring for ASUS ROG Astral cards\n"
        f"Exec={desktop_quote(launcher)}\nIcon={app_id}\nStartupWMClass={app_id}\n"
        "Terminal=false\nCategories=System;Monitor;\nKeywords=gpu;power;i2c;astral;\n"
    )
    legacy = desktop.with_name("astral-pins.desktop")
    if legacy.is_file():
        legacy.unlink()
    for tool, args in (
        ("gtk-update-icon-cache", ["-f", "-t", str(icon.parents[2])]),
        ("update-desktop-database", [str(desktop.parent)]),
    ):
        executable = shutil.which(tool)
        if executable:
            try:
                subprocess.run([executable, *args], capture_output=True, timeout=15, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
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

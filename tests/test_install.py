"""Installer checks need no display or hardware."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import install


class InstallTests(unittest.TestCase):
    def test_launcher_handles_spaces_and_shell_characters(self):
        with tempfile.TemporaryDirectory(prefix="astral install ") as temp:
            root = Path(temp)
            source = root / "source ' $ ` %"
            source.mkdir()
            (source / "astral_pins.py").write_text("import sys; print(repr(sys.argv[1:]))\n")
            launcher, desktop = install.install(source, root / "prefix", root / "data", sys.executable)
            result = subprocess.run([str(launcher), "--demo", "a b", "$literal"],
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "['--demo', 'a b', '$literal']")
            self.assertTrue(os.access(launcher, os.X_OK))
            self.assertIn(f'Exec="{launcher}"', desktop.read_text())
            (source / "astral_pins.py").write_text("print('updated checkout')\n")
            result = subprocess.run([str(launcher)], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "updated checkout")

    def test_install_is_repeatable_and_leaves_other_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "astral_pins.py").touch()
            other = root / "unrelated"
            other.write_text("keep")
            first = install.install(root, root, root / "share", sys.executable)
            self.assertEqual(first, install.install(root, root, root / "share", sys.executable))
            self.assertEqual(other.read_text(), "keep")

    def test_invalid_source_creates_no_launchers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(RuntimeError, "complete"):
                install.install(root, root, root / "share", sys.executable)
            self.assertFalse((root / "bin").exists())

    def test_check_does_not_write_files(self):
        with patch.object(sys, "argv", ["install.py", "--check"]), patch.object(install, "check_dependencies"), patch.object(install, "install") as write:
            self.assertEqual(install.main(), 0)
            write.assert_not_called()

    def test_missing_dependencies_prevent_install(self):
        with patch.object(sys, "argv", ["install.py"]), patch.object(install, "check_dependencies", side_effect=RuntimeError("GTK4 missing")), patch.object(install, "install") as write:
            self.assertEqual(install.main(), 1)
            write.assert_not_called()

    def test_xdg_and_prefix_destinations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for args, expected_prefix, expected_data in (
                ([], Path.home() / ".local", root / "xdg"),
                (["--prefix", str(root / "custom")], root / "custom", root / "custom/share"),
            ):
                with patch.object(sys, "argv", ["install.py"] + args), patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "xdg")}), patch.object(install, "check_dependencies"), patch.object(install, "install", return_value=(root / "bin/astral-pins", root / "entry.desktop")) as write:
                    self.assertEqual(install.main(), 0)
                    self.assertEqual(write.call_args.args[1:3], (expected_prefix, expected_data))


if __name__ == "__main__":
    unittest.main()

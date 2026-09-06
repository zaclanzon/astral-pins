"""Test distro selection and non-mutating setup entry points without root."""
from pathlib import Path
import os
import shutil
import tempfile
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def family(self, distro, like=""):
        return subprocess.run(
            ["sh", "-c", '. "$1/scripts/linux-deps.sh"; linux_family "$2" "$3"',
             "sh", str(ROOT), distro, like], capture_output=True, text=True)

    def test_distro_families_and_derivatives(self):
        for distro, like, expected in (
            ("ubuntu", "debian", "debian"), ("linuxmint", "ubuntu debian", "debian"),
            ("fedora", "", "fedora"), ("rocky", "rhel centos fedora", "fedora"),
            ("manjaro", "arch", "arch"), ("opensuse-tumbleweed", "opensuse suse", "suse"),
            ("custom", "debian", "debian"),
        ):
            with self.subTest(distro=distro):
                result = self.family(distro, like)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_unknown_family_is_rejected(self):
        self.assertNotEqual(self.family("nixos").returncode, 0)

    def test_all_families_include_cairo_bridge_and_device_tools(self):
        for family, bridge in (("debian", "python3-gi-cairo"), ("fedora", "python3-gobject"),
                               ("arch", "python-gobject"), ("suse", "python3-gobject-Gdk")):
            result = subprocess.run(
                ["sh", "-c", '. "$1/scripts/linux-deps.sh"; linux_packages "$2"',
                 "sh", str(ROOT), family], capture_output=True, text=True, check=True)
            self.assertIn(bridge, result.stdout.split())
            self.assertIn("kmod", result.stdout.split())

    def test_help_and_bad_options(self):
        result = subprocess.run(["sh", str(ROOT / "setup.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--dry-run", result.stdout)
        for args in (("--unknown",), ("--demo", "--dependencies-only")):
            result = subprocess.run(["sh", str(ROOT / "setup.sh"), *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_setup_orchestration_with_fake_system_commands(self):
        # Run the real shell script. Fake sudo records requests and never
        # executes them; a tiny installer fixture records only user setup.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copy(ROOT / "setup.sh", root / "setup.sh")
            shutil.copytree(ROOT / "scripts", root / "scripts")
            (root / "install.py").write_text(
                "import os, sys\n"
                "with open(os.environ['SETUP_TEST_LOG'], 'a') as log: "
                "log.write('installer ' + ' '.join(sys.argv[1:]) + '\\n')\n")
            fake = root / "bin"
            fake.mkdir()
            commands = {
                "id": '#!/bin/sh\ncase "$1" in -u) echo 1000;; -un) echo testuser;; esac\n',
                "sudo": '#!/bin/sh\nprintf "sudo %s\\n" "$*" >> "$SETUP_TEST_LOG"\n[ "${SETUP_TEST_FAIL:-}" != yes ]\n',
                "nvidia-smi": '#!/bin/sh\nexit 0\n',
            }
            for name, content in commands.items():
                path = fake / name
                path.write_text(content)
                path.chmod(0o755)
            log = root / "calls"
            env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ["PATH"],
                       SETUP_TEST_LOG=str(log))
            result = subprocess.run(["sh", str(root / "setup.sh")], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log.read_text()
            self.assertIn("installer --check", calls)
            self.assertIn("sudo modprobe i2c-dev", calls)
            self.assertIn("sudo usermod -aG i2c testuser", calls)
            self.assertIn("sudo udevadm trigger --subsystem-match=i2c-dev", calls)
            self.assertTrue(calls.endswith("installer \n"))
            log.unlink()
            result = subprocess.run(["sh", str(root / "setup.sh"), "--demo"], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("modprobe", log.read_text())
            log.unlink()
            result = subprocess.run(["sh", str(root / "setup.sh"), "--dry-run"], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(log.exists())
            result = subprocess.run(["sh", str(root / "setup.sh")],
                                    env=dict(env, SETUP_TEST_FAIL="yes"), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("installer", log.read_text())
            self.assertNotIn("modprobe", log.read_text())

    def test_scripts_parse(self):
        for script in (ROOT / "setup.sh", ROOT / "scripts/linux-deps.sh"):
            subprocess.run(["sh", "-n", str(script)], check=True)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path


VM_DIR = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = VM_DIR / "setup-axis-vm.sh"
GUEST_HELPER = VM_DIR / "windows" / "START-AXIS-SETUP.cmd"


class SetupAxisVmTest(unittest.TestCase):
    def run_setup(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.setdefault("USER", "axis-test-user")
        with tempfile.TemporaryDirectory() as config_dir:
            env["XDG_CONFIG_HOME"] = config_dir
            return subprocess.run(
                [str(SETUP_SCRIPT), *args],
                cwd=VM_DIR,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=10,
                check=False,
            )

    def test_help_describes_guided_axis_media(self) -> None:
        result = self.run_setup("--help")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("--axis-installer PATH", result.stdout)
        self.assertIn("--skip-axis-media", result.stdout)
        self.assertIn("START-AXIS-SETUP.cmd", result.stdout)

    @unittest.skipUnless(platform.machine() == "x86_64", "VM target is x86_64 only")
    def test_dry_run_stages_installer_and_attaches_third_cd(self) -> None:
        result = self.run_setup(
            "--windows-iso",
            "/tmp/Windows 11.iso",
            "--axis-installer",
            "/tmp/Axis Studio.zip",
            "--dry-run",
            "--skip-packages",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("with installer /tmp/Axis Studio.zip", result.stdout)
        self.assertIn("axis-studio-axis-setup.iso,device=cdrom,readonly=on", result.stdout)
        self.assertIn("open the AXIS_SETUP CD", result.stdout)

    @unittest.skipUnless(platform.machine() == "x86_64", "VM target is x86_64 only")
    def test_default_media_guides_download_when_installer_is_omitted(self) -> None:
        result = self.run_setup(
            "--windows-iso",
            "/tmp/windows.iso",
            "--dry-run",
            "--skip-packages",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("build guided Axis Studio setup CD", result.stdout)
        self.assertIn("axis-studio-axis-setup.iso,device=cdrom,readonly=on", result.stdout)
        self.assertIn("opens\n     Noitom's official download", result.stdout)

    @unittest.skipUnless(platform.machine() == "x86_64", "VM target is x86_64 only")
    def test_skip_axis_media_omits_setup_cd(self) -> None:
        result = self.run_setup(
            "--windows-iso",
            "/tmp/windows.iso",
            "--skip-axis-media",
            "--dry-run",
            "--skip-packages",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("axis-setup.iso", result.stdout)
        self.assertIn("download the correct Axis Studio edition", result.stdout)

    def test_installer_and_skip_media_are_mutually_exclusive(self) -> None:
        result = self.run_setup(
            "--windows-iso",
            "/tmp/windows.iso",
            "--axis-installer",
            "/tmp/axis.msi",
            "--skip-axis-media",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be used with --skip-axis-media", result.stdout)

    def test_rejects_unknown_installer_format(self) -> None:
        result = self.run_setup(
            "--windows-iso",
            "/tmp/windows.iso",
            "--axis-installer",
            "/tmp/axis.txt",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must point to an .exe, .msi, or .zip", result.stdout)

    def test_missing_option_value_has_clear_error(self) -> None:
        result = self.run_setup("--axis-installer")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--axis-installer requires a value", result.stdout)

    def test_guest_helper_keeps_activation_interactive(self) -> None:
        helper = GUEST_HELPER.read_text(encoding="utf-8")
        self.assertIn("https://account.noitom.com/", helper)
        self.assertIn("AxisStudioSetup.msi", helper)
        self.assertIn("AxisStudioSetup.zip", helper)
        self.assertIn("none was started automatically", helper)
        self.assertIn("Restart Windows before launching", helper)
        self.assertIn("never asks for or stores", helper)
        self.assertNotIn("--password", helper)
        self.assertNotIn("--product-id", helper)


if __name__ == "__main__":
    unittest.main()

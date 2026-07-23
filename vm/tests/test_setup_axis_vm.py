from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path


VM_DIR = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = VM_DIR / "setup-axis-vm.sh"
GUEST_HELPER = VM_DIR / "windows" / "START-AXIS-SETUP.cmd"


def media_url_cache_key(url: str, *, strip_query: bool = False) -> str:
    identity = url.split("#", 1)[0]
    if strip_query:
        identity = identity.split("?", 1)[0]
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


class SetupAxisVmTest(unittest.TestCase):
    def run_setup(
        self, *args: str, env_updates: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.setdefault("USER", "axis-test-user")
        if env_updates:
            env.update(env_updates)
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

    @staticmethod
    def install_fake_curl(directory: Path, *, valid: bool = True) -> Path:
        bin_dir = directory / "bin"
        bin_dir.mkdir()
        curl = bin_dir / "curl"
        if valid:
            payload = r"""#!/bin/sh
output=
url=
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) output="$2"; shift 2 ;;
        *) url="$1"; shift ;;
    esac
done
case "$url" in
    *Win11*)
        dd if=/dev/zero of="$output" bs=32774 count=1 2>/dev/null
        printf 'CD001' | dd of="$output" bs=1 seek=32769 conv=notrunc 2>/dev/null
        ;;
    *) printf '\320\317\021\340\241\261\032\341axis-msi' >"$output" ;;
esac
"""
        else:
            payload = "#!/bin/sh\nwhile [ \"$1\" != -o ]; do shift; done\nprintf '<html>not media</html>' >\"$2\"\n"
        curl.write_text(payload, encoding="ascii")
        curl.chmod(0o755)
        return bin_dir

    def test_help_describes_guided_axis_media(self) -> None:
        result = self.run_setup("--help")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("--windows-iso-url URL", result.stdout)
        self.assertIn("--axis-installer PATH", result.stdout)
        self.assertIn("--axis-installer-url URL", result.stdout)
        self.assertIn("--download-axis-studio-3", result.stdout)
        self.assertIn("--download-only", result.stdout)
        self.assertIn("--skip-axis-media", result.stdout)
        self.assertIn("START-AXIS-SETUP.cmd", result.stdout)
        self.assertIn(
            "https://www.microsoft.com/software-download/windows11", result.stdout
        )

    def test_missing_windows_source_points_to_official_download(self) -> None:
        result = self.run_setup("--dry-run", "--skip-packages")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("example filenames are not bundled", result.stdout)
        self.assertIn(
            "https://www.microsoft.com/software-download/windows11", result.stdout
        )
        self.assertIn("--windows-iso-url", result.stdout)

    def test_win10_missing_source_points_to_win10_download(self) -> None:
        result = self.run_setup(
            "--windows-version", "win10", "--dry-run", "--skip-packages"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "https://www.microsoft.com/software-download/windows10ISO", result.stdout
        )
        self.assertNotIn("software-download/windows11", result.stdout)

    def test_missing_example_path_points_to_official_download(self) -> None:
        result = self.run_setup(
            "--windows-iso", "/definitely/missing/Win11.iso", "--skip-packages"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Windows ISO not readable", result.stdout)
        self.assertIn("Example filenames are not bundled", result.stdout)
        self.assertIn(
            "https://www.microsoft.com/software-download/windows11", result.stdout
        )

    def test_local_and_url_sources_are_mutually_exclusive(self) -> None:
        windows = self.run_setup(
            "--windows-iso",
            "/tmp/windows.iso",
            "--windows-iso-url",
            "https://software.download.prss.microsoft.com/windows.iso",
            "--dry-run",
        )
        self.assertNotEqual(windows.returncode, 0)
        self.assertIn("--windows-iso and --windows-iso-url", windows.stdout)

        axis = self.run_setup(
            "--windows-iso",
            "/tmp/windows.iso",
            "--axis-installer",
            "/tmp/axis.msi",
            "--axis-installer-url",
            "https://cdn.noitom.com/axis.msi",
            "--dry-run",
        )
        self.assertNotEqual(axis.returncode, 0)
        self.assertIn("--axis-installer and --axis-installer-url", axis.stdout)

    def test_rejects_insecure_media_urls(self) -> None:
        result = self.run_setup(
            "--windows-iso-url",
            "http://example.test/windows.iso",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("accepts only a direct HTTPS URL", result.stdout)

        non_microsoft = self.run_setup(
            "--windows-iso-url",
            "https://downloads.example.test/windows.iso",
            "--dry-run",
        )
        self.assertNotEqual(non_microsoft.returncode, 0)
        self.assertIn("must be a direct microsoft.com URL", non_microsoft.stdout)

        userinfo_bypass = self.run_setup(
            "--windows-iso-url",
            "https://microsoft.com:443@evil.example/Win11.iso",
            "--dry-run",
        )
        self.assertNotEqual(userinfo_bypass.returncode, 0)
        self.assertIn("must not contain URL credentials", userinfo_bypass.stdout)

    def test_download_only_dry_run_does_not_claim_media_is_ready(self) -> None:
        result = self.run_setup(
            "--axis-installer-url",
            "https://cdn.noitom.com/AxisStudio.msi",
            "--download-only",
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("no publisher media was downloaded", result.stdout)
        self.assertIn("Planned Axis cache", result.stdout)
        self.assertNotIn("Publisher media is ready", result.stdout)
        self.assertNotIn("Enabling libvirtd", result.stdout)

    @unittest.skipUnless(platform.machine() == "x86_64", "VM target is x86_64 only")
    def test_url_dry_run_redacts_urls_and_uses_cache_paths(self) -> None:
        cache = "/tmp/axis-vm-test-media"
        windows_url = (
            "https://software.download.prss.microsoft.com/Win11.iso"
            "?token=windows-secret"
        )
        axis_url = "https://cdn.noitom.com/AxisStudio.msi?token=axis-secret"
        result = self.run_setup(
            "--windows-iso-url",
            windows_url,
            "--axis-installer-url",
            axis_url,
            "--media-cache",
            cache,
            "--dry-run",
            "--skip-packages",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn(
            f"{cache}/windows-win11-x86_64-"
            f"{media_url_cache_key(windows_url, strip_query=True)}.iso",
            result.stdout,
        )
        self.assertIn(
            f"{cache}/AxisStudioSetup-{media_url_cache_key(axis_url)}.msi",
            result.stdout,
        )
        self.assertIn("download Windows win11 x86-64 ISO from its publisher", result.stdout)
        self.assertIn("download Axis Studio installer from its publisher", result.stdout)
        self.assertNotIn("windows-secret", result.stdout)
        self.assertNotIn("axis-secret", result.stdout)
        self.assertNotIn("software.download.prss.microsoft.com", result.stdout)

    @unittest.skipUnless(platform.machine() == "x86_64", "VM target is x86_64 only")
    def test_dry_run_stages_windows_iso_for_system_libvirt(self) -> None:
        result = self.run_setup(
            "--windows-iso",
            "/tmp/Windows 11.iso",
            "--dry-run",
            "--skip-packages",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        staged = "/var/lib/libvirt/images/axis-studio-windows-installer.iso"
        self.assertIn(
            f"sudo install -m 0644 /tmp/Windows 11.iso {staged}.incoming.",
            result.stdout,
        )
        self.assertIn(f"sudo ln {staged}.incoming.", result.stdout)
        self.assertIn(f"--cdrom {staged}", result.stdout)
        self.assertIn("virtio-win.iso.incoming.", result.stdout)
        self.assertIn("validate ISO-9660 signature", result.stdout)
        self.assertIn("axis-studio.qcow2.incoming.", result.stdout)

    @unittest.skipUnless(platform.machine() == "x86_64", "VM target is x86_64 only")
    def test_pinned_axis_studio_download_is_explicit_and_redacted(self) -> None:
        result = self.run_setup(
            "--windows-iso",
            "/tmp/windows.iso",
            "--download-axis-studio-3",
            "--dry-run",
            "--skip-packages",
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("AxisStudioSetup-", result.stdout)
        self.assertIn(".msi", result.stdout)
        self.assertIn("download Axis Studio installer from its publisher", result.stdout)
        self.assertNotIn("Axis_Studio_nacs", result.stdout)

    def test_download_only_validates_and_keys_real_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = self.install_fake_curl(root)
            cache = root / "cache"
            environment = {"PATH": f"{bin_dir}:{os.environ['PATH']}"}
            urls = (
                "https://cdn.noitom.com/releases/AxisStudio.msi?edition=online",
                "https://cdn.noitom.com/releases/AxisStudio.msi?edition=dongle",
            )
            for url in urls:
                result = self.run_setup(
                    "--axis-installer-url",
                    url,
                    "--media-cache",
                    str(cache),
                    "--download-only",
                    env_updates=environment,
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                expected = cache / f"AxisStudioSetup-{media_url_cache_key(url)}.msi"
                self.assertTrue(expected.is_file(), result.stdout)
                self.assertEqual(expected.read_bytes()[:8], bytes.fromhex("d0cf11e0a1b11ae1"))
                self.assertNotIn(url.split("edition=", 1)[1], result.stdout)

            self.assertEqual(len(list(cache.glob("AxisStudioSetup-*.msi"))), 2)

    def test_download_only_validates_windows_iso_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = self.install_fake_curl(root)
            cache = root / "cache"
            url = (
                "https://software.download.prss.microsoft.com/Win11.iso"
                "?token=fresh-link"
            )
            result = self.run_setup(
                "--windows-iso-url",
                url,
                "--media-cache",
                str(cache),
                "--download-only",
                env_updates={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            expected = cache / (
                "windows-win11-x86_64-"
                f"{media_url_cache_key(url, strip_query=True)}.iso"
            )
            self.assertTrue(expected.is_file(), result.stdout)
            self.assertEqual(expected.read_bytes()[32769:32774], b"CD001")
            self.assertNotIn("fresh-link", result.stdout)

    def test_download_only_rejects_html_and_cleans_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = self.install_fake_curl(root, valid=False)
            cache = root / "cache"
            result = self.run_setup(
                "--axis-installer-url",
                "https://cdn.noitom.com/releases/AxisStudio.msi",
                "--media-cache",
                str(cache),
                "--download-only",
                env_updates={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not a valid msi file", result.stdout)
            self.assertEqual(list(cache.iterdir()), [])

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

    def test_rejects_vm_name_that_could_escape_images_directory(self) -> None:
        result = self.run_setup(
            "--windows-iso",
            "/tmp/windows.iso",
            "--name",
            "../../outside",
            "--dry-run",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--name must start with a letter or number", result.stdout)

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

    def test_failed_setup_cleanup_tracks_created_vm_artifacts(self) -> None:
        script = SETUP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("AXIS_SETUP_ISO_CREATED=1", script)
        self.assertIn("STAGED_WINDOWS_ISO_CREATED=1", script)
        self.assertIn("DISK_CREATED=1", script)
        self.assertIn("removed Axis setup ISO after failed setup", script)
        self.assertIn("removed staged Windows ISO after failed setup", script)
        self.assertIn("removed guest disk after failed setup", script)
        self.assertIn("preserving VM artifacts because domain", script)
        self.assertIn("domain absence could not be verified", script)
        self.assertIn("virsh list --all --name", script)
        self.assertIn("grep -Fxq --", script)


if __name__ == "__main__":
    unittest.main()

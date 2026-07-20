#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Exercise the curl installer's remote-release routing with a mock transport."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from studio.packaging import build_release


ROOT = Path(__file__).resolve().parents[2]
VERSION_PATTERN = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
CUSTOM_BASE = "https://downloads.example.test/releases"

MOCK_CURL = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import sys


args = sys.argv[1:]
urls = [argument for argument in args if argument.startswith("https://")]
if len(urls) != 1:
    print(f"mock curl expected one HTTPS URL, found {urls!r}", file=sys.stderr)
    raise SystemExit(2)
url = urls[0]
try:
    output = Path(args[args.index("-o") + 1])
except (ValueError, IndexError):
    print("mock curl requires -o", file=sys.stderr)
    raise SystemExit(2)
with Path(os.environ["MOCK_CURL_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"args": args, "url": url}) + "\n")

asset_name = os.environ["MOCK_ASSET_NAME"]
archive = Path(os.environ["MOCK_ARCHIVE"])
checksum = Path(os.environ["MOCK_CHECKSUM"])
previous_asset_name = os.environ.get("MOCK_PREVIOUS_ASSET_NAME", "")
previous_archive = Path(os.environ["MOCK_PREVIOUS_ARCHIVE"]) if previous_asset_name else None
previous_checksum = Path(os.environ["MOCK_PREVIOUS_CHECKSUM"]) if previous_asset_name else None
previous_tag = os.environ.get("MOCK_PREVIOUS_RELEASE_TAG", "")
if "/releases/latest" in url or url.endswith("/releases/tags/" + os.environ["MOCK_RELEASE_TAG"]):
    payload = {
        "tag_name": os.environ["MOCK_RELEASE_TAG"],
        "assets": [
            {"id": 101, "name": asset_name},
            {"id": 102, "name": asset_name + ".sha256"},
        ],
    }
    output.write_text(json.dumps(payload), encoding="utf-8")
elif previous_tag and url.endswith("/releases/tags/" + previous_tag):
    payload = {
        "tag_name": previous_tag,
        "assets": [
            {"id": 201, "name": previous_asset_name},
            {"id": 202, "name": previous_asset_name + ".sha256"},
        ],
    }
    output.write_text(json.dumps(payload), encoding="utf-8")
elif url.endswith("/releases/assets/101") or url.endswith("/" + asset_name):
    shutil.copyfile(archive, output)
elif url.endswith("/releases/assets/102") or url.endswith("/" + asset_name + ".sha256"):
    shutil.copyfile(checksum, output)
elif previous_archive is not None and (
    url.endswith("/releases/assets/201") or url.endswith("/" + previous_asset_name)
):
    shutil.copyfile(previous_archive, output)
elif previous_checksum is not None and (
    url.endswith("/releases/assets/202") or url.endswith("/" + previous_asset_name + ".sha256")
):
    shutil.copyfile(previous_checksum, output)
else:
    print(f"mock curl does not recognize {url}", file=sys.stderr)
    raise SystemExit(22)
'''


def release_target() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        if machine in {"arm64", "aarch64"}:
            return "darwin-arm64"
        if machine in {"x86_64", "amd64"}:
            return "darwin-x86_64"
    if system == "Linux":
        if machine in {"arm64", "aarch64"}:
            return "linux-aarch64"
        if machine in {"x86_64", "amd64"}:
            return "linux-x86_64"
    raise unittest.SkipTest(f"unsupported installer test platform: {system} {machine}")


class RemoteInstallerRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        version_text = (ROOT / "studio/backend/mocap_studio/__init__.py").read_text(
            encoding="utf-8"
        )
        match = VERSION_PATTERN.search(version_text)
        if match is None:
            raise AssertionError("could not determine Mocap Studio version")
        cls.version = match.group(1)
        cls.tag = f"studio-v{cls.version}"
        cls.target = release_target()
        cls.asset_name = f"mocap-studio-{cls.version}-{cls.target}.tar.gz"
        cls.bundle_temporary = tempfile.TemporaryDirectory()
        cls.bundle_dir = Path(cls.bundle_temporary.name)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "studio/packaging/build_release.py"),
                "--version",
                cls.version,
                "--target",
                cls.target,
                "--source-date-epoch",
                "0",
                "--output-dir",
                str(cls.bundle_dir),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.archive = cls.bundle_dir / cls.asset_name
        cls.checksum = cls.bundle_dir / f"{cls.asset_name}.sha256"
        cls.previous_version = "0.0.1"
        cls.previous_tag = f"studio-v{cls.previous_version}"
        cls.previous_asset_name = (
            f"mocap-studio-{cls.previous_version}-{cls.target}.tar.gz"
        )
        cls.previous_archive = cls.bundle_dir / cls.previous_asset_name
        previous_files = build_release.payload_files(
            ROOT, cls.previous_version, cls.target
        )
        build_release.build_archive(
            cls.previous_archive,
            previous_files,
            cls.previous_version,
            0,
        )
        cls.previous_checksum = build_release.write_checksum(cls.previous_archive)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.bundle_temporary.cleanup()

    def mock_environment(
        self, root: Path
    ) -> tuple[Path, Path, dict[str, str]]:
        home = root / "home with space"
        mock_bin = root / "mock-bin"
        home.mkdir()
        mock_bin.mkdir()
        mock_curl = mock_bin / "curl"
        mock_curl.write_text(MOCK_CURL, encoding="utf-8")
        mock_curl.chmod(0o755)
        log_path = root / "curl.jsonl"
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(home),
                "SHELL": "/bin/sh",
                "PATH": f"{mock_bin}{os.pathsep}{environment['PATH']}",
                "MOCK_ARCHIVE": str(self.archive),
                "MOCK_CHECKSUM": str(self.checksum),
                "MOCK_ASSET_NAME": self.asset_name,
                "MOCK_CURL_LOG": str(log_path),
                "MOCK_RELEASE_TAG": self.tag,
                "MOCK_PREVIOUS_ARCHIVE": str(self.previous_archive),
                "MOCK_PREVIOUS_CHECKSUM": str(self.previous_checksum),
                "MOCK_PREVIOUS_ASSET_NAME": self.previous_asset_name,
                "MOCK_PREVIOUS_RELEASE_TAG": self.previous_tag,
            }
        )
        for name in (
            "MOCAP_STUDIO_RELEASE_BASE_URL",
            "MOCAP_STUDIO_REPOSITORY",
            "MOCAP_STUDIO_TAG_PREFIX",
        ):
            environment.pop(name, None)
        return home, log_path, environment

    def install(
        self,
        *,
        latest: bool = False,
        release_base_url: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, log_path, environment = self.mock_environment(root)
            command = [
                "/bin/sh",
                str(ROOT / "install.sh"),
                "--no-modify-path",
                "--no-desktop-integration",
            ]
            if not latest:
                command.extend(["--version", self.version])
            if release_base_url is not None:
                command.extend(["--release-base-url", release_base_url])
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            version_result = subprocess.run(
                [str(home / ".local/bin/mocap-studio"), "--version"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            ) if result.returncode == 0 else subprocess.CompletedProcess([], 1, "", "install failed")
            return result, records, version_result.stdout.strip()

    def assert_https_only(self, records: list[dict[str, object]]) -> None:
        for record in records:
            args = record["args"]
            self.assertIsInstance(args, list)
            self.assertIn("--proto", args)
            self.assertIn("--proto-redir", args)
            self.assertGreaterEqual(args.count("=https"), 2)

    def application_home(self, home: Path) -> Path:
        if platform.system() == "Darwin":
            return home / "Library" / "Application Support" / "Mocap Studio"
        return home / ".local" / "share" / "mocap-studio"

    def graphical_launcher(self, home: Path, app_home: Path) -> Path:
        if platform.system() == "Darwin":
            return home / "Applications" / "Mocap Studio.app" / "Contents" / "MacOS" / "mocap-studio"
        return app_home / "desktop" / "mocap-studio"

    def test_default_release_uses_github_api_assets(self) -> None:
        result, records, installed_version = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(installed_version, f"mocap-studio {self.version}")
        urls = [record["url"] for record in records]
        api_root = "https://api.github.com/repos/KevinMi2023p/MocapApi"
        self.assertEqual(
            urls,
            [
                f"{api_root}/releases/tags/{self.tag}",
                f"{api_root}/releases/assets/101",
                f"{api_root}/releases/assets/102",
            ],
        )
        self.assertNotIn("https://github.com/", "\n".join(urls))
        self.assertIn("Accept: application/vnd.github+json", records[0]["args"])
        self.assertIn("Accept: application/octet-stream", records[1]["args"])
        self.assertIn("Accept: application/octet-stream", records[2]["args"])
        self.assert_https_only(records)

    def test_latest_release_uses_github_api_metadata(self) -> None:
        result, records, installed_version = self.install(latest=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(installed_version, f"mocap-studio {self.version}")
        self.assertTrue(str(records[0]["url"]).endswith("/releases/latest"))
        self.assertEqual(len(records), 3)
        self.assert_https_only(records)

    def test_custom_release_base_url_bypasses_github_api(self) -> None:
        result, records, installed_version = self.install(release_base_url=CUSTOM_BASE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(installed_version, f"mocap-studio {self.version}")
        self.assertEqual(
            [record["url"] for record in records],
            [
                f"{CUSTOM_BASE}/{self.tag}/{self.asset_name}",
                f"{CUSTOM_BASE}/{self.tag}/{self.asset_name}.sha256",
            ],
        )
        self.assert_https_only(records)

    def test_installed_command_updates_noops_then_uninstalls_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, log_path, environment = self.mock_environment(root)
            command = home / ".local" / "bin" / "mocap-studio"
            initial = subprocess.run(
                [
                    "/bin/sh",
                    str(ROOT / "install.sh"),
                    "--version",
                    self.previous_version,
                    "--no-modify-path",
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(initial.returncode, 0, initial.stderr)
            old_install = command.resolve().parent.parent
            self.assertEqual(
                (old_install / "VERSION").read_text(encoding="utf-8").strip(),
                self.previous_version,
            )
            app_home = self.application_home(home)
            install_lock = app_home / ".mocap-studio-installer-lock"
            external_lock = root / "external lock"
            external_lock.mkdir()
            external_pid = external_lock / "pid"
            external_pid.write_text("999999999\n", encoding="utf-8")
            install_lock.symlink_to(external_lock, target_is_directory=True)
            symlink_blocked = subprocess.run(
                [str(command), "update"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(symlink_blocked.returncode, 0)
            self.assertTrue(external_pid.is_file())
            install_lock.unlink()
            external_pid.unlink()
            external_lock.rmdir()
            install_lock.mkdir()
            (install_lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
            blocked_update = subprocess.run(
                [str(command), "update"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(blocked_update.returncode, 0)
            self.assertIn("another install, update, or uninstall", blocked_update.stderr)
            self.assertEqual(command.resolve().parent.parent, old_install)
            (install_lock / "pid").unlink()
            install_lock.rmdir()
            install_lock.mkdir()
            (install_lock / "pid").write_text("999999999\n", encoding="utf-8")
            sentinel = app_home / "takes" / "sentinel" / "take.txt"
            sentinel.parent.mkdir(parents=True)
            sentinel.write_text("preserve me\n", encoding="utf-8")
            if platform.system() == "Darwin":
                original_surface = home / "Applications" / "Mocap Studio.app"
            else:
                original_surface = (
                    home
                    / ".local"
                    / "share"
                    / "applications"
                    / "io.github.KevinMi2023p.MocapStudio.desktop"
                )
            self.assertTrue(original_surface.exists())
            changed_home = root / "changed home"
            changed_home.mkdir()
            changed_xdg = root / "changed xdg data"
            environment["HOME"] = str(changed_home)
            environment["XDG_DATA_HOME"] = str(changed_xdg)

            log_path.write_text("", encoding="utf-8")
            update = subprocess.run(
                [str(command), "update"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(update.returncode, 0, update.stderr)
            self.assertIn(
                f"Updated Mocap Studio from {self.previous_version} to {self.version}",
                update.stdout,
            )
            current_install = command.resolve().parent.parent
            self.assertEqual(
                (current_install / "VERSION").read_text(encoding="utf-8").strip(),
                self.version,
            )
            self.assertNotEqual(current_install, old_install)
            self.assertFalse(install_lock.exists())
            installed_version = subprocess.run(
                [str(command), "--version"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(installed_version.returncode, 0, installed_version.stderr)
            self.assertEqual(
                installed_version.stdout.strip(), f"mocap-studio {self.version}"
            )
            self.assertTrue(old_install.is_dir(), "the running prior version must be retained")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve me\n")
            graphical_launcher = self.graphical_launcher(home, app_home)
            self.assertIn(
                os.readlink(command), graphical_launcher.read_text(encoding="utf-8")
            )
            self.assertTrue(original_surface.exists())
            if platform.system() == "Darwin":
                changed_surface = changed_home / "Applications" / "Mocap Studio.app"
            else:
                changed_surface = (
                    changed_xdg
                    / "applications"
                    / "io.github.KevinMi2023p.MocapStudio.desktop"
                )
            self.assertFalse(changed_surface.exists())
            update_records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [record["url"] for record in update_records],
                [
                    "https://api.github.com/repos/KevinMi2023p/MocapApi/releases/latest",
                    "https://api.github.com/repos/KevinMi2023p/MocapApi/releases/assets/101",
                    "https://api.github.com/repos/KevinMi2023p/MocapApi/releases/assets/102",
                ],
            )

            log_path.write_text("", encoding="utf-8")
            noop = subprocess.run(
                [str(command), "update"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(noop.returncode, 0, noop.stderr)
            self.assertIn(f"Mocap Studio {self.version} is already up to date.", noop.stdout)
            noop_records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(noop_records), 1)
            self.assertTrue(str(noop_records[0]["url"]).endswith("/releases/latest"))

            for invalid_arguments in (
                ["update", "--version", self.previous_version],
                ["update", "--local"],
                ["update", "--uninstall"],
            ):
                invalid = subprocess.run(
                    [str(command), *invalid_arguments],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(invalid.returncode, 0, invalid_arguments)

            uninstall = subprocess.run(
                [str(command), "uninstall", "--yes"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertIn("Recorded takes were preserved", uninstall.stdout)
            self.assertFalse(os.path.lexists(command))
            self.assertFalse(old_install.exists())
            self.assertFalse(current_install.exists())
            self.assertFalse(graphical_launcher.exists())
            self.assertFalse(original_surface.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve me\n")

    def test_update_preserves_desktop_integration_opt_out(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, _log_path, environment = self.mock_environment(root)
            physical_prefix = root / "physical prefix"
            physical_prefix.mkdir()
            prefix = root / "linked prefix"
            prefix.symlink_to(physical_prefix, target_is_directory=True)
            command = prefix / "bin" / "mocap-studio"
            initial = subprocess.run(
                [
                    "/bin/sh",
                    str(ROOT / "install.sh"),
                    "--version",
                    self.previous_version,
                    "--prefix",
                    str(prefix),
                    "--no-modify-path",
                    "--no-desktop-integration",
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(initial.returncode, 0, initial.stderr)
            app_home = prefix / "share" / "mocap-studio"
            graphical_launcher = self.graphical_launcher(home, app_home)
            self.assertFalse(graphical_launcher.exists())
            update = subprocess.run(
                [str(command), "update"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(update.returncode, 0, update.stderr)
            self.assertEqual(
                (command.resolve().parent.parent / "VERSION")
                .read_text(encoding="utf-8")
                .strip(),
                self.version,
            )
            self.assertFalse(graphical_launcher.exists())
            uninstall = subprocess.run(
                [str(command), "uninstall", "--yes"],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(uninstall.returncode, 0, uninstall.stderr)
            self.assertFalse(os.path.lexists(command))
            self.assertFalse((app_home / "versions").exists())


if __name__ == "__main__":
    unittest.main()

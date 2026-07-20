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
if "/releases/latest" in url or "/releases/tags/" in url:
    payload = {
        "tag_name": os.environ["MOCK_RELEASE_TAG"],
        "assets": [
            {"id": 101, "name": asset_name},
            {"id": 102, "name": asset_name + ".sha256"},
        ],
    }
    output.write_text(json.dumps(payload), encoding="utf-8")
elif url.endswith("/releases/assets/101") or url.endswith("/" + asset_name):
    shutil.copyfile(archive, output)
elif url.endswith("/releases/assets/102") or url.endswith("/" + asset_name + ".sha256"):
    shutil.copyfile(checksum, output)
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

    @classmethod
    def tearDownClass(cls) -> None:
        cls.bundle_temporary.cleanup()

    def install(
        self,
        *,
        latest: bool = False,
        release_base_url: str | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], str]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
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
                }
            )
            for name in (
                "MOCAP_STUDIO_RELEASE_BASE_URL",
                "MOCAP_STUDIO_REPOSITORY",
                "MOCAP_STUDIO_TAG_PREFIX",
            ):
                environment.pop(name, None)
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


if __name__ == "__main__":
    unittest.main()

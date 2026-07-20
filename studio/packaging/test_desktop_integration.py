#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p

from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path

from studio.packaging.desktop import desktop_integration


class DesktopIntegrationTests(unittest.TestCase):
    def arguments(
        self,
        operation: str,
        platform: str,
        root: Path,
        *,
        app_home: Path | None = None,
        home: Path | None = None,
        xdg_data_home: Path | None = None,
    ) -> list[str]:
        home = home or root / "home with space"
        install_dir = (app_home or root / "data home" / "mocap-studio") / "versions" / "v1"
        python = root / "tools" / "python3"
        return [
            operation,
            "--platform",
            platform,
            "--app-home",
            str(app_home or root / "data home" / "mocap-studio"),
            "--install-dir",
            str(install_dir),
            "--python",
            str(python),
            "--home",
            str(home),
            "--xdg-data-home",
            str(xdg_data_home or root / "xdg data"),
            "--version",
            "0.1.0",
        ]

    def test_linux_entry_uses_absolute_launcher_and_owned_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.arguments("install", "linux", root)
            self.assertEqual(desktop_integration.main(args), 0)

            app_home = root / "data home" / "mocap-studio"
            launcher = app_home / "desktop" / "mocap-studio"
            state_file = app_home / "desktop" / desktop_integration.STATE_FILENAME
            entry = (
                root
                / "xdg data"
                / "applications"
                / desktop_integration.LINUX_FILENAME
            )
            icon = (
                root
                / "xdg data"
                / "icons"
                / "hicolor"
                / "scalable"
                / "apps"
                / desktop_integration.ICON_FILENAME
            )
            entry_text = entry.read_text(encoding="utf-8")
            self.assertIn(f'Exec="{launcher}"', entry_text)
            self.assertNotIn("Exec=mocap-studio", entry_text)
            self.assertIn("--reuse-existing", launcher.read_text(encoding="utf-8"))
            self.assertIn("installer-managed icon", icon.read_text(encoding="utf-8"))
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
            self.assertTrue(state_file.is_file())

            self.assertEqual(
                desktop_integration.main(
                    self.arguments(
                        "uninstall",
                        "linux",
                        root,
                        xdg_data_home=root / "changed xdg data",
                    )
                ),
                0,
            )
            self.assertFalse(launcher.exists())
            self.assertFalse(state_file.exists())
            self.assertFalse(entry.exists())
            self.assertFalse(icon.exists())

    def test_linux_refuses_unmanaged_and_other_install_owners(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = (
                root
                / "xdg data"
                / "applications"
                / desktop_integration.LINUX_FILENAME
            )
            entry.parent.mkdir(parents=True)
            entry.write_text("unmanaged\n", encoding="utf-8")
            self.assertEqual(
                desktop_integration.main(self.arguments("check", "linux", root)), 1
            )
            self.assertEqual(entry.read_text(encoding="utf-8"), "unmanaged\n")

            entry.unlink()
            first_home = root / "first" / "mocap-studio"
            second_home = root / "second" / "mocap-studio"
            self.assertEqual(
                desktop_integration.main(
                    self.arguments("install", "linux", root, app_home=first_home)
                ),
                0,
            )
            self.assertEqual(
                desktop_integration.main(
                    self.arguments("check", "linux", root, app_home=second_home)
                ),
                1,
            )

    def test_macos_bundle_contains_plist_launcher_and_owner_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(
                desktop_integration.main(self.arguments("install", "darwin", root)), 0
            )
            bundle = root / "home with space" / "Applications" / "Mocap Studio.app"
            executable = bundle / "Contents" / "MacOS" / "mocap-studio"
            marker = bundle / "Contents" / "Resources" / ".mocap-studio-managed"
            state_file = (
                root
                / "data home"
                / "mocap-studio"
                / "desktop"
                / desktop_integration.STATE_FILENAME
            )
            with (bundle / "Contents" / "Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            self.assertEqual(info["CFBundleIdentifier"], desktop_integration.APPLICATION_ID)
            self.assertEqual(info["CFBundleShortVersionString"], "0.1.0")
            self.assertTrue(executable.stat().st_mode & 0o100)
            self.assertIn("--reuse-existing", executable.read_text(encoding="utf-8"))
            self.assertIn("owner=", marker.read_text(encoding="utf-8"))
            self.assertTrue(state_file.is_file())

            self.assertEqual(
                desktop_integration.main(
                    self.arguments(
                        "uninstall",
                        "darwin",
                        root,
                        home=root / "different home",
                    )
                ),
                0,
            )
            self.assertFalse(bundle.exists())
            self.assertFalse(state_file.exists())

    def test_macos_refuses_an_unmanaged_application_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = root / "home with space" / "Applications" / "Mocap Studio.app"
            bundle.mkdir(parents=True)
            sentinel = bundle / "keep.txt"
            sentinel.write_text("do not replace\n", encoding="utf-8")
            self.assertEqual(
                desktop_integration.main(self.arguments("install", "darwin", root)), 1
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not replace\n")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Offline checks for the axis-vm Windows VM tooling.

These tests need neither libvirt/qemu nor network access; they only assert
that argument validation, help output, and the shipped guest artifacts stay
consistent with the canonical stream contract.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = ROOT / "studio" / "tools" / "windows-vm"
SCRIPT = TOOL_DIR / "axis-vm.sh"
UNATTEND = TOOL_DIR / "autounattend.xml"
GUEST_SETUP = TOOL_DIR / "guest-setup.ps1"

OPERATIONS = (
    "--preflight",
    "--install-deps",
    "--define-network",
    "--download-axis",
    "--build-payload-iso",
    "--create",
    "--probe-usb",
    "--attach-usb",
    "--detach-usb",
    "--install-hotplug",
    "--firewall",
    "--start",
    "--shutdown",
    "--destroy",
    "--autostart",
    "--console",
    "--rdp",
    "--status",
    "--detach-internet",
    "--attach-internet",
    "--help",
)

STREAM_CONSTANTS = ("192.168.77.1", "7012", "YXZ", "52:54:00:6d:63:70")


class AxisVmScriptTests(unittest.TestCase):
    def run_script(
        self, *arguments: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(SCRIPT), *arguments],
            cwd=TOOL_DIR,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_posix_shell_syntax(self) -> None:
        result = subprocess.run(
            ["sh", "-n", str(SCRIPT)], text=True, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_spdx_header(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("# SPDX-License-Identifier: Apache-2.0", text)

    def test_help_exits_zero_and_mentions_every_operation(self) -> None:
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for operation in OPERATIONS:
            self.assertIn(operation, result.stdout)

    def test_unknown_flag_exits_nonzero_with_usage_pointer(self) -> None:
        result = self.run_script("--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown option: --bogus", result.stderr)
        self.assertIn("--help", result.stderr)

    def test_mutually_exclusive_operations_are_rejected(self) -> None:
        result = self.run_script("--start", "--status")
        self.assertEqual(result.returncode, 2)
        self.assertIn("one", result.stderr)

    def test_vid_pid_validation_rejects_garbage(self) -> None:
        for flag in ("--attach-usb", "--detach-usb", "--install-hotplug"):
            for value in ("zz:11", "1234", "12345:6789", "1234:56zz", "1234:", ""):
                with self.subTest(flag=flag, value=value):
                    result = self.run_script(flag, value)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn("VID:PID", result.stderr)

    def test_vid_pid_validation_happens_before_any_virsh_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "virsh-was-called"
            fake_virsh = Path(tmp) / "virsh"
            fake_virsh.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 99\n")
            fake_virsh.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{tmp}:{env.get('PATH', '')}"
            result = self.run_script("--attach-usb", "zz:11", env=env)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse(marker.exists(), "virsh ran before validation")

    def test_download_axis_accepts_only_two_or_three(self) -> None:
        for value in ("1", "4", "banana", ""):
            with self.subTest(value=value):
                result = self.run_script("--download-axis", value)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("2 or 3", result.stderr)

    def test_autostart_accepts_only_on_or_off(self) -> None:
        result = self.run_script("--autostart", "maybe")
        self.assertEqual(result.returncode, 2)
        self.assertIn("on", result.stderr)

    def test_create_requires_a_windows_iso(self) -> None:
        result = self.run_script("--create")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--windows-iso", result.stderr)

    def test_create_rejects_a_missing_windows_iso_before_virsh(self) -> None:
        result = self.run_script(
            "--create", "--windows-iso", "/nonexistent/axis-vm-test.iso"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not exist", result.stderr)

    def test_create_numeric_flags_are_validated(self) -> None:
        for flag in ("--disk-gib", "--ram-mib", "--vcpus"):
            with self.subTest(flag=flag):
                result = self.run_script(
                    "--create", "--windows-iso", "/nonexistent.iso", flag, "abc"
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("positive integer", result.stderr)

    def test_create_only_flags_are_rejected_elsewhere(self) -> None:
        result = self.run_script("--status", "--windows-iso", "/tmp/x.iso")
        self.assertEqual(result.returncode, 2)
        self.assertIn("only accepted with --create", result.stderr)

    def test_missing_value_is_reported(self) -> None:
        result = self.run_script("--attach-usb")
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires a value", result.stderr)

    def test_stream_constants_appear_in_the_script(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        for constant in STREAM_CONSTANTS:
            self.assertIn(constant, text)


class AutounattendTests(unittest.TestCase):
    def test_parses_as_wellformed_xml(self) -> None:
        ET.parse(UNATTEND)

    def test_password_placeholder_appears_exactly_once(self) -> None:
        text = UNATTEND.read_text(encoding="utf-8")
        self.assertEqual(text.count("@AXIS_VM_PASSWORD@"), 1)

    def test_contains_no_real_password(self) -> None:
        tree = ET.parse(UNATTEND)
        root = tree.getroot()
        parents = {child: parent for parent in root.iter() for child in parent}
        password_values = [
            element.text
            for element in root.iter()
            if element.tag.endswith("}Value")
            and parents[element].tag.endswith("}Password")
        ]
        self.assertEqual(password_values, ["@AXIS_VM_PASSWORD@"])

    def test_guest_contract_fields(self) -> None:
        text = UNATTEND.read_text(encoding="utf-8")
        self.assertIn("<Name>mocap</Name>", text)
        self.assertIn("<ComputerName>AXIS-STUDIO</ComputerName>", text)
        for bypass in ("BypassTPMCheck", "BypassSecureBootCheck", "BypassRAMCheck"):
            self.assertIn(bypass, text)


class GuestSetupTests(unittest.TestCase):
    def test_stream_contract_constants(self) -> None:
        text = GUEST_SETUP.read_text(encoding="utf-8")
        for constant in ("7012", "YXZ", "192.168.77.1"):
            self.assertIn(constant, text)

    def test_spdx_header(self) -> None:
        text = GUEST_SETUP.read_text(encoding="utf-8")
        self.assertIn("# SPDX-License-Identifier: Apache-2.0", text)


if __name__ == "__main__":
    unittest.main()

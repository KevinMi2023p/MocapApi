#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Exercise Mocap Studio's public help and syntax-error guidance."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "studio" / "packaging" / "mocap-studio"


class CliHelpTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(LAUNCHER), *arguments],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_root_help_and_help_alias_are_equivalent(self) -> None:
        direct = self.run_cli("--help")
        alias = self.run_cli("help")
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertEqual(alias.returncode, 0, alias.stderr)
        self.assertEqual(alias.stdout, direct.stdout)
        for value in (
            "--port",
            "--no-browser",
            "--reuse-existing",
            "--data-dir",
            "update",
            "uninstall",
            "completion",
        ):
            self.assertIn(value, direct.stdout)

    def test_command_help_works_without_an_install_or_network(self) -> None:
        cases = {
            ("update", "--help"): ("mocap-studio update", "--release-base-url"),
            ("help", "update"): ("mocap-studio update", "--release-base-url"),
            ("uninstall", "--help"): ("mocap-studio uninstall", "--yes"),
            ("help", "uninstall"): ("mocap-studio uninstall", "--yes"),
            ("completion", "--help"): ("completion install", "completion fish"),
            ("help", "completion"): ("completion install", "completion fish"),
        }
        for arguments, expected in cases.items():
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                for fragment in expected:
                    self.assertIn(fragment, result.stdout)

    def test_completion_emitters_return_the_packaged_static_assets(self) -> None:
        names = {
            "bash": "mocap-studio.bash",
            "zsh": "_mocap-studio",
            "fish": "mocap-studio.fish",
        }
        for shell, name in names.items():
            with self.subTest(shell=shell):
                result = self.run_cli("completion", shell)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = (LAUNCHER.parent / "completions" / name).read_text(
                    encoding="utf-8"
                )
                self.assertEqual(result.stdout, expected)

    def test_runtime_typos_suggest_a_safe_correction_and_root_help(self) -> None:
        for arguments, correction in (
            (("updtae",), "Did you mean 'update'?"),
            (("--prot", "9000"), "Did you mean '--port'?"),
            (("--no-brower",), "Did you mean '--no-browser'?"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn(correction, result.stderr)
                self.assertIn(
                    "Try 'mocap-studio --help' for more information.", result.stderr
                )

    def test_bad_values_and_command_topics_suggest_relevant_help(self) -> None:
        cases = (
            (("--port", "70000"), "Try 'mocap-studio --help'"),
            (("--port",), "Try 'mocap-studio --help'"),
            (("help", "udpate"), "Try 'mocap-studio --help'"),
            (("completion", "bashh"), "Try 'mocap-studio completion --help'"),
            (("completion",), "Try 'mocap-studio completion --help'"),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                result = self.run_cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stderr)


if __name__ == "__main__":
    unittest.main()

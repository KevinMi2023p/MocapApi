#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Validate the public shell-completion grammar and Bash behavior."""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


COMPLETION_DIR = Path(__file__).resolve().parent / "completions"
ASSETS = {
    "bash": COMPLETION_DIR / "mocap-studio.bash",
    "zsh": COMPLETION_DIR / "_mocap-studio",
    "fish": COMPLETION_DIR / "mocap-studio.fish",
}
EXPECTED_GRAMMAR = {
    "top-commands": {"help", "update", "uninstall", "completion"},
    "top-options": {
        "-h",
        "--help",
        "--port",
        "--no-browser",
        "--reuse-existing",
        "--data-dir",
        "--version",
    },
    "help-topics": {"update", "uninstall", "completion"},
    "update-options": {
        "-h",
        "--help",
        "--launch",
        "--repo",
        "--release-base-url",
        "--tag-prefix",
        "--no-desktop-integration",
    },
    "uninstall-options": {"-h", "--help", "--yes"},
    "completion-values": {"install", "bash", "zsh", "fish"},
}


def declared_grammar(path: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("# grammar "):
            continue
        name, values = line.removeprefix("# grammar ").split(":", 1)
        if name in result:
            raise AssertionError(f"duplicate grammar declaration {name!r} in {path}")
        words = shlex.split(values)
        if len(words) != len(set(words)):
            raise AssertionError(f"duplicate grammar value in {name!r} in {path}")
        result[name] = set(words)
    return result


class ShellCompletionAssetTests(unittest.TestCase):
    def test_assets_declare_the_exact_public_grammar(self) -> None:
        for shell, path in ASSETS.items():
            with self.subTest(shell=shell):
                self.assertTrue(path.is_file())
                self.assertEqual(declared_grammar(path), EXPECTED_GRAMMAR)
                implementation = "\n".join(
                    line
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if not line.startswith("# grammar ")
                )
                for values in EXPECTED_GRAMMAR.values():
                    for value in values:
                        if shell == "fish" and value == "-h":
                            representation = "-s h"
                        elif shell == "fish" and value.startswith("--"):
                            representation = f"-l {value[2:]}"
                        else:
                            representation = value
                        self.assertIn(representation, implementation)

    def test_assets_pass_available_shell_syntax_checks(self) -> None:
        checked: set[str] = set()
        for shell, path in ASSETS.items():
            executable = shutil.which(shell)
            if executable is None:
                continue
            with self.subTest(shell=shell):
                result = subprocess.run(
                    [executable, "-n", str(path)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                checked.add(shell)
        self.assertIn("bash", checked)


class BashCompletionBehaviorTests(unittest.TestCase):
    def complete(self, words: list[str], *, cwd: Path | None = None) -> list[str]:
        assignments = " ".join(shlex.quote(word) for word in words)
        script = f"""
source {shlex.quote(str(ASSETS['bash']))}
COMP_WORDS=({assignments})
COMP_CWORD={len(words) - 1}
_mocap_studio_complete
if (( ${{#COMPREPLY[@]}} )); then
    printf '%s\\0' "${{COMPREPLY[@]}}"
fi
"""
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", script],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        if not result.stdout:
            return []
        return [item.decode() for item in result.stdout.rstrip(b"\0").split(b"\0")]

    def assert_candidates(self, words: list[str], expected: set[str]) -> None:
        actual = self.complete(words)
        self.assertEqual(set(actual), expected)
        self.assertEqual(len(actual), len(set(actual)), "completion returned duplicates")

    def test_top_level_and_command_inventories(self) -> None:
        top_level = EXPECTED_GRAMMAR["top-commands"] | EXPECTED_GRAMMAR["top-options"]
        self.assert_candidates(["mocap-studio", ""], top_level)
        self.assert_candidates(
            ["/opt/Mocap Studio/bin/mocap-studio", ""], top_level
        )
        self.assert_candidates(
            ["mocap-studio", "help", ""], EXPECTED_GRAMMAR["help-topics"]
        )
        self.assert_candidates(
            ["mocap-studio", "update", ""], EXPECTED_GRAMMAR["update-options"]
        )
        self.assert_candidates(
            ["mocap-studio", "uninstall", ""],
            EXPECTED_GRAMMAR["uninstall-options"],
        )
        self.assert_candidates(
            ["mocap-studio", "completion", ""],
            EXPECTED_GRAMMAR["completion-values"],
        )

    def test_prefix_filtering_and_option_values(self) -> None:
        self.assert_candidates(
            ["mocap-studio", "--re"], {"--reuse-existing"}
        )
        self.assert_candidates(
            ["mocap-studio", "update", "--rel"], {"--release-base-url"}
        )
        self.assert_candidates(["mocap-studio", "completion", "z"], {"zsh"})
        self.assert_candidates(
            ["mocap-studio", "completion", "in"], {"install"}
        )
        self.assert_candidates(["mocap-studio", "--port", ""], set())
        self.assert_candidates(["mocap-studio", "update", "--repo", ""], set())
        self.assert_candidates(
            ["mocap-studio", "update", "--release-base-url", ""], set()
        )
        self.assert_candidates(
            ["mocap-studio", "update", "--tag-prefix", ""], set()
        )

    def test_directory_values_preserve_spaces_and_exclude_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "alpha").mkdir()
            (root / "al pine").mkdir()
            (root / "al-file").write_text("not a directory\n", encoding="utf-8")
            expected = {"alpha", "al pine"}
            self.assertEqual(
                set(self.complete(["mocap-studio", "--data-dir", "al"], cwd=root)),
                expected,
            )
            self.assertEqual(
                set(self.complete(["mocap-studio", "--data-dir=al"], cwd=root)),
                {f"--data-dir={item}" for item in expected},
            )

    def test_option_values_that_look_like_commands_are_skipped(self) -> None:
        expected = EXPECTED_GRAMMAR["top-commands"] | EXPECTED_GRAMMAR["top-options"]
        self.assert_candidates(
            ["mocap-studio", "--data-dir", "update", ""], expected
        )

    def test_script_registers_the_bare_command(self) -> None:
        result = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                f"source {shlex.quote(str(ASSETS['bash']))}; complete -p mocap-studio",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-F _mocap_studio_complete mocap-studio", result.stdout)


if __name__ == "__main__":
    unittest.main()

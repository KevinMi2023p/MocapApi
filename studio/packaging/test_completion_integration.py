#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Test collision-safe installation of per-user shell completion files."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from studio.packaging.completions import completion_integration


class CompletionIntegrationTests(unittest.TestCase):
    def create_install(self, app_home: Path, version: str) -> Path:
        install_dir = app_home / "versions" / version
        completion_dir = install_dir / "completions"
        completion_dir.mkdir(parents=True)
        (completion_dir / "mocap-studio.bash").write_text(
            f"# Bash completion asset {version}\n",
            encoding="utf-8",
        )
        (completion_dir / "_mocap-studio").write_text(
            f"#compdef mocap-studio\n# Zsh completion asset {version}\n",
            encoding="utf-8",
        )
        (completion_dir / "mocap-studio.fish").write_text(
            f"# Fish completion asset {version}\n",
            encoding="utf-8",
        )
        return install_dir

    def arguments(
        self,
        operation: str,
        *,
        app_home: Path,
        install_dir: Path,
        home: Path,
        xdg_data_home: Path,
        xdg_config_home: Path,
        shell: str = "/bin/bash",
        zdotdir: Path | None = None,
        modify_profile: bool = False,
    ) -> list[str]:
        result = [
            operation,
            "--app-home",
            str(app_home),
            "--install-dir",
            str(install_dir),
            "--home",
            str(home),
            "--shell",
            shell,
            "--xdg-data-home",
            str(xdg_data_home),
            "--xdg-config-home",
            str(xdg_config_home),
        ]
        if zdotdir is not None:
            result.extend(("--zdotdir", str(zdotdir)))
        if modify_profile:
            result.append("--modify-profile")
        return result

    def invoke(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = completion_integration.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_install_check_and_update_reuse_saved_original_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_home = root / "application data" / "mocap-studio"
            original_home = root / "original home"
            original_data = root / "original xdg data"
            original_config = root / "original xdg config"
            first_install = self.create_install(app_home, "v1")
            first_args = self.arguments(
                "install",
                app_home=app_home,
                install_dir=first_install,
                home=original_home,
                xdg_data_home=original_data,
                xdg_config_home=original_config,
                modify_profile=True,
            )

            check_args = first_args.copy()
            check_args[0] = "check"
            self.assertEqual(self.invoke(check_args)[0], 0)
            self.assertEqual(self.invoke(first_args)[0], 0)

            owner = completion_integration.owner_for(app_home)
            targets = {
                "bash": original_data
                / "bash-completion"
                / "completions"
                / "mocap-studio",
                "zsh": original_data
                / "zsh"
                / "site-functions"
                / "_mocap-studio",
                "fish": original_config
                / "fish"
                / "completions"
                / "mocap-studio.fish",
            }
            for shell, target in targets.items():
                with self.subTest(shell=shell):
                    content = target.read_text(encoding="utf-8")
                    self.assertIn(
                        f"# format={completion_integration.FORMAT}\n", content
                    )
                    self.assertIn(f"# owner={owner}\n", content)
                    self.assertIn(f"completion asset v1", content)
                    self.assertEqual(target.stat().st_mode & 0o777, 0o644)

            self.assertEqual(
                targets["zsh"].read_text(encoding="utf-8").splitlines()[0],
                "#compdef mocap-studio",
            )
            profile = original_home / ".bashrc"
            self.assertIn(completion_integration.BLOCK_START, profile.read_text())
            state_file = (
                app_home
                / "completions"
                / completion_integration.STATE_FILENAME
            )
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["owner"], owner)
            self.assertEqual(state["bash"], str(targets["bash"]))
            self.assertEqual(state["zsh"], str(targets["zsh"]))
            self.assertEqual(state["fish"], str(targets["fish"]))
            self.assertEqual(state["profile"], str(profile))
            self.assertEqual(state["profile_shell"], "bash")
            self.assertEqual(state_file.stat().st_mode & 0o777, 0o644)

            second_install = self.create_install(app_home, "v2")
            changed_home = root / "changed home"
            changed_data = root / "changed xdg data"
            changed_config = root / "changed xdg config"
            update_args = self.arguments(
                "install",
                app_home=app_home,
                install_dir=second_install,
                home=changed_home,
                xdg_data_home=changed_data,
                xdg_config_home=changed_config,
                shell="/bin/zsh",
                zdotdir=root / "changed zsh config",
                modify_profile=True,
            )
            saved_check_args = update_args.copy()
            saved_check_args[0] = "check"
            self.assertEqual(self.invoke(saved_check_args)[0], 0)
            self.assertEqual(self.invoke(update_args)[0], 0)

            for shell, target in targets.items():
                with self.subTest(updated_shell=shell):
                    self.assertIn(
                        "completion asset v2", target.read_text(encoding="utf-8")
                    )
                    self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertEqual(
                profile.read_text(encoding="utf-8").count(
                    completion_integration.BLOCK_START
                ),
                1,
            )
            self.assertFalse(changed_home.exists())
            self.assertFalse(changed_data.exists())
            self.assertFalse(changed_config.exists())
            self.assertFalse((root / "changed zsh config").exists())

    def test_bash_and_zsh_profile_blocks_are_idempotent_and_removed(self) -> None:
        for shell in ("bash", "zsh"):
            with self.subTest(shell=shell), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                app_home = root / "app" / "mocap-studio"
                install_dir = self.create_install(app_home, "v1")
                home = root / "home"
                zdotdir = root / "zsh configuration" if shell == "zsh" else None
                profile = (zdotdir or home) / (
                    ".zshrc" if shell == "zsh" else ".bashrc"
                )
                profile.parent.mkdir(parents=True)
                profile.write_text("# user configuration\n", encoding="utf-8")
                profile.chmod(0o600)
                args = self.arguments(
                    "install",
                    app_home=app_home,
                    install_dir=install_dir,
                    home=home,
                    xdg_data_home=root / "data",
                    xdg_config_home=root / "config",
                    shell=f"/usr/bin/{shell}",
                    zdotdir=zdotdir,
                    modify_profile=True,
                )

                self.assertEqual(self.invoke(args)[0], 0)
                self.assertEqual(self.invoke(args)[0], 0)
                installed_profile = profile.read_text(encoding="utf-8")
                self.assertEqual(
                    installed_profile.count(completion_integration.BLOCK_START), 1
                )
                self.assertEqual(
                    installed_profile.count(completion_integration.BLOCK_END), 1
                )
                self.assertIn("# user configuration\n", installed_profile)
                self.assertEqual(profile.stat().st_mode & 0o777, 0o600)

                uninstall_args = args.copy()
                uninstall_args[0] = "uninstall"
                self.assertEqual(self.invoke(uninstall_args)[0], 0)
                remaining_profile = profile.read_text(encoding="utf-8")
                self.assertIn("# user configuration\n", remaining_profile)
                self.assertNotIn(
                    completion_integration.BLOCK_START, remaining_profile
                )
                self.assertNotIn(completion_integration.BLOCK_END, remaining_profile)
                self.assertEqual(profile.stat().st_mode & 0o777, 0o600)
                self.assertFalse(
                    (
                        app_home
                        / "completions"
                        / completion_integration.STATE_FILENAME
                    ).exists()
                )

    def test_unmanaged_target_collision_is_preserved_and_prevents_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_home = root / "app" / "mocap-studio"
            install_dir = self.create_install(app_home, "v1")
            data_home = root / "data"
            config_home = root / "config"
            collision = (
                config_home / "fish" / "completions" / "mocap-studio.fish"
            )
            collision.parent.mkdir(parents=True)
            collision.write_text("# user's completion\n", encoding="utf-8")
            args = self.arguments(
                "check",
                app_home=app_home,
                install_dir=install_dir,
                home=root / "home",
                xdg_data_home=data_home,
                xdg_config_home=config_home,
            )

            result, _, stderr = self.invoke(args)
            self.assertEqual(result, 1)
            self.assertIn("not owned by this installation", stderr)
            args[0] = "install"
            result, _, stderr = self.invoke(args)
            self.assertEqual(result, 1)
            self.assertIn("not owned by this installation", stderr)
            self.assertEqual(
                collision.read_text(encoding="utf-8"), "# user's completion\n"
            )
            self.assertFalse(
                (data_home / "bash-completion" / "completions" / "mocap-studio").exists()
            )
            self.assertFalse(
                (data_home / "zsh" / "site-functions" / "_mocap-studio").exists()
            )
            self.assertFalse(
                (
                    app_home
                    / "completions"
                    / completion_integration.STATE_FILENAME
                ).exists()
            )

    def test_profile_symlink_is_preserved_while_its_target_is_managed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_home = root / "app" / "mocap-studio"
            install_dir = self.create_install(app_home, "v1")
            home = root / "home"
            profile_target = root / "dotfiles" / "bashrc"
            profile_target.parent.mkdir(parents=True)
            profile_target.write_text("# linked user configuration\n", encoding="utf-8")
            home.mkdir()
            profile_link = home / ".bashrc"
            profile_link.symlink_to(profile_target)
            args = self.arguments(
                "install",
                app_home=app_home,
                install_dir=install_dir,
                home=home,
                xdg_data_home=root / "data",
                xdg_config_home=root / "config",
                modify_profile=True,
            )

            self.assertEqual(self.invoke(args)[0], 0)
            self.assertTrue(profile_link.is_symlink())
            self.assertIn(
                completion_integration.BLOCK_START,
                profile_target.read_text(encoding="utf-8"),
            )
            args[0] = "uninstall"
            self.assertEqual(self.invoke(args)[0], 0)
            self.assertTrue(profile_link.is_symlink())
            self.assertNotIn(
                completion_integration.BLOCK_START,
                profile_target.read_text(encoding="utf-8"),
            )

    def test_uninstall_preserves_a_modified_completion_target_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app_home = root / "app" / "mocap-studio"
            install_dir = self.create_install(app_home, "v1")
            data_home = root / "data"
            config_home = root / "config"
            args = self.arguments(
                "install",
                app_home=app_home,
                install_dir=install_dir,
                home=root / "home",
                xdg_data_home=data_home,
                xdg_config_home=config_home,
                modify_profile=True,
            )
            self.assertEqual(self.invoke(args)[0], 0)

            bash_target = (
                data_home / "bash-completion" / "completions" / "mocap-studio"
            )
            zsh_target = data_home / "zsh" / "site-functions" / "_mocap-studio"
            fish_target = config_home / "fish" / "completions" / "mocap-studio.fish"
            state_file = (
                app_home
                / "completions"
                / completion_integration.STATE_FILENAME
            )
            bash_target.write_text(
                "# user replaced this completion after installation\n",
                encoding="utf-8",
            )

            args[0] = "uninstall"
            result, _, stderr = self.invoke(args)
            self.assertEqual(result, 0)
            self.assertIn("preserving unmanaged Bash completion", stderr)
            self.assertEqual(
                bash_target.read_text(encoding="utf-8"),
                "# user replaced this completion after installation\n",
            )
            self.assertFalse(zsh_target.exists())
            self.assertFalse(fish_target.exists())
            self.assertTrue(state_file.is_file())
            self.assertNotIn(
                completion_integration.BLOCK_START,
                (root / "home" / ".bashrc").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()

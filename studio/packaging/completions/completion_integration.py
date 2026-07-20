#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Install collision-safe, per-user shell completion for Mocap Studio."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


FORMAT = "mocap-studio-completion-v1"
STATE_FORMAT = "mocap-studio-completion-state-v1"
STATE_FILENAME = ".mocap-studio-integration.json"
BLOCK_START = "# >>> mocap-studio managed completion >>>"
BLOCK_END = "# <<< mocap-studio managed completion <<<"


class CompletionIntegrationError(RuntimeError):
    """Raised when an unmanaged path would be changed."""


@dataclass(frozen=True)
class CompletionPaths:
    app_home: Path
    install_dir: Path
    owner: str
    state_file: Path
    bash: Path
    zsh: Path
    fish: Path
    profile: Path | None
    profile_shell: str | None


def absolute_path(raw: str, label: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise CompletionIntegrationError(f"{label} must be an absolute path: {raw}")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise CompletionIntegrationError(f"{label} contains a control character")
    return Path(os.path.abspath(raw))


def optional_absolute(raw: str, fallback: Path, label: str) -> Path:
    if not raw:
        return fallback
    if not Path(raw).is_absolute():
        return fallback
    return absolute_path(raw, label)


def shell_profile_path(candidate: Path) -> Path:
    """Keep a dotfile symlink intact while managing its regular-file target."""

    if not candidate.is_symlink():
        return candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise CompletionIntegrationError(
            f"shell profile symlink cannot be resolved: {candidate}"
        ) from error
    if not resolved.is_file() or resolved.is_symlink():
        raise CompletionIntegrationError(
            f"shell profile symlink does not target a regular file: {candidate}"
        )
    return absolute_path(str(resolved), "shell profile target")


def owner_for(app_home: Path) -> str:
    return hashlib.sha256(os.fsencode(app_home)).hexdigest()


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def load_state(state_file: Path, owner: str) -> dict[str, object] | None:
    if not lexists(state_file):
        return None
    if state_file.is_symlink() or not state_file.is_file():
        raise CompletionIntegrationError(
            f"completion state is not a regular file: {state_file}"
        )
    if state_file.stat().st_size > 32 * 1024:
        raise CompletionIntegrationError(f"completion state is too large: {state_file}")
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CompletionIntegrationError(
            f"completion state is invalid: {state_file}: {error}"
        ) from error
    expected = {"format", "owner", "bash", "zsh", "fish", "profile", "profile_shell"}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise CompletionIntegrationError(f"completion state is invalid: {state_file}")
    if payload["format"] != STATE_FORMAT or payload["owner"] != owner:
        raise CompletionIntegrationError(f"completion state is not owned here: {state_file}")
    for name in ("bash", "zsh", "fish"):
        if not isinstance(payload[name], str):
            raise CompletionIntegrationError(f"completion state is invalid: {state_file}")
    if payload["profile"] is not None and not isinstance(payload["profile"], str):
        raise CompletionIntegrationError(f"completion state is invalid: {state_file}")
    if payload["profile_shell"] not in (None, "bash", "zsh"):
        raise CompletionIntegrationError(f"completion state is invalid: {state_file}")
    if (payload["profile"] is None) != (payload["profile_shell"] is None):
        raise CompletionIntegrationError(f"completion state is invalid: {state_file}")
    return payload


def completion_paths(args: argparse.Namespace) -> CompletionPaths:
    home = absolute_path(args.home, "home")
    app_home = absolute_path(args.app_home, "application home")
    install_dir = absolute_path(args.install_dir, "installed version")
    owner = owner_for(app_home)
    state_file = app_home / "completions" / STATE_FILENAME
    saved = load_state(state_file, owner)
    if saved is not None:
        profile = (
            absolute_path(str(saved["profile"]), "saved shell profile")
            if saved["profile"] is not None
            else None
        )
        return CompletionPaths(
            app_home=app_home,
            install_dir=install_dir,
            owner=owner,
            state_file=state_file,
            bash=absolute_path(str(saved["bash"]), "saved Bash completion"),
            zsh=absolute_path(str(saved["zsh"]), "saved Zsh completion"),
            fish=absolute_path(str(saved["fish"]), "saved Fish completion"),
            profile=profile,
            profile_shell=str(saved["profile_shell"]) if profile is not None else None,
        )

    data_home = optional_absolute(
        args.xdg_data_home, home / ".local" / "share", "XDG data home"
    )
    config_home = optional_absolute(
        args.xdg_config_home, home / ".config", "XDG config home"
    )
    if args.bash_completion_user_dir and Path(args.bash_completion_user_dir).is_absolute():
        bash_directory = absolute_path(
            args.bash_completion_user_dir, "Bash completion user directory"
        )
    else:
        bash_directory = data_home / "bash-completion" / "completions"

    profile: Path | None = None
    profile_shell: str | None = None
    if args.modify_profile:
        shell_name = Path(args.shell).name
        if shell_name == "bash":
            profile = shell_profile_path(home / ".bashrc")
            profile_shell = "bash"
        elif shell_name == "zsh":
            zdotdir = optional_absolute(args.zdotdir, home, "Zsh configuration home")
            profile = shell_profile_path(zdotdir / ".zshrc")
            profile_shell = "zsh"

    return CompletionPaths(
        app_home=app_home,
        install_dir=install_dir,
        owner=owner,
        state_file=state_file,
        bash=bash_directory / "mocap-studio",
        zsh=data_home / "zsh" / "site-functions" / "_mocap-studio",
        fish=config_home / "fish" / "completions" / "mocap-studio.fish",
        profile=profile,
        profile_shell=profile_shell,
    )


def owned_regular_file(path: Path, owner: str) -> bool:
    if not lexists(path) or path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size > 1024 * 1024:
            return False
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return f"format={FORMAT}" in content and f"owner={owner}" in content


def source_path(paths: CompletionPaths, shell: str) -> Path:
    names = {
        "bash": "mocap-studio.bash",
        "zsh": "_mocap-studio",
        "fish": "mocap-studio.fish",
    }
    return paths.install_dir / "completions" / names[shell]


def managed_content(paths: CompletionPaths, shell: str) -> str:
    source = source_path(paths, shell)
    if source.is_symlink() or not source.is_file():
        raise CompletionIntegrationError(f"completion asset is missing: {source}")
    content = source.read_text(encoding="utf-8")
    marker = f"# format={FORMAT}\n# owner={paths.owner}\n"
    if shell == "zsh" and content.startswith("#compdef "):
        first_line, remainder = content.split("\n", 1)
        return f"{first_line}\n{marker}{remainder}"
    return marker + content


def profile_block(paths: CompletionPaths) -> str:
    assert paths.profile is not None and paths.profile_shell is not None
    target = paths.bash if paths.profile_shell == "bash" else paths.zsh
    quoted = shlex.quote(str(target))
    if paths.profile_shell == "bash":
        body = f"if [ -r {quoted} ]; then\n    . {quoted}\nfi\n"
    else:
        body = (
            f"if [[ -r {quoted} ]]; then\n"
            "    if (( ! $+functions[compdef] )); then\n"
            "        autoload -Uz compinit && compinit\n"
            "    fi\n"
            f"    source {quoted}\n"
            "fi\n"
        )
    return (
        f"{BLOCK_START}\n"
        f"# format={FORMAT}\n"
        f"# owner={paths.owner}\n"
        f"{body}"
        f"{BLOCK_END}\n"
    )


def read_profile(path: Path) -> str:
    if not lexists(path):
        return ""
    if path.is_symlink() or not path.is_file():
        raise CompletionIntegrationError(f"shell profile is not a regular file: {path}")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise CompletionIntegrationError(f"shell profile is too large: {path}")
    return path.read_text(encoding="utf-8")


def check_profile(paths: CompletionPaths) -> None:
    if paths.profile is None:
        return
    content = read_profile(paths.profile)
    start_count = content.count(BLOCK_START)
    end_count = content.count(BLOCK_END)
    if start_count == 0 and end_count == 0:
        return
    block = profile_block(paths)
    if start_count == 1 and end_count == 1 and block in content:
        return
    raise CompletionIntegrationError(
        f"shell profile contains an unmanaged or incomplete completion block: {paths.profile}"
    )


def check(paths: CompletionPaths) -> None:
    for shell, target in (("Bash", paths.bash), ("Zsh", paths.zsh), ("Fish", paths.fish)):
        if lexists(target) and not owned_regular_file(target, paths.owner):
            raise CompletionIntegrationError(
                f"{shell} completion path already exists and is not owned by this installation: {target}"
            )
    check_profile(paths)
    for shell in ("bash", "zsh", "fish"):
        managed_content(paths, shell)


def atomic_write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        temporary_path.chmod(mode)
        os.replace(temporary_path, path)
    finally:
        if lexists(temporary_path):
            temporary_path.unlink()


def write_state(paths: CompletionPaths) -> None:
    content = json.dumps(
        {
            "format": STATE_FORMAT,
            "owner": paths.owner,
            "bash": str(paths.bash),
            "zsh": str(paths.zsh),
            "fish": str(paths.fish),
            "profile": str(paths.profile) if paths.profile is not None else None,
            "profile_shell": paths.profile_shell,
        },
        sort_keys=True,
    )
    atomic_write(paths.state_file, content + "\n")


def install(paths: CompletionPaths) -> None:
    check(paths)
    for shell, target in (("bash", paths.bash), ("zsh", paths.zsh), ("fish", paths.fish)):
        atomic_write(target, managed_content(paths, shell))
        print(f"{shell.capitalize()} completion: {target}")
    if paths.profile is not None:
        content = read_profile(paths.profile)
        block = profile_block(paths)
        if block not in content:
            if content and not content.endswith("\n"):
                content += "\n"
            if content and not content.endswith("\n\n"):
                content += "\n"
            content += block
            mode = paths.profile.stat().st_mode & 0o777 if paths.profile.exists() else 0o644
            atomic_write(paths.profile, content, mode)
        print(f"Completion startup file: {paths.profile}")
    write_state(paths)


def remove_owned(path: Path, owner: str, label: str) -> bool:
    if not lexists(path):
        return True
    if owned_regular_file(path, owner):
        path.unlink()
        return True
    print(f"shell completion: preserving unmanaged {label} {path}", file=sys.stderr)
    return False


def uninstall(paths: CompletionPaths) -> None:
    removed = True
    for label, target in (("Bash completion", paths.bash), ("Zsh completion", paths.zsh), ("Fish completion", paths.fish)):
        removed = remove_owned(target, paths.owner, label) and removed
    if paths.profile is not None and lexists(paths.profile):
        try:
            content = read_profile(paths.profile)
            block = profile_block(paths)
            if block in content:
                mode = paths.profile.stat().st_mode & 0o777
                atomic_write(paths.profile, content.replace(block, "", 1), mode)
            elif BLOCK_START in content or BLOCK_END in content:
                print(
                    f"shell completion: preserving modified profile block in {paths.profile}",
                    file=sys.stderr,
                )
                removed = False
        except CompletionIntegrationError as error:
            print(f"shell completion: {error}", file=sys.stderr)
            removed = False
    if removed and lexists(paths.state_file):
        paths.state_file.unlink()
        try:
            paths.state_file.parent.rmdir()
        except OSError:
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("operation", choices=("check", "install", "uninstall"))
    result.add_argument("--app-home", required=True)
    result.add_argument("--install-dir", required=True)
    result.add_argument("--home", required=True)
    result.add_argument("--shell", default="")
    result.add_argument("--xdg-data-home", default="")
    result.add_argument("--xdg-config-home", default="")
    result.add_argument("--bash-completion-user-dir", default="")
    result.add_argument("--zdotdir", default="")
    result.add_argument("--modify-profile", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        paths = completion_paths(args)
        if args.operation == "check":
            check(paths)
        elif args.operation == "install":
            install(paths)
        else:
            uninstall(paths)
    except (CompletionIntegrationError, OSError, UnicodeError, ValueError) as error:
        print(f"shell completion: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

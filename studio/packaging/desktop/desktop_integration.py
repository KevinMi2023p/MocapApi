#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Install collision-safe per-user Linux and macOS launch surfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


APPLICATION_ID = "io.github.KevinMi2023p.MocapStudio"
LINUX_FILENAME = f"{APPLICATION_ID}.desktop"
ICON_FILENAME = f"{APPLICATION_ID}.svg"
APP_BUNDLE_NAME = "Mocap Studio.app"
FORMAT = "mocap-studio-desktop-v1"
STATE_FILENAME = ".mocap-studio-integration.json"
STATE_FORMAT = "mocap-studio-integration-v1"


class DesktopIntegrationError(RuntimeError):
    """Raised when an unmanaged file occupies an integration path."""


@dataclass(frozen=True)
class IntegrationPaths:
    platform: str
    app_home: Path
    install_dir: Path
    python: Path
    owner: str
    location_root: Path
    state_file: Path
    launcher: Path
    entry: Path | None = None
    icon: Path | None = None
    bundle: Path | None = None


def absolute_path(raw: str, label: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise DesktopIntegrationError(f"{label} must be an absolute path: {raw}")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        raise DesktopIntegrationError(f"{label} contains a control character")
    # Normalize dot segments without resolving symlinks. This gives ownership
    # markers a stable identity while preserving intentional per-user symlinks.
    return Path(os.path.abspath(raw))


def owner_for(app_home: Path) -> str:
    return hashlib.sha256(os.fsencode(app_home)).hexdigest()


def integration_paths(args: argparse.Namespace) -> IntegrationPaths:
    home = absolute_path(args.home, "home")
    app_home = absolute_path(args.app_home, "application home")
    install_dir = absolute_path(args.install_dir, "installed version")
    python = absolute_path(args.python, "Python interpreter")
    owner = owner_for(app_home)
    launcher = app_home / "desktop" / "mocap-studio"
    state_file = app_home / "desktop" / STATE_FILENAME
    saved_location = load_saved_location(state_file, owner, args.platform)
    if args.platform == "linux":
        if saved_location is not None:
            data_home = saved_location
        elif args.xdg_data_home and Path(args.xdg_data_home).is_absolute():
            data_home = absolute_path(args.xdg_data_home, "XDG data home")
        else:
            data_home = home / ".local" / "share"
        return IntegrationPaths(
            platform=args.platform,
            app_home=app_home,
            install_dir=install_dir,
            python=python,
            owner=owner,
            location_root=data_home,
            state_file=state_file,
            launcher=launcher,
            entry=data_home / "applications" / LINUX_FILENAME,
            icon=data_home / "icons" / "hicolor" / "scalable" / "apps" / ICON_FILENAME,
        )
    bundle_home = saved_location or home
    return IntegrationPaths(
        platform=args.platform,
        app_home=app_home,
        install_dir=install_dir,
        python=python,
        owner=owner,
        location_root=bundle_home,
        state_file=state_file,
        launcher=launcher,
        bundle=bundle_home / "Applications" / APP_BUNDLE_NAME,
    )


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def load_saved_location(state_file: Path, owner: str, platform: str) -> Path | None:
    if not lexists(state_file):
        return None
    if state_file.is_symlink() or not state_file.is_file():
        raise DesktopIntegrationError(
            f"integration state is not a regular file: {state_file}"
        )
    if state_file.stat().st_size > 16 * 1024:
        raise DesktopIntegrationError(f"integration state is too large: {state_file}")
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    expected_keys = {"format", "owner", "platform", "location_root"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise DesktopIntegrationError(f"integration state is invalid: {state_file}")
    if (
        payload["format"] != STATE_FORMAT
        or payload["owner"] != owner
        or payload["platform"] != platform
        or not isinstance(payload["location_root"], str)
    ):
        raise DesktopIntegrationError(f"integration state is not owned here: {state_file}")
    return absolute_path(payload["location_root"], "saved integration location")


def owned_regular_file(path: Path, owner: str, marker: str) -> bool:
    if not lexists(path) or path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size > 1024 * 1024:
            return False
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return marker in text and f"owner={owner}" in text


def owned_bundle(path: Path, owner: str) -> bool:
    if not lexists(path) or path.is_symlink() or not path.is_dir():
        return False
    marker = path / "Contents" / "Resources" / ".mocap-studio-managed"
    return owned_regular_file(marker, owner, f"format={FORMAT}")


def collision(path: Path, kind: str) -> DesktopIntegrationError:
    return DesktopIntegrationError(
        f"{kind} path already exists and is not owned by this installation: {path}"
    )


def check(paths: IntegrationPaths) -> None:
    if paths.platform == "linux":
        assert paths.entry is not None and paths.icon is not None
        launcher_marker = "# mocap-studio installer-managed launcher"
        if lexists(paths.launcher) and not owned_regular_file(
            paths.launcher, paths.owner, launcher_marker
        ):
            raise collision(paths.launcher, "desktop launcher")
        if lexists(paths.entry) and not owned_regular_file(
            paths.entry, paths.owner, "X-Mocap-Studio-Managed=true"
        ):
            raise collision(paths.entry, "desktop entry")
        if lexists(paths.icon) and not owned_regular_file(
            paths.icon, paths.owner, "mocap-studio installer-managed icon"
        ):
            raise collision(paths.icon, "application icon")
    else:
        assert paths.bundle is not None
        if lexists(paths.bundle) and not owned_bundle(paths.bundle, paths.owner):
            raise collision(paths.bundle, "application bundle")


def launcher_text(paths: IntegrationPaths) -> str:
    payload = paths.install_dir / "bin" / "mocap-studio"
    return (
        "#!/bin/sh\n"
        "# mocap-studio installer-managed launcher\n"
        f"# format={FORMAT}\n"
        f"# owner={paths.owner}\n"
        "set -eu\n"
        f"MOCAP_STUDIO_PYTHON={shlex.quote(str(paths.python))}\n"
        "export MOCAP_STUDIO_PYTHON\n"
        "case ${1-} in -psn_*) shift ;; esac\n"
        f"exec {shlex.quote(str(payload))} --reuse-existing \"$@\"\n"
    )


def desktop_exec_quote(value: str) -> str:
    # Desktop Entry quoting differs from shell quoting. Percent signs introduce
    # field codes even in quoted arguments, so a literal percent is doubled.
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
        .replace("%", "%%")
    )
    return f'"{escaped}"'


def desktop_entry_text(paths: IntegrationPaths) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Mocap Studio\n"
        "GenericName=Motion Capture Console\n"
        "Comment=View and record local motion capture streams\n"
        f"Exec={desktop_exec_quote(str(paths.launcher))}\n"
        f"Icon={APPLICATION_ID}\n"
        "Terminal=false\n"
        "Categories=Graphics;3DGraphics;\n"
        "Keywords=motion capture;mocap;BVH;animation;\n"
        "StartupNotify=false\n"
        "DBusActivatable=false\n"
        "X-Mocap-Studio-Managed=true\n"
        f"X-Mocap-Studio-Owner={paths.owner}\n"
        f"# format={FORMAT}\n"
        f"# owner={paths.owner}\n"
    )


def icon_text(paths: IntegrationPaths) -> str:
    icon = Path(__file__).with_name("mocap-studio.svg").read_text(encoding="utf-8")
    marker = f"<!-- format={FORMAT}; owner={paths.owner} -->\n"
    if icon.startswith("<?xml"):
        first, remainder = icon.split("\n", 1)
        return f"{first}\n{marker}{remainder}"
    return marker + icon


def atomic_write(path: Path, content: str, mode: int) -> None:
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


def write_state(paths: IntegrationPaths) -> None:
    content = json.dumps(
        {
            "format": STATE_FORMAT,
            "location_root": str(paths.location_root),
            "owner": paths.owner,
            "platform": paths.platform,
        },
        sort_keys=True,
    )
    atomic_write(paths.state_file, content + "\n", 0o644)


def install_linux(paths: IntegrationPaths) -> None:
    assert paths.entry is not None and paths.icon is not None
    atomic_write(paths.launcher, launcher_text(paths), 0o755)
    atomic_write(paths.icon, icon_text(paths), 0o644)
    atomic_write(paths.entry, desktop_entry_text(paths), 0o644)
    print(f"Linux app entry: {paths.entry}")


def plist_content(version: str) -> bytes:
    payload = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "Mocap Studio",
        "CFBundleExecutable": "mocap-studio",
        "CFBundleIdentifier": APPLICATION_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "Mocap Studio",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": "1",
        "LSApplicationCategoryType": "public.app-category.graphics-design",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def install_macos(paths: IntegrationPaths, version: str) -> None:
    assert paths.bundle is not None
    applications = paths.bundle.parent
    applications.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".mocap-studio-app.", dir=applications))
    backup = applications / f".mocap-studio-app.previous.{os.getpid()}"
    if lexists(backup):
        shutil.rmtree(staging)
        raise DesktopIntegrationError(f"temporary application path already exists: {backup}")
    moved_old = False
    try:
        contents = staging / "Contents"
        macos = contents / "MacOS"
        resources = contents / "Resources"
        macos.mkdir(parents=True)
        resources.mkdir()
        (contents / "Info.plist").write_bytes(plist_content(version))
        executable = macos / "mocap-studio"
        executable.write_text(launcher_text(paths), encoding="utf-8", newline="\n")
        executable.chmod(0o755)
        (resources / "mocap-studio.svg").write_text(
            icon_text(paths), encoding="utf-8", newline="\n"
        )
        (resources / ".mocap-studio-managed").write_text(
            f"format={FORMAT}\nowner={paths.owner}\n", encoding="utf-8", newline="\n"
        )
        if lexists(paths.bundle):
            paths.bundle.rename(backup)
            moved_old = True
        staging.rename(paths.bundle)
        if moved_old:
            shutil.rmtree(backup)
        print(f"macOS application: {paths.bundle}")
    except Exception:
        if moved_old and not lexists(paths.bundle) and lexists(backup):
            backup.rename(paths.bundle)
        raise
    finally:
        if lexists(staging):
            shutil.rmtree(staging)
        if lexists(backup) and owned_bundle(backup, paths.owner):
            shutil.rmtree(backup)


def install(paths: IntegrationPaths, version: str) -> None:
    check(paths)
    if paths.platform == "linux":
        install_linux(paths)
    else:
        install_macos(paths, version)
    write_state(paths)


def remove_if_owned(path: Path, owner: str, marker: str) -> bool:
    if not lexists(path):
        return True
    if owned_regular_file(path, owner, marker):
        path.unlink()
        return True
    else:
        print(f"desktop integration: preserving unmanaged path {path}", file=sys.stderr)
        return False


def uninstall(paths: IntegrationPaths) -> None:
    if paths.platform == "linux":
        assert paths.entry is not None and paths.icon is not None
        removed = (
            remove_if_owned(paths.entry, paths.owner, "X-Mocap-Studio-Managed=true")
            & remove_if_owned(paths.icon, paths.owner, "mocap-studio installer-managed icon")
            & remove_if_owned(
                paths.launcher,
                paths.owner,
                "# mocap-studio installer-managed launcher",
            )
        )
        if removed:
            if lexists(paths.state_file):
                paths.state_file.unlink()
            try:
                paths.launcher.parent.rmdir()
            except OSError:
                pass
        return
    assert paths.bundle is not None
    removed = not lexists(paths.bundle)
    if not removed and owned_bundle(paths.bundle, paths.owner):
        shutil.rmtree(paths.bundle)
        removed = True
    elif not removed:
        print(
            f"desktop integration: preserving unmanaged application {paths.bundle}",
            file=sys.stderr,
        )
    if removed and lexists(paths.state_file):
        paths.state_file.unlink()
        try:
            paths.state_file.parent.rmdir()
        except OSError:
            pass


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("operation", choices=("check", "install", "uninstall"))
    result.add_argument("--platform", choices=("linux", "darwin"), required=True)
    result.add_argument("--app-home", required=True)
    result.add_argument("--install-dir", required=True)
    result.add_argument("--python", required=True)
    result.add_argument("--home", required=True)
    result.add_argument("--xdg-data-home", default="")
    result.add_argument("--version", default="0.0.0")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        paths = integration_paths(args)
        if args.operation == "check":
            check(paths)
        elif args.operation == "install":
            install(paths, args.version)
        else:
            uninstall(paths)
    except (DesktopIntegrationError, OSError, UnicodeError, ValueError) as error:
        print(f"desktop integration: error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

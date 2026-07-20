#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Create a deterministic, license-isolated Mocap Studio release archive.

Only files explicitly selected from ``studio/`` are eligible for the archive.
In particular, no upstream MocapApi headers, examples, static libraries, or
native binaries are copied. The generated UI in
``studio/backend/mocap_studio/static`` is already placed inside the Python
package so the standard-library HTTP server can serve it without Node.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SUPPORTED_TARGETS = {
    "darwin-arm64",
    "darwin-x86_64",
    "linux-aarch64",
    "linux-x86_64",
}


@dataclass(frozen=True)
class PayloadFile:
    source: Path | None
    destination: PurePosixPath
    mode: int = 0o644
    content: bytes | None = None

    def read(self) -> bytes:
        if self.content is not None:
            return self.content
        if self.source is None:
            raise RuntimeError(f"no content for {self.destination}")
        return self.source.read_bytes()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a deterministic, standalone Mocap Studio release bundle."
    )
    parser.add_argument("--version", required=True, help="release version, for example 0.1.0")
    parser.add_argument("--target", required=True, choices=sorted(SUPPORTED_TARGETS))
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument(
        "--source-date-epoch",
        type=int,
        help="tar member timestamp; defaults to SOURCE_DATE_EPOCH or the Git commit time",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        help="checkout root; normally inferred from this script",
    )
    return parser.parse_args(argv)


def source_epoch(root: Path, explicit: int | None) -> int:
    if explicit is not None:
        epoch = explicit
    elif os.environ.get("SOURCE_DATE_EPOCH"):
        try:
            epoch = int(os.environ["SOURCE_DATE_EPOCH"])
        except ValueError as error:
            raise ValueError("SOURCE_DATE_EPOCH must be a non-negative integer") from error
    else:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%ct", "--", "studio"],
                check=True,
                capture_output=True,
                text=True,
            )
            epoch = int(result.stdout.strip())
        except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
            epoch = 0
    if epoch < 0:
        raise ValueError("source date epoch must not be negative")
    return epoch


def require_regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required regular file is missing: {path}")


def selected_tree(source: Path, destination: PurePosixPath) -> list[PayloadFile]:
    if not source.is_dir() or source.is_symlink():
        raise ValueError(f"required directory is missing: {source}")
    links = sorted(path for path in source.rglob("*") if path.is_symlink())
    if links:
        raise ValueError(f"release input must not contain symlinks: {links[0]}")
    result: list[PayloadFile] = []
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        relative = PurePosixPath(path.relative_to(source).as_posix())
        result.append(PayloadFile(path, destination / relative))
    return result


def payload_files(root: Path, version: str, target: str) -> list[PayloadFile]:
    studio = root / "studio"
    frontend_dist = studio / "backend" / "mocap_studio" / "static"
    required = {
        "README.md": studio / "README.md",
        "LICENSE": studio / "LICENSE",
        "NOTICE": studio / "NOTICE",
        "THIRD_PARTY_NOTICES.md": studio / "THIRD_PARTY_NOTICES.md",
        "backend/pyproject.toml": studio / "backend" / "pyproject.toml",
        "bin/mocap-studio": studio / "packaging" / "mocap-studio",
        "libexec/install.sh": root / "install.sh",
        "desktop/mocap-studio.svg": studio / "packaging" / "desktop" / "mocap-studio.svg",
        "desktop/desktop_integration.py": studio
        / "packaging"
        / "desktop"
        / "desktop_integration.py",
    }
    for path in required.values():
        require_regular_file(path)
    require_regular_file(frontend_dist / "index.html")

    files = [
        PayloadFile(
            path,
            PurePosixPath(destination),
            0o755 if destination.startswith(("bin/", "libexec/")) else 0o644,
        )
        for destination, path in required.items()
    ]

    package = studio / "backend" / "mocap_studio"
    if not package.is_dir() or package.is_symlink():
        raise ValueError(f"required directory is missing: {package}")
    for path in sorted(package.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"backend source must be a regular file: {path}")
        relative = PurePosixPath(path.relative_to(studio / "backend").as_posix())
        files.append(PayloadFile(path, PurePosixPath("backend") / relative))

    files.extend(
        selected_tree(frontend_dist, PurePosixPath("backend/mocap_studio/static"))
    )
    marker = (
        "format=mocap-studio-bundle-v1\n"
        f"version={version}\n"
        f"target={target}\n"
    ).encode("utf-8")
    files.extend(
        [
            PayloadFile(None, PurePosixPath("VERSION"), content=f"{version}\n".encode()),
            PayloadFile(None, PurePosixPath(".mocap-studio-bundle"), content=marker),
        ]
    )

    destinations = [str(item.destination) for item in files]
    if len(destinations) != len(set(destinations)):
        raise ValueError("release payload contains duplicate paths")
    return sorted(files, key=lambda item: str(item.destination))


def directory_names(files: list[PayloadFile]) -> list[PurePosixPath]:
    directories: set[PurePosixPath] = set()
    for item in files:
        parent = item.destination.parent
        while str(parent) != ".":
            directories.add(parent)
            parent = parent.parent
    return sorted(directories, key=lambda path: (len(path.parts), str(path)))


def tar_info(name: str, *, mode: int, epoch: int, size: int = 0, directory: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name + ("/" if directory and not name.endswith("/") else ""))
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = epoch
    info.size = 0 if directory else size
    return info


def build_archive(
    archive: Path,
    files: list[PayloadFile],
    version: str,
    epoch: int,
) -> None:
    prefix = PurePosixPath(f"mocap-studio-{version}")
    with archive.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as output:
                output.addfile(tar_info(str(prefix), mode=0o755, epoch=epoch, directory=True))
                for directory in directory_names(files):
                    output.addfile(
                        tar_info(str(prefix / directory), mode=0o755, epoch=epoch, directory=True)
                    )
                for item in files:
                    content = item.read()
                    info = tar_info(
                        str(prefix / item.destination),
                        mode=item.mode,
                        epoch=epoch,
                        size=len(content),
                    )
                    output.addfile(info, io.BytesIO(content))


def write_checksum(archive: Path) -> Path:
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    checksum = archive.with_name(archive.name + ".sha256")
    checksum.write_text(f"{digest.hexdigest()}  {archive.name}\n", encoding="ascii", newline="\n")
    return checksum


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not SAFE_VALUE.fullmatch(args.version):
        raise ValueError("version must contain only letters, numbers, dots, underscores, and hyphens")
    root = (
        args.repository_root.resolve()
        if args.repository_root
        else Path(__file__).resolve().parents[2]
    )
    subprocess.run(
        [
            sys.executable,
            str(root / "studio" / "packaging" / "check_versions.py"),
            "--repository-root",
            str(root),
            "--expected",
            args.version,
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(root / "studio" / "packaging" / "generate_third_party_notices.py"),
            "--repository-root",
            str(root),
            "--check",
        ],
        check=True,
    )
    epoch = source_epoch(root, args.source_date_epoch)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"mocap-studio-{args.version}-{args.target}.tar.gz"
    checksum = archive.with_name(archive.name + ".sha256")
    if archive.exists() or checksum.exists():
        raise FileExistsError(f"refusing to overwrite an existing release asset: {archive}")
    files = payload_files(root, args.version, args.target)
    build_archive(archive, files, args.version, epoch)
    checksum = write_checksum(archive)
    print(archive)
    print(checksum)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"build_release.py: error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

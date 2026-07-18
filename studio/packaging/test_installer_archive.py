#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Exercise the exact Python extractor embedded in the curl installer."""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT_NAME = "mocap-studio-0.1.0"


def embedded_extractor() -> str:
    installer = Path(__file__).resolve().parents[2] / "install.sh"
    text = installer.read_text(encoding="utf-8")
    marker = "<<'SAFE_EXTRACT_PY'"
    try:
        block = text.split(marker, 1)[1].split("\n", 1)[1]
        return block.split("\nSAFE_EXTRACT_PY\n", 1)[0]
    except (IndexError, ValueError) as error:
        raise AssertionError("could not locate embedded safe extractor") from error


def member(name: str, *, kind: bytes = tarfile.REGTYPE, mode: int = 0o644) -> tarfile.TarInfo:
    result = tarfile.TarInfo(name)
    result.type = kind
    result.mode = mode
    if kind == tarfile.REGTYPE:
        result.size = 1
    return result


def root_members() -> list[tuple[tarfile.TarInfo, bytes]]:
    root = member(ROOT_NAME, kind=tarfile.DIRTYPE, mode=0o755)
    root.size = 0
    return [(root, b"")]


def write_archive(
    path: Path,
    entries: list[tuple[tarfile.TarInfo, bytes]],
    *,
    archive_format: int = tarfile.USTAR_FORMAT,
) -> None:
    with tarfile.open(path, mode="w:gz", format=archive_format) as output:
        for info, content in entries:
            output.addfile(info, io.BytesIO(content) if info.isreg() else None)


class SafeExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.extractor = embedded_extractor()

    def run_extractor(self, archive: Path, destination: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", self.extractor, str(archive), str(destination), ROOT_NAME],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_extracts_only_expected_regular_files_and_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            bin_dir = member(f"{ROOT_NAME}/bin", kind=tarfile.DIRTYPE, mode=0o755)
            bin_dir.size = 0
            launcher = member(f"{ROOT_NAME}/bin/mocap-studio", mode=0o755)
            archive = work / "valid.tar.gz"
            write_archive(archive, root_members() + [(bin_dir, b""), (launcher, b"x")])
            destination = work / "out"
            destination.mkdir()
            result = self.run_extractor(archive, destination)
            self.assertEqual(result.returncode, 0, result.stderr)
            extracted = destination / ROOT_NAME / "bin" / "mocap-studio"
            self.assertEqual(extracted.read_bytes(), b"x")
            self.assertEqual(extracted.stat().st_mode & 0o7777, 0o755)

    def test_rejects_malicious_and_ambiguous_members_before_writing(self) -> None:
        cases: dict[str, list[tuple[tarfile.TarInfo, bytes]]] = {
            "traversal": [(member(f"{ROOT_NAME}/../escape"), b"x")],
            "absolute": [(member(f"/{ROOT_NAME}/escape"), b"x")],
            "wrong-root": [(member("other-root/file"), b"x")],
            "backslash": [(member(f"{ROOT_NAME}\\escape"), b"x")],
            "dot-segment": [(member(f"{ROOT_NAME}/./escape"), b"x")],
            "unsafe-mode": [(member(f"{ROOT_NAME}/file", mode=0o666), b"x")],
            "unexpected-executable": [(member(f"{ROOT_NAME}/file", mode=0o755), b"x")],
            "symlink": [(member(f"{ROOT_NAME}/link", kind=tarfile.SYMTYPE), b"")],
            "hardlink": [(member(f"{ROOT_NAME}/link", kind=tarfile.LNKTYPE), b"")],
            "fifo": [(member(f"{ROOT_NAME}/pipe", kind=tarfile.FIFOTYPE), b"")],
            "character-device": [(member(f"{ROOT_NAME}/device", kind=tarfile.CHRTYPE), b"")],
            "contiguous-file": [(member(f"{ROOT_NAME}/contiguous", kind=tarfile.CONTTYPE), b"")],
            "missing-parent": [(member(f"{ROOT_NAME}/nested/file"), b"x")],
        }
        duplicate = member(f"{ROOT_NAME}/same")
        cases["duplicate"] = [(duplicate, b"x"), (member(duplicate.name), b"y")]

        for label, malicious in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                archive = work / "malicious.tar.gz"
                write_archive(archive, root_members() + malicious)
                destination = work / "out"
                destination.mkdir()
                result = self.run_extractor(archive, destination)
                self.assertNotEqual(result.returncode, 0, label)
                self.assertFalse((destination / ROOT_NAME).exists(), label)

    def test_rejects_extended_archive_metadata_before_writing(self) -> None:
        cases: list[tuple[str, tarfile.TarInfo, int]] = []
        pax = member(f"{ROOT_NAME}/file")
        pax.pax_headers = {"comment": "not part of the release format"}
        cases.append(("pax", pax, tarfile.PAX_FORMAT))
        gnu_longname = member(f"{ROOT_NAME}/{'a' * 120}")
        cases.append(("gnu-longname", gnu_longname, tarfile.GNU_FORMAT))

        for label, extended, archive_format in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                work = Path(temporary)
                archive = work / f"{label}.tar.gz"
                write_archive(
                    archive,
                    root_members() + [(extended, b"x")],
                    archive_format=archive_format,
                )
                destination = work / "out"
                destination.mkdir()
                result = self.run_extractor(archive, destination)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertFalse((destination / ROOT_NAME).exists())


if __name__ == "__main__":
    unittest.main()

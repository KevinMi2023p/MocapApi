#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Check that every published Mocap Studio version declaration agrees."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path


PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def backend_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == "__version__":
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                raise ValueError(f"__version__ is not a string literal in {path}")
            values.append(node.value.value)
    if len(values) != 1:
        raise ValueError(f"expected exactly one __version__ assignment in {path}")
    return values[0]


def pyproject_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    project_match = re.search(r"(?ms)^\[project\]\s*$\n(.*?)(?=^\[|\Z)", text)
    if project_match is None:
        raise ValueError(f"[project] table is missing from {path}")
    version_match = PYPROJECT_VERSION.search(project_match.group(1))
    if version_match is None:
        raise ValueError(f"project.version is missing from {path}")
    return version_match.group(1)


def frontend_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError(f"version is missing from {path}")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", help="also require this release/tag version")
    parser.add_argument("--repository-root", type=Path)
    args = parser.parse_args(argv)
    root = (
        args.repository_root.resolve()
        if args.repository_root
        else Path(__file__).resolve().parents[2]
    )
    versions = {
        "backend __version__": backend_version(
            root / "studio" / "backend" / "mocap_studio" / "__init__.py"
        ),
        "backend pyproject.toml": pyproject_version(
            root / "studio" / "backend" / "pyproject.toml"
        ),
        "frontend package.json": frontend_version(
            root / "studio" / "frontend" / "package.json"
        ),
    }
    declared = set(versions.values())
    if len(declared) != 1:
        details = ", ".join(f"{name}={value!r}" for name, value in versions.items())
        raise ValueError(f"Mocap Studio version declarations disagree: {details}")
    version = declared.pop()
    if args.expected is not None and version != args.expected:
        raise ValueError(f"declared version {version!r} does not match expected {args.expected!r}")
    print(f"Mocap Studio version declarations agree: {version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as error:
        print(f"check_versions.py: error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Generate exact third-party notices for code emitted in the Vite bundle.

The production source maps identify the package installation that contributed
each bundled module. Package versions are checked against package-lock.json,
and the exact license text is copied from the installed npm package. A pinned
fallback is permitted only when an npm tarball omitted its repository license.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LICENSE_FILENAMES = (
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "LICENCE",
    "LICENCE.md",
    "LICENCE.txt",
    "COPYING",
)

# @react-three/fiber 8.18.0 declares MIT and its package metadata points to the
# pmndrs/react-three-fiber repository, but its npm tarball omits LICENSE. Keep a
# version-pinned copy of that repository tag's license and fail closed on an
# upgrade until the fallback is reviewed.
FALLBACKS = {
    ("@react-three/fiber", "8.18.0"): (
        "studio/packaging/license_fallbacks/react-three-fiber-8.18.0.LICENSE",
        "https://github.com/pmndrs/react-three-fiber/blob/v8.18.0/LICENSE",
    )
}


@dataclass(frozen=True)
class PackageNotice:
    name: str
    version: str
    license_id: str
    install_path: str
    license_source: str
    repository: str
    license_text: str
    source_count: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate notices for packages present in the built Vite bundle."
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        help="checkout root; normally inferred from this script",
    )
    parser.add_argument("--output", type=Path, help="override generated notice path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the checked-in notice is current without rewriting it",
    )
    return parser.parse_args(argv)


def load_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read JSON from {path}: {error}") from error
    if not isinstance(result, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return result


def package_root(source: Path, node_modules: Path) -> Path:
    try:
        source.relative_to(node_modules)
    except ValueError as error:
        raise ValueError(f"source-map dependency escaped node_modules: {source}") from error
    for candidate in (source.parent, *source.parents):
        if candidate == node_modules:
            break
        unscoped = candidate.parent.name == "node_modules"
        scoped = (
            candidate.parent.name.startswith("@")
            and candidate.parent.parent.name == "node_modules"
        )
        if unscoped or scoped:
            if not (candidate / "package.json").is_file():
                raise ValueError(f"bundled package has no package.json: {candidate}")
            return candidate
    raise ValueError(f"could not resolve npm package for bundled source: {source}")


def bundled_roots(static_dir: Path, node_modules: Path) -> tuple[Counter[Path], list[Path]]:
    maps = sorted(static_dir.rglob("*.map"))
    if not maps:
        raise ValueError(f"no Vite source maps found below {static_dir}; run npm run build")
    roots: Counter[Path] = Counter()
    third_party_sources = 0
    for source_map in maps:
        data = load_json(source_map)
        sources = data.get("sources")
        if not isinstance(sources, list):
            raise ValueError(f"source map has no sources array: {source_map}")
        for source_name in sources:
            if not isinstance(source_name, str) or "node_modules" not in source_name:
                continue
            source = (source_map.parent / source_name).resolve()
            roots[package_root(source, node_modules)] += 1
            third_party_sources += 1
    if not roots or not third_party_sources:
        raise ValueError("Vite source maps did not identify any bundled npm dependencies")
    return roots, maps


def repository_url(metadata: dict[str, Any]) -> str:
    repository = metadata.get("repository")
    if isinstance(repository, str):
        value = repository
    elif isinstance(repository, dict) and isinstance(repository.get("url"), str):
        value = repository["url"]
    elif isinstance(metadata.get("homepage"), str):
        value = metadata["homepage"]
    else:
        return "(not declared)"
    if value.startswith("git+"):
        value = value[4:]
    if value.endswith(".git"):
        value = value[:-4]
    return value


def exact_license(
    root: Path,
    checkout: Path,
    name: str,
    version: str,
) -> tuple[str, str]:
    for filename in LICENSE_FILENAMES:
        candidate = root / filename
        if candidate.is_file() and not candidate.is_symlink():
            text = candidate.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
            if not text:
                raise ValueError(f"empty license file: {candidate}")
            return text, candidate.relative_to(checkout / "studio" / "frontend").as_posix()
    fallback = FALLBACKS.get((name, version))
    if fallback is None:
        raise ValueError(
            f"bundled package {name}@{version} has no license file; add a reviewed, "
            "version-pinned fallback before distributing it"
        )
    relative, source_url = fallback
    candidate = checkout / relative
    text = candidate.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    if not text:
        raise ValueError(f"empty fallback license file: {candidate}")
    return text, f"{relative} (from {source_url})"


def collect_notices(checkout: Path) -> tuple[list[PackageNotice], list[Path]]:
    frontend = checkout / "studio" / "frontend"
    node_modules = (frontend / "node_modules").resolve()
    static_dir = (checkout / "studio" / "backend" / "mocap_studio" / "static").resolve()
    if not node_modules.is_dir():
        raise ValueError(f"node_modules is missing: run npm ci in {frontend}")
    roots, maps = bundled_roots(static_dir, node_modules)
    lock_data = load_json(frontend / "package-lock.json")
    lock_packages = lock_data.get("packages")
    if not isinstance(lock_packages, dict):
        raise ValueError("package-lock.json has no packages table")

    notices: list[PackageNotice] = []
    for root, count in sorted(roots.items(), key=lambda item: str(item[0])):
        metadata = load_json(root / "package.json")
        name = metadata.get("name")
        version = metadata.get("version")
        license_id = metadata.get("license")
        if not all(isinstance(value, str) and value for value in (name, version, license_id)):
            raise ValueError(f"incomplete name/version/license metadata in {root / 'package.json'}")
        install_path = root.relative_to(frontend).as_posix()
        locked = lock_packages.get(install_path)
        if not isinstance(locked, dict) or locked.get("version") != version:
            raise ValueError(
                f"installed {name}@{version} does not match package-lock entry {install_path}"
            )
        license_text, license_source = exact_license(root, checkout, name, version)
        notices.append(
            PackageNotice(
                name=name,
                version=version,
                license_id=license_id,
                install_path=install_path,
                license_source=license_source,
                repository=repository_url(metadata),
                license_text=license_text,
                source_count=count,
            )
        )
    notices.sort(key=lambda item: (item.name.lower(), item.version, item.install_path))
    return notices, maps


def render(checkout: Path, notices: list[PackageNotice], maps: list[Path]) -> str:
    unique_names = len({notice.name for notice in notices})
    source_modules = sum(notice.source_count for notice in notices)
    lines = [
        "# Third-party notices and distribution boundary",
        "",
        "<!-- Generated by studio/packaging/generate_third_party_notices.py. -->",
        "<!-- Do not hand-edit the generated package table or license sections. -->",
        "",
        "Mocap Studio (`studio/`) is an independent application licensed under",
        "Apache-2.0. Noitom, Axis Studio, Perception Neuron, and related names are",
        "trademarks of their respective owners. This project is not affiliated with,",
        "endorsed by, or supported by Noitom Ltd.",
        "",
        "## Upstream MocapApi files are not relicensed",
        "",
        "The `pnmocap/MocapApi` repository did not contain a root license file when this",
        "application was prepared. Under default copyright rules, a public repository is",
        "not automatically open source. The Apache-2.0 license in `studio/LICENSE` applies",
        "only to the new Mocap Studio work; it does not apply to upstream headers,",
        "documentation, demos, artwork, static libraries, or binaries elsewhere in the",
        "fork.",
        "",
        "Official release archives are generated by the allow-list builder in",
        "`studio/packaging/build_release.py`. They exclude every upstream MocapApi file",
        "and native library. The current upstream snapshot contains Linux `.so` files but",
        "no macOS `.dylib`; neither is redistributed by Mocap Studio.",
        "",
        "## Generated runtime-package coverage",
        "",
        "The table and license texts below were generated from the production Vite source",
        "maps, installed package metadata, and `package-lock.json`. Detection follows each",
        "emitted module to its nearest (including nested) `node_modules` package root. This",
        "covers the dependencies actually emitted into the browser JavaScript, rather than",
        "over-reporting every development or optional package installed by npm.",
        "",
        f"Coverage for this build: **{len(notices)} installed package instances**,",
        f"**{unique_names} package names**, and **{source_modules} third-party source-map",
        f"entries** across **{len(maps)} production source map(s)**.",
        "",
        "| Package | Version | SPDX/license | Locked install path | Bundled modules | License source |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for notice in notices:
        lines.append(
            f"| [{notice.name}]({notice.repository}) | {notice.version} | "
            f"{notice.license_id} | `{notice.install_path}` | {notice.source_count} | "
            f"`{notice.license_source}` |"
        )
    lines.extend(
        [
            "",
            "Source maps inspected:",
            "",
        ]
    )
    for path in maps:
        lines.append(f"- `{path.relative_to(checkout).as_posix()}`")
    lines.extend(
        [
            "",
            "Generation fails if a bundled package is absent from the lockfile, lacks",
            "license metadata/text, escapes `node_modules`, or changes a version-pinned",
            "fallback. Regenerate after `npm ci && npm run build` with:",
            "",
            "```sh",
            "python3 studio/packaging/generate_third_party_notices.py",
            "```",
            "",
            "## Exact license texts",
            "",
        ]
    )

    grouped: dict[str, list[PackageNotice]] = defaultdict(list)
    texts: dict[str, str] = {}
    for notice in notices:
        digest = hashlib.sha256(notice.license_text.encode("utf-8")).hexdigest()
        grouped[digest].append(notice)
        texts[digest] = notice.license_text
    ordered_groups = sorted(
        grouped,
        key=lambda digest: [
            (item.name.lower(), item.version, item.install_path) for item in grouped[digest]
        ],
    )
    for index, digest in enumerate(ordered_groups, start=1):
        packages = grouped[digest]
        lines.extend(
            [
                f"### License text {index}: {packages[0].license_id}",
                "",
                "Applies exactly to:",
                "",
            ]
        )
        for notice in packages:
            lines.append(f"- `{notice.name}@{notice.version}` (`{notice.install_path}`)")
        lines.extend(["", "```text", texts[digest], "```", ""])

    lines.extend(
        [
            "No Noitom UI artwork, screenshots, character meshes, sensor icons,",
            "proprietary motion solver, `.mbx` implementation, or Axis Studio executable",
            "is included.",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    checkout = (
        args.repository_root.resolve()
        if args.repository_root
        else Path(__file__).resolve().parents[2]
    )
    output = args.output.resolve() if args.output else checkout / "studio" / "THIRD_PARTY_NOTICES.md"
    notices, maps = collect_notices(checkout)
    generated = render(checkout, notices, maps)
    if args.check:
        try:
            current = output.read_text(encoding="utf-8")
        except OSError as error:
            raise ValueError(f"could not read generated notice {output}: {error}") from error
        if current != generated:
            raise ValueError(
                f"{output} is stale; run studio/packaging/generate_third_party_notices.py"
            )
        print(
            f"verified {output}: {len(notices)} package instances, "
            f"{len({item.name for item in notices})} package names"
        )
        return 0
    output.write_text(generated, encoding="utf-8", newline="\n")
    print(
        f"wrote {output}: {len(notices)} package instances, "
        f"{len({item.name for item in notices})} package names"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"generate_third_party_notices.py: error: {error}", file=sys.stderr)
        raise SystemExit(1) from error

#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
# Per-user installer for the Apache-2.0 Mocap Studio release payload.

set -eu

DEFAULT_REPOSITORY=KevinMi2023p/MocapApi
DEFAULT_TAG_PREFIX=studio-v
OPERATION=install
REQUESTED_VERSION=latest
LOCAL_MODE=0
LOCAL_ROOT=
PREFIX=
APP_HOME_OVERRIDE=
BIN_DIR_OVERRIDE=
REPOSITORY=${MOCAP_STUDIO_REPOSITORY:-$DEFAULT_REPOSITORY}
RELEASE_BASE_URL=${MOCAP_STUDIO_RELEASE_BASE_URL:-}
TAG_PREFIX=${MOCAP_STUDIO_TAG_PREFIX:-$DEFAULT_TAG_PREFIX}
LAUNCH=0
ASSUME_YES=0
TEMP_DIR=
INCOMING_DIR=
VERSIONS_DIR=

usage() {
    cat <<'EOF'
Install Mocap Studio for the current user (never run this script with sudo).

Usage:
  ./install.sh [--version VERSION] [--launch] [location options]
  ./install.sh --local [CHECKOUT] [--launch] [location options]
  ./install.sh --uninstall [--yes] [location options]
  ./install.sh --launch [location options]

Options:
  --version VERSION       Release version (default: latest).
  --local [CHECKOUT]      Build/install the current checkout; UI must be prebuilt.
  --prefix DIR            Use DIR/share/mocap-studio and DIR/bin.
  --data-home DIR         Override the application install-data directory.
  --bin-dir DIR           Override the command directory (default: ~/.local/bin).
  --repo OWNER/REPO       GitHub release repository.
  --release-base-url URL  Release download root; assets are under URL/TAG/.
  --tag-prefix PREFIX     Prefix for a bare version (default: studio-v).
  --launch                Launch after installation, or launch an existing install.
  --uninstall             Remove installed application versions; preserve all takes.
  --yes                   Confirm uninstall non-interactively.
  -h, --help              Show this help.

Environment equivalents: MOCAP_STUDIO_REPOSITORY,
MOCAP_STUDIO_RELEASE_BASE_URL, MOCAP_STUDIO_TAG_PREFIX.
EOF
}

die() {
    printf 'install.sh: error: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        case "$TEMP_DIR" in
            "${TMPDIR:-/tmp}"/mocap-studio-install.*) rm -rf -- "$TEMP_DIR" ;;
        esac
    fi
    if [ -n "$INCOMING_DIR" ] && [ -d "$INCOMING_DIR" ] && [ -n "$VERSIONS_DIR" ]; then
        case "$INCOMING_DIR" in
            "$VERSIONS_DIR"/.incoming.*) rm -rf -- "$INCOMING_DIR" ;;
        esac
    fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

need_value() {
    [ "$#" -ge 2 ] || die "$1 requires a value"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            need_value "$@"; REQUESTED_VERSION=$2; shift 2 ;;
        --version=*) REQUESTED_VERSION=${1#*=}; shift ;;
        --local)
            LOCAL_MODE=1
            if [ "$#" -ge 2 ] && [ "${2#-}" = "$2" ]; then
                LOCAL_ROOT=$2; shift 2
            else
                LOCAL_ROOT=$(pwd); shift
            fi ;;
        --prefix)
            need_value "$@"; PREFIX=$2; shift 2 ;;
        --data-home)
            need_value "$@"; APP_HOME_OVERRIDE=$2; shift 2 ;;
        --bin-dir)
            need_value "$@"; BIN_DIR_OVERRIDE=$2; shift 2 ;;
        --repo)
            need_value "$@"; REPOSITORY=$2; shift 2 ;;
        --release-base-url)
            need_value "$@"; RELEASE_BASE_URL=$2; shift 2 ;;
        --tag-prefix)
            need_value "$@"; TAG_PREFIX=$2; shift 2 ;;
        --launch) LAUNCH=1; shift ;;
        --uninstall) OPERATION=uninstall; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

[ "$(id -u)" -ne 0 ] || die "do not run this per-user installer as root or with sudo"
[ -n "${HOME:-}" ] || die "HOME is not set"

case "$(uname -s)" in
    Darwin) PLATFORM=darwin ; DEFAULT_APP_HOME=$HOME/Library/Application\ Support/Mocap\ Studio ;;
    Linux) PLATFORM=linux ; DEFAULT_APP_HOME=${XDG_DATA_HOME:-$HOME/.local/share}/mocap-studio ;;
    *) die "only macOS and Linux are supported" ;;
esac

if [ -n "$PREFIX" ]; then
    case "$PREFIX" in /*) ;; *) die "--prefix must be an absolute path" ;; esac
    DEFAULT_APP_HOME=$PREFIX/share/mocap-studio
    DEFAULT_BIN_DIR=$PREFIX/bin
else
    DEFAULT_BIN_DIR=$HOME/.local/bin
fi
APP_HOME=${APP_HOME_OVERRIDE:-$DEFAULT_APP_HOME}
BIN_DIR=${BIN_DIR_OVERRIDE:-$DEFAULT_BIN_DIR}
case "$APP_HOME" in /*) ;; *) die "application data path must be absolute" ;; esac
case "$BIN_DIR" in /*) ;; *) die "command directory must be absolute" ;; esac
case "$APP_HOME" in /|"$HOME"|"$HOME"/) die "refusing unsafe application data path: $APP_HOME" ;; esac

VERSIONS_DIR=$APP_HOME/versions
COMMAND_PATH=$BIN_DIR/mocap-studio

managed_command() {
    [ -L "$COMMAND_PATH" ] || return 1
    MANAGED_TARGET=$(readlink "$COMMAND_PATH")
    case "$MANAGED_TARGET" in
        "$VERSIONS_DIR"/mocap-studio-*/bin/mocap-studio) return 0 ;;
        *) return 1 ;;
    esac
}

launch_installed() {
    [ -x "$COMMAND_PATH" ] || die "no runnable install found at $COMMAND_PATH"
    trap - EXIT HUP INT TERM
    exec "$COMMAND_PATH"
}

if [ "$OPERATION" = uninstall ]; then
    FOUND=0
    if [ -d "$VERSIONS_DIR" ]; then
        for VERSION_DIR in "$VERSIONS_DIR"/mocap-studio-*; do
            [ -d "$VERSION_DIR" ] || continue
            [ -f "$VERSION_DIR/.mocap-studio-bundle" ] || continue
            FOUND=1
        done
    fi
    if [ "$FOUND" -eq 0 ] && ! managed_command; then
        printf '%s\n' "Mocap Studio is not installed in $APP_HOME."
        exit 0
    fi
    if [ "$ASSUME_YES" -ne 1 ]; then
        [ -r /dev/tty ] || die "uninstall needs confirmation; rerun with --yes in a non-interactive shell"
        printf 'Remove Mocap Studio application files from "%s"? Takes are preserved. [y/N] ' "$APP_HOME" >/dev/tty
        IFS= read -r REPLY </dev/tty || die "could not read confirmation"
        case "$REPLY" in y|Y|yes|YES) ;; *) printf '%s\n' "Uninstall cancelled."; exit 0 ;; esac
    fi
    if managed_command; then
        rm -- "$COMMAND_PATH"
    fi
    if [ -d "$VERSIONS_DIR" ]; then
        for VERSION_DIR in "$VERSIONS_DIR"/mocap-studio-*; do
            [ -d "$VERSION_DIR" ] || continue
            [ -f "$VERSION_DIR/.mocap-studio-bundle" ] || continue
            case "$VERSION_DIR" in "$VERSIONS_DIR"/mocap-studio-*) rm -rf -- "$VERSION_DIR" ;; esac
        done
        rmdir "$VERSIONS_DIR" 2>/dev/null || true
    fi
    printf '%s\n' "Mocap Studio application files were removed. Recorded takes were preserved."
    exit 0
fi

if [ "$LAUNCH" -eq 1 ] && [ "$LOCAL_MODE" -eq 0 ] && [ "$REQUESTED_VERSION" = latest ] && [ -x "$COMMAND_PATH" ]; then
    launch_installed
fi

case "$(uname -m)" in
    x86_64|amd64) MACHINE=x86_64 ;;
    arm64|aarch64)
        if [ "$PLATFORM" = darwin ]; then MACHINE=arm64; else MACHINE=aarch64; fi ;;
    *) die "unsupported processor architecture: $(uname -m)" ;;
esac
TARGET=$PLATFORM-$MACHINE

command -v python3 >/dev/null 2>&1 || die "Python 3.10 or newer is required"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || die "Python 3.10 or newer is required"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/mocap-studio-install.XXXXXX") || die "could not create a temporary directory"

if [ "$LOCAL_MODE" -eq 1 ]; then
    LOCAL_ROOT=$(CDPATH= cd "$LOCAL_ROOT" 2>/dev/null && pwd) || die "local checkout does not exist: $LOCAL_ROOT"
    BUILDER=$LOCAL_ROOT/studio/packaging/build_release.py
    [ -f "$BUILDER" ] || die "not a Mocap Studio checkout: $LOCAL_ROOT"
    [ -f "$LOCAL_ROOT/studio/backend/mocap_studio/static/index.html" ] || die "local UI is not built; run 'npm ci && npm run build' in studio/frontend"
    if [ "$REQUESTED_VERSION" = latest ]; then
        VERSION=$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$LOCAL_ROOT/studio/backend/mocap_studio/__init__.py" | head -n 1)
    else
        VERSION=$REQUESTED_VERSION
    fi
    [ -n "$VERSION" ] || die "could not determine local application version"
    python3 "$BUILDER" --repository-root "$LOCAL_ROOT" --version "$VERSION" --target "$TARGET" --output-dir "$TEMP_DIR"
else
    command -v curl >/dev/null 2>&1 || die "curl is required"
    if [ "$REQUESTED_VERSION" = latest ]; then
        LATEST_URL=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPOSITORY/releases/latest") || die "could not resolve the latest release"
        RELEASE_TAG=${LATEST_URL##*/}
        [ -n "$RELEASE_TAG" ] && [ "$RELEASE_TAG" != latest ] || die "the latest release tag could not be determined"
    else
        case "$REQUESTED_VERSION" in
            "$TAG_PREFIX"*|v*) RELEASE_TAG=$REQUESTED_VERSION ;;
            *) RELEASE_TAG=$TAG_PREFIX$REQUESTED_VERSION ;;
        esac
    fi
    case "$RELEASE_TAG" in
        "$TAG_PREFIX"*) VERSION=${RELEASE_TAG#"$TAG_PREFIX"} ;;
        v*) VERSION=${RELEASE_TAG#v} ;;
        *) VERSION=$RELEASE_TAG ;;
    esac
    case "$VERSION" in ''|*[!A-Za-z0-9._-]*) die "unsafe release version: $VERSION" ;; esac
    ASSET=mocap-studio-$VERSION-$TARGET.tar.gz
    if [ -z "$RELEASE_BASE_URL" ]; then
        RELEASE_BASE_URL=https://github.com/$REPOSITORY/releases/download
    fi
    DOWNLOAD_ROOT=${RELEASE_BASE_URL%/}/$RELEASE_TAG
    curl -fL --proto '=https' --tlsv1.2 --retry 3 -o "$TEMP_DIR/$ASSET" "$DOWNLOAD_ROOT/$ASSET"
    curl -fL --proto '=https' --tlsv1.2 --retry 3 -o "$TEMP_DIR/$ASSET.sha256" "$DOWNLOAD_ROOT/$ASSET.sha256"
fi

ASSET=mocap-studio-$VERSION-$TARGET.tar.gz
ARCHIVE=$TEMP_DIR/$ASSET
CHECKSUM_FILE=$ARCHIVE.sha256
[ -f "$ARCHIVE" ] && [ -f "$CHECKSUM_FILE" ] || die "release builder did not produce the expected assets"
EXPECTED_HASH=$(awk 'NR == 1 { print $1 }' "$CHECKSUM_FILE")
printf '%s\n' "$EXPECTED_HASH" | grep -Eq '^[0-9a-fA-F]{64}$' || die "invalid SHA256 file"
if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_HASH=$(sha256sum "$ARCHIVE" | awk '{ print $1 }')
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_HASH=$(shasum -a 256 "$ARCHIVE" | awk '{ print $1 }')
else
    ACTUAL_HASH=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$ARCHIVE")
fi
[ "$ACTUAL_HASH" = "$EXPECTED_HASH" ] || die "SHA256 verification failed for $ASSET"

PAYLOAD_ROOT=mocap-studio-$VERSION
python3 - "$ARCHIVE" "$TEMP_DIR" "$PAYLOAD_ROOT" <<'SAFE_EXTRACT_PY' || die "release archive validation or extraction failed"
# Keep this block self-contained: the curl installer cannot import checkout files.
import os
from pathlib import Path
import shutil
import sys
import tarfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
expected_root = sys.argv[3]
max_members = 10_000
max_file_size = 128 * 1024 * 1024
max_total_size = 512 * 1024 * 1024


def fail(message: str) -> None:
    raise ValueError(message)


def normalized_name(member: tarfile.TarInfo) -> tuple[str, tuple[str, ...]]:
    raw = member.name
    if not raw or "\x00" in raw or "\\" in raw or raw.startswith("/"):
        fail(f"unsafe archive path: {raw!r}")
    trimmed = raw[:-1] if raw.endswith("/") else raw
    if not trimmed or len(trimmed) > 1024:
        fail(f"unsafe archive path: {raw!r}")
    parts = tuple(trimmed.split("/"))
    if any(
        not part
        or part in {".", ".."}
        or len(part) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in part)
        for part in parts
    ):
        fail(f"archive path is not normalized: {raw!r}")
    normalized = "/".join(parts)
    if trimmed != normalized or parts[0] != expected_root:
        fail(f"archive path is outside the expected root: {raw!r}")
    return normalized, parts


try:
    if archive.stat().st_size > 256 * 1024 * 1024:
        fail("compressed archive exceeds the allowed size")
    with tarfile.open(archive, mode="r:gz") as bundle:
        validated: dict[str, tuple[tarfile.TarInfo, tuple[str, ...], str]] = {}
        member_count = 0
        total_size = 0
        launcher_name = f"{expected_root}/bin/mocap-studio"
        for member in bundle:
            member_count += 1
            if member_count > max_members:
                fail("archive has too many members")
            name, parts = normalized_name(member)
            if name in validated:
                fail(f"duplicate archive path: {name}")
            if (
                member.offset_data - member.offset != tarfile.BLOCKSIZE
                or member.pax_headers
                or getattr(member, "sparse", None)
            ):
                fail(f"extended or sparse archive metadata is not allowed for {name}")
            if member.type == tarfile.DIRTYPE:
                if member.size != 0 or (member.mode & 0o7777) != 0o755:
                    fail(f"unsafe directory metadata for {name}")
                kind = "directory"
            elif member.type in {tarfile.REGTYPE, tarfile.AREGTYPE}:
                expected_mode = 0o755 if name == launcher_name else 0o644
                if (member.mode & 0o7777) != expected_mode:
                    fail(f"unsafe file mode for {name}")
                if member.size < 0 or member.size > max_file_size:
                    fail(f"unsafe file size for {name}")
                total_size += member.size
                if total_size > max_total_size:
                    fail("archive expands beyond the allowed total size")
                kind = "file"
            else:
                fail(f"unsupported archive member type for {name}")
            validated[name] = (member, parts, kind)

        if member_count == 0:
            fail("archive is empty")
        root_record = validated.get(expected_root)
        if root_record is None or root_record[2] != "directory":
            fail("archive root directory is missing")
        for name, (_member, parts, _kind) in validated.items():
            for depth in range(1, len(parts)):
                parent = "/".join(parts[:depth])
                record = validated.get(parent)
                if record is None or record[2] != "directory":
                    fail(f"archive omits directory parent {parent!r} for {name!r}")

        directories = sorted(
            (record for record in validated.values() if record[2] == "directory"),
            key=lambda record: (len(record[1]), record[1]),
        )
        for _member, parts, _kind in directories:
            output = destination.joinpath(*parts)
            output.mkdir(mode=0o755, exist_ok=False)
            os.chmod(output, 0o755)

        files = sorted(
            (record for record in validated.values() if record[2] == "file"),
            key=lambda record: record[1],
        )
        for member, parts, _kind in files:
            source = bundle.extractfile(member)
            if source is None:
                fail(f"could not read archive file {member.name!r}")
            output = destination.joinpath(*parts)
            with source, output.open("xb") as stream:
                shutil.copyfileobj(source, stream, length=1024 * 1024)
            if output.stat().st_size != member.size:
                fail(f"archive file size changed while reading {member.name!r}")
            os.chmod(output, 0o755 if member.name == launcher_name else 0o644)
except (OSError, tarfile.TarError, ValueError) as error:
    print(f"install.sh: unsafe or invalid release archive: {error}", file=sys.stderr)
    raise SystemExit(1) from error
SAFE_EXTRACT_PY
PAYLOAD=$TEMP_DIR/$PAYLOAD_ROOT
[ -f "$PAYLOAD/.mocap-studio-bundle" ] || die "release bundle marker is missing"
grep -Fqx "version=$VERSION" "$PAYLOAD/.mocap-studio-bundle" || die "release bundle version does not match"
grep -Fqx "target=$TARGET" "$PAYLOAD/.mocap-studio-bundle" || die "release bundle target does not match"
[ -x "$PAYLOAD/bin/mocap-studio" ] || die "release launcher is missing"
[ -f "$PAYLOAD/backend/mocap_studio/static/index.html" ] || die "release UI is missing"

INSTALL_DIR=$VERSIONS_DIR/$PAYLOAD_ROOT-$TARGET
if [ -e "$COMMAND_PATH" ] || [ -L "$COMMAND_PATH" ]; then
    managed_command || die "$COMMAND_PATH already exists and is not managed by this installer; choose --bin-dir"
fi
if [ -e "$INSTALL_DIR" ]; then
    die "version $VERSION is already installed at $INSTALL_DIR; refusing to overwrite it"
fi
mkdir -p "$VERSIONS_DIR" "$BIN_DIR"
INCOMING_DIR=$VERSIONS_DIR/.incoming.$$
[ ! -e "$INCOMING_DIR" ] || die "temporary install path already exists: $INCOMING_DIR"
cp -R "$PAYLOAD" "$INCOMING_DIR"
mv "$INCOMING_DIR" "$INSTALL_DIR"
INCOMING_DIR=
if managed_command; then
    rm -- "$COMMAND_PATH"
fi
ln -s "$INSTALL_DIR/bin/mocap-studio" "$COMMAND_PATH" || die "could not create command link at $COMMAND_PATH"

printf 'Installed Mocap Studio %s for %s.\n' "$VERSION" "$TARGET"
printf 'Command: %s\n' "$COMMAND_PATH"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) printf 'Add %s to PATH, or run the command by its full path.\n' "$BIN_DIR" ;; esac

if [ "$LAUNCH" -eq 1 ]; then
    launch_installed
fi

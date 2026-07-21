#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
# Per-user installer for the Apache-2.0 Mocap Studio release payload.

set -eu

DEFAULT_REPOSITORY=KevinMi2023p/MocapApi
DEFAULT_TAG_PREFIX=studio-v
OPERATION=install
OPERATION_EXPLICIT=0
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
MODIFY_PATH=1
DESKTOP_INTEGRATION=1
SHELL_COMPLETIONS=1
TEMP_DIR=
INCOMING_DIR=
COMMAND_LINK_TEMP=
INSTALL_LOCK_DIR=
INSTALL_LOCK_HELD=0
LOCK_CREATED_APP_HOME=0
VERSIONS_DIR=
PATH_SETUP_RESULT=
PATH_PROFILE_ATTEMPTS=0
PATH_PROFILE_SUCCESSES=0
VERSION_OPTION_USED=0
REPOSITORY_OPTION_USED=0
RELEASE_BASE_OPTION_USED=0
TAG_PREFIX_OPTION_USED=0

usage() {
    case "${MOCAP_STUDIO_CLI_CONTEXT:-}" in
        ' update')
            cat <<'EOF'
Update Mocap Studio to the latest release.

Usage:
  mocap-studio update [OPTIONS]

Options:
  --launch                Launch Mocap Studio after updating.
  --repo OWNER/REPO       Read releases from a different GitHub repository.
  --release-base-url URL  Read release assets from URL/TAG/.
  --tag-prefix PREFIX     Prefix used for release tags (default: studio-v).
  --no-desktop-integration
                          Keep desktop integration disabled.
  -h, --help              Show this help.
EOF
            return
            ;;
        ' uninstall')
            cat <<'EOF'
Uninstall Mocap Studio while preserving recorded takes.

Usage:
  mocap-studio uninstall [--yes]

Options:
  --yes       Confirm non-interactively.
  -h, --help  Show this help.
EOF
            return
            ;;
        ' completion')
            cat <<'EOF'
Install shell completion for the current user.

Usage:
  mocap-studio completion install
EOF
            return
            ;;
    esac
    cat <<'EOF'
Install Mocap Studio for the current user (never run this script with sudo).

Usage:
  ./install.sh [--version VERSION] [--launch] [location options]
  ./install.sh --update [--launch] [location options]
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
  --update                Update a managed installation to the latest release.
  --no-modify-path        Do not add the default command directory to shell startup files.
  --no-desktop-integration
                          Do not install an app-drawer entry or macOS app bundle.
  --no-shell-completions  Do not install Bash, Zsh, or Fish completion.
  --uninstall             Remove installed application versions; preserve all takes.
  --yes                   Confirm uninstall non-interactively.
  -h, --help              Show this help.

Environment equivalents: MOCAP_STUDIO_REPOSITORY,
MOCAP_STUDIO_RELEASE_BASE_URL, MOCAP_STUDIO_TAG_PREFIX,
MOCAP_STUDIO_NO_MODIFY_PATH.
EOF
}

usage_error() {
    printf 'install.sh: error: %s\n' "$*" >&2
    case "${MOCAP_STUDIO_CLI_CONTEXT:-}" in
        ' update'|' uninstall'|' completion')
            printf "Try 'mocap-studio%s --help' for more information.\n" \
                "$MOCAP_STUDIO_CLI_CONTEXT" >&2
            ;;
        *) printf "Try './install.sh --help' for more information.\n" >&2 ;;
    esac
    exit 2
}

die() {
    printf 'install.sh: error: %s\n' "$*" >&2
    exit 1
}

warn() {
    printf 'install.sh: warning: %s\n' "$*" >&2
}

release_install_lock() {
    [ "$INSTALL_LOCK_HELD" -eq 1 ] || return 0
    LOCK_OWNER=
    if [ -f "$INSTALL_LOCK_DIR/pid" ] && [ ! -L "$INSTALL_LOCK_DIR/pid" ]; then
        LOCK_OWNER=$(sed -n '1p' "$INSTALL_LOCK_DIR/pid" 2>/dev/null || true)
    fi
    if [ "$LOCK_OWNER" = "$$" ]; then
        rm -- "$INSTALL_LOCK_DIR/pid"
        rmdir "$INSTALL_LOCK_DIR" 2>/dev/null \
            || warn "installer lock directory could not be removed: $INSTALL_LOCK_DIR"
    else
        warn "installer lock ownership changed; preserving $INSTALL_LOCK_DIR"
    fi
    INSTALL_LOCK_HELD=0
    if [ "$LOCK_CREATED_APP_HOME" -eq 1 ]; then
        rmdir "$APP_HOME" 2>/dev/null || true
    fi
}

github_release_value() {
    RELEASE_JSON_PATH=$1
    RELEASE_VALUE_KIND=$2
    RELEASE_ASSET_NAME=${3-}
    python3 - "$RELEASE_JSON_PATH" "$RELEASE_VALUE_KIND" "$RELEASE_ASSET_NAME" <<'GITHUB_RELEASE_PY'
import json
from pathlib import Path
import sys


def fail(message: str) -> None:
    print(f"GitHub release metadata error: {message}", file=sys.stderr)
    raise SystemExit(1)


path = Path(sys.argv[1])
kind = sys.argv[2]
requested_name = sys.argv[3]
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as error:
    fail(str(error))
if not isinstance(data, dict):
    fail("response is not a JSON object")

if kind == "tag":
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        fail("tag_name is missing")
    print(tag)
elif kind == "asset":
    assets = data.get("assets")
    if not isinstance(assets, list):
        fail("assets is missing")
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == requested_name]
    if len(matches) != 1:
        fail(f"expected exactly one asset named {requested_name!r}, found {len(matches)}")
    asset_id = matches[0].get("id")
    if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
        fail(f"asset {requested_name!r} has an invalid id")
    print(asset_id)
else:
    fail(f"unsupported value kind {kind!r}")
GITHUB_RELEASE_PY
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
    if [ -n "$COMMAND_LINK_TEMP" ] && [ -L "$COMMAND_LINK_TEMP" ] && [ -n "$BIN_DIR" ]; then
        case "$COMMAND_LINK_TEMP" in
            "$BIN_DIR"/.mocap-studio-link.*) rm -- "$COMMAND_LINK_TEMP" ;;
        esac
    fi
    release_install_lock
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

case "${MOCAP_STUDIO_NO_MODIFY_PATH:-0}" in
    0|false|FALSE|no|NO|'') ;;
    1|true|TRUE|yes|YES) MODIFY_PATH=0 ;;
    *) die "MOCAP_STUDIO_NO_MODIFY_PATH must be 0 or 1" ;;
esac

need_value() {
    [ "$#" -ge 2 ] || usage_error "$1 requires a value"
}

select_operation() {
    [ "$OPERATION_EXPLICIT" -eq 0 ] \
        || usage_error "only one management operation may be used"
    OPERATION=$1
    OPERATION_EXPLICIT=1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --version)
            need_value "$@"; VERSION_OPTION_USED=1; REQUESTED_VERSION=$2; shift 2 ;;
        --version=*) VERSION_OPTION_USED=1; REQUESTED_VERSION=${1#*=}; shift ;;
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
            need_value "$@"; REPOSITORY_OPTION_USED=1; REPOSITORY=$2; shift 2 ;;
        --release-base-url)
            need_value "$@"; RELEASE_BASE_OPTION_USED=1; RELEASE_BASE_URL=$2; shift 2 ;;
        --tag-prefix)
            need_value "$@"; TAG_PREFIX_OPTION_USED=1; TAG_PREFIX=$2; shift 2 ;;
        --launch) LAUNCH=1; shift ;;
        --no-modify-path) MODIFY_PATH=0; shift ;;
        --no-desktop-integration) DESKTOP_INTEGRATION=0; shift ;;
        --no-shell-completions) SHELL_COMPLETIONS=0; shift ;;
        --update) select_operation update; shift ;;
        --uninstall) select_operation uninstall; shift ;;
        --install-completions) select_operation completion; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "unknown option: $1" ;;
    esac
done

[ "$ASSUME_YES" -eq 0 ] || [ "$OPERATION" = uninstall ] \
    || usage_error "--yes is accepted only with --uninstall"
[ "$OPERATION" != update ] || [ "$LOCAL_MODE" -eq 0 ] \
    || usage_error "--update cannot be combined with --local"
[ "$OPERATION" != update ] || [ "$REQUESTED_VERSION" = latest ] \
    || usage_error "--update always selects the latest release; do not combine it with --version"
if [ "$OPERATION" = uninstall ]; then
    [ "$LOCAL_MODE" -eq 0 ] \
        || usage_error "--local is not accepted with --uninstall"
    [ "$VERSION_OPTION_USED" -eq 0 ] \
        || usage_error "--version is not accepted with --uninstall"
    [ "$REPOSITORY_OPTION_USED" -eq 0 ] \
        || usage_error "--repo is not accepted with --uninstall"
    [ "$RELEASE_BASE_OPTION_USED" -eq 0 ] \
        || usage_error "--release-base-url is not accepted with --uninstall"
    [ "$TAG_PREFIX_OPTION_USED" -eq 0 ] \
        || usage_error "--tag-prefix is not accepted with --uninstall"
    [ "$LAUNCH" -eq 0 ] \
        || usage_error "--launch is not accepted with --uninstall"
    [ "$DESKTOP_INTEGRATION" -eq 1 ] \
        || usage_error "--no-desktop-integration is not accepted with --uninstall"
    [ "$SHELL_COMPLETIONS" -eq 1 ] \
        || usage_error "--no-shell-completions is not accepted with --uninstall"
fi

[ "$(id -u)" -ne 0 ] || die "do not run this per-user installer as root or with sudo"
[ -n "${HOME:-}" ] || die "HOME is not set"
umask 022

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
DEFAULT_COMMAND_LOCATION=0
if [ -z "$PREFIX" ] && [ -z "$BIN_DIR_OVERRIDE" ]; then
    DEFAULT_COMMAND_LOCATION=1
fi

managed_command() {
    [ -L "$COMMAND_PATH" ] || return 1
    MANAGED_TARGET=$(readlink "$COMMAND_PATH")
    case "$MANAGED_TARGET" in
        "$VERSIONS_DIR"/mocap-studio-*/bin/mocap-studio) return 0 ;;
        *) return 1 ;;
    esac
}

managed_install() {
    managed_command || return 1
    MANAGED_INSTALL_DIR=${MANAGED_TARGET%/bin/mocap-studio}
    case "$MANAGED_INSTALL_DIR" in
        "$VERSIONS_DIR"/mocap-studio-*) ;;
        *) return 1 ;;
    esac
    [ -f "$MANAGED_INSTALL_DIR/.mocap-studio-bundle" ] || return 1
    [ -f "$MANAGED_INSTALL_DIR/VERSION" ] || return 1
    MANAGED_VERSION=$(sed -n '1p' "$MANAGED_INSTALL_DIR/VERSION")
    case "$MANAGED_VERSION" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
    grep -Fqx "version=$MANAGED_VERSION" "$MANAGED_INSTALL_DIR/.mocap-studio-bundle" \
        || return 1
}

try_reclaim_stale_install_lock() {
    [ -d "$INSTALL_LOCK_DIR" ] && [ ! -L "$INSTALL_LOCK_DIR" ] || return 1
    [ -f "$INSTALL_LOCK_DIR/pid" ] && [ ! -L "$INSTALL_LOCK_DIR/pid" ] || return 1
    STALE_LOCK_PID=$(sed -n '1p' "$INSTALL_LOCK_DIR/pid" 2>/dev/null || true)
    case "$STALE_LOCK_PID" in ''|0|*[!0-9]*) return 1 ;; esac
    kill -0 "$STALE_LOCK_PID" 2>/dev/null && return 1
    rm -- "$INSTALL_LOCK_DIR/pid" 2>/dev/null || return 1
    rmdir "$INSTALL_LOCK_DIR" 2>/dev/null || return 1
}

acquire_install_lock() {
    if [ ! -d "$APP_HOME" ]; then
        [ ! -e "$APP_HOME" ] && [ ! -L "$APP_HOME" ] \
            || die "application data path is not a directory: $APP_HOME"
        mkdir -p "$APP_HOME" || die "could not create application data directory $APP_HOME"
        LOCK_CREATED_APP_HOME=1
    fi
    INSTALL_LOCK_DIR=$APP_HOME/.mocap-studio-installer-lock
    if ! mkdir "$INSTALL_LOCK_DIR" 2>/dev/null; then
        if ! try_reclaim_stale_install_lock \
            || ! mkdir "$INSTALL_LOCK_DIR" 2>/dev/null; then
            die "another install, update, or uninstall is already using $APP_HOME (lock: $INSTALL_LOCK_DIR)"
        fi
    fi
    if ! printf '%s\n' "$$" >"$INSTALL_LOCK_DIR/pid"; then
        rmdir "$INSTALL_LOCK_DIR" 2>/dev/null || true
        die "could not record installer lock ownership in $INSTALL_LOCK_DIR"
    fi
    INSTALL_LOCK_HELD=1
}

launch_installed() {
    [ -x "$COMMAND_PATH" ] || die "no runnable install found at $COMMAND_PATH"
    cleanup
    trap - EXIT HUP INT TERM
    exec "$COMMAND_PATH" --reuse-existing
}

run_desktop_helper() {
    DESKTOP_OPERATION=$1
    DESKTOP_HELPER_PATH=$2
    DESKTOP_INSTALL_PATH=$3
    DESKTOP_PYTHON_PATH=$4
    "$DESKTOP_PYTHON_PATH" "$DESKTOP_HELPER_PATH" "$DESKTOP_OPERATION" \
        --platform "$PLATFORM" \
        --app-home "$APP_HOME" \
        --install-dir "$DESKTOP_INSTALL_PATH" \
        --python "$DESKTOP_PYTHON_PATH" \
        --home "$HOME" \
        --xdg-data-home "${XDG_DATA_HOME:-}" \
        --version "${VERSION:-0.0.0}"
}

run_completion_helper() {
    COMPLETION_OPERATION=$1
    COMPLETION_HELPER_PATH=$2
    COMPLETION_INSTALL_PATH=$3
    COMPLETION_PYTHON_PATH=$4
    shift 4
    "$COMPLETION_PYTHON_PATH" "$COMPLETION_HELPER_PATH" "$COMPLETION_OPERATION" \
        --app-home "$APP_HOME" \
        --install-dir "$COMPLETION_INSTALL_PATH" \
        --home "$HOME" \
        --shell "${SHELL:-}" \
        --xdg-data-home "${XDG_DATA_HOME:-}" \
        --xdg-config-home "${XDG_CONFIG_HOME:-}" \
        --bash-completion-user-dir "${BASH_COMPLETION_USER_DIR:-}" \
        --zdotdir "${ZDOTDIR:-}" \
        "$@"
}

print_completion_activation_hint() {
    COMPLETION_HINT_PYTHON=$1
    QUOTED_COMPLETION_COMMAND=$(
        "$COMPLETION_HINT_PYTHON" -c \
            'import shlex, sys; print(shlex.quote(sys.argv[1]))' \
        "$COMMAND_PATH"
    ) || QUOTED_COMPLETION_COMMAND=mocap-studio
    COMPLETION_HINT_SHELL=${SHELL:-}
    case ${COMPLETION_HINT_SHELL##*/} in
        bash)
            printf '%s\n' 'To enable it in this Bash session now:'
            printf '  source <(%s completion bash)\n' "$QUOTED_COMPLETION_COMMAND"
            ;;
        zsh)
            printf '%s\n' 'To enable it in this Zsh session now:'
            printf '  source <(%s completion zsh)\n' "$QUOTED_COMPLETION_COMMAND"
            ;;
        fish)
            printf '%s\n' 'To enable it in this Fish session now:'
            printf '  %s completion fish | source\n' "$QUOTED_COMPLETION_COMMAND"
            ;;
        *)
            printf '%s\n' \
                "Run 'mocap-studio help completion' for current-shell activation commands."
            ;;
    esac
}

append_posix_path_block() {
    PROFILE_PATH=$1
    PROFILE_PARENT=$(dirname "$PROFILE_PATH")
    PATH_PROFILE_ATTEMPTS=$((PATH_PROFILE_ATTEMPTS + 1))
    if [ -L "$PROFILE_PATH" ] && [ ! -e "$PROFILE_PATH" ]; then
        warn "cannot configure PATH through broken profile symlink $PROFILE_PATH"
        return 0
    fi
    if [ -e "$PROFILE_PATH" ] && [ ! -f "$PROFILE_PATH" ]; then
        warn "cannot configure PATH because $PROFILE_PATH is not a regular file"
        return 0
    fi
    if [ -f "$PROFILE_PATH" ]; then
        PATH_BLOCK_START_FOUND=0
        PATH_BLOCK_END_FOUND=0
        grep -Fqx '# >>> mocap-studio managed PATH >>>' "$PROFILE_PATH" && PATH_BLOCK_START_FOUND=1
        grep -Fqx '# <<< mocap-studio managed PATH <<<' "$PROFILE_PATH" && PATH_BLOCK_END_FOUND=1
        if [ "$PATH_BLOCK_START_FOUND" -eq 1 ] || [ "$PATH_BLOCK_END_FOUND" -eq 1 ]; then
            if [ "$PATH_BLOCK_START_FOUND" -eq 1 ] && [ "$PATH_BLOCK_END_FOUND" -eq 1 ]; then
                PATH_PROFILE_SUCCESSES=$((PATH_PROFILE_SUCCESSES + 1))
            else
                warn "managed PATH block is incomplete in $PROFILE_PATH; refusing to append another"
            fi
            return 0
        fi
    fi
    if [ ! -d "$PROFILE_PARENT" ] && ! mkdir -p "$PROFILE_PARENT"; then
        warn "could not create shell configuration directory $PROFILE_PARENT"
        return 0
    fi
    if [ -e "$PROFILE_PATH" ] && [ ! -w "$PROFILE_PATH" ]; then
        warn "shell profile is not writable: $PROFILE_PATH"
        return 0
    fi
    if [ ! -e "$PROFILE_PATH" ] && [ ! -w "$PROFILE_PARENT" ]; then
        warn "shell configuration directory is not writable: $PROFILE_PARENT"
        return 0
    fi
    if {
        [ ! -s "$PROFILE_PATH" ] || printf '\n'
        cat <<'PATH_BLOCK'
# >>> mocap-studio managed PATH >>>
case ":${PATH-}:" in
    *":$HOME/.local/bin:"*) ;;
    *) PATH="$HOME/.local/bin${PATH:+:$PATH}"; export PATH ;;
esac
# <<< mocap-studio managed PATH <<<
PATH_BLOCK
    } >>"$PROFILE_PATH"; then
        PATH_PROFILE_SUCCESSES=$((PATH_PROFILE_SUCCESSES + 1))
    else
        warn "could not update shell profile $PROFILE_PATH"
    fi
}

configure_fish_path() {
    case "${XDG_CONFIG_HOME:-}" in
        /*) FISH_CONFIG_HOME=$XDG_CONFIG_HOME ;;
        *) FISH_CONFIG_HOME=$HOME/.config ;;
    esac
    FISH_CONFIG_DIR=$FISH_CONFIG_HOME/fish/conf.d
    FISH_CONFIG_FILE=$FISH_CONFIG_DIR/mocap-studio-path.fish
    PATH_PROFILE_ATTEMPTS=$((PATH_PROFILE_ATTEMPTS + 1))
    if [ -e "$FISH_CONFIG_FILE" ] || [ -L "$FISH_CONFIG_FILE" ]; then
        if [ -f "$FISH_CONFIG_FILE" ] \
            && grep -Fqx '# >>> mocap-studio managed PATH >>>' "$FISH_CONFIG_FILE" \
            && grep -Fqx '# <<< mocap-studio managed PATH <<<' "$FISH_CONFIG_FILE"; then
            PATH_PROFILE_SUCCESSES=$((PATH_PROFILE_SUCCESSES + 1))
        else
            warn "refusing to replace unmanaged or incomplete fish configuration $FISH_CONFIG_FILE"
        fi
        return 0
    fi
    if ! mkdir -p "$FISH_CONFIG_DIR"; then
        warn "could not create fish configuration directory $FISH_CONFIG_DIR"
        return 0
    fi
    FISH_TEMP_FILE=$(mktemp "$FISH_CONFIG_DIR/.mocap-studio-path.XXXXXX") || {
        warn "could not create temporary fish configuration in $FISH_CONFIG_DIR"
        return 0
    }
    if cat >"$FISH_TEMP_FILE" <<'FISH_PATH_BLOCK'
# >>> mocap-studio managed PATH >>>
if not contains -- "$HOME/.local/bin" $PATH
    set -gx PATH "$HOME/.local/bin" $PATH
end
# <<< mocap-studio managed PATH <<<
FISH_PATH_BLOCK
    then
        if chmod 0644 "$FISH_TEMP_FILE" && mv "$FISH_TEMP_FILE" "$FISH_CONFIG_FILE"; then
            PATH_PROFILE_SUCCESSES=$((PATH_PROFILE_SUCCESSES + 1))
        else
            warn "could not install fish configuration $FISH_CONFIG_FILE"
            [ ! -e "$FISH_TEMP_FILE" ] || rm -- "$FISH_TEMP_FILE"
        fi
    else
        warn "could not create fish configuration $FISH_CONFIG_FILE"
        [ ! -e "$FISH_TEMP_FILE" ] || rm -- "$FISH_TEMP_FILE"
    fi
}

configure_command_path() {
    case ":${PATH-}:" in
        *":$BIN_DIR:"*) PATH_SETUP_RESULT=present; return 0 ;;
    esac
    if [ "$MODIFY_PATH" -ne 1 ]; then
        PATH_SETUP_RESULT=disabled
        return 0
    fi
    if [ "$DEFAULT_COMMAND_LOCATION" -ne 1 ] || [ "$BIN_DIR" != "$HOME/.local/bin" ]; then
        PATH_SETUP_RESULT=custom
        return 0
    fi

    SHELL_PATH=${SHELL:-}
    case "${SHELL_PATH##*/}" in
        zsh)
            append_posix_path_block "$HOME/.zshrc"
            append_posix_path_block "$HOME/.zprofile"
            ;;
        bash)
            append_posix_path_block "$HOME/.bashrc"
            if [ -e "$HOME/.bash_profile" ]; then
                append_posix_path_block "$HOME/.bash_profile"
            elif [ -e "$HOME/.bash_login" ]; then
                append_posix_path_block "$HOME/.bash_login"
            else
                append_posix_path_block "$HOME/.profile"
            fi
            ;;
        fish) configure_fish_path ;;
        sh|dash|ash|ksh|mksh|'') append_posix_path_block "$HOME/.profile" ;;
        *)
            warn "unsupported login shell ${SHELL:-unknown}; PATH was not changed"
            ;;
    esac

    if [ "$PATH_PROFILE_ATTEMPTS" -gt 0 ] \
        && [ "$PATH_PROFILE_SUCCESSES" -eq "$PATH_PROFILE_ATTEMPTS" ]; then
        PATH_SETUP_RESULT=configured
    elif [ "$PATH_PROFILE_SUCCESSES" -gt 0 ]; then
        PATH_SETUP_RESULT=partial
    else
        PATH_SETUP_RESULT=unavailable
    fi
}

acquire_install_lock

if [ "$OPERATION" = completion ]; then
    managed_install || die "no managed Mocap Studio installation was found at $COMMAND_PATH"
    command -v python3 >/dev/null 2>&1 || die "Python 3.10 or newer is required"
    python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        >/dev/null 2>&1 || die "Python 3.10 or newer is required"
    COMPLETION_PYTHON=$(command -v python3)
    case "$COMPLETION_PYTHON" in
        /*) ;;
        *) COMPLETION_PYTHON=$(python3 -c 'import os, sys; print(os.path.abspath(sys.executable))') ;;
    esac
    COMPLETION_HELPER=$MANAGED_INSTALL_DIR/completions/completion_integration.py
    [ -f "$COMPLETION_HELPER" ] \
        || die "the installed shell-completion helper is missing"
    COMPLETION_PROFILE_OPTION=
    if [ "$MODIFY_PATH" -eq 1 ]; then
        COMPLETION_PROFILE_OPTION=--modify-profile
    fi
    run_completion_helper check "$COMPLETION_HELPER" "$MANAGED_INSTALL_DIR" \
        "$COMPLETION_PYTHON" ${COMPLETION_PROFILE_OPTION:+"$COMPLETION_PROFILE_OPTION"} \
        --repair-profile \
        || die "shell completion paths are occupied or unsafe"
    run_completion_helper install "$COMPLETION_HELPER" "$MANAGED_INSTALL_DIR" \
        "$COMPLETION_PYTHON" ${COMPLETION_PROFILE_OPTION:+"$COMPLETION_PROFILE_OPTION"} \
        --repair-profile \
        || die "shell completion could not be installed"
    printf '%s\n' 'Shell completion is installed.'
    print_completion_activation_hint "$COMPLETION_PYTHON"
    exit 0
fi

if [ "$OPERATION" = update ]; then
    managed_install || die "no managed Mocap Studio installation was found at $COMMAND_PATH"
    UPDATE_FROM_VERSION=$MANAGED_VERSION
fi

if [ "$OPERATION" = uninstall ]; then
    FOUND=0
    DESKTOP_HELPER=
    DESKTOP_INSTALL_DIR=
    COMPLETION_HELPER=
    COMPLETION_INSTALL_DIR=
    if [ -d "$VERSIONS_DIR" ]; then
        for VERSION_DIR in "$VERSIONS_DIR"/mocap-studio-*; do
            [ -d "$VERSION_DIR" ] || continue
            [ -f "$VERSION_DIR/.mocap-studio-bundle" ] || continue
            FOUND=1
            if [ -f "$VERSION_DIR/desktop/desktop_integration.py" ]; then
                DESKTOP_HELPER=$VERSION_DIR/desktop/desktop_integration.py
                DESKTOP_INSTALL_DIR=$VERSION_DIR
            fi
            if [ -f "$VERSION_DIR/completions/completion_integration.py" ]; then
                COMPLETION_HELPER=$VERSION_DIR/completions/completion_integration.py
                COMPLETION_INSTALL_DIR=$VERSION_DIR
            fi
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
    if [ -n "$DESKTOP_HELPER" ]; then
        if command -v python3 >/dev/null 2>&1 \
            && python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            UNINSTALL_PYTHON=$(command -v python3)
            case "$UNINSTALL_PYTHON" in
                /*) ;;
                *) UNINSTALL_PYTHON=$(python3 -c 'import os, sys; print(os.path.abspath(sys.executable))') ;;
            esac
            run_desktop_helper uninstall "$DESKTOP_HELPER" "$DESKTOP_INSTALL_DIR" "$UNINSTALL_PYTHON" \
                || warn "desktop integration could not be completely removed"
        else
            warn "Python 3.10 or newer is unavailable; desktop integration was preserved"
        fi
    fi
    if [ -n "$COMPLETION_HELPER" ]; then
        if command -v python3 >/dev/null 2>&1 \
            && python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            UNINSTALL_COMPLETION_PYTHON=$(command -v python3)
            case "$UNINSTALL_COMPLETION_PYTHON" in
                /*) ;;
                *) UNINSTALL_COMPLETION_PYTHON=$(python3 -c 'import os, sys; print(os.path.abspath(sys.executable))') ;;
            esac
            run_completion_helper uninstall "$COMPLETION_HELPER" \
                "$COMPLETION_INSTALL_DIR" "$UNINSTALL_COMPLETION_PYTHON" \
                || warn "shell completion could not be completely removed"
        else
            warn "Python 3.10 or newer is unavailable; shell completion was preserved"
        fi
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

if [ "$OPERATION" = install ] && [ "$LAUNCH" -eq 1 ] && [ "$LOCAL_MODE" -eq 0 ] \
    && [ "$REQUESTED_VERSION" = latest ] && [ -x "$COMMAND_PATH" ]; then
    configure_command_path
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
PYTHON_PATH=$(command -v python3)
case "$PYTHON_PATH" in
    /*) ;;
    *) PYTHON_PATH=$(python3 -c 'import os, sys; print(os.path.abspath(sys.executable))') ;;
esac
case "$PYTHON_PATH" in /*) ;; *) die "could not resolve an absolute Python path" ;; esac
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
    case "$REPOSITORY" in
        ''|/*|*/|*/*/*|*[!A-Za-z0-9._/-]*) die "unsafe GitHub repository name: $REPOSITORY" ;;
        */*) ;;
        *) die "GitHub repository must be OWNER/REPO: $REPOSITORY" ;;
    esac
    GITHUB_RELEASE_JSON=
    if [ "$REQUESTED_VERSION" = latest ]; then
        GITHUB_RELEASE_JSON=$TEMP_DIR/github-release.json
        curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 \
            -H 'Accept: application/vnd.github+json' \
            -H 'X-GitHub-Api-Version: 2022-11-28' \
            -o "$GITHUB_RELEASE_JSON" \
            "https://api.github.com/repos/$REPOSITORY/releases/latest" \
            || die "could not resolve the latest release"
        RELEASE_TAG=$(github_release_value "$GITHUB_RELEASE_JSON" tag) \
            || die "the latest release tag could not be determined"
    else
        case "$REQUESTED_VERSION" in
            "$TAG_PREFIX"*|v*) RELEASE_TAG=$REQUESTED_VERSION ;;
            *) RELEASE_TAG=$TAG_PREFIX$REQUESTED_VERSION ;;
        esac
    fi
    case "$RELEASE_TAG" in ''|*[!A-Za-z0-9._-]*) die "unsafe release tag: $RELEASE_TAG" ;; esac
    case "$RELEASE_TAG" in
        "$TAG_PREFIX"*) VERSION=${RELEASE_TAG#"$TAG_PREFIX"} ;;
        v*) VERSION=${RELEASE_TAG#v} ;;
        *) VERSION=$RELEASE_TAG ;;
    esac
    case "$VERSION" in ''|*[!A-Za-z0-9._-]*) die "unsafe release version: $VERSION" ;; esac
    if [ "$OPERATION" = update ] && [ "$VERSION" = "$UPDATE_FROM_VERSION" ]; then
        printf 'Mocap Studio %s is already up to date.\n' "$VERSION"
        if [ "$LAUNCH" -eq 1 ]; then
            launch_installed
        fi
        exit 0
    fi
    ASSET=mocap-studio-$VERSION-$TARGET.tar.gz
    if [ -z "$RELEASE_BASE_URL" ]; then
        if [ -z "$GITHUB_RELEASE_JSON" ]; then
            GITHUB_RELEASE_JSON=$TEMP_DIR/github-release.json
            curl -fsSL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 \
                -H 'Accept: application/vnd.github+json' \
                -H 'X-GitHub-Api-Version: 2022-11-28' \
                -o "$GITHUB_RELEASE_JSON" \
                "https://api.github.com/repos/$REPOSITORY/releases/tags/$RELEASE_TAG" \
                || die "could not resolve release $RELEASE_TAG"
        fi
        ARCHIVE_ASSET_ID=$(github_release_value "$GITHUB_RELEASE_JSON" asset "$ASSET") \
            || die "release archive $ASSET is missing"
        CHECKSUM_ASSET_ID=$(github_release_value "$GITHUB_RELEASE_JSON" asset "$ASSET.sha256") \
            || die "release checksum $ASSET.sha256 is missing"
        curl -fL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 \
            -H 'Accept: application/octet-stream' \
            -H 'X-GitHub-Api-Version: 2022-11-28' \
            -o "$TEMP_DIR/$ASSET" \
            "https://api.github.com/repos/$REPOSITORY/releases/assets/$ARCHIVE_ASSET_ID"
        curl -fL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 \
            -H 'Accept: application/octet-stream' \
            -H 'X-GitHub-Api-Version: 2022-11-28' \
            -o "$TEMP_DIR/$ASSET.sha256" \
            "https://api.github.com/repos/$REPOSITORY/releases/assets/$CHECKSUM_ASSET_ID"
    else
        DOWNLOAD_ROOT=${RELEASE_BASE_URL%/}/$RELEASE_TAG
        curl -fL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 -o "$TEMP_DIR/$ASSET" "$DOWNLOAD_ROOT/$ASSET"
        curl -fL --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 3 -o "$TEMP_DIR/$ASSET.sha256" "$DOWNLOAD_ROOT/$ASSET.sha256"
    fi
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
        executable_names = {
            f"{expected_root}/bin/mocap-studio",
            f"{expected_root}/libexec/install.sh",
        }
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
                expected_mode = 0o755 if name in executable_names else 0o644
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
            os.chmod(output, 0o755 if member.name in executable_names else 0o644)
except (OSError, tarfile.TarError, ValueError) as error:
    print(f"install.sh: unsafe or invalid release archive: {error}", file=sys.stderr)
    raise SystemExit(1) from error
SAFE_EXTRACT_PY
PAYLOAD=$TEMP_DIR/$PAYLOAD_ROOT
[ -f "$PAYLOAD/.mocap-studio-bundle" ] || die "release bundle marker is missing"
grep -Fqx "version=$VERSION" "$PAYLOAD/.mocap-studio-bundle" || die "release bundle version does not match"
grep -Fqx "target=$TARGET" "$PAYLOAD/.mocap-studio-bundle" || die "release bundle target does not match"
[ -x "$PAYLOAD/bin/mocap-studio" ] || die "release launcher is missing"
[ -x "$PAYLOAD/libexec/install.sh" ] || die "release management helper is missing"
[ -f "$PAYLOAD/backend/mocap_studio/static/index.html" ] || die "release UI is missing"
[ -f "$PAYLOAD/desktop/desktop_integration.py" ] || die "desktop integration helper is missing"
[ -f "$PAYLOAD/desktop/mocap-studio.svg" ] || die "desktop application icon is missing"
[ -f "$PAYLOAD/completions/completion_integration.py" ] \
    || die "shell-completion helper is missing"
[ -f "$PAYLOAD/completions/mocap-studio.bash" ] \
    || die "Bash completion asset is missing"
[ -f "$PAYLOAD/completions/_mocap-studio" ] \
    || die "Zsh completion asset is missing"
[ -f "$PAYLOAD/completions/mocap-studio.fish" ] \
    || die "Fish completion asset is missing"

INSTALL_DIR=$VERSIONS_DIR/$PAYLOAD_ROOT-$TARGET
if [ -e "$COMMAND_PATH" ] || [ -L "$COMMAND_PATH" ]; then
    managed_command || die "$COMMAND_PATH already exists and is not managed by this installer; choose --bin-dir"
fi
if [ -e "$INSTALL_DIR" ]; then
    die "version $VERSION is already installed at $INSTALL_DIR; refusing to overwrite it"
fi
if [ "$DESKTOP_INTEGRATION" -eq 1 ]; then
    run_desktop_helper check "$PAYLOAD/desktop/desktop_integration.py" "$INSTALL_DIR" "$PYTHON_PATH" \
        || die "desktop integration path is occupied; use --no-desktop-integration to leave it unchanged"
fi
if [ "$SHELL_COMPLETIONS" -eq 1 ]; then
    COMPLETION_PROFILE_OPTION=
    if [ "$MODIFY_PATH" -eq 1 ]; then
        COMPLETION_PROFILE_OPTION=--modify-profile
    fi
    run_completion_helper check "$PAYLOAD/completions/completion_integration.py" \
        "$PAYLOAD" "$PYTHON_PATH" \
        ${COMPLETION_PROFILE_OPTION:+"$COMPLETION_PROFILE_OPTION"} \
        || die "shell completion path is occupied; use --no-shell-completions to leave it unchanged"
fi
mkdir -p "$VERSIONS_DIR" "$BIN_DIR"
INCOMING_DIR=$VERSIONS_DIR/.incoming.$$
[ ! -e "$INCOMING_DIR" ] || die "temporary install path already exists: $INCOMING_DIR"
cp -R "$PAYLOAD" "$INCOMING_DIR"
python3 - "$INCOMING_DIR" "$INSTALL_DIR" <<'ATOMIC_INSTALL_PY' \
    || die "another installer created $INSTALL_DIR before this install completed"
import os
import sys

os.rename(sys.argv[1], sys.argv[2])
ATOMIC_INSTALL_PY
INCOMING_DIR=
COMMAND_LINK_TEMP=$BIN_DIR/.mocap-studio-link.$$
[ ! -e "$COMMAND_LINK_TEMP" ] && [ ! -L "$COMMAND_LINK_TEMP" ] \
    || die "temporary command link already exists: $COMMAND_LINK_TEMP"
ln -s "$INSTALL_DIR/bin/mocap-studio" "$COMMAND_LINK_TEMP" \
    || die "could not stage command link at $COMMAND_LINK_TEMP"
python3 - "$COMMAND_LINK_TEMP" "$COMMAND_PATH" "$VERSIONS_DIR" <<'ATOMIC_LINK_PY' \
    || die "could not activate command link at $COMMAND_PATH"
import os
from pathlib import Path
import stat
import sys

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
versions = Path(os.path.abspath(sys.argv[3]))
if os.path.lexists(destination):
    metadata = os.lstat(destination)
    if not stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"refusing to replace non-symlink command path {destination}")
    target = Path(os.readlink(destination))
    if not target.is_absolute():
        target = destination.parent / target
    target = Path(os.path.abspath(target))
    try:
        relative = target.relative_to(versions)
    except ValueError as error:
        raise RuntimeError(f"refusing to replace unmanaged command link {destination}") from error
    if len(relative.parts) != 3 or not relative.parts[0].startswith("mocap-studio-") \
            or relative.parts[1:] != ("bin", "mocap-studio"):
        raise RuntimeError(f"refusing to replace unmanaged command link {destination}")
os.replace(source, destination)
ATOMIC_LINK_PY
COMMAND_LINK_TEMP=

if [ "$DESKTOP_INTEGRATION" -eq 1 ]; then
    run_desktop_helper install "$INSTALL_DIR/desktop/desktop_integration.py" "$INSTALL_DIR" "$PYTHON_PATH" \
        || die "application files installed, but desktop integration failed"
fi
if [ "$SHELL_COMPLETIONS" -eq 1 ]; then
    run_completion_helper install \
        "$INSTALL_DIR/completions/completion_integration.py" "$INSTALL_DIR" \
        "$PYTHON_PATH" ${COMPLETION_PROFILE_OPTION:+"$COMPLETION_PROFILE_OPTION"} \
        || die "application files installed, but shell completion failed"
fi

configure_command_path

if [ "$OPERATION" = update ]; then
    printf 'Updated Mocap Studio from %s to %s for %s.\n' "$UPDATE_FROM_VERSION" "$VERSION" "$TARGET"
else
    printf 'Installed Mocap Studio %s for %s.\n' "$VERSION" "$TARGET"
fi
printf 'Command: %s\n' "$COMMAND_PATH"
if [ "$DESKTOP_INTEGRATION" -eq 0 ]; then
    printf '%s\n' 'Desktop integration was disabled.'
fi
if [ "$SHELL_COMPLETIONS" -eq 0 ]; then
    printf '%s\n' 'Shell completion was disabled.'
fi
case "$PATH_SETUP_RESULT" in
    present) printf '%s\n' 'Run "mocap-studio" from this terminal.' ;;
    configured) printf '%s\n' 'PATH configured. Open a new terminal, then run "mocap-studio".' ;;
    partial) printf '%s\n' 'PATH was only partially configured; review the warnings or use the command path shown above.' ;;
    disabled) printf '%s\n' 'PATH setup was disabled; use the command path shown above.' ;;
    custom) printf '%s\n' 'Custom command locations are not added to PATH; use the command path shown above.' ;;
    unavailable) printf '%s\n' 'PATH could not be configured; use the command path shown above.' ;;
esac

if [ "$LAUNCH" -eq 1 ]; then
    launch_installed
fi

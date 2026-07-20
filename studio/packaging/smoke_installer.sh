#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p

set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -P "$SCRIPT_DIR/../.." 2>/dev/null && pwd)
SMOKE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/mocap-studio-platform-smoke.XXXXXX")
SMOKE_HOME=$SMOKE_ROOT/home\ with\ space
SMOKE_BIN=$SMOKE_HOME/.local/bin
SERVER_PID=

cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [ -d "$SMOKE_ROOT" ]; then
        case "$SMOKE_ROOT" in
            "${TMPDIR:-/tmp}"/mocap-studio-platform-smoke.*) rm -rf -- "$SMOKE_ROOT" ;;
        esac
    fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

cd "$REPOSITORY_ROOT"
sh -n install.sh
sh -n studio/packaging/mocap-studio
EXPECTED_VERSION=$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' \
    studio/backend/mocap_studio/__init__.py)
[ -n "$EXPECTED_VERSION" ] || {
    printf '%s\n' 'Could not determine the declared Mocap Studio version' >&2
    exit 1
}
python3 studio/packaging/check_versions.py --expected "$EXPECTED_VERSION"
python3 -m unittest studio/packaging/test_installer_archive.py -v
python3 -m unittest studio/packaging/test_installer_remote.py -v
python3 -m unittest studio/packaging/test_desktop_integration.py -v
python3 -m unittest studio/packaging/test_shell_completions.py -v
python3 -m unittest studio/packaging/test_completion_integration.py -v
python3 -m unittest studio/packaging/test_cli_help.py -v

mkdir -p "$SMOKE_HOME"
HOME=$SMOKE_HOME
export HOME
unset XDG_CONFIG_HOME XDG_DATA_HOME
case "$(uname -s)" in
    Darwin)
        SHELL=/bin/zsh
        APP_HOME=$HOME/Library/Application\ Support/Mocap\ Studio
        APP_LAUNCHER=$HOME/Applications/Mocap\ Studio.app/Contents/MacOS/mocap-studio
        APP_BUNDLE=$HOME/Applications/Mocap\ Studio.app
        PROFILE_ONE=$HOME/.zshrc
        PROFILE_TWO=$HOME/.zprofile
        ;;
    Linux)
        SHELL=/bin/bash
        APP_HOME=$HOME/.local/share/mocap-studio
        APP_LAUNCHER=$APP_HOME/desktop/mocap-studio
        DESKTOP_ENTRY=$HOME/.local/share/applications/io.github.KevinMi2023p.MocapStudio.desktop
        DESKTOP_ICON=$HOME/.local/share/icons/hicolor/scalable/apps/io.github.KevinMi2023p.MocapStudio.svg
        PROFILE_ONE=$HOME/.bashrc
        PROFILE_TWO=$HOME/.profile
        ;;
    *) printf '%s\n' 'Unsupported smoke platform' >&2; exit 1 ;;
esac
export SHELL
TAKES_DIR=$APP_HOME/takes
BASH_COMPLETION=$HOME/.local/share/bash-completion/completions/mocap-studio
ZSH_COMPLETION=$HOME/.local/share/zsh/site-functions/_mocap-studio
FISH_COMPLETION=$HOME/.config/fish/completions/mocap-studio.fish
printf '%s\n' '# existing interactive shell configuration' >"$PROFILE_ONE"
printf '%s\n' '# existing login shell configuration' >"$PROFILE_TWO"

./install.sh --local "$REPOSITORY_ROOT"
"$SMOKE_BIN/mocap-studio" --version | grep -Fqx "mocap-studio $EXPECTED_VERSION"
[ -x "$APP_LAUNCHER" ]
"$APP_LAUNCHER" --version | grep -Fqx "mocap-studio $EXPECTED_VERSION"
if [ "$(uname -s)" = Darwin ]; then
    [ -f "$APP_BUNDLE/Contents/Info.plist" ]
    [ -f "$APP_BUNDLE/Contents/Resources/.mocap-studio-managed" ]
    plutil -lint "$APP_BUNDLE/Contents/Info.plist" >/dev/null
else
    [ -f "$DESKTOP_ENTRY" ]
    [ -f "$DESKTOP_ICON" ]
    grep -Fqx "Exec=\"$APP_LAUNCHER\"" "$DESKTOP_ENTRY"
    grep -Fqx 'Terminal=false' "$DESKTOP_ENTRY"
    grep -Fqx 'X-Mocap-Studio-Managed=true' "$DESKTOP_ENTRY"
fi
head -n 1 "$PROFILE_ONE" | grep -Fqx '# existing interactive shell configuration'
head -n 1 "$PROFILE_TWO" | grep -Fqx '# existing login shell configuration'
grep -Fxc '# >>> mocap-studio managed PATH >>>' "$PROFILE_ONE" | grep -Fqx '1'
grep -Fxc '# >>> mocap-studio managed PATH >>>' "$PROFILE_TWO" | grep -Fqx '1'
grep -Fxc '# >>> mocap-studio managed completion >>>' "$PROFILE_ONE" | grep -Fqx '1'
[ -f "$BASH_COMPLETION" ]
[ -f "$ZSH_COMPLETION" ]
[ -f "$FISH_COMPLETION" ]
if [ "$(uname -s)" = Darwin ]; then
    env -i HOME="$HOME" SHELL=/bin/zsh PATH=/usr/bin:/bin \
        /bin/zsh -lic 'command -v mocap-studio' | grep -Fqx "$SMOKE_BIN/mocap-studio"
    env -i HOME="$HOME" SHELL=/bin/zsh PATH=/usr/bin:/bin \
        /bin/zsh -lic 'typeset -f _mocap_studio >/dev/null'
else
    env -i HOME="$HOME" SHELL=/bin/bash PATH=/usr/bin:/bin \
        /bin/bash --noprofile --rcfile "$PROFILE_ONE" -ic 'command -v mocap-studio' \
        | grep -Fqx "$SMOKE_BIN/mocap-studio"
    env -i HOME="$HOME" SHELL=/bin/bash PATH=/usr/bin:/bin \
        /bin/bash --noprofile --rcfile "$PROFILE_ONE" -ic 'complete -p mocap-studio' \
        | grep -Fq '_mocap_studio_complete mocap-studio'
fi

mkdir -p "$TAKES_DIR/sentinel"
printf '%s\n' 'preserve me' >"$TAKES_DIR/sentinel/take.txt"
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
"$SMOKE_BIN/mocap-studio" \
    --no-browser \
    --port "$PORT" \
    --data-dir "$TAKES_DIR" \
    >"$SMOKE_ROOT/server.log" 2>&1 &
SERVER_PID=$!

READY=0
ATTEMPTS=0
while [ "$ATTEMPTS" -lt 100 ]; do
    if curl -fsS "http://127.0.0.1:$PORT/api/health" >"$SMOKE_ROOT/health.json" 2>/dev/null; then
        READY=1
        break
    fi
    ATTEMPTS=$((ATTEMPTS + 1))
    sleep 0.1
done
if [ "$READY" -ne 1 ]; then
    sed -n '1,80p' "$SMOKE_ROOT/server.log" >&2
    exit 1
fi
grep -Fqx '{"status":"ok"}' "$SMOKE_ROOT/health.json"
curl -fsS "http://127.0.0.1:$PORT/" | grep -Fq '<title>Mocap Studio</title>'

kill "$SERVER_PID"
wait "$SERVER_PID" || true
SERVER_PID=
"$SMOKE_BIN/mocap-studio" uninstall --yes
[ ! -e "$SMOKE_BIN/mocap-studio" ] && [ ! -L "$SMOKE_BIN/mocap-studio" ]
[ ! -e "$APP_LAUNCHER" ]
[ ! -e "$BASH_COMPLETION" ]
[ ! -e "$ZSH_COMPLETION" ]
[ ! -e "$FISH_COMPLETION" ]
! grep -Fq '# >>> mocap-studio managed completion >>>' "$PROFILE_ONE"
if [ "$(uname -s)" = Darwin ]; then
    [ ! -e "$APP_BUNDLE" ]
else
    [ ! -e "$DESKTOP_ENTRY" ]
    [ ! -e "$DESKTOP_ICON" ]
fi
[ -f "$TAKES_DIR/sentinel/take.txt" ]

# Reinstalling must reuse, not duplicate, the managed shell configuration.
./install.sh --local "$REPOSITORY_ROOT"
grep -Fxc '# >>> mocap-studio managed PATH >>>' "$PROFILE_ONE" | grep -Fqx '1'
grep -Fxc '# >>> mocap-studio managed PATH >>>' "$PROFILE_TWO" | grep -Fqx '1'
grep -Fxc '# >>> mocap-studio managed completion >>>' "$PROFILE_ONE" | grep -Fqx '1'
./install.sh --uninstall --yes
[ -f "$TAKES_DIR/sentinel/take.txt" ]

# Explicit opt-out must leave a fresh home directory's shell files untouched.
OPT_OUT_HOME=$SMOKE_ROOT/opt-out-home
mkdir -p "$OPT_OUT_HOME"
HOME=$OPT_OUT_HOME SHELL=$SHELL ./install.sh --local "$REPOSITORY_ROOT" --no-modify-path
[ ! -e "$OPT_OUT_HOME/.zshrc" ]
[ ! -e "$OPT_OUT_HOME/.zprofile" ]
[ ! -e "$OPT_OUT_HOME/.bashrc" ]
[ ! -e "$OPT_OUT_HOME/.profile" ]
HOME=$OPT_OUT_HOME SHELL=$SHELL ./install.sh --uninstall --yes --no-modify-path

# Desktop integration may be explicitly disabled without affecting the CLI.
NO_DESKTOP_HOME=$SMOKE_ROOT/no-desktop-home
mkdir -p "$NO_DESKTOP_HOME"
HOME=$NO_DESKTOP_HOME SHELL=$SHELL ./install.sh --local "$REPOSITORY_ROOT" \
    --no-modify-path --no-desktop-integration
if [ "$(uname -s)" = Darwin ]; then
    [ ! -e "$NO_DESKTOP_HOME/Applications/Mocap Studio.app" ]
else
    [ ! -e "$NO_DESKTOP_HOME/.local/share/applications/io.github.KevinMi2023p.MocapStudio.desktop" ]
fi
HOME=$NO_DESKTOP_HOME SHELL=$SHELL ./install.sh --uninstall --yes --no-modify-path

# An interrupted managed block must be reported, not duplicated or called a success.
PARTIAL_HOME=$SMOKE_ROOT/partial-home
mkdir -p "$PARTIAL_HOME"
if [ "$(uname -s)" = Darwin ]; then
    PARTIAL_ONE=$PARTIAL_HOME/.zshrc
    PARTIAL_TWO=$PARTIAL_HOME/.zprofile
else
    PARTIAL_ONE=$PARTIAL_HOME/.bashrc
    PARTIAL_TWO=$PARTIAL_HOME/.profile
fi
printf '%s\n' '# >>> mocap-studio managed PATH >>>' >"$PARTIAL_ONE"
HOME=$PARTIAL_HOME SHELL=$SHELL ./install.sh --local "$REPOSITORY_ROOT" \
    >"$SMOKE_ROOT/partial.log" 2>&1
grep -Fq 'managed PATH block is incomplete' "$SMOKE_ROOT/partial.log"
grep -Fq 'PATH was only partially configured' "$SMOKE_ROOT/partial.log"
grep -Fxc '# >>> mocap-studio managed PATH >>>' "$PARTIAL_ONE" | grep -Fqx '1'
grep -Fxc '# >>> mocap-studio managed PATH >>>' "$PARTIAL_TWO" | grep -Fqx '1'
HOME=$PARTIAL_HOME SHELL=$SHELL ./install.sh --uninstall --yes

# An unmanaged app entry/bundle must never be overwritten.
COLLISION_HOME=$SMOKE_ROOT/collision-home
mkdir -p "$COLLISION_HOME"
if [ "$(uname -s)" = Darwin ]; then
    COLLISION_TARGET=$COLLISION_HOME/Applications/Mocap\ Studio.app/keep.txt
else
    COLLISION_TARGET=$COLLISION_HOME/.local/share/applications/io.github.KevinMi2023p.MocapStudio.desktop
fi
mkdir -p "$(dirname "$COLLISION_TARGET")"
printf '%s\n' 'unmanaged sentinel' >"$COLLISION_TARGET"
if HOME=$COLLISION_HOME SHELL=$SHELL ./install.sh --local "$REPOSITORY_ROOT" \
    --no-modify-path >"$SMOKE_ROOT/collision.log" 2>&1; then
    printf '%s\n' 'installer replaced an unmanaged desktop path' >&2
    exit 1
fi
grep -Fqx 'unmanaged sentinel' "$COLLISION_TARGET"
grep -Fq 'desktop integration path is occupied' "$SMOKE_ROOT/collision.log"

printf '%s\n' "Mocap Studio installer smoke passed on $(uname -s) $(uname -m)."

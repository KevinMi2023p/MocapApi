#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p

set -eu

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd)
REPOSITORY_ROOT=$(CDPATH= cd -P "$SCRIPT_DIR/../.." 2>/dev/null && pwd)
SMOKE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/mocap-studio-platform-smoke.XXXXXX")
SMOKE_PREFIX=$SMOKE_ROOT/prefix
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
python3 studio/packaging/check_versions.py --expected 0.1.0
python3 -m unittest studio/packaging/test_installer_archive.py -v

./install.sh --local . --prefix "$SMOKE_PREFIX"
"$SMOKE_PREFIX/bin/mocap-studio" --version | grep -Fqx 'mocap-studio 0.1.0'

mkdir -p "$SMOKE_PREFIX/share/mocap-studio/takes/sentinel"
printf '%s\n' 'preserve me' >"$SMOKE_PREFIX/share/mocap-studio/takes/sentinel/take.txt"
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
"$SMOKE_PREFIX/bin/mocap-studio" \
    --no-browser \
    --port "$PORT" \
    --data-dir "$SMOKE_PREFIX/share/mocap-studio/takes" \
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
./install.sh --uninstall --yes --prefix "$SMOKE_PREFIX"
[ ! -e "$SMOKE_PREFIX/bin/mocap-studio" ]
[ -f "$SMOKE_PREFIX/share/mocap-studio/takes/sentinel/take.txt" ]
printf '%s\n' "Mocap Studio installer smoke passed on $(uname -s) $(uname -m)."

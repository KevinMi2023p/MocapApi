#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p

set -eu

OUTPUT_DIR=/output
AXIS_EXECUTABLE=/axis/axis_studio.exe
WAIT_SECONDS=${AXIS_WINE_WAIT_SECONDS:-25}

case "$WAIT_SECONDS" in
    ''|*[!0-9]*)
        printf '%s\n' 'AXIS_WINE_WAIT_SECONDS must be a whole number.' >&2
        exit 2
        ;;
esac

if [ "$WAIT_SECONDS" -lt 5 ] || [ "$WAIT_SECONDS" -gt 55 ]; then
    printf '%s\n' 'AXIS_WINE_WAIT_SECONDS must be between 5 and 55.' >&2
    exit 2
fi

[ -f "$AXIS_EXECUTABLE" ] || {
    printf '%s\n' 'axis_studio.exe was not found in the read-only /axis mount.' >&2
    exit 2
}

mkdir -p "$OUTPUT_DIR"

{
    wine --version
    sha256sum "$AXIS_EXECUTABLE"
    printf 'WINEDLLOVERRIDES=%s\n' "${WINEDLLOVERRIDES:-}"
    printf 'observation_seconds=%s\n' "$WAIT_SECONDS"
} >"$OUTPUT_DIR/environment.txt"

XVFB_PID=
OPENBOX_PID=
AXIS_PID=

cleanup() {
    if [ -n "$AXIS_PID" ]; then
        kill "$AXIS_PID" 2>/dev/null || true
    fi
    wineserver -k 2>/dev/null || true
    if [ -n "$OPENBOX_PID" ]; then
        kill "$OPENBOX_PID" 2>/dev/null || true
    fi
    if [ -n "$XVFB_PID" ]; then
        kill "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT HUP INT TERM

Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp >"$OUTPUT_DIR/xvfb.log" 2>&1 &
XVFB_PID=$!

display_ready=0
attempt=0
while [ "$attempt" -lt 50 ]; do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        display_ready=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 0.1
done
[ "$display_ready" -eq 1 ] || {
    printf '%s\n' 'Xvfb did not become ready.' >&2
    exit 1
}

openbox >"$OUTPUT_DIR/openbox.log" 2>&1 &
OPENBOX_PID=$!

wineboot --init >"$OUTPUT_DIR/wineboot.log" 2>&1 || true

cd /axis
wine "$AXIS_EXECUTABLE" >"$OUTPUT_DIR/axis-studio.log" 2>&1 &
AXIS_PID=$!

elapsed=0
while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    if ! kill -0 "$AXIS_PID" 2>/dev/null; then
        break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

wmctrl -l >"$OUTPUT_DIR/windows.txt" 2>&1 || true
xprop -root _NET_CLIENT_LIST_STACKING >"$OUTPUT_DIR/x11-clients.txt" 2>&1 || true

# Capture the complete fixed-size reference desktop. The source application is
# never copied out of the container.
import -display "$DISPLAY" -window root "$OUTPUT_DIR/axis-studio.png" \
    >"$OUTPUT_DIR/screenshot.log" 2>&1 || true

if kill -0 "$AXIS_PID" 2>/dev/null; then
    printf 'running_after_seconds=%s\n' "$elapsed" >"$OUTPUT_DIR/status.txt"
    kill "$AXIS_PID" 2>/dev/null || true
    wait "$AXIS_PID" 2>/dev/null || true
    AXIS_PID=
else
    wait "$AXIS_PID" 2>/dev/null || axis_status=$?
    printf 'exited_after_seconds=%s\nexit_status=%s\n' \
        "$elapsed" "${axis_status:-0}" >"$OUTPUT_DIR/status.txt"
    AXIS_PID=
fi

wineserver -k 2>/dev/null || true
touch "$OUTPUT_DIR/ready"

# Docker does not expose tmpfs contents after a container stops. Stay alive for
# a bounded handoff window so capture.sh can export the fixed artifact list.
hold_seconds=0
while [ "$hold_seconds" -lt 30 ]; do
    sleep 1
    hold_seconds=$((hold_seconds + 1))
done

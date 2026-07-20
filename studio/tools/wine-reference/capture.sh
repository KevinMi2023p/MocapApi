#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p

set -eu

usage() {
    cat <<'EOF'
Build and run the isolated Axis Studio Wine reference container.

Usage:
  ./capture.sh AXIS_DIRECTORY OUTPUT_DIRECTORY

AXIS_DIRECTORY must be the extracted vendor application directory containing
axis_studio.exe. It is mounted read-only. OUTPUT_DIRECTORY receives only the
screenshot and diagnostic text files before the disposable container is
removed.
EOF
}

if [ "$#" -ne 2 ]; then
    usage >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd)
AXIS_DIRECTORY=$(CDPATH= cd -P "$1" 2>/dev/null && pwd) || {
    printf 'Axis directory does not exist: %s\n' "$1" >&2
    exit 2
}
OUTPUT_DIRECTORY=$2

[ -f "$AXIS_DIRECTORY/axis_studio.exe" ] || {
    printf 'axis_studio.exe is missing from %s\n' "$AXIS_DIRECTORY" >&2
    exit 2
}

case "$OUTPUT_DIRECTORY" in
    /*) ;;
    *) OUTPUT_DIRECTORY=$(pwd)/$OUTPUT_DIRECTORY ;;
esac

case "$OUTPUT_DIRECTORY" in
    /|"$AXIS_DIRECTORY"|"$AXIS_DIRECTORY"/*)
        printf 'Refusing unsafe output directory: %s\n' "$OUTPUT_DIRECTORY" >&2
        exit 2
        ;;
esac

mkdir -p "$OUTPUT_DIRECTORY"
OUTPUT_DIRECTORY=$(CDPATH= cd -P "$OUTPUT_DIRECTORY" 2>/dev/null && pwd)
case "$OUTPUT_DIRECTORY" in
    /|"$AXIS_DIRECTORY"|"$AXIS_DIRECTORY"/*)
        printf 'Refusing unsafe resolved output directory: %s\n' "$OUTPUT_DIRECTORY" >&2
        exit 2
        ;;
esac

IMAGE=${AXIS_WINE_IMAGE:-mocap-studio-axis-wine-reference:ubuntu-24.04}
ARTIFACTS='axis-studio.png status.txt environment.txt windows.txt x11-clients.txt axis-studio.log wineboot.log xvfb.log openbox.log screenshot.log'
OBSERVATION_SECONDS=${AXIS_WINE_WAIT_SECONDS:-25}

case "$OBSERVATION_SECONDS" in
    ''|*[!0-9]*) die_wait=1 ;;
    *) die_wait=0 ;;
esac
if [ "$die_wait" -eq 1 ] \
    || [ "$OBSERVATION_SECONDS" -lt 5 ] \
    || [ "$OBSERVATION_SECONDS" -gt 55 ]; then
    printf '%s\n' 'AXIS_WINE_WAIT_SECONDS must be between 5 and 55.' >&2
    exit 2
fi

for artifact in $ARTIFACTS; do
    if [ -e "$OUTPUT_DIRECTORY/$artifact" ]; then
        printf 'Refusing to overwrite existing artifact: %s\n' \
            "$OUTPUT_DIRECTORY/$artifact" >&2
        exit 2
    fi
done

docker build --pull --tag "$IMAGE" --file "$SCRIPT_DIR/Containerfile" "$SCRIPT_DIR"

CONTAINER_ID=

cleanup() {
    if [ -n "$CONTAINER_ID" ]; then
        docker rm -f "$CONTAINER_ID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT HUP INT TERM

CONTAINER_ID=$(docker create \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --pids-limit 512 \
    --memory 3g \
    --cpus 2 \
    --env "AXIS_WINE_WAIT_SECONDS=$OBSERVATION_SECONDS" \
    --env "WINEDEBUG=${AXIS_WINE_DEBUG:--all}" \
    --env "WINEDLLOVERRIDES=${AXIS_WINE_DLL_OVERRIDES-hnetcfg=d}" \
    --tmpfs /tmp:rw,nodev,nosuid,size=256m \
    --tmpfs /home/analyst:rw,nodev,nosuid,size=1536m,uid=10001,gid=10001 \
    --tmpfs /output:rw,nodev,nosuid,size=128m,uid=10001,gid=10001 \
    --mount "type=bind,src=$AXIS_DIRECTORY,dst=/axis,readonly" \
    "$IMAGE")

docker start "$CONTAINER_ID" >/dev/null

ready=0
attempt=0
max_attempts=$(((OBSERVATION_SECONDS + 20) * 4))
while [ "$attempt" -lt "$max_attempts" ]; do
    if docker exec "$CONTAINER_ID" test -f /output/ready >/dev/null 2>&1; then
        ready=1
        break
    fi
    running=$(docker inspect --format '{{.State.Running}}' "$CONTAINER_ID" 2>/dev/null || true)
    [ "$running" = true ] || break
    attempt=$((attempt + 1))
    sleep 0.25
done

if [ "$ready" -ne 1 ]; then
    printf '%s\n' 'Reference container stopped before producing artifacts.' >&2
    docker logs "$CONTAINER_ID" >&2 || true
    exit 1
fi

# Export a fixed list from the bounded tmpfs. Axis and its Wine processes have
# already stopped; the proprietary process never receives a writable host mount.
docker exec --env "ARTIFACTS=$ARTIFACTS" "$CONTAINER_ID" /bin/sh -c '
    cd /output
    set --
    for artifact in $ARTIFACTS; do
        if [ -f "$artifact" ] && [ ! -L "$artifact" ]; then
            set -- "$@" "$artifact"
        fi
    done
    [ "$#" -gt 0 ]
    exec tar -cf - -- "$@"
' | tar -C "$OUTPUT_DIRECTORY" -xf -

[ -s "$OUTPUT_DIRECTORY/environment.txt" ] || {
    printf '%s\n' 'Reference environment metadata was not captured.' >&2
    exit 1
}
[ -s "$OUTPUT_DIRECTORY/status.txt" ] || {
    printf '%s\n' 'Reference process status was not captured.' >&2
    exit 1
}

printf 'Reference artifacts copied to %s\n' "$OUTPUT_DIRECTORY"
printf '%s\n' 'This run had no network and no host USB devices.'

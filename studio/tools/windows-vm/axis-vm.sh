#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
# Provision and manage the attached QEMU/KVM Windows VM that runs Noitom
# Axis Studio and streams BVH over UDP to Mocap Studio on this Linux host.
# Implements "Architecture B: Windows VM passthrough" from
# studio/docs/USB_ON_LINUX.md; the full guide is studio/docs/WINDOWS_VM.md.

set -eu

# ---------------------------------------------------------------------------
# Canonical constants (single source of truth; keep in sync with the docs).
# ---------------------------------------------------------------------------
VM_NAME_DEFAULT=axis-studio
NET_NAME=axis-mocap
NET_BRIDGE=virbr-axis
NET_SUBNET=192.168.77.0/24
HOST_IP=192.168.77.1
GUEST_IP=192.168.77.2
GUEST_MAC=52:54:00:6d:63:70
BVH_PORT=7012
GUEST_BVH_LOCAL_PORT=7001
ROTATION_ORDER=YXZ
GUEST_ACCOUNT=mocap
# The transceiver RNDIS link inside the guest owns 192.168.1.0/24 (Axis
# manual); VM networking must never reuse it.
TRANSCEIVER_SUBNET=192.168.1.0/24
DISK_GIB_DEFAULT=100
RAM_MIB_DEFAULT=8192
VCPUS_DEFAULT=4
AXIS_CATALOG_URL='https://www.noitom.com/app-api/front/getSoftware?id=1'
AXIS2_URL=https://noitom.com/download/Axis_Studio_x64_2_12_14002.zip
AXIS3_URL=https://noitom.com/download/Axis_Studio_nacs_x64_3_0_14004_2620_20251222173616363.msi
HOTPLUG_HELPER_PATH=/usr/local/libexec/axis-vm-usb-hotplug.sh
UDEV_RULES_PATH=/etc/udev/rules.d/70-axis-vm-usb.rules

# ---------------------------------------------------------------------------
# Mutable state.
# ---------------------------------------------------------------------------
OPERATION=preflight
OPERATION_EXPLICIT=0
OP_ARG=
VM_NAME=$VM_NAME_DEFAULT
STATE_DIR_OVERRIDE=
STATE_DIR=
SCRIPT_DIR=
ASSUME_YES=0
WINDOWS_ISO=
ISO_SHA256=
VIRTIO_ISO=
DISK_GIB=$DISK_GIB_DEFAULT
RAM_MIB=$RAM_MIB_DEFAULT
VCPUS=$VCPUS_DEFAULT
USB_VENDOR=
USB_PRODUCT=
TEMP_DIR=
PRINTED_PRIV_HEADER=0
BLOCKERS=0
WARNINGS=0

usage() {
    cat <<'EOF'
Provision and manage the attached QEMU/KVM Windows VM that runs Noitom Axis
Studio and streams standard BVH over UDP to Mocap Studio on this Linux host.
Never run this script as root. Full guide: studio/docs/WINDOWS_VM.md.

Usage:
  ./axis-vm.sh [OPERATION] [OPTIONS]

Operations (mutually exclusive; default: --preflight):
  --preflight              Check CPU/KVM, RAM, disk, WSL, packages, groups,
                           and route conflicts. Exits 1 on hard blockers.
  --install-deps           Print (or with --yes run) the apt-get and usermod
                           commands that prepare this host.
  --define-network         Define the isolated 'axis-mocap' libvirt network
                           (bridge virbr-axis, host 192.168.77.1/24).
  --download-axis 2|3      Download the Axis Studio 2 or 3 installer into the
                           payload directory and pin its SHA-256.
  --build-payload-iso      Build axis-payload.iso (installers, guest-setup.ps1,
                           optional mesa3d archives) and offer it to the VM.
  --create                 Create the VM and start unattended Windows Setup.
  --probe-usb              Measure the transceiver USB VID:PID (lsusb diff).
  --attach-usb VID:PID     Attach the USB device to the VM (persistent).
  --detach-usb VID:PID     Detach the USB device from the VM (persistent).
  --install-hotplug VID:PID
                           Print (or with --yes install) udev rules that
                           re-attach the device on hotplug.
  --firewall               Print (or with --yes apply) the inbound UDP 7012
                           rule scoped to interface virbr-axis.
  --start                  Start the VM.
  --shutdown               Ask the guest to shut down (ACPI).
  --destroy                Force the VM off (asks for confirmation).
  --autostart on|off       Start the VM together with libvirtd.
  --console                Open the SPICE console (virt-viewer).
  --rdp                    Connect over RDP as 'mocap' (xfreerdp/wlfreerdp).
  --status                 Show domain, network, USB, and UDP 7012 listener
                           state.
  --detach-internet        Remove the NAT NIC (kept for activation/updates).
  --attach-internet        Re-add the NAT NIC.
  -h, --help               Show this help.

Options for --create:
  --windows-iso PATH       Windows 10/11 installation ISO (required;
                           user-supplied, never downloaded by this script).
  --iso-sha256 HASH        Verify the Windows ISO against this SHA-256.
  --virtio-iso PATH        Optional virtio-win ISO for later in-guest upgrades.
  --disk-gib N             System disk size in GiB (default: 100).
  --ram-mib N              Guest RAM in MiB (default: 8192).
  --vcpus N                Guest vCPUs (default: 4).

Common options:
  --name NAME              Libvirt domain name (default: axis-studio).
  --state-dir DIR          State directory
                           (default: ${XDG_DATA_HOME:-~/.local/share}/axis-vm).
  --yes                    Execute privileged steps via sudo and skip
                           confirmations (default: print the exact commands).

The BVH stream contract is fixed: Axis Studio broadcasts Binary BVH
(rotation YXZ, displacement on) over UDP from 192.168.77.2:7001 to
192.168.77.1:7012, where Mocap Studio listens (host 192.168.77.1, not
127.0.0.1).
EOF
}

usage_error() {
    printf 'axis-vm.sh: error: %s\n' "$*" >&2
    printf "Try './axis-vm.sh --help' for more information.\n" >&2
    exit 2
}

die() {
    printf 'axis-vm.sh: error: %s\n' "$*" >&2
    exit 1
}

warn() {
    printf 'axis-vm.sh: warning: %s\n' "$*" >&2
}

cleanup() {
    if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then
        case "$TEMP_DIR" in
            "${TMPDIR:-/tmp}"/axis-vm.*) rm -rf -- "$TEMP_DIR" ;;
        esac
    fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
need_value() {
    [ "$#" -ge 2 ] || usage_error "$1 requires a value"
}

select_operation() {
    [ "$OPERATION_EXPLICIT" -eq 0 ] \
        || usage_error "only one operation may be used per invocation"
    OPERATION=$1
    OPERATION_EXPLICIT=1
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 \
        || die "required command is missing: $1 (${2:-run --install-deps})"
}

is_uint() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

is_vid_pid() {
    case "$1" in
        [0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F])
            return 0 ;;
        *) return 1 ;;
    esac
}

make_temp_dir() {
    [ -z "$TEMP_DIR" ] || return 0
    TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/axis-vm.XXXXXX") \
        || die "could not create a temporary directory"
}

ensure_state_dir() {
    mkdir -p "$STATE_DIR" || die "could not create state directory $STATE_DIR"
}

# Privilege model: privileged host mutations are printed by default and run
# through sudo only when --yes is given. Read-only operations never use sudo.
run_priv() {
    if [ "$ASSUME_YES" -eq 1 ]; then
        printf 'axis-vm.sh: + sudo %s\n' "$*"
        sudo "$@"
    else
        if [ "$PRINTED_PRIV_HEADER" -eq 0 ]; then
            printf '%s\n' 'Privileged steps (not executed). Review them, then rerun with --yes:'
            PRINTED_PRIV_HEADER=1
        fi
        printf '  sudo %s\n' "$*"
    fi
}

file_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{ print $1 }'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{ print $1 }'
    else
        python3 -c 'import hashlib, sys
digest = hashlib.sha256()
with open(sys.argv[1], "rb") as stream:
    for chunk in iter(lambda: stream.read(1 << 20), b""):
        digest.update(chunk)
print(digest.hexdigest())' "$1"
    fi
}

print_bvh_contract() {
    cat <<EOF
BVH stream contract (canonical):
  Axis Studio (guest) -> Settings -> BVH Broadcasting:
    enable "BVH - Capture" (never "Advanced BVH")
    Frame Format: Binary ("use old header format" unchecked)
    Rotation: $ROTATION_ORDER; Displacement: checked
    Protocol: UDP
    Local address: $GUEST_IP port $GUEST_BVH_LOCAL_PORT
    Destination address: $HOST_IP port $BVH_PORT
    Never broadcast to 255.255.255.255.
  Mocap Studio (host) source settings:
    mode "bvh", transport "udp", host $HOST_IP (NOT the default
    127.0.0.1 - loopback never receives guest packets), port $BVH_PORT,
    rotationOrder "$ROTATION_ORDER", unit "centimeters".
EOF
}

render_network_xml() {
    # Isolated network: no <forward> element, so guests reach only the host.
    cat > "$1" <<EOF
<!-- Isolated libvirt network for the Axis Studio VM (axis-vm.sh).
     No <forward> element: guest traffic reaches only the Linux host.
     Never reuse $TRANSCEIVER_SUBNET here; the transceiver RNDIS link
     inside the guest owns that subnet. -->
<network>
  <name>$NET_NAME</name>
  <bridge name='$NET_BRIDGE' stp='on' delay='0'/>
  <ip address='$HOST_IP' netmask='255.255.255.0'>
    <dhcp>
      <range start='$GUEST_IP' end='$GUEST_IP'/>
      <host mac='$GUEST_MAC' ip='$GUEST_IP'/>
    </dhcp>
  </ip>
</network>
EOF
}

render_hostdev_xml() {
    # $1 output file, $2 vendor hex (no 0x), $3 product hex (no 0x).
    cat > "$1" <<EOF
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source startupPolicy='optional'>
    <vendor id='0x$2'/>
    <product id='0x$3'/>
  </source>
</hostdev>
EOF
}

catalog_url_for_major() {
    # $1 catalog JSON file, $2 major version (2 or 3). Prints the first
    # plausible installer URL; exits nonzero when nothing matches.
    python3 - "$1" "$2" <<'CATALOG_PY'
import json
import sys
from pathlib import Path


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


try:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, ValueError):
    raise SystemExit(1)
major = sys.argv[2]
for record in walk(data):
    url = record.get("softUrl")
    if not isinstance(url, str) or not url.startswith("https://"):
        continue
    version = str(record.get("version") or "")
    name = str(record.get("name") or "")
    if version.startswith(major + ".") or version == major or major in name.split():
        print(url)
        raise SystemExit(0)
raise SystemExit(1)
CATALOG_PY
}

# ---------------------------------------------------------------------------
# Host checks shared by --preflight, --define-network, and --create.
# ---------------------------------------------------------------------------
# User the qemu processes run as under qemu:///system (Debian/Ubuntu name;
# hosts running qemu as a different user apply the equivalent for it).
QEMU_USER=libvirt-qemu

overlapping_routes() {
    # $1: subnet in CIDR form. Prints every host route that overlaps it:
    # 'to match' lists covering routes (supernets such as 192.168.0.0/16),
    # 'to root' lists routes contained in the subnet. The default route
    # matches every prefix and is ignored. Empty output means no overlap.
    {
        ip -4 route show to match "$1" 2>/dev/null
        ip -4 route show to root "$1" 2>/dev/null
    } | grep -v '^default' | sort -u
}

qemu_blocked_ancestors() {
    # Prints every existing ancestor of directory $1 (from / down to $1)
    # that the qemu user cannot traverse: the last octal digit of its mode
    # lacks the x bit and no user:$QEMU_USER ACL entry grants x. Ubuntu
    # 21.04 and later create home directories 0750, which blocks
    # qemu:///system from opening disks under $HOME.
    ANCESTORS=
    ANCESTOR=$1
    while [ -n "$ANCESTOR" ] && [ "$ANCESTOR" != / ]; do
        ANCESTORS="$ANCESTOR
$ANCESTORS"
        ANCESTOR=$(dirname "$ANCESTOR")
    done
    ANCESTORS="/
$ANCESTORS"
    printf '%s' "$ANCESTORS" | while IFS= read -r ANCESTOR_DIR; do
        [ -d "$ANCESTOR_DIR" ] || continue
        DIR_MODE=$(stat -c %a "$ANCESTOR_DIR" 2>/dev/null) || continue
        case "$DIR_MODE" in
            *[1357]) continue ;;
        esac
        if command -v getfacl >/dev/null 2>&1 \
            && getfacl -p "$ANCESTOR_DIR" 2>/dev/null \
                | grep -q "^user:$QEMU_USER:..x"; then
            continue
        fi
        printf '%s\n' "$ANCESTOR_DIR"
    done
}

ensure_qemu_disk_traversal() {
    # $1: disk directory. qemu (running as $QEMU_USER) must be able to
    # traverse every ancestor, or virt-install/'virsh start' fails with
    # 'Cannot access storage file ... Permission denied'. Print the fix;
    # apply it via sudo under --yes; otherwise die before creating state.
    BLOCKED_DIRS=$(qemu_blocked_ancestors "$1")
    [ -n "$BLOCKED_DIRS" ] || return 0
    printf 'The libvirt qemu user (%s) cannot traverse these parents of\n' "$QEMU_USER"
    printf 'the disk directory %s:\n' "$1"
    # Split the newline-separated list once, then restore IFS so run_priv
    # (which joins "$*" with the first IFS character) prints one line.
    OLD_IFS=$IFS
    IFS='
'
    set -f
    # shellcheck disable=SC2086
    set -- $BLOCKED_DIRS
    set +f
    IFS=$OLD_IFS
    for BLOCKED_DIR in "$@"; do
        printf '  setfacl -m u:%s:x %s\n' "$QEMU_USER" "$BLOCKED_DIR"
    done
    if [ "$ASSUME_YES" -eq 1 ]; then
        for BLOCKED_DIR in "$@"; do
            run_priv setfacl -m "u:$QEMU_USER:x" "$BLOCKED_DIR"
        done
        return 0
    fi
    die "grant traversal with the setfacl commands above (hosts whose qemu runs as a different user grant that user instead), or rerun with --yes to apply them via sudo"
}

# ---------------------------------------------------------------------------
# Preflight helpers.
# ---------------------------------------------------------------------------
check_ok() {
    printf 'ok:      %s\n' "$*"
}

check_warn() {
    printf 'warning: %s\n' "$*"
    WARNINGS=$((WARNINGS + 1))
}

check_blocker() {
    printf 'BLOCKER: %s\n' "$*"
    BLOCKERS=$((BLOCKERS + 1))
}

# ---------------------------------------------------------------------------
# Operations.
# ---------------------------------------------------------------------------
op_preflight() {
    printf 'Preflight for the %s VM (read-only; nothing is changed).\n\n' "$VM_NAME"

    if grep -Eq '(vmx|svm)' /proc/cpuinfo 2>/dev/null; then
        check_ok "CPU exposes hardware virtualization (vmx/svm)"
    else
        check_blocker "no vmx/svm flag in /proc/cpuinfo; enable VT-x/AMD-V in firmware"
    fi

    if [ -e /dev/kvm ]; then
        if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
            check_ok "/dev/kvm is accessible"
        else
            check_warn "/dev/kvm exists but $(id -un) cannot open it (join group kvm, then log out and back in)"
        fi
    else
        check_blocker "/dev/kvm is missing (kernel KVM unavailable; install qemu/kvm and reboot)"
    fi

    RAM_KIB=$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo 2>/dev/null || printf '0')
    RAM_MIB_TOTAL=$((${RAM_KIB:-0} / 1024))
    if [ "$RAM_MIB_TOTAL" -ge 16384 ]; then
        check_ok "host RAM ${RAM_MIB_TOTAL} MiB"
    elif [ "$RAM_MIB_TOTAL" -ge 8192 ]; then
        check_warn "host RAM ${RAM_MIB_TOTAL} MiB meets the 8 GiB minimum; 16 GiB is recommended (guest default is ${RAM_MIB_DEFAULT} MiB)"
    else
        check_blocker "host RAM ${RAM_MIB_TOTAL} MiB is below the 8 GiB minimum"
    fi

    DF_TARGET=$STATE_DIR
    while [ ! -d "$DF_TARGET" ]; do
        DF_TARGET=$(dirname "$DF_TARGET")
    done
    AVAIL_KIB=$(df -Pk "$DF_TARGET" 2>/dev/null | awk 'NR == 2 { print $4 }')
    AVAIL_GIB=$((${AVAIL_KIB:-0} / 1048576))
    if [ "$AVAIL_GIB" -ge 120 ]; then
        check_ok "free disk at $DF_TARGET: ${AVAIL_GIB} GiB"
    else
        check_warn "free disk at $DF_TARGET is ${AVAIL_GIB} GiB (< 120 GiB); the ${DISK_GIB_DEFAULT} GiB qcow2 grows on demand and will eventually not fit"
    fi

    if grep -qi microsoft /proc/version 2>/dev/null; then
        check_warn "WSL detected: prefer running Axis Studio natively on the Windows host instead of nesting a VM"
        cat <<EOF
         To stream UDP from the Windows host into WSL2 either set
         networkingMode=mirrored in .wslconfig (then 127.0.0.1 works) or
         send to the WSL2 eth0 address ('wsl hostname -I'); NAT-mode
         localhostForwarding is TCP-only and silently drops UDP.
         Nested VM creation still works here (/dev/kvm) for developing this
         tooling, but the USB (usbipd chaining) and GPU legs cannot be
         validated under WSL2.
EOF
    fi

    MISSING=
    for tool in virsh virt-install qemu-system-x86_64 swtpm genisoimage virt-viewer curl lsusb; do
        command -v "$tool" >/dev/null 2>&1 || MISSING="$MISSING $tool"
    done
    if [ -n "$MISSING" ]; then
        check_warn "missing tools:$MISSING (run --install-deps)"
    else
        check_ok "required tools are installed"
    fi

    GROUP_LIST=" $(id -nG 2>/dev/null) "
    for group_name in kvm libvirt; do
        case "$GROUP_LIST" in
            *" $group_name "*) check_ok "member of group $group_name" ;;
            *) check_warn "not in group $group_name (run --install-deps, then log out and back in)" ;;
        esac
    done

    QEMU_BLOCKED=$(qemu_blocked_ancestors "$STATE_DIR/disks" | tr '\n' ' ')
    QEMU_BLOCKED=${QEMU_BLOCKED% }
    if [ -z "$QEMU_BLOCKED" ]; then
        check_ok "the qemu user ($QEMU_USER) can traverse the disk directory path"
    else
        check_warn "the qemu user ($QEMU_USER) cannot traverse: $QEMU_BLOCKED (Ubuntu >= 21.04 home directories are 0750, so 'virsh start' fails with EACCES); --create prints the 'setfacl -m u:$QEMU_USER:x' fix and applies it with --yes"
    fi

    ROUTES=$(ip -4 route 2>/dev/null || true)
    OVERLAP=$(overlapping_routes "$NET_SUBNET")
    if [ -z "$OVERLAP" ]; then
        check_ok "no existing route overlaps $NET_SUBNET"
    elif printf '%s\n' "$OVERLAP" | grep -qv "$NET_BRIDGE"; then
        check_warn "an existing route overlaps $NET_SUBNET; --define-network will refuse until it is gone"
    else
        check_ok "$NET_SUBNET is routed via $NET_BRIDGE ($NET_NAME already defined)"
    fi
    if printf '%s\n' "$ROUTES" | grep -q '192\.168\.1\.'; then
        check_warn "$TRANSCEIVER_SUBNET is routed on this host; that is fine (the transceiver RNDIS link lives inside the guest) but never reuse it for VM networking"
    fi

    printf '\n%s blocker(s), %s warning(s).\n' "$BLOCKERS" "$WARNINGS"
    [ "$BLOCKERS" -eq 0 ] || exit 1
}

op_install_deps() {
    command -v apt-get >/dev/null 2>&1 \
        || die "automatic dependency installation supports apt-based hosts only; install qemu-system-x86, libvirt, virtinst, ovmf, swtpm, virt-viewer, genisoimage, curl, and usbutils with your package manager"
    run_priv apt-get update
    run_priv apt-get install -y qemu-system-x86 libvirt-daemon-system \
        libvirt-clients virtinst ovmf swtpm swtpm-tools virt-viewer \
        genisoimage curl usbutils
    run_priv usermod -aG kvm,libvirt "$(id -un)"
    printf '%s\n' 'Group membership (kvm, libvirt) takes effect after logging out and back in.'
}

op_define_network() {
    ensure_state_dir
    if command -v virsh >/dev/null 2>&1 \
        && virsh net-info "$NET_NAME" >/dev/null 2>&1; then
        if virsh net-info "$NET_NAME" 2>/dev/null | grep -q '^Active:.*yes'; then
            printf 'libvirt network "%s" already exists and is active; nothing to do.\n' "$NET_NAME"
            return 0
        fi
        printf 'libvirt network "%s" exists but is inactive.\n' "$NET_NAME"
        run_priv virsh net-start "$NET_NAME"
        return 0
    fi
    if [ -n "$(overlapping_routes "$NET_SUBNET")" ]; then
        die "an existing route overlaps $NET_SUBNET; refusing to define network $NET_NAME"
    fi
    NET_XML=$STATE_DIR/$NET_NAME-network.xml
    render_network_xml "$NET_XML"
    printf 'Wrote network definition: %s\n' "$NET_XML"
    run_priv virsh net-define "$NET_XML"
    run_priv virsh net-autostart "$NET_NAME"
    run_priv virsh net-start "$NET_NAME"
}

op_download_axis() {
    require_cmd curl
    require_cmd python3 'install python3'
    ensure_state_dir
    PAYLOAD_DIR=$STATE_DIR/payload
    mkdir -p "$PAYLOAD_DIR"
    case "$OP_ARG" in
        2) PINNED_URL=$AXIS2_URL ;;
        3) PINNED_URL=$AXIS3_URL ;;
    esac
    make_temp_dir
    CATALOG_JSON=$TEMP_DIR/catalog.json
    DOWNLOAD_URL=
    if curl -fsSL --retry 2 -o "$CATALOG_JSON" "$AXIS_CATALOG_URL" 2>/dev/null; then
        DOWNLOAD_URL=$(catalog_url_for_major "$CATALOG_JSON" "$OP_ARG" 2>/dev/null || true)
    fi
    if [ -n "$DOWNLOAD_URL" ]; then
        printf 'Catalog API resolved Axis Studio %s to: %s\n' "$OP_ARG" "$DOWNLOAD_URL"
    else
        DOWNLOAD_URL=$PINNED_URL
        printf 'Catalog API unavailable or unparsable; using the pinned URL: %s\n' "$DOWNLOAD_URL"
    fi
    FILE_NAME=${DOWNLOAD_URL##*/}
    case "$FILE_NAME" in
        ''|*[!A-Za-z0-9._-]*)
            warn "catalog file name looks unsafe; falling back to the pinned URL"
            DOWNLOAD_URL=$PINNED_URL
            FILE_NAME=${DOWNLOAD_URL##*/}
            ;;
    esac
    DEST=$PAYLOAD_DIR/$FILE_NAME
    PIN_FILE=$STATE_DIR/$FILE_NAME.sha256
    if [ -f "$DEST" ] && [ -f "$PIN_FILE" ] \
        && [ "$(file_sha256 "$DEST")" = "$(awk 'NR == 1 { print $1 }' "$PIN_FILE")" ]; then
        printf 'Already downloaded and verified: %s\n' "$DEST"
        return 0
    fi
    printf 'Downloading to %s (resumable)...\n' "$DEST"
    # Never hash or pin unless curl exited 0: hashing a truncated file
    # would pin the wrong SHA-256 forever. Modern curl exits 0 when a
    # resume finds the file already complete (HTTP 416), so a nonzero
    # exit always means a failed or incomplete transfer.
    curl -fL --retry 3 --continue-at - -o "$DEST" "$DOWNLOAD_URL" \
        || die "download failed or incomplete: $DOWNLOAD_URL (partial file kept at $DEST for resume; rerun to continue)"
    ACTUAL_HASH=$(file_sha256 "$DEST")
    if [ -f "$PIN_FILE" ]; then
        EXPECTED_HASH=$(awk 'NR == 1 { print $1 }' "$PIN_FILE")
        [ "$ACTUAL_HASH" = "$EXPECTED_HASH" ] || die "SHA-256 mismatch for $FILE_NAME: expected $EXPECTED_HASH (pinned at $PIN_FILE), got $ACTUAL_HASH; delete both files to re-download"
        printf 'SHA-256 verified against the pin at %s\n' "$PIN_FILE"
    else
        printf '%s  %s\n' "$ACTUAL_HASH" "$FILE_NAME" > "$PIN_FILE"
        printf 'Noitom publishes no checksums; recorded a first-download SHA-256 pin at %s\n' "$PIN_FILE"
    fi
    printf 'Installer ready: %s\n' "$DEST"
}

op_build_payload_iso() {
    require_cmd genisoimage
    [ -f "$SCRIPT_DIR/guest-setup.ps1" ] \
        || die "guest-setup.ps1 is missing next to axis-vm.sh"
    PAYLOAD_DIR=$STATE_DIR/payload
    [ -d "$PAYLOAD_DIR" ] \
        || die "payload directory is missing: $PAYLOAD_DIR (run --download-axis 2|3 first)"
    FOUND_INSTALLER=0
    for candidate in "$PAYLOAD_DIR"/*; do
        [ -f "$candidate" ] || continue
        case "${candidate##*/}" in
            [Aa]xis*.msi|[Aa]xis*.zip) FOUND_INSTALLER=1 ;;
        esac
    done
    [ "$FOUND_INSTALLER" -eq 1 ] \
        || die "no Axis Studio installer (Axis*.msi or Axis*.zip) in $PAYLOAD_DIR; run --download-axis first"
    cp "$SCRIPT_DIR/guest-setup.ps1" "$PAYLOAD_DIR/guest-setup.ps1"
    ISO_PATH=$STATE_DIR/axis-payload.iso
    rm -f "$ISO_PATH"
    genisoimage -quiet -J -r -joliet-long -V AXIS_PAYLOAD -o "$ISO_PATH" "$PAYLOAD_DIR"
    printf 'Built payload ISO: %s\n' "$ISO_PATH"
    if command -v virsh >/dev/null 2>&1 \
        && virsh dominfo "$VM_NAME" >/dev/null 2>&1; then
        # Locate the reserved payload CD drive: prefer the target already
        # sourcing the payload ISO, else the first empty SATA cdrom (the
        # windows/unattend/virtio ISOs all carry a non-empty source, so
        # they never match the '-' test).
        PAYLOAD_TARGET=$(virsh domblklist "$VM_NAME" --details 2>/dev/null \
            | awk -v iso="$ISO_PATH" '
                $2 != "cdrom" { next }
                $4 == iso { print $3; exit }
                empty == "" && $3 ~ /^sd/ && $4 == "-" { empty = $3 }
                END { if (empty != "") print empty }')
        [ -n "$PAYLOAD_TARGET" ] \
            || die "no payload CD drive found on $VM_NAME (expected the empty SATA cdrom reserved by --create); recreate the domain with --create"
        if [ "$(virsh domstate "$VM_NAME" 2>/dev/null)" = running ]; then
            printf 'Inserting the payload ISO into %s (%s); the running guest sees\nthe disc immediately...\n' "$VM_NAME" "$PAYLOAD_TARGET"
            virsh change-media "$VM_NAME" "$PAYLOAD_TARGET" "$ISO_PATH" \
                --update --config --live \
                || warn "could not insert the media; run: virsh change-media $VM_NAME $PAYLOAD_TARGET $ISO_PATH --update --config --live"
        else
            printf 'Inserting the payload ISO into %s (%s) for the next boot...\n' "$VM_NAME" "$PAYLOAD_TARGET"
            virsh change-media "$VM_NAME" "$PAYLOAD_TARGET" "$ISO_PATH" \
                --update --config \
                || warn "could not insert the media; run: virsh change-media $VM_NAME $PAYLOAD_TARGET $ISO_PATH --update --config"
        fi
    else
        printf 'Domain %s does not exist yet; --create will attach the payload ISO.\n' "$VM_NAME"
    fi
}

op_create() {
    # Validate all inputs before touching libvirt.
    [ -f "$WINDOWS_ISO" ] || die "Windows ISO does not exist: $WINDOWS_ISO"
    [ -z "$VIRTIO_ISO" ] || [ -f "$VIRTIO_ISO" ] \
        || die "virtio-win ISO does not exist: $VIRTIO_ISO"
    [ -f "$SCRIPT_DIR/autounattend.xml" ] \
        || die "autounattend.xml template is missing next to axis-vm.sh"
    require_cmd python3 'install python3'
    require_cmd genisoimage
    require_cmd virt-install
    require_cmd virsh
    ensure_state_dir

    if [ -n "$ISO_SHA256" ]; then
        printf 'Verifying the Windows ISO SHA-256 (this can take a minute)...\n'
        ACTUAL_HASH=$(file_sha256 "$WINDOWS_ISO" | tr 'ABCDEF' 'abcdef')
        EXPECTED_HASH=$(printf '%s' "$ISO_SHA256" | tr 'ABCDEF' 'abcdef')
        [ "$ACTUAL_HASH" = "$EXPECTED_HASH" ] \
            || die "Windows ISO SHA-256 mismatch: expected $EXPECTED_HASH, got $ACTUAL_HASH"
        printf 'Windows ISO SHA-256 verified.\n'
    fi

    # Both networks must be ACTIVE before any state is created;
    # virt-install fails mid-way otherwise.
    virsh net-info "$NET_NAME" 2>/dev/null | grep -q '^Active:.*yes' \
        || die "libvirt network $NET_NAME is missing or inactive; run --define-network --yes first"
    virsh net-info default 2>/dev/null | grep -q '^Active:.*yes' \
        || die "libvirt network 'default' is missing or inactive (the NAT NIC needs it); run: virsh net-start default; virsh net-autostart default"
    if virsh dominfo "$VM_NAME" >/dev/null 2>&1; then
        die "domain $VM_NAME already exists (remove it with: virsh undefine $VM_NAME --nvram)"
    fi
    DISK_DIR=$STATE_DIR/disks
    mkdir -p "$DISK_DIR"
    DISK_PATH=$DISK_DIR/$VM_NAME.qcow2
    [ ! -e "$DISK_PATH" ] || die "disk image already exists: $DISK_PATH"
    ensure_qemu_disk_traversal "$DISK_DIR"

    GUEST_PASSWORD=$(python3 -c 'import secrets; print("Ax-" + secrets.token_urlsafe(15))')
    case "$GUEST_PASSWORD" in
        ''|*[!A-Za-z0-9_-]*) die "generated password contains unexpected characters" ;;
    esac
    ( umask 077; printf '%s\n' "$GUEST_PASSWORD" > "$STATE_DIR/guest-password" )
    chmod 600 "$STATE_DIR/guest-password"

    make_temp_dir
    UNATTEND_BUILD_DIR=$TEMP_DIR/unattend
    mkdir -p "$UNATTEND_BUILD_DIR"
    sed "s|@AXIS_VM_PASSWORD@|$GUEST_PASSWORD|" "$SCRIPT_DIR/autounattend.xml" \
        > "$UNATTEND_BUILD_DIR/autounattend.xml"
    chmod 600 "$UNATTEND_BUILD_DIR/autounattend.xml"
    UNATTEND_ISO=$STATE_DIR/unattend.iso
    rm -f "$UNATTEND_ISO"
    genisoimage -quiet -J -r -V UNATTEND -o "$UNATTEND_ISO" "$UNATTEND_BUILD_DIR"
    chmod 600 "$UNATTEND_ISO"
    printf 'Built unattend ISO (contains the generated password): %s\n' "$UNATTEND_ISO"

    PAYLOAD_ISO=$STATE_DIR/axis-payload.iso

    set -- virt-install \
        --connect qemu:///system \
        --name "$VM_NAME" \
        --osinfo win11 \
        --boot uefi \
        --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb \
        --memory "$RAM_MIB" \
        --vcpus "$VCPUS" \
        --controller usb,model=qemu-xhci \
        --graphics spice,listen=127.0.0.1 \
        --video qxl \
        --disk "path=$DISK_PATH,size=$DISK_GIB,format=qcow2,bus=sata" \
        --network "network=$NET_NAME,mac=$GUEST_MAC,model=e1000e" \
        --network network=default,model=e1000e \
        --disk "path=$WINDOWS_ISO,device=cdrom,bus=sata" \
        --disk "path=$UNATTEND_ISO,device=cdrom,bus=sata" \
        --noautoconsole
    # Always reserve a payload CD drive so --build-payload-iso can insert
    # or refresh the ISO later with virsh change-media (no hot attach).
    if [ -f "$PAYLOAD_ISO" ]; then
        set -- "$@" --disk "path=$PAYLOAD_ISO,device=cdrom,bus=sata"
    else
        set -- "$@" --disk device=cdrom,bus=sata
    fi
    if [ -n "$VIRTIO_ISO" ]; then
        set -- "$@" --disk "path=$VIRTIO_ISO,device=cdrom,bus=sata"
    fi

    cat <<EOF
The firmware shows 'Press any key to boot from CD or DVD...' for only a
few seconds once the domain starts. Open the console in ANOTHER terminal
NOW and press a key in it when that prompt appears:
  ./axis-vm.sh --console --name $VM_NAME
EOF
    printf 'Running:\n  %s\n' "$*"
    "$@" || die "virt-install failed; clean up the partial domain before retrying: virsh undefine $VM_NAME --nvram; rm -f $DISK_PATH"

    cat <<EOF

Created domain $VM_NAME; unattended Windows Setup is running.

Next steps:
  1. If the console shows 'Shell>' (the UEFI shell), the boot prompt was
     missed: run 'virsh -c qemu:///system reset $VM_NAME' (or exit the
     UEFI shell) and press a key promptly this time.
  2. Setup takes roughly 15-45 minutes and reboots several times. The
     local admin account is '$GUEST_ACCOUNT'. Its generated password is
     stored (mode 0600) at:
       $STATE_DIR/guest-password
     Password (printed only this once): $GUEST_PASSWORD
  3. After the first login: run --download-axis 2|3 and
     --build-payload-iso, then run guest-setup.ps1 from the payload CD
     inside the guest.
  4. Attach the transceiver: --probe-usb, then --attach-usb VID:PID and
     optionally --install-hotplug VID:PID --yes.
  5. Open the host firewall: --firewall (add --yes to apply).

EOF
    print_bvh_contract
}

op_probe_usb() {
    require_cmd lsusb 'install usbutils'
    make_temp_dir
    BEFORE_FILE=$TEMP_DIR/lsusb-before
    lsusb > "$BEFORE_FILE"
    printf 'Captured the current USB device list (%s devices).\n' "$(wc -l < "$BEFORE_FILE")"
    printf 'Plug in the Noitom transceiver now (unplug it first if it is already\n'
    printf 'connected). Watching for a new USB device for 60 seconds...\n'
    SECONDS_LEFT=60
    while [ "$SECONDS_LEFT" -gt 0 ]; do
        sleep 1
        SECONDS_LEFT=$((SECONDS_LEFT - 1))
        NEW_LINES=$(lsusb | grep -Fxv -f "$BEFORE_FILE" || true)
        [ -n "$NEW_LINES" ] || continue
        printf '\nNew USB device(s):\n%s\n' "$NEW_LINES"
        VID_PID=$(printf '%s\n' "$NEW_LINES" \
            | sed -n 's/.*ID \([0-9a-fA-F]\{4\}:[0-9a-fA-F]\{4\}\).*/\1/p' \
            | sed -n '1p')
        if [ -n "$VID_PID" ]; then
            printf '\nTransceiver VID:PID: %s\n' "$VID_PID"
            printf 'Record this value; it is not published anywhere. Next:\n'
            printf '  ./axis-vm.sh --attach-usb %s\n' "$VID_PID"
            printf '  ./axis-vm.sh --install-hotplug %s --yes\n' "$VID_PID"
        fi
        return 0
    done
    die "no new USB device appeared within 60 seconds"
}

op_attach_usb() {
    require_cmd virsh
    make_temp_dir
    HOSTDEV_XML=$TEMP_DIR/hostdev.xml
    render_hostdev_xml "$HOSTDEV_XML" "$USB_VENDOR" "$USB_PRODUCT"
    printf 'Attaching USB %s:%s to %s (persistent)...\n' "$USB_VENDOR" "$USB_PRODUCT" "$VM_NAME"
    virsh attach-device "$VM_NAME" "$HOSTDEV_XML" --persistent
    printf 'Done. The guest owns the device exclusively while it is attached.\n'
}

op_detach_usb() {
    require_cmd virsh
    make_temp_dir
    HOSTDEV_XML=$TEMP_DIR/hostdev.xml
    render_hostdev_xml "$HOSTDEV_XML" "$USB_VENDOR" "$USB_PRODUCT"
    printf 'Detaching USB %s:%s from %s (persistent)...\n' "$USB_VENDOR" "$USB_PRODUCT" "$VM_NAME"
    virsh detach-device "$VM_NAME" "$HOSTDEV_XML" --persistent
    printf 'Done.\n'
}

op_install_hotplug() {
    ensure_state_dir
    HELPER_STAGE=$STATE_DIR/axis-vm-usb-hotplug.sh
    RULES_STAGE=$STATE_DIR/70-axis-vm-usb.rules
    cat > "$HELPER_STAGE" <<'HOTPLUG_HELPER'
#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
# Generated by axis-vm.sh --install-hotplug. udev RUN handlers must return
# immediately, so this wrapper hands the libvirt attach/detach to a
# transient systemd unit (systemd-run --no-block) instead of blocking udev.
set -eu
OPERATION=$1
DOMAIN=$2
VENDOR=$3
PRODUCT=$4
case "$OPERATION" in
    add) VERB=attach-device ;;
    remove) VERB=detach-device ;;
    *) exit 2 ;;
esac
XML_DIR=/run/axis-vm
mkdir -p "$XML_DIR"
XML_FILE=$XML_DIR/usb-$VENDOR-$PRODUCT.xml
cat > "$XML_FILE" <<HOSTDEV_XML
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source startupPolicy='optional'>
    <vendor id='0x$VENDOR'/>
    <product id='0x$PRODUCT'/>
  </source>
</hostdev>
HOSTDEV_XML
exec systemd-run --no-block --collect \
    /usr/bin/virsh --connect qemu:///system "$VERB" "$DOMAIN" "$XML_FILE" --live
HOTPLUG_HELPER
    chmod 755 "$HELPER_STAGE"
    cat > "$RULES_STAGE" <<EOF
# Generated by axis-vm.sh --install-hotplug $USB_VENDOR:$USB_PRODUCT
# Re-attach the Axis transceiver to the '$VM_NAME' libvirt domain when it
# is plugged in; drop the guest hostdev again when it is removed.
ACTION=="add", SUBSYSTEM=="usb", ENV{ID_VENDOR_ID}=="$USB_VENDOR", ENV{ID_MODEL_ID}=="$USB_PRODUCT", RUN+="$HOTPLUG_HELPER_PATH add $VM_NAME $USB_VENDOR $USB_PRODUCT"
ACTION=="remove", SUBSYSTEM=="usb", ENV{ID_VENDOR_ID}=="$USB_VENDOR", ENV{ID_MODEL_ID}=="$USB_PRODUCT", RUN+="$HOTPLUG_HELPER_PATH remove $VM_NAME $USB_VENDOR $USB_PRODUCT"
EOF
    printf 'Staged hotplug files:\n  %s\n  %s\n' "$HELPER_STAGE" "$RULES_STAGE"
    run_priv install -D -m 0755 "$HELPER_STAGE" "$HOTPLUG_HELPER_PATH"
    run_priv install -D -m 0644 "$RULES_STAGE" "$UDEV_RULES_PATH"
    run_priv udevadm control --reload
}

op_firewall() {
    if command -v ufw >/dev/null 2>&1; then
        run_priv ufw allow in on "$NET_BRIDGE" to any port "$BVH_PORT" proto udp
    elif command -v firewall-cmd >/dev/null 2>&1; then
        ZONE=$(firewall-cmd --get-zone-of-interface="$NET_BRIDGE" 2>/dev/null || true)
        if [ -z "$ZONE" ]; then
            ZONE=libvirt
            warn "could not determine the firewalld zone of $NET_BRIDGE; using the '$ZONE' zone (libvirt's default for its bridges)"
        fi
        run_priv firewall-cmd --zone="$ZONE" --add-port="$BVH_PORT/udp" --permanent
        run_priv firewall-cmd --reload
    else
        printf 'Neither ufw nor firewalld was detected. If this host filters inbound\n'
        printf 'traffic, allow UDP %s in on interface %s only, e.g.:\n' "$BVH_PORT" "$NET_BRIDGE"
        printf '  sudo iptables -I INPUT -i %s -p udp --dport %s -j ACCEPT\n' "$NET_BRIDGE" "$BVH_PORT"
        printf 'With no host firewall, no action is needed.\n'
    fi
}

op_start() {
    require_cmd virsh
    virsh start "$VM_NAME"
}

op_shutdown() {
    require_cmd virsh
    virsh shutdown "$VM_NAME"
}

op_destroy() {
    require_cmd virsh
    if [ "$ASSUME_YES" -ne 1 ]; then
        [ -r /dev/tty ] || die "destroy needs confirmation; rerun with --yes in a non-interactive shell"
        printf 'Force domain "%s" off (like pulling the power)? [y/N] ' "$VM_NAME" >/dev/tty
        IFS= read -r REPLY </dev/tty || die "could not read confirmation"
        case "$REPLY" in
            y|Y|yes|YES) ;;
            *) printf '%s\n' 'Cancelled.'; exit 0 ;;
        esac
    fi
    virsh destroy "$VM_NAME"
}

op_autostart() {
    require_cmd virsh
    if [ "$OP_ARG" = on ]; then
        virsh autostart "$VM_NAME"
    else
        virsh autostart "$VM_NAME" --disable
    fi
}

op_console() {
    require_cmd virt-viewer
    exec virt-viewer -c qemu:///system "$VM_NAME"
}

op_rdp() {
    require_cmd virsh
    GUEST_ADDR=$(virsh -q domifaddr "$VM_NAME" --source lease 2>/dev/null \
        | awk '$4 ~ /^192\.168\.77\./ { sub(/\/.*/, "", $4); print $4; exit }')
    [ -n "$GUEST_ADDR" ] || GUEST_ADDR=$GUEST_IP
    printf "Guest '%s' password is stored at: %s\n" "$GUEST_ACCOUNT" "$STATE_DIR/guest-password"
    RDP_CLIENT=
    for candidate in xfreerdp wlfreerdp; do
        if command -v "$candidate" >/dev/null 2>&1; then
            RDP_CLIENT=$candidate
            break
        fi
    done
    if [ -n "$RDP_CLIENT" ]; then
        printf 'Connecting: %s /v:%s /u:%s\n' "$RDP_CLIENT" "$GUEST_ADDR" "$GUEST_ACCOUNT"
        exec "$RDP_CLIENT" "/v:$GUEST_ADDR" "/u:$GUEST_ACCOUNT" /dynamic-resolution +clipboard
    fi
    printf 'No FreeRDP client (xfreerdp/wlfreerdp) was found. Install one and run:\n'
    printf '  xfreerdp /v:%s /u:%s /dynamic-resolution +clipboard\n' "$GUEST_ADDR" "$GUEST_ACCOUNT"
}

op_status() {
    require_cmd virsh
    printf '== Domain %s ==\n' "$VM_NAME"
    virsh domstate "$VM_NAME" 2>&1 || true
    printf '\n== Network %s ==\n' "$NET_NAME"
    virsh net-info "$NET_NAME" 2>&1 || true
    printf '\n== Guest addresses (DHCP leases) ==\n'
    virsh domifaddr "$VM_NAME" --source lease 2>&1 || true
    printf '\n== Attached USB host devices ==\n'
    USB_DEVICES=$(virsh dumpxml "$VM_NAME" 2>/dev/null \
        | grep -E "<hostdev|<vendor id|<product id" || true)
    if [ -n "$USB_DEVICES" ]; then
        printf '%s\n' "$USB_DEVICES"
    else
        printf 'none\n'
    fi
    printf '\n== Host listeners on UDP %s ==\n' "$BVH_PORT"
    LISTENERS=$(ss -ulnp 2>/dev/null | grep ":$BVH_PORT " || true)
    [ -n "$LISTENERS" ] || LISTENERS=$(ss -uln 2>/dev/null | grep ":$BVH_PORT " || true)
    if [ -n "$LISTENERS" ]; then
        printf '%s\n' "$LISTENERS"
    else
        printf 'nothing is listening on UDP %s; start Mocap Studio (mode "bvh",\n' "$BVH_PORT"
        printf 'transport "udp", host %s, port %s, rotationOrder "%s").\n' \
            "$HOST_IP" "$BVH_PORT" "$ROTATION_ORDER"
    fi
}

op_detach_internet() {
    require_cmd virsh
    NAT_MAC=$(virsh domiflist "$VM_NAME" 2>/dev/null \
        | awk '$3 == "default" { print $5; exit }')
    [ -n "$NAT_MAC" ] \
        || die "no interface on the 'default' NAT network is attached to $VM_NAME"
    virsh detach-interface "$VM_NAME" network --mac "$NAT_MAC" --persistent
    printf 'Detached the NAT NIC (%s). Re-add it for activation/updates with --attach-internet.\n' "$NAT_MAC"
}

op_attach_internet() {
    require_cmd virsh
    EXISTING_MAC=$(virsh domiflist "$VM_NAME" 2>/dev/null \
        | awk '$3 == "default" { print $5; exit }')
    if [ -n "$EXISTING_MAC" ]; then
        printf 'A NAT NIC (%s) is already attached to %s; nothing to do.\n' "$EXISTING_MAC" "$VM_NAME"
        return 0
    fi
    virsh attach-interface "$VM_NAME" network default --model e1000e --persistent
    printf 'Attached a NAT NIC on the default network (activation/updates only).\n'
}

# ---------------------------------------------------------------------------
# Argument parsing.
# ---------------------------------------------------------------------------
while [ "$#" -gt 0 ]; do
    case "$1" in
        --preflight) select_operation preflight; shift ;;
        --install-deps) select_operation install-deps; shift ;;
        --define-network) select_operation define-network; shift ;;
        --download-axis)
            need_value "$@"; select_operation download-axis; OP_ARG=$2; shift 2 ;;
        --download-axis=*) select_operation download-axis; OP_ARG=${1#*=}; shift ;;
        --build-payload-iso) select_operation build-payload-iso; shift ;;
        --create) select_operation create; shift ;;
        --probe-usb) select_operation probe-usb; shift ;;
        --attach-usb)
            need_value "$@"; select_operation attach-usb; OP_ARG=$2; shift 2 ;;
        --attach-usb=*) select_operation attach-usb; OP_ARG=${1#*=}; shift ;;
        --detach-usb)
            need_value "$@"; select_operation detach-usb; OP_ARG=$2; shift 2 ;;
        --detach-usb=*) select_operation detach-usb; OP_ARG=${1#*=}; shift ;;
        --install-hotplug)
            need_value "$@"; select_operation install-hotplug; OP_ARG=$2; shift 2 ;;
        --install-hotplug=*) select_operation install-hotplug; OP_ARG=${1#*=}; shift ;;
        --firewall) select_operation firewall; shift ;;
        --start) select_operation start; shift ;;
        --shutdown) select_operation shutdown; shift ;;
        --destroy) select_operation destroy; shift ;;
        --autostart)
            need_value "$@"; select_operation autostart; OP_ARG=$2; shift 2 ;;
        --autostart=*) select_operation autostart; OP_ARG=${1#*=}; shift ;;
        --console) select_operation console; shift ;;
        --rdp) select_operation rdp; shift ;;
        --status) select_operation status; shift ;;
        --detach-internet) select_operation detach-internet; shift ;;
        --attach-internet) select_operation attach-internet; shift ;;
        --windows-iso)
            need_value "$@"; WINDOWS_ISO=$2; shift 2 ;;
        --windows-iso=*) WINDOWS_ISO=${1#*=}; shift ;;
        --iso-sha256)
            need_value "$@"; ISO_SHA256=$2; shift 2 ;;
        --iso-sha256=*) ISO_SHA256=${1#*=}; shift ;;
        --virtio-iso)
            need_value "$@"; VIRTIO_ISO=$2; shift 2 ;;
        --virtio-iso=*) VIRTIO_ISO=${1#*=}; shift ;;
        --disk-gib)
            need_value "$@"; DISK_GIB=$2; shift 2 ;;
        --disk-gib=*) DISK_GIB=${1#*=}; shift ;;
        --ram-mib)
            need_value "$@"; RAM_MIB=$2; shift 2 ;;
        --ram-mib=*) RAM_MIB=${1#*=}; shift ;;
        --vcpus)
            need_value "$@"; VCPUS=$2; shift 2 ;;
        --vcpus=*) VCPUS=${1#*=}; shift ;;
        --name)
            need_value "$@"; VM_NAME=$2; shift 2 ;;
        --name=*) VM_NAME=${1#*=}; shift ;;
        --state-dir)
            need_value "$@"; STATE_DIR_OVERRIDE=$2; shift 2 ;;
        --state-dir=*) STATE_DIR_OVERRIDE=${1#*=}; shift ;;
        --yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage_error "unknown option: $1" ;;
    esac
done

# ---------------------------------------------------------------------------
# Post-parse validation (before anything privileged or any virsh call).
# ---------------------------------------------------------------------------
case "$VM_NAME" in
    ''|*[!A-Za-z0-9._-]*)
        usage_error "--name must use only letters, digits, '.', '_', and '-'" ;;
esac

if [ "$OPERATION" != create ]; then
    [ -z "$WINDOWS_ISO" ] || usage_error "--windows-iso is only accepted with --create"
    [ -z "$ISO_SHA256" ] || usage_error "--iso-sha256 is only accepted with --create"
    [ -z "$VIRTIO_ISO" ] || usage_error "--virtio-iso is only accepted with --create"
    [ "$DISK_GIB" = "$DISK_GIB_DEFAULT" ] || usage_error "--disk-gib is only accepted with --create"
    [ "$RAM_MIB" = "$RAM_MIB_DEFAULT" ] || usage_error "--ram-mib is only accepted with --create"
    [ "$VCPUS" = "$VCPUS_DEFAULT" ] || usage_error "--vcpus is only accepted with --create"
else
    [ -n "$WINDOWS_ISO" ] || usage_error "--create requires --windows-iso PATH"
    is_uint "$DISK_GIB" && [ "$DISK_GIB" -ge 1 ] \
        || usage_error "--disk-gib requires a positive integer (GiB)"
    is_uint "$RAM_MIB" && [ "$RAM_MIB" -ge 1 ] \
        || usage_error "--ram-mib requires a positive integer (MiB)"
    is_uint "$VCPUS" && [ "$VCPUS" -ge 1 ] \
        || usage_error "--vcpus requires a positive integer"
    if [ -n "$ISO_SHA256" ]; then
        printf '%s\n' "$ISO_SHA256" | grep -Eq '^[0-9a-fA-F]{64}$' \
            || usage_error "--iso-sha256 must be 64 hexadecimal characters"
    fi
fi

case "$OPERATION" in
    download-axis)
        case "$OP_ARG" in
            2|3) ;;
            *) usage_error "--download-axis requires 2 or 3 (the Axis Studio major version)" ;;
        esac
        ;;
    attach-usb|detach-usb|install-hotplug)
        is_vid_pid "$OP_ARG" \
            || usage_error "--$OPERATION requires a VID:PID argument (four hex digits, ':', four hex digits); measure it with --probe-usb"
        USB_VENDOR=$(printf '%s' "${OP_ARG%%:*}" | tr 'ABCDEF' 'abcdef')
        USB_PRODUCT=$(printf '%s' "${OP_ARG#*:}" | tr 'ABCDEF' 'abcdef')
        ;;
    autostart)
        case "$OP_ARG" in
            on|off) ;;
            *) usage_error "--autostart requires 'on' or 'off'" ;;
        esac
        ;;
esac

[ "$(id -u)" -ne 0 ] \
    || die "do not run axis-vm.sh as root; privileged commands are printed by default and run via sudo only with --yes"
[ -n "${HOME:-}" ] || die "HOME is not set"
umask 022

LIBVIRT_DEFAULT_URI=qemu:///system
export LIBVIRT_DEFAULT_URI

SCRIPT_DIR=$(CDPATH= cd -P "$(dirname "$0")" 2>/dev/null && pwd)
[ -n "$SCRIPT_DIR" ] || die "could not resolve the script directory"

if [ -n "$STATE_DIR_OVERRIDE" ]; then
    case "$STATE_DIR_OVERRIDE" in
        /*) STATE_DIR=$STATE_DIR_OVERRIDE ;;
        *) STATE_DIR=$(pwd)/$STATE_DIR_OVERRIDE ;;
    esac
else
    STATE_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/axis-vm
fi

# ---------------------------------------------------------------------------
# Dispatch.
# ---------------------------------------------------------------------------
case "$OPERATION" in
    preflight) op_preflight ;;
    install-deps) op_install_deps ;;
    define-network) op_define_network ;;
    download-axis) op_download_axis ;;
    build-payload-iso) op_build_payload_iso ;;
    create) op_create ;;
    probe-usb) op_probe_usb ;;
    attach-usb) op_attach_usb ;;
    detach-usb) op_detach_usb ;;
    install-hotplug) op_install_hotplug ;;
    firewall) op_firewall ;;
    start) op_start ;;
    shutdown) op_shutdown ;;
    destroy) op_destroy ;;
    autostart) op_autostart ;;
    console) op_console ;;
    rdp) op_rdp ;;
    status) op_status ;;
    detach-internet) op_detach_internet ;;
    attach-internet) op_attach_internet ;;
    *) die "internal error: unhandled operation $OPERATION" ;;
esac

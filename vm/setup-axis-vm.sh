#!/usr/bin/env bash
# Provision a local Windows VM ("attached VM") that runs Noitom Axis Studio.
#
# The VM owns the Perception Neuron USB/RNDIS transceiver exclusively (see
# attach-usb.sh); Axis Studio streams BVH/Calc frames over unicast UDP/TCP to
# this Linux host, where MocapApi / Mocap Studio / the gr00t bridge consume
# them. Architecture reference: studio/docs/USB_ON_LINUX.md, "Architecture B".
#
# Tested target: Ubuntu 22.04/24.04 x86_64 with KVM. Requires VT-x/AMD-V
# enabled in firmware. Cloud instances without nested virtualization
# (no /dev/kvm) cannot run the VM; use --dry-run there to inspect commands.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VM_NAME="axis-studio"
WINDOWS_ISO=""
WINDOWS_VERSION="win11"          # win10 | win11
MEMORY_MIB=8192
VCPUS=4
DISK_SIZE_GIB=120
IMAGES_DIR="/var/lib/libvirt/images"
VIRTIO_WIN_URL="https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
AXIS_INSTALLER=""
AXIS_MEDIA=1
AXIS_GUEST_ASSETS_DIR="${SCRIPT_DIR}/windows"
AXIS_MEDIA_STAGING=""
DRY_RUN=0
SKIP_PACKAGES=0
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/axis-vm"

usage() {
    cat <<EOF
Usage: $(basename "$0") --windows-iso PATH [options]

Creates the Axis Studio Windows VM used by the mocap station.

Required:
  --windows-iso PATH     Windows 10/11 installer ISO (user-provided; licensed)

Options:
  --name NAME            Libvirt domain name (default: ${VM_NAME})
  --windows-version VER  win10 or win11 (default: ${WINDOWS_VERSION})
  --memory MIB           Guest RAM in MiB (default: ${MEMORY_MIB})
  --vcpus N              Guest vCPUs (default: ${VCPUS})
  --disk-size GIB        Guest disk size in GiB, sparse (default: ${DISK_SIZE_GIB})
  --images-dir DIR       Where disk/ISOs live (default: ${IMAGES_DIR})
  --axis-installer PATH  Put a downloaded Axis Studio .exe/.msi/.zip on the guest
                         setup CD; without it, the CD opens Noitom's download page
  --skip-axis-media      Do not create or attach the guided AXIS_SETUP CD
  --skip-packages        Do not apt-get install host packages
  --dry-run              Print the commands without executing them
  -h, --help             Show this help

After Windows setup, open the AXIS_SETUP CD in the guest and run
START-AXIS-SETUP.cmd. It installs the supplied Axis Studio package (if any)
and walks the user through account/Product-ID activation and login without
collecting credentials. Continue with vm/README.md for USB passthrough and BVH.
EOF
}

log()  { printf '\033[1;34m[axis-vm]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[axis-vm]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[axis-vm]\033[0m %s\n' "$*" >&2; exit 1; }

run() {
    if [ "${DRY_RUN}" -eq 1 ]; then
        printf '\033[2m[dry-run]\033[0m %s\n' "$*"
    else
        "$@"
    fi
}

cleanup_axis_media_staging() {
    if [ -n "${AXIS_MEDIA_STAGING}" ] && [ -d "${AXIS_MEDIA_STAGING}" ]; then
        rm -rf -- "${AXIS_MEDIA_STAGING}"
    fi
}
trap cleanup_axis_media_staging EXIT

build_axis_setup_media() {
    local installer_name=""
    local media_payload=""

    if [ "${DRY_RUN}" -eq 1 ]; then
        printf '\033[2m[dry-run]\033[0m build guided Axis Studio setup CD at %s' "${AXIS_SETUP_ISO}"
        if [ -n "${AXIS_INSTALLER}" ]; then
            printf ' with installer %s' "${AXIS_INSTALLER}"
        fi
        printf '\n'
        return
    fi

    command -v xorriso >/dev/null || \
        die "xorriso not found (install it, or rerun without --skip-packages)"
    [ -r "${AXIS_GUEST_ASSETS_DIR}/START-AXIS-SETUP.cmd" ] || \
        die "Guest setup assets missing from ${AXIS_GUEST_ASSETS_DIR}"
    [ -r "${AXIS_GUEST_ASSETS_DIR}/README.txt" ] || \
        die "Guest setup assets missing from ${AXIS_GUEST_ASSETS_DIR}"

    AXIS_MEDIA_STAGING="$(mktemp -d -t axis-vm-media.XXXXXX)"
    media_payload="${AXIS_MEDIA_STAGING}/payload"
    mkdir "${media_payload}"
    cp -- "${AXIS_GUEST_ASSETS_DIR}/START-AXIS-SETUP.cmd" "${media_payload}/"
    cp -- "${AXIS_GUEST_ASSETS_DIR}/README.txt" "${media_payload}/"
    if [ -n "${AXIS_INSTALLER}" ]; then
        case "${AXIS_INSTALLER}" in
            *.[eE][xX][eE]) installer_name="AxisStudioSetup.exe" ;;
            *.[mM][sS][iI]) installer_name="AxisStudioSetup.msi" ;;
            *.[zZ][iI][pP]) installer_name="AxisStudioSetup.zip" ;;
        esac
        cp -- "${AXIS_INSTALLER}" "${media_payload}/${installer_name}"
    fi

    log "Creating guided Axis Studio setup CD"
    xorriso -as mkisofs -quiet -iso-level 3 -J -joliet-long -R \
        -V AXIS_SETUP -o "${AXIS_MEDIA_STAGING}/axis-setup.iso" "${media_payload}"
    sudo mkdir -p "${IMAGES_DIR}"
    sudo install -m 0644 "${AXIS_MEDIA_STAGING}/axis-setup.iso" "${AXIS_SETUP_ISO}"
    cleanup_axis_media_staging
    AXIS_MEDIA_STAGING=""
}

require_option_value() {
    [ "$#" -ge 2 ] && [ -n "$2" ] || die "$1 requires a value"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --windows-iso)      require_option_value "$@"; WINDOWS_ISO="$2"; shift 2 ;;
        --name)             require_option_value "$@"; VM_NAME="$2"; shift 2 ;;
        --windows-version)  require_option_value "$@"; WINDOWS_VERSION="$2"; shift 2 ;;
        --memory)           require_option_value "$@"; MEMORY_MIB="$2"; shift 2 ;;
        --vcpus)            require_option_value "$@"; VCPUS="$2"; shift 2 ;;
        --disk-size)        require_option_value "$@"; DISK_SIZE_GIB="$2"; shift 2 ;;
        --images-dir)       require_option_value "$@"; IMAGES_DIR="$2"; shift 2 ;;
        --axis-installer)   require_option_value "$@"; AXIS_INSTALLER="$2"; shift 2 ;;
        --skip-axis-media)  AXIS_MEDIA=0; shift ;;
        --skip-packages)    SKIP_PACKAGES=1; shift ;;
        --dry-run)          DRY_RUN=1; shift ;;
        -h|--help)          usage; exit 0 ;;
        *)                  usage >&2; die "Unknown argument: $1" ;;
    esac
done

case "${WINDOWS_VERSION}" in
    win10|win11) ;;
    *) die "--windows-version must be win10 or win11" ;;
esac

[ -n "${WINDOWS_ISO}" ] || { usage >&2; die "--windows-iso is required"; }
if [ "${DRY_RUN}" -eq 0 ]; then
    [ -r "${WINDOWS_ISO}" ] || die "Windows ISO not readable: ${WINDOWS_ISO}"
fi
if [ -n "${AXIS_INSTALLER}" ]; then
    [ "${AXIS_MEDIA}" -eq 1 ] || die "--axis-installer cannot be used with --skip-axis-media"
    case "${AXIS_INSTALLER}" in
        *.[eE][xX][eE]|*.[mM][sS][iI]|*.[zZ][iI][pP]) ;;
        *) die "--axis-installer must point to an .exe, .msi, or .zip file" ;;
    esac
    if [ "${DRY_RUN}" -eq 0 ]; then
        [ -r "${AXIS_INSTALLER}" ] || die "Axis Studio installer not readable: ${AXIS_INSTALLER}"
    fi
fi

# ---------------------------------------------------------------- preflight
[ "$(uname -m)" = "x86_64" ] || die "Only x86_64 hosts are supported"

if ! grep -qE '(vmx|svm)' /proc/cpuinfo; then
    warn "CPU exposes no VT-x/AMD-V flags."
    warn "On physical stations: enable virtualization in BIOS/UEFI firmware."
    warn "On cloud instances without nested virtualization this cannot work."
    [ "${DRY_RUN}" -eq 1 ] || die "Hardware virtualization unavailable; aborting (use --dry-run to inspect)."
fi
if [ ! -e /dev/kvm ] && [ "${DRY_RUN}" -eq 0 ]; then
    die "/dev/kvm missing. Load the kvm_intel/kvm_amd module or enable firmware virtualization."
fi

command -v apt-get >/dev/null || warn "apt-get not found; install QEMU/libvirt packages manually."

# ------------------------------------------------------------ host packages
if [ "${SKIP_PACKAGES}" -eq 0 ] && command -v apt-get >/dev/null; then
    log "Installing host virtualization packages (sudo required)"
    HOST_PACKAGES=(
        qemu-system-x86 qemu-utils
        libvirt-daemon-system libvirt-clients virtinst virt-manager virt-viewer
        ovmf swtpm swtpm-tools
        curl
    )
    if [ "${AXIS_MEDIA}" -eq 1 ]; then
        HOST_PACKAGES+=(xorriso)
    fi
    run sudo apt-get update
    run sudo apt-get install -y "${HOST_PACKAGES[@]}"
fi

log "Enabling libvirtd and default NAT network"
run sudo systemctl enable --now libvirtd
run sudo usermod -aG libvirt,kvm "${USER}"
if [ "${DRY_RUN}" -eq 0 ]; then
    sudo virsh net-info default >/dev/null 2>&1 || die "libvirt 'default' network missing"
    sudo virsh net-autostart default >/dev/null
    sudo virsh net-start default >/dev/null 2>&1 || true
else
    run sudo virsh net-autostart default
    run sudo virsh net-start default
fi

# ------------------------------------------------------- virtio-win drivers
VIRTIO_ISO="${IMAGES_DIR}/virtio-win.iso"
if [ "${DRY_RUN}" -eq 0 ] && [ ! -f "${VIRTIO_ISO}" ]; then
    log "Downloading virtio-win driver ISO (Windows storage/network drivers)"
    run sudo mkdir -p "${IMAGES_DIR}"
    run sudo curl -fL --retry 3 -o "${VIRTIO_ISO}" "${VIRTIO_WIN_URL}"
elif [ "${DRY_RUN}" -eq 1 ]; then
    run sudo curl -fL --retry 3 -o "${VIRTIO_ISO}" "${VIRTIO_WIN_URL}"
fi

# ------------------------------------------------------------------ VM disk
DISK_PATH="${IMAGES_DIR}/${VM_NAME}.qcow2"
AXIS_SETUP_ISO="${IMAGES_DIR}/${VM_NAME}-axis-setup.iso"
if [ "${DRY_RUN}" -eq 0 ] && sudo virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
    die "Domain '${VM_NAME}' already exists. Remove it first: sudo virsh undefine ${VM_NAME} --nvram --tpm"
fi
if [ "${DRY_RUN}" -eq 0 ] && sudo test -e "${DISK_PATH}"; then
    die "Disk already exists: ${DISK_PATH} (remove it or pass --name)"
fi
if [ "${AXIS_MEDIA}" -eq 1 ] && [ "${DRY_RUN}" -eq 0 ] && sudo test -e "${AXIS_SETUP_ISO}"; then
    die "Axis setup ISO already exists: ${AXIS_SETUP_ISO} (remove it or pass --name)"
fi

if [ "${AXIS_MEDIA}" -eq 1 ]; then
    build_axis_setup_media
fi

log "Creating sparse ${DISK_SIZE_GIB} GiB guest disk at ${DISK_PATH}"
run sudo qemu-img create -f qcow2 "${DISK_PATH}" "${DISK_SIZE_GIB}G"

# ------------------------------------------------------------- virt-install
# Windows 11 requires UEFI Secure Boot + TPM 2.0; emulate both with
# OVMF + swtpm. Windows 10 gets the same UEFI stack for consistency.
# The explicit qemu-xhci controller is required so attach-usb.sh can pass the
# Noitom transceiver (a USB/RNDIS network device) into the guest later.
OS_VARIANT="${WINDOWS_VERSION}"
if [ "${DRY_RUN}" -eq 0 ] && command -v osinfo-query >/dev/null; then
    if ! osinfo-query os short-id="${OS_VARIANT}" 2>/dev/null | grep -q "${OS_VARIANT}"; then
        warn "osinfo-db does not know '${OS_VARIANT}'; falling back to win10"
        OS_VARIANT="win10"
    fi
fi

VIRT_INSTALL_ARGS=(
    --name "${VM_NAME}"
    --memory "${MEMORY_MIB}"
    --vcpus "${VCPUS}"
    --cpu host-passthrough
    --os-variant "${OS_VARIANT}"
    --disk "path=${DISK_PATH},bus=virtio,format=qcow2"
    --cdrom "${WINDOWS_ISO}"
    --disk "path=${VIRTIO_ISO},device=cdrom"
    --network network=default,model=virtio
    --controller usb,model=qemu-xhci
    --graphics spice
    --video qxl
    --boot uefi
    --noautoconsole
)
if [ "${AXIS_MEDIA}" -eq 1 ]; then
    VIRT_INSTALL_ARGS+=(
        --disk "path=${AXIS_SETUP_ISO},device=cdrom,readonly=on"
    )
fi
if [ "${WINDOWS_VERSION}" = "win11" ]; then
    VIRT_INSTALL_ARGS+=(
        --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb
        --features smm.state=on
    )
fi

log "Defining and starting the VM (Windows installer boots next)"
run sudo virt-install "${VIRT_INSTALL_ARGS[@]}"

# ------------------------------------------------------------- station note
run mkdir -p "${CONFIG_DIR}"
if [ "${DRY_RUN}" -eq 0 ]; then
    printf 'VM_NAME=%s\n' "${VM_NAME}" > "${CONFIG_DIR}/config.env"
fi

cat <<EOF

$(log "VM '${VM_NAME}' created.")

Next steps (details in vm/README.md):
  1. Open the console and complete Windows setup:
       virt-viewer --connect qemu:///system ${VM_NAME}
     During install, load the disk driver from the virtio-win CD
     (amd64\\${WINDOWS_VERSION}), and afterwards run virtio-win-gt-x64.msi
     from the same CD for the network/guest drivers.
EOF

if [ "${AXIS_MEDIA}" -eq 1 ]; then
    cat <<EOF
  2. In Windows, open the AXIS_SETUP CD and double-click
     START-AXIS-SETUP.cmd. The guide installs the supplied package or opens
     Noitom's official download and account pages, then explains Product-ID
     registration and Axis Studio login. Credentials are never stored here.
EOF
else
    cat <<EOF
  2. In Windows, download the correct Axis Studio edition from your Noitom
     account, install it, register the Product ID, and sign in.
EOF
fi

cat <<EOF
  3. Detect and attach the Noitom transceiver from this vm directory:
       ./attach-usb.sh detect && ./attach-usb.sh attach
  4. Configure the transceiver's RNDIS adapter in Windows as 192.168.1.100/24
     and point Axis Studio's BVH broadcast at this host: 192.168.122.1:7012 (UDP).
  5. Verify frames on the host:
       python3 vm/bridge/bvh_to_gr00t.py --probe

If this shell was not already in the 'libvirt' group, log out/in once before
running virsh without sudo.
EOF

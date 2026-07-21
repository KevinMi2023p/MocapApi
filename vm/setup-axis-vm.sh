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

VM_NAME="axis-studio"
WINDOWS_ISO=""
WINDOWS_VERSION="win11"          # win10 | win11
MEMORY_MIB=8192
VCPUS=4
DISK_SIZE_GIB=120
IMAGES_DIR="/var/lib/libvirt/images"
VIRTIO_WIN_URL="https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
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
  --skip-packages        Do not apt-get install host packages
  --dry-run              Print the commands without executing them
  -h, --help             Show this help

After Windows setup completes, continue with vm/README.md:
Axis Studio install/activation, USB passthrough (attach-usb.sh), and
BVH streaming configuration.
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

while [ $# -gt 0 ]; do
    case "$1" in
        --windows-iso)      WINDOWS_ISO="$2"; shift 2 ;;
        --name)             VM_NAME="$2"; shift 2 ;;
        --windows-version)  WINDOWS_VERSION="$2"; shift 2 ;;
        --memory)           MEMORY_MIB="$2"; shift 2 ;;
        --vcpus)            VCPUS="$2"; shift 2 ;;
        --disk-size)        DISK_SIZE_GIB="$2"; shift 2 ;;
        --images-dir)       IMAGES_DIR="$2"; shift 2 ;;
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
    run sudo apt-get update
    run sudo apt-get install -y \
        qemu-system-x86 qemu-utils \
        libvirt-daemon-system libvirt-clients virtinst virt-manager virt-viewer \
        ovmf swtpm swtpm-tools \
        curl
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
if [ "${DRY_RUN}" -eq 0 ] && sudo virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
    die "Domain '${VM_NAME}' already exists. Remove it first: sudo virsh undefine ${VM_NAME} --nvram --tpm"
fi
if [ "${DRY_RUN}" -eq 0 ] && sudo test -e "${DISK_PATH}"; then
    die "Disk already exists: ${DISK_PATH} (remove it or pass --name)"
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
  2. Detect and attach the Noitom transceiver:
       ./attach-usb.sh detect && ./attach-usb.sh attach
  3. In the guest, install Axis Studio and activate it (Noitom manual:
     https://support.noitom.com.cn/s/customer-manual-en/doc/1-software-installation-and-activation-yC49l3MCA2 )
  4. Configure the transceiver's RNDIS adapter in Windows as 192.168.1.100/24
     and point Axis Studio's BVH broadcast at this host: 192.168.122.1:7012 (UDP).
  5. Verify frames on the host:
       python3 vm/bridge/bvh_to_gr00t.py --probe

If this shell was not already in the 'libvirt' group, log out/in once before
running virsh without sudo.
EOF

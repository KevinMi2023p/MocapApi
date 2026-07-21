#!/usr/bin/env bash
# Pass the Noitom Perception Neuron transceiver through to the Axis Studio VM.
#
# The transceiver is a USB/RNDIS network device. The guest gets it exclusively
# (studio/docs/USB_ON_LINUX.md, Architecture B): we address it by the exact
# VID:PID recorded by `detect`, never by a glob or a whole controller.
#
# Subcommands:
#   detect            Interactively identify the transceiver's VID:PID and save it
#   attach            Attach the device to the running VM (live + persistent)
#   detach            Detach the device from the VM
#   status            Show the saved device, host binding, and VM attachment
#   install-hotplug   Install a udev rule that re-attaches the device on replug

set -euo pipefail

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/axis-vm"
CONFIG_FILE="${CONFIG_DIR}/config.env"
UDEV_RULE="/etc/udev/rules.d/90-axis-vm-usb.rules"
HOTPLUG_HELPER="/usr/local/bin/axis-vm-usb-attach"
VIRSH=(sudo virsh --connect qemu:///system)

log()  { printf '\033[1;34m[axis-usb]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[axis-usb]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[axis-usb]\033[0m %s\n' "$*" >&2; exit 1; }

load_config() {
    [ -f "${CONFIG_FILE}" ] || die "No config at ${CONFIG_FILE}. Run: $(basename "$0") detect"
    # shellcheck source=/dev/null
    . "${CONFIG_FILE}"
    VM_NAME="${VM_NAME:-axis-studio}"
}

require_device() {
    [ -n "${USB_VID:-}" ] && [ -n "${USB_PID:-}" ] || \
        die "No transceiver recorded in ${CONFIG_FILE}. Run: $(basename "$0") detect"
}

hostdev_xml() {
    cat <<EOF
<hostdev mode='subsystem' type='usb' managed='yes'>
  <source>
    <vendor id='0x${USB_VID}'/>
    <product id='0x${USB_PID}'/>
  </source>
</hostdev>
EOF
}

cmd_detect() {
    command -v lsusb >/dev/null || die "lsusb not found (apt install usbutils)"
    mkdir -p "${CONFIG_DIR}"

    log "Unplug the Noitom transceiver if it is plugged in, then press Enter."
    read -r
    before="$(lsusb)"
    log "Now plug the transceiver into the host and wait a few seconds..."
    new_line=""
    for _ in $(seq 1 30); do
        sleep 1
        new_line="$(comm -13 <(sort <<<"${before}") <(lsusb | sort) | head -n 1)"
        [ -n "${new_line}" ] && break
    done
    [ -n "${new_line}" ] || die "No new USB device appeared within 30 seconds."

    vidpid="$(sed -n 's/.*ID \([0-9a-fA-F]\{4\}\):\([0-9a-fA-F]\{4\}\).*/\1:\2/p' <<<"${new_line}")"
    [ -n "${vidpid}" ] || die "Could not parse VID:PID from: ${new_line}"
    vid="${vidpid%%:*}"; pid="${vidpid##*:}"

    log "New device: ${new_line}"
    printf 'Record %s:%s as the Noitom transceiver? [y/N] ' "${vid}" "${pid}"
    read -r answer
    case "${answer}" in
        y|Y|yes|YES) ;;
        *) die "Aborted; nothing recorded." ;;
    esac

    vm_name="axis-studio"
    if [ -f "${CONFIG_FILE}" ]; then
        # shellcheck source=/dev/null
        . "${CONFIG_FILE}"
        vm_name="${VM_NAME:-axis-studio}"
    fi
    {
        printf 'VM_NAME=%s\n' "${vm_name}"
        printf 'USB_VID=%s\n' "${vid}"
        printf 'USB_PID=%s\n' "${pid}"
        printf 'USB_DESCRIPTION=%s\n' "\"${new_line}\""
    } > "${CONFIG_FILE}"
    log "Saved to ${CONFIG_FILE}. Next: $(basename "$0") attach"
}

cmd_attach() {
    load_config; require_device
    "${VIRSH[@]}" domstate "${VM_NAME}" 2>/dev/null | grep -q running || \
        die "VM '${VM_NAME}' is not running. Start it: sudo virsh start ${VM_NAME}"
    lsusb -d "${USB_VID}:${USB_PID}" >/dev/null || \
        die "Device ${USB_VID}:${USB_PID} is not plugged into the host."

    xml_file="$(mktemp /tmp/axis-vm-hostdev.XXXXXX.xml)"
    trap 'rm -f "${xml_file}"' EXIT
    hostdev_xml > "${xml_file}"
    if "${VIRSH[@]}" dumpxml "${VM_NAME}" | grep -q "0x${USB_PID}"; then
        log "Device already attached to '${VM_NAME}'."
        return 0
    fi
    "${VIRSH[@]}" attach-device "${VM_NAME}" "${xml_file}" --live --config
    log "Attached ${USB_VID}:${USB_PID} to '${VM_NAME}' (live + persistent)."
    log "Windows now owns the device; it will disappear from host lsusb output."
}

cmd_detach() {
    load_config; require_device
    xml_file="$(mktemp /tmp/axis-vm-hostdev.XXXXXX.xml)"
    trap 'rm -f "${xml_file}"' EXIT
    hostdev_xml > "${xml_file}"
    "${VIRSH[@]}" detach-device "${VM_NAME}" "${xml_file}" --live --config
    log "Detached ${USB_VID}:${USB_PID} from '${VM_NAME}'."
}

cmd_status() {
    load_config
    log "VM: ${VM_NAME} ($("${VIRSH[@]}" domstate "${VM_NAME}" 2>/dev/null || echo 'not defined'))"
    if [ -n "${USB_VID:-}" ]; then
        log "Recorded transceiver: ${USB_VID}:${USB_PID} ${USB_DESCRIPTION:-}"
        if lsusb -d "${USB_VID}:${USB_PID}" >/dev/null 2>&1; then
            log "Host sees the device (not yet claimed by the guest, or VM stopped)."
        else
            log "Host does not see the device (unplugged, or claimed by the guest)."
        fi
        if "${VIRSH[@]}" dumpxml "${VM_NAME}" 2>/dev/null | grep -q "0x${USB_PID}"; then
            log "Domain XML contains the hostdev entry (attached/persistent)."
        else
            log "Domain XML has no hostdev entry for it."
        fi
    else
        warn "No transceiver recorded yet. Run: $(basename "$0") detect"
    fi
}

cmd_install_hotplug() {
    load_config; require_device
    script_path="$(readlink -f "$0")"
    log "Installing udev hotplug rule (sudo required)"
    sudo tee "${HOTPLUG_HELPER}" >/dev/null <<EOF
#!/bin/sh
# Auto-generated by attach-usb.sh: re-attach the Noitom transceiver to the
# Axis Studio VM when it is (re)plugged. Runs detached because udev RUN
# handlers must return quickly.
exec systemd-run --no-block "${script_path}" attach
EOF
    sudo chmod 755 "${HOTPLUG_HELPER}"
    sudo tee "${UDEV_RULE}" >/dev/null <<EOF
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="${USB_VID}", ATTR{idProduct}=="${USB_PID}", RUN+="${HOTPLUG_HELPER}"
EOF
    sudo udevadm control --reload-rules
    log "Installed ${UDEV_RULE}. Replugging the transceiver now auto-attaches it."
    warn "Note: while the rule exists, the host cannot use the device itself."
}

case "${1:-}" in
    detect)          cmd_detect ;;
    attach)          cmd_attach ;;
    detach)          cmd_detach ;;
    status)          cmd_status ;;
    install-hotplug) cmd_install_hotplug ;;
    *)
        cat >&2 <<EOF
Usage: $(basename "$0") {detect|attach|detach|status|install-hotplug}

  detect            Identify the Noitom transceiver's VID:PID and record it
  attach            Give the running VM exclusive use of the transceiver
  detach            Return the transceiver to the host
  status            Show recorded device and current attachment state
  install-hotplug   Auto-attach on replug via a udev rule (optional)
EOF
        exit 2
        ;;
esac

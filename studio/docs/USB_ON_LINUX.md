# Axis hardware on Linux

This document separates the different features that Axis Studio describes as
"USB" and records the supported experiments for Mocap Studio. It is a test
plan, not a claim of hardware compatibility. No supported Noitom hardware was
available in the current Linux build environment.

## Recommendation

For a Perception Neuron Studio transceiver, let Linux own the USB device and
expose it as an RNDIS network interface. Configure that one interface with
`192.168.1.100/24`, then let Axis Studio running under Wine use the normal host
network stack. Do **not** start by passing raw USB through to Wine.

If Axis cannot enumerate or use the Linux-managed interface, pass the complete
USB device exclusively to a Windows virtual machine. Keep firmware updates on
a supported Windows installation even if ordinary capture works under Wine.

This approach keeps the native Mocap Studio application focused on the public,
open integration surface: BVH/Calc streams and the MocapApi command channel.
It does not attempt to recreate Noitom's sensor firmware, pairing protocol,
sensor-fusion solver, or calibration solver.

## Why the transceiver is a network device

The supplied *Axis Studio Manual*, page 7, identifies the PN Studio
transceiver as a "Remote NDIS Compatible Device" virtual network card. It
instructs the operator to configure the computer as `192.168.1.100` with a
`255.255.255.0` mask and then select that network interface in Axis Studio.
Microsoft describes RNDIS as Ethernet carried over USB, and Linux includes the
open-source `rndis_host` driver.

Static inspection of the supplied Axis Studio v3 package supports the same
model:

- the device library contains USB-network mounting/configuration and network
  adapter monitoring routines;
- device UI plugins enumerate Windows and Qt network interfaces;
- the relevant transport components use sockets, UDP, and broadcast traffic;
  they do not import WinUSB, SetupAPI, or HID APIs.

The supplied application is proprietary reference material and is not part of
this repository or its releases.

## Three independent USB paths

| Path | What it carries | Preferred Linux approach |
| --- | --- | --- |
| PN Studio transceiver | Sensor traffic as USB Ethernet/RNDIS | Linux `rndis_host`, dedicated static-IP profile, Wine networking |
| Legacy CodeMeter dongle | Axis 2.x license | Windows VM passthrough, or a permitted native CodeMeter network license |
| Firmware/device maintenance | Low-level device operations | Supported Windows installation only |

Axis Studio v3 activation in the supplied manual is account/Product-ID based.
Older Axis releases may instead use a Wibu CodeMeter dongle. These are separate
problems and must not be conflated with the RNDIS transceiver.

## Architecture A: Linux RNDIS plus Wine

Expected flow:

```text
sensors -> transceiver -> USB/RNDIS -> Linux network interface
                                     -> Wine / Axis Studio
                                     -> BVH, Calc, or MocapApi stream
                                     -> native Mocap Studio
```

Before changing anything, capture read-only diagnostics:

```sh
lsusb
ip -brief link
ip -brief address
ip route
lsmod | grep -E 'rndis_host|usbnet|cdc_ether'
```

The interface name may change between machines. Identify it from its USB
parent, MAC address, and driver; never assume it is named `usb0`.

Create a dedicated NetworkManager profile only after confirming the target
interface. Its intended settings are:

- IPv4 address `192.168.1.100/24`;
- no gateway;
- no DNS;
- `ipv4.never-default=yes`;
- no IPv6 default route.

Do not silently alter an existing profile. In particular, first detect whether
another interface already routes `192.168.1.0/24`. If it does, either run Wine
and the RNDIS interface in a dedicated network namespace or use the Windows VM
route below. The eventual in-app setup wizard should use NetworkManager's
D-Bus/Polkit integration for the one privileged operation; the application
itself must not run as root.

Wine exposes Linux networking through its Winsock and adapter-compatibility
layers. Recent Wine versions also include a libusb-backed `wineusb` driver,
but that is not a complete Windows NDIS driver stack. Since this device is
already network-shaped, raw USB access adds risk without adding a useful
capability. Linux should set the static address before Axis starts because
Wine's adapter-configuration compatibility is incomplete.

The supplied Axis v3 build has a separate Wine startup incompatibility: it
assumes Windows Firewall rule enumeration returns a valid `IEnumVARIANT`, while
Wine's `hnetcfg` implementation returns `E_NOTIMPL` and a null pointer. The
developer-only reference harness documents a no-network workaround that lets
the login UI open. That result does not yet validate login, activation, RNDIS,
capture, or hardware behavior.

## Architecture B: Windows VM passthrough

QEMU/libvirt can attach a selected host USB device exclusively to a Windows
guest. This lets Windows use its RNDIS driver and provides the closest behavior
to a supported Axis installation. An implementation of this architecture now
lives at `studio/tools/windows-vm/`; the full station guide is
[WINDOWS_VM.md](WINDOWS_VM.md).

Use all of the following safeguards:

- identify the exact device by verified VID/PID or stable physical port;
- add an emulated xHCI controller to the guest;
- give Axis a host-only or bridged interface for traffic to Mocap Studio;
- use explicit unicast UDP/TCP instead of assuming broadcasts cross NAT;
- reserve guest internet access for activation/update requirements;
- never pass an ambiguous USB glob or a complete host controller through.

3D rendering may require guest 3D acceleration, working software OpenGL, or
GPU passthrough. USB connectivity alone does not guarantee the Axis viewport
will render correctly.

## Architecture C: USB/IP

Linux USB/IP uses an exporting server and importing client; the device's
driver runs on the importing side. It can help when the physical hardware is
on a different Linux host, or when feeding a laboratory VM. It adds latency,
disconnect behavior, and another privileged service, so it is not the normal
installation path.

## Legacy CodeMeter licensing

Wibu documents two relevant arrangements:

1. attach a CmDongle exclusively to one virtual machine; or
2. run CodeMeter Runtime on a host/client as a network license server and let
   the protected application connect over TCP port `22350`.

The second option is valid only when the license's configured network quantity
and Noitom's terms permit it. Mocap Studio must not try to bypass or emulate
Axis activation.

## What MocapApi can reuse

The public SDK remains the primary integration boundary. It already models
avatars, joints, sensors, body parts, rigid bodies, and trackers, and exposes
commands for capture, zeroing, calibration, recording, and posture recovery.
Mocap Studio should reuse those interfaces where the user supplies a compatible
runtime rather than designing a parallel command protocol.

The upstream repository currently has Linux x86-64 and ARM64 runtime libraries
but no macOS desktop dynamic library. It also has no root license file that
clearly grants redistribution of its headers and precompiled SDK binaries.
Until the rights holder clarifies that license, releases must not relicense or
bundle those artifacts. A runtime supplied by the user can be detected and
loaded separately.

MocapApi is downstream of Axis: it cannot pair sensors, manage transceiver
firmware, or implement Noitom's motion solver. macOS therefore uses BVH/Calc
streaming from Axis or a small network relay until a redistributable macOS SDK
is available.

## Hardware validation checklist

1. Record USB descriptors, driver binding, interface MAC, addresses, and routes
   without changing the host.
2. Confirm `rndis_host` creates a network interface.
3. Detect any `192.168.1.0/24` route conflict.
4. With operator confirmation, add the isolated NetworkManager profile.
5. Confirm Axis under Wine can enumerate and select the interface.
6. Test sensor connection, calibration, capture, and ordinary recording.
7. Test BVH/Calc or MocapApi streaming into native Mocap Studio.
8. Repeat through Windows VM USB passthrough and compare behavior if Wine fails.
9. Do not test firmware upgrades under Wine.

Record the exact transceiver VID/PID, firmware version, Wine version, Linux
kernel, NetworkManager version, and every result. A failed test must leave the
host's original network profiles unchanged.

## References

- [Noitom Axis Studio manual: starting capture](https://noitom.gitbook.io/axis-studio-manual/kai-shi-dong-bu)
- [Official MocapApi repository](https://github.com/pnmocap/MocapApi)
- [Microsoft RNDIS overview](https://learn.microsoft.com/en-us/windows-hardware/drivers/network/overview-of-remote-ndis--rndis-)
- [Linux `rndis_host` source](https://github.com/torvalds/linux/blob/master/drivers/net/usb/rndis_host.c)
- [NetworkManager IPv4 settings](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html)
- [Wine 11 USB backend](https://github.com/wine-mirror/wine/blob/wine-11.0/dlls/wineusb.sys/unixlib.c)
- [Wine 11 WMI implementation](https://github.com/wine-mirror/wine/blob/wine-11.0/dlls/wbemprox/builtin.c)
- [Wine 11 Windows Firewall compatibility implementation](https://github.com/wine-mirror/wine/blob/wine-11.0/dlls/hnetcfg/policy.c)
- [QEMU USB passthrough](https://qemu.readthedocs.io/en/v10.0.3/system/devices/usb.html)
- [libvirt USB host-device configuration](https://www.libvirt.org/formatdomain)
- [Linux USB/IP architecture](https://docs.kernel.org/5.17/usb/usbip_protocol.html)
- [Wibu CodeMeter in virtual environments](https://cdn.wibu.com/fileadmin/wibu_downloads/White_Papers/CodeMeter_in_virtual_environments/CodeMeter_in_virtual_Environments_web_Final.pdf)

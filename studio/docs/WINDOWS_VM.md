# Axis Studio in an attached Windows VM

This is the operator guide for the QEMU/KVM provisioning tooling under
`studio/tools/windows-vm/`. It implements "Architecture B: Windows VM
passthrough" from [USB_ON_LINUX.md](USB_ON_LINUX.md). Like that document,
this is a test plan, not a claim of hardware compatibility. No supported
Noitom hardware was available in the current build environment, and every
hardware-dependent leg below is UNVALIDATED until someone runs the
validation checklist with a real transceiver and suit.

## Purpose and pipeline

Axis Studio is Windows-only. A Linux-only workstation that must drive
Perception Neuron hardware gets one "attached" Windows VM per station. The
VM exclusively owns the USB/RNDIS transceiver and streams standard BVH over
UDP unicast to the Linux host, where Mocap Studio (`studio/backend`) and
MocapApi consumers receive it. The host never parses proprietary sensor
traffic; the integration surface stays the public BVH stream.

```text
Noitom sensors
  -> 2.4 GHz radio
  -> USB/RNDIS transceiver (passed through to the guest)
  -> Windows 10/11 guest running Axis Studio
  -> standard BVH over UDP unicast
  -> Linux host: Mocap Studio :7012 / MocapApi
  -> [future bridge] -> g1_deploy ZMQ :5556
```

## Relationship to USB_ON_LINUX.md

Architecture B's safeguards are encoded directly in the tooling: devices
are identified by an exact, operator-verified VID:PID (never a glob or a
whole controller); the guest gets an emulated xHCI controller
(`qemu-xhci`); Axis traffic reaches the host over an isolated network as
explicit unicast UDP; guest internet lives on a second, detachable NIC
reserved for activation and updates; firmware and device maintenance stay
on Windows, never under Wine. Try Architecture A (Linux RNDIS plus Wine)
first if it fits your risk profile; this VM path is the fallback that most
closely matches a supported Axis installation.

## Feasibility status

Statuses are honest as of 2026-07-21. "Scripted" means code and stdlib
tests exist and run without hardware; it is not a hardware result.

| Leg | Status | Notes |
| --- | --- | --- |
| Script argument handling, XML rendering | Scripted, unit-tested | No VM or hardware required. |
| Unattended Windows install | UNVALIDATED | Needs a real ISO run to completion. |
| USB passthrough of the transceiver | UNVALIDATED | VID:PID is not even published; see probe below. |
| Sensor pairing/calibration/capture in guest | UNVALIDATED | Requires suit and transceiver. |
| OpenGL viewport (QXL, llvmpipe) | UNVALIDATED | Known vendor failure mode; see GPU section. |
| Axis activation inside a VM | UNVALIDATED | Vendor seat behavior in VMs untested. |
| BVH UDP guest-to-host delivery | UNVALIDATED | Host listener path is testable without hardware via `studio/tools/windows-vm/send_test_bvh.py`. |
| End-to-end latency | UNMEASURED | No numbers exist yet. |
| udev hotplug reattach | UNVALIDATED | Rules install is scripted; replug untested. |

## Host requirements

- x86-64 CPU with VT-x/AMD-V and a working `/dev/kvm`.
- At least 8 GiB RAM for the guest (16 GiB host total recommended) and
  120 GiB free disk at the state directory
  (`${XDG_DATA_HOME:-$HOME/.local/share}/axis-vm/`; `--state-dir DIR`
  overrides it).
- Debian/Ubuntu-style `apt` for `--install-deps` (qemu-system-x86,
  libvirt, virtinst, OVMF, swtpm, virt-viewer, genisoimage, curl); other
  distributions install the printed equivalents manually.
- Membership in the `kvm` and `libvirt` groups (log out and back in after
  `--install-deps` adds them).
- A user-supplied Windows 10/11 ISO and, for the real thing, a Noitom
  account plus Perception Neuron hardware.

The script refuses to run as root. Privileged host changes are printed as
exact commands by default and executed via sudo only with `--yes`.
Read-only operations (`--preflight`, `--status`, `--probe-usb`) never need
sudo. All libvirt operations use the `qemu:///system` connection.

## Station setup with axis-vm.sh

Run from the repository root, in this order. Every operation accepts
`--name NAME` (default domain `axis-studio`), `--state-dir DIR`, and
`--yes`.

```sh
studio/tools/windows-vm/axis-vm.sh --preflight
studio/tools/windows-vm/axis-vm.sh --install-deps --yes
studio/tools/windows-vm/axis-vm.sh --define-network --yes
studio/tools/windows-vm/axis-vm.sh --create --windows-iso /path/to/Win11.iso
studio/tools/windows-vm/axis-vm.sh --download-axis 2    # or: 3
studio/tools/windows-vm/axis-vm.sh --build-payload-iso
studio/tools/windows-vm/axis-vm.sh --probe-usb
studio/tools/windows-vm/axis-vm.sh --attach-usb VID:PID
studio/tools/windows-vm/axis-vm.sh --install-hotplug VID:PID --yes
studio/tools/windows-vm/axis-vm.sh --firewall --yes
```

`--preflight` (also the default operation) checks CPU virtualization
flags, `/dev/kvm`, RAM, free disk, group membership, missing packages,
WSL2, and route conflicts for `192.168.77.0/24`; it warns if
`192.168.1.0/24` is routed, because that subnet belongs to the transceiver
RNDIS link inside the guest and is never used by this tooling.

`--define-network` creates the isolated `axis-mocap` libvirt network:
bridge `virbr-axis`, host `192.168.77.1/24`, guest pinned to
`192.168.77.2` by a DHCP host entry for MAC `52:54:00:6d:63:70`. It
refuses to proceed if `192.168.77.0/24` overlaps an existing route.

`--create` installs Windows unattended (tens of minutes). Optional flags:
`--iso-sha256 HEX`, `--virtio-iso PATH`, `--disk-gib N` (default 100),
`--ram-mib N` (default 8192), `--vcpus N` (default 4). The VM shape is
deliberately driver-free for Windows Setup: SATA qcow2 disk at
`$STATE_DIR/disks/axis-studio.qcow2`, e1000e NICs, `qemu-xhci` USB
controller, UEFI with an emulated swtpm TPM 2.0, SPICE graphics on
127.0.0.1 with QXL video. A local administrator `mocap` is created with a
password generated at create time, stored mode 0600 at
`$STATE_DIR/guest-password`, and printed once. The answer file names the
machine `AXIS-STUDIO` and enables RDP.

`--download-axis 2|3` fetches the installer for your hardware generation
(version fork below); run it while Windows installs.
`--build-payload-iso` packs `$STATE_DIR/payload/` (installer,
`guest-setup.ps1`, any mesa-dist-win archive you dropped in) into an ISO
and inserts or refreshes it in the VM's reserved payload CD drive with
`virsh change-media`: a live insert while the guest is running (the guest
sees the disc immediately), and `--config` so it is present on the next
boot.

Then log into the guest (`--console` for SPICE, `--rdp` after the install
finishes) and run the payload CD's script from an elevated (Run as
administrator) PowerShell prompt:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
& <payload-drive>:\guest-setup.ps1    # add -SoftwareGl to deploy llvmpipe
```

The payload disc carries the volume label `AXIS_PAYLOAD`; check the drive
letter under This PC (the Windows and unattend ISOs occupy the earlier
letters).

It installs Axis Studio (the Axis 2 zip is extracted for an interactive
setup run; silent flags for it are unverified), deploys software OpenGL
when invoked with `-SoftwareGl`, sets the transceiver RNDIS adapter to
static `192.168.1.100/24`, and prints the canonical BVH Broadcasting
settings. It never touches activation.

Finally configure the BVH stream contract below on both sides and connect
from Mocap Studio. Day-to-day management: `--start`, `--shutdown`,
`--destroy` (force off, asks for confirmation), `--autostart on|off`,
`--console`, `--rdp`, and `--status` (domain state, network info, guest
address, attached USB hostdevs, and whether anything on the host listens
on UDP 7012).

## Windows ISO acquisition

The script never downloads Windows; Microsoft blocks automated ISO
retrieval, and Windows licensing is the operator's responsibility. Fetch
an ISO manually from <https://www.microsoft.com/software-download/windows11>
(Windows 10 ISOs also work with this VM shape). For time-boxed lab use,
Microsoft's Windows 11 Enterprise Evaluation is an alternative; check its
terms yourself. The answer file uses the generic Windows 11 Pro
edition-selection key (`VK7JG-NPHTM-C97JM-9MPGT-3V66T`), which only
selects the edition during setup and does not activate Windows.

## Axis Studio download and activation

Noitom split Axis Studio by hardware generation:

- Axis Studio 2 (v2.12.14002) — PN Studio / PN3 hardware sold before
  September 2025:
  `https://noitom.com/download/Axis_Studio_x64_2_12_14002.zip`
  (820,488,605 bytes).
- Axis Studio 3 (v3.0.14004) — the anti-magnetic hardware generation only:
  `https://noitom.com/download/Axis_Studio_nacs_x64_3_0_14004_2620_20251222173616363.msi`
  (398,966,784 bytes).

`--download-axis 2|3` first queries the vendor catalog API
(`https://www.noitom.com/app-api/front/getSoftware?id=1`, JSON with name,
version, and softUrl fields; reachable without authentication as of
2026-07-21) and falls back to the pinned URLs above. Noitom publishes no
checksums, so on first download the script records the file's SHA-256 to
`$STATE_DIR/<filename>.sha256` and verifies later runs against that pin —
this detects silent replacement after the first fetch, not a compromised
first fetch. Installers, Windows ISOs, and Mesa binaries are downloaded
artifacts and must never be committed to this repository.

Activation is interactive and account-based; the tooling never automates
it. Create an account at <https://account.noitom.com>, register the
hardware Product ID (starts with `U1H`, printed on the transceiver; `N3T`
on PN3 warranty cards), then log in from inside Axis Studio while the
internet NIC is attached. An account allows two simultaneous device
logins; "unbinding" a device frees a seat. Whether a VM counts as a stable
"device" for seat counting is UNVALIDATED. Trial licenses:
`contact@neuronmocap.com`. Older PN3 kits may use a Wibu CodeMeter USB
dongle instead: pass it through as a second USB device (`--attach-usb`
with its own VID:PID), or use a CodeMeter network license on TCP 22350
where the license terms permit — see [USB_ON_LINUX.md](USB_ON_LINUX.md).

## BVH stream contract

These values are the single source of truth for this station design. Most
"no data" reports reduce to one of these fields.

Axis Studio (guest), Settings -> BVH Broadcasting:

| Field | Value |
| --- | --- |
| Stream | enable "BVH - Capture" |
| Frame Format | Binary ("use old header format" unchecked) |
| Rotation | YXZ |
| Displacement | checked |
| Protocol | UDP |
| Local address | 192.168.77.2, port 7001 |
| Destination address | 192.168.77.1, port 7012 |

Never select "Advanced BVH" (the manual reserves it for specific
Maya/MotionBuilder plugins) and never broadcast to 255.255.255.255.

Mocap Studio (Linux host) connection settings:

| Field | Value |
| --- | --- |
| Mode | bvh |
| Transport | udp |
| Host | 192.168.77.1 |
| Port | 7012 |
| Rotation order | YXZ |
| Unit | centimeters |

The host field is the common pitfall: the UI default `127.0.0.1` never
receives packets from the guest, because loopback is not reachable from
the bridge. Use `192.168.77.1`; `0.0.0.0` also works but listens more
broadly than necessary.

MocapApi consumers use `SetSettingsUDP(7012)` with BVH rotation `YXZ`.
Note that the upstream sample `demo/demo-py/mocap_demo.py` hardcodes `XYZ`
at line 49; that mismatch is demo-specific — adjust your own consumer
code, do not edit the upstream demo files.

## GPU and OpenGL risk

Axis Studio's stated minimum is OpenGL 4.4 with 2 GiB VRAM (from the
v1.1-era official guide; the current v3 requirement is unverified). QXL
and virtio-gpu provide no accelerated OpenGL to Windows guests, so a stock
VM does not meet that minimum, and a Noitom knowledge-base article titled
"Black 3D Viewport Error" shows OpenGL problems are a real failure mode.
Mitigations, in order:

1. Mesa3D llvmpipe software OpenGL (supports GL 4.5+). Download a release
   archive from <https://github.com/pal1000/mesa-dist-win/releases>
   (MIT/LGPL-licensed, redistributable), drop it into
   `$STATE_DIR/payload/`, rebuild the payload ISO, and run
   `guest-setup.ps1 -SoftwareGl` in the guest to place the x64 llvmpipe
   `opengl32.dll` (and companions) next to `AxisStudio.exe` as a per-app
   override. Rendering is CPU-bound; expect a slow viewport. Whether a
   slow viewport affects capture quality is unknown.
2. VFIO GPU passthrough of a spare host GPU: real acceleration, but
   host-specific (IOMMU groups, driver rebinding) and out of scope for
   `axis-vm.sh`; consult your distribution's VFIO documentation.

Neither mitigation is validated against Axis Studio yet.

## USB passthrough, probe, and hotplug

The transceiver's USB VID:PID is not published anywhere we could find.
`--probe-usb` measures it once per hardware batch: it snapshots `lsusb`,
asks you to plug in the transceiver, polls for the difference (about a
60-second window), and prints the new device line plus suggested next
commands. Record the value alongside firmware and software versions, in
the spirit of the USB_ON_LINUX.md checklist, so future operators do not
have to re-measure it.

`--attach-usb VID:PID` and `--detach-usb VID:PID` render a libvirt
hostdev definition and apply it persistently, whether the domain is
running or not. Input must be exactly four hex digits, a colon, and four
hex digits; anything else is rejected before libvirt is invoked.
`--install-hotplug VID:PID` writes
`/etc/udev/rules.d/70-axis-vm-usb.rules` plus a helper at
`/usr/local/libexec/axis-vm-usb-hotplug.sh` so an unplugged and replugged
device is re-attached automatically (the helper defers the virsh call
through `systemd-run --no-block` to stay within udev's execution limits).
Like all privileged steps, it prints by default and applies with `--yes`.

Inside the guest, Windows binds its RNDIS driver and the transceiver link
uses `192.168.1.0/24`: `guest-setup.ps1` sets the adapter to static
`192.168.1.100/24` with no gateway, per the Axis manual (the older v1.1
guide used `.200`; either works, this tooling standardizes on `.100`).
That subnet exists only inside the guest, which is why the host tooling
refuses to touch `192.168.1.0/24`.

## Networking and firewall

The `axis-mocap` network is isolated (libvirt forward mode `none`): no
NAT, no internet, only guest-to-host traffic over `virbr-axis`. The host
owns `192.168.77.1`; the guest always receives `192.168.77.2` because its
MAC `52:54:00:6d:63:70` has a pinned DHCP host entry.

The BVH stream needs one inbound host firewall rule, UDP 7012, scoped to
the bridge interface only:

```sh
ufw allow in on virbr-axis to any port 7012 proto udp
```

For firewalld, add port 7012/udp to the zone containing `virbr-axis`.
`--firewall` detects ufw or firewalld and prints (or, with `--yes`,
applies) the matching rule. Do not open UDP 7012 on all interfaces; BVH
packets are unauthenticated and unencrypted.

## Guest internet policy

The VM's second NIC sits on the libvirt `default` NAT network, reserved
for activation and updates per USB_ON_LINUX.md. After activating Axis
Studio, run `--detach-internet`; reattach with `--attach-internet` when an
update or re-activation requires it. With the NAT NIC detached the guest
can reach only the host on the isolated bridge, which is the intended
steady state for a capture station.

## WSL2 and the Windows-host alternative

If the "Linux" machine is actually WSL2 (preflight detects this from
`/proc/version` containing "microsoft"), do not nest a VM for production
use. Run Axis Studio natively on the Windows host and stream UDP into
WSL2 either with `.wslconfig` `networkingMode=mirrored` (after which
`127.0.0.1` works as the Mocap Studio host) or to the WSL2 `eth0` address
(`wsl hostname -I`) in NAT mode — NAT-mode `localhostForwarding` is
TCP-only and silently drops UDP, so sending to `127.0.0.1` will not work
there. Nested VM creation does function under WSL2 when `/dev/kvm` is
exposed, which is useful for developing this tooling, but the USB legs
(usbipd-win chaining into a nested guest) and GPU legs cannot be
validated in that environment.

## Troubleshooting

No packets at the host: run `--status` (domain running? guest holding
`192.168.77.2`? anything listening on UDP 7012?); bind Mocap Studio to
`192.168.77.1` (or `0.0.0.0`), not the default `127.0.0.1`; in Axis
Studio verify unicast to destination `192.168.77.1` port 7012, local
address `192.168.77.2` port 7001, "BVH - Capture" enabled, Binary format
(broadcast destinations do not cross the bridge reliably); confirm the
firewall rule with `--firewall` and that `virbr-axis` is up.

Black or empty 3D viewport in the guest: the known OpenGL failure mode.
Deploy llvmpipe (`guest-setup.ps1 -SoftwareGl`) and restart Axis Studio;
if still broken, the remaining option is VFIO GPU passthrough.

Transceiver missing in the guest: confirm the hostdev with `--status`,
re-run `--probe-usb` to check the device still enumerates on the host
with the same VID:PID, try another physical port, and check `dmesg`. If
the host's `rndis_host` driver claimed the device first, passthrough
detaches it anyway — but note it in your records.

RDP fails: the unattended install may still be running; watch with
`--console` instead. RDP is enabled by the answer file once setup
completes. Log in as `mocap` against `192.168.77.2` with the password
stored at `$STATE_DIR/guest-password`.

`Cannot access storage file ... Permission denied` from `--create` or
`--start`: the system libvirt qemu user (`libvirt-qemu`) cannot traverse
a parent directory of the disk path — Ubuntu 21.04 and later create home
directories mode 0750, and the state directory defaults to living under
`$HOME`. `--create` checks every ancestor up front and prints the exact
fix (`setfacl -m u:libvirt-qemu:x DIR` for each blocked directory); run
those commands, or rerun with `--yes` to apply them via sudo. Hosts whose
qemu runs as a different user grant that user instead.

`--define-network` refuses to run: another interface already routes
`192.168.77.0/24`. Resolve that conflict first; the script will not stack
a second route.

## Validation checklist (VM path)

Mirror of the USB_ON_LINUX.md checklist, specialized for this path.
Record kernel, libvirt/QEMU, Windows build, Axis Studio version,
transceiver VID:PID and firmware, and every result.

1. Run `--preflight` on the real (non-WSL) station host; record output.
2. Complete the unattended Windows install; log in over RDP as `mocap`.
3. Measure the transceiver VID:PID with `--probe-usb`; record it.
4. Attach the device; confirm Windows binds RNDIS and Axis Studio can
   enumerate and select the `192.168.1.100` interface.
5. Pair sensors, calibrate, and run an ordinary capture in the guest.
6. Record viewport behavior with and without llvmpipe, including frame
   rate.
7. Stream BVH to Mocap Studio on the host; confirm skeleton motion and
   measure end-to-end latency.
8. Unplug and replug the transceiver; confirm the hotplug rule re-attaches
   it and Axis recovers.
9. Record activation seat behavior in the VM, including unbinding.
10. Confirm a failed test leaves host routes and network profiles
    unchanged.

Until this checklist has been executed on real hardware, treat every
table row above marked UNVALIDATED as exactly that.

## Next step: g1_deploy bridge

Once BVH frames reach the host, the planned downstream consumer is a
bridge that converts them to g1_deploy's ZMQ command stream on port 5556.
That bridge does not exist yet; the design, wire format, and validation
plan are documented in [G1_STREAMING.md](G1_STREAMING.md).

## References

- [Axis Studio manual root](https://noitom.gitbook.io/axis-studio-manual)
- [Axis Studio manual: starting capture](https://noitom.gitbook.io/axis-studio-manual/kai-shi-dong-bu)
- [Noitom support manual: installation and activation](https://support.noitom.com.cn/s/customer-manual-en/doc/1-software-installation-and-activation-yC49l3MCA2)
- [Noitom software downloads](https://noitom.com/serviceinfo.html?id=1)
- [Noitom download catalog API](https://www.noitom.com/app-api/front/getSoftware?id=1)
- [Noitom account portal](https://account.noitom.com)
- [Microsoft Windows 11 download](https://www.microsoft.com/software-download/windows11)
- [Windows 11 Enterprise Evaluation](https://www.microsoft.com/en-us/evalcenter/evaluate-windows-11-enterprise)
- [libvirt network XML format](https://libvirt.org/formatnetwork.html)
- [libvirt domain XML format (hostdev, controllers)](https://www.libvirt.org/formatdomain)
- [virt-install manual](https://github.com/virt-manager/virt-manager/blob/main/man/virt-install.rst)
- [swtpm, the software TPM emulator](https://github.com/stefanberger/swtpm)
- [QEMU USB passthrough](https://qemu.readthedocs.io/en/v10.0.3/system/devices/usb.html)
- [Schneegans Windows unattend generator](https://schneegans.de/windows/unattend-generator/)
- [mesa-dist-win (Mesa3D builds for Windows)](https://github.com/pal1000/mesa-dist-win)
- [usbipd-win](https://github.com/dorssel/usbipd-win)
- [WSL networking documentation](https://learn.microsoft.com/en-us/windows/wsl/networking)
- [Wibu CodeMeter in virtual environments](https://cdn.wibu.com/fileadmin/wibu_downloads/White_Papers/CodeMeter_in_virtual_environments/CodeMeter_in_virtual_Environments_web_Final.pdf)

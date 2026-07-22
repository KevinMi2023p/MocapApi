# Attached Windows VM for Axis Studio

Developer/station tooling that provisions a QEMU/KVM Windows guest which owns
the Noitom USB/RNDIS transceiver and runs Axis Studio, streaming standard BVH
over UDP unicast to Mocap Studio on the Linux host. It implements
"Architecture B: Windows VM passthrough" from
[USB_ON_LINUX.md](../../docs/USB_ON_LINUX.md) and is not part of Mocap Studio
release archives.

```text
sensors -> 2.4GHz -> USB/RNDIS transceiver (passed through to the guest)
        -> Windows 10/11 guest + Axis Studio
        -> BVH over UDP -> 192.168.77.1:7012 (Mocap Studio / MocapApi)
```

The full station guide, feasibility status, and troubleshooting live in
[WINDOWS_VM.md](../../docs/WINDOWS_VM.md). Every hardware-facing leg (real
transceiver passthrough, viewport rendering, end-to-end latency) is a test
plan, not a compatibility claim, until bench-tested.

## Files

- `axis-vm.sh` — POSIX sh installer/manager; run `./axis-vm.sh --help`.
  Privileged host changes are printed by default and executed via sudo only
  with `--yes`. The script refuses to run as root.
- `autounattend.xml` — unattended Windows Setup template; the create step
  injects a generated password for the local `mocap` admin account.
- `guest-setup.ps1` — run inside the guest from the payload CD.
- `test_axis_vm.py` — offline tests (no libvirt, qemu, or network needed).

## Quick start (ordered)

```sh
./axis-vm.sh --preflight
./axis-vm.sh --install-deps --yes      # then log out and back in (groups)
./axis-vm.sh --define-network --yes
./axis-vm.sh --create --windows-iso ~/isos/Win11.iso
./axis-vm.sh --download-axis 2         # or 3 (anti-magnetic hardware only)
./axis-vm.sh --build-payload-iso
./axis-vm.sh --probe-usb               # measure the transceiver VID:PID
./axis-vm.sh --attach-usb VID:PID
./axis-vm.sh --firewall --yes
```

Then, inside the guest, run `guest-setup.ps1` from the payload CD and enable
BVH Broadcasting (Binary frame format, rotation YXZ, displacement checked,
UDP) from `192.168.77.2:7001` to `192.168.77.1:7012`. On the host, configure
the Mocap Studio source as mode `bvh`, transport `udp`, host `192.168.77.1`
(not 127.0.0.1), port `7012`, rotation order `YXZ`, unit centimeters.

## Legal

- No Noitom or Microsoft artifact is committed to git. The operator supplies
  the Windows ISO; Windows licensing is the operator's responsibility. Axis
  Studio installers and Mesa binaries are downloaded at setup time and stay
  in the local state directory.
- Axis Studio activation is interactive (account.noitom.com, hardware
  Product ID) and is never automated by this tooling.
- Noitom, Axis Studio, Perception Neuron, Windows, and other names are
  trademarks of their respective owners.

# Axis Studio attached VM

Noitom's Axis Studio only runs on Windows, but our stations run Linux. This
directory sets up an "attached VM": a local Windows guest that owns the
Perception Neuron USB transceiver and runs Axis Studio, while everything
downstream stays native on the Linux host.

```
Noitom sensors
      │  2.4 GHz
      ▼
USB/RNDIS transceiver ──(exclusive USB passthrough)──▶ Windows 10/11 VM
                                                        Axis Studio
                                                            │ BVH/Calc, unicast
                                                            │ UDP or TCP
                                                            ▼
                                              Linux host (this repo)
                                              MocapApi / Mocap Studio /
                                              vm/bridge/bvh_to_gr00t.py
                                                            │ ROS2
                                                            ▼
                                              gr00t G1 control loop
                                              (StreamDataPolicy/raw_data)
```

This implements "Architecture B: Windows VM passthrough" from
[`studio/docs/USB_ON_LINUX.md`](../studio/docs/USB_ON_LINUX.md). Firmware
updates and licensing stay on a supported Windows installation, and the
open BVH/Calc network boundary is the only integration surface.

## Contents

| File | Purpose |
| --- | --- |
| `setup-axis-vm.sh` | Installs QEMU/libvirt/OVMF/swtpm and provisions the Windows guest (UEFI, emulated TPM 2.0, virtio disk/net, xHCI USB controller) |
| `attach-usb.sh` | Records the transceiver's exact VID:PID, attaches/detaches it to the guest, optional udev auto-attach on replug |
| `bridge/bvh_to_gr00t.py` | Receives the BVH stream on the host and publishes wrist poses to gr00t's ROS2 ingestion topic; `--probe` mode verifies connectivity without ROS2 |
| `windows/` | Guided Axis Studio install/login helper placed on the VM's `AXIS_SETUP` CD |

## Host requirements

- Ubuntu 22.04/24.04 x86_64 (other distros work; adjust package installs).
- **VT-x/AMD-V enabled in BIOS/UEFI.** Verify with `ls /dev/kvm`. Cloud dev
  boxes without nested virtualization (like the machine this was authored on)
  cannot run the guest — the scripts are validated there with `--dry-run` only.
- ≥ 16 GB RAM (guest defaults to 8 GB), ≥ 130 GB free disk.
- A Windows 10 or 11 installer ISO and a Windows license
  ([microsoft.com/software-download](https://www.microsoft.com/software-download)).
- An Axis Studio license (online account/Product-ID for v2.12+/v3; legacy
  Axis 2.x CodeMeter dongles can be passed through like the transceiver).

## 1. Provision the VM

```sh
./setup-axis-vm.sh \
    --windows-iso ~/Downloads/Win11_24H2_English_x64.iso \
    --axis-installer ~/Downloads/AxisStudioSetup.msi

# The Axis installer is optional; the guest helper opens Noitom's official
# download and account pages when it is not supplied.
./setup-axis-vm.sh --windows-iso ~/Downloads/Win11_24H2_English_x64.iso

# smaller/older stations:
./setup-axis-vm.sh --windows-iso ... --windows-version win10 --memory 6144
```

The script checks KVM, installs the virtualization stack, downloads the
virtio-win driver ISO, creates a sparse qcow2 disk, and boots the Windows
installer. Open the console with:

```sh
virt-viewer --connect qemu:///system axis-studio
```

During Windows setup the virtio disk is invisible until you click *Load
driver* and pick the second CD (`virtio-win`) → `amd64\win11` (or `win10`).
After first boot, run `virtio-win-gt-x64.msi` from that CD to install the
network and guest drivers. The setup script also attaches an `AXIS_SETUP` CD
containing the guided Axis Studio setup helper. Pass `--skip-axis-media` only
if you want to handle the entire Axis Studio installation yourself.

## 2. Install and activate Axis Studio (in the guest)

After the virtio network driver is installed, open **This PC → AXIS_SETUP**
and double-click `START-AXIS-SETUP.cmd`:

- If `--axis-installer` was supplied, the helper launches that `.exe` or `.msi`
  interactively. `.zip` packages are extracted first; a single installer is
  launched, while an ambiguous bundle is opened for the operator to choose.
- Otherwise it opens Noitom's
  [installation guide](https://support.neuronmocap.com/hc/en-us/articles/5497866781467-Installation)
  and [account portal](https://account.noitom.com/) so the operator can
  download the edition assigned to the license.
- For an online license, create or sign in to the account, click **Register**,
  enter and verify the kit's Product ID, then launch the **Online** edition of
  Axis Studio and sign in with the same email and password.

Axis Studio V2.12+ ships as *Online* and *Dongle* editions; use the one matching
the license. Legacy CodeMeter users must pass the Wibu dongle through to the
guest. The helper deliberately never accepts or stores an email, password, or
Product ID. Activation is machine-bound, so deactivate/unbind the old VM in
the Noitom portal before rebuilding it. Guest internet access is provided by
the default libvirt NAT network.

**Graphics caveat:** Axis Studio wants OpenGL 4.4. The emulated QXL adapter
does not provide that, so the 3D viewport may render poorly or fall back to
software. Capture, solving, and BVH streaming do not depend on the viewport.
If the app refuses to start or you need the viewport, either drop
[Mesa3D for Windows](https://github.com/pal1000/mesa-dist-win) (llvmpipe
`opengl32.dll`) next to the Axis Studio executable, or use GPU passthrough on
stations with a spare GPU (out of scope for the default setup).

## 3. Pass the transceiver through

```sh
./attach-usb.sh detect    # unplug/replug the transceiver once; records VID:PID
./attach-usb.sh attach    # gives the running VM exclusive use of it
./attach-usb.sh status    # shows where the device currently lives
./attach-usb.sh install-hotplug   # optional: auto-attach on replug
```

Windows then sees the transceiver as a "Remote NDIS Compatible Device"
network adapter. Per the Axis manual, give that adapter a static address
inside the guest: **192.168.1.100 / 255.255.255.0**, no gateway. Select that
interface in Axis Studio's device settings; the sensors should enumerate.

Different transceiver hardware revisions use different USB IDs, which is why
`detect` records the exact VID:PID per station instead of hardcoding one.

## 4. Stream BVH to the host

In Axis Studio: *Settings → BVH Broadcasting* (use **standard BVH**, not
Advanced BVH):

| Setting | Value |
| --- | --- |
| Protocol | UDP |
| Destination IP | `192.168.122.1` (the host, from inside libvirt's default NAT) |
| Port | `7012` |
| Rotation order | `YXZ` (default) |
| Displacement / unit | either; the decoder infers layout, unit defaults to cm |

Use **unicast to the host IP** — subnet broadcasts do not cross the NAT
boundary. TCP also works (Axis Studio is the server; consumers connect to the
guest's `192.168.122.x` address), but UDP-to-host is the simplest and needs
no extra firewall/forwarding work.

Verify frames arrive (no ROS2 needed):

```sh
python3 vm/bridge/bvh_to_gr00t.py --probe
# [bridge]  96.0 fps  wrists(BVH frame): left=(+0.32,+0.91,+0.05)m right=(-0.34,+0.88,+0.02)m
```

The same stream feeds the rest of the repo — only one process can bind
UDP 7012 at a time, so add extra broadcast destinations in Axis Studio (or
use different ports) to run several consumers:

- **MocapApi SDK / Python wrapper:** `demo/demo-py/mocap_demo.py` uses
  `MCPSettings().set_udp(7012)`.
- **Mocap Studio:** select BVH / UDP / port 7012 in the connection dialog
  (see [`studio/README.md`](../studio/README.md)).

## 5. Feed gr00t (G1 deployment)

The bridge publishes to `StreamDataPolicy/raw_data`, gr00t's pre-IK teleop
ingestion topic (msgpack over `std_msgs/ByteMultiArray`, the
`ROSMsgPublisher` wire format). In a shell with the gr00t environment and
ROS2 sourced:

```sh
python3 vm/bridge/bvh_to_gr00t.py                 # terminal 1: Axis -> ROS2
python -m groot.control.main.teleop.run_g1_control_loop \
    --interface <robot-ip-or-sim> --body-control-device ros2   # terminal 2
```

`ros2_streamer` picks up whatever keys are present; the bridge currently
publishes `left_wrist`/`right_wrist` (4×4 transforms, robot convention:
X forward, Y left, Z up — see `--axes`). To sanity-check the control loop
without mocap hardware, use `--body-control-device mock` first.

### Known limitations / next steps

- **Fingers are not bridged yet.** gr00t expects Manus-convention
  `(25, 4, 4)` finger transforms; a validated mapping from Noitom glove
  joints is future work. Wrist-only teleop is accepted by the IK.
- **Axis alignment is a convention, not a calibration.** `--axes
  y-up-z-forward` assumes the performer calibrates facing the agreed capture
  direction; expect to tune this the first time on real hardware.
- **One VM per station, one license per seat.** Axis activation is
  per-machine; deactivate before recreating a VM
  (`sudo virsh undefine axis-studio --nvram --tpm` removes the domain).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `setup-axis-vm.sh` dies with "/dev/kvm missing" | Enable VT-x/AMD-V in firmware; on cloud instances use a bare-metal/nested-virt-capable host |
| Windows installer sees no disk | *Load driver* from the virtio-win CD (`amd64\win11`) |
| `AXIS_SETUP` CD is missing | Recreate the VM without `--skip-axis-media`; check `sudo virsh domblklist axis-studio` for the `axis-studio-axis-setup.iso` CD-ROM |
| Axis helper opens a download page instead of installing | Recreate with `--axis-installer /path/to/AxisStudioSetup.msi` (also accepts `.exe`/`.zip`), or download the assigned edition inside the guest |
| Online activation says the machine limit was reached | Unbind the retired VM under Software Activation in the Noitom account portal, then sign in again |
| Transceiver absent in guest | `./attach-usb.sh status`; the VM must be running when you attach; replug + `attach` again (or `install-hotplug`) |
| Sensors don't enumerate in Axis | RNDIS adapter must be `192.168.1.100/24` inside the guest and selected in Axis device settings |
| `--probe` shows "no frames received" | Axis must unicast to `192.168.122.1:7012`; confirm with `sudo tcpdump -i virbr0 udp port 7012`; Windows Defender outbound is open by default, but third-party firewalls may not be |
| Frames arrive but pose looks wrong | Match `--rotation-order`/`--unit` to Axis settings; try `--axes identity` to inspect the raw frame |
| Windows 11 install rejects the machine | The script already provides UEFI + TPM 2.0; ensure you didn't pass `--windows-version win10` with a win11 ISO |

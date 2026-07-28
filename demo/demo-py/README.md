# MocapApi Python demos

These demos receive Axis Studio BVH data through MocapApi. The
`mocap_wuji_bridge.py` demo retargets the Axis Studio hands to the connected
Wuji Hand 2 devices — left, right, or both, auto-detected from the hardware.
For the native, single-process C++ implementation used for Wuji bring-up, see
[`../mocap-wuji-cpp`](../mocap-wuji-cpp).

MocapApi is a network receiver; it does **not** open `.bvh` files directly:

- Use **BVH-Capture** to broadcast live motion capture.
- Use **BVH-Edit** to play a recorded motion file and broadcast its frames.

## Requirements

- Linux on x86_64 or aarch64
- Python 3.10 or newer
- Axis Studio on a host that can send UDP packets to this machine
- One or two discoverable Wuji Hand 2 devices (at most one per side)

Create an environment and install the local MocapApi wrapper plus the versions
used by the bridge:

```bash
cd /path/to/MocapApi/demo/demo-py
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ./MocapApi
python -m pip install -r requirements-wuji.txt
```

The dependency file pins `wuji-sdk==2026.7.21` and `numpy==2.2.6`. Do not
install or load the older native library from the repository root: the bridge
requires the MocapApi `0.0.73` library bundled with the local Python package.

To run the original MocapApi event demo:

```bash
python mocap_demo.py
```

## Configure Axis Studio

### Load or capture motion

Launch Axis Studio and either start live capture or open a recorded motion
file. You should see its 3D model moving:

![Axis Studio playing motion](img/launch_axis_studio.gif)

### Configure BVH Broadcasting

Open the Axis Studio settings, select **BVH Data Broadcast**, and enable the
appropriate source:

- **BVH-Capture** for real-time motion data
- **BVH-Edit** for playback of recorded motion data

Configure the broadcast as follows:

| Setting | Value |
| --- | --- |
| Format | Binary |
| Old Header | Off |
| Skeleton | Axis Studio |
| Displacement | On |
| Transport | UDP |
| Destination Port | `8088` |
| Rotation | `YXZ` |

Set **Local Address** to the address of the computer running Axis Studio and
**Destination Address** to the address of the Linux computer running the
bridge. If both programs run on one computer, use the loopback address. Ensure
that the receiving firewall permits UDP port `8088`.

The rotation setting must match `--bvh-rotation`; both default to `YXZ`. If a
different destination port is necessary, change Axis Studio and pass the same
value through `--udp-port`.

![Axis Studio BVH broadcast settings](img/bvh_edit.png)

## Run the Wuji bridge

Start in dry-run mode:

```bash
python mocap_wuji_bridge.py
```

Dry-run is the default. The bridge connects to every discovered Wuji Hand 2
(one or two, at most one per side), reads whether each is a left or right
hand, selects the corresponding side(s) of the BVH avatar, and runs the
complete retargeting pipeline for each connected hand. It does not configure
the motors, enable the hands, or publish joint commands.

If more than one avatar is available, or you want specific hands, choose
explicitly (`--hand-sn` may be repeated to pin both hands):

```bash
python mocap_wuji_bridge.py \
  --avatar-name "AvatarName" \
  --hand-sn "LEFT_HAND_SERIAL" \
  --hand-sn "RIGHT_HAND_SERIAL"
```

Only after dry-run reports valid, continuously updating frames should motor
control be enabled:

```bash
python mocap_wuji_bridge.py --enable-motors
```

The motor-enabled path waits for 30 valid mocap frames, verifies all 20 joints
of every connected hand, enables the hands, and blends from their current
joint positions into the retargeted pose. Keep the physical workspace clear
and be ready to remove power.

Motor control fails closed:

- A valid, fresh mocap frame is required at all times.
- During preflight (before motors are armed) a tracking gap only restarts
  calibration; once preflight completes, 250 ms without a fresh frame
  disables the hands and exits nonzero.
- Mocap, retargeting, publishing, or device errors also disable the hands.
- `Ctrl+C` disables and disconnects the hands.
- The process never reconnects or re-enables automatically; restart it
  explicitly after correcting the problem.

Run `python mocap_wuji_bridge.py --help` for the complete option list.

## Troubleshooting

### No mocap frames

- Confirm that BVH broadcasting is enabled for the correct source:
  BVH-Capture for live capture or BVH-Edit for recorded playback.
- Check the destination address, UDP port, firewall, Binary format, and that
  **Old Header** is off.
- Make sure the Axis rotation order matches `--bvh-rotation`.
- Do not pass a `.bvh` path to the bridge; play the file through Axis Studio.

### No hand or ambiguous hand

- Confirm that at least one Wuji Hand 2 is connected and discoverable.
- The bridge derives each BVH side from the physical hand's handedness; it
  does not arbitrarily choose left or right.
- Two hands of the same side (or more than two devices) are rejected; pass
  `--hand-sn` (repeatable) to select the ones to use.

### Interface or native-library errors

Check the installed distributions:

```bash
python -c 'from importlib.metadata import version; print("mocap_api", version("mocap_api")); print("wuji-sdk", version("wuji-sdk")); print("numpy", version("numpy"))'
```

The expected versions are `mocap_api 0.1.5`, `wuji-sdk 2026.7.21`, and
`numpy 2.2.6`. An `InterfaceIncompatible` error, missing global-pose call, or
unexpected library path usually means an older MocapApi wrapper or native
library is being imported. Re-activate the environment, reinstall
`./MocapApi`, and remove any external `LD_LIBRARY_PATH` entry that forces an
older `libMocapApi.so`.

### Motion is rejected or fingers do not move

- Enable **Displacement** and use the **Axis Studio** skeleton.
- Confirm that finger tracking is available and enabled in the Axis setup.
- Verify that dry-run reports changing poses before enabling motors.

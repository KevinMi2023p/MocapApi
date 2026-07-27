# Native MocapApi-to-Wuji bridge

`mocap_wuji_bridge` is a C++17 bridge from Axis Studio BVH data to a Wuji Hand
2. It uses the native MocapApi v73 library bundled in this repository and the
C interface from Wuji's prebuilt SDK; Python is not involved.

Motor output is opt-in. Start with the receive-only and dry-run checks below,
with the physical hand clear and its emergency stop within reach.

## Prerequisites

- Linux on x86-64 or ARM64
- CMake 3.16 or newer and a C++17 compiler
- The Wuji C SDK **2026.7.21** Linux tarball for the host architecture
- `libusb-1.0.so.0` and the normal C++ runtime

Extract the Wuji tarball without rearranging it. Its layout must include:

```text
wuji-sdk-c-2026.7.21-<target>/
├── include/wuji_sdk.h
└── lib/
    ├── libwuji_sdk_c.so
    └── libwujihandcpp.so
```

`libwujihandcpp.so` must remain beside `libwuji_sdk_c.so`.

## Build

From the MocapApi repository root:

```bash
WUJI_SDK_ROOT=/absolute/path/to/wuji-sdk-c-2026.7.21-x86_64-linux-gnu

cmake -S demo/mocap-wuji-cpp -B demo/mocap-wuji-cpp/build \
  -DWUJI_SDK_ROOT="$WUJI_SDK_ROOT"
cmake --build demo/mocap-wuji-cpp/build -j
```

Use the `aarch64-linux-gnu` tarball on ARM64. CMake selects the matching
bundled MocapApi library automatically. If the Wuji include and library are
stored separately, configure with:

```bash
cmake -S demo/mocap-wuji-cpp -B demo/mocap-wuji-cpp/build \
  -DWUJI_SDK_INCLUDE_DIR=/absolute/path/to/include \
  -DWUJI_SDK_LIB=/absolute/path/to/lib/libwuji_sdk_c.so
```

The executable's build RPATH contains both native-library directories, so
`LD_LIBRARY_PATH` is not normally needed.

## Axis Studio network settings

Configure Axis Studio's BVH broadcast to match the known-working C++ MocapApi
demo:

| Setting | Value |
|---|---|
| Data format | Binary |
| Old header | Off |
| Displacement | On |
| Destination port | `8088` |
| Rotation order | `YXZ` |
| Destination address | Linux bridge computer's hotspot-facing IPv4 address |

Find the receiving computer's address **while it is connected to the Axis
laptop's hotspot**:

```bash
ip -4 -brief address
```

Enter the IPv4 address on that hotspot interface as Axis Studio's destination,
not an address from Ethernet, another Wi-Fi network, or a previous hotspot
session. Start playback/live capture in Axis Studio before testing the bridge.

## Safe bring-up

First prove that native MocapApi receives a usable right-hand skeleton. This
mode does not connect to or command the Wuji hand:

```bash
./demo/mocap-wuji-cpp/build/mocap_wuji_bridge \
  --mocap-only --side right
```

Next run the full pipeline in its default dry-run mode:

```bash
./demo/mocap-wuji-cpp/build/mocap_wuji_bridge
```

Do not enable motors until the dry run reports a completed preflight and keeps
receiving stable frames. Then, with the hand clear:

```bash
./demo/mocap-wuji-cpp/build/mocap_wuji_bridge \
  --enable-motors
```

Press Ctrl+C to stop. The motor-enabled path disables the hand during cleanup.
It first establishes a disabled baseline, requires 30 valid tracking frames,
holds the measured joint pose during enable, and then blends into the retargeted
pose. During motion it fails closed on a 250 ms tracking gap, stale or faulted
Wuji telemetry, a stopped/offline motor, a non-finite command, or an abrupt
command jump.

## Why the previous run timed out

The existing working C++ MocapApi demo listens on UDP `8088` and interprets
BVH rotations as `YXZ`. The earlier Python bridge defaulted to UDP `7012` and
`XYZ`. With Axis transmitting to `8088`, a listener on `7012` receives no
`AvatarUpdated` event, which produces:

```text
Tracking stopped: no valid Axis hand frame arrived within 10 seconds
```

The port mismatch explains the lack of frames; the rotation-order mismatch
would make the resulting hand geometry incorrect after packets started
arriving. The timeout occurs before motor enable, so the safety preflight
correctly prevented any hand movement.

## Troubleshooting

Only one local MocapApi process should listen on UDP `8088`. Stop the original
C++ demo or any other bridge before starting this one, then check the socket:

```bash
ss -lunp '( sport = :8088 )'
```

Confirm that packets reach the Linux computer:

```bash
sudo tcpdump -ni any udp port 8088
```

- No packets: re-check the hotspot-facing destination IP, port `8088`, subnet,
  host firewall, and hotspot/client-isolation settings.
- Packets but no avatar frames: confirm Binary format, old header off,
  displacement on, `YXZ`, and that Axis Studio is actively streaming.
- Bind failure: another listener owns `8088`; stop it and retry.
- Mocap-only works but dry-run does not: check that exactly one Wuji Hand 2 is
  discoverable, or select the intended hand using the bridge's serial option.
- Dry-run works but the hand does not move: verify that `--enable-motors` was
  supplied and that preflight completed; inspect joint diagnostics and clear
  hardware faults before retrying.

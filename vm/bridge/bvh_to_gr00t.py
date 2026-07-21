#!/usr/bin/env python3
"""Bridge Axis Studio's BVH network stream into gr00t's G1 teleop stack.

Receives the standard BVH stream that Axis Studio (running in the attached
Windows VM) sends over unicast UDP or TCP, runs forward kinematics using the
Mocap Studio decoder in this repository, and publishes left/right wrist poses
to gr00t's ``StreamDataPolicy/raw_data`` ROS2 topic — the pre-IK ingestion
point consumed by ``groot.control.teleop.streamers.ros2_streamer`` when the
G1 control loop runs with ``--body-control-device ros2``.

Wire format matches ``groot.control.utils.ros_utils.ROSMsgPublisher``:
a msgpack-encoded dict carried in a ``std_msgs/ByteMultiArray``.

Modes:
  --probe        No ROS2 required. Prints frame rate and wrist positions so a
                 station can verify Axis Studio -> host connectivity first.
  (default)      Publishes to ROS2; requires rclpy + msgpack (a sourced gr00t
                 environment provides both).

Finger data is not published yet: gr00t expects Manus-convention (25, 4, 4)
finger transforms and a Noitom-glove mapping has not been validated. The
G1 IK consumes whatever subset of keys is present, so wrist-only is valid.
"""

from __future__ import annotations

import argparse
import functools
import socket
import sys
import time
from pathlib import Path

# Status lines must survive pipes/service logs, not sit in a block buffer.
print = functools.partial(print, flush=True)  # noqa: A001

# Allow running from a checkout without installing the studio backend.
_STUDIO_BACKEND = Path(__file__).resolve().parents[2] / "studio" / "backend"
if _STUDIO_BACKEND.is_dir() and str(_STUDIO_BACKEND) not in sys.path:
    sys.path.insert(0, str(_STUDIO_BACKEND))

from mocap_studio.providers.base import MotionFrame, ProviderError  # noqa: E402
from mocap_studio.providers.bvh import BvhStreamDecoder, normalize_wire_frame  # noqa: E402

STREAM_DATA_POLICY_TOPIC = "StreamDataPolicy/raw_data"

# Basis change from the Axis/BVH world frame (right-handed, Y up, Z forward,
# X left) to the robot convention gr00t uses (X forward, Y left, Z up).
# Yaw alignment between the performer and the robot happens when the suit is
# calibrated facing the agreed capture direction; this only permutes axes.
AXES_PRESETS = {
    "y-up-z-forward": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "identity": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
}


def quaternion_to_matrix(q: tuple[float, float, float, float]) -> list[list[float]]:
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def _mat3_mul(a, b):
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
        for i in range(3)
    ]


def _mat3_vec(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def _transpose(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


def wrist_transform(
    position: tuple[float, float, float],
    rotation: tuple[float, float, float, float],
    axes: tuple[tuple[float, ...], ...],
) -> list[list[float]]:
    """Build a 4x4 robot-frame transform from a BVH-frame joint pose."""
    c = [list(row) for row in axes]
    r = _mat3_mul(_mat3_mul(c, quaternion_to_matrix(rotation)), _transpose(c))
    p = _mat3_vec(c, list(position))
    return [
        [r[0][0], r[0][1], r[0][2], p[0]],
        [r[1][0], r[1][1], r[1][2], p[1]],
        [r[2][0], r[2][1], r[2][2], p[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


class BvhReceiver:
    """Receive Axis Studio BVH frames over UDP (server) or TCP (client)."""

    def __init__(self, transport: str, host: str, port: int) -> None:
        self.transport = transport
        self.decoder = BvhStreamDecoder()
        if transport == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((host or "0.0.0.0", port))
        else:
            self.sock = socket.create_connection((host, port), timeout=10.0)
        self.sock.settimeout(1.0)

    def frames(self):
        while True:
            try:
                if self.transport == "udp":
                    data, _ = self.sock.recvfrom(65535)
                    wire_frames = self.decoder.feed(data, datagram=True)
                else:
                    data = self.sock.recv(65536)
                    if not data:
                        raise ConnectionError("TCP stream closed by Axis Studio")
                    wire_frames = self.decoder.feed(data)
            except socket.timeout:
                yield None
                continue
            except ProviderError as exc:
                print(f"[bridge] dropped malformed data: {exc}", file=sys.stderr)
                continue
            for wf in wire_frames:
                yield wf


class RosWristPublisher:
    """Minimal reimplementation of gr00t's ROSMsgPublisher wire format."""

    def __init__(self, topic: str) -> None:
        import msgpack  # deferred: only needed when actually publishing
        import rclpy
        from std_msgs.msg import ByteMultiArray

        self._msgpack = msgpack
        self._byte_multi_array = ByteMultiArray
        if not rclpy.ok():
            rclpy.init()
        self.node = rclpy.create_node("bvh_to_gr00t_bridge")
        self.publisher = self.node.create_publisher(ByteMultiArray, topic, 1)

    def publish(self, msg: dict) -> None:
        payload = self._msgpack.packb(msg)
        ros_msg = self._byte_multi_array()
        ros_msg.data = tuple(bytes([b]) for b in payload)
        self.publisher.publish(ros_msg)


def extract_wrists(frame: MotionFrame) -> dict[str, tuple]:
    wrists = {}
    for joint in frame.joints:
        if joint.name == "LeftHand":
            wrists["left"] = (joint.position, joint.rotation)
        elif joint.name == "RightHand":
            wrists["right"] = (joint.position, joint.rotation)
    return wrists


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transport", choices=("udp", "tcp"), default="udp",
                        help="udp: listen for Axis unicast datagrams (default); "
                             "tcp: connect to Axis Studio's BVH TCP server")
    parser.add_argument("--host", default="0.0.0.0",
                        help="UDP bind address, or Axis Studio guest IP for TCP")
    parser.add_argument("--port", type=int, default=7012,
                        help="BVH stream port (default: 7012)")
    parser.add_argument("--rotation-order", default="YXZ",
                        choices=("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"),
                        help="Must match Axis Studio's BVH rotation setting (default: YXZ)")
    parser.add_argument("--unit", default="centimeters",
                        choices=("centimeters", "meters"),
                        help="Must match Axis Studio's BVH displacement unit")
    parser.add_argument("--axes", default="y-up-z-forward", choices=sorted(AXES_PRESETS),
                        help="World-frame conversion applied to wrist poses")
    parser.add_argument("--topic", default=STREAM_DATA_POLICY_TOPIC,
                        help=f"ROS2 topic (default: {STREAM_DATA_POLICY_TOPIC})")
    parser.add_argument("--max-rate", type=float, default=0.0,
                        help="Cap publish rate in Hz (0 = publish every frame)")
    parser.add_argument("--probe", action="store_true",
                        help="Print stream statistics instead of publishing to ROS2")
    args = parser.parse_args()

    if args.transport == "tcp" and args.host in ("", "0.0.0.0"):
        parser.error("--transport tcp requires --host <axis-studio-guest-ip>")

    receiver = BvhReceiver(args.transport, args.host, args.port)
    publisher = None if args.probe else RosWristPublisher(args.topic)
    axes = AXES_PRESETS[args.axes]
    min_interval = 1.0 / args.max_rate if args.max_rate > 0 else 0.0

    where = f"{args.host}:{args.port}/{args.transport}"
    print(f"[bridge] waiting for Axis Studio BVH frames on {where} "
          f"({'probe mode' if args.probe else f'publishing to {args.topic}'})")

    frame_number = 0
    frames_in_window = 0
    window_start = time.monotonic()
    last_publish = 0.0
    last_wrists: dict[str, tuple] = {}

    try:
        for wire_frame in receiver.frames():
            now = time.monotonic()
            if wire_frame is not None:
                frame_number += 1
                frames_in_window += 1
                try:
                    frame = normalize_wire_frame(
                        wire_frame,
                        frame_number,
                        rotation_order=args.rotation_order,
                        source_unit=args.unit,
                    )
                except ProviderError as exc:
                    print(f"[bridge] dropped frame: {exc}", file=sys.stderr)
                    continue
                wrists = extract_wrists(frame)
                if wrists:
                    last_wrists = wrists
                if publisher and wrists and now - last_publish >= min_interval:
                    msg = {}
                    if "left" in wrists:
                        msg["left_wrist"] = wrist_transform(*wrists["left"], axes)
                    if "right" in wrists:
                        msg["right_wrist"] = wrist_transform(*wrists["right"], axes)
                    publisher.publish(msg)
                    last_publish = now

            if now - window_start >= 2.0:
                fps = frames_in_window / (now - window_start)
                if fps > 0 and last_wrists:
                    positions = " ".join(
                        f"{side}=({p[0][0]:+.2f},{p[0][1]:+.2f},{p[0][2]:+.2f})m"
                        for side, p in sorted(last_wrists.items())
                    )
                    print(f"[bridge] {fps:5.1f} fps  wrists(BVH frame): {positions}")
                elif fps == 0:
                    print("[bridge] no frames received; check Axis Studio "
                          "broadcast target, port, and Windows firewall")
                frames_in_window = 0
                window_start = now
    except KeyboardInterrupt:
        print("\n[bridge] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

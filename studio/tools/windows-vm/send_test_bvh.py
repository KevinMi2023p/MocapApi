#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Send a synthetic standard-BVH binary stream to Mocap Studio.

Hardware-free smoke sender for the Windows-VM streaming path. It emits the
same modern 64-byte binary frame envelope that Axis Studio's "BVH - Capture"
broadcast uses (Binary frame format, "use old header format" unchecked,
rotation order YXZ, displacement checked, centimeters), carrying a 59-joint
T-pose with a small sinusoidal arm wave and head nod. Use it to prove the
listener path before any Noitom hardware or Windows guest exists.

Mocap Studio settings to see the skeleton:

    mode:           bvh
    transport:      udp
    host:           127.0.0.1   (local test)
                    192.168.77.1 or 0.0.0.0 (receiving from the axis-studio VM)
    port:           7012
    rotation order: YXZ
    unit:           centimeters

This script is stdlib-only Python 3.10+ and also runs on Windows. To test the
guest-to-host leg from inside the axis-studio VM:

    python send_test_bvh.py --host 192.168.77.1

Transports:

- ``--transport udp`` (default): datagrams are sent to ``--host:--port``,
  one frame per datagram, exactly like Axis Studio's UDP broadcast.
- ``--transport tcp``: this script acts as the TCP *server* (Axis Studio's
  role); ``--host`` becomes the local bind address. Point Mocap Studio's
  TCP BVH provider at that address and port, then start this sender first.

Frame building is deliberately pure (no sockets) so the accompanying
``test_send_test_bvh.py`` can feed generated frames straight into the real
``mocap_studio.providers.bvh`` decoder.
"""

from __future__ import annotations

import argparse
import math
import socket
import struct
import sys
import time

# Modern Axis/Neuron 64-byte frame envelope, verbatim from
# studio/backend/mocap_studio/providers/bvh.py (MODERN_HEADER):
# start token, 4 version bytes, value count, displacement flag, reference
# flag, avatar index, 32-byte avatar name, source frame index, 3 reserved
# words, end token.
MODERN_HEADER = struct.Struct("<H4BHBBI32sIIIIH")
START_TOKEN = 0xDDFF
END_TOKEN = 0xEEFF
STREAM_VERSION = (2, 0, 0, 0)
AVATAR_INDEX = 0
MAX_AVATAR_NAME_BYTES = 31

# Synthetic motion shape. Zero time yields the exact template T-pose.
WAVE_DEGREES = 30.0
WAVE_HZ = 0.5
SWAY_CENTIMETERS = 5.0
SWAY_HZ = 0.25
NOD_DEGREES = 15.0
NOD_HZ = 0.5

# Official Noitom NeuronDataReader Appendix B template offsets in
# centimeters, in the public MocapApi enum order. This mirrors
# studio/backend/mocap_studio/skeleton.py (JOINTS, stored there in meters).
# The 60th entry, Spine3, is a later MocapApi extension absent from the
# classic 59-bone BVH hierarchy.
JOINT_OFFSETS_CM: tuple[tuple[str, tuple[float, float, float]], ...] = (
    ("Hips", (0.0, 84.102, 0.0)),
    ("RightUpLeg", (-9.5, 0.0, 0.0)),
    ("RightLeg", (0.0, -37.051, 0.0)),
    ("RightFoot", (0.0, -37.051, 0.0)),
    ("LeftUpLeg", (9.5, 0.0, 0.0)),
    ("LeftLeg", (0.0, -37.051, 0.0)),
    ("LeftFoot", (0.0, -37.051, 0.0)),
    ("Spine", (0.0, 7.14, 0.0)),
    ("Spine1", (0.0, 15.81, 0.0)),
    ("Spine2", (0.0, 11.22, 0.0)),
    ("Neck", (0.0, 16.83, 0.0)),
    ("Neck1", (0.0, 4.5, 0.0)),
    ("Head", (0.0, 4.5, 0.0)),
    ("RightShoulder", (-2.55, 11.73, 0.0)),
    ("RightArm", (-11.45, 0.0, 0.0)),
    ("RightForeArm", (-25.0, 0.0, 0.0)),
    ("RightHand", (-25.0, 0.0, 0.0)),
    ("RightHandThumb1", (-2.418, 0.185, 3.031)),
    ("RightHandThumb2", (-3.578, 0.0, 0.0)),
    ("RightHandThumb3", (-2.485, 0.0, 0.0)),
    ("RightInHandIndex", (-3.132, 0.494, 1.922)),
    ("RightHandIndex1", (-5.068, -0.089, 0.971)),
    ("RightHandIndex2", (-3.516, 0.0, 0.0)),
    ("RightHandIndex3", (-1.993, 0.0, 0.0)),
    ("RightInHandMiddle", (-3.285, 0.502, 0.735)),
    ("RightHandMiddle1", (-5.026, -0.082, 0.305)),
    ("RightHandMiddle2", (-3.837, 0.0, 0.0)),
    ("RightHandMiddle3", (-2.405, 0.0, 0.0)),
    ("RightInHandRing", (-3.269, 0.523, -0.125)),
    ("RightHandRing1", (-4.502, -0.021, -0.465)),
    ("RightHandRing2", (-3.344, 0.0, 0.0)),
    ("RightHandRing3", (-2.32, 0.0, 0.0)),
    ("RightInHandPinky", (-3.071, 0.456, -1.167)),
    ("RightHandPinky1", (-4.023, -0.021, -1.059)),
    ("RightHandPinky2", (-2.678, 0.0, 0.0)),
    ("RightHandPinky3", (-1.692, 0.0, 0.0)),
    ("LeftShoulder", (2.55, 11.73, 0.0)),
    ("LeftArm", (11.45, 0.0, 0.0)),
    ("LeftForeArm", (25.0, 0.0, 0.0)),
    ("LeftHand", (25.0, 0.0, 0.0)),
    ("LeftHandThumb1", (2.418, 0.185, 3.031)),
    ("LeftHandThumb2", (3.578, 0.0, 0.0)),
    ("LeftHandThumb3", (2.485, 0.0, 0.0)),
    ("LeftInHandIndex", (3.132, 0.494, 1.922)),
    ("LeftHandIndex1", (5.068, -0.089, 0.971)),
    ("LeftHandIndex2", (3.516, 0.0, 0.0)),
    ("LeftHandIndex3", (1.993, 0.0, 0.0)),
    ("LeftInHandMiddle", (3.285, 0.502, 0.735)),
    ("LeftHandMiddle1", (5.026, -0.082, 0.305)),
    ("LeftHandMiddle2", (3.837, 0.0, 0.0)),
    ("LeftHandMiddle3", (2.405, 0.0, 0.0)),
    ("LeftInHandRing", (3.269, 0.523, -0.125)),
    ("LeftHandRing1", (4.502, -0.021, -0.465)),
    ("LeftHandRing2", (3.344, 0.0, 0.0)),
    ("LeftHandRing3", (2.32, 0.0, 0.0)),
    ("LeftInHandPinky", (3.071, 0.456, -1.167)),
    ("LeftHandPinky1", (4.023, -0.021, -1.059)),
    ("LeftHandPinky2", (2.678, 0.0, 0.0)),
    ("LeftHandPinky3", (1.692, 0.0, 0.0)),
    ("Spine3", (0.0, 8.0, 0.0)),
)

_JOINT_INDEX = {name: index for index, (name, _) in enumerate(JOINT_OFFSETS_CM)}


def build_frame_values(time_seconds: float, joint_count: int = 59) -> list[float]:
    """Build one displacement-layout value block: 6 floats per joint.

    Per joint: local position X, Y, Z in centimeters, then rotation channels
    in wire order Y, X, Z (degrees), matching Axis Studio's YXZ broadcast.
    ``time_seconds == 0.0`` produces the exact Appendix B template T-pose.
    """
    if joint_count not in (59, 60):
        raise ValueError("joint_count must be 59 or 60")
    if not math.isfinite(time_seconds):
        raise ValueError("time_seconds must be finite")

    wave = WAVE_DEGREES * math.sin(2.0 * math.pi * WAVE_HZ * time_seconds)
    sway = SWAY_CENTIMETERS * math.sin(2.0 * math.pi * SWAY_HZ * time_seconds)
    nod = NOD_DEGREES * math.sin(2.0 * math.pi * NOD_HZ * time_seconds)
    right_arm = _JOINT_INDEX["RightArm"]
    left_arm = _JOINT_INDEX["LeftArm"]
    head = _JOINT_INDEX["Head"]

    values: list[float] = []
    for index, (_name, (px, py, pz)) in enumerate(JOINT_OFFSETS_CM[:joint_count]):
        rotation_y = rotation_x = rotation_z = 0.0
        if index == 0:
            px += sway
        elif index == right_arm:
            # The right arm points along -X; a negative Z rotation raises it
            # toward +Y so both arms wave upward together.
            rotation_z = -wave
        elif index == left_arm:
            rotation_z = wave
        elif index == head:
            # Head nod about X exercises the middle slot of the Y,X,Z wire
            # triple so channel-order regressions are testable.
            rotation_x = nod
        values.extend((px, py, pz, rotation_y, rotation_x, rotation_z))
    return values


def encode_avatar_name(name: str) -> bytes:
    """Validate and encode an avatar name into the 32-byte header field."""
    encoded = name.encode("utf-8")
    if not encoded:
        raise ValueError("Avatar name must not be empty")
    if len(encoded) > MAX_AVATAR_NAME_BYTES:
        raise ValueError(f"Avatar name must be at most {MAX_AVATAR_NAME_BYTES} UTF-8 bytes")
    if any(not character.isprintable() for character in name):
        raise ValueError("Avatar name must contain only printable characters")
    return encoded.ljust(32, b"\0")


def build_packet(
    values: list[float] | tuple[float, ...],
    *,
    avatar_index: int = AVATAR_INDEX,
    avatar_name: str = "PySender",
    frame_index: int = 1,
) -> bytes:
    """Wrap a value block in the modern 64-byte binary frame envelope."""
    header = MODERN_HEADER.pack(
        START_TOKEN,
        *STREAM_VERSION,
        len(values),
        1,  # with displacement: every joint carries a local position
        0,  # no reference bone
        avatar_index,
        encode_avatar_name(avatar_name),
        frame_index,
        0,
        0,
        0,
        END_TOKEN,
    )
    return header + struct.pack(f"<{len(values)}f", *values)


def build_frame(
    frame_index: int,
    time_seconds: float,
    *,
    avatar_name: str = "PySender",
    joint_count: int = 59,
) -> bytes:
    """Build the complete wire packet for one animation frame."""
    return build_packet(
        build_frame_values(time_seconds, joint_count),
        avatar_name=avatar_name,
        frame_index=frame_index,
    )


def _positive_float(text: str) -> float:
    try:
        value = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number") from error
    if not math.isfinite(value) or value <= 0.0:
        raise argparse.ArgumentTypeError("value must be a finite positive number")
    return value


def _port_number(text: str) -> int:
    try:
        value = int(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{text!r} is not an integer") from error
    if not 1 <= value <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return value


def _avatar_name(text: str) -> str:
    try:
        encode_avatar_name(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    return text


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a synthetic standard-BVH binary stream to Mocap Studio.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="UDP destination address, or TCP bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=_port_number, default=7012, help="destination port (default: 7012)"
    )
    parser.add_argument(
        "--fps", type=_positive_float, default=60.0, help="frames per second (default: 60)"
    )
    parser.add_argument(
        "--seconds",
        type=_positive_float,
        default=10.0,
        help="stream duration in seconds (default: 10)",
    )
    parser.add_argument(
        "--avatar-name",
        type=_avatar_name,
        default="PySender",
        help="avatar name in the frame header (default: PySender)",
    )
    parser.add_argument(
        "--transport",
        choices=("udp", "tcp"),
        default="udp",
        help="udp sends datagrams; tcp acts as the server Mocap Studio connects to",
    )
    parser.add_argument(
        "--joints",
        type=int,
        choices=(59, 60),
        default=59,
        help="joint count: 59 (classic BVH hierarchy) or 60 (with Spine3)",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    total_frames = max(1, round(args.fps * args.seconds))
    interval = 1.0 / args.fps
    connection: socket.socket | None = None

    if args.transport == "udp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        destination = (args.host, args.port)

        def send(payload: bytes) -> None:
            sock.sendto(payload, destination)

        print(f"Sending UDP BVH frames to {args.host}:{args.port}")
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((args.host, args.port))
        except OSError as error:
            print(f"error: cannot bind {args.host}:{args.port}: {error}", file=sys.stderr)
            sock.close()
            return 1
        sock.listen(1)
        print(
            f"Waiting for a TCP connection on {args.host}:{args.port} "
            "(connect Mocap Studio's TCP BVH provider now)"
        )
        try:
            connection, peer = sock.accept()
        except KeyboardInterrupt:
            print("\nInterrupted while waiting for a connection.")
            sock.close()
            return 0
        print(f"Peer connected from {peer[0]}:{peer[1]}")

        def send(payload: bytes) -> None:
            connection.sendall(payload)

    frames_sent = 0
    aborted = False
    started = time.monotonic()
    try:
        for frame in range(total_frames):
            packet = build_frame(
                frame + 1,
                frame * interval,
                avatar_name=args.avatar_name,
                joint_count=args.joints,
            )
            send(packet)
            frames_sent += 1
            deadline = started + (frame + 1) * interval
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except ConnectionError:
        aborted = True
        print(
            f"Peer closed the connection after {frames_sent}/{total_frames} frames.",
            file=sys.stderr,
        )
    finally:
        if connection is not None:
            connection.close()
        sock.close()

    elapsed = max(time.monotonic() - started, 1e-9)
    print(
        f"Sent {frames_sent} frames ({args.joints} joints, displacement, YXZ) "
        f"in {elapsed:.1f}s ({frames_sent / elapsed:.1f} fps)"
    )
    return 1 if aborted else 0


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 KevinMi2023p
"""Integration tests: synthetic sender frames against the real BVH decoder.

Feeds packets built by send_test_bvh (pure functions, no sockets) into
mocap_studio.providers.bvh so the smoke sender can never drift from what
the backend actually accepts.
"""

from __future__ import annotations

import contextlib
import io
import math
import sys
import unittest
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(TOOL_DIR))
sys.path.insert(0, str(BACKEND_DIR))

import send_test_bvh  # noqa: E402
from mocap_studio.providers.bvh import (  # noqa: E402
    BvhStreamDecoder,
    LegacyBinaryDecoder,
    normalize_wire_frame,
)
from mocap_studio.skeleton import JOINTS, euler_to_quaternion  # noqa: E402


class OffsetTableTests(unittest.TestCase):
    def test_offsets_match_backend_skeleton(self) -> None:
        self.assertEqual(len(send_test_bvh.JOINT_OFFSETS_CM), len(JOINTS))
        for (name, offset_cm), definition in zip(
            send_test_bvh.JOINT_OFFSETS_CM, JOINTS, strict=True
        ):
            with self.subTest(joint=name):
                self.assertEqual(name, definition.name)
                for centimeters, meters in zip(offset_cm, definition.offset, strict=True):
                    self.assertAlmostEqual(centimeters, meters * 100.0, places=6)


class FrameBuilderTests(unittest.TestCase):
    def test_value_block_shape_and_finiteness(self) -> None:
        for joint_count in (59, 60):
            with self.subTest(joint_count=joint_count):
                values = send_test_bvh.build_frame_values(0.25, joint_count)
                self.assertEqual(len(values), joint_count * 6)
                self.assertTrue(all(math.isfinite(value) for value in values))

    def test_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            send_test_bvh.build_frame_values(0.0, 58)
        with self.assertRaises(ValueError):
            send_test_bvh.build_frame_values(math.nan)
        with self.assertRaises(ValueError):
            send_test_bvh.build_packet([0.0] * 6, avatar_name="")
        with self.assertRaises(ValueError):
            send_test_bvh.build_packet([0.0] * 6, avatar_name="x" * 32)

    def test_zero_time_is_exact_template_t_pose(self) -> None:
        values = send_test_bvh.build_frame_values(0.0)
        for index, (name, offset_cm) in enumerate(send_test_bvh.JOINT_OFFSETS_CM[:59]):
            block = values[index * 6 : index * 6 + 6]
            with self.subTest(joint=name):
                self.assertEqual(tuple(block[:3]), offset_cm)
                self.assertEqual(tuple(block[3:]), (0.0, 0.0, 0.0))


class DecoderIntegrationTests(unittest.TestCase):
    def decode_one(self, packet: bytes):
        frames = LegacyBinaryDecoder().feed(packet)
        self.assertEqual(len(frames), 1)
        return frames[0]

    def test_packet_parses_with_modern_header_fields(self) -> None:
        packet = send_test_bvh.build_frame(7, 0.5, avatar_name="PySender")
        wire = self.decode_one(packet)
        self.assertEqual(wire.avatar_name, "PySender")
        self.assertEqual(wire.avatar_index, send_test_bvh.AVATAR_INDEX)
        self.assertEqual(wire.source_frame_index, 7)
        self.assertEqual(wire.version, send_test_bvh.STREAM_VERSION)
        self.assertIs(wire.with_displacement, True)
        self.assertIs(wire.with_reference, False)
        self.assertEqual(len(wire.values), 59 * 6)

    def test_udp_datagram_path(self) -> None:
        packet = send_test_bvh.build_frame(1, 0.0)
        frames = BvhStreamDecoder().feed(packet, datagram=True)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].source_frame_index, 1)

    def test_tcp_stream_path_with_fragmentation(self) -> None:
        stream = b"".join(
            send_test_bvh.build_frame(index + 1, index / 60.0) for index in range(3)
        )
        decoder = BvhStreamDecoder()
        frames = []
        for offset in range(0, len(stream), 100):
            frames.extend(decoder.feed(stream[offset : offset + 100]))
        self.assertEqual([frame.source_frame_index for frame in frames], [1, 2, 3])

    def test_normalize_produces_grounded_t_pose(self) -> None:
        wire = self.decode_one(send_test_bvh.build_frame(1, 0.0))
        motion = normalize_wire_frame(
            wire, 1, rotation_order="YXZ", source_unit="centimeters"
        )
        self.assertEqual(motion.avatar_name, "PySender")
        self.assertEqual(len(motion.joints), 59)
        for joint, definition in zip(motion.joints, JOINTS[:59], strict=True):
            self.assertEqual(joint.name, definition.name)
        positions = {joint.name: joint.position for joint in motion.joints}
        for name, expected in {
            "Hips": (0.0, 0.84102, 0.0),
            "RightFoot": (-0.095, 0.1, 0.0),
            "LeftFoot": (0.095, 0.1, 0.0),
            "RightHand": (-0.64, 1.30002, 0.0),
            "Head": (0.0, 1.44102, 0.0),
        }.items():
            for actual, wanted in zip(positions[name], expected, strict=True):
                self.assertAlmostEqual(actual, wanted, places=5)

    def test_arm_wave_uses_yxz_wire_channels(self) -> None:
        # At t = 0.5 s with WAVE_HZ 0.5 the wave peaks at WAVE_DEGREES and
        # the head nod (NOD_HZ 0.5) peaks at NOD_DEGREES.
        time_seconds = 0.5
        nod = send_test_bvh.NOD_DEGREES * math.sin(
            2.0 * math.pi * send_test_bvh.NOD_HZ * time_seconds
        )
        self.assertGreater(abs(nod), 1.0)  # guard: the nod must be nonzero

        # Raw wire check: the head nod rotates about X only, so it must land
        # in the middle slot of the Y,X,Z wire triple. Swapping the channel
        # order in the sender moves it and fails these assertions.
        values = send_test_bvh.build_frame_values(time_seconds)
        head = send_test_bvh._JOINT_INDEX["Head"]
        wire_y, wire_x, wire_z = values[head * 6 + 3 : head * 6 + 6]
        self.assertEqual(wire_y, 0.0)
        self.assertAlmostEqual(wire_x, nod, places=9)
        self.assertEqual(wire_z, 0.0)

        wire = self.decode_one(send_test_bvh.build_frame(2, time_seconds))
        motion = normalize_wire_frame(
            wire, 2, rotation_order="YXZ", source_unit="centimeters"
        )
        rotations = {joint.name: joint.rotation for joint in motion.joints}
        # Every ancestor of RightArm has identity rotation, so its global
        # rotation equals the local wire rotation: -WAVE_DEGREES about Z.
        expected = euler_to_quaternion((0.0, 0.0, -send_test_bvh.WAVE_DEGREES), "YXZ")
        for actual, wanted in zip(rotations["RightArm"], expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=5)
        expected = euler_to_quaternion((0.0, 0.0, send_test_bvh.WAVE_DEGREES), "YXZ")
        for actual, wanted in zip(rotations["LeftArm"], expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=5)
        # Head's ancestors are also identity; the decoder must read the nod
        # from wire position 1 as an X rotation (euler tuple order is X,Y,Z).
        expected = euler_to_quaternion((nod, 0.0, 0.0), "YXZ")
        for actual, wanted in zip(rotations["Head"], expected, strict=True):
            self.assertAlmostEqual(actual, wanted, places=5)

    def test_60_joint_variant_parses_and_normalizes(self) -> None:
        wire = self.decode_one(send_test_bvh.build_frame(1, 0.0, joint_count=60))
        motion = normalize_wire_frame(
            wire, 1, rotation_order="YXZ", source_unit="centimeters"
        )
        self.assertEqual(len(motion.joints), 60)
        self.assertEqual(motion.joints[-1].name, "Spine3")

    def test_animated_stream_stays_decodable(self) -> None:
        decoder = LegacyBinaryDecoder()
        for index in range(120):
            frames = decoder.feed(send_test_bvh.build_frame(index + 1, index / 60.0))
            self.assertEqual(len(frames), 1)
            normalize_wire_frame(frames[0], index + 1)


class ArgumentValidationTests(unittest.TestCase):
    def parse(self, argv: list[str]):
        return send_test_bvh.parse_args(argv)

    def assert_rejected(self, argv: list[str]) -> None:
        with self.subTest(argv=argv):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                self.parse(argv)
            self.assertNotEqual(caught.exception.code, 0)

    def test_defaults_match_stream_contract(self) -> None:
        args = self.parse([])
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 7012)
        self.assertEqual(args.fps, 60.0)
        self.assertEqual(args.seconds, 10.0)
        self.assertEqual(args.avatar_name, "PySender")
        self.assertEqual(args.transport, "udp")
        self.assertEqual(args.joints, 59)

    def test_valid_overrides_are_accepted(self) -> None:
        args = self.parse(
            [
                "--host", "192.168.77.1",
                "--port", "7012",
                "--fps", "120",
                "--seconds", "0.5",
                "--avatar-name", "Guest01",
                "--transport", "tcp",
                "--joints", "60",
            ]
        )
        self.assertEqual(args.host, "192.168.77.1")
        self.assertEqual(args.transport, "tcp")
        self.assertEqual(args.joints, 60)

    def test_invalid_arguments_are_rejected(self) -> None:
        self.assert_rejected(["--port", "0"])
        self.assert_rejected(["--port", "65536"])
        self.assert_rejected(["--port", "not-a-port"])
        self.assert_rejected(["--fps", "0"])
        self.assert_rejected(["--fps", "-30"])
        self.assert_rejected(["--fps", "nan"])
        self.assert_rejected(["--seconds", "0"])
        self.assert_rejected(["--avatar-name", ""])
        self.assert_rejected(["--avatar-name", "x" * 32])
        self.assert_rejected(["--transport", "carrier-pigeon"])
        self.assert_rejected(["--joints", "58"])


if __name__ == "__main__":
    unittest.main()

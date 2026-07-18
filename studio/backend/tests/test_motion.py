from __future__ import annotations

import math
import struct
import threading
import time
import unittest

from mocap_studio.providers.base import ProviderError
from mocap_studio.providers.base import ProviderConnectionError
from mocap_studio.providers.bvh import (
    LEGACY_END_TOKEN,
    LEGACY_HEADER,
    LEGACY_START_TOKEN,
    MODERN_HEADER,
    BvhStreamDecoder,
    BvhStringDecoder,
    LegacyBinaryDecoder,
    WireFrame,
    normalize_wire_frame,
    parse_string_frame,
)
from mocap_studio.providers.demo import DemoProvider
from mocap_studio.skeleton import (
    JOINTS,
    euler_to_quaternion,
    finite_values,
    multiply_quaternion,
    normalize_quaternion,
    rotate_vector,
)


def old_packet(values: list[float], *, avatar: int = 3, name: str = "Actor") -> bytes:
    encoded = name.encode()[:31].ljust(32, b"\0")
    header = LEGACY_HEADER.pack(
        LEGACY_START_TOKEN, 1, 0, 0, 0, len(values), 0, 0, avatar, encoded, 0, 0,
        LEGACY_END_TOKEN,
    )
    return header + struct.pack(f"<{len(values)}f", *values)


def modern_packet(
    values: list[float], *, avatar: int = 4, name: str = "Modern", frame: int = 101
) -> bytes:
    encoded = name.encode()[:31].ljust(32, b"\0")
    header = MODERN_HEADER.pack(
        LEGACY_START_TOKEN, 2, 0, 0, 0, len(values), 0, 0, avatar, encoded,
        frame, 0, 0, 0, LEGACY_END_TOKEN,
    )
    return header + struct.pack(f"<{len(values)}f", *values)


class QuaternionTests(unittest.TestCase):
    def assertVectorAlmostEqual(self, actual, expected):  # noqa: N802
        for left, right in zip(actual, expected, strict=True):
            self.assertAlmostEqual(left, right, places=7)

    def test_normalize_zero_and_multiply_identity(self) -> None:
        self.assertEqual(normalize_quaternion((0, 0, 0, 0)), (0, 0, 0, 1))
        q = euler_to_quaternion((35, -20, 10), "XYZ")
        self.assertAlmostEqual(sum(value * value for value in q), 1.0)
        self.assertVectorAlmostEqual(multiply_quaternion((0, 0, 0, 1), q), q)

    def test_axis_rotations_rotate_vectors(self) -> None:
        qx = euler_to_quaternion((90, 0, 0), "XYZ")
        qz = euler_to_quaternion((0, 0, 90), "XYZ")
        self.assertVectorAlmostEqual(rotate_vector(qx, (0, 1, 0)), (0, 0, 1))
        self.assertVectorAlmostEqual(rotate_vector(qz, (1, 0, 0)), (0, 1, 0))

    def test_finite_value_guard(self) -> None:
        self.assertTrue(finite_values([0.0, -1_000_000.0]))
        self.assertFalse(finite_values([math.nan]))
        self.assertFalse(finite_values([math.inf]))
        self.assertFalse(finite_values([1_000_001.0]))


class StringDecoderTests(unittest.TestCase):
    def test_parse_and_incremental_coalesced_frames(self) -> None:
        parsed = parse_string_frame(b"7 Actor 1, 2, 3")
        self.assertEqual((parsed.avatar_index, parsed.avatar_name), (7, "Actor"))
        self.assertEqual(parsed.values, (1.0, 2.0, 3.0))
        decoder = BvhStringDecoder()
        self.assertEqual(decoder.feed(b"1 A 0 1"), [])
        frames = decoder.feed(b" 2 || 2 B 3 4 5 ||")
        self.assertEqual([frame.avatar_name for frame in frames], ["A", "B"])

    def test_delimiterless_datagram(self) -> None:
        frames = BvhStringDecoder().feed(b"1 A 0 1 2", datagram=True)
        self.assertEqual(len(frames), 1)

    def test_rejects_malformed_and_nonfinite(self) -> None:
        for payload in (b"1 A 1", b"1 A x 2 3", b"1 A nan 2 3", b"\xff A 1 2 3"):
            with self.subTest(payload=payload), self.assertRaises(ProviderError):
                parse_string_frame(payload)


class BinaryDecoderTests(unittest.TestCase):
    def test_both_headers_fragment_and_coalesce(self) -> None:
        first = old_packet([float(index) for index in range(180)])
        second = modern_packet([0.0] * 183, frame=177)
        decoder = LegacyBinaryDecoder()
        frames = []
        combined = first + second
        for offset in range(0, len(combined), 7):
            frames.extend(decoder.feed(combined[offset : offset + 7]))
        self.assertEqual(len(frames), 2)
        self.assertIsNone(frames[0].source_frame_index)
        self.assertEqual(frames[1].source_frame_index, 177)
        self.assertEqual(frames[1].version, (2, 0, 0, 0))

    def test_tcp_format_persists_across_one_byte_start_fragment(self) -> None:
        packet = modern_packet([0.0] * 180, frame=22)
        decoder = BvhStreamDecoder()
        self.assertEqual(decoder.feed(packet[:1]), [])
        self.assertEqual(decoder.feed(packet[1:53]), [])
        frames = decoder.feed(packet[53:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].source_frame_index, 22)

    def test_truncated_udp_datagram_does_not_contaminate_next_packet(self) -> None:
        decoder = BvhStreamDecoder()
        truncated = modern_packet([0.0] * 180, frame=21)[:80]
        self.assertEqual(decoder.feed(truncated, datagram=True), [])
        frames = decoder.feed(modern_packet([0.0] * 180, frame=22), datagram=True)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].source_frame_index, 22)

        malformed = bytearray(old_packet([0.0] * 180)[: LEGACY_HEADER.size])
        struct.pack_into("<I", malformed, 6, 0)
        with self.assertRaises(ProviderError):
            decoder.feed(bytes(malformed), datagram=True)
        frames = decoder.feed(modern_packet([0.0] * 180, frame=23), datagram=True)
        self.assertEqual(frames[0].source_frame_index, 23)

    def test_resynchronizes_garbage_and_recovers_after_bad_header(self) -> None:
        good = old_packet([0.0] * 180)
        decoder = LegacyBinaryDecoder()
        self.assertEqual(len(decoder.feed(b"garbage" + good)), 1)

        bad = bytearray(old_packet([0.0] * 180)[:64])
        struct.pack_into("<I", bad, 6, 0)
        decoder = LegacyBinaryDecoder()
        with self.assertRaises(ProviderError):
            decoder.feed(bytes(bad))
        self.assertEqual(len(decoder.feed(good)), 1)

    def test_rejects_nonfinite_body(self) -> None:
        packet = old_packet([math.nan] + [0.0] * 179)
        with self.assertRaises(ProviderError):
            LegacyBinaryDecoder().feed(packet)


class ScriptedSocket:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def recv(self, _size):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class BvhProviderLifecycleTests(unittest.TestCase):
    def provider(self, transport: str):
        from mocap_studio.providers.bvh import BvhProvider

        return BvhProvider(transport=transport, host="127.0.0.1", port=7012)

    def test_tcp_eof_and_socket_error_are_terminal(self) -> None:
        for result in (b"", OSError("reset")):
            with self.subTest(result=result):
                provider = self.provider("tcp")
                sock = ScriptedSocket([result])
                provider._socket = sock
                errors = []
                provider._run(lambda _frame: self.fail("unexpected frame"), errors.append)
                self.assertEqual(sock.calls, 1)
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], ProviderConnectionError)
                self.assertTrue(provider._stop.is_set())

    def test_malformed_udp_is_nonfatal_and_next_frame_arrives(self) -> None:
        provider = self.provider("udp")
        sock = ScriptedSocket([b"bad packet", modern_packet([0.0] * 180, frame=9)])
        provider._socket = sock
        errors = []
        frames = []

        def receive(frame):
            frames.append(frame)
            provider._stop.set()

        provider._run(receive, errors.append)
        self.assertEqual(sock.calls, 2)
        self.assertEqual(len(errors), 1)
        self.assertNotIsInstance(errors[0], ProviderConnectionError)
        self.assertEqual(frames[0].source_frame_index, 9)


class NormalizeTests(unittest.TestCase):
    @staticmethod
    def rotations(joint_count: int) -> tuple[float, ...]:
        return (100.0, 200.0, 300.0) + (0.0,) * (joint_count * 3)

    def test_normalizes_59_and_60_joint_rotation_layouts(self) -> None:
        for count in (59, 60):
            with self.subTest(count=count):
                motion = normalize_wire_frame(
                    WireFrame(1, "Actor", self.rotations(count), 99), 8
                )
                self.assertEqual(len(motion.joints), count)
                self.assertEqual(motion.joints[0].position, (1.0, 2.84102, 3.0))
                self.assertEqual(motion.joints[-1].name, JOINTS[count - 1].name)

    def test_official_appendix_b_offsets_and_zero_pose_fk(self) -> None:
        definitions = {joint.name: joint for joint in JOINTS}
        self.assertEqual(definitions["Hips"].offset, (0.0, 0.84102, 0.0))
        self.assertEqual(definitions["RightUpLeg"].offset, (-0.095, 0.0, 0.0))
        self.assertEqual(definitions["LeftUpLeg"].offset, (0.095, 0.0, 0.0))
        self.assertEqual(definitions["RightLeg"].offset, (0.0, -0.37051, 0.0))
        self.assertEqual(
            definitions["RightHandIndex1"].offset,
            (-0.05068, -0.00089, 0.00971),
        )
        self.assertEqual(
            definitions["LeftHandIndex1"].offset,
            (0.05068, -0.00089, 0.00971),
        )
        self.assertEqual(definitions["Spine3"].offset, (0.0, 0.08, 0.0))

        motion = normalize_wire_frame(
            WireFrame(1, "Actor", (0.0,) * 180, 0), 1, source_unit="meters"
        )
        poses = {joint.name: joint.position for joint in motion.joints}
        for name, expected in {
            "Hips": (0.0, 0.84102, 0.0),
            "RightFoot": (-0.095, 0.1, 0.0),
            "LeftFoot": (0.095, 0.1, 0.0),
            "RightHand": (-0.64, 1.30002, 0.0),
            "Head": (0.0, 1.44102, 0.0),
        }.items():
            for actual, wanted in zip(poses[name], expected, strict=True):
                self.assertAlmostEqual(actual, wanted, places=7)

    def test_normalizes_displacement_and_reference_layout(self) -> None:
        values = (9.0,) * 6 + (0.0,) * (59 * 6)
        motion = normalize_wire_frame(
            WireFrame(2, "A", values, 0, with_displacement=True, with_reference=True), 1
        )
        self.assertEqual(len(motion.joints), 59)
        self.assertEqual(motion.joints[0].position, (0.0, 0.0, 0.0))

    def test_yxz_wire_channels_are_mapped_to_axes(self) -> None:
        # Wire channels are Y, X, Z. A 90-degree first channel must rotate
        # around Y, not X.
        values = (0.0, 0.0, 0.0, 90.0, 0.0, 0.0) + (0.0,) * (58 * 3)
        motion = normalize_wire_frame(WireFrame(1, "A", values, 0), 1, rotation_order="YXZ")
        expected = euler_to_quaternion((0.0, 90.0, 0.0), "YXZ")
        for actual, wanted in zip(motion.joints[0].rotation, expected, strict=True):
            self.assertAlmostEqual(actual, wanted)

    def test_rejects_unknown_layout(self) -> None:
        with self.assertRaises(ProviderError):
            normalize_wire_frame(WireFrame(1, "A", (0.0,) * 12, 0), 1)


class DemoProviderTests(unittest.TestCase):
    def test_frame_shape_commands_and_async_lifecycle(self) -> None:
        provider = DemoProvider(fps=240)
        idle = provider._make_frame(1, 1.0)
        self.assertEqual(len(idle.joints), len(JOINTS))
        self.assertEqual(len(idle.sensors), 18)
        self.assertEqual(provider.command("start_capture", {}), "Demo capture started")
        self.assertIn("calibration", provider.command("start_calibration", {}))
        self.assertIn("accepted", provider.command("calibration_next", {}))
        self.assertIn("accepted", provider.command("calibration_cancel", {}))
        self.assertIn("completed", provider.command("resume_original_posture", {}))
        moving = provider._make_frame(2, 1.0)
        self.assertNotEqual(idle.joints[2].position, moving.joints[2].position)
        with self.assertRaises(ProviderError):
            provider.command("not-a-command", {})

        received = threading.Event()
        provider.start(lambda _frame: received.set(), self.fail)
        self.assertTrue(received.wait(1.0))
        with self.assertRaises(ProviderError):
            provider.start(lambda _frame: None, self.fail)
        provider.stop()


if __name__ == "__main__":
    unittest.main()

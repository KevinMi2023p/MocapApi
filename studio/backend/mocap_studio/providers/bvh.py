from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

from ..skeleton import (
    JOINTS,
    euler_to_quaternion,
    finite_values,
    multiply_quaternion,
    rotate_vector,
)
from .base import (
    ErrorCallback,
    FrameCallback,
    JointPose,
    MotionFrame,
    Provider,
    ProviderCapabilities,
    ProviderConnectionError,
    ProviderError,
)


LEGACY_START_TOKEN = 0xDDFF
LEGACY_END_TOKEN = 0xEEFF
# Axis' original ``_BVH_PIPE`` header stores the value count and flags as
# 32-bit integers. Newer Axis/Neuron streams use the same 64-byte envelope but
# compact those fields and add a source frame index.
LEGACY_HEADER = struct.Struct("<H4BIiiI32sIIH")
MODERN_HEADER = struct.Struct("<H4BHBBI32sIIIIH")
MAX_FRAME_VALUES = 4096
MAX_STRING_BUFFER = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class WireFrame:
    avatar_index: int
    avatar_name: str
    values: tuple[float, ...]
    source_bytes: int
    version: tuple[int, int, int, int] | None = None
    with_displacement: bool | None = None
    with_reference: bool = False
    source_frame_index: int | None = None


class BvhStringDecoder:
    """Incrementally decode Axis/Neuron's documented `... ||` string frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes, datagram: bool = False) -> list[WireFrame]:
        if not data:
            return []
        self._buffer.extend(data)
        if len(self._buffer) > MAX_STRING_BUFFER:
            self._buffer.clear()
            raise ProviderError("BVH string buffer exceeded 2 MiB without a frame delimiter")

        frames: list[WireFrame] = []
        delimiter = b"||"
        while True:
            boundary = self._buffer.find(delimiter)
            if boundary < 0:
                break
            payload = bytes(self._buffer[:boundary]).strip(b"\x00\r\n \t")
            del self._buffer[: boundary + len(delimiter)]
            if payload:
                frames.append(parse_string_frame(payload))

        # UDP normally carries one complete frame. Accept a delimiter-less
        # datagram for integrations that trim the documented `||` terminator.
        if datagram and self._buffer:
            payload = bytes(self._buffer).strip(b"\x00\r\n \t")
            self._buffer.clear()
            if payload:
                frames.append(parse_string_frame(payload))
        return frames


class LegacyBinaryDecoder:
    """Decode both published 64-byte BVH frame-header generations."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[WireFrame]:
        self._buffer.extend(data)
        frames: list[WireFrame] = []
        start_bytes = struct.pack("<H", LEGACY_START_TOKEN)
        while len(self._buffer) >= LEGACY_HEADER.size:
            if self._buffer[:2] != start_bytes:
                boundary = self._buffer.find(start_bytes, 1)
                if boundary < 0:
                    del self._buffer[:-1]
                    break
                del self._buffer[:boundary]
                continue
            if struct.unpack_from("<H", self._buffer, LEGACY_HEADER.size - 2)[0] != LEGACY_END_TOKEN:
                del self._buffer[:2]
                continue
            try:
                header = _decode_binary_header(self._buffer)
            except ProviderError:
                # Drop the known-size corrupt envelope so a bad datagram cannot
                # poison this decoder forever. Its unknown body is resynchronised
                # by the start-token scan on the next feed.
                del self._buffer[: LEGACY_HEADER.size]
                raise
            (
                version,
                data_count,
                with_displacement,
                with_reference,
                avatar_index,
                name,
                source_frame_index,
            ) = header
            frame_size = LEGACY_HEADER.size + data_count * 4
            if len(self._buffer) < frame_size:
                break
            values = struct.unpack_from(f"<{data_count}f", self._buffer, LEGACY_HEADER.size)
            del self._buffer[:frame_size]
            if not finite_values(values):
                raise ProviderError("Legacy BVH frame contains NaN, infinity, or unsafe values")
            frames.append(
                WireFrame(
                    avatar_index=avatar_index,
                    avatar_name=name or f"Avatar{avatar_index:02d}",
                    values=tuple(values),
                    source_bytes=frame_size,
                    version=version,
                    with_displacement=bool(with_displacement),
                    with_reference=bool(with_reference),
                    source_frame_index=source_frame_index,
                )
            )
        return frames


def _decode_binary_header(
    data: bytes | bytearray,
) -> tuple[tuple[int, int, int, int], int, int, int, int, str, int | None]:
    old = LEGACY_HEADER.unpack_from(data)
    modern = MODERN_HEADER.unpack_from(data)
    candidates: list[
        tuple[int, tuple[int, int, int, int], int, int, int, int, str, int | None]
    ] = []

    def add_candidate(
        *,
        version: tuple[int, int, int, int],
        count: int,
        displacement: int,
        reference: int,
        avatar_index: int,
        raw_name: bytes,
        source_frame_index: int | None,
        modern_layout: bool,
    ) -> None:
        if not 0 < count <= MAX_FRAME_VALUES or displacement not in (0, 1) or reference not in (0, 1):
            return
        decoded = _plausible_avatar_name(raw_name)
        if decoded is None:
            return
        score = (2 if decoded else 0) + (1 if avatar_index < 1_000_000 else 0)
        # Prefer the modern interpretation when it carries its distinguishing
        # frame index; completely zero/empty headers are semantically identical.
        if modern_layout and source_frame_index:
            score += 1
        candidates.append(
            (
                score,
                version,
                count,
                displacement,
                reference,
                avatar_index,
                decoded,
                source_frame_index,
            )
        )

    (
        start,
        v0,
        v1,
        v2,
        v3,
        count,
        displacement,
        reference,
        avatar_index,
        raw_name,
        _reserved1,
        _reserved2,
        end,
    ) = old
    if start == LEGACY_START_TOKEN and end == LEGACY_END_TOKEN:
        add_candidate(
            version=(v0, v1, v2, v3),
            count=count,
            displacement=displacement,
            reference=reference,
            avatar_index=avatar_index,
            raw_name=raw_name,
            source_frame_index=None,
            modern_layout=False,
        )

    (
        start,
        v0,
        v1,
        v2,
        v3,
        count,
        displacement,
        reference,
        avatar_index,
        raw_name,
        frame_index,
        _reserved0,
        _reserved1,
        _reserved2,
        end,
    ) = modern
    if start == LEGACY_START_TOKEN and end == LEGACY_END_TOKEN:
        add_candidate(
            version=(v0, v1, v2, v3),
            count=count,
            displacement=displacement,
            reference=reference,
            avatar_index=avatar_index,
            raw_name=raw_name,
            source_frame_index=frame_index,
            modern_layout=True,
        )

    if not candidates:
        raise ProviderError("Invalid 64-byte BVH frame header")
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    _, version, count, displacement, reference, avatar_index, name, frame_index = candidates[0]
    return version, count, displacement, reference, avatar_index, name, frame_index


def _plausible_avatar_name(raw_name: bytes) -> str | None:
    name_bytes, separator, padding = raw_name.partition(b"\x00")
    if separator and any(padding):
        return None
    try:
        name = name_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    if any(not character.isprintable() for character in name):
        return None
    return name


def parse_string_frame(payload: bytes) -> WireFrame:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ProviderError("BVH string frame is not valid UTF-8") from error
    tokens = text.replace(",", " ").split()
    if len(tokens) < 5:
        raise ProviderError("BVH string frame is too short")
    try:
        avatar_index = int(tokens[0])
        avatar_name = tokens[1]
        values = tuple(float(value) for value in tokens[2:])
    except ValueError as error:
        raise ProviderError("BVH string frame has an invalid index or numeric value") from error
    if len(values) > MAX_FRAME_VALUES:
        raise ProviderError("BVH string frame is unreasonably large")
    if not finite_values(values):
        raise ProviderError("BVH string frame contains NaN, infinity, or unsafe values")
    return WireFrame(
        avatar_index=avatar_index,
        avatar_name=avatar_name,
        values=values,
        source_bytes=len(payload) + 2,
    )


def normalize_wire_frame(
    frame: WireFrame,
    frame_number: int,
    *,
    rotation_order: str = "YXZ",
    source_unit: Literal["meters", "centimeters"] = "centimeters",
) -> MotionFrame:
    if rotation_order not in {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}:
        raise ProviderError("Unsupported BVH rotation order")
    if source_unit not in {"meters", "centimeters"}:
        raise ProviderError("Source unit must be meters or centimeters")
    values = frame.values
    if frame.with_reference:
        # Published legacy headers describe an optional 6-float reference bone
        # before the skeleton. It does not belong in the avatar hierarchy.
        if len(values) < 6:
            raise ProviderError("BVH reference flag is set but reference data is missing")
        values = values[6:]

    with_displacement, joint_count = _infer_layout(values, frame.with_displacement)
    definitions = JOINTS[:joint_count]
    scale = 0.01 if source_unit == "centimeters" else 1.0
    local_positions: dict[str, tuple[float, float, float]] = {}
    local_rotations: dict[str, tuple[float, float, float, float]] = {}

    if with_displacement:
        for index, definition in enumerate(definitions):
            offset = index * 6
            px, py, pz, rx, ry, rz = values[offset : offset + 6]
            local_positions[definition.name] = (px * scale, py * scale, pz * scale)
            local_rotations[definition.name] = _wire_rotation((rx, ry, rz), rotation_order)
    else:
        # Translation channels are motion relative to the Appendix B template
        # root. At zero translation, Hips remains at its documented 84.102 cm
        # height so the feet land on the ground plane.
        root_position = tuple(
            offset + value * scale
            for offset, value in zip(definitions[0].offset, values[:3], strict=True)
        )
        local_positions[definitions[0].name] = root_position  # type: ignore[assignment]
        local_rotations[definitions[0].name] = _wire_rotation(values[3:6], rotation_order)
        for index, definition in enumerate(definitions[1:], start=1):
            offset = 6 + (index - 1) * 3
            local_positions[definition.name] = definition.offset
            local_rotations[definition.name] = _wire_rotation(
                values[offset : offset + 3], rotation_order
            )

    global_positions: dict[str, tuple[float, float, float]] = {}
    global_rotations: dict[str, tuple[float, float, float, float]] = {}
    poses: list[JointPose] = []
    for definition in definitions:
        local_position = local_positions[definition.name]
        local_rotation = local_rotations[definition.name]
        if definition.parent is None or definition.parent not in global_positions:
            position = local_position
            rotation = local_rotation
        else:
            parent_position = global_positions[definition.parent]
            parent_rotation = global_rotations[definition.parent]
            rotated = rotate_vector(parent_rotation, local_position)
            position = tuple(a + b for a, b in zip(parent_position, rotated, strict=True))
            rotation = multiply_quaternion(parent_rotation, local_rotation)
        global_positions[definition.name] = position  # type: ignore[assignment]
        global_rotations[definition.name] = rotation
        poses.append(
            JointPose(
                id=definition.name,
                name=definition.name,
                parent=definition.parent,
                position=position,  # type: ignore[arg-type]
                rotation=rotation,
                sensor_id=definition.sensor_id,
                grounded=definition.name in {"LeftFoot", "RightFoot"},
            )
        )

    return MotionFrame(
        avatar_id=f"avatar-{frame.avatar_index}",
        avatar_name=frame.avatar_name,
        frame_number=frame_number,
        received_at=time.time(),
        joints=tuple(poses),
        fps=0.0,
        source_bytes=frame.source_bytes,
        source_frame_index=frame.source_frame_index,
    )


def _wire_rotation(
    channels: Sequence[float], rotation_order: str
) -> tuple[float, float, float, float]:
    """Map order-positioned wire channels into the converter's XYZ tuple."""
    axis_values = dict(zip(rotation_order, channels, strict=True))
    return euler_to_quaternion(tuple(axis_values[axis] for axis in "XYZ"), rotation_order)


def _infer_layout(
    values: Sequence[float], explicit_displacement: bool | None
) -> tuple[bool, int]:
    count = len(values)
    no_displacement_count = (count - 3) // 3 if count >= 6 and (count - 3) % 3 == 0 else 0
    displacement_count = count // 6 if count % 6 == 0 else 0
    valid_counts = {59, 60}
    if explicit_displacement is True and displacement_count in valid_counts:
        return True, displacement_count
    if explicit_displacement is False and no_displacement_count in valid_counts:
        return False, no_displacement_count
    if no_displacement_count in valid_counts:
        return False, no_displacement_count
    if displacement_count in valid_counts:
        return True, displacement_count
    raise ProviderError(
        f"Unsupported BVH value layout ({count} floats); expected 59/60-joint data"
    )


class BvhStreamDecoder:
    """Persist the selected wire format across fragmented TCP reads."""

    def __init__(self) -> None:
        self._format: Literal["binary", "string"] | None = None
        self._pending = bytearray()
        self._strings = BvhStringDecoder()
        self._binary = LegacyBinaryDecoder()

    def feed(self, data: bytes, *, datagram: bool = False) -> list[WireFrame]:
        if datagram:
            # A UDP datagram is an atomic transport unit. Never retain an
            # incomplete packet: otherwise the next sender packet could be
            # consumed as the missing body of a truncated header.
            if data.startswith(struct.pack("<H", LEGACY_START_TOKEN)):
                return LegacyBinaryDecoder().feed(data)
            return BvhStringDecoder().feed(data, datagram=True)

        if self._format is None:
            self._pending.extend(data)
            if len(self._pending) < 2:
                return []
            start_bytes = struct.pack("<H", LEGACY_START_TOKEN)
            self._format = "binary" if self._pending.startswith(start_bytes) else "string"
            data = bytes(self._pending)
            self._pending.clear()
        if self._format == "binary":
            return self._binary.feed(data)
        return self._strings.feed(data)


class BvhProvider(Provider):
    mode = "bvh"
    capabilities = ProviderCapabilities(
        receive_motion=True,
        receive_sensors=False,
        server_commands=False,
        calibration_commands=False,
        axis_recording=False,
        local_recording=True,
        reason=(
            "Standard BVH transport provides solved motion only. Capture, calibration, "
            "sensor telemetry, and Axis-side recording require a compatible MocapApi runtime."
        ),
    )

    def __init__(
        self,
        *,
        transport: Literal["udp", "tcp"],
        host: str,
        port: int,
        rotation_order: str = "YXZ",
        source_unit: Literal["meters", "centimeters"] = "centimeters",
    ) -> None:
        if not 1 <= port <= 65535:
            raise ProviderError("Port must be between 1 and 65535")
        if rotation_order not in {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}:
            raise ProviderError("Unsupported BVH rotation order")
        self.transport = transport
        self.host = host
        self.port = port
        self.rotation_order = rotation_order
        self.source_unit = source_unit
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def start(self, on_frame: FrameCallback, on_error: ErrorCallback) -> None:
        if self._thread and self._thread.is_alive():
            raise ProviderError("BVH provider is already running")
        socket_type = socket.SOCK_DGRAM if self.transport == "udp" else socket.SOCK_STREAM
        sock = socket.socket(socket.AF_INET, socket_type)
        sock.settimeout(0.5)
        try:
            if self.transport == "udp":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host or "0.0.0.0", self.port))
            else:
                sock.connect((self.host, self.port))
        except OSError as error:
            sock.close()
            raise ProviderError(f"Could not open {self.transport.upper()} connection: {error}") from error
        self._socket = sock
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(on_frame, on_error),
            name=f"mocap-bvh-{self.transport}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock, self._socket = self._socket, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None

    def _run(self, on_frame: FrameCallback, on_error: ErrorCallback) -> None:
        decoder = BvhStreamDecoder()
        frame_numbers: dict[int, int] = {}
        last_error_at = 0.0
        while not self._stop.is_set():
            try:
                if self._socket is None:
                    break
                data = self._socket.recv(65535)
                if not data:
                    if self.transport == "tcp":
                        on_error(ProviderConnectionError("BVH TCP peer closed the connection"))
                        break
                    continue
                wire_frames: Iterable[WireFrame] = decoder.feed(
                    data, datagram=self.transport == "udp"
                )
                for wire_frame in wire_frames:
                    next_number = frame_numbers.get(wire_frame.avatar_index, 0) + 1
                    frame_numbers[wire_frame.avatar_index] = next_number
                    on_frame(
                        normalize_wire_frame(
                            wire_frame,
                            next_number,
                            rotation_order=self.rotation_order,
                            source_unit=self.source_unit,
                        )
                    )
            except socket.timeout:
                continue
            except OSError as error:
                if self._stop.is_set():
                    break
                on_error(ProviderConnectionError(f"BVH socket error: {error}"))
                break
            except Exception as error:
                if self.transport == "tcp":
                    on_error(ProviderConnectionError(f"BVH TCP stream error: {error}"))
                    break
                # A malformed UDP datagram must not tear down the receiver. Limit
                # error delivery to avoid overwhelming the event log.
                now = time.monotonic()
                if now - last_error_at >= 2.0:
                    on_error(error)
                    last_error_at = now
        self._stop.set()

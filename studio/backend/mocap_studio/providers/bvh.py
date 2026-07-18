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
    ProviderError,
)


LEGACY_START_TOKEN = 0xDDFF
LEGACY_END_TOKEN = 0xEEFF
LEGACY_HEADER = struct.Struct("<H4BIiiI32sIIH")
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
    """Decode the published 64-byte legacy BVH frame header (v1.0.x)."""

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
            unpacked = LEGACY_HEADER.unpack_from(self._buffer)
            (
                start,
                ver0,
                ver1,
                ver2,
                ver3,
                data_count,
                with_displacement,
                with_reference,
                avatar_index,
                avatar_name,
                _reserved1,
                _reserved2,
                end,
            ) = unpacked
            if start != LEGACY_START_TOKEN or end != LEGACY_END_TOKEN:
                del self._buffer[:2]
                continue
            if data_count <= 0 or data_count > MAX_FRAME_VALUES:
                raise ProviderError(f"Invalid legacy BVH value count: {data_count}")
            frame_size = LEGACY_HEADER.size + data_count * 4
            if len(self._buffer) < frame_size:
                break
            values = struct.unpack_from(f"<{data_count}f", self._buffer, LEGACY_HEADER.size)
            del self._buffer[:frame_size]
            if not finite_values(values):
                raise ProviderError("Legacy BVH frame contains NaN, infinity, or unsafe values")
            name = avatar_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            frames.append(
                WireFrame(
                    avatar_index=avatar_index,
                    avatar_name=name or f"Avatar{avatar_index:02d}",
                    values=tuple(values),
                    source_bytes=frame_size,
                    version=(ver0, ver1, ver2, ver3),
                    with_displacement=bool(with_displacement),
                    with_reference=bool(with_reference),
                )
            )
        return frames


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
            local_rotations[definition.name] = euler_to_quaternion((rx, ry, rz), rotation_order)
    else:
        root_position = tuple(value * scale for value in values[:3])
        local_positions[definitions[0].name] = root_position  # type: ignore[assignment]
        local_rotations[definitions[0].name] = euler_to_quaternion(values[3:6], rotation_order)
        for index, definition in enumerate(definitions[1:], start=1):
            offset = 6 + (index - 1) * 3
            local_positions[definition.name] = definition.offset
            local_rotations[definition.name] = euler_to_quaternion(
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
    )


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
        string_decoder = BvhStringDecoder()
        binary_decoder = LegacyBinaryDecoder()
        frame_numbers: dict[int, int] = {}
        last_error_at = 0.0
        while not self._stop.is_set():
            try:
                if self._socket is None:
                    break
                data = self._socket.recv(65535)
                if not data:
                    if self.transport == "tcp":
                        raise ProviderError("BVH TCP peer closed the connection")
                    continue
                is_binary = data.startswith(struct.pack("<H", LEGACY_START_TOKEN))
                wire_frames: Iterable[WireFrame]
                if is_binary:
                    wire_frames = binary_decoder.feed(data)
                else:
                    wire_frames = string_decoder.feed(data, datagram=self.transport == "udp")
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
                on_error(ProviderError(f"BVH socket error: {error}"))
                break
            except Exception as error:
                # A malformed UDP datagram must not tear down the receiver. Limit
                # error delivery to avoid overwhelming the event log.
                now = time.monotonic()
                if now - last_error_at >= 2.0:
                    on_error(error)
                    last_error_at = now

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Literal


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    receive_motion: bool = True
    receive_sensors: bool = False
    server_commands: bool = False
    calibration_commands: bool = False
    axis_recording: bool = False
    local_recording: bool = True
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "receiveMotion": self.receive_motion,
            "receiveSensors": self.receive_sensors,
            "serverCommands": self.server_commands,
            "calibrationCommands": self.calibration_commands,
            "axisRecording": self.axis_recording,
            "localRecording": self.local_recording,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class JointPose:
    id: str
    name: str
    parent: str | None
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    sensor_id: int | None = None
    grounded: bool = False

    def as_dict(self) -> dict[str, Any]:
        x, y, z = self.position
        qx, qy, qz, qw = self.rotation
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "parent": self.parent,
            "position": {"x": x, "y": y, "z": z},
            "rotation": {"x": qx, "y": qy, "z": qz, "w": qw},
            "grounded": self.grounded,
        }
        if self.sensor_id is not None:
            result["sensorId"] = self.sensor_id
        return result


@dataclass(frozen=True, slots=True)
class MotionFrame:
    avatar_id: str
    avatar_name: str
    frame_number: int
    received_at: float
    joints: tuple[JointPose, ...]
    fps: float = 60.0
    source_bytes: int = 0
    sensors: tuple[dict[str, Any], ...] = field(default_factory=tuple)


FrameCallback = Callable[[MotionFrame], None]
ErrorCallback = Callable[[Exception], None]


class ProviderError(RuntimeError):
    """A provider could not start, parse data, or execute a command."""


class Provider(ABC):
    mode: Literal["demo", "bvh", "mocap-api"]
    capabilities: ProviderCapabilities

    @abstractmethod
    def start(self, on_frame: FrameCallback, on_error: ErrorCallback) -> None:
        """Start asynchronous ingestion."""

    @abstractmethod
    def stop(self) -> None:
        """Stop ingestion and release sockets/threads."""

    def command(self, name: str, options: dict[str, Any]) -> str:
        raise ProviderError(f"The {self.mode} provider does not support {name!r}")

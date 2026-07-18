from __future__ import annotations

import threading
import time
from math import cos, sin
from typing import Any

from ..skeleton import JOINTS, euler_to_quaternion
from .base import (
    ErrorCallback,
    FrameCallback,
    JointPose,
    MotionFrame,
    Provider,
    ProviderCapabilities,
    ProviderError,
)


class DemoProvider(Provider):
    mode = "demo"
    capabilities = ProviderCapabilities(
        receive_motion=True,
        receive_sensors=True,
        server_commands=True,
        calibration_commands=True,
        axis_recording=True,
        local_recording=True,
        reason="Demo mode simulates supported server operations without contacting hardware.",
    )

    def __init__(self, fps: float = 60.0) -> None:
        self._fps = max(1.0, min(fps, 240.0))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capturing = False
        self._calibrated = True

    def start(self, on_frame: FrameCallback, on_error: ErrorCallback) -> None:
        if self._thread and self._thread.is_alive():
            raise ProviderError("Demo provider is already running")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(on_frame, on_error),
            name="mocap-demo-provider",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None

    def command(self, name: str, options: dict[str, Any]) -> str:
        del options
        if name == "start_capture":
            self._capturing = True
            return "Demo capture started"
        if name == "stop_capture":
            self._capturing = False
            return "Demo capture stopped"
        if name in {"zero_position", "resume_posture", "resume_hands"}:
            return f"Demo {name.replace('_', ' ')} completed"
        if name == "calibrate":
            self._calibrated = True
            return "Demo calibration completed"
        if name in {"start_record", "stop_record"}:
            return f"Demo {name.replace('_', ' ')} accepted"
        raise ProviderError(f"Unknown demo command: {name}")

    def _run(self, on_frame: FrameCallback, on_error: ErrorCallback) -> None:
        started = time.monotonic()
        next_frame = started
        frame_number = 0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                delay = next_frame - now
                if delay > 0:
                    self._stop.wait(delay)
                    continue
                next_frame += 1.0 / self._fps
                if next_frame < now - 0.25:
                    next_frame = now
                frame_number += 1
                on_frame(self._make_frame(frame_number, now - started))
        except Exception as error:  # pragma: no cover - defensive worker boundary
            on_error(error)

    def _make_frame(self, frame_number: int, phase: float) -> MotionFrame:
        # A procedural idle/walk blend gives the viewport a useful, deterministic
        # signal without reproducing any Noitom mesh or animation asset.
        gait = sin(phase * 2.4) if self._capturing else sin(phase * 0.7) * 0.08
        sway = sin(phase * 1.2) * (0.025 if self._capturing else 0.008)
        bob = abs(sin(phase * 2.4)) * (0.025 if self._capturing else 0.003)
        positions: dict[str, tuple[float, float, float]] = {}
        poses: list[JointPose] = []

        for definition in JOINTS:
            ox, oy, oz = definition.offset
            if definition.parent is None:
                position = (ox + sway, oy + bob, oz)
            else:
                px, py, pz = positions[definition.parent]
                leg_phase = gait if definition.name.startswith("Left") else -gait
                arm_phase = -leg_phase
                z_motion = 0.0
                y_motion = 0.0
                if "Leg" in definition.name or "Foot" in definition.name:
                    z_motion = leg_phase * 0.06
                    y_motion = max(0.0, leg_phase) * 0.025
                elif "Arm" in definition.name or "ForeArm" in definition.name:
                    z_motion = arm_phase * 0.05
                position = (px + ox, py + oy + y_motion, pz + oz + z_motion)
            positions[definition.name] = position
            angle = 0.0
            if definition.name in {"LeftArm", "RightLeg"}:
                angle = gait * 12.0
            elif definition.name in {"RightArm", "LeftLeg"}:
                angle = -gait * 12.0
            rotation = euler_to_quaternion((angle, 0.0, sway * 80.0), "XYZ")
            poses.append(
                JointPose(
                    id=definition.name,
                    name=definition.name,
                    parent=definition.parent,
                    position=position,
                    rotation=rotation,
                    sensor_id=definition.sensor_id,
                    grounded=definition.name in {"LeftFoot", "RightFoot"},
                )
            )

        sensors = tuple(_demo_sensor(index, phase) for index in range(1, 19))
        return MotionFrame(
            avatar_id="demo-avatar-1",
            avatar_name="Performer 01",
            frame_number=frame_number,
            received_at=time.time(),
            joints=tuple(poses),
            fps=self._fps,
            source_bytes=0,
            sensors=sensors,
        )


def _demo_sensor(sensor_id: int, phase: float) -> dict[str, Any]:
    signal = 91 - (sensor_id % 4) * 3
    unstable = sensor_id == 9 and int(phase) % 12 > 8
    return {
        "id": sensor_id,
        "name": f"PNS-{sensor_id:02d}",
        "bodyPart": _SENSOR_BODY_PARTS[sensor_id - 1],
        "signal": 69 if unstable else signal,
        "battery": 76 + (sensor_id % 5) * 4,
        "temperature": round(31.2 + (sensor_id % 4) * 0.6 + cos(phase) * 0.1, 1),
        "packetRate": 94 if unstable else 98,
        "magnetic": "unstable" if unstable else "steady",
        "connected": True,
    }


_SENSOR_BODY_PARTS = (
    "Hips",
    "Spine",
    "Chest",
    "Upper Chest",
    "Head",
    "Left Upper Arm",
    "Left Forearm",
    "Left Hand",
    "Right Upper Arm",
    "Right Forearm",
    "Right Hand",
    "Left Thigh",
    "Left Shin",
    "Left Foot",
    "Right Thigh",
    "Right Shin",
    "Right Foot",
    "Reference",
)

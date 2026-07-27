#!/usr/bin/env python3
"""Retarget an Axis Studio BVH hand to one Wuji Hand 2.

The process intentionally keeps the two SDKs in one address space:

    Axis Studio UDP -> MocapApi -> MediaPipe landmarks -> Wuji retargeter -> hand

Motor control is opt-in.  Without ``--enable-motors`` the program connects to
the physical hand only to identify its handedness and verify that all joints
are online.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import logging
import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import numpy.typing as npt


LOGGER = logging.getLogger("mocap_wuji_bridge")

EXPECTED_MOCAP_VERSION = (0, 0, 73)
DEFAULT_UDP_PORT = 8088
DEFAULT_ROTATION = "YXZ"
MAX_RATE_HZ = 120.0
FRAME_BUDGET_SECONDS = 1.0 / MAX_RATE_HZ
STARTUP_TIMEOUT_SECONDS = 10.0
TRACKING_TIMEOUT_SECONDS = 0.250
TIP_CALIBRATION_FRAMES = 30
ENABLE_TIMEOUT_SECONDS = 5.0
JOINT_STATE_TIMEOUT_SECONDS = 2.0
STARTUP_BLEND_SECONDS = 0.5
TOTAL_WUJI_JOINTS = 20
# Hand 2 publishes five four-joint finger groups.  The gaps are the per-finger
# controller node IDs; publisher order is this flattened sequence.
EXPECTED_WUJI_NIDS = (
    1,
    2,
    3,
    4,
    6,
    7,
    8,
    9,
    11,
    12,
    13,
    14,
    16,
    17,
    18,
    19,
    21,
    22,
    23,
    24,
)
EFFORT_LIMIT_AMPS = 1.5
MIT_KP = 3.0
MIT_KD = 0.05

FINGER_NAMES = ("Thumb", "Index", "Middle", "Ring", "Pinky")
TIP_RATIOS = {
    "Thumb": 0.857451357,
    "Index": 0.879712793,
    "Middle": 0.797619078,
    "Ring": 0.777477857,
    "Pinky": 0.944473814,
}
MEDIAPIPE_BASE_INDEX = {
    "Thumb": 1,
    "Index": 5,
    "Middle": 9,
    "Ring": 13,
    "Pinky": 17,
}

MIN_BONE_LENGTH_METERS = 0.001
MAX_BONE_LENGTH_METERS = 0.20
MAX_WRIST_DISTANCE_METERS = 0.50


class BridgeError(RuntimeError):
    """Base class for bridge failures."""


class ConfigurationError(BridgeError):
    """The SDK, stream, or device configuration is incompatible."""


class FrameValidationError(BridgeError):
    """A mocap or retarget frame is malformed or unsafe."""


class TrackingStaleError(BridgeError):
    """No valid tracking frame arrived before the watchdog deadline."""


@dataclass(frozen=True)
class BridgeConfig:
    udp_port: int = DEFAULT_UDP_PORT
    bvh_rotation: str = DEFAULT_ROTATION
    avatar_name: str | None = None
    hand_sn: str | None = None
    enable_motors: bool = False


@dataclass(frozen=True)
class RawHandPose:
    avatar_name: str
    posture_index: int
    received_at: float
    positions: Mapping[str, npt.NDArray[np.float64]]
    distal_rotations_xyzw: Mapping[str, npt.NDArray[np.float64]]


@dataclass(frozen=True)
class HandLandmarkFrame:
    avatar_name: str
    posture_index: int
    received_at: float
    keypoints: npt.NDArray[np.float32]
    calibration_ready: bool


class FreshnessWatchdog:
    """Track the most recent valid frame using a monotonic clock."""

    def __init__(self, timeout_seconds: float = TRACKING_TIMEOUT_SECONDS):
        self.timeout_seconds = timeout_seconds
        self.last_valid_at: float | None = None

    def mark_valid(self, timestamp: float) -> None:
        self.last_valid_at = timestamp

    def expired(self, now: float) -> bool:
        return (
            self.last_valid_at is not None
            and now - self.last_valid_at >= self.timeout_seconds
        )

    def require_fresh(self, now: float) -> None:
        if self.expired(now):
            age_ms = (now - self.last_valid_at) * 1000.0  # type: ignore[operator]
            raise TrackingStaleError(
                f"tracking frame is stale ({age_ms:.1f} ms; "
                f"limit {self.timeout_seconds * 1000.0:.0f} ms)"
            )


def normalize_handedness(value: Any) -> str:
    """Normalize a Wuji handedness resource value to ``left`` or ``right``."""

    text = str(value).strip().lower()
    if text.endswith(".left"):
        text = "left"
    elif text.endswith(".right"):
        text = "right"
    if text not in {"left", "right"}:
        raise ConfigurationError(f"unknown Wuji handedness value: {value!r}")
    return text


def normalize_quaternion_xyzw(
    quaternion: Sequence[float],
) -> npt.NDArray[np.float64]:
    q = np.asarray(quaternion, dtype=np.float64)
    if q.shape != (4,) or not np.isfinite(q).all():
        raise FrameValidationError("joint quaternion must contain four finite values")
    norm = float(np.linalg.norm(q))
    if norm < 0.5 or norm > 1.5:
        raise FrameValidationError(f"joint quaternion has invalid norm {norm:.6f}")
    return q / norm


def wxyz_to_xyzw(quaternion: Sequence[float]) -> npt.NDArray[np.float64]:
    """Convert the MocapApi Python wrapper's quaternion order to C/Wuji order."""

    if len(quaternion) != 4:
        raise FrameValidationError("MocapApi quaternion must contain four values")
    w, x, y, z = quaternion
    return normalize_quaternion_xyzw((x, y, z, w))


def rotate_vector_xyzw(
    quaternion: Sequence[float], vector: Sequence[float]
) -> npt.NDArray[np.float64]:
    """Rotate a 3-vector by an active local-to-world XYZW quaternion."""

    q = normalize_quaternion_xyzw(quaternion)
    v = np.asarray(vector, dtype=np.float64)
    if v.shape != (3,) or not np.isfinite(v).all():
        raise FrameValidationError("rotation input must be a finite 3-vector")
    xyz = q[:3]
    w = q[3]
    return v + 2.0 * np.cross(xyz, np.cross(xyz, v) + w * v)


def smoothstep(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def validate_qpos(qpos: Any) -> npt.NDArray[np.float32]:
    result = np.asarray(qpos, dtype=np.float32)
    if result.shape != (TOTAL_WUJI_JOINTS,):
        raise FrameValidationError(
            f"Wuji retargeter returned {result.shape}; expected (20,)"
        )
    if not np.isfinite(result).all():
        raise FrameValidationError("Wuji retargeter returned NaN or infinity")
    return result


def _joint_name(side: str, finger: str | None = None, index: int | None = None) -> str:
    prefix = "Left" if side == "left" else "Right"
    if finger is None:
        return f"{prefix}Hand"
    return f"{prefix}Hand{finger}{index}"


def required_joint_names(side: str) -> tuple[str, ...]:
    names = [_joint_name(side)]
    for finger in FINGER_NAMES:
        names.extend(_joint_name(side, finger, index) for index in (1, 2, 3))
    return tuple(names)


def _finite_point(value: Any, label: str) -> npt.NDArray[np.float64]:
    point = np.asarray(value, dtype=np.float64)
    if point.shape != (3,) or not np.isfinite(point).all():
        raise FrameValidationError(f"{label} must be a finite 3-vector")
    return point


class HandLandmarkBuilder:
    """Convert one Axis hand pose to 21 MediaPipe-order landmarks."""

    def __init__(
        self,
        side: str,
        calibration_frames: int = TIP_CALIBRATION_FRAMES,
    ):
        self.side = normalize_handedness(side)
        if calibration_frames < 1:
            raise ValueError("calibration_frames must be positive")
        self.calibration_frames = calibration_frames
        self._length_samples: dict[str, list[float]] = {
            finger: [] for finger in FINGER_NAMES
        }
        self._calibrated_lengths: dict[str, float] | None = None

    @property
    def calibration_ready(self) -> bool:
        return self._calibrated_lengths is not None

    def reset(self) -> None:
        self._length_samples = {finger: [] for finger in FINGER_NAMES}
        self._calibrated_lengths = None

    def _observe_lengths(
        self, positions: Mapping[str, npt.NDArray[np.float64]]
    ) -> Mapping[str, float]:
        current: dict[str, float] = {}
        for finger in FINGER_NAMES:
            p2 = positions[_joint_name(self.side, finger, 2)]
            p3 = positions[_joint_name(self.side, finger, 3)]
            length = float(np.linalg.norm(p3 - p2))
            if not MIN_BONE_LENGTH_METERS <= length <= MAX_BONE_LENGTH_METERS:
                raise FrameValidationError(
                    f"{finger} distal bone length {length:.6f} m is outside "
                    f"[{MIN_BONE_LENGTH_METERS}, {MAX_BONE_LENGTH_METERS}]"
                )
            current[finger] = length

        if self._calibrated_lengths is None:
            for finger, length in current.items():
                self._length_samples[finger].append(length)
            if all(
                len(samples) >= self.calibration_frames
                for samples in self._length_samples.values()
            ):
                self._calibrated_lengths = {
                    finger: float(np.median(samples[-self.calibration_frames :]))
                    for finger, samples in self._length_samples.items()
                }

        if self._calibrated_lengths is not None:
            return self._calibrated_lengths
        return {
            finger: float(np.median(samples))
            for finger, samples in self._length_samples.items()
        }

    def build(self, raw: RawHandPose) -> HandLandmarkFrame:
        expected = required_joint_names(self.side)
        missing = [name for name in expected if name not in raw.positions]
        if missing:
            raise FrameValidationError(
                "Axis avatar is missing required joints: " + ", ".join(missing)
            )

        positions = {
            name: _finite_point(raw.positions[name], name) for name in expected
        }
        for finger in FINGER_NAMES:
            if finger not in raw.distal_rotations_xyzw:
                raise FrameValidationError(
                    f"Axis avatar is missing {finger} distal global rotation"
                )

        lengths = self._observe_lengths(positions)
        points = np.empty((21, 3), dtype=np.float64)
        wrist = positions[_joint_name(self.side)]
        points[0] = wrist
        side_sign = 1.0 if self.side == "left" else -1.0

        for finger in FINGER_NAMES:
            base = MEDIAPIPE_BASE_INDEX[finger]
            for offset, joint_index in enumerate((1, 2, 3)):
                points[base + offset] = positions[
                    _joint_name(self.side, finger, joint_index)
                ]

            p3 = positions[_joint_name(self.side, finger, 3)]
            local_tip = (
                side_sign * TIP_RATIOS[finger] * lengths[finger],
                0.0,
                0.0,
            )
            points[base + 3] = p3 + rotate_vector_xyzw(
                raw.distal_rotations_xyzw[finger], local_tip
            )

        points -= wrist
        validate_landmarks(points)
        return HandLandmarkFrame(
            avatar_name=raw.avatar_name,
            posture_index=raw.posture_index,
            received_at=raw.received_at,
            keypoints=points.astype(np.float32),
            calibration_ready=self.calibration_ready,
        )


def validate_landmarks(keypoints: Any) -> None:
    points = np.asarray(keypoints, dtype=np.float64)
    if points.shape != (21, 3):
        raise FrameValidationError(
            f"landmark array has shape {points.shape}; expected (21, 3)"
        )
    if not np.isfinite(points).all():
        raise FrameValidationError("landmarks contain NaN or infinity")
    if float(np.linalg.norm(points[0])) > 1e-6:
        raise FrameValidationError("landmarks are not wrist-relative")

    for finger in FINGER_NAMES:
        base = MEDIAPIPE_BASE_INDEX[finger]
        chain = (0, base, base + 1, base + 2, base + 3)
        for start, end in zip(chain, chain[1:]):
            length = float(np.linalg.norm(points[end] - points[start]))
            if not MIN_BONE_LENGTH_METERS <= length <= MAX_BONE_LENGTH_METERS:
                raise FrameValidationError(
                    f"{finger} landmark segment {start}->{end} has invalid "
                    f"length {length:.6f} m"
                )
    if float(np.linalg.norm(points, axis=1).max()) > MAX_WRIST_DISTANCE_METERS:
        raise FrameValidationError("a hand landmark is more than 0.5 m from the wrist")


class MocapSource:
    """Small adapter around the packaged MocapApi Python wrapper."""

    def __init__(
        self,
        config: BridgeConfig,
        side: str,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.side = normalize_handedness(side)
        self.clock = clock
        self.mcp: Any = None
        self.app: Any = None
        self.settings: Any = None
        self.render_settings: Any = None
        self._seen_avatars: set[str] = set()
        self._joint_cache: dict[str, Mapping[str, Any]] = {}
        self._last_posture: dict[str, int] = {}

    def open(self) -> None:
        import mocap_api as mcp

        self.mcp = mcp
        version = tuple(mcp.get_mocap_api_version())
        if version[:3] != EXPECTED_MOCAP_VERSION:
            raise ConfigurationError(
                f"MocapApi {EXPECTED_MOCAP_VERSION[0]}."
                f"{EXPECTED_MOCAP_VERSION[1]}.{EXPECTED_MOCAP_VERSION[2]} "
                f"is required; loaded {mcp.get_mocap_api_version_string()}"
            )

        settings = mcp.MCPSettings()
        settings.set_udp(self.config.udp_port)
        settings.set_bvh_data(mcp.MCPBvhData.Binary)
        rotation = getattr(mcp.MCPBvhRotation, self.config.bvh_rotation)
        settings.set_bvh_rotation(rotation)
        settings.set_bvh_transformation(mcp.MCPBvhDisplacement.Enable)

        render = mcp.MCPRenderSettings()
        render.set_up_vector(mcp.MCPUpVector.YAxis, 1)
        render.set_front_vector(mcp.MCPFrontVector.ParityEven, 1)
        render.set_coord_system(mcp.MCPCoordSystem.RightHanded)
        render.set_rotating_direction(mcp.MCPRotatingDirection.CounterClockwise)
        render.set_unit(mcp.MCPUnit.Meter)

        app = mcp.MCPApplication()
        app.set_settings(settings)
        app.set_render_settings(render)
        opened, detail = app.open()
        if not opened:
            raise ConfigurationError(f"could not open MocapApi: {detail}")

        self.settings = settings
        self.render_settings = render
        self.app = app
        LOGGER.info(
            "MocapApi %s listening on UDP %d (%s, meters)",
            mcp.get_mocap_api_version_string(),
            self.config.udp_port,
            self.config.bvh_rotation,
        )

    def close(self) -> None:
        if self.app is not None and self.app.is_opened():
            with contextlib.suppress(Exception):
                self.app.close()

    def _joints_for_avatar(self, avatar: Any, avatar_name: str) -> Mapping[str, Any]:
        if avatar_name not in self._joint_cache:
            by_name = {joint.get_name(): joint for joint in avatar.get_joints()}
            missing = [
                name for name in required_joint_names(self.side) if name not in by_name
            ]
            if missing:
                raise ConfigurationError(
                    f"avatar {avatar_name!r} does not contain the selected "
                    f"{self.side} hand joints: {', '.join(missing)}"
                )
            self._joint_cache[avatar_name] = by_name
        return self._joint_cache[avatar_name]

    def poll_latest(self) -> RawHandPose | None:
        if self.app is None:
            raise RuntimeError("MocapSource is not open")

        latest: tuple[Any, str, int] | None = None
        for event in self.app.poll_next_event():
            if event.event_type != self.mcp.MCPEventType.AvatarUpdated:
                continue
            avatar = self.mcp.MCPAvatar(event.event_data.avatar_handle)
            avatar_name = avatar.get_name()
            self._seen_avatars.add(avatar_name)
            if self.config.avatar_name is None and len(self._seen_avatars) > 1:
                raise ConfigurationError(
                    "multiple Axis avatars are present; pass --avatar-name"
                )
            if (
                self.config.avatar_name is not None
                and avatar_name != self.config.avatar_name
            ):
                continue
            posture_index = avatar.get_avatar_posture_index()
            if self._last_posture.get(avatar_name) == posture_index:
                continue
            latest = avatar, avatar_name, posture_index

        if latest is None:
            return None
        avatar, avatar_name, posture_index = latest
        self._last_posture[avatar_name] = posture_index
        joints = self._joints_for_avatar(avatar, avatar_name)
        positions: dict[str, npt.NDArray[np.float64]] = {}
        rotations: dict[str, npt.NDArray[np.float64]] = {}
        for name in required_joint_names(self.side):
            positions[name] = _finite_point(joints[name].get_global_position(), name)
        for finger in FINGER_NAMES:
            name = _joint_name(self.side, finger, 3)
            rotations[finger] = wxyz_to_xyzw(joints[name].get_global_rotation())

        return RawHandPose(
            avatar_name=avatar_name,
            posture_index=posture_index,
            received_at=self.clock(),
            positions=positions,
            distal_rotations_xyzw=rotations,
        )


class WujiTarget:
    """Wuji discovery, retargeting, and opt-in motor control."""

    def __init__(self, hand_sn: str | None = None):
        self.hand_sn = hand_sn
        self.manager: Any = None
        self.hand: Any = None
        self.side: str | None = None
        self.session: Any = None
        self.publisher: Any = None
        self.joint_state_sub: Any = None
        self.diagnostic_sub: Any = None
        self.enabled = False
        self.enable_attempted = False
        self._wuji: dict[str, Any] = {}

    def connect(self) -> str:
        from wuji_sdk import (
            DeviceType,
            HandModel,
            Handedness,
            JointCommand,
            RetargetSession,
            SdkManager,
            WujiHand2,
        )

        self._wuji = {
            "JointCommand": JointCommand,
        }
        manager = SdkManager.instance()
        self.manager = manager
        devices = [
            device
            for device in manager.scan()
            if device.device_type == DeviceType.WujiHand2
        ]
        if self.hand_sn is not None:
            devices = [device for device in devices if device.sn == self.hand_sn]
            if not devices:
                raise ConfigurationError(
                    f"Wuji Hand 2 serial {self.hand_sn!r} was not discovered"
                )
        if len(devices) != 1:
            qualifier = "matching " if self.hand_sn is not None else ""
            raise ConfigurationError(
                f"expected exactly one {qualifier}Wuji Hand 2; found {len(devices)}"
            )

        device = devices[0]
        hand = manager.connect(sn=device.sn, device_name="mocap_wuji_hand")
        self.hand = hand
        if not isinstance(hand, WujiHand2):
            raise ConfigurationError(f"{device.sn} is not a Wuji Hand 2")
        online = hand.online_joints_count().get()
        if online != TOTAL_WUJI_JOINTS:
            raise ConfigurationError(
                f"Wuji Hand 2 has {online}/{TOTAL_WUJI_JOINTS} joints online"
            )
        side = normalize_handedness(hand.handedness().get())
        retarget_side = Handedness.Left if side == "left" else Handedness.Right
        session = RetargetSession.for_hand(HandModel.WujiHand2, side=retarget_side)

        self.side = side
        self.session = session
        LOGGER.info(
            "Connected read-only to %s (%s, %d joints online)",
            hand.serial_number,
            side,
            online,
        )
        return side

    def retarget(self, keypoints: Any) -> npt.NDArray[np.float32]:
        if self.session is None:
            raise RuntimeError("WujiTarget is not connected")
        validate_landmarks(keypoints)
        return validate_qpos(self.session.step(keypoints))

    def reset_retarget(self) -> None:
        if self.session is not None:
            self.session.reset()

    def prepare_actuation(self) -> None:
        if self.hand is None:
            raise RuntimeError("WujiTarget is not connected")
        self.hand.effort_limit().set(EFFORT_LIMIT_AMPS)
        self.hand.mit_params().set((MIT_KP, MIT_KD))
        # Open every channel that can fail before motors are enabled.
        self.publisher = self.hand.joint_command().publish()
        self.joint_state_sub = self.hand.joint_states().subscribe()
        self.diagnostic_sub = self.hand.joint_diagnostics().subscribe()

    @staticmethod
    def _drain_latest(subscription: Any) -> Any:
        latest = None
        while True:
            frame = subscription.recv()
            if frame is None:
                return latest
            latest = frame

    def latest_actual_positions(self) -> npt.NDArray[np.float32] | None:
        if self.joint_state_sub is None:
            return None
        frame = self._drain_latest(self.joint_state_sub)
        if frame is None:
            return None
        if len(frame.joints) != TOTAL_WUJI_JOINTS:
            raise ConfigurationError(
                f"joint-state frame contains {len(frame.joints)}/20 joints"
            )
        by_nid = {entry.nid: entry for entry in frame.joints}
        if set(by_nid) != set(EXPECTED_WUJI_NIDS):
            raise ConfigurationError(
                f"joint-state frame has unexpected node IDs: {sorted(by_nid)}"
            )
        return validate_qpos([by_nid[nid].position for nid in EXPECTED_WUJI_NIDS])

    def enable(self) -> None:
        if self.hand is None:
            raise RuntimeError("WujiTarget is not connected")
        self.enable_attempted = True
        self.hand.enable()
        self.enabled = True

    def all_motors_enabled(self) -> bool:
        if self.diagnostic_sub is None:
            return False
        frame = self._drain_latest(self.diagnostic_sub)
        if frame is None:
            return False
        if len(frame.joints) != TOTAL_WUJI_JOINTS:
            return False
        if {entry.nid for entry in frame.joints} != set(EXPECTED_WUJI_NIDS):
            return False
        return all(entry.status_word.ext_state == 2 for entry in frame.joints)

    def send(self, qpos: Any) -> None:
        if self.publisher is None or not self.enabled:
            raise RuntimeError("Wuji motors are not ready for commands")
        positions = validate_qpos(qpos)
        command_type = self._wuji["JointCommand"]
        self.publisher.send(
            [command_type(float(position), 0.0, 0.0) for position in positions]
        )

    def close(self) -> None:
        if self.hand is not None and (self.enabled or self.enable_attempted):
            try:
                self.hand.disable()
            except Exception:
                LOGGER.exception("Wuji disable failed; requesting emergency stop")
                with contextlib.suppress(Exception):
                    self.hand.emergency_stop()
            finally:
                self.enabled = False
                self.enable_attempted = False
        for resource in (
            self.publisher,
            self.joint_state_sub,
            self.diagnostic_sub,
        ):
            if resource is not None:
                with contextlib.suppress(Exception):
                    resource.close()
        if self.manager is not None:
            with contextlib.suppress(Exception):
                self.manager.disconnect_all()


def _pump_tracking(
    source: MocapSource,
    builder: HandLandmarkBuilder,
    target: WujiTarget,
    watchdog: FreshnessWatchdog,
) -> npt.NDArray[np.float32] | None:
    raw = source.poll_latest()
    now = time.monotonic()
    if raw is None:
        watchdog.require_fresh(now)
        return None
    frame = builder.build(raw)
    qpos = target.retarget(frame.keypoints)
    watchdog.mark_valid(frame.received_at)
    return qpos


def _wait_with_tracking(
    predicate: Callable[[], Any],
    deadline: float,
    source: MocapSource,
    builder: HandLandmarkBuilder,
    target: WujiTarget,
    watchdog: FreshnessWatchdog,
    description: str,
) -> tuple[Any, npt.NDArray[np.float32]]:
    latest_qpos: npt.NDArray[np.float32] | None = None
    next_poll = time.monotonic()
    while time.monotonic() < deadline:
        now = time.monotonic()
        if now >= next_poll:
            updated = _pump_tracking(source, builder, target, watchdog)
            if updated is not None:
                latest_qpos = updated
            next_poll = now + FRAME_BUDGET_SECONDS
        result = predicate()
        if result is not None and result is not False and latest_qpos is not None:
            return result, latest_qpos
        watchdog.require_fresh(time.monotonic())
        time.sleep(0.001)
    raise ConfigurationError(f"timed out waiting for {description}")


def _activate_motors(
    source: MocapSource,
    builder: HandLandmarkBuilder,
    target: WujiTarget,
    watchdog: FreshnessWatchdog,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    target.prepare_actuation()
    actual, _ = _wait_with_tracking(
        target.latest_actual_positions,
        time.monotonic() + JOINT_STATE_TIMEOUT_SECONDS,
        source,
        builder,
        target,
        watchdog,
        "a complete Wuji joint-state frame",
    )
    target.enable()
    _, enabled_qpos = _wait_with_tracking(
        lambda: True if target.all_motors_enabled() else None,
        time.monotonic() + ENABLE_TIMEOUT_SECONDS,
        source,
        builder,
        target,
        watchdog,
        "all Wuji motors to report Enabled",
    )
    LOGGER.warning("All motors enabled; beginning tracked command stream")
    return np.asarray(actual, dtype=np.float32), enabled_qpos


def run_bridge(
    config: BridgeConfig,
    source_factory: Callable[[BridgeConfig, str], MocapSource] = MocapSource,
    target_factory: Callable[[str | None], WujiTarget] = WujiTarget,
) -> None:
    target = target_factory(config.hand_sn)
    source: MocapSource | None = None
    try:
        side = target.connect()
        source = source_factory(config, side)
        source.open()
        builder = HandLandmarkBuilder(side)
        watchdog = FreshnessWatchdog()
        startup_deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        latest_qpos: npt.NDArray[np.float32] | None = None
        next_poll = time.monotonic()
        last_invalid_log = 0.0

        while latest_qpos is None or not builder.calibration_ready:
            now = time.monotonic()
            if now >= startup_deadline and watchdog.last_valid_at is None:
                raise TrackingStaleError(
                    f"no valid Axis hand frame arrived within "
                    f"{STARTUP_TIMEOUT_SECONDS:.0f} seconds"
                )
            if now < next_poll:
                watchdog.require_fresh(now)
                time.sleep(min(0.001, next_poll - now))
                continue
            next_poll = now + FRAME_BUDGET_SECONDS
            try:
                updated = _pump_tracking(source, builder, target, watchdog)
            except FrameValidationError as exc:
                builder.reset()
                target.reset_retarget()
                if now - last_invalid_log >= 1.0:
                    LOGGER.warning("Rejected mocap frame during preflight: %s", exc)
                    last_invalid_log = now
                watchdog.require_fresh(now)
                continue
            if updated is not None:
                latest_qpos = updated
            watchdog.require_fresh(time.monotonic())

        LOGGER.info(
            "Preflight complete: %d valid frames, %s avatar side",
            TIP_CALIBRATION_FRAMES,
            side,
        )

        blend_from: npt.NDArray[np.float32] | None = None
        blend_started = 0.0
        if config.enable_motors:
            blend_from, latest_qpos = _activate_motors(
                source, builder, target, watchdog
            )
            blend_started = time.monotonic()
        else:
            LOGGER.info(
                "Dry-run active: motors will not be configured, enabled, or commanded"
            )

        last_report = time.monotonic()
        report_started = last_report
        report_frames = 0
        next_poll = time.monotonic()
        while True:
            now = time.monotonic()
            if now < next_poll:
                watchdog.require_fresh(now)
                time.sleep(min(0.001, next_poll - now))
                continue
            next_poll = now + FRAME_BUDGET_SECONDS

            updated = _pump_tracking(source, builder, target, watchdog)
            if updated is not None:
                latest_qpos = updated
                report_frames += 1

            watchdog.require_fresh(time.monotonic())
            if config.enable_motors:
                command = latest_qpos
                if blend_from is not None:
                    elapsed = time.monotonic() - blend_started
                    alpha = smoothstep(elapsed / STARTUP_BLEND_SECONDS)
                    command = (blend_from + (latest_qpos - blend_from) * alpha).astype(
                        np.float32
                    )
                    if alpha >= 1.0:
                        blend_from = None
                target.send(command)
            if now - last_report >= 1.0 and latest_qpos is not None:
                mode = "LIVE" if config.enable_motors else "DRY-RUN"
                report_elapsed = max(time.monotonic() - report_started, 1e-6)
                LOGGER.info(
                    "%s side=%s input=%.1fHz qpos=[%+.3f, %+.3f]",
                    mode,
                    side,
                    report_frames / report_elapsed,
                    float(latest_qpos.min()),
                    float(latest_qpos.max()),
                )
                last_report = now
                report_started = time.monotonic()
                report_frames = 0
    finally:
        target.close()
        if source is not None:
            source.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retarget Axis Studio BVH hand data to one Wuji Hand 2."
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=DEFAULT_UDP_PORT,
        help=f"local MocapApi UDP port (default: {DEFAULT_UDP_PORT})",
    )
    parser.add_argument(
        "--bvh-rotation",
        choices=("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"),
        default=DEFAULT_ROTATION,
        help=f"Axis Studio BVH rotation order (default: {DEFAULT_ROTATION})",
    )
    parser.add_argument(
        "--avatar-name",
        help="Axis avatar name; required when multiple avatars are broadcast",
    )
    parser.add_argument(
        "--hand-sn",
        help="Wuji Hand 2 serial; required when multiple hands are discovered",
    )
    parser.add_argument(
        "--enable-motors",
        action="store_true",
        help="configure, enable, and command motors (default is dry-run)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_argument_parser().parse_args(argv)
    if not 1 <= args.udp_port <= 65535:
        LOGGER.error("--udp-port must be between 1 and 65535")
        return 2
    config = BridgeConfig(
        udp_port=args.udp_port,
        bvh_rotation=args.bvh_rotation,
        avatar_name=args.avatar_name,
        hand_sn=args.hand_sn,
        enable_motors=args.enable_motors,
    )
    try:
        run_bridge(config)
        return 0
    except KeyboardInterrupt:
        LOGGER.info("Stopped by operator")
        return 130
    except TrackingStaleError as exc:
        LOGGER.error("Tracking stopped: %s", exc)
        return 2
    except Exception as exc:
        LOGGER.error("Bridge failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

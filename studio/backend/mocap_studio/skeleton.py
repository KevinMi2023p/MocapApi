from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin, sqrt
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class JointDefinition:
    name: str
    parent: str | None
    offset: tuple[float, float, float]
    sensor_id: int | None = None


# The public MocapApi enum order. The first 59 entries are the long-standing
# Axis/Neuron hierarchy; Spine3 was added later. The wire decoder determines the
# count from each packet and never assumes that both generations are identical.
JOINTS: tuple[JointDefinition, ...] = (
    JointDefinition("Hips", None, (0.0, 1.02, 0.0), 1),
    JointDefinition("RightUpLeg", "Hips", (0.12, -0.09, 0.0), 16),
    JointDefinition("RightLeg", "RightUpLeg", (0.02, -0.43, 0.0), 17),
    JointDefinition("RightFoot", "RightLeg", (0.0, -0.43, 0.08), 18),
    JointDefinition("LeftUpLeg", "Hips", (-0.12, -0.09, 0.0), 13),
    JointDefinition("LeftLeg", "LeftUpLeg", (-0.02, -0.43, 0.0), 14),
    JointDefinition("LeftFoot", "LeftLeg", (0.0, -0.43, 0.08), 15),
    JointDefinition("Spine", "Hips", (0.0, 0.15, 0.0), 2),
    JointDefinition("Spine1", "Spine", (0.0, 0.16, 0.0), 3),
    JointDefinition("Spine2", "Spine1", (0.0, 0.16, 0.0), 4),
    JointDefinition("Neck", "Spine2", (0.0, 0.14, 0.0), 5),
    JointDefinition("Neck1", "Neck", (0.0, 0.07, 0.0)),
    JointDefinition("Head", "Neck1", (0.0, 0.15, 0.0), 6),
    JointDefinition("RightShoulder", "Spine2", (0.15, 0.05, 0.0)),
    JointDefinition("RightArm", "RightShoulder", (0.24, -0.02, 0.0), 10),
    JointDefinition("RightForeArm", "RightArm", (0.27, -0.02, 0.0), 11),
    JointDefinition("RightHand", "RightForeArm", (0.21, -0.01, 0.0), 12),
    JointDefinition("RightHandThumb1", "RightHand", (0.05, -0.02, 0.04)),
    JointDefinition("RightHandThumb2", "RightHandThumb1", (0.035, 0.0, 0.025)),
    JointDefinition("RightHandThumb3", "RightHandThumb2", (0.03, 0.0, 0.02)),
    JointDefinition("RightInHandIndex", "RightHand", (0.055, 0.0, 0.025)),
    JointDefinition("RightHandIndex1", "RightInHandIndex", (0.035, 0.0, 0.0)),
    JointDefinition("RightHandIndex2", "RightHandIndex1", (0.025, 0.0, 0.0)),
    JointDefinition("RightHandIndex3", "RightHandIndex2", (0.02, 0.0, 0.0)),
    JointDefinition("RightInHandMiddle", "RightHand", (0.06, 0.0, 0.008)),
    JointDefinition("RightHandMiddle1", "RightInHandMiddle", (0.04, 0.0, 0.0)),
    JointDefinition("RightHandMiddle2", "RightHandMiddle1", (0.03, 0.0, 0.0)),
    JointDefinition("RightHandMiddle3", "RightHandMiddle2", (0.022, 0.0, 0.0)),
    JointDefinition("RightInHandRing", "RightHand", (0.055, 0.0, -0.01)),
    JointDefinition("RightHandRing1", "RightInHandRing", (0.037, 0.0, 0.0)),
    JointDefinition("RightHandRing2", "RightHandRing1", (0.027, 0.0, 0.0)),
    JointDefinition("RightHandRing3", "RightHandRing2", (0.02, 0.0, 0.0)),
    JointDefinition("RightInHandPinky", "RightHand", (0.045, 0.0, -0.025)),
    JointDefinition("RightHandPinky1", "RightInHandPinky", (0.03, 0.0, 0.0)),
    JointDefinition("RightHandPinky2", "RightHandPinky1", (0.022, 0.0, 0.0)),
    JointDefinition("RightHandPinky3", "RightHandPinky2", (0.018, 0.0, 0.0)),
    JointDefinition("LeftShoulder", "Spine2", (-0.15, 0.05, 0.0)),
    JointDefinition("LeftArm", "LeftShoulder", (-0.24, -0.02, 0.0), 7),
    JointDefinition("LeftForeArm", "LeftArm", (-0.27, -0.02, 0.0), 8),
    JointDefinition("LeftHand", "LeftForeArm", (-0.21, -0.01, 0.0), 9),
    JointDefinition("LeftHandThumb1", "LeftHand", (-0.05, -0.02, 0.04)),
    JointDefinition("LeftHandThumb2", "LeftHandThumb1", (-0.035, 0.0, 0.025)),
    JointDefinition("LeftHandThumb3", "LeftHandThumb2", (-0.03, 0.0, 0.02)),
    JointDefinition("LeftInHandIndex", "LeftHand", (-0.055, 0.0, 0.025)),
    JointDefinition("LeftHandIndex1", "LeftInHandIndex", (-0.035, 0.0, 0.0)),
    JointDefinition("LeftHandIndex2", "LeftHandIndex1", (-0.025, 0.0, 0.0)),
    JointDefinition("LeftHandIndex3", "LeftHandIndex2", (-0.02, 0.0, 0.0)),
    JointDefinition("LeftInHandMiddle", "LeftHand", (-0.06, 0.0, 0.008)),
    JointDefinition("LeftHandMiddle1", "LeftInHandMiddle", (-0.04, 0.0, 0.0)),
    JointDefinition("LeftHandMiddle2", "LeftHandMiddle1", (-0.03, 0.0, 0.0)),
    JointDefinition("LeftHandMiddle3", "LeftHandMiddle2", (-0.022, 0.0, 0.0)),
    JointDefinition("LeftInHandRing", "LeftHand", (-0.055, 0.0, -0.01)),
    JointDefinition("LeftHandRing1", "LeftInHandRing", (-0.037, 0.0, 0.0)),
    JointDefinition("LeftHandRing2", "LeftHandRing1", (-0.027, 0.0, 0.0)),
    JointDefinition("LeftHandRing3", "LeftHandRing2", (-0.02, 0.0, 0.0)),
    JointDefinition("LeftInHandPinky", "LeftHand", (-0.045, 0.0, -0.025)),
    JointDefinition("LeftHandPinky1", "LeftInHandPinky", (-0.03, 0.0, 0.0)),
    JointDefinition("LeftHandPinky2", "LeftHandPinky1", (-0.022, 0.0, 0.0)),
    JointDefinition("LeftHandPinky3", "LeftHandPinky2", (-0.018, 0.0, 0.0)),
    JointDefinition("Spine3", "Spine2", (0.0, 0.08, 0.0)),
)


def normalize_quaternion(q: Sequence[float]) -> tuple[float, float, float, float]:
    length = sqrt(sum(value * value for value in q))
    if length < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(value / length for value in q)  # type: ignore[return-value]


def multiply_quaternion(
    left: Sequence[float], right: Sequence[float]
) -> tuple[float, float, float, float]:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return normalize_quaternion(
        (
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
            lw * rw - lx * rx - ly * ry - lz * rz,
        )
    )


def euler_to_quaternion(
    angles_degrees: Sequence[float], order: str = "YXZ"
) -> tuple[float, float, float, float]:
    """Convert intrinsic Euler rotations to an x/y/z/w quaternion."""
    values = dict(zip("XYZ", (radians(value) for value in angles_degrees), strict=True))
    result = (0.0, 0.0, 0.0, 1.0)
    for axis in order:
        half = values[axis] / 2.0
        s, c = sin(half), cos(half)
        rotation = {
            "X": (s, 0.0, 0.0, c),
            "Y": (0.0, s, 0.0, c),
            "Z": (0.0, 0.0, s, c),
        }[axis]
        result = multiply_quaternion(result, rotation)
    return result


def rotate_vector(
    quaternion: Sequence[float], vector: Sequence[float]
) -> tuple[float, float, float]:
    qx, qy, qz, qw = quaternion
    vx, vy, vz = vector
    # Optimized q * v * conjugate(q).
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + qy * tz - qz * ty,
        vy + qw * ty + qz * tx - qx * tz,
        vz + qw * tz + qx * ty - qy * tx,
    )


def finite_values(values: Iterable[float], maximum: float = 1_000_000.0) -> bool:
    return all(value == value and abs(value) <= maximum for value in values)

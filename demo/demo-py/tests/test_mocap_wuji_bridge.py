from pathlib import Path
from types import SimpleNamespace
import sys
import time

import numpy as np
import pytest


DEMO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO_ROOT))

import mocap_wuji_bridge as bridge  # noqa: E402


def make_raw_pose(side: str, *, rotation=None, posture_index: int = 1):
    sign = 1.0 if side == "left" else -1.0
    positions = {bridge._joint_name(side): np.zeros(3)}
    rotations = {}
    for finger_index, finger in enumerate(bridge.FINGER_NAMES):
        y = 0.01 + finger_index * 0.012
        positions[bridge._joint_name(side, finger, 1)] = np.array(
            [sign * 0.030, y, 0.0]
        )
        positions[bridge._joint_name(side, finger, 2)] = np.array(
            [sign * 0.050, y, 0.0]
        )
        positions[bridge._joint_name(side, finger, 3)] = np.array(
            [sign * 0.070, y, 0.0]
        )
        rotations[finger] = np.asarray(
            rotation if rotation is not None else (0.0, 0.0, 0.0, 1.0)
        )
    return bridge.RawHandPose(
        avatar_name="Actor",
        posture_index=posture_index,
        received_at=1.0 + posture_index / 120.0,
        positions=positions,
        distal_rotations_xyzw=rotations,
    )


def live_raw_pose(side: str, posture_index: int):
    raw = make_raw_pose(side, posture_index=posture_index)
    return bridge.RawHandPose(
        avatar_name=raw.avatar_name,
        posture_index=raw.posture_index,
        received_at=time.monotonic(),
        positions=raw.positions,
        distal_rotations_xyzw=raw.distal_rotations_xyzw,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("left", "left"),
        ("RIGHT", "right"),
        ("Handedness.Left", "left"),
        ("Handedness.Right", "right"),
    ],
)
def test_normalize_handedness(value, expected):
    assert bridge.normalize_handedness(value) == expected


def test_unknown_handedness_fails_closed():
    with pytest.raises(bridge.ConfigurationError, match="unknown"):
        bridge.normalize_handedness("ambidextrous")


def test_wxyz_conversion_and_quaternion_rotation():
    converted = bridge.wxyz_to_xyzw((1.0, 0.0, 0.0, 0.0))
    np.testing.assert_allclose(converted, (0.0, 0.0, 0.0, 1.0))

    half = np.sqrt(0.5)
    rotated = bridge.rotate_vector_xyzw(
        (0.0, 0.0, half, half),
        (1.0, 0.0, 0.0),
    )
    np.testing.assert_allclose(rotated, (0.0, 1.0, 0.0), atol=1e-7)


@pytest.mark.parametrize(("side", "sign"), [("left", 1.0), ("right", -1.0)])
def test_landmark_mapping_and_fingertip_calibration(side, sign):
    builder = bridge.HandLandmarkBuilder(side)
    frame = None
    for posture_index in range(1, bridge.TIP_CALIBRATION_FRAMES + 1):
        frame = builder.build(make_raw_pose(side, posture_index=posture_index))

    assert frame is not None
    assert frame.calibration_ready
    assert frame.keypoints.shape == (21, 3)
    assert frame.keypoints.dtype == np.float32
    np.testing.assert_allclose(frame.keypoints[0], 0.0)

    for finger in bridge.FINGER_NAMES:
        base = bridge.MEDIAPIPE_BASE_INDEX[finger]
        np.testing.assert_allclose(
            frame.keypoints[base : base + 3, 0],
            sign * np.array((0.030, 0.050, 0.070)),
        )
        expected_tip_x = sign * (0.070 + bridge.TIP_RATIOS[finger] * 0.020)
        assert frame.keypoints[base + 3, 0] == pytest.approx(expected_tip_x)


def test_tip_uses_distal_joint_rotation():
    half = np.sqrt(0.5)
    builder = bridge.HandLandmarkBuilder("right", calibration_frames=1)
    frame = builder.build(make_raw_pose("right", rotation=(0.0, 0.0, half, half)))
    index_tip = frame.keypoints[bridge.MEDIAPIPE_BASE_INDEX["Index"] + 3]
    index_distal = frame.keypoints[bridge.MEDIAPIPE_BASE_INDEX["Index"] + 2]
    expected_offset = np.array((0.0, -bridge.TIP_RATIOS["Index"] * 0.020, 0.0))
    np.testing.assert_allclose(index_tip - index_distal, expected_offset, atol=1e-7)


def test_calibration_requires_consecutive_samples_after_reset():
    builder = bridge.HandLandmarkBuilder("right", calibration_frames=3)
    builder.build(make_raw_pose("right", posture_index=1))
    builder.build(make_raw_pose("right", posture_index=2))
    assert not builder.calibration_ready
    builder.reset()
    for posture_index in (3, 4):
        assert not builder.build(
            make_raw_pose("right", posture_index=posture_index)
        ).calibration_ready
    assert builder.build(make_raw_pose("right", posture_index=5)).calibration_ready


def test_degenerate_geometry_is_rejected():
    raw = make_raw_pose("right")
    positions = dict(raw.positions)
    positions[bridge._joint_name("right", "Index", 3)] = positions[
        bridge._joint_name("right", "Index", 2)
    ]
    bad = bridge.RawHandPose(
        avatar_name=raw.avatar_name,
        posture_index=raw.posture_index,
        received_at=raw.received_at,
        positions=positions,
        distal_rotations_xyzw=raw.distal_rotations_xyzw,
    )
    with pytest.raises(bridge.FrameValidationError, match="Index distal"):
        bridge.HandLandmarkBuilder("right").build(bad)


def test_watchdog_expires_at_250_milliseconds():
    watchdog = bridge.FreshnessWatchdog()
    watchdog.mark_valid(10.0)
    assert not watchdog.expired(10.249999)
    assert watchdog.expired(10.250)
    with pytest.raises(bridge.TrackingStaleError):
        watchdog.require_fresh(10.251)


def test_qpos_contract_rejects_wrong_shape_and_nonfinite_values():
    good = bridge.validate_qpos(np.arange(20, dtype=np.float64))
    assert good.shape == (20,)
    assert good.dtype == np.float32
    with pytest.raises(bridge.FrameValidationError, match="expected"):
        bridge.validate_qpos(np.zeros(19))
    bad = np.zeros(20)
    bad[2] = np.nan
    with pytest.raises(bridge.FrameValidationError, match="NaN"):
        bridge.validate_qpos(bad)


def test_wuji_send_preserves_firmware_order():
    sent = []

    class Command:
        def __init__(self, position, velocity, effort):
            self.position = position
            self.velocity = velocity
            self.effort = effort

    target = bridge.WujiTarget()
    target.publisher = SimpleNamespace(send=sent.append)
    target.enabled = True
    target._wuji = {"JointCommand": Command}
    qpos = np.linspace(-1.0, 1.0, 20, dtype=np.float32)
    target.send(qpos)

    assert len(sent) == 1
    assert [command.position for command in sent[0]] == pytest.approx(qpos.tolist())
    assert all(command.velocity == 0.0 for command in sent[0])
    assert all(command.effort == 0.0 for command in sent[0])


def test_actual_joint_positions_are_reordered_by_node_id():
    entries = [
        SimpleNamespace(nid=nid, position=float(index))
        for index, nid in enumerate(bridge.EXPECTED_WUJI_NIDS)
    ]
    frames = [SimpleNamespace(joints=list(reversed(entries))), None]
    target = bridge.WujiTarget()
    target.joint_state_sub = SimpleNamespace(recv=lambda: frames.pop(0))

    actual = target.latest_actual_positions()

    np.testing.assert_allclose(actual, np.arange(20, dtype=np.float32))


def test_wuji_cleanup_is_read_only_until_enable_is_attempted():
    actions = []
    target = bridge.WujiTarget()
    target.hand = SimpleNamespace(
        disable=lambda: actions.append("disable"),
        emergency_stop=lambda: actions.append("emergency_stop"),
    )
    target.manager = SimpleNamespace(
        disconnect_all=lambda: actions.append("disconnect")
    )

    target.close()

    assert actions == ["disconnect"]


def test_wuji_cleanup_disables_after_a_partial_enable_attempt():
    actions = []
    target = bridge.WujiTarget()
    target.hand = SimpleNamespace(
        disable=lambda: actions.append("disable"),
        emergency_stop=lambda: actions.append("emergency_stop"),
    )
    target.manager = SimpleNamespace(
        disconnect_all=lambda: actions.append("disconnect")
    )
    target.enable_attempted = True

    target.close()

    assert actions == ["disable", "disconnect"]
    assert not target.enable_attempted


def test_default_cli_is_dry_run():
    args = bridge.build_argument_parser().parse_args([])
    assert args.udp_port == 8088
    assert args.bvh_rotation == "YXZ"
    assert not args.enable_motors


def test_run_bridge_dry_run_never_prepares_or_enables_motors(monkeypatch):
    class EndTest(Exception):
        pass

    class FakeSource:
        def __init__(self):
            self.frame = 0
            self.opened = False
            self.closed = False

        def open(self):
            self.opened = True

        def poll_latest(self):
            self.frame += 1
            if self.frame > bridge.TIP_CALIBRATION_FRAMES:
                raise EndTest
            return live_raw_pose("right", self.frame)

        def close(self):
            self.closed = True

    class FakeTarget:
        def __init__(self):
            self.prepared = False
            self.enabled = False
            self.closed = False

        def connect(self):
            return "right"

        def retarget(self, keypoints):
            return np.zeros(20, dtype=np.float32)

        def reset_retarget(self):
            pass

        def prepare_actuation(self):
            self.prepared = True

        def close(self):
            self.closed = True

    source = FakeSource()
    target = FakeTarget()
    monkeypatch.setattr(bridge, "FRAME_BUDGET_SECONDS", 0.0)

    with pytest.raises(EndTest):
        bridge.run_bridge(
            bridge.BridgeConfig(),
            source_factory=lambda _config, _side: source,
            target_factory=lambda _sn: target,
        )

    assert source.opened and source.closed
    assert target.closed
    assert not target.prepared
    assert not target.enabled


def test_motor_path_prepares_enables_sends_and_always_cleans_up(monkeypatch):
    class EndTest(Exception):
        pass

    class FakeSource:
        def __init__(self):
            self.frame = 0
            self.closed = False

        def open(self):
            pass

        def poll_latest(self):
            self.frame += 1
            return live_raw_pose("left", self.frame)

        def close(self):
            self.closed = True

    class FakeTarget:
        def __init__(self):
            self.actions = []
            self.enabled = False
            self.closed = False

        def connect(self):
            self.actions.append("connect")
            return "left"

        def retarget(self, keypoints):
            return np.linspace(-0.5, 0.5, 20, dtype=np.float32)

        def reset_retarget(self):
            self.actions.append("reset")

        def prepare_actuation(self):
            self.actions.append("prepare")

        def latest_actual_positions(self):
            return np.zeros(20, dtype=np.float32)

        def enable(self):
            self.actions.append("enable")
            self.enabled = True

        def all_motors_enabled(self):
            self.actions.append("diagnostics")
            return True

        def send(self, qpos):
            self.actions.append("send")
            raise EndTest

        def close(self):
            self.actions.append("close")
            self.enabled = False
            self.closed = True

    source = FakeSource()
    target = FakeTarget()
    monkeypatch.setattr(bridge, "FRAME_BUDGET_SECONDS", 0.0)

    with pytest.raises(EndTest):
        bridge.run_bridge(
            bridge.BridgeConfig(enable_motors=True),
            source_factory=lambda _config, _side: source,
            target_factory=lambda _sn: target,
        )

    assert target.actions.index("prepare") < target.actions.index("enable")
    assert target.actions.index("enable") < target.actions.index("diagnostics")
    assert target.actions.index("diagnostics") < target.actions.index("send")
    assert target.actions[-1] == "close"
    assert target.closed and source.closed
    assert not target.enabled


def test_real_wuji_retarget_contract():
    wuji_sdk = pytest.importorskip("wuji_sdk")
    builder = bridge.HandLandmarkBuilder("right", calibration_frames=1)
    keypoints = builder.build(make_raw_pose("right")).keypoints
    session = wuji_sdk.RetargetSession.for_hand(
        wuji_sdk.HandModel.WujiHand2,
        side=wuji_sdk.Handedness.Right,
    )
    qpos = session.step(keypoints)
    assert qpos.shape == (20,)
    assert qpos.dtype == np.float32
    assert np.isfinite(qpos).all()

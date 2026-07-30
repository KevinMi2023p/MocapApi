import ipaddress
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


def test_watchdog_reset_clears_the_last_valid_frame():
    watchdog = bridge.FreshnessWatchdog()
    watchdog.mark_valid(10.0)
    watchdog.reset()
    assert not watchdog.expired(20.0)
    watchdog.require_fresh(20.0)


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


def test_wuji_send_preserves_firmware_order_per_hand():
    sent = {"left": [], "right": []}

    class Command:
        def __init__(self, position, velocity, effort):
            self.position = position
            self.velocity = velocity
            self.effort = effort

    target = bridge.WujiTarget()
    for side in ("left", "right"):
        unit = bridge.WujiHandUnit(sn=f"SN-{side}", side=side)
        unit.publisher = SimpleNamespace(send=sent[side].append)
        unit.enabled = True
        target.hands[side] = unit
    target._wuji = {"JointCommand": Command}
    left_qpos = np.linspace(-1.0, 1.0, 20, dtype=np.float32)
    right_qpos = np.linspace(-0.5, 0.5, 20, dtype=np.float32)
    target.send({"left": left_qpos, "right": right_qpos})

    for side, qpos in (("left", left_qpos), ("right", right_qpos)):
        assert len(sent[side]) == 1
        assert [command.position for command in sent[side][0]] == pytest.approx(
            qpos.tolist()
        )
        assert all(command.velocity == 0.0 for command in sent[side][0])
        assert all(command.effort == 0.0 for command in sent[side][0])


def test_wuji_send_requires_a_command_for_every_connected_hand():
    target = bridge.WujiTarget()
    target.hands["left"] = bridge.WujiHandUnit(sn="SN-left", side="left")
    target.hands["right"] = bridge.WujiHandUnit(sn="SN-right", side="right")
    with pytest.raises(RuntimeError, match="expected commands"):
        target.send({"left": np.zeros(20, dtype=np.float32)})


def test_actual_joint_positions_are_reordered_by_node_id():
    entries = [
        SimpleNamespace(nid=nid, position=float(index))
        for index, nid in enumerate(bridge.EXPECTED_WUJI_NIDS)
    ]
    frames = [SimpleNamespace(joints=list(reversed(entries))), None]
    target = bridge.WujiTarget()
    unit = bridge.WujiHandUnit(sn="SN-right", side="right")
    unit.joint_state_sub = SimpleNamespace(recv=lambda: frames.pop(0))
    target.hands["right"] = unit

    actual = target.latest_actual_positions()

    assert set(actual) == {"right"}
    np.testing.assert_allclose(actual["right"], np.arange(20, dtype=np.float32))


def test_actual_joint_positions_wait_for_every_hand():
    entries = [
        SimpleNamespace(nid=nid, position=float(index))
        for index, nid in enumerate(bridge.EXPECTED_WUJI_NIDS)
    ]
    right_frames = [SimpleNamespace(joints=entries), None, None]
    left_frames = [None]
    target = bridge.WujiTarget()
    right = bridge.WujiHandUnit(sn="SN-right", side="right")
    right.joint_state_sub = SimpleNamespace(recv=lambda: right_frames.pop(0))
    left = bridge.WujiHandUnit(sn="SN-left", side="left")
    left.joint_state_sub = SimpleNamespace(recv=lambda: left_frames.pop(0))
    target.hands["right"] = right
    target.hands["left"] = left

    assert target.latest_actual_positions() is None

    left_frames.extend([SimpleNamespace(joints=entries), None])
    actual = target.latest_actual_positions()
    assert set(actual) == {"left", "right"}


def make_diagnostic_frame(currents, voltages, *, enabled=True):
    return SimpleNamespace(
        joints=[
            SimpleNamespace(
                nid=nid,
                current=current,
                vbus_v_fb=voltage,
                status_word=SimpleNamespace(ext_state=2 if enabled else 1),
            )
            for nid, current, voltage in zip(
                bridge.EXPECTED_WUJI_NIDS, currents, voltages
            )
        ]
    )


def test_power_reading_uses_median_bus_voltage_and_absolute_motor_current():
    currents = np.linspace(-1.0, 1.0, bridge.TOTAL_WUJI_JOINTS)
    voltages = np.full(bridge.TOTAL_WUJI_JOINTS, 24.0)
    voltages[-1] = 120.0
    frames = [make_diagnostic_frame(currents, voltages), None]
    target = bridge.WujiTarget()
    unit = bridge.WujiHandUnit(sn="SN-right", side="right")
    unit.diagnostic_sub = SimpleNamespace(recv=lambda: frames.pop(0))
    target.hands["right"] = unit

    readings = target.latest_power_readings()

    assert set(readings) == {"right"}
    reading = readings["right"]
    assert reading.voltage_volts == pytest.approx(24.0)
    assert reading.current_amps == pytest.approx(np.abs(currents).sum())
    assert reading.power_watts == pytest.approx(
        np.sum(np.abs(currents) * voltages)
    )
    assert unit.motors_enabled


def test_power_recorder_uses_one_shared_elapsed_timestamp_for_both_hands():
    recorder = bridge.PowerRecorder()
    recorder.record(
        {
            "left": bridge.PowerReading(24.0, 2.0, 48.0),
            "right": bridge.PowerReading(23.5, 3.0, 70.5),
        },
        recorded_at=10.0,
    )
    recorder.record(
        {"left": bridge.PowerReading(23.8, 2.5, 59.5)},
        recorded_at=10.25,
    )

    assert [sample.side for sample in recorder.samples] == [
        "left",
        "right",
        "left",
    ]
    assert [sample.elapsed_seconds for sample in recorder.samples] == pytest.approx(
        [0.0, 0.0, 0.25]
    )


def make_cleanup_target(actions):
    target = bridge.WujiTarget()
    for side in ("left", "right"):
        unit = bridge.WujiHandUnit(sn=f"SN-{side}", side=side)
        unit.hand = SimpleNamespace(
            disable=lambda side=side: actions.append(f"disable-{side}"),
            emergency_stop=lambda side=side: actions.append(f"stop-{side}"),
        )
        target.hands[side] = unit
    target.manager = SimpleNamespace(
        disconnect_all=lambda: actions.append("disconnect")
    )
    return target


def test_wuji_cleanup_is_read_only_until_enable_is_attempted():
    actions = []
    target = make_cleanup_target(actions)

    target.close()

    assert actions == ["disconnect"]


def test_wuji_cleanup_disables_after_a_partial_enable_attempt():
    actions = []
    target = make_cleanup_target(actions)
    target.hands["left"].enable_attempted = True

    target.close()

    assert actions == ["disable-left", "disconnect"]
    assert not target.hands["left"].enable_attempted


def test_wuji_cleanup_disables_every_enabled_hand():
    actions = []
    target = make_cleanup_target(actions)
    for unit in target.hands.values():
        unit.enabled = True

    target.close()

    assert set(actions[:-1]) == {"disable-left", "disable-right"}
    assert actions[-1] == "disconnect"


def make_fake_avatar(sides, posture_index):
    joints = []
    for side in sides:
        for name in bridge.required_joint_names(side):
            joints.append(
                SimpleNamespace(
                    get_name=lambda name=name: name,
                    get_global_position=lambda: (0.1, 0.2, 0.3),
                    get_global_rotation=lambda: (1.0, 0.0, 0.0, 0.0),
                )
            )
    return SimpleNamespace(
        get_name=lambda: "Actor",
        get_avatar_posture_index=lambda: posture_index,
        get_joints=lambda: joints,
    )


def test_mocap_source_polls_every_selected_side():
    source = bridge.MocapSource(bridge.BridgeConfig(), ("left", "right"))
    avatar = make_fake_avatar(("left", "right"), posture_index=7)
    event = SimpleNamespace(
        event_type="AvatarUpdated",
        event_data=SimpleNamespace(avatar_handle=avatar),
    )
    source.mcp = SimpleNamespace(
        MCPEventType=SimpleNamespace(AvatarUpdated="AvatarUpdated"),
        MCPAvatar=lambda handle: handle,
    )
    source.app = SimpleNamespace(poll_next_event=lambda: [event])

    poses = source.poll_latest()

    assert set(poses) == {"left", "right"}
    for side, raw in poses.items():
        assert set(raw.positions) == set(bridge.required_joint_names(side))
        assert set(raw.distal_rotations_xyzw) == set(bridge.FINGER_NAMES)
    assert poses["left"].received_at == poses["right"].received_at
    # The same posture index must not be consumed twice.
    assert source.poll_latest() is None


def test_mocap_source_rejects_duplicate_or_empty_sides():
    with pytest.raises(bridge.ConfigurationError, match="unique"):
        bridge.MocapSource(bridge.BridgeConfig(), ("left", "left"))
    with pytest.raises(bridge.ConfigurationError, match="at least one"):
        bridge.MocapSource(bridge.BridgeConfig(), ())


def test_default_cli_is_dry_run():
    args = bridge.build_argument_parser().parse_args([])
    assert args.udp_port == 8088
    assert args.bvh_rotation == "YXZ"
    assert args.hand_sns is None
    assert not args.no_auto_routes
    assert not args.enable_motors


def test_cli_accepts_repeated_hand_serials():
    args = bridge.build_argument_parser().parse_args(
        ["--hand-sn", "SN-A", "--hand-sn", "SN-B"]
    )
    assert args.hand_sns == ["SN-A", "SN-B"]


def test_cli_can_disable_temporary_hand_routes():
    args = bridge.build_argument_parser().parse_args(["--no-auto-routes"])
    assert args.no_auto_routes


def test_parse_device_ipv4_accepts_wuji_udp_addresses():
    assert str(bridge.parse_device_ipv4("192.168.1.110:7447")) == "192.168.1.110"
    assert bridge.parse_device_ipv4("not-an-address") is None
    assert bridge.parse_device_ipv4("[2001:db8::1]:7447") is None


def test_temporary_routes_map_each_hand_to_its_reachable_interface(monkeypatch):
    routes = bridge.TemporaryHandRoutes()
    routes.ip_command = "/usr/sbin/ip"
    routes.ping_command = "/usr/bin/ping"
    interfaces = [
        bridge.InterfaceAddress(
            name="enp1s0",
            address=ipaddress.IPv4Address("192.168.1.10"),
            network=ipaddress.IPv4Network("192.168.1.0/24"),
        ),
        bridge.InterfaceAddress(
            name="enp2s0",
            address=ipaddress.IPv4Address("192.168.1.20"),
            network=ipaddress.IPv4Network("192.168.1.0/24"),
        ),
    ]
    reachable = {
        ("192.168.1.110", "enp1s0"),
        ("192.168.1.111", "enp2s0"),
    }
    route_commands = []
    monkeypatch.setattr(routes, "_interface_addresses", lambda: interfaces)
    monkeypatch.setattr(
        routes,
        "_is_reachable",
        lambda destination, interface: (str(destination), interface) in reachable,
    )
    monkeypatch.setattr(routes, "_exact_host_routes", lambda _destination: [])

    def record_route_command(*arguments, check=True):
        route_commands.append((arguments, check))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(routes, "_run_privileged", record_route_command)
    devices = [
        SimpleNamespace(address="192.168.1.110:7447"),
        SimpleNamespace(address="192.168.1.111:7447"),
    ]

    routes.configure(devices)

    assert route_commands == [
        (
            (
                "route",
                "replace",
                "192.168.1.110/32",
                "dev",
                "enp1s0",
                "src",
                "192.168.1.10",
            ),
            True,
        ),
        (
            (
                "route",
                "replace",
                "192.168.1.111/32",
                "dev",
                "enp2s0",
                "src",
                "192.168.1.20",
            ),
            True,
        ),
    ]
    assert routes.installed == [
        bridge.InstalledRoute(
            ipaddress.IPv4Address("192.168.1.110"), "enp1s0"
        ),
        bridge.InstalledRoute(
            ipaddress.IPv4Address("192.168.1.111"), "enp2s0"
        ),
    ]

    routes.restore()

    assert route_commands[-2:] == [
        (
            ("route", "del", "192.168.1.111/32", "dev", "enp2s0"),
            False,
        ),
        (
            ("route", "del", "192.168.1.110/32", "dev", "enp1s0"),
            False,
        ),
    ]
    assert routes.installed == []


def test_temporary_routes_fail_when_interface_probe_is_ambiguous(monkeypatch):
    routes = bridge.TemporaryHandRoutes()
    routes.ip_command = "/usr/sbin/ip"
    routes.ping_command = "/usr/bin/ping"
    interfaces = [
        bridge.InterfaceAddress(
            name=name,
            address=ipaddress.IPv4Address(address),
            network=ipaddress.IPv4Network("192.168.1.0/24"),
        )
        for name, address in (
            ("enp1s0", "192.168.1.10"),
            ("enp2s0", "192.168.1.20"),
        )
    ]
    monkeypatch.setattr(routes, "_interface_addresses", lambda: interfaces)
    monkeypatch.setattr(
        routes, "_is_reachable", lambda _destination, _interface: False
    )

    with pytest.raises(bridge.ConfigurationError, match="cannot determine"):
        routes.configure([SimpleNamespace(address="192.168.1.110:7447")])


class EndTest(Exception):
    pass


class FakeSource:
    def __init__(self, sides, stop_after=None):
        self.sides = tuple(sides)
        self.stop_after = stop_after
        self.frame = 0
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True

    def poll_latest(self):
        self.frame += 1
        if self.stop_after is not None and self.frame > self.stop_after:
            raise EndTest
        return {side: live_raw_pose(side, self.frame) for side in self.sides}

    def close(self):
        self.closed = True


class FakeTarget:
    def __init__(self, sides):
        self.sides = tuple(sides)
        self.actions = []
        self.sent = []
        self.prepared = False
        self.enabled = False
        self.closed = False
        self.end_on_send = False

    def connect(self):
        self.actions.append("connect")
        return self.sides

    def retarget(self, side, keypoints):
        self.actions.append(f"retarget-{side}")
        offset = 0.1 if side == "left" else -0.1
        return np.linspace(-0.5, 0.5, 20, dtype=np.float32) + np.float32(offset)

    def reset_retarget(self):
        self.actions.append("reset")

    def prepare_actuation(self):
        self.actions.append("prepare")
        self.prepared = True

    def latest_actual_positions(self):
        return {side: np.zeros(20, dtype=np.float32) for side in self.sides}

    def enable(self):
        self.actions.append("enable")
        self.enabled = True

    def all_motors_enabled(self):
        self.actions.append("diagnostics")
        return True

    def send(self, commands):
        self.actions.append("send")
        self.sent.append(commands)
        if self.end_on_send:
            raise EndTest

    def latest_power_readings(self):
        self.actions.append("power")
        return {}

    def close(self):
        self.actions.append("close")
        self.enabled = False
        self.closed = True


def test_run_bridge_dry_run_never_prepares_or_enables_motors(monkeypatch):
    source = FakeSource(("right",), stop_after=bridge.TIP_CALIBRATION_FRAMES)
    target = FakeTarget(("right",))
    monkeypatch.setattr(bridge, "FRAME_BUDGET_SECONDS", 0.0)

    with pytest.raises(EndTest):
        bridge.run_bridge(
            bridge.BridgeConfig(),
            source_factory=lambda _config, _sides: source,
            target_factory=lambda _sns, _auto_routes: target,
        )

    assert source.opened and source.closed
    assert target.closed
    assert not target.prepared
    assert not target.enabled


def test_motor_path_prepares_enables_sends_and_always_cleans_up(monkeypatch):
    source = FakeSource(("left",))
    target = FakeTarget(("left",))
    target.end_on_send = True
    monkeypatch.setattr(bridge, "FRAME_BUDGET_SECONDS", 0.0)

    with pytest.raises(EndTest):
        bridge.run_bridge(
            bridge.BridgeConfig(enable_motors=True),
            source_factory=lambda _config, _sides: source,
            target_factory=lambda _sns, _auto_routes: target,
        )

    assert target.actions.index("prepare") < target.actions.index("enable")
    assert target.actions.index("enable") < target.actions.index("diagnostics")
    assert target.actions.index("diagnostics") < target.actions.index("send")
    assert target.actions[-1] == "close"
    assert target.closed and source.closed
    assert not target.enabled


def test_run_bridge_drives_both_hands(monkeypatch):
    source = FakeSource(("left", "right"))
    target = FakeTarget(("left", "right"))
    target.end_on_send = True
    monkeypatch.setattr(bridge, "FRAME_BUDGET_SECONDS", 0.0)

    with pytest.raises(EndTest):
        bridge.run_bridge(
            bridge.BridgeConfig(enable_motors=True),
            source_factory=lambda _config, _sides: source,
            target_factory=lambda _sns, _auto_routes: target,
        )

    assert len(target.sent) == 1
    commands = target.sent[0]
    assert set(commands) == {"left", "right"}
    for qpos in commands.values():
        assert qpos.shape == (20,)
        assert np.isfinite(qpos).all()
    assert "retarget-left" in target.actions
    assert "retarget-right" in target.actions
    assert target.closed and source.closed


def test_preflight_recovers_from_a_tracking_gap(monkeypatch):
    class GapSource(FakeSource):
        def __init__(self):
            super().__init__(
                ("left",), stop_after=bridge.TIP_CALIBRATION_FRAMES * 2
            )
            self.gap_until = None

        def poll_latest(self):
            if self.frame == 5 and self.gap_until is None:
                self.gap_until = time.monotonic() + 1.5 * (
                    bridge.TRACKING_TIMEOUT_SECONDS
                )
            if self.gap_until is not None and time.monotonic() < self.gap_until:
                return None
            return super().poll_latest()

    source = GapSource()
    target = FakeTarget(("left",))
    warnings = []
    monkeypatch.setattr(bridge, "FRAME_BUDGET_SECONDS", 0.0)
    monkeypatch.setattr(
        bridge.LOGGER,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )

    with pytest.raises(EndTest):
        bridge.run_bridge(
            bridge.BridgeConfig(),
            source_factory=lambda _config, _sides: source,
            target_factory=lambda _sns, _auto_routes: target,
        )

    assert any(
        "Tracking gap" in message and "restarting calibration" in message
        for message in warnings
    )
    # The gap discarded the first five samples, so calibration needed a full
    # fresh window before the dry-run loop could reach stop_after.
    assert source.frame > bridge.TIP_CALIBRATION_FRAMES + 5
    assert "reset" in target.actions
    assert target.closed and source.closed


class FakeWujiModule:
    """Minimal stand-in for the wuji_sdk import surface used by connect()."""

    class DeviceType:
        WujiHand2 = "WujiHand2"
        WujiGlove = "WujiGlove"

    class HandModel:
        WujiHand2 = "HandModel.WujiHand2"

    class Handedness:
        Left = "Handedness.Left"
        Right = "Handedness.Right"

    class JointCommand:
        def __init__(self, position, velocity, effort):
            self.position = position
            self.velocity = velocity
            self.effort = effort

    class ConnectOptions:
        def __init__(self, enable_bridge=True):
            self.enable_bridge = enable_bridge

    class RetargetSession:
        def __init__(self, side):
            self.side = side

        @classmethod
        def for_hand(cls, hand_model, side):
            return cls(side)

    class WujiHand2:
        def __init__(self, sn, side, online=bridge.TOTAL_WUJI_JOINTS):
            self.sn = sn
            self.side = side
            self.online = online

        def online_joints_count(self):
            return SimpleNamespace(get=lambda: self.online)

        def handedness(self):
            return SimpleNamespace(get=lambda: self.side)

    class SdkManager:
        current = None

        def __init__(self, devices):
            self.devices = devices
            self.connections = []

        @classmethod
        def instance(cls):
            return cls.current

        def scan(self):
            return [
                SimpleNamespace(sn=sn, device_type=FakeWujiModule.DeviceType.WujiHand2)
                for sn, _side in self.devices
            ]

        def connect(self, *, sn, device_name, options=None):
            self.connections.append((sn, device_name, options))
            side = dict(self.devices)[sn]
            return FakeWujiModule.WujiHand2(sn, side)

        def disconnect_all(self):
            self.connections.clear()


@pytest.fixture
def fake_wuji(monkeypatch):
    module = FakeWujiModule()
    monkeypatch.setitem(sys.modules, "wuji_sdk", module)

    def install(devices):
        FakeWujiModule.SdkManager.current = FakeWujiModule.SdkManager(devices)
        return FakeWujiModule.SdkManager.current

    yield install
    FakeWujiModule.SdkManager.current = None


def test_connect_auto_detects_a_single_left_hand(fake_wuji):
    manager = fake_wuji([("SN-LEFT", "left")])
    target = bridge.WujiTarget(auto_routes=False)

    assert target.connect() == ("left",)
    assert set(target.hands) == {"left"}
    assert target.hands["left"].session.side == FakeWujiModule.Handedness.Left
    # The multi-client DeviceBridge must stay off for every connection.
    assert all(options.enable_bridge is False for _, _, options in manager.connections)


def test_connect_auto_detects_both_hands(fake_wuji):
    fake_wuji([("SN-RIGHT", "right"), ("SN-LEFT", "left")])
    target = bridge.WujiTarget(auto_routes=False)

    assert target.connect() == ("left", "right")
    assert target.hands["left"].sn == "SN-LEFT"
    assert target.hands["right"].sn == "SN-RIGHT"
    assert target.hands["right"].session.side == FakeWujiModule.Handedness.Right


def test_connect_configures_routes_before_devices_and_restores_after_disconnect(
    fake_wuji,
):
    manager = fake_wuji([("SN-RIGHT", "right"), ("SN-LEFT", "left")])
    actions = []

    class FakeRoutes:
        def configure(self, devices):
            assert manager.connections == []
            actions.append(("configure", [device.sn for device in devices]))

        def restore(self):
            assert manager.connections == []
            actions.append(("restore", None))

    target = bridge.WujiTarget(temporary_routes=FakeRoutes())

    assert target.connect() == ("left", "right")
    target.close()

    assert actions == [
        ("configure", ["SN-RIGHT", "SN-LEFT"]),
        ("restore", None),
    ]


def test_connect_rejects_two_hands_with_the_same_side(fake_wuji):
    fake_wuji([("SN-A", "left"), ("SN-B", "left")])
    with pytest.raises(bridge.ConfigurationError, match="two left"):
        bridge.WujiTarget(auto_routes=False).connect()


def test_connect_rejects_zero_hands(fake_wuji):
    fake_wuji([])
    with pytest.raises(bridge.ConfigurationError, match="no Wuji Hand 2"):
        bridge.WujiTarget(auto_routes=False).connect()


def test_connect_requires_requested_serials_to_be_discovered(fake_wuji):
    fake_wuji([("SN-LEFT", "left")])
    with pytest.raises(bridge.ConfigurationError, match="SN-MISSING"):
        bridge.WujiTarget(
            hand_sns=("SN-MISSING",), auto_routes=False
        ).connect()


def test_connect_selects_only_the_requested_serial(fake_wuji):
    fake_wuji([("SN-RIGHT", "right"), ("SN-LEFT", "left")])
    target = bridge.WujiTarget(
        hand_sns=("SN-RIGHT",), auto_routes=False
    )

    assert target.connect() == ("right",)
    assert set(target.hands) == {"right"}


@pytest.mark.parametrize("side", ["left", "right"])
def test_real_wuji_retarget_contract(side):
    wuji_sdk = pytest.importorskip("wuji_sdk")
    builder = bridge.HandLandmarkBuilder(side, calibration_frames=1)
    keypoints = builder.build(make_raw_pose(side)).keypoints
    session = wuji_sdk.RetargetSession.for_hand(
        wuji_sdk.HandModel.WujiHand2,
        side=wuji_sdk.Handedness.Left
        if side == "left"
        else wuji_sdk.Handedness.Right,
    )
    qpos = session.step(keypoints)
    assert qpos.shape == (20,)
    assert qpos.dtype == np.float32
    assert np.isfinite(qpos).all()

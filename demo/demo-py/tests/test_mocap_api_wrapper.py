from ctypes import POINTER, c_float, c_void_p, pointer, sizeof
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "MocapApi"
sys.path.insert(0, str(PACKAGE_ROOT))

import mocap_api  # noqa: E402
from mocap_api import mocap_api as implementation  # noqa: E402


def _field_type(structure, name):
    return dict(structure._fields_)[name]


def test_packaged_v73_interfaces_and_error_codes_are_exposed():
    assert (
        implementation.MCPJoint.IMCPJointApi_Version.value
        == b"PROC_TABLE:IMCPJoint_004"
    )
    assert (
        implementation.MCPAvatar.IMCPAvatarApi_Version.value
        == b"PROC_TABLE:IMCPAvatar_005"
    )
    assert (
        implementation.MCPApplication.IMCPApplicationApi_Version.value
        == b"PROC_TABLE:IMCPApplication_004"
    )

    assert tuple(implementation.MCPError) == tuple(range(23))
    assert implementation.MCPError.ServerNotReady == 16
    assert implementation.MCPError.InterfaceIncompatible == 22

    for interface_version in (
        implementation.MCPJoint.IMCPJointApi_Version,
        implementation.MCPAvatar.IMCPAvatarApi_Version,
        implementation.MCPApplication.IMCPApplicationApi_Version,
    ):
        proc_table = c_void_p()
        error = implementation.MocapApi.MCPGetGenericInterface(
            interface_version, pointer(proc_table)
        )
        assert error == implementation.MCPError.NoError
        assert proc_table.value is not None


def test_native_version_helpers_report_the_loaded_library():
    numeric = mocap_api.get_mocap_api_version()
    version_string = mocap_api.get_mocap_api_version_string()
    major, minor, build, revision = version_string.split(".")

    assert numeric[:3] == (int(major), int(minor), int(build))
    assert numeric[3] == int(revision, 16)
    assert numeric[:3] == (0, 0, 73)


def test_global_rotation_abi_and_public_return_order():
    function_type = _field_type(
        implementation.MCPJoint.MCPJointApi, "GetJointGlobalRotation"
    )
    assert function_type._argtypes_ == (
        POINTER(c_float),
        POINTER(c_float),
        POINTER(c_float),
        POINTER(c_float),
        implementation.MCPJointHandle,
    )

    @function_type
    def get_global_rotation(x, y, z, w, handle):
        assert handle == 42
        x[0] = 1.0
        y[0] = 2.0
        z[0] = 3.0
        w[0] = 4.0
        return implementation.MCPError.NoError

    table = implementation.MCPJoint.MCPJointApi()
    table.GetJointGlobalRotation = get_global_rotation
    joint = SimpleNamespace(
        api=pointer(table), handle=implementation.MCPJointHandle(42)
    )

    assert implementation.MCPJoint.get_global_rotation(joint) == (4.0, 1.0, 2.0, 3.0)


def test_global_position_public_return_order():
    function_type = _field_type(
        implementation.MCPJoint.MCPJointApi, "GetJointGlobalPosition"
    )

    @function_type
    def get_global_position(x, y, z, handle):
        assert handle == 99
        x[0] = 1.25
        y[0] = -2.5
        z[0] = 3.75
        return implementation.MCPError.NoError

    table = implementation.MCPJoint.MCPJointApi()
    table.GetJointGlobalPosition = get_global_position
    joint = SimpleNamespace(
        api=pointer(table), handle=implementation.MCPJointHandle(99)
    )

    assert implementation.MCPJoint.get_global_position(joint) == (1.25, -2.5, 3.75)


def test_poll_next_event_retries_more_event_and_returns_complete_batch():
    function_type = _field_type(
        implementation.MCPApplication.MCPApplicationApi,
        "PollApplicationNextEvent",
    )
    calls = []
    initialized_sizes = []

    @function_type
    def poll(events, count, handle):
        calls.append(bool(events))
        assert handle == 123

        if len(calls) == 1:
            assert not events
            count[0] = 1
            return implementation.MCPError.NoError
        if len(calls) == 2:
            assert events
            initialized_sizes.append(events[0].size)
            count[0] = 1
            return implementation.MCPError.MoreEvent
        if len(calls) == 3:
            assert not events
            count[0] = 2
            return implementation.MCPError.NoError

        assert events
        initialized_sizes.extend((events[0].size, events[1].size))
        events[0].event_type = implementation.MCPEventType.AvatarUpdated
        events[1].event_type = implementation.MCPEventType.RigidBodyUpdated
        count[0] = 2
        return implementation.MCPError.NoError

    table = implementation.MCPApplication.MCPApplicationApi()
    table.PollApplicationNextEvent = poll
    application = SimpleNamespace(
        api=pointer(table), _handle=implementation.MCPApplicationHandle(123)
    )

    events = implementation.MCPApplication.poll_next_event(application)

    assert calls == [False, True, False, True]
    assert initialized_sizes == [sizeof(implementation.MCPEvent)] * 3
    assert [event.event_type for event in events] == [
        implementation.MCPEventType.AvatarUpdated,
        implementation.MCPEventType.RigidBodyUpdated,
    ]


def test_poll_next_event_raises_instead_of_silently_dropping_errors():
    function_type = _field_type(
        implementation.MCPApplication.MCPApplicationApi,
        "PollApplicationNextEvent",
    )

    @function_type
    def poll(events, count, handle):
        if not events:
            count[0] = 1
            return implementation.MCPError.NoError
        return implementation.MCPError.InvalidHandle

    table = implementation.MCPApplication.MCPApplicationApi()
    table.PollApplicationNextEvent = poll
    application = SimpleNamespace(
        api=pointer(table), _handle=implementation.MCPApplicationHandle(123)
    )

    with pytest.raises(RuntimeError, match="InvalidHandle"):
        implementation.MCPApplication.poll_next_event(application)

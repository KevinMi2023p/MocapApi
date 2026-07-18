from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from mocap_studio.__main__ import main
from mocap_studio.providers.base import (
    JointPose,
    MotionFrame,
    Provider,
    ProviderCapabilities,
    ProviderConnectionError,
    ProviderError,
)
from mocap_studio.providers.bvh import BvhProvider
from mocap_studio.recording import TakeLibraryBusyError, TakeRecorder, sanitize_take_name
from mocap_studio.state import StudioController


def frame(number: int = 1, source: int | None = None) -> MotionFrame:
    joint = JointPose("Hips", "Hips", None, (0, 1, 0), (0, 0, 0, 1))
    return MotionFrame("avatar", "Actor", number, 1.0, (joint,), source_frame_index=source)


class RecorderTests(unittest.TestCase):
    def test_finalize_is_crash_safe_and_never_reuses_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = TakeRecorder(Path(temporary))
            first_dir = recorder.start("take / unsafe", "notes")
            self.assertTrue((first_dir / ".recording").exists())
            recorder.append(frame(source=12))
            take = recorder.stop()
            self.assertIsNotNone(take)
            self.assertTrue((first_dir / "frames.ndjson").exists())
            self.assertTrue((first_dir / "manifest.json").exists())
            self.assertFalse((first_dir / "frames.ndjson.partial").exists())
            payload = json.loads((first_dir / "frames.ndjson").read_text())
            self.assertEqual(payload["sourceFrame"], 12)

            second_dir = recorder.start("take / unsafe")
            recorder.stop()
            self.assertNotEqual(first_dir, second_dir)
            self.assertTrue((first_dir / "frames.ndjson").exists())
            recorder.close()

    def test_recovery_and_corrupt_metadata_are_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            interrupted = root / "old"
            interrupted.mkdir()
            (interrupted / ".recording").write_text("in-progress\n")
            (interrupted / "frames.ndjson.partial").write_text("{}\n{}\n")
            (interrupted / "manifest.json.partial").write_text(
                '{"name":"Old","durationMs":"bad"}'
            )
            recorder = TakeRecorder(root)
            recovered = recorder.recover_interrupted()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0].frames, 2)
            self.assertEqual(recovered[0].duration_ms, 0)
            self.assertEqual(recorder.list_takes()[0]["status"], "interrupted")

            non_object = root / "non-object"
            non_object.mkdir()
            (non_object / "manifest.json").write_text("[]")
            self.assertEqual(len(recorder.list_takes()), 1)
            recorder.close()

    def test_active_take_is_not_mistaken_for_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            recorder = TakeRecorder(Path(temporary))
            recorder.start("live")
            self.assertEqual(recorder.recover_interrupted(), [])
            self.assertTrue(recorder.active)
            recorder.stop()
            recorder.close()

    def test_take_library_has_single_process_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = TakeRecorder(root)
            with self.assertRaisesRegex(TakeLibraryBusyError, "already in use"):
                TakeRecorder(root)
            first.close()
            replacement = TakeRecorder(root)
            replacement.close()

    def test_controller_owns_library_lock_until_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = StudioController(root)
            with self.assertRaises(TakeLibraryBusyError):
                StudioController(root)
            first.close()
            replacement = StudioController(root)
            replacement.close()

    def test_lock_path_rejects_symlink_fifo_and_never_alters_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("do-not-touch")
            lock_path = root / ".mocap-studio.lock"
            lock_path.symlink_to(target)
            with self.assertRaisesRegex(TakeLibraryBusyError, "regular"):
                TakeRecorder(root)
            self.assertEqual(target.read_text(), "do-not-touch")
            lock_path.unlink()

            os.mkfifo(lock_path)
            with self.assertRaisesRegex(TakeLibraryBusyError, "regular"):
                TakeRecorder(root)
            lock_path.unlink()

            lock_path.write_text("existing-content")
            recorder = TakeRecorder(root)
            self.assertEqual(lock_path.read_text(), "existing-content")
            recorder.close()

    def test_close_releases_lock_and_marks_closed_when_finalization_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorder = TakeRecorder(root)
            recorder.start("will-fail")
            with patch.object(
                recorder, "stop", side_effect=RuntimeError("finalization failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "finalization failed"):
                    recorder.close()
            self.assertTrue(recorder._closed)
            self.assertFalse(recorder.active)
            with self.assertRaisesRegex(RuntimeError, "closed"):
                recorder.start("again")
            replacement = TakeRecorder(root)
            replacement.close()

    def test_controller_init_failure_releases_library_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(
                TakeRecorder,
                "recover_interrupted",
                side_effect=RuntimeError("recovery failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "recovery failed"):
                    StudioController(root)
            replacement = TakeRecorder(root)
            replacement.close()

    def test_cli_bind_failure_closes_controller_and_reports_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            errors = io.StringIO()
            with patch(
                "mocap_studio.__main__.run_server",
                side_effect=OSError("address already in use"),
            ), redirect_stderr(errors):
                result = main(["--data-dir", str(root), "--no-browser", "--port", "0"])
            self.assertEqual(result, 1)
            self.assertIn("could not start local server", errors.getvalue())
            replacement = TakeRecorder(root)
            replacement.close()

    def test_sanitize_name(self) -> None:
        self.assertEqual(sanitize_take_name(" ../hello world "), "hello-world")
        self.assertEqual(sanitize_take_name("..."), "take")


class FakeProvider(Provider):
    mode = "demo"
    capabilities = ProviderCapabilities(
        receive_motion=True,
        server_commands=False,
        calibration_commands=False,
        axis_recording=False,
        local_recording=True,
        reason="unsupported by fake",
    )

    def start(self, on_frame, on_error) -> None:
        self.on_frame = on_frame
        self.on_error = on_error

    def stop(self) -> None:
        self.stopped = True


class CommandProvider(FakeProvider):
    capabilities = ProviderCapabilities(
        receive_motion=True,
        server_commands=True,
        calibration_commands=True,
        axis_recording=True,
        local_recording=True,
    )

    def __init__(self) -> None:
        self.commands = []
        self.stopped = False

    def command(self, name, options):
        self.commands.append(name)
        return f"{name} complete"


class BlockingStartProvider(CommandProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.start_calls = 0

    def start(self, on_frame, on_error) -> None:
        self.start_calls += 1
        self.started.set()
        self.release.wait(2)
        super().start(on_frame, on_error)


class FailingStartProvider(CommandProvider):
    def start(self, on_frame, on_error) -> None:
        raise ProviderError("provider exploded")


class ImmediatelyFatalProvider(CommandProvider):
    def start(self, on_frame, on_error) -> None:
        super().start(on_frame, on_error)
        on_error(ProviderConnectionError("failed during start"))


class ControllerTests(unittest.TestCase):
    def test_capability_gating_local_recording_and_source_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            with self.assertRaises(ProviderError):
                controller.command("start_capture", {})
            provider = FakeProvider()
            controller._provider = provider
            controller._state["capabilities"] = provider.capabilities.as_dict()
            with self.assertRaisesRegex(ProviderError, "unsupported by fake"):
                controller.command("calibrate", {})
            for command in (
                "start_calibration",
                "calibration_next",
                "calibration_cancel",
                "resume_original_posture",
            ):
                with self.subTest(command=command), self.assertRaisesRegex(
                    ProviderError, "unsupported by fake"
                ):
                    controller.command(command, {})
            self.assertEqual(
                controller.command("start_record", {"target": "local", "takeName": "one"}),
                "Local recording started",
            )
            controller._on_frame(frame(1, 10))
            controller._on_frame(frame(2, 13))
            controller._on_frame(frame(3, 2))  # reset, not a 2^32-sized gap
            self.assertEqual(controller.snapshot()["diagnostics"]["droppedFrames"], 2)
            self.assertIn("Saved", controller.command("stop_record", {"target": "local"}))
            self.assertEqual(len(controller.snapshot()["takes"]), 1)
            controller.close()

    def test_unknown_command_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            with self.assertRaises(ProviderError):
                controller.command("format_disk", {})
            controller.close()

    def test_connect_and_disconnect_reset_stream_baselines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            controller._on_frame(frame(1, 10))
            controller._on_frame(frame(2, 13))
            self.assertEqual(controller.snapshot()["diagnostics"]["droppedFrames"], 2)

            provider = FakeProvider()
            with patch("mocap_studio.state.DemoProvider", return_value=provider):
                controller.connect({"mode": "demo"})
            diagnostics = controller.snapshot()["diagnostics"]
            self.assertEqual(diagnostics["receivedFrames"], 0)
            self.assertEqual(diagnostics["droppedFrames"], 0)
            self.assertEqual(diagnostics["jitterMs"], 0.0)
            controller._on_frame(frame(3, 1000))
            self.assertEqual(controller.snapshot()["diagnostics"]["droppedFrames"], 0)

            controller.disconnect()
            diagnostics = controller.snapshot()["diagnostics"]
            self.assertEqual(diagnostics["receivedFrames"], 0)
            self.assertIsNone(diagnostics["lastFrameAt"])
            controller.close()

    def test_connection_validation_and_start_failures_leave_error_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            with self.assertRaisesRegex(ProviderError, "Demo FPS"):
                controller.connect({"mode": "demo", "fps": "not-a-number"})
            self.assertIsNone(controller._provider)
            state = controller.snapshot()
            self.assertEqual(state["connection"]["status"], "error")
            self.assertIn("Demo FPS", state["connection"]["message"])

            failing = FailingStartProvider()
            with patch("mocap_studio.state.DemoProvider", return_value=failing):
                with self.assertRaisesRegex(ProviderError, "provider exploded"):
                    controller.connect({"mode": "demo"})
            self.assertTrue(failing.stopped)
            self.assertIsNone(controller._provider)
            self.assertEqual(controller.snapshot()["connection"]["status"], "error")

            immediately_fatal = ImmediatelyFatalProvider()
            with patch(
                "mocap_studio.state.DemoProvider", return_value=immediately_fatal
            ):
                with self.assertRaises(ProviderConnectionError):
                    controller.connect({"mode": "demo"})
            self.assertIsNone(controller._provider)
            self.assertEqual(controller.snapshot()["connection"]["status"], "error")
            controller.close()

    def test_concurrent_connect_is_serialized_without_provider_leak(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            provider = BlockingStartProvider()
            outcomes = []

            def connect():
                try:
                    controller.connect({"mode": "demo"})
                    outcomes.append("connected")
                except Exception as error:  # test captures the competing request
                    outcomes.append(error)

            with patch("mocap_studio.state.DemoProvider", return_value=provider):
                first = threading.Thread(target=connect)
                first.start()
                self.assertTrue(provider.started.wait(1))
                second = threading.Thread(target=connect)
                second.start()
                time.sleep(0.05)
                self.assertTrue(second.is_alive())
                provider.release.set()
                first.join(1)
                second.join(1)

            self.assertEqual(provider.start_calls, 1)
            self.assertIn("connected", outcomes)
            errors = [result for result in outcomes if isinstance(result, Exception)]
            self.assertEqual(len(errors), 1)
            self.assertIn("Disconnect", str(errors[0]))
            self.assertIs(controller._provider, provider)
            controller.close()

    def test_command_and_disconnect_are_serialized(self) -> None:
        class BlockingCommandProvider(CommandProvider):
            def __init__(self):
                super().__init__()
                self.command_started = threading.Event()
                self.command_release = threading.Event()

            def command(self, name, options):
                self.command_started.set()
                self.command_release.wait(2)
                return super().command(name, options)

        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            provider = BlockingCommandProvider()
            with patch("mocap_studio.state.DemoProvider", return_value=provider):
                controller.connect({"mode": "demo"})
            command_thread = threading.Thread(
                target=lambda: controller.command("zero_position", {})
            )
            command_thread.start()
            self.assertTrue(provider.command_started.wait(1))
            disconnect_thread = threading.Thread(target=controller.disconnect)
            disconnect_thread.start()
            time.sleep(0.05)
            self.assertFalse(provider.stopped)
            provider.command_release.set()
            command_thread.join(1)
            disconnect_thread.join(1)
            self.assertTrue(provider.stopped)
            self.assertEqual(controller.snapshot()["connection"]["status"], "disconnected")
            controller.close()

    def test_stop_capture_finalizes_local_take_and_bvh_capture_is_receive_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            demo = CommandProvider()
            with patch("mocap_studio.state.DemoProvider", return_value=demo):
                controller.connect({"mode": "demo"})
            demo.on_frame(frame())
            self.assertTrue(controller.snapshot()["avatars"][0]["calibrated"])
            self.assertIsNone(controller.snapshot()["diagnostics"]["latencyMs"])
            controller.command("start_record", {"target": "local", "takeName": "demo"})
            controller._on_frame(frame())
            message = controller.command("stop_capture", {})
            self.assertIn("Saved", message)
            self.assertFalse(controller.recorder.active)
            self.assertFalse(controller.snapshot()["session"]["recording"])
            self.assertEqual(len(controller.snapshot()["takes"]), 1)
            controller.close()

        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            bvh = FakeProvider()
            bvh.mode = "bvh"
            bvh.capabilities = BvhProvider.capabilities
            with patch("mocap_studio.state.BvhProvider", return_value=bvh):
                controller.connect({"mode": "bvh"})
            bvh.on_frame(frame())
            self.assertIsNone(controller.snapshot()["avatars"][0]["calibrated"])
            self.assertIsNone(controller.snapshot()["diagnostics"]["latencyMs"])
            with self.assertRaisesRegex(ProviderError, "receive-only"):
                controller.command("start_capture", {})
            controller.command("start_record", {"target": "local", "takeName": "bvh"})
            controller._on_frame(frame())
            self.assertIn("remains connected", controller.command("stop_capture", {}))
            self.assertFalse(controller.recorder.active)
            self.assertNotIn("stop_capture", getattr(bvh, "commands", []))
            controller.close()

    def test_terminal_provider_error_moves_controller_to_error_and_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            provider = CommandProvider()
            with patch("mocap_studio.state.DemoProvider", return_value=provider):
                controller.connect({"mode": "demo"})
            controller.command("start_record", {"target": "local", "takeName": "fatal"})
            provider.on_error(ProviderConnectionError("peer closed"))
            state = controller.snapshot()
            self.assertEqual(state["connection"]["status"], "error")
            self.assertEqual(state["connection"]["message"], "peer closed")
            self.assertFalse(state["session"]["recording"])
            self.assertFalse(controller.recorder.active)
            self.assertIsNone(controller._provider)
            self.assertEqual(state["takes"][0]["status"], "interrupted")
            controller.close()

    def test_stale_provider_frames_cannot_mutate_state_or_recording(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            first = CommandProvider()
            second = CommandProvider()
            with patch(
                "mocap_studio.state.DemoProvider", side_effect=[first, second]
            ):
                controller.connect({"mode": "demo"})
                controller.disconnect()
                controller.connect({"mode": "demo"})
            controller.command("start_record", {"target": "local", "takeName": "scoped"})
            first.on_frame(frame(99, 99))
            self.assertEqual(controller.snapshot()["diagnostics"]["receivedFrames"], 0)
            second.on_frame(frame(1, 1))
            self.assertEqual(controller.snapshot()["diagnostics"]["receivedFrames"], 1)
            controller.command("stop_record", {"target": "local"})
            self.assertEqual(controller.snapshot()["takes"][0]["frames"], 1)
            controller.close()

    def test_connection_settings_are_normalized_before_provider_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            demo = CommandProvider()
            with patch(
                "mocap_studio.state.DemoProvider", return_value=demo
            ) as demo_constructor:
                controller.connect(
                    {
                        "mode": "DEMO",
                        "fps": 500,
                        "port": "irrelevant-not-an-integer",
                        "transport": object(),
                    }
                )
            demo_constructor.assert_called_once_with(fps=240.0)
            state = controller.snapshot()
            self.assertEqual(state["connection"]["port"], 7012)
            self.assertEqual(state["connection"]["transport"], "udp")
            controller.disconnect()

            bvh = FakeProvider()
            bvh.mode = "bvh"
            bvh.capabilities = BvhProvider.capabilities
            with patch("mocap_studio.state.BvhProvider", return_value=bvh) as constructor:
                controller.connect(
                    {
                        "mode": "BVH",
                        "transport": "TCP",
                        "host": "localhost",
                        "port": "7013",
                        "rotationOrder": "zyx",
                        "unit": "METERS",
                    }
                )
            constructor.assert_called_once_with(
                transport="tcp",
                host="localhost",
                port=7013,
                rotation_order="ZYX",
                source_unit="meters",
            )
            controller.disconnect()

            with patch("mocap_studio.state.BvhProvider") as constructor:
                with self.assertRaisesRegex(ProviderError, "Port must be"):
                    controller.connect({"mode": "bvh", "port": "bad"})
                constructor.assert_not_called()
            self.assertEqual(controller.snapshot()["connection"]["status"], "error")
            self.assertIsNone(controller._provider)
            controller.close()

    def test_terminal_callback_never_waits_on_lifecycle_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            controller = StudioController(Path(temporary))
            provider = CommandProvider()
            with patch("mocap_studio.state.DemoProvider", return_value=provider):
                controller.connect({"mode": "demo"})

            controller._lifecycle_lock.acquire()
            try:
                callback = threading.Thread(
                    target=lambda: provider.on_error(
                        ProviderConnectionError("terminal while lifecycle busy")
                    )
                )
                callback.start()
                callback.join(0.25)
                self.assertFalse(callback.is_alive())
            finally:
                controller._lifecycle_lock.release()

            deadline = time.monotonic() + 1.0
            while controller.snapshot()["connection"]["status"] != "error":
                if time.monotonic() >= deadline:
                    self.fail("terminal cleanup helper did not complete")
                time.sleep(0.01)
            controller.close()


if __name__ == "__main__":
    unittest.main()

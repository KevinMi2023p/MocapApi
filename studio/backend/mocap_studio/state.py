from __future__ import annotations

import copy
import math
import queue
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .providers import (
    BvhProvider,
    DemoProvider,
    MotionFrame,
    Provider,
    ProviderConnectionError,
    ProviderError,
    ProviderStreamIdle,
)
from .recording import TakeRecorder, default_library_dir


class StudioController:
    def __init__(self, library_dir: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._provider: Provider | None = None
        self._closed = False
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._last_publish = 0.0
        self._last_frame_monotonic: dict[str, float] = {}
        self._last_source_frame: dict[str, int] = {}
        self._frame_intervals: deque[float] = deque(maxlen=120)
        self._byte_samples: deque[tuple[float, int]] = deque(maxlen=240)
        self._session_started = 0.0
        self._stream_idle = False
        self.recorder = TakeRecorder(library_dir or default_library_dir())
        try:
            recovered = self.recorder.recover_interrupted()
            self._state = _initial_state()
            self._state["takes"] = self.recorder.list_takes()
            for take in recovered:
                self._add_event(
                    "warning",
                    "Recorder",
                    f"Recovered interrupted take {take.name!r} ({take.frames} frames)",
                    publish=False,
                )
        except BaseException:
            try:
                self.recorder.close()
            except BaseException:
                pass
            raise

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._update_elapsed_locked()
            return copy.deepcopy(self._state)

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            self._subscribers.add(subscriber)
            subscriber.put_nowait(copy.deepcopy(self._state))
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def connect(self, settings: dict[str, Any]) -> None:
        with self._lifecycle_lock:
            mode = str(settings.get("mode", "demo")).strip().lower()
            with self._lock:
                if self._closed:
                    raise ProviderError("Mocap Studio controller is closed")
                if self._provider is not None:
                    raise ProviderError("Disconnect the current source before connecting another")
                self._reset_stream_tracking_locked()
                self._state["connection"].update(
                    {"status": "connecting", "mode": mode, "message": "Connecting…"}
                )
                self._publish_locked(force=True)

            provider: Provider | None = None
            normalized: dict[str, Any] | None = None
            try:
                normalized = self._normalize_connection_settings(settings)
                mode = normalized["mode"]
                provider = self._create_provider(mode, normalized)
                # Tentatively register the provider while lifecycle operations
                # remain locked so an immediate worker failure is attributable
                # to this connection, but no command/disconnect can race start.
                with self._lock:
                    self._provider = provider
                provider.start(
                    lambda frame, source=provider: self._on_provider_frame(source, frame),
                    lambda error, source=provider: self._on_provider_error(source, error),
                )
                with self._lock:
                    if self._provider is not provider:
                        raise ProviderConnectionError(
                            self._state["connection"]["message"]
                            or "Provider stopped during connection"
                        )
            except Exception as error:
                if provider is not None:
                    with self._lock:
                        if self._provider is provider:
                            self._provider = None
                    try:
                        provider.stop()
                    except Exception:
                        pass
                message = f"Connection failed: {str(error).strip() or type(error).__name__}"
                with self._lock:
                    self._provider = None
                    self._state["connection"].update(
                        {"status": "error", "mode": mode, "message": message}
                    )
                    self._state["capabilities"] = _disconnected_capabilities()
                    self._add_event("error", "Connection", message, publish=False)
                    self._publish_locked(force=True)
                if isinstance(error, ProviderError):
                    raise
                raise ProviderError(message) from error

            with self._lock:
                assert normalized is not None
                self._provider = provider
                self._state["connection"].update(
                    {
                        "status": "connected",
                        "mode": mode,
                        "transport": normalized["transport"],
                        "host": normalized["host"],
                        "port": normalized["port"],
                        "message": "Demo source online" if mode == "demo" else "Waiting for BVH frames",
                    }
                )
                self._state["capabilities"] = provider.capabilities.as_dict()
                self._add_event(
                    "success",
                    "Connection",
                    f"{mode.upper()} source connected",
                    publish=False,
                )
                self._publish_locked(force=True)

    def disconnect(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                provider, self._provider = self._provider, None
            if provider:
                try:
                    provider.stop()
                except Exception as error:
                    with self._lock:
                        self._add_event(
                            "warning",
                            "Connection",
                            f"Provider cleanup reported: {error}",
                            publish=False,
                        )
            completed = self.recorder.stop(status="interrupted")
            with self._lock:
                self._reset_stream_tracking_locked()
                if completed:
                    self._state["takes"] = self.recorder.list_takes()
                self._state["connection"].update(
                    {"status": "disconnected", "message": "Ready"}
                )
                self._state["session"].update(
                    {
                        "capturing": False,
                        "recording": False,
                        "recordingTarget": None,
                        "elapsedMs": 0,
                    }
                )
                self._state["capabilities"] = _disconnected_capabilities()
                self._add_event("info", "Connection", "Source disconnected", publish=False)
                self._publish_locked(force=True)

    def command(self, name: str, options: dict[str, Any]) -> str:
        allowed = {
            "start_capture",
            "stop_capture",
            "zero_position",
            "calibrate",
            "start_calibration",
            "calibration_next",
            "calibration_cancel",
            "resume_posture",
            "resume_original_posture",
            "resume_hands",
            "start_record",
            "stop_record",
        }
        with self._lifecycle_lock:
            if name not in allowed:
                raise ProviderError(f"Unknown command: {name}")
            with self._lock:
                provider = self._provider
                capabilities = self._state["capabilities"].copy()
                default_take_name = self._state["session"]["takeName"]
                session_recording = bool(self._state["session"]["recording"])
                recording_target = self._state["session"]["recordingTarget"]
            if provider is None:
                raise ProviderError("Connect a source before sending commands")

            requested_target = options.get("target")
            if requested_target is None:
                target = (
                    "local"
                    if name == "start_record" or self.recorder.active
                    else "axis"
                )
            else:
                target = str(requested_target).strip().lower()
            if name in {"start_record", "stop_record"} and target not in {
                "local",
                "axis",
            }:
                raise ProviderError("Recording target must be local or axis")
            if name == "start_record" and (session_recording or self.recorder.active):
                active_target = recording_target or (
                    "local" if self.recorder.active else "unknown"
                )
                raise ProviderError(f"A {active_target} recording is already active")
            if name == "stop_record" and target != recording_target:
                if recording_target is None:
                    raise ProviderError("No recording is currently active")
                raise ProviderError(
                    f"Cannot stop {target} recording while {recording_target} recording is active"
                )
            if name == "start_record" and target == "local":
                if not capabilities.get("localRecording"):
                    raise ProviderError("Local recording is not available for this source")
                take_name = str(options.get("takeName") or default_take_name)
                notes = str(options.get("notes", ""))
                path = self.recorder.start(take_name, notes)
                with self._lock:
                    self._session_started = time.monotonic()
                    self._state["session"].update(
                        {
                            "recording": True,
                            "recordingTarget": "local",
                            "capturing": True,
                            "takeName": take_name,
                            "elapsedMs": 0,
                        }
                    )
                    self._add_event(
                        "success",
                        "Recorder",
                        f"Local recording started: {path.name}",
                        publish=False,
                    )
                    self._publish_locked(force=True)
                return "Local recording started"

            if name == "stop_record" and target == "local":
                if not self.recorder.active:
                    raise ProviderError("No local take is currently recording")
                return self._finalize_local_recording("Local recording stopped")

            if name == "start_capture" and provider.mode == "bvh":
                raise ProviderError(
                    "BVH is receive-only; use Local Record to save the incoming motion stream"
                )

            if name == "stop_capture" and self.recorder.active:
                if provider.mode == "bvh":
                    provider_message = "BVH receive stream remains connected"
                else:
                    self._require_capability(capabilities, "serverCommands")
                    provider_message = provider.command(name, options)
                return self._finalize_local_recording(
                    provider_message, stop_capture=True
                )

            required = "serverCommands"
            if name in {
                "calibrate",
                "start_calibration",
                "calibration_next",
                "calibration_cancel",
            }:
                required = "calibrationCommands"
            if name in {"start_record", "stop_record"}:
                required = "axisRecording"
            self._require_capability(capabilities, required)
            message = provider.command(name, options)
            with self._lock:
                if name == "start_capture":
                    self._session_started = time.monotonic()
                    self._state["session"].update({"capturing": True, "elapsedMs": 0})
                elif name == "stop_capture":
                    self._state["session"].update(
                        {
                            "capturing": False,
                            "recording": False,
                            "recordingTarget": None,
                        }
                    )
                elif name == "start_record":
                    self._session_started = time.monotonic()
                    self._state["session"].update(
                        {
                            "recording": True,
                            "recordingTarget": "axis",
                            "capturing": True,
                            "elapsedMs": 0,
                        }
                    )
                elif name == "stop_record":
                    self._state["session"].update(
                        {"recording": False, "recordingTarget": None}
                    )
                self._add_event("success", "Command", message, publish=False)
                self._publish_locked(force=True)
            return message

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self.disconnect()
            self.recorder.close()
            self._closed = True

    def _create_provider(self, mode: str, settings: dict[str, Any]) -> Provider:
        if mode == "demo":
            return DemoProvider(fps=float(settings.get("fps", 60)))
        if mode == "bvh":
            return BvhProvider(
                transport=_transport(settings.get("transport")),
                host=str(settings.get("host", "0.0.0.0")),
                port=int(settings.get("port", 7012)),
                rotation_order=str(settings.get("rotationOrder", "YXZ")),
                source_unit=_unit(settings.get("unit")),
            )
        if mode == "mocap-api":
            raise ProviderError(
                "MocapApi runtime adapter is unavailable. Use standard BVH or Demo mode; "
                "the upstream repository does not ship a compatible macOS library."
            )
        raise ProviderError(f"Unknown connection mode: {mode}")

    @staticmethod
    def _normalize_connection_settings(settings: dict[str, Any]) -> dict[str, Any]:
        mode = str(settings.get("mode", "demo")).strip().lower()
        normalized: dict[str, Any] = {
            "mode": mode,
            "transport": "udp",
            "host": "127.0.0.1",
            "port": 7012,
            "rotationOrder": "YXZ",
            "unit": "centimeters",
            "fps": 60.0,
        }
        if mode == "demo":
            try:
                fps = float(settings.get("fps", 60.0))
            except (TypeError, ValueError, OverflowError) as error:
                raise ProviderError("Demo FPS must be a finite number") from error
            if not math.isfinite(fps):
                raise ProviderError("Demo FPS must be a finite number")
            normalized["fps"] = max(1.0, min(fps, 240.0))
            # Network fields do not control DemoProvider. Keep their display
            # representation canonical instead of parsing irrelevant input.
            return normalized
        if mode == "bvh":
            normalized["transport"] = _transport(settings.get("transport"))
            normalized["host"] = str(settings.get("host") or "0.0.0.0")
            try:
                port = int(settings.get("port", 7012))
            except (TypeError, ValueError, OverflowError) as error:
                raise ProviderError("Port must be an integer between 1 and 65535") from error
            if not 1 <= port <= 65535:
                raise ProviderError("Port must be between 1 and 65535")
            normalized["port"] = port
            order = str(settings.get("rotationOrder") or "YXZ").upper()
            if order not in {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}:
                raise ProviderError("Unsupported BVH rotation order")
            normalized["rotationOrder"] = order
            normalized["unit"] = _unit(settings.get("unit"))
            return normalized
        if mode == "mocap-api":
            return normalized
        raise ProviderError(f"Unknown connection mode: {mode}")

    @staticmethod
    def _require_capability(capabilities: dict[str, Any], name: str) -> None:
        if capabilities.get(name):
            return
        reason = capabilities.get("reason") or "The connected provider does not support this command"
        raise ProviderError(str(reason))

    def _finalize_local_recording(
        self, prefix: str, *, stop_capture: bool = False
    ) -> str:
        completed = self.recorder.stop()
        if completed is None:
            raise ProviderError("No local take is currently recording")
        saved = f"Saved {completed.name} ({completed.frames} frames)"
        message = f"{prefix}; {saved}" if prefix else saved
        with self._lock:
            self._state["session"].update(
                {"recording": False, "recordingTarget": None}
            )
            if stop_capture:
                self._state["session"]["capturing"] = False
            self._state["takes"] = self.recorder.list_takes()
            self._add_event("success", "Recorder", message, publish=False)
            self._publish_locked(force=True)
        return message

    def _on_provider_frame(self, provider: Provider, frame: MotionFrame) -> None:
        # Hold identity and mutation under the same state lock. Once disconnect
        # or reconnect marks this provider stale, no late frame can reach state
        # diagnostics, avatars, subscribers, or the recorder.
        with self._lock:
            if self._provider is not provider:
                return
            self._stream_idle = False
            # Demo owns its simulated calibration state. Standard BVH contains
            # solved motion but no calibration-status field, so that state is
            # deliberately unknown instead of inferred from frame arrival.
            self._on_frame(frame, calibrated=True if provider.mode == "demo" else None)

    def _on_frame(self, frame: MotionFrame, *, calibrated: bool | None = None) -> None:
        now = time.monotonic()
        with self._lock:
            previous = self._last_frame_monotonic.get(frame.avatar_id)
            self._last_frame_monotonic[frame.avatar_id] = now
            if previous is not None:
                interval = now - previous
                if 0.0 < interval < 1.0:
                    self._frame_intervals.append(interval)
            estimated_fps = _fps(self._frame_intervals) or frame.fps
            avatar = {
                "id": frame.avatar_id,
                "name": frame.avatar_name,
                "joints": [joint.as_dict() for joint in frame.joints],
                "frame": frame.frame_number,
                "fps": round(estimated_fps, 1),
                "calibrated": calibrated,
            }
            avatars = self._state["avatars"]
            for index, existing in enumerate(avatars):
                if existing["id"] == frame.avatar_id:
                    avatars[index] = avatar
                    break
            else:
                avatars.append(avatar)
            if frame.sensors:
                self._state["sensors"] = list(frame.sensors)
            diagnostics = self._state["diagnostics"]
            diagnostics["receivedFrames"] += 1
            if frame.source_frame_index is not None:
                previous_source = self._last_source_frame.get(frame.avatar_id)
                if previous_source is not None and frame.source_frame_index > previous_source:
                    diagnostics["droppedFrames"] += max(
                        0, frame.source_frame_index - previous_source - 1
                    )
                # A lower value is a reset or uint32 wrap. Remember it as the
                # new baseline without reporting a giant false-positive gap.
                self._last_source_frame[frame.avatar_id] = frame.source_frame_index
            diagnostics["packetsPerSecond"] = round(estimated_fps, 1)
            diagnostics["jitterMs"] = round(_jitter_ms(self._frame_intervals), 2)
            diagnostics["lastFrameAt"] = datetime.now(UTC).isoformat()
            self._byte_samples.append((now, frame.source_bytes))
            diagnostics["bytesPerSecond"] = _bytes_per_second(self._byte_samples, now)
            self._state["connection"]["message"] = "Receiving motion"
            self._update_elapsed_locked()
        self.recorder.append(frame)
        with self._lock:
            self._publish_locked(force=False)

    def _on_provider_error(self, provider: Provider, error: Exception) -> None:
        if isinstance(error, ProviderStreamIdle):
            with self._lock:
                if self._provider is not provider or self._stream_idle:
                    return
                self._stream_idle = True
                # These are live-rate indicators, so retaining the last values
                # during a stopped broadcast would falsely imply fresh motion.
                self._last_frame_monotonic.clear()
                self._frame_intervals.clear()
                self._byte_samples.clear()
                diagnostics = self._state["diagnostics"]
                diagnostics.update(
                    {
                        "packetsPerSecond": 0.0,
                        "bytesPerSecond": 0,
                        "jitterMs": 0.0,
                    }
                )
                for avatar in self._state["avatars"]:
                    avatar["fps"] = 0.0
                for sensor in self._state["sensors"]:
                    if "packetRate" in sensor:
                        sensor["packetRate"] = 0
                self._state["connection"]["message"] = str(error)
                self._add_event("warning", "Provider", str(error), publish=False)
                self._publish_locked(force=True)
            return

        if not isinstance(error, ProviderConnectionError):
            with self._lock:
                if self._provider is not provider:
                    return
                self._add_event("error", "Provider", str(error), publish=False)
                self._state["connection"]["message"] = str(error)
                self._publish_locked(force=True)
            return

        with self._lock:
            if self._provider is not provider:
                return
        if self._lifecycle_lock.acquire(blocking=False):
            try:
                self._handle_terminal_provider_error(provider, error)
            finally:
                self._lifecycle_lock.release()
            return

        # Never make the provider's receive thread wait on a lifecycle operation
        # that may itself be joining that thread. A helper completes cleanup
        # after the serialized operation, with identity rechecked first.
        threading.Thread(
            target=self._finish_terminal_provider_error,
            args=(provider, error),
            name="mocap-provider-error-cleanup",
            daemon=True,
        ).start()

    def _finish_terminal_provider_error(
        self, provider: Provider, error: ProviderConnectionError
    ) -> None:
        with self._lifecycle_lock:
            self._handle_terminal_provider_error(provider, error)

    def _handle_terminal_provider_error(
        self, provider: Provider, error: ProviderConnectionError
    ) -> None:
        with self._lock:
            if self._provider is not provider:
                return
            self._provider = None
        try:
            provider.stop()
        except Exception:
            # The transport is already terminal; recorder/state cleanup must
            # still run even if a provider's release hook fails.
            pass
        completed = self.recorder.stop(status="interrupted")
        with self._lock:
            if completed:
                self._state["takes"] = self.recorder.list_takes()
            self._state["connection"].update(
                {"status": "error", "message": str(error)}
            )
            self._state["session"].update(
                {
                    "capturing": False,
                    "recording": False,
                    "recordingTarget": None,
                    "elapsedMs": 0,
                }
            )
            self._state["capabilities"] = _disconnected_capabilities()
            self._add_event("error", "Provider", str(error), publish=False)
            self._publish_locked(force=True)

    def _add_event(
        self, level: str, source: str, message: str, *, publish: bool = True
    ) -> None:
        with self._lock:
            self._state["events"].insert(
                0,
                {
                    "id": str(uuid.uuid4()),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "level": level,
                    "source": source,
                    "message": message,
                },
            )
            del self._state["events"][500:]
            if publish:
                self._publish_locked(force=True)

    def _update_elapsed_locked(self) -> None:
        if self._state["session"]["capturing"] and self._session_started:
            self._state["session"]["elapsedMs"] = round(
                (time.monotonic() - self._session_started) * 1000
            )

    def _reset_stream_tracking_locked(self) -> None:
        self._stream_idle = False
        self._last_frame_monotonic.clear()
        self._last_source_frame.clear()
        self._frame_intervals.clear()
        self._byte_samples.clear()
        self._state["diagnostics"].update(
            {
                "receivedFrames": 0,
                "droppedFrames": 0,
                "jitterMs": 0.0,
                # The published BVH packets do not include a comparable
                # wall-clock send timestamp, so one-way latency is unknown.
                "latencyMs": None,
                "packetsPerSecond": 0.0,
                "bytesPerSecond": 0,
                "lastFrameAt": None,
            }
        )

    def _publish_locked(self, *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_publish < 0.05:
            return
        self._last_publish = now
        self._update_elapsed_locked()
        snapshot = copy.deepcopy(self._state)
        stale: list[queue.Queue[dict[str, Any]]] = []
        for subscriber in self._subscribers:
            try:
                if subscriber.full():
                    subscriber.get_nowait()
                subscriber.put_nowait(snapshot)
            except (queue.Empty, queue.Full):
                stale.append(subscriber)
        for subscriber in stale:
            self._subscribers.discard(subscriber)


def _initial_state() -> dict[str, Any]:
    return {
        "connection": {
            "status": "disconnected",
            "mode": "demo",
            "transport": "udp",
            "host": "127.0.0.1",
            "port": 7012,
            "message": "Ready",
        },
        "session": {
            "capturing": False,
            "recording": False,
            "recordingTarget": None,
            "takeName": "take001",
            "elapsedMs": 0,
        },
        "avatars": [],
        "sensors": [],
        "rigidBodies": [],
        "trackers": [],
        "takes": [],
        "events": [
            {
                "id": str(uuid.uuid4()),
                "timestamp": datetime.now(UTC).isoformat(),
                "level": "info",
                "source": "Studio",
                "message": "Operator console ready. Connect a BVH stream or start Demo mode.",
            }
        ],
        "diagnostics": {
            "receivedFrames": 0,
            "droppedFrames": 0,
            "jitterMs": 0.0,
            "latencyMs": None,
            "packetsPerSecond": 0.0,
            "bytesPerSecond": 0,
            "lastFrameAt": None,
        },
        "capabilities": _disconnected_capabilities(),
    }


def _disconnected_capabilities() -> dict[str, Any]:
    return {
        "receiveMotion": False,
        "receiveSensors": False,
        "serverCommands": False,
        "calibrationCommands": False,
        "axisRecording": False,
        "localRecording": False,
        "reason": "Connect a provider to discover its capabilities.",
    }


def _transport(value: Any) -> str:
    transport = str(value or "udp").lower()
    if transport not in {"udp", "tcp"}:
        raise ProviderError("Transport must be udp or tcp")
    return transport


def _unit(value: Any) -> str:
    unit = str(value or "centimeters").lower()
    if unit not in {"meters", "centimeters"}:
        raise ProviderError("Source unit must be meters or centimeters")
    return unit


def _fps(intervals: deque[float]) -> float:
    if not intervals:
        return 0.0
    average = sum(intervals) / len(intervals)
    return 1.0 / average if average else 0.0


def _jitter_ms(intervals: deque[float]) -> float:
    if len(intervals) < 2:
        return 0.0
    average = sum(intervals) / len(intervals)
    variance = sum((interval - average) ** 2 for interval in intervals) / len(intervals)
    return variance**0.5 * 1000


def _bytes_per_second(samples: deque[tuple[float, int]], now: float) -> int:
    while samples and now - samples[0][0] > 1.0:
        samples.popleft()
    return sum(size for _, size in samples)

from __future__ import annotations

import copy
import queue
import threading
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .providers import BvhProvider, DemoProvider, MotionFrame, Provider, ProviderError
from .recording import TakeRecorder, default_library_dir


class StudioController:
    def __init__(self, library_dir: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._provider: Provider | None = None
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._last_publish = 0.0
        self._last_frame_monotonic: dict[str, float] = {}
        self._frame_intervals: deque[float] = deque(maxlen=120)
        self._byte_samples: deque[tuple[float, int]] = deque(maxlen=240)
        self._session_started = 0.0
        self.recorder = TakeRecorder(library_dir or default_library_dir())
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
        mode = str(settings.get("mode", "demo"))
        with self._lock:
            if self._provider is not None:
                raise ProviderError("Disconnect the current source before connecting another")
            self._state["connection"].update(
                {"status": "connecting", "mode": mode, "message": "Connecting…"}
            )
            self._publish_locked(force=True)

        if mode == "demo":
            provider: Provider = DemoProvider(fps=float(settings.get("fps", 60)))
        elif mode == "bvh":
            provider = BvhProvider(
                transport=_transport(settings.get("transport")),
                host=str(settings.get("host", "0.0.0.0")),
                port=int(settings.get("port", 7012)),
                rotation_order=str(settings.get("rotationOrder", "YXZ")),
                source_unit=_unit(settings.get("unit")),
            )
        elif mode == "mocap-api":
            with self._lock:
                self._state["connection"].update(
                    {
                        "status": "error",
                        "message": (
                            "MocapApi runtime adapter is unavailable. Use standard BVH or Demo mode; "
                            "the upstream repository does not ship a compatible macOS library."
                        ),
                    }
                )
                self._publish_locked(force=True)
            raise ProviderError(self._state["connection"]["message"])
        else:
            raise ProviderError(f"Unknown connection mode: {mode}")

        try:
            provider.start(self._on_frame, self._on_provider_error)
        except Exception:
            provider.stop()
            with self._lock:
                self._state["connection"].update(
                    {"status": "error", "message": "Connection failed"}
                )
                self._publish_locked(force=True)
            raise

        with self._lock:
            self._provider = provider
            self._state["connection"].update(
                {
                    "status": "connected",
                    "mode": mode,
                    "transport": str(settings.get("transport", "udp")),
                    "host": str(settings.get("host", "127.0.0.1")),
                    "port": int(settings.get("port", 7012)),
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
        with self._lock:
            provider, self._provider = self._provider, None
        if provider:
            provider.stop()
        completed = self.recorder.stop(status="interrupted")
        with self._lock:
            if completed:
                self._state["takes"] = self.recorder.list_takes()
            self._state["connection"].update(
                {"status": "disconnected", "message": "Ready"}
            )
            self._state["session"].update(
                {"capturing": False, "recording": False, "elapsedMs": 0}
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
            "resume_posture",
            "resume_hands",
            "start_record",
            "stop_record",
        }
        if name not in allowed:
            raise ProviderError(f"Unknown command: {name}")
        with self._lock:
            provider = self._provider
            capabilities = self._state["capabilities"].copy()
        if provider is None:
            raise ProviderError("Connect a source before sending commands")

        if name == "start_record" and options.get("target", "local") == "local":
            if not capabilities.get("localRecording"):
                raise ProviderError("Local recording is not available for this source")
            take_name = str(options.get("takeName") or self._state["session"]["takeName"])
            notes = str(options.get("notes", ""))
            path = self.recorder.start(take_name, notes)
            with self._lock:
                self._session_started = time.monotonic()
                self._state["session"].update(
                    {"recording": True, "capturing": True, "takeName": take_name, "elapsedMs": 0}
                )
                self._add_event(
                    "success", "Recorder", f"Local recording started: {path.name}", publish=False
                )
                self._publish_locked(force=True)
            return "Local recording started"

        if name == "stop_record" and self.recorder.active:
            completed = self.recorder.stop()
            with self._lock:
                self._state["session"]["recording"] = False
                self._state["takes"] = self.recorder.list_takes()
                message = (
                    f"Saved {completed.name} ({completed.frames} frames)"
                    if completed
                    else "Local recording stopped"
                )
                self._add_event("success", "Recorder", message, publish=False)
                self._publish_locked(force=True)
            return message

        required = "serverCommands"
        if name == "calibrate":
            required = "calibrationCommands"
        if name in {"start_record", "stop_record"}:
            required = "axisRecording"
        if not capabilities.get(required):
            reason = capabilities.get("reason") or "The connected provider does not support this command"
            raise ProviderError(str(reason))
        message = provider.command(name, options)
        with self._lock:
            if name == "start_capture":
                self._session_started = time.monotonic()
                self._state["session"].update({"capturing": True, "elapsedMs": 0})
            elif name == "stop_capture":
                self._state["session"].update({"capturing": False, "recording": False})
            elif name == "start_record":
                self._session_started = time.monotonic()
                self._state["session"].update({"recording": True, "capturing": True, "elapsedMs": 0})
            elif name == "stop_record":
                self._state["session"]["recording"] = False
            self._add_event("success", "Command", message, publish=False)
            self._publish_locked(force=True)
        return message

    def close(self) -> None:
        self.disconnect()

    def _on_frame(self, frame: MotionFrame) -> None:
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
                "calibrated": True,
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

    def _on_provider_error(self, error: Exception) -> None:
        with self._lock:
            self._add_event("error", "Provider", str(error), publish=False)
            self._state["connection"]["message"] = str(error)
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
            "latencyMs": 0.0,
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

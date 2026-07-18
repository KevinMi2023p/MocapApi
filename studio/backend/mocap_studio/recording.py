from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .providers.base import MotionFrame

try:  # POSIX is the supported macOS/Linux runtime; keep imports portable.
    import fcntl
except ImportError:  # pragma: no cover - Windows is not a supported target
    fcntl = None  # type: ignore[assignment]


SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class TakeLibraryBusyError(RuntimeError):
    """Another process owns the take library's recovery/recording lock."""


@dataclass(frozen=True, slots=True)
class CompletedTake:
    id: str
    name: str
    notes: str
    started_at: str
    duration_ms: int
    frames: int
    status: str
    local_path: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "notes": self.notes,
            "startedAt": self.started_at,
            "durationMs": self.duration_ms,
            "frames": self.frames,
            "status": self.status,
            "localPath": self.local_path,
        }


class TakeRecorder:
    """Append-only, crash-recoverable local motion recorder.

    A live take uses `.partial` data and an explicit marker. Finalization fsyncs
    data and atomically renames both frame and manifest files. Existing take
    directories are never overwritten.
    """

    def __init__(self, library_dir: Path) -> None:
        self.library_dir = library_dir.expanduser().resolve()
        self.library_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._library_lock_fd: int | None = None
        self._closed = False
        self._acquire_library_lock()
        self._take_dir: Path | None = None
        self._stream: Any = None
        self._name = ""
        self._notes = ""
        self._started_wall = 0.0
        self._started_iso = ""
        self._last_frame = 0
        self._frames = 0
        self._bytes_since_sync = 0

    @property
    def active(self) -> bool:
        with self._lock:
            return self._stream is not None

    def start(self, name: str, notes: str = "") -> Path:
        with self._lock:
            self._ensure_open()
            if self._stream is not None:
                raise RuntimeError("A local take is already recording")
            clean_name = sanitize_take_name(name)
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            take_dir = _unique_directory(self.library_dir, f"{timestamp}_{clean_name}")
            take_dir.mkdir(mode=0o700)
            frames_path = take_dir / "frames.ndjson.partial"
            marker_path = take_dir / ".recording"
            stream = frames_path.open("x", encoding="utf-8", newline="\n")
            marker_path.write_text("in-progress\n", encoding="utf-8")
            self._take_dir = take_dir
            self._stream = stream
            self._name = clean_name
            self._notes = notes[:2000]
            self._started_wall = time.time()
            self._started_iso = datetime.fromtimestamp(self._started_wall, UTC).isoformat()
            self._last_frame = 0
            self._frames = 0
            self._bytes_since_sync = 0
            _atomic_json(
                take_dir / "manifest.json.partial",
                self._manifest(status="recording", duration_ms=0),
            )
            return take_dir

    def append(self, frame: MotionFrame) -> None:
        with self._lock:
            if self._stream is None:
                return
            payload = {
                "avatarId": frame.avatar_id,
                "avatarName": frame.avatar_name,
                "frame": frame.frame_number,
                "receivedAt": frame.received_at,
                "fps": frame.fps,
                "joints": [joint.as_dict() for joint in frame.joints],
                "sensors": list(frame.sensors),
            }
            if frame.source_frame_index is not None:
                payload["sourceFrame"] = frame.source_frame_index
            line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"
            self._stream.write(line)
            self._frames += 1
            self._last_frame = max(self._last_frame, frame.frame_number)
            self._bytes_since_sync += len(line.encode("utf-8"))
            if self._bytes_since_sync >= 256 * 1024:
                self._sync()

    def stop(self, status: str = "complete") -> CompletedTake | None:
        with self._lock:
            if self._stream is None or self._take_dir is None:
                return None
            duration_ms = max(0, round((time.time() - self._started_wall) * 1000))
            self._sync()
            self._stream.close()
            self._stream = None
            partial_frames = self._take_dir / "frames.ndjson.partial"
            frames_path = self._take_dir / "frames.ndjson"
            os.replace(partial_frames, frames_path)
            _fsync_directory(self._take_dir)
            manifest = self._manifest(status=status, duration_ms=duration_ms)
            _atomic_json(self._take_dir / "manifest.json", manifest)
            partial_manifest = self._take_dir / "manifest.json.partial"
            partial_manifest.unlink(missing_ok=True)
            (self._take_dir / ".recording").unlink(missing_ok=True)
            _fsync_directory(self._take_dir)
            take = CompletedTake(
                id=self._take_dir.name,
                name=self._name,
                notes=self._notes,
                started_at=self._started_iso,
                duration_ms=duration_ms,
                frames=self._frames,
                status=status,
                local_path=str(self._take_dir),
            )
            self._take_dir = None
            return take

    def recover_interrupted(self) -> list[CompletedTake]:
        with self._lock:
            self._ensure_open()
            recovered: list[CompletedTake] = []
            for marker in sorted(self.library_dir.glob("*/.recording")):
                take_dir = marker.parent
                if take_dir == self._take_dir:
                    continue
                partial_frames = take_dir / "frames.ndjson.partial"
                frames_path = take_dir / "frames.ndjson"
                if partial_frames.exists() and not frames_path.exists():
                    os.replace(partial_frames, frames_path)
                partial_manifest = take_dir / "manifest.json.partial"
                manifest: dict[str, Any] = {}
                if partial_manifest.exists():
                    try:
                        loaded = json.loads(partial_manifest.read_text(encoding="utf-8"))
                        manifest = loaded if isinstance(loaded, dict) else {}
                    except (OSError, json.JSONDecodeError):
                        manifest = {}
                frame_count = _count_lines(frames_path) if frames_path.exists() else 0
                manifest.update(
                    {
                        "schemaVersion": 1,
                        "id": take_dir.name,
                        "name": manifest.get("name", take_dir.name),
                        "notes": manifest.get("notes", "Recovered after an interrupted recording"),
                        "status": "interrupted",
                        "frames": frame_count,
                        "localPath": str(take_dir),
                    }
                )
                _atomic_json(take_dir / "manifest.json", manifest)
                partial_manifest.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                recovered.append(
                    CompletedTake(
                        id=take_dir.name,
                        name=str(manifest["name"]),
                        notes=str(manifest["notes"]),
                        started_at=str(manifest.get("startedAt", "")),
                        duration_ms=_safe_nonnegative_int(manifest.get("durationMs")),
                        frames=frame_count,
                        status="interrupted",
                        local_path=str(take_dir),
                    )
                )
            return recovered

    def list_takes(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_open()
            takes: list[dict[str, Any]] = []
            for path in sorted(self.library_dir.glob("*/manifest.json"), reverse=True):
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(manifest, dict):
                    continue
                takes.append(
                    {
                        "id": str(manifest.get("id", path.parent.name)),
                        "name": str(manifest.get("name", path.parent.name)),
                        "notes": str(manifest.get("notes", "")),
                        "startedAt": str(manifest.get("startedAt", "")),
                        "durationMs": _safe_nonnegative_int(manifest.get("durationMs")),
                        "frames": _safe_nonnegative_int(manifest.get("frames")),
                        "status": str(manifest.get("status", "complete")),
                        "localPath": str(path.parent),
                    }
                )
            return takes

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            failure: BaseException | None = None
            try:
                if self._stream is not None:
                    self.stop(status="interrupted")
            except BaseException as error:
                failure = error
            finally:
                # If finalization failed before stop() closed the stream, leave
                # the marker/partial files for next-start recovery but release
                # this process's file handle and ownership deterministically.
                if self._stream is not None:
                    try:
                        self._stream.close()
                    except BaseException as error:
                        if failure is None:
                            failure = error
                    self._stream = None
                descriptor, self._library_lock_fd = self._library_lock_fd, None
                if descriptor is not None:
                    try:
                        if fcntl is not None:
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                    except BaseException as error:
                        if failure is None:
                            failure = error
                    finally:
                        try:
                            os.close(descriptor)
                        except BaseException as error:
                            if failure is None:
                                failure = error
                self._closed = True
            if failure is not None:
                raise failure.with_traceback(failure.__traceback__)

    def _acquire_library_lock(self) -> None:
        if fcntl is None:
            raise RuntimeError("Take library locking requires a POSIX runtime")
        lock_path = self.library_dir / ".mocap-studio.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as error:
            raise TakeLibraryBusyError(
                f"Take library lock must be a regular, non-symlink file: {lock_path}"
            ) from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise TakeLibraryBusyError(
                    f"Take library lock must be a regular, singly linked file: {lock_path}"
                )
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(descriptor)
            raise TakeLibraryBusyError(
                f"Take library is already in use by another Mocap Studio instance: "
                f"{self.library_dir}"
            ) from error
        except Exception:
            os.close(descriptor)
            raise
        self._library_lock_fd = descriptor

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Take recorder is closed")

    def _sync(self) -> None:
        if self._stream is None:
            return
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._bytes_since_sync = 0

    def _manifest(self, *, status: str, duration_ms: int) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "id": self._take_dir.name if self._take_dir else "",
            "name": self._name,
            "notes": self._notes,
            "startedAt": self._started_iso,
            "durationMs": duration_ms,
            "frames": self._frames,
            "lastSourceFrame": self._last_frame,
            "status": status,
            "format": "mocap-studio-ndjson-v1",
            "localPath": str(self._take_dir) if self._take_dir else "",
        }


def sanitize_take_name(name: str) -> str:
    cleaned = SAFE_NAME.sub("-", name.strip()).strip(".-_")[:80]
    return cleaned or "take"


def default_library_dir() -> Path:
    if os.name == "posix" and "darwin" in os.sys.platform:
        return Path.home() / "Library" / "Application Support" / "Mocap Studio" / "Takes"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "mocap-studio" / "takes"


def _unique_directory(parent: Path, stem: str) -> Path:
    candidate = parent / stem
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{stem}-{suffix}"
        suffix += 1
    return candidate


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _count_lines(path: Path) -> int:
    with path.open("rb") as stream:
        return sum(1 for _ in stream)


def _safe_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0

from __future__ import annotations

import json
import mimetypes
import queue
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .state import StudioController


MAX_REQUEST_BODY = 64 * 1024
SAFE_HOST = re.compile(r"^(localhost|127(?:\.\d{1,3}){3}|\[?::1\]?)(:\d+)?$", re.IGNORECASE)
STATIC_ROOT = Path(__file__).with_name("static")


class StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], controller: StudioController) -> None:
        super().__init__(address, StudioRequestHandler)
        self.controller = controller


class StudioRequestHandler(BaseHTTPRequestHandler):
    server: StudioHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        if not self._trusted_host():
            return
        path = urlsplit(self.path).path
        if path == "/api/state":
            self._send_json(self.server.controller.snapshot())
        elif path == "/api/events":
            self._serve_events()
        elif path == "/api/health":
            self._send_json({"status": "ok"})
        else:
            self._serve_static(path, include_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        if not self._trusted_host():
            return
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)
        else:
            self._serve_static(path, include_body=False)

    def do_POST(self) -> None:  # noqa: N802
        if not self._trusted_host():
            return
        try:
            body = self._read_json()
            path = urlsplit(self.path).path
            if path == "/api/connect":
                self.server.controller.connect(body)
                self._send_json({"ok": True}, status=HTTPStatus.ACCEPTED)
            elif path == "/api/disconnect":
                self.server.controller.disconnect()
                self._send_json({"ok": True})
            elif path == "/api/commands":
                command = body.pop("command", None)
                if not isinstance(command, str):
                    raise ValueError("A string command is required")
                message = self.server.controller.command(command, body)
                self._send_json({"ok": True, "message": message})
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError) as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as error:
            self._send_json({"error": str(error)}, status=HTTPStatus.CONFLICT)

    def do_OPTIONS(self) -> None:  # noqa: N802
        # The local API intentionally has no cross-origin policy. This prevents
        # arbitrary websites from driving capture commands through a browser.
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep terminal output useful; health polling and SSE reconnects are not
        # actionable access-log entries for a local desktop service.
        if self.path == "/api/health":
            return
        super().log_message(format, *args)

    def _trusted_host(self) -> bool:
        host = self.headers.get("Host", "")
        if SAFE_HOST.fullmatch(host):
            return True
        self._send_json(
            {"error": "Rejected Host header; Mocap Studio accepts loopback requests only"},
            status=HTTPStatus.FORBIDDEN,
        )
        return False

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if length <= 0 or length > MAX_REQUEST_BODY:
            raise ValueError("JSON request body must be between 1 byte and 64 KiB")
        raw = self.rfile.read(length)
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Malformed JSON request") from error
        if not isinstance(result, dict):
            raise ValueError("JSON request must be an object")
        return result

    def _serve_events(self) -> None:
        subscriber = self.server.controller.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._security_headers()
        self.end_headers()
        try:
            while True:
                try:
                    snapshot = subscriber.get(timeout=15.0)
                    payload = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
                    self.wfile.write(f"event: state\ndata: {payload}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.controller.unsubscribe(subscriber)

    def _serve_static(self, request_path: str, *, include_body: bool) -> None:
        if not STATIC_ROOT.exists():
            self._send_text(
                "Frontend assets are not built. Run `npm run build` in studio/frontend.\n",
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        relative = unquote(request_path).lstrip("/") or "index.html"
        candidate = (STATIC_ROOT / relative).resolve()
        try:
            candidate.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            candidate = STATIC_ROOT / "index.html"
        try:
            data = candidate.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if candidate.name == "index.html":
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self._security_headers()
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text: str, *, status: HTTPStatus) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'",
        )


def run_server(
    controller: StudioController,
    host: str = "127.0.0.1",
    port: int = 8765,
    ready: threading.Event | None = None,
) -> StudioHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Mocap Studio's local API may only bind to a loopback address")
    server = StudioHTTPServer((host, port), controller)
    if ready:
        ready.set()
    return server

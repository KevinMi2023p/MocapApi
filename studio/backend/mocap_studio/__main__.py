from __future__ import annotations

import argparse
import http.client
import json
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import APPLICATION_ID, __version__
from .recording import TakeLibraryBusyError
from .server import run_server
from .state import StudioController


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mocap-studio",
        description="Launch the local Mocap Studio operator console.",
    )
    parser.add_argument("--port", type=int, default=8765, help="loopback HTTP port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the UI in a browser")
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="reopen an existing instance on the requested port instead of failing",
    )
    parser.add_argument("--data-dir", type=Path, help="override the local take library directory")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def existing_instance_url(port: int, timeout: float = 0.35) -> str | None:
    """Return the URL only when *port* belongs to this application.

    Desktop launchers use this narrowly scoped probe to make repeated app-drawer
    or Finder launches reopen the existing browser UI. A generic healthy HTTP
    service is never treated as Mocap Studio merely because it occupies the
    configured port.
    """

    if not 1 <= port <= 65535:
        return None
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", "/api/instance")
        response = connection.getresponse()
        if response.status != 200:
            response.read(4096)
            return None
        raw = response.read(4097)
        if len(raw) > 4096:
            return None
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("application") != APPLICATION_ID:
            return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, http.client.HTTPException):
        return None
    finally:
        connection.close()
    return f"http://127.0.0.1:{port}/"


def reopen_existing(
    port: int, *, open_browser: bool, attempts: int = 1, retry_delay: float = 0.1
) -> bool:
    for attempt in range(max(1, attempts)):
        url = existing_instance_url(port)
        if url is not None:
            if open_browser:
                webbrowser.open(url)
            print(f"Mocap Studio is already running at {url}")
            return True
        if attempt + 1 < attempts:
            time.sleep(retry_delay)
    return False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("error: --port must be between 0 and 65535", file=sys.stderr)
        return 2
    if args.reuse_existing and reopen_existing(args.port, open_browser=not args.no_browser):
        return 0
    try:
        controller = StudioController(args.data_dir)
    except TakeLibraryBusyError as error:
        # A concurrent first launch acquires the take-library lock just before
        # it binds the HTTP port. Give that winning process a brief chance to
        # become discoverable, then reopen it.
        if args.reuse_existing and reopen_existing(
            args.port, open_browser=not args.no_browser, attempts=10
        ):
            return 0
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        server = run_server(controller, port=args.port)
    except (OSError, ValueError) as error:
        try:
            controller.close()
        except Exception:
            pass
        # Two desktop launches can race between the initial probe and bind.
        # Probe once more after a failed bind so the loser reliably reopens the
        # instance that won the race.
        if args.reuse_existing and reopen_existing(
            args.port, open_browser=not args.no_browser, attempts=3
        ):
            return 0
        print(f"error: could not start local server: {error}", file=sys.stderr)
        return 1
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    stopping = threading.Event()

    def shutdown(_signum: int, _frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    if not args.no_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    print(f"Mocap Studio {__version__} is running at {url}")
    print("Press Ctrl+C to stop. Motion and takes remain on this computer.")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        controller.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

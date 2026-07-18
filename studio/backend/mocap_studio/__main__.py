from __future__ import annotations

import argparse
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
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
    parser.add_argument("--data-dir", type=Path, help="override the local take library directory")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("error: --port must be between 0 and 65535", file=sys.stderr)
        return 2
    try:
        controller = StudioController(args.data_dir)
    except TakeLibraryBusyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    try:
        server = run_server(controller, port=args.port)
    except (OSError, ValueError) as error:
        try:
            controller.close()
        except Exception:
            pass
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

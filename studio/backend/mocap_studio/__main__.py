from __future__ import annotations

import argparse
import difflib
import http.client
import json
import re
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


RUNTIME_OPTIONS = (
    "-h",
    "--help",
    "--port",
    "--no-browser",
    "--reuse-existing",
    "--data-dir",
    "--version",
)
COMMANDS = ("help", "update", "uninstall", "completion")


class HelpfulArgumentParser(argparse.ArgumentParser):
    """Argparse parser that points syntax mistakes back to actionable help."""

    def _suggestion(self, message: str) -> str | None:
        match = re.match(r"unrecognized arguments?:\s+(\S+)", message)
        if match is None:
            return None
        unknown = match.group(1).split("=", 1)[0]
        candidates = RUNTIME_OPTIONS if unknown.startswith("-") else COMMANDS
        matches = difflib.get_close_matches(unknown, candidates, n=1, cutoff=0.72)
        return matches[0] if matches else None

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self._print_message(f"{self.prog}: error: {message}\n", sys.stderr)
        if suggestion := self._suggestion(message):
            self._print_message(f"Did you mean '{suggestion}'?\n", sys.stderr)
        self._print_message(
            f"Try '{self.prog} --help' for more information.\n", sys.stderr
        )
        self.exit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = HelpfulArgumentParser(
        prog="mocap-studio",
        allow_abbrev=False,
        description="Launch the local Mocap Studio operator console.",
        epilog=(
            "Commands:\n"
            "  update       Install the latest Mocap Studio release.\n"
            "  uninstall    Remove the application while preserving recorded takes.\n"
            "  completion   Print or install Bash, Zsh, and Fish completion.\n"
            "  help          Show this help or help for a command.\n\n"
            "Run 'mocap-studio help COMMAND' for command-specific help."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
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

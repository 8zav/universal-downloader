from __future__ import annotations

import argparse
import contextlib
import os
import sys
from typing import Iterator, List, Optional

from .download import Progress
from .errors import UdlError
from .format import format_bytes, format_speed
from .manager import DownloadManager

_REFRESH_SECONDS = 0.5
_BAR_WIDTH = 20
_QUIT_EXIT_CODE = 130


def _bar(percent: Optional[float]) -> str:
    filled = round(((percent or 0.0) / 100) * _BAR_WIDTH)
    return f"[{'#' * filled}{'.' * (_BAR_WIDTH - filled)}]"


def _line(snapshot: Progress) -> str:
    size = format_bytes(snapshot.downloaded)
    if snapshot.total is not None:
        size = f"{size}/{format_bytes(snapshot.total)}"
    percent = "  ?  " if snapshot.percent is None else f"{snapshot.percent:5.1f}%"
    return (f"{snapshot.file_name[:30]:<30} {_bar(snapshot.percent)} {percent} {size}  "
            f"{format_speed(snapshot.speed)}  left {format_bytes(snapshot.remaining)}  "
            f"ETA {snapshot.eta_text}  {snapshot.state}")


def _render(manager: DownloadManager, previous_lines: int) -> int:
    lines = [_line(item.progress) for item in manager.downloads]
    stats = manager.stats()
    lines.append(
        f"Total: {stats.active} active, {stats.queued} queued, {stats.paused} paused, {stats.completed} done, "
        f"{stats.failed} failed | {format_speed(stats.speed)} | left {format_bytes(stats.remaining)} | "
        f"ETA {stats.eta_text}"
    )
    up = f"\x1b[{previous_lines}A" if previous_lines else ""
    sys.stdout.write(up + "\n".join(f"\x1b[2K{text}" for text in lines) + "\n")
    sys.stdout.flush()
    return len(lines)


@contextlib.contextmanager
def _raw_keys() -> Iterator[None]:
    if not sys.stdin.isatty() or os.name == "nt":
        yield
        return
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    saved = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        yield
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)


def _read_key() -> Optional[str]:
    if not sys.stdin.isatty():
        return None
    if os.name == "nt":
        import msvcrt

        return msvcrt.getwch() if msvcrt.kbhit() else None
    import select

    return sys.stdin.read(1) if select.select([sys.stdin], [], [], 0)[0] else None


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="udl",
        description="Download from file hosts. Keys while running: [p] pause all, [r] resume all, [q] quit "
                    "(partial files are kept).",
    )
    parser.add_argument("urls", nargs="+", help="links to download")
    parser.add_argument("-o", "--out", default="downloads", help="destination folder (default: ./downloads)")
    parser.add_argument("-c", "--concurrency", type=int, default=3, help="parallel downloads (default: 3)")
    parser.add_argument("--password", help="password for protected links")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if os.name == "nt":
        os.system("")

    manager = DownloadManager(directory=args.out, concurrency=args.concurrency)
    manager.on("failed", lambda item, error: sys.stderr.write(f"\n{item.file_name}: {error}\n"))

    for url in args.urls:
        try:
            manager.add(url, password=args.password)
        except (UdlError, OSError, ValueError) as error:
            sys.stderr.write(f"{url}: {error}\n")

    drawn = 0
    exit_code = 0
    try:
        with _raw_keys():
            while not manager.wait_idle(timeout=_REFRESH_SECONDS):
                drawn = _render(manager, drawn)
                key = _read_key()
                if key == "p":
                    manager.pause_all()
                elif key == "r":
                    manager.resume_all()
                elif key in ("q", "\x03"):
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        manager.pause_all()
        exit_code = _QUIT_EXIT_CODE
    _render(manager, drawn)
    return exit_code or (1 if manager.stats().failed else 0)

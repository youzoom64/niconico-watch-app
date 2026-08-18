from __future__ import annotations

import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any


def _write_stdout(text: str) -> bool:
    """Write to stdout when a console is available.

    GUI-only Python processes (notably ``pythonw.exe`` on Windows) expose
    ``sys.stdout`` as ``None``.  Progress output is optional, so an absent or
    unusable stream must never interrupt the actual job.
    """
    stream = sys.stdout
    if stream is None:
        return False
    try:
        stream.write(text)
        stream.flush()
    except Exception:
        return False
    return True


def hms_seconds(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    try:
        seconds = max(0, int(float(seconds)))
    except Exception:
        return "-"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_ffmpeg_time_seconds(value: str | None) -> float | None:
    if not value:
        return None
    match = re.match(r"(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)", str(value).strip())
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def progress_percent_eta(done_seconds: float | None, total_seconds: float | None, elapsed_seconds: float) -> dict[str, Any]:
    if not done_seconds or not total_seconds or total_seconds <= 0:
        return {"percent": None, "eta_seconds": None}
    percent = max(0.0, min(100.0, (float(done_seconds) / float(total_seconds)) * 100.0))
    if done_seconds <= 0 or elapsed_seconds <= 0:
        eta = None
    else:
        speed = float(done_seconds) / float(elapsed_seconds)
        eta = (float(total_seconds) - float(done_seconds)) / speed if speed > 0 else None
    return {"percent": percent, "eta_seconds": eta}


def progress_bar(percent: float | None, *, width: int = 20) -> str:
    if percent is None:
        return "[" + "-" * width + "]"
    percent = max(0.0, min(100.0, float(percent)))
    filled = int((percent / 100.0) * width)
    marker = ">" if 0 < filled < width else ""
    empty = max(0, width - filled - len(marker))
    return "[" + "#" * filled + marker + "-" * empty + "]"


def terminal_width(default: int = 80) -> int:
    try:
        return max(60, min(120, int(shutil.get_terminal_size((default, 20)).columns)))
    except Exception:
        return default


def shorten_text(text: str, max_width: int) -> str:
    text = str(text).replace("\n", " ").replace("\r", " ")
    if len(text) <= max_width:
        return text
    return text[: max(0, max_width - 1)] + "…"


@dataclass
class ConsoleProgress:
    label: str
    total_seconds: float | None = None
    min_interval_seconds: float = 0.25
    width: int = field(default_factory=terminal_width)
    previous_length: int = 0
    previous_line: str = ""
    started_monotonic: float = field(default_factory=time.monotonic)
    last_draw_monotonic: float = 0.0
    active: bool = False

    def update(
        self,
        done_seconds: float | None,
        *,
        speed: str = "",
        size: str = "",
        frame: str = "",
        extra: str = "",
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and self.last_draw_monotonic and now - self.last_draw_monotonic < self.min_interval_seconds:
            return
        self.last_draw_monotonic = now
        self.active = True
        self.width = terminal_width()
        line = self.make_line(done_seconds, speed=speed, size=size, frame=frame, extra=extra)
        if line == self.previous_line:
            return
        self.write_line(line)
        self.previous_line = line

    def make_line(
        self,
        done_seconds: float | None,
        *,
        speed: str = "",
        size: str = "",
        frame: str = "",
        extra: str = "",
    ) -> str:
        elapsed = time.monotonic() - self.started_monotonic
        percent_eta = progress_percent_eta(done_seconds, self.total_seconds, elapsed)
        percent = percent_eta["percent"]
        eta = percent_eta["eta_seconds"]
        percent_text = f"{percent:5.1f}%" if percent is not None else "    -"
        label = shorten_text(self.label, 30)
        if done_seconds is None:
            line = f"{label} {percent_text} {progress_bar(None, width=12)} elapsed {hms_seconds(elapsed)}"
            if extra:
                line += " " + extra
            return shorten_text(line, self.width - 3)
        fixed = (
            f"{label} {percent_text} "
            f"{hms_seconds(done_seconds)}/{hms_seconds(self.total_seconds)} "
            f"ETA {hms_seconds(eta)}"
        )
        tail_parts = [part for part in [speed, extra, size, f"f{frame}" if frame else ""] if part]
        tail = " ".join(tail_parts)
        available = max(10, self.width - len(fixed) - len(tail) - 4)
        bar_width = max(8, min(14, available - 2))
        line = f"{label} {percent_text} {progress_bar(percent, width=bar_width)} {hms_seconds(done_seconds)}/{hms_seconds(self.total_seconds)} ETA {hms_seconds(eta)}"
        if tail:
            line += " " + tail
        return shorten_text(line, self.width - 3)

    def write_line(self, line: str) -> None:
        # Keep this plain for Windows cmd.exe: ANSI clear can be disabled, and
        # long wrapped lines cannot be overwritten by a single carriage return.
        width = max(20, terminal_width() - 8)
        line = shorten_text(line, width)
        clear_tail_len = max(0, min(self.previous_length, width) - len(line))
        _write_stdout("\r" + line + (" " * clear_tail_len))
        self.previous_length = len(line)

    def finish(self, message: str = "done") -> None:
        if self.active:
            self.write_line(f"{self.label} {message}")
            _write_stdout("\n")
            self.active = False
            self.previous_length = 0
            self.previous_line = ""

    def fail(self, message: str = "failed") -> None:
        if self.active:
            self.write_line(f"{self.label} {message}")
            _write_stdout("\n")
            self.active = False
            self.previous_length = 0
            self.previous_line = ""

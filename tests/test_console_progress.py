from __future__ import annotations

import sys

import pytest

from console_progress import ConsoleProgress


@pytest.mark.parametrize("terminal_method", ["finish", "fail"])
def test_console_progress_does_not_fail_when_stdout_is_none(
    monkeypatch: pytest.MonkeyPatch,
    terminal_method: str,
) -> None:
    progress = ConsoleProgress("pythonw job", total_seconds=10)
    monkeypatch.setattr(sys, "stdout", None)

    progress.update(5, force=True)
    getattr(progress, terminal_method)()

    assert progress.active is False
    assert progress.previous_length == 0
    assert progress.previous_line == ""


def test_console_progress_does_not_fail_when_stdout_write_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenStdout:
        def write(self, _text: str) -> None:
            raise OSError("console is unavailable")

        def flush(self) -> None:
            raise OSError("console is unavailable")

    progress = ConsoleProgress("broken console", total_seconds=10)
    monkeypatch.setattr(sys, "stdout", BrokenStdout())

    progress.update(5, force=True)
    progress.finish()

    assert progress.active is False

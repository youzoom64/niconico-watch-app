from __future__ import annotations

import subprocess

import tracker


def test_selenium_cleanup_never_decodes_localized_windows_output(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        command = list(command)
        calls.append((command, kwargs))
        if command[0] == "powershell":
            # 0x83 is a common leading byte in CP932 Japanese output and is
            # invalid as a standalone UTF-8 byte.  PID lines are ASCII only.
            return subprocess.CompletedProcess(command, 0, b"\x83localized error\r\n4321\r\n", b"\x83error")
        return subprocess.CompletedProcess(command, 0, None, None)

    monkeypatch.setattr(tracker, "close_tracker_driver", lambda: None)
    monkeypatch.setattr(tracker.subprocess, "run", fake_run)
    monkeypatch.setattr(tracker.shutil, "rmtree", lambda *_args, **_kwargs: None)

    assert tracker.cleanup_selenium_processes() == [4321]

    powershell_call = calls[0][1]
    assert powershell_call["capture_output"] is True
    assert "text" not in powershell_call
    assert "encoding" not in powershell_call

    taskkill_call = calls[1][1]
    assert taskkill_call["stdout"] is subprocess.DEVNULL
    assert taskkill_call["stderr"] is subprocess.DEVNULL
    assert "capture_output" not in taskkill_call
    assert "text" not in taskkill_call

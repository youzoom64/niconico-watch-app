from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import tracker


ROOT = Path(__file__).resolve().parents[1]


def test_main_exposes_timeshift_as_a_separate_command() -> None:
    spec = importlib.util.spec_from_file_location(
        "niconico_watch_app_entrypoint",
        ROOT / "main.py",
    )
    assert spec is not None and spec.loader is not None
    entrypoint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrypoint)
    args = entrypoint.build_parser().parse_args(["timeshift"])
    assert callable(args.handler)


def test_timeshift_command_accepts_multiple_input_urls() -> None:
    spec = importlib.util.spec_from_file_location(
        "niconico_watch_app_entrypoint_multi_url",
        ROOT / "main.py",
    )
    assert spec is not None and spec.loader is not None
    entrypoint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrypoint)

    args = entrypoint.build_parser().parse_args(
        [
            "timeshift",
            "--input-url",
            "https://live.nicovideo.jp/watch/lv100",
            "--input-url",
            "https://live.nicovideo.jp/watch/lv200",
            "--input-file",
            "C:/recordings/lv100.mp4",
            "--input-file",
            "C:/recordings/lv200.mp4",
        ]
    )

    assert args.input_url == [
        "https://live.nicovideo.jp/watch/lv100",
        "https://live.nicovideo.jp/watch/lv200",
    ]
    assert args.input_file == [
        "C:/recordings/lv100.mp4",
        "C:/recordings/lv200.mp4",
    ]


def test_selected_broadcaster_rows_are_deduplicated_by_lv() -> None:
    import gui_app

    assert gui_app.broadcaster_rows_to_timeshift_urls(
        [
            {"lv": "lv100", "text": "first"},
            {"lv": "https://live.nicovideo.jp/watch/lv200"},
            {"lv": "lv100", "text": "same broadcast, another row"},
            {"lv": ""},
        ]
    ) == [
        "https://live.nicovideo.jp/watch/lv100",
        "https://live.nicovideo.jp/watch/lv200",
    ]
    assert gui_app.broadcaster_rows_to_lvs(
        [{"lv": "lv100"}, {"lv": "lv200"}, {"lv": "lv100"}]
    ) == ["lv100", "lv200"]


def test_right_click_inside_selection_keeps_all_selected_rows() -> None:
    import gui_app

    class FakeIndex:
        def __init__(self, row: int) -> None:
            self._row = row

        def row(self) -> int:
            return self._row

    class FakeSelectionModel:
        def selectedRows(self) -> list[FakeIndex]:
            return [FakeIndex(4), FakeIndex(1), FakeIndex(4)]

    class FakeTable:
        def selectionModel(self) -> FakeSelectionModel:
            return FakeSelectionModel()

    table = FakeTable()
    assert gui_app.context_action_row_numbers(table, 4) == [1, 4]
    assert gui_app.context_action_row_numbers(table, 7) == [7]


def test_handoff_protocol_requires_receiver_acknowledgement() -> None:
    from timeshift_handoff import (
        decode_ack_message,
        decode_add_local_files_message,
        decode_add_urls_message,
        encode_ack_message,
        encode_add_local_files_message,
        encode_add_urls_message,
    )

    urls = [
        "https://live.nicovideo.jp/watch/lv100",
        "https://live.nicovideo.jp/watch/lv200",
        "https://live.nicovideo.jp/watch/lv100",
    ]
    assert decode_add_urls_message(encode_add_urls_message(urls).rstrip(b"\n")) == urls[:2]
    paths = ["C:/video/lv100.mp4", "C:/video/lv200.mp4", "C:/VIDEO/lv100.mp4"]
    assert decode_add_local_files_message(
        encode_add_local_files_message(paths).rstrip(b"\n")
    ) == paths[:2]
    assert decode_ack_message(encode_ack_message(2).rstrip(b"\n"))
    assert not decode_ack_message(b'{"status":"failed"}')


def test_timeshift_input_adds_multiple_urls_without_duplicates() -> None:
    code = """
import os
import sys
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path.cwd() / 'app'))
from PyQt6.QtWidgets import QApplication
import gui_app
app = QApplication.instance() or QApplication([])
tab = gui_app.TimeshiftTab()
tab.url_input.setPlainText('https://live.nicovideo.jp/watch/lv100')
added = tab.add_input_urls([
    'https://live.nicovideo.jp/watch/lv100',
    'https://live.nicovideo.jp/watch/lv200',
    'https://live.nicovideo.jp/watch/lv300',
])
assert added == [
    'https://live.nicovideo.jp/watch/lv200',
    'https://live.nicovideo.jp/watch/lv300',
]
assert tab.url_input.toPlainText().splitlines() == [
    'https://live.nicovideo.jp/watch/lv100',
    'https://live.nicovideo.jp/watch/lv200',
    'https://live.nicovideo.jp/watch/lv300',
]
"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_inspection_table_keeps_real_qt_multiple_row_selection() -> None:
    code = """
import os
import sys
from pathlib import Path
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path.cwd() / 'app'))
from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import QApplication, QAbstractItemView
import gui_app
app = QApplication.instance() or QApplication([])
model = gui_app.SimpleDictTableModel([('lv', 'LV')])
model.update_rows([{'lv': f'lv{index}'} for index in range(4)])
table = gui_app.InspectionTab.make_table(None, model)
assert table.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection
flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
table.selectionModel().select(model.index(1, 0), flags)
table.selectionModel().select(model.index(3, 0), flags)
assert gui_app.context_action_row_numbers(table, 3) == [1, 3]
assert gui_app.context_action_row_numbers(table, 2) == [2]
"""
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_monitor_launches_timeshift_with_pythonw_and_all_urls(monkeypatch) -> None:
    import gui_app

    calls: list[tuple[str, list[str], str]] = []

    class FakeQProcess:
        @staticmethod
        def startDetached(program: str, arguments: list[str], cwd: str):
            calls.append((program, list(arguments), cwd))
            return True, 4321

    monkeypatch.setattr(gui_app, "send_handoff_urls", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(gui_app, "QProcess", FakeQProcess)

    result = gui_app.send_urls_to_timeshift_gui(
        [
            "https://live.nicovideo.jp/watch/lv100",
            "https://live.nicovideo.jp/watch/lv200",
        ]
    )

    assert result == "started"
    assert len(calls) == 1
    program, arguments, cwd = calls[0]
    assert Path(program).name.lower() == "pythonw.exe"
    assert Path(program).name.lower() not in {"cmd.exe", "powershell.exe", "python.exe"}
    assert arguments.count("--input-url") == 2
    assert "https://live.nicovideo.jp/watch/lv100" in arguments
    assert "https://live.nicovideo.jp/watch/lv200" in arguments
    assert Path(cwd) == ROOT


def test_monitor_launches_local_processing_with_pythonw_and_all_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import gui_app

    first = tmp_path / "lv100.mp4"
    second = tmp_path / "lv200.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    calls: list[tuple[str, list[str], str]] = []

    class FakeQProcess:
        @staticmethod
        def startDetached(program: str, arguments: list[str], cwd: str):
            calls.append((program, list(arguments), cwd))
            return True, 4321

    monkeypatch.setattr(gui_app, "send_handoff_local_files", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(gui_app, "QProcess", FakeQProcess)

    result = gui_app.send_local_files_to_processing_gui([first, second])

    assert result == "started"
    assert len(calls) == 1
    program, arguments, cwd = calls[0]
    assert Path(program).name.lower() == "pythonw.exe"
    assert arguments.count("--input-file") == 2
    assert str(first) in arguments
    assert str(second) in arguments
    assert "--input-url" not in arguments
    assert Path(cwd) == ROOT


def test_local_processing_labels_do_not_call_existing_files_timeshift() -> None:
    source = (ROOT / "app" / "gui_app.py").read_text(encoding="utf-8")
    standalone = (ROOT / "app" / "timeshift_app.py").read_text(encoding="utf-8")

    assert 'QPushButton("ローカル動画からHTML作成")' in source
    assert 'self.tabs.addTab(self.local_files_tab, "ローカル処理")' in standalone
    assert "ローカル処理へ送る" in source


def test_html_generation_checkbox_is_immediately_right_of_monitor() -> None:
    import gui_app

    assert gui_app.BroadcasterMonitorTableModel.columns[:3] == [
        ("onair", "配信"),
        ("enabled", "監視"),
        ("html_generation_enabled", "HTML生成"),
    ]
    assert "html_generation_enabled" in gui_app.BroadcasterMonitorTableModel.checkable


def test_monitor_window_no_longer_registers_timeshift_tab() -> None:
    source = (ROOT / "app" / "gui_app.py").read_text(encoding="utf-8")
    main_window_source = source.split("class MainWindow", 1)[1]
    assert 'tabs.addTab(self.timeshift_tab, "タイムシフト")' not in main_window_source
    assert "self.timeshift_tab = TimeshiftTab()" not in main_window_source


def test_timeshift_launcher_is_detached_and_does_not_start_monitor_api() -> None:
    source = (ROOT / "scripts" / "start_timeshift_gui.cmd").read_text(encoding="utf-8")
    assert "start \"Niconico Timeshift\"" in source
    assert "pythonw.exe" in source
    assert "main.py\" timeshift" in source
    assert "start_intervention_api" not in source
    assert "NICONICO_WATCH_APP_ROLE=timeshift" in source


def test_standalone_process_reuses_the_same_tracker_database() -> None:
    code = """
import os
import sys
from pathlib import Path
root = Path.cwd()
sys.path.insert(0, str(root / 'app'))
os.environ['NICONICO_WATCH_APP_ROLE'] = 'timeshift'
import tracker
import timeshift_app
import gui_app
assert timeshift_app.tracker is tracker
assert gui_app.tracker is tracker
assert tracker.DB_PATH.resolve() == (root / 'data' / 'tracker.db').resolve()
print(tracker.DB_PATH.resolve())
"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert str((ROOT / "data" / "tracker.db").resolve()) in result.stdout


def test_monitor_role_cannot_run_a_timeshift_worker() -> None:
    code = """
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'app'))
os.environ['NICONICO_WATCH_APP_ROLE'] = 'monitor'
import gui_app
try:
    gui_app.require_timeshift_process()
except RuntimeError:
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_html_step_never_opens_a_console(
    monkeypatch,
    tmp_path: Path,
) -> None:
    popen_calls: list[object] = []
    log_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        tracker.subprocess,
        "Popen",
        lambda *args, **kwargs: popen_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        tracker,
        "postprocess_log",
        lambda *args, **kwargs: log_calls.append((*args, kwargs)),
    )

    for role in ("timeshift", "monitor", ""):
        monkeypatch.setenv("NICONICO_WATCH_APP_ROLE", role)
        tracker.start_visible_legacy_step_log_window(
            f"lv-{role or 'unset'}",
            "step12_html_generator",
            tmp_path / f"step12-{role or 'unset'}.log",
        )

    assert popen_calls == []
    assert len(log_calls) == 3
    assert all("cmd表示は無効" in str(call) for call in log_calls)


def test_windows_child_process_flags_always_disable_console() -> None:
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    create_new_console = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0) or 0)
    flags = tracker._windows_no_console_creationflags(create_new_console)

    assert flags & create_new_console == 0
    if os.name == "nt":
        assert create_no_window
        assert flags & create_no_window


def test_finalize_worker_bypasses_cmd_and_runs_hidden(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class DummyProcess:
        pid = 4321

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return DummyProcess()

    monkeypatch.setattr(tracker, "TMP_DIR", tmp_path)
    monkeypatch.setattr(tracker.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(tracker, "postprocess_log", lambda *args, **kwargs: None)

    assert tracker.start_visible_finalize_pipeline_process(
        lv="lv-hidden",
        broadcaster_id="39532023",
        target_dir=str(tmp_path / "target"),
    )
    assert len(calls) == 1
    args, kwargs = calls[0]
    command = list(args[0])
    assert Path(str(command[0])).name.lower() not in {"cmd.exe", "powershell.exe"}
    assert Path(str(command[1])).name == "run_finalize_pipeline.py"
    if os.name == "nt":
        assert int(kwargs["creationflags"]) & int(subprocess.CREATE_NO_WINDOW)

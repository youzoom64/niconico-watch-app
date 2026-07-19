from __future__ import annotations

import sqlite3
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "process_supervisor.db"


def now_text() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS process_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tab_name TEXT NOT NULL,
                exe_path TEXT NOT NULL,
                args TEXT,
                cwd TEXT,
                pid INTEGER,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                exit_code INTEGER,
                exit_status TEXT,
                auto_restart INTEGER NOT NULL DEFAULT 0,
                restarted_from_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def insert_run(
    *,
    tab_name: str,
    exe_path: str,
    args: str,
    cwd: str,
    pid: int,
    auto_restart: bool,
    restarted_from_id: int | None = None,
) -> int:
    current = now_text()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO process_runs
                (tab_name, exe_path, args, cwd, pid, started_at, auto_restart,
                 restarted_from_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tab_name,
                exe_path,
                args,
                cwd,
                pid,
                current,
                int(auto_restart),
                restarted_from_id,
                current,
                current,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def finish_run(run_id: int, *, exit_code: int, exit_status: str) -> None:
    current = now_text()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE process_runs
            SET ended_at = ?, exit_code = ?, exit_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (current, int(exit_code), exit_status, current, int(run_id)),
        )
        conn.commit()


@dataclass
class LaunchSettings:
    exe_path: str
    args: str
    cwd: str
    auto_restart: bool
    restart_delay_seconds: int


class ProcessTab(QWidget):
    def __init__(self, tab_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tab_name = tab_name
        self.process: QProcess | None = None
        self.current_run_id: int | None = None
        self.manual_stop = False
        self.last_finished_run_id: int | None = None

        self.exe_path = QLineEdit()
        self.exe_path.setPlaceholderText("起動するexe/cmd/batを指定")
        self.browse_button = QPushButton("参照")
        self.browse_button.clicked.connect(self.browse_exe)

        self.args = QLineEdit()
        self.args.setPlaceholderText("引数。空でも可")
        self.cwd = QLineEdit()
        self.cwd.setPlaceholderText("作業フォルダ。空なら実行ファイルのフォルダ")
        self.cwd_browse_button = QPushButton("参照")
        self.cwd_browse_button.clicked.connect(self.browse_cwd)

        self.auto_restart = QCheckBox("終了したら自動再起動")
        self.restart_delay = QSpinBox()
        self.restart_delay.setRange(0, 3600)
        self.restart_delay.setSuffix(" 秒")
        self.restart_delay.setValue(0)

        self.status = QLabel("未起動")
        self.pid_label = QLabel("PID: -")
        self.started_label = QLabel("開始: -")
        self.ended_label = QLabel("終了: -")

        self.start_button = QPushButton("起動")
        self.start_button.clicked.connect(self.start_process)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop_process)
        self.stop_button.setEnabled(False)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        exe_row = QWidget()
        exe_layout = QHBoxLayout(exe_row)
        exe_layout.setContentsMargins(0, 0, 0, 0)
        exe_layout.addWidget(self.exe_path, 1)
        exe_layout.addWidget(self.browse_button)

        cwd_row = QWidget()
        cwd_layout = QHBoxLayout(cwd_row)
        cwd_layout.setContentsMargins(0, 0, 0, 0)
        cwd_layout.addWidget(self.cwd, 1)
        cwd_layout.addWidget(self.cwd_browse_button)

        restart_row = QWidget()
        restart_layout = QHBoxLayout(restart_row)
        restart_layout.setContentsMargins(0, 0, 0, 0)
        restart_layout.addWidget(self.auto_restart)
        restart_layout.addWidget(QLabel("再起動遅延"))
        restart_layout.addWidget(self.restart_delay)
        restart_layout.addStretch(1)

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addStretch(1)

        form = QFormLayout()
        form.addRow("実行ファイル", exe_row)
        form.addRow("引数", self.args)
        form.addRow("作業フォルダ", cwd_row)
        form.addRow("再起動", restart_row)

        info_row = QWidget()
        info_layout = QHBoxLayout(info_row)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.addWidget(self.status)
        info_layout.addWidget(self.pid_label)
        info_layout.addWidget(self.started_label)
        info_layout.addWidget(self.ended_label)
        info_layout.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(button_row)
        layout.addWidget(info_row)
        layout.addWidget(self.log, 1)

    def browse_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "起動するアプリを選択",
            str(Path.home()),
            "Programs (*.exe *.cmd *.bat);;All files (*.*)",
        )
        if not path:
            return
        self.exe_path.setText(path)
        if not self.cwd.text().strip():
            self.cwd.setText(str(Path(path).resolve().parent))

    def browse_cwd(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "作業フォルダを選択", self.cwd.text().strip() or str(Path.home()))
        if path:
            self.cwd.setText(path)

    def settings(self) -> LaunchSettings:
        exe = self.exe_path.text().strip()
        cwd = self.cwd.text().strip()
        if not cwd and exe:
            cwd = str(Path(exe).resolve().parent)
        return LaunchSettings(
            exe_path=exe,
            args=self.args.text().strip(),
            cwd=cwd,
            auto_restart=self.auto_restart.isChecked(),
            restart_delay_seconds=int(self.restart_delay.value()),
        )

    def append_log(self, message: str) -> None:
        self.log.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def start_process(self, *, restarted_from_id: int | None = None) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            return
        settings = self.settings()
        if not settings.exe_path:
            self.append_log("起動できない: 実行ファイルが空")
            return
        exe = Path(settings.exe_path)
        if not exe.exists():
            self.append_log(f"起動できない: 実行ファイルが見つからない {settings.exe_path}")
            return
        cwd = Path(settings.cwd or exe.parent)
        cwd.mkdir(parents=True, exist_ok=True)

        self.manual_stop = False
        process = QProcess(self)
        process.setProgram(str(exe))
        if settings.args:
            process.setArguments(shlex.split(settings.args, posix=False))
        process.setWorkingDirectory(str(cwd))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self.read_output)
        process.started.connect(lambda: self.on_started(settings, restarted_from_id))
        process.finished.connect(self.on_finished)
        process.errorOccurred.connect(lambda error: self.append_log(f"QProcess error: {error.name}"))
        self.process = process
        self.status.setText("起動中")
        self.ended_label.setText("終了: -")
        self.append_log(f"start: {settings.exe_path} {settings.args}".rstrip())
        process.start()

    def on_started(self, settings: LaunchSettings, restarted_from_id: int | None) -> None:
        if not self.process:
            return
        pid = int(self.process.processId())
        self.current_run_id = insert_run(
            tab_name=self.tab_name,
            exe_path=settings.exe_path,
            args=settings.args,
            cwd=settings.cwd,
            pid=pid,
            auto_restart=settings.auto_restart,
            restarted_from_id=restarted_from_id,
        )
        started = now_text()
        self.status.setText("実行中")
        self.pid_label.setText(f"PID: {pid}")
        self.started_label.setText(f"開始: {started}")
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.append_log(f"started: PID {pid} / run_id {self.current_run_id}")

    def read_output(self) -> None:
        if not self.process:
            return
        data = bytes(self.process.readAllStandardOutput())
        if not data:
            return
        text = data.decode("utf-8", errors="replace").rstrip()
        if text:
            self.log.append(text)

    def stop_process(self) -> None:
        if not self.process or self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.manual_stop = True
        self.auto_restart.setChecked(False)
        self.status.setText("停止要求中")
        self.append_log("manual stop requested")
        self.process.terminate()
        QTimer.singleShot(5000, self.kill_if_running)

    def kill_if_running(self) -> None:
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.append_log("terminate timeout. kill process.")
            self.process.kill()

    def on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        ended = now_text()
        run_id = self.current_run_id
        if run_id is not None:
            finish_run(run_id, exit_code=exit_code, exit_status=exit_status.name)
            self.last_finished_run_id = run_id
        self.current_run_id = None
        self.status.setText("終了")
        self.pid_label.setText("PID: -")
        self.ended_label.setText(f"終了: {ended}")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.append_log(f"finished: exit_code={exit_code} exit_status={exit_status.name} / run_id {run_id}")

        should_restart = self.auto_restart.isChecked() and not self.manual_stop
        if should_restart:
            delay_ms = int(self.restart_delay.value()) * 1000
            self.status.setText(f"再起動待ち {self.restart_delay.value()}秒")
            QTimer.singleShot(delay_ms, lambda previous=run_id: self.start_process(restarted_from_id=previous))

    def close_tab(self) -> None:
        self.auto_restart.setChecked(False)
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.manual_stop = True
            self.process.terminate()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Process Supervisor Lab")
        self.resize(980, 720)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_process_tab)
        self.setCentralWidget(self.tabs)

        add_button = QPushButton("子プロセス追加")
        add_button.clicked.connect(self.add_process_tab)
        self.statusBar().addPermanentWidget(add_button)
        self.statusBar().showMessage(f"DB: {DB_PATH}")
        self.add_process_tab()

    def add_process_tab(self) -> None:
        number = self.tabs.count() + 1
        tab_name = f"child-{number}"
        tab = ProcessTab(tab_name)
        index = self.tabs.addTab(tab, tab_name)
        self.tabs.setCurrentIndex(index)

    def close_process_tab(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if isinstance(tab, ProcessTab):
            tab.close_tab()
        self.tabs.removeTab(index)
        if tab:
            tab.deleteLater()

    def closeEvent(self, event) -> None:
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if isinstance(tab, ProcessTab):
                tab.close_tab()
        event.accept()


def main() -> int:
    init_db()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

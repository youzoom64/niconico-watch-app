from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import tracker
from codex_exec_runner import extract_reply_json_value, run_codex_exec


ROOT = Path(__file__).resolve().parents[1]
SESSION_HISTORY_PATH = ROOT / "data" / "ai_reaction_tester_sessions.json"
SETTINGS_PATH = ROOT / "data" / "ai_reaction_tester_settings.json"


class RunSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class RunJob(QRunnable):
    def __init__(self, provider: str, system_prompt: str, prompt: str, session_id: str) -> None:
        super().__init__()
        self.provider = provider
        self.system_prompt = system_prompt
        self.prompt = prompt
        self.session_id = session_id
        self.signals = RunSignals()

    def run(self) -> None:
        combined = (
            "【システムプロンプト】\n"
            f"{self.system_prompt.strip()}\n\n"
            "【ユーザープロンプト】\n"
            f"{self.prompt.strip()}"
        )
        try:
            config = tracker.codex_exec_config()
            config = replace(
                config,
                enabled=True,
                provider=self.provider,
                command={"codex": "codex", "grok": "grok", "claude": "claude"}[self.provider],
            )
            result = run_codex_exec(
                combined,
                config=config,
                session_id=self.session_id,
            )
            self.signals.finished.emit(result)
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class ReactionTesterWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Codex", "codex")
        self.provider_combo.addItem("Grok", "grok")
        self.provider_combo.addItem("ClaudeCode", "claude")
        self.provider_combo.currentIndexChanged.connect(self.load_sessions)

        self.session_combo = QComboBox()
        self.session_combo.setEditable(True)
        self.session_combo.setPlaceholderText("空欄なら新規セッション")
        self.new_session_button = QPushButton("新規セッション")
        self.new_session_button.clicked.connect(self.session_combo.clearEditText)

        self.system_prompt = QPlainTextEdit()
        self.system_prompt.setPlaceholderText("システムプロンプト")
        default_system_prompt = (
            "ニコニコ生放送のコメントに短く反応してください。\n"
            "出力は {\"reply\":\"反応文\"} というJSONオブジェクト1個だけにしてください。"
        )
        try:
            saved_settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved_settings = {}
        self.system_prompt.setPlainText(
            str(saved_settings.get("system_prompt") or default_system_prompt)
        )
        self.system_prompt.textChanged.connect(self.save_system_prompt)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("試したいコメントと状況を入力")

        self.run_button = QPushButton("Codexで実行")
        self.run_button.clicked.connect(self.run_prompt)
        self.status = QLabel("待機中")

        self.reply_output = QPlainTextEdit()
        self.reply_output.setReadOnly(True)
        self.reply_output.setPlaceholderText("抽出されたreply")
        self.raw_output = QPlainTextEdit()
        self.raw_output.setReadOnly(True)
        self.raw_output.setPlaceholderText("Codexの生出力")

        session_row = QWidget()
        session_layout = QHBoxLayout(session_row)
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.addWidget(QLabel("AI担当"))
        session_layout.addWidget(self.provider_combo)
        session_layout.addWidget(QLabel("resumeセッションID"))
        session_layout.addWidget(self.session_combo, 1)
        session_layout.addWidget(self.new_session_button)

        inputs = QSplitter()
        system_box = QWidget()
        system_layout = QVBoxLayout(system_box)
        system_layout.addWidget(QLabel("システムプロンプト"))
        system_layout.addWidget(self.system_prompt)
        prompt_box = QWidget()
        prompt_layout = QVBoxLayout(prompt_box)
        prompt_layout.addWidget(QLabel("プロンプト"))
        prompt_layout.addWidget(self.prompt)
        inputs.addWidget(system_box)
        inputs.addWidget(prompt_box)
        inputs.setSizes([500, 500])

        outputs = QSplitter()
        reply_box = QWidget()
        reply_layout = QVBoxLayout(reply_box)
        reply_layout.addWidget(QLabel("reply"))
        reply_layout.addWidget(self.reply_output)
        raw_box = QWidget()
        raw_layout = QVBoxLayout(raw_box)
        raw_layout.addWidget(QLabel("生出力"))
        raw_layout.addWidget(self.raw_output)
        outputs.addWidget(reply_box)
        outputs.addWidget(raw_box)
        outputs.setSizes([400, 600])

        layout = QVBoxLayout(self)
        layout.addWidget(session_row)
        layout.addWidget(inputs, 1)
        layout.addWidget(self.run_button)
        layout.addWidget(self.status)
        layout.addWidget(outputs, 1)
        self.load_sessions()

    def load_sessions(self) -> None:
        try:
            values = json.loads(SESSION_HISTORY_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            values = {}
        if isinstance(values, list):
            values = {"codex": values}
        provider = str(self.provider_combo.currentData() or "codex")
        provider_values = values.get(provider, []) if isinstance(values, dict) else []
        self.session_combo.clear()
        self.session_combo.addItems([str(value) for value in provider_values if str(value).strip()])
        self.session_combo.setCurrentIndex(-1)

    def save_system_prompt(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(
                {"system_prompt": self.system_prompt.toPlainText()},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def remember_session(self, session_id: str) -> None:
        session_id = session_id.strip()
        if not session_id:
            return
        provider = str(self.provider_combo.currentData() or "codex")
        try:
            history = json.loads(SESSION_HISTORY_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            history = {}
        if isinstance(history, list):
            history = {"codex": history}
        values = list(history.get(provider, [])) if isinstance(history, dict) else []
        values = [session_id, *[str(value) for value in values if str(value) != session_id]][:50]
        history[provider] = values
        SESSION_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SESSION_HISTORY_PATH.write_text(
            json.dumps(history, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.session_combo.clear()
        self.session_combo.addItems(values)
        self.session_combo.setCurrentText(session_id)

    def run_prompt(self) -> None:
        self.save_system_prompt()
        if not self.prompt.toPlainText().strip():
            QMessageBox.warning(self, "入力不足", "プロンプトを入力してください。")
            return
        self.run_button.setEnabled(False)
        self.status.setText("Codex実行中...")
        self.reply_output.clear()
        self.raw_output.clear()
        job = RunJob(
            str(self.provider_combo.currentData() or "codex"),
            self.system_prompt.toPlainText(),
            self.prompt.toPlainText(),
            self.session_combo.currentText().strip(),
        )
        job.signals.finished.connect(self.on_finished)
        job.signals.failed.connect(self.on_failed)
        QThreadPool.globalInstance().start(job)

    def on_finished(self, result: object) -> None:
        self.run_button.setEnabled(True)
        text = str(getattr(result, "text", "") or "")
        session_id = str(getattr(result, "session_id", "") or "")
        self.raw_output.setPlainText(text)
        self.reply_output.setPlainText(extract_reply_json_value(text))
        self.remember_session(session_id)
        self.status.setText(
            f"完了 / session: {session_id or '取得なし'} / returncode: {getattr(result, 'returncode', '')}"
        )

    def on_failed(self, error: str) -> None:
        self.run_button.setEnabled(True)
        self.status.setText(f"失敗: {error}")


def main() -> int:
    app = QApplication(sys.argv)
    window = QMainWindow()
    window.setWindowTitle("AI反応プロンプトテスター")
    window.resize(1000, 760)
    window.setCentralWidget(ReactionTesterWidget())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

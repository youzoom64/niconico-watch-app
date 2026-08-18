"""アーカイブ動画と生成物を一覧して片付ける窓。

保存先に何がどれだけ積まれているかを実ファイルから出し、放送単位で
「動画だけ」「中間音声だけ」といった消し方ができるようにする。
tracker.db は参照しない（[[archive_library]] と同じ理由）。
"""

from __future__ import annotations

import traceback
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import archive_library
import storage_paths


# 放送1件を、消す単位でまとめた列。削除ボタンもこの並びに合わせる。
BROADCAST_SIZE_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("動画", ("video",)),
    ("中間音声", ("segment_audio",)),
    ("音声", ("audio",)),
    ("画像", ("thumbnail", "image")),
    ("HTML", ("page", "data")),
    ("作業", ("work",)),
    ("その他", ("other",)),
)


def log_app(message: str, level: str = "INFO") -> None:
    """本体のログへ流す。単体で開いたときは黙って捨てる。

    gui_app からこの窓を開くので、逆向きの取り込みを常設すると循環する。
    """
    try:
        from gui_app import append_app_log
    except Exception:
        return
    append_app_log(message, level)


def format_size(size: int) -> str:
    return storage_paths.format_bytes(int(size or 0))


def format_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime) else ""


def format_mtime(value: float) -> str:
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def open_in_explorer(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))


# --- 走査と削除のスレッド -------------------------------------------


class ScanWorker(QThread):
    progressed = pyqtSignal(str)
    finished_with_result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, platform_root: Path, recording_root: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.platform_root = Path(platform_root)
        self.recording_root = Path(recording_root)
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            inventory = archive_library.build_inventory(
                platform_root=self.platform_root,
                recording_root=self.recording_root,
                on_progress=self.progressed.emit,
                should_cancel=lambda: self._cancel_requested,
            )
        except Exception as exc:
            log_app(traceback.format_exc(), "ERROR")
            self.failed.emit(str(exc))
            return
        self.finished_with_result.emit(inventory)


class DeleteWorker(QThread):
    progressed = pyqtSignal(str)
    finished_with_result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        paths: Sequence[Path],
        *,
        use_recycle_bin: bool,
        prune_roots: Sequence[Path] = (),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.paths = [Path(path) for path in paths]
        self.use_recycle_bin = bool(use_recycle_bin)
        self.prune_roots = [Path(path) for path in prune_roots]

    def run(self) -> None:
        try:
            result = archive_library.delete_paths(
                self.paths,
                use_recycle_bin=self.use_recycle_bin,
                on_progress=self.progressed.emit,
            )
            for root in self.prune_roots:
                archive_library.prune_empty_directories(root)
        except Exception as exc:
            log_app(traceback.format_exc(), "ERROR")
            self.failed.emit(str(exc))
            return
        self.finished_with_result.emit(result)


# --- モデル ---------------------------------------------------------


class SortableTableModel(QAbstractTableModel):
    """列ごとに (表示文字列, 並べ替えキー) を返す実装を持たせる土台。"""

    headers: list[str] = []

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Any] = []
        self.sort_column = 0
        self.sort_order = Qt.SortOrder.AscendingOrder

    def display_value(self, row: Any, column: int) -> str:
        raise NotImplementedError

    def sort_value(self, row: Any, column: int) -> Any:
        return self.display_value(row, column)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= len(self.rows):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.display_value(self.rows[index.row()], index.column())
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.display_value(self.rows[index.row()], index.column())
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and section < len(self.headers):
            return self.headers[section]
        return section + 1

    def set_rows(self, rows: list[Any]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()
        self.sort(self.sort_column, self.sort_order)

    def row_at(self, row: int) -> Any | None:
        if 0 <= row < len(self.rows):
            return self.rows[row]
        return None

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        if column < 0 or column >= len(self.headers):
            return
        self.sort_column = column
        self.sort_order = order
        self.layoutAboutToBeChanged.emit()
        self.rows.sort(
            key=lambda row: _sort_key(self.sort_value(row, column)),
            reverse=order == Qt.SortOrder.DescendingOrder,
        )
        self.layoutChanged.emit()


def _sort_key(value: Any) -> tuple[int, float, str]:
    """型が混ざる列でも落ちない並べ替えキー。空欄は常に後ろへ寄せる。"""
    if value is None or value == "":
        return (2, 0.0, "")
    if isinstance(value, bool):
        return (0, float(value), "")
    if isinstance(value, (int, float)):
        return (0, float(value), "")
    if isinstance(value, datetime):
        return (0, value.timestamp(), "")
    return (1, 0.0, str(value).casefold())


class BroadcastModel(SortableTableModel):
    headers = ["配信者", "lv", "タイトル", "開始", *[label for label, _ in BROADCAST_SIZE_COLUMNS], "合計"]

    def __init__(self) -> None:
        super().__init__()
        self.sort_column = len(self.headers) - 1
        self.sort_order = Qt.SortOrder.DescendingOrder

    def size_column_index(self, column: int) -> int:
        return column - 4

    def display_value(self, row: archive_library.BroadcastEntry, column: int) -> str:
        if column == 0:
            return row.broadcaster_label
        if column == 1:
            return row.lv
        if column == 2:
            return row.title
        if column == 3:
            return format_time(row.started_at)
        if column == len(self.headers) - 1:
            return format_size(row.total_size)
        size = self.category_size(row, column)
        return format_size(size) if size else ""

    def sort_value(self, row: archive_library.BroadcastEntry, column: int) -> Any:
        if column == 3:
            return row.started_at
        if column == len(self.headers) - 1:
            return row.total_size
        if column >= 4:
            return self.category_size(row, column)
        return self.display_value(row, column)

    def category_size(self, row: archive_library.BroadcastEntry, column: int) -> int:
        index = self.size_column_index(column)
        if not 0 <= index < len(BROADCAST_SIZE_COLUMNS):
            return 0
        return sum(row.size_of(category) for category in BROADCAST_SIZE_COLUMNS[index][1])


class RecordingModel(SortableTableModel):
    headers = ["配信者", "lv", "タイトル", "開始", "本数", "サイズ", "生成物"]

    def __init__(self) -> None:
        super().__init__()
        self.sort_column = 5
        self.sort_order = Qt.SortOrder.DescendingOrder

    def display_value(self, row: archive_library.RecordingEntry, column: int) -> str:
        if column == 0:
            return row.broadcaster_label
        if column == 1:
            return row.lv or "(lv不明)"
        if column == 2:
            return row.title
        if column == 3:
            return format_time(row.started_at)
        if column == 4:
            return str(len(row.files))
        if column == 5:
            return format_size(row.total_size)
        if column == 6:
            return "あり" if row.generated else ""
        return ""

    def sort_value(self, row: archive_library.RecordingEntry, column: int) -> Any:
        if column == 3:
            return row.started_at
        if column == 4:
            return len(row.files)
        if column == 5:
            return row.total_size
        if column == 6:
            return row.generated
        return self.display_value(row, column)


class FileModel(SortableTableModel):
    headers = ["種別", "ファイル", "サイズ", "更新"]

    def __init__(self) -> None:
        super().__init__()
        self.sort_column = 2
        self.sort_order = Qt.SortOrder.DescendingOrder

    def display_value(self, row: archive_library.ArchiveFile, column: int) -> str:
        if column == 0:
            return row.category_label
        if column == 1:
            return row.relative
        if column == 2:
            return format_size(row.size)
        if column == 3:
            return format_mtime(row.mtime)
        return ""

    def sort_value(self, row: archive_library.ArchiveFile, column: int) -> Any:
        if column == 2:
            return row.size
        if column == 3:
            return row.mtime
        return self.display_value(row, column)


def make_table(model: QAbstractTableModel, widths: Sequence[int]) -> QTableView:
    table = QTableView()
    table.setModel(model)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(True)
    for index, width in enumerate(widths):
        table.setColumnWidth(index, width)
    # 並べ替えを有効にすると0列目で並べ直されるので、モデルの既定へ戻す。
    table.setSortingEnabled(True)
    table.sortByColumn(
        int(getattr(model, "sort_column", 0)),
        getattr(model, "sort_order", Qt.SortOrder.AscendingOrder),
    )
    return table


# --- 窓 -------------------------------------------------------------


class ArchiveManagerWindow(QMainWindow):
    """アーカイブ動画と生成物の管理窓。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("アーカイブ管理")
        self.resize(1280, 800)
        self.inventory: archive_library.Inventory | None = None
        self.scan_worker: ScanWorker | None = None
        self.delete_worker: DeleteWorker | None = None
        self.pending_directories: list[Path] = []

        self.platform_root_label = QLabel("")
        self.platform_root_label.setWordWrap(True)
        self.recording_root_label = QLabel("")
        self.recording_root_label.setWordWrap(True)
        self.rescan_button = QPushButton("再スキャン")
        self.rescan_button.clicked.connect(self.start_scan)
        self.open_platform_button = QPushButton("生成物フォルダを開く")
        self.open_platform_button.clicked.connect(
            lambda: open_in_explorer(storage_paths.platform_root_of(storage_paths.read_target_root()))
        )
        self.open_recording_button = QPushButton("録画フォルダを開く")
        self.open_recording_button.clicked.connect(
            lambda: open_in_explorer(storage_paths.read_recording_root())
        )
        self.recycle_bin_checkbox = QCheckBox("ごみ箱に入れる")
        self.recycle_bin_checkbox.setChecked(archive_library.recycle_bin_available())
        self.recycle_bin_checkbox.setEnabled(archive_library.recycle_bin_available())
        self.recycle_bin_checkbox.setToolTip(
            "チェックを外すと完全削除します。ごみ箱経由では、空にするまで空き容量は戻りません"
        )
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.addWidget(self.platform_root_label)
        header_layout.addWidget(self.recording_root_label)
        header_buttons = QHBoxLayout()
        header_buttons.addWidget(self.rescan_button)
        header_buttons.addWidget(self.open_platform_button)
        header_buttons.addWidget(self.open_recording_button)
        header_buttons.addWidget(self.recycle_bin_checkbox)
        header_buttons.addStretch(1)
        header_layout.addLayout(header_buttons)
        header_layout.addWidget(self.progress)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_broadcast_tab(), "生成物")
        self.tabs.addTab(self.build_recording_tab(), "録画フォルダ")

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.addWidget(header)
        central_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        self.refresh_root_labels()
        self.start_scan()

    # --- 画面組み立て ---

    def build_broadcast_tab(self) -> QWidget:
        self.broadcast_search = QLineEdit()
        self.broadcast_search.setPlaceholderText("lv・タイトル・配信者で絞り込み")
        self.broadcast_search.textChanged.connect(self.apply_broadcast_filter)
        self.broadcaster_filter = QComboBox()
        self.broadcaster_filter.addItem("すべての配信者", "")
        self.broadcaster_filter.currentIndexChanged.connect(self.apply_broadcast_filter)
        self.video_only_checkbox = QCheckBox("動画が残っているものだけ")
        self.video_only_checkbox.toggled.connect(self.apply_broadcast_filter)

        filter_row = QHBoxLayout()
        filter_row.addWidget(self.broadcast_search, 1)
        filter_row.addWidget(self.broadcaster_filter)
        filter_row.addWidget(self.video_only_checkbox)

        self.broadcast_model = BroadcastModel()
        self.broadcast_table = make_table(
            self.broadcast_model, [180, 110, 320, 130, 90, 90, 90, 90, 90, 90, 90, 100]
        )
        self.broadcast_table.selectionModel().selectionChanged.connect(self.on_broadcast_selected)
        self.broadcast_table.doubleClicked.connect(self.open_selected_broadcast_folder)

        self.file_model = FileModel()
        self.file_table = make_table(self.file_model, [110, 420, 100, 140])
        self.file_table.doubleClicked.connect(self.open_selected_file)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.broadcast_table)
        splitter.addWidget(self.file_table)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.broadcast_summary = QLabel("スキャン中です。")
        self.broadcast_summary.setWordWrap(True)

        self.delete_video_button = QPushButton("動画を削除")
        self.delete_video_button.setToolTip("選んだ放送の mp4/ts などの動画だけ消します")
        self.delete_video_button.clicked.connect(lambda: self.delete_broadcast_categories(("video",)))
        self.delete_segment_audio_button = QPushButton("中間音声を削除")
        self.delete_segment_audio_button.setToolTip(
            "文字起こしに使った recording_segments の wav/mp3 を消します。完成音声は残します"
        )
        self.delete_segment_audio_button.clicked.connect(
            lambda: self.delete_broadcast_categories(("segment_audio",))
        )
        self.delete_work_button = QPushButton("作業ファイルを削除")
        self.delete_work_button.setToolTip("結合作業の残骸や .bak を消します")
        self.delete_work_button.clicked.connect(lambda: self.delete_broadcast_categories(("work",)))
        self.delete_broadcast_button = QPushButton("放送ごと削除")
        self.delete_broadcast_button.setToolTip("選んだ放送のディレクトリを丸ごと消します")
        self.delete_broadcast_button.clicked.connect(self.delete_selected_broadcasts)
        self.delete_files_button = QPushButton("下の表で選んだファイルを削除")
        self.delete_files_button.clicked.connect(self.delete_selected_files)
        self.open_html_button = QPushButton("HTMLを開く")
        self.open_html_button.clicked.connect(self.open_selected_html)
        self.open_folder_button = QPushButton("フォルダを開く")
        self.open_folder_button.clicked.connect(self.open_selected_broadcast_folder)

        action_row = QHBoxLayout()
        action_row.addWidget(self.open_folder_button)
        action_row.addWidget(self.open_html_button)
        action_row.addStretch(1)
        action_row.addWidget(self.delete_video_button)
        action_row.addWidget(self.delete_segment_audio_button)
        action_row.addWidget(self.delete_work_button)
        action_row.addWidget(self.delete_files_button)
        action_row.addWidget(self.delete_broadcast_button)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addLayout(filter_row)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.broadcast_summary)
        layout.addLayout(action_row)
        return tab

    def build_recording_tab(self) -> QWidget:
        self.recording_search = QLineEdit()
        self.recording_search.setPlaceholderText("lv・タイトル・配信者で絞り込み")
        self.recording_search.textChanged.connect(self.apply_recording_filter)
        self.generated_only_checkbox = QCheckBox("生成物ができているものだけ")
        self.generated_only_checkbox.setToolTip(
            "同じlvのHTMLが生成物側にある録画だけを出します。消しても作り直せない放送を隠すための絞り込みです"
        )
        self.generated_only_checkbox.toggled.connect(self.apply_recording_filter)

        filter_row = QHBoxLayout()
        filter_row.addWidget(self.recording_search, 1)
        filter_row.addWidget(self.generated_only_checkbox)

        self.recording_model = RecordingModel()
        self.recording_table = make_table(self.recording_model, [200, 110, 340, 130, 70, 100, 80])
        self.recording_table.selectionModel().selectionChanged.connect(self.on_recording_selected)
        self.recording_table.doubleClicked.connect(self.open_selected_recording_folder)

        self.recording_file_model = FileModel()
        self.recording_file_table = make_table(self.recording_file_model, [110, 420, 100, 140])
        self.recording_file_table.doubleClicked.connect(self.open_selected_recording_file)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.recording_table)
        splitter.addWidget(self.recording_file_table)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.recording_summary = QLabel("スキャン中です。")
        self.recording_summary.setWordWrap(True)

        self.open_recording_folder_button = QPushButton("フォルダを開く")
        self.open_recording_folder_button.clicked.connect(self.open_selected_recording_folder)
        self.delete_recording_button = QPushButton("選んだ録画を削除")
        self.delete_recording_button.setToolTip("選んだ行の録画ファイルを消します")
        self.delete_recording_button.clicked.connect(self.delete_selected_recordings)
        self.delete_recording_files_button = QPushButton("下の表で選んだファイルを削除")
        self.delete_recording_files_button.clicked.connect(self.delete_selected_recording_files)

        action_row = QHBoxLayout()
        action_row.addWidget(self.open_recording_folder_button)
        action_row.addStretch(1)
        action_row.addWidget(self.delete_recording_files_button)
        action_row.addWidget(self.delete_recording_button)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addLayout(filter_row)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.recording_summary)
        layout.addLayout(action_row)
        return tab

    # --- スキャン ---

    def refresh_root_labels(self) -> None:
        try:
            target_root = storage_paths.read_target_root()
            platform_root = storage_paths.platform_root_of(target_root)
            recording_root = storage_paths.read_recording_root()
        except Exception as exc:
            self.platform_root_label.setText(f"保存先を読めません: {exc}")
            return
        self.platform_root_label.setText(f"生成物: {platform_root}　{self.describe_free(platform_root)}")
        self.recording_root_label.setText(f"録画: {recording_root}　{self.describe_free(recording_root)}")

    def describe_free(self, path: Path) -> str:
        try:
            status = storage_paths.describe_root(path)
        except Exception:
            return ""
        return (
            f"[ドライブ {status.drive or '不明'} 空き {format_size(status.free_bytes)}"
            f" / 全体 {format_size(status.total_bytes)}]"
        )

    def start_scan(self) -> None:
        if self.scan_worker is not None and self.scan_worker.isRunning():
            return
        if self.delete_worker is not None and self.delete_worker.isRunning():
            self.statusBar().showMessage("削除中はスキャンできません。")
            return
        self.refresh_root_labels()
        try:
            platform_root = storage_paths.platform_root_of(storage_paths.read_target_root())
            recording_root = storage_paths.read_recording_root()
        except Exception as exc:
            self.statusBar().showMessage(f"保存先を読めません: {exc}")
            return
        self.set_busy(True, "スキャン中です。")
        self.scan_worker = ScanWorker(platform_root, recording_root, self)
        self.scan_worker.progressed.connect(self.statusBar().showMessage)
        self.scan_worker.finished_with_result.connect(self.on_scan_finished)
        self.scan_worker.failed.connect(self.on_scan_failed)
        self.scan_worker.start()

    def on_scan_failed(self, message: str) -> None:
        self.set_busy(False)
        self.statusBar().showMessage(f"スキャンに失敗しました: {message}")

    def on_scan_finished(self, inventory: object) -> None:
        self.set_busy(False)
        if not isinstance(inventory, archive_library.Inventory):
            return
        self.inventory = inventory
        self.reload_broadcaster_filter()
        self.apply_broadcast_filter()
        self.apply_recording_filter()
        self.statusBar().showMessage(
            f"生成物 {len(inventory.broadcasts)} 放送 / {format_size(inventory.broadcast_total_size)}　"
            f"録画 {len(inventory.recordings)} 件 / {format_size(inventory.recording_total_size)}"
        )

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.progress.setVisible(busy)
        for widget in (
            self.rescan_button,
            self.delete_video_button,
            self.delete_segment_audio_button,
            self.delete_work_button,
            self.delete_broadcast_button,
            self.delete_files_button,
            self.delete_recording_button,
            self.delete_recording_files_button,
        ):
            widget.setEnabled(not busy)
        if message:
            self.statusBar().showMessage(message)

    # --- 生成物タブ ---

    def reload_broadcaster_filter(self) -> None:
        current = self.broadcaster_filter.currentData()
        labels: dict[str, str] = {}
        for entry in self.inventory.broadcasts if self.inventory else []:
            labels.setdefault(entry.broadcaster_id, entry.broadcaster_label)
        self.broadcaster_filter.blockSignals(True)
        self.broadcaster_filter.clear()
        self.broadcaster_filter.addItem("すべての配信者", "")
        for broadcaster_id, label in sorted(labels.items(), key=lambda item: item[1].casefold()):
            self.broadcaster_filter.addItem(label, broadcaster_id)
        index = self.broadcaster_filter.findData(current)
        self.broadcaster_filter.setCurrentIndex(max(index, 0))
        self.broadcaster_filter.blockSignals(False)

    def apply_broadcast_filter(self) -> None:
        entries = list(self.inventory.broadcasts) if self.inventory else []
        query = self.broadcast_search.text().strip().casefold()
        broadcaster_id = str(self.broadcaster_filter.currentData() or "")
        if query:
            entries = [entry for entry in entries if query in entry.search_text()]
        if broadcaster_id:
            entries = [entry for entry in entries if entry.broadcaster_id == broadcaster_id]
        if self.video_only_checkbox.isChecked():
            entries = [entry for entry in entries if entry.size_of("video") > 0]
        self.broadcast_model.set_rows(entries)
        self.file_model.set_rows([])
        self.update_broadcast_summary(entries)

    def update_broadcast_summary(self, entries: Sequence[archive_library.BroadcastEntry]) -> None:
        selected = self.selected_broadcasts()
        shown_total = sum(entry.total_size for entry in entries)
        parts = [f"表示中 {len(entries)} 放送 / {format_size(shown_total)}"]
        if selected:
            per_category = [
                f"{label} {format_size(sum(sum(entry.size_of(key) for key in keys) for entry in selected))}"
                for label, keys in BROADCAST_SIZE_COLUMNS
                if sum(sum(entry.size_of(key) for key in keys) for entry in selected) > 0
            ]
            parts.append(
                f"選択 {len(selected)} 放送 / {format_size(sum(entry.total_size for entry in selected))}"
            )
            if per_category:
                parts.append("　".join(per_category))
        self.broadcast_summary.setText("　|　".join(parts))

    def selected_broadcasts(self) -> list[archive_library.BroadcastEntry]:
        rows = {index.row() for index in self.broadcast_table.selectionModel().selectedRows()}
        entries = [self.broadcast_model.row_at(row) for row in sorted(rows)]
        return [entry for entry in entries if entry is not None]

    def on_broadcast_selected(self) -> None:
        selected = self.selected_broadcasts()
        files: list[archive_library.ArchiveFile] = []
        for entry in selected:
            files.extend(entry.files)
        self.file_model.set_rows(files)
        self.update_broadcast_summary(self.broadcast_model.rows)

    def open_selected_broadcast_folder(self) -> None:
        for entry in self.selected_broadcasts()[:5]:
            open_in_explorer(entry.directory)

    def open_selected_html(self) -> None:
        for entry in self.selected_broadcasts()[:5]:
            pages = [
                item
                for item in entry.files
                if item.path.suffix.casefold() == ".html"
                and not item.path.stem.casefold().endswith("_mobile")
                and item.path.name.casefold().startswith(f"{entry.lv.casefold()}_")
            ]
            if not pages:
                pages = [item for item in entry.files if item.path.suffix.casefold() == ".html"]
            if pages:
                open_in_explorer(max(pages, key=lambda item: item.size).path)
            else:
                self.statusBar().showMessage(f"{entry.lv}: HTMLがありません。")

    def open_selected_file(self) -> None:
        for index in self.file_table.selectionModel().selectedRows()[:5]:
            item = self.file_model.row_at(index.row())
            if item is not None:
                open_in_explorer(item.path)

    def delete_broadcast_categories(self, categories: Iterable[str]) -> None:
        entries = self.selected_broadcasts()
        if not entries:
            self.statusBar().showMessage("放送を選んでください。")
            return
        categories = tuple(categories)
        paths = [path for entry in entries for path in entry.paths_in(categories)]
        label = "・".join(archive_library.CATEGORY_LABELS.get(key, key) for key in categories)
        self.confirm_and_delete(
            paths,
            title=f"{label}を削除",
            description=f"{len(entries)} 放送の{label}",
            prune_roots=[entry.directory for entry in entries],
            affected_directories=[entry.directory for entry in entries],
        )

    def delete_selected_broadcasts(self) -> None:
        entries = self.selected_broadcasts()
        if not entries:
            self.statusBar().showMessage("放送を選んでください。")
            return
        self.confirm_and_delete(
            [entry.directory for entry in entries],
            title="放送ごと削除",
            description=f"{len(entries)} 放送のディレクトリ",
            affected_directories=[entry.directory for entry in entries],
        )

    def delete_selected_files(self) -> None:
        items = [
            self.file_model.row_at(index.row())
            for index in self.file_table.selectionModel().selectedRows()
        ]
        paths = [item.path for item in items if item is not None]
        if not paths:
            self.statusBar().showMessage("ファイルを選んでください。")
            return
        directories = [entry.directory for entry in self.selected_broadcasts()]
        self.confirm_and_delete(
            paths,
            title="ファイルを削除",
            description=f"{len(paths)} ファイル",
            prune_roots=directories,
            affected_directories=directories,
        )

    # --- 録画タブ ---

    def apply_recording_filter(self) -> None:
        entries = list(self.inventory.recordings) if self.inventory else []
        query = self.recording_search.text().strip().casefold()
        if query:
            entries = [entry for entry in entries if query in entry.search_text()]
        if self.generated_only_checkbox.isChecked():
            entries = [entry for entry in entries if entry.generated]
        self.recording_model.set_rows(entries)
        self.recording_file_model.set_rows([])
        self.update_recording_summary(entries)

    def update_recording_summary(self, entries: Sequence[archive_library.RecordingEntry]) -> None:
        selected = self.selected_recordings()
        parts = [
            f"表示中 {len(entries)} 件 / {format_size(sum(entry.total_size for entry in entries))}"
        ]
        if selected:
            parts.append(
                f"選択 {len(selected)} 件 / {format_size(sum(entry.total_size for entry in selected))}"
            )
            without_generated = [entry for entry in selected if not entry.generated]
            if without_generated:
                parts.append(f"うち生成物なし {len(without_generated)} 件")
        self.recording_summary.setText("　|　".join(parts))

    def selected_recordings(self) -> list[archive_library.RecordingEntry]:
        rows = {index.row() for index in self.recording_table.selectionModel().selectedRows()}
        entries = [self.recording_model.row_at(row) for row in sorted(rows)]
        return [entry for entry in entries if entry is not None]

    def on_recording_selected(self) -> None:
        files: list[archive_library.ArchiveFile] = []
        for entry in self.selected_recordings():
            files.extend(entry.files)
        self.recording_file_model.set_rows(files)
        self.update_recording_summary(self.recording_model.rows)

    def open_selected_recording_folder(self) -> None:
        for entry in self.selected_recordings()[:5]:
            open_in_explorer(entry.directory)

    def open_selected_recording_file(self) -> None:
        for index in self.recording_file_table.selectionModel().selectedRows()[:5]:
            item = self.recording_file_model.row_at(index.row())
            if item is not None:
                open_in_explorer(item.path)

    def delete_selected_recordings(self) -> None:
        entries = self.selected_recordings()
        if not entries:
            self.statusBar().showMessage("録画を選んでください。")
            return
        paths = [path for entry in entries for path in entry.paths()]
        without_generated = [entry for entry in entries if not entry.generated]
        warning = ""
        if without_generated:
            warning = (
                f"\n※ {len(without_generated)} 件は生成物がまだありません。"
                "消すとHTMLも文字起こしも作れなくなります。"
            )
        self.confirm_and_delete(
            paths,
            title="録画を削除",
            description=f"{len(entries)} 件の録画ファイル",
            extra_warning=warning,
        )

    def delete_selected_recording_files(self) -> None:
        items = [
            self.recording_file_model.row_at(index.row())
            for index in self.recording_file_table.selectionModel().selectedRows()
        ]
        paths = [item.path for item in items if item is not None]
        if not paths:
            self.statusBar().showMessage("ファイルを選んでください。")
            return
        self.confirm_and_delete(paths, title="録画ファイルを削除", description=f"{len(paths)} ファイル")

    # --- 削除 ---

    def confirm_and_delete(
        self,
        paths: Sequence[Path],
        *,
        title: str,
        description: str,
        prune_roots: Sequence[Path] = (),
        affected_directories: Sequence[Path] = (),
        extra_warning: str = "",
    ) -> None:
        if self.delete_worker is not None and self.delete_worker.isRunning():
            self.statusBar().showMessage("削除中です。終わるまで待ってください。")
            return
        paths = [Path(path) for path in paths]
        if not paths:
            self.statusBar().showMessage("消せるものがありません。")
            return
        total = sum(archive_library.path_size(path) for path in paths)
        use_recycle_bin = self.recycle_bin_checkbox.isChecked()
        destination = (
            "ごみ箱へ移します。空き容量はごみ箱を空にするまで戻りません。"
            if use_recycle_bin
            else "完全に削除します。元へは戻せません。"
        )
        answer = QMessageBox.question(
            self,
            title,
            (
                f"{description}（{len(paths)} 項目 / {format_size(total)}）を消します。\n"
                f"{destination}{extra_warning}\n\n"
                "録画中・処理中のファイルは消せません。実行しますか。"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.pending_directories = [Path(path) for path in affected_directories]
        self.set_busy(True, "削除中です。")
        self.delete_worker = DeleteWorker(
            paths,
            use_recycle_bin=use_recycle_bin,
            prune_roots=prune_roots,
            parent=self,
        )
        self.delete_worker.progressed.connect(self.statusBar().showMessage)
        self.delete_worker.finished_with_result.connect(self.on_delete_finished)
        self.delete_worker.failed.connect(self.on_delete_failed)
        self.delete_worker.start()

    def on_delete_failed(self, message: str) -> None:
        self.set_busy(False)
        self.statusBar().showMessage(f"削除に失敗しました: {message}")

    def on_delete_finished(self, result: object) -> None:
        self.set_busy(False)
        if not isinstance(result, archive_library.DeleteResult):
            return
        summary = (
            f"削除 {len(result.deleted)} 項目 / {format_size(result.deleted_bytes)}"
            f"（{'ごみ箱' if result.recycled else '完全削除'}）"
        )
        if result.failed:
            summary += f"　失敗 {len(result.failed)} 項目"
        self.statusBar().showMessage(summary)
        log_app(f"アーカイブ管理: {summary}", "INFO")
        if result.failed:
            detail = "\n".join(f"{path}: {error}" for path, error in result.failed[:20])
            QMessageBox.warning(
                self,
                "消せなかったものがあります",
                f"{len(result.failed)} 項目を消せませんでした。\n\n{detail}",
            )
        self.refresh_after_delete()

    def refresh_after_delete(self) -> None:
        """消したぶんだけ数え直す。全体の再スキャンはかけない。"""
        self.refresh_root_labels()
        if self.inventory is None:
            return
        directories = {str(path) for path in self.pending_directories}
        refreshed: list[archive_library.BroadcastEntry] = []
        for entry in self.inventory.broadcasts:
            if str(entry.directory) not in directories:
                refreshed.append(entry)
                continue
            if not entry.directory.is_dir():
                continue
            refreshed.append(
                archive_library.scan_broadcast_directory(entry.directory, entry.broadcaster_id)
            )
        self.inventory.broadcasts = refreshed

        for entry in self.inventory.recordings:
            entry.files = [item for item in entry.files if item.path.exists()]
            entry.total_size = sum(item.size for item in entry.files)
        self.inventory.recordings = [entry for entry in self.inventory.recordings if entry.files]

        self.reload_broadcaster_filter()
        self.apply_broadcast_filter()
        self.apply_recording_filter()

    # --- 後始末 ---

    def closeEvent(self, event) -> None:
        if self.delete_worker is not None and self.delete_worker.isRunning():
            QMessageBox.warning(self, "削除中", "削除が終わるまで閉じられません。")
            event.ignore()
            return
        if self.scan_worker is not None and self.scan_worker.isRunning():
            self.scan_worker.request_cancel()
            self.scan_worker.wait(5000)
        super().closeEvent(event)

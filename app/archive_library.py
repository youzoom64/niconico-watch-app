"""アーカイブ動画と生成物をファイルシステムから数え上げる。

tracker.db は開かない。放送の情報は放送ディレクトリ名と ``lv*_data.json``、
録画ファイル名から取る。取り出したいのは「どこに何GB積まれているか」なので、
DBの状態ではなく実ファイルが唯一の根拠になる。

保存先は2系統ある。
- 生成物: ``target_root/platform/niconico/<配信者ID>/broadcast/<lv>/``
- 録画: SlNicoLiveRec の StorageLocation 配下 ``<配信者ID>_<名前>/<lv>_<日時>_<題名>.ts``
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePath

import tracker


# --- 分類 -----------------------------------------------------------

CATEGORY_LABELS: dict[str, str] = {
    "video": "動画",
    "segment_audio": "中間音声",
    "audio": "音声",
    "thumbnail": "サムネイル",
    "image": "画像",
    "page": "HTML",
    "data": "データ",
    "work": "作業ファイル",
    "other": "その他",
}
CATEGORY_ORDER: tuple[str, ...] = tuple(CATEGORY_LABELS)

VIDEO_SUFFIXES = {".mp4", ".ts", ".mkv", ".webm", ".flv", ".m4v", ".avi", ".mov"}
SEGMENT_AUDIO_SUFFIXES = {".wav"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".opus", ".flac", ".ogg"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PAGE_SUFFIXES = {".html", ".htm", ".js", ".css"}
DATA_SUFFIXES = {".json", ".txt", ".csv", ".xml", ".srt", ".vtt", ".nicojk"}
WORK_SUFFIXES = {".bak", ".tmp", ".part", ".partial", ".log"}

WORK_DIR_NAMES = {"_audio_concat_work", "_concat_work", "_interval_transcription"}
SEGMENT_DIR_NAME = "recording_segments"
SCREENSHOT_DIR_NAME = "screenshot"
ARCHIVE_DIR_NAME = "archive"

MEDIA_SUFFIXES = VIDEO_SUFFIXES | SEGMENT_AUDIO_SUFFIXES | AUDIO_SUFFIXES


def classify(relative_path: PurePath) -> str:
    """放送ディレクトリからの相対パスを表示用のカテゴリへ振り分ける。

    作業用ディレクトリと中断ファイルを先に見る。作業中の ``.wav`` を
    「中間音声」に混ぜると、消していい物の量が読めなくなるため。
    """
    parts = {part.casefold() for part in relative_path.parts[:-1]}
    suffix = relative_path.suffix.casefold()
    name = relative_path.name

    if parts & WORK_DIR_NAMES or suffix in WORK_SUFFIXES or name.startswith("."):
        return "work"
    if suffix.startswith(".bak_"):
        return "work"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in SEGMENT_AUDIO_SUFFIXES:
        return "segment_audio"
    if suffix in AUDIO_SUFFIXES:
        return "segment_audio" if SEGMENT_DIR_NAME in parts else "audio"
    if suffix in IMAGE_SUFFIXES:
        return "thumbnail" if SCREENSHOT_DIR_NAME in parts else "image"
    if suffix in PAGE_SUFFIXES:
        return "page"
    if suffix in DATA_SUFFIXES:
        return "data"
    return "other"


# --- 数え上げた結果 -------------------------------------------------


@dataclass(frozen=True)
class ArchiveFile:
    path: Path
    relative: str
    category: str
    size: int
    mtime: float

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)


@dataclass
class BroadcastEntry:
    """生成物側の放送ひとつぶん。"""

    lv: str
    directory: Path
    broadcaster_id: str = ""
    broadcaster_name: str = ""
    title: str = ""
    started_at: datetime | None = None
    files: list[ArchiveFile] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    total_size: int = 0

    @property
    def broadcaster_label(self) -> str:
        if self.broadcaster_name and self.broadcaster_id:
            return f"{self.broadcaster_name} ({self.broadcaster_id})"
        return self.broadcaster_name or self.broadcaster_id

    @property
    def has_page(self) -> bool:
        return self.counts.get("page", 0) > 0

    def size_of(self, category: str) -> int:
        return int(self.sizes.get(category, 0))

    def paths_in(self, categories: Iterable[str]) -> list[Path]:
        wanted = set(categories)
        return [item.path for item in self.files if item.category in wanted]

    def search_text(self) -> str:
        return " ".join([self.lv, self.title, self.broadcaster_name, self.broadcaster_id]).casefold()


@dataclass
class RecordingEntry:
    """録画フォルダ側の、同じlvの録画ファイルをまとめたもの。"""

    lv: str
    folder: str
    directory: Path
    broadcaster_id: str = ""
    broadcaster_name: str = ""
    title: str = ""
    started_at: datetime | None = None
    files: list[ArchiveFile] = field(default_factory=list)
    total_size: int = 0
    generated: bool = False

    @property
    def broadcaster_label(self) -> str:
        if self.broadcaster_name and self.broadcaster_id:
            return f"{self.broadcaster_name} ({self.broadcaster_id})"
        return self.broadcaster_name or self.broadcaster_id or self.folder

    def paths(self) -> list[Path]:
        return [item.path for item in self.files]

    def search_text(self) -> str:
        return " ".join([self.lv, self.title, self.broadcaster_name, self.folder]).casefold()


@dataclass
class Inventory:
    platform_root: Path
    recording_root: Path
    broadcasts: list[BroadcastEntry] = field(default_factory=list)
    recordings: list[RecordingEntry] = field(default_factory=list)

    @property
    def broadcast_total_size(self) -> int:
        return sum(entry.total_size for entry in self.broadcasts)

    @property
    def recording_total_size(self) -> int:
        return sum(entry.total_size for entry in self.recordings)

    def category_totals(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for entry in self.broadcasts:
            for category, size in entry.sizes.items():
                totals[category] = totals.get(category, 0) + size
        return totals


ProgressCallback = Callable[[str], None]
CancelCallback = Callable[[], bool]


# --- 走査 -----------------------------------------------------------


def _stat_file(path: Path) -> tuple[int, float] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return int(info.st_size), float(info.st_mtime)


def path_size(path: Path) -> int:
    """ファイルなら実サイズ、ディレクトリなら配下の合計。"""
    path = Path(path)
    try:
        if path.is_file() and not path.is_symlink():
            return int(path.stat().st_size)
        if not path.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += int(entry.stat().st_size)
        except OSError:
            continue
    return total


def _unix_seconds(value: object) -> datetime | None:
    try:
        seconds = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds)
    except (OSError, OverflowError, ValueError):
        return None


def read_broadcast_metadata(directory: Path, lv: str) -> dict[str, object]:
    """放送ディレクトリから題名・配信者・開始時刻を拾う。

    ``lv*_data.json`` が正。無い放送（HTML生成前や失敗ぶん）もあるので、
    その場合はHTMLと録画ファイルの名前から埋める。
    """
    metadata: dict[str, object] = {"title": "", "broadcaster_name": "", "started_at": None}
    data_path = directory / f"{lv}_data.json"
    if not data_path.is_file():
        candidates = sorted(directory.glob("lv*_data.json"))
        data_path = candidates[0] if candidates else data_path
    if data_path.is_file():
        try:
            raw = json.loads(data_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if isinstance(raw, dict):
            metadata["title"] = str(raw.get("live_title") or "").strip()
            metadata["broadcaster_name"] = str(
                raw.get("owner_name") or raw.get("broadcaster") or ""
            ).strip()
            for key in ("start_time", "open_time", "begin_time"):
                started_at = _unix_seconds(raw.get(key))
                if started_at is not None:
                    metadata["started_at"] = started_at
                    break
    return metadata


def _fill_metadata_from_filenames(entry: BroadcastEntry) -> None:
    """data.json が無い放送を、ファイル名から埋める。"""
    if entry.title and entry.started_at is not None:
        return
    for item in entry.files:
        parsed = tracker.parse_slnico_segment_filename(item.path)
        if not parsed:
            continue
        if not entry.title:
            entry.title = str(parsed.get("title") or "")
        if entry.started_at is None:
            started_at = parsed.get("started_at")
            entry.started_at = started_at if isinstance(started_at, datetime) else None
        break
    if not entry.title:
        prefix = f"{entry.lv}_"
        for item in entry.files:
            if item.category == "page" and item.path.name.startswith(prefix):
                entry.title = item.path.stem[len(prefix) :]
                break
    if entry.started_at is None and entry.files:
        entry.started_at = datetime.fromtimestamp(min(item.mtime for item in entry.files))


def scan_broadcast_directory(directory: Path, broadcaster_id: str = "") -> BroadcastEntry:
    """放送ディレクトリ1つを数える。"""
    lv = directory.name
    entry = BroadcastEntry(lv=lv, directory=directory, broadcaster_id=broadcaster_id)
    for path in directory.rglob("*"):
        try:
            if path.is_dir() or path.is_symlink():
                continue
        except OSError:
            continue
        stat_result = _stat_file(path)
        if stat_result is None:
            continue
        size, mtime = stat_result
        relative = path.relative_to(directory)
        category = classify(relative)
        entry.files.append(
            ArchiveFile(
                path=path,
                relative=str(relative),
                category=category,
                size=size,
                mtime=mtime,
            )
        )
        entry.sizes[category] = entry.sizes.get(category, 0) + size
        entry.counts[category] = entry.counts.get(category, 0) + 1
        entry.total_size += size

    entry.files.sort(key=lambda item: (CATEGORY_ORDER.index(item.category), -item.size))
    metadata = read_broadcast_metadata(directory, lv)
    entry.title = str(metadata.get("title") or "")
    entry.broadcaster_name = str(metadata.get("broadcaster_name") or "")
    started_at = metadata.get("started_at")
    entry.started_at = started_at if isinstance(started_at, datetime) else None
    _fill_metadata_from_filenames(entry)
    return entry


def scan_broadcasts(
    platform_root: Path,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> list[BroadcastEntry]:
    """``platform/niconico`` 配下の放送ディレクトリを全部数える。"""
    entries: list[BroadcastEntry] = []
    root = Path(platform_root)
    if not root.is_dir():
        return entries
    account_dirs = sorted(path for path in root.iterdir() if path.is_dir())
    for index, account_dir in enumerate(account_dirs, start=1):
        if should_cancel is not None and should_cancel():
            return entries
        broadcast_root = account_dir / "broadcast"
        if not broadcast_root.is_dir():
            continue
        if on_progress is not None:
            on_progress(f"生成物を確認中 {index}/{len(account_dirs)}: {account_dir.name}")
        for directory in sorted(broadcast_root.iterdir()):
            if should_cancel is not None and should_cancel():
                return entries
            if not directory.is_dir() or not directory.name.lower().startswith("lv"):
                continue
            entries.append(scan_broadcast_directory(directory, broadcaster_id=account_dir.name))
    return entries


def split_recording_folder_name(name: str) -> tuple[str, str]:
    """``<配信者ID>_<名前>`` を分解する。分けられなければ名前だけ返す。"""
    head, separator, tail = name.partition("_")
    if separator and head.isdigit():
        return head, tail
    return "", name


def scan_recordings(
    recording_root: Path,
    *,
    generated_lvs: Iterable[str] = (),
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> list[RecordingEntry]:
    """録画フォルダを走査し、lvごとにまとめる。

    lvを読み取れないファイルは、フォルダ単位の ``lv`` 無しグループへ入れる。
    消し忘れを見落とさないよう、拾えなかったぶんも表に出す。
    """
    root = Path(recording_root)
    if not root.is_dir():
        return []
    known_lvs = {str(value).casefold() for value in generated_lvs}
    groups: dict[tuple[str, str], RecordingEntry] = {}

    folders = sorted(path for path in root.iterdir() if path.is_dir())
    scan_targets: list[Path] = [root, *folders]
    for index, folder in enumerate(scan_targets, start=1):
        if should_cancel is not None and should_cancel():
            break
        if on_progress is not None:
            on_progress(f"録画フォルダを確認中 {index}/{len(scan_targets)}: {folder.name or root.name}")
        pattern = "*" if folder == root else "**/*"
        for path in folder.glob(pattern):
            if should_cancel is not None and should_cancel():
                break
            try:
                if path.is_dir() or path.is_symlink():
                    continue
            except OSError:
                continue
            if path.suffix.casefold() not in MEDIA_SUFFIXES:
                continue
            stat_result = _stat_file(path)
            if stat_result is None:
                continue
            size, mtime = stat_result
            parsed = tracker.parse_slnico_segment_filename(path)
            lv = str(parsed.get("lv") or "").lower() if parsed else ""
            folder_name = folder.name if folder != root else ""
            key = (folder_name, lv)
            entry = groups.get(key)
            if entry is None:
                broadcaster_id, broadcaster_name = split_recording_folder_name(folder_name)
                entry = RecordingEntry(
                    lv=lv,
                    folder=folder_name or root.name,
                    directory=folder,
                    broadcaster_id=broadcaster_id,
                    broadcaster_name=broadcaster_name,
                    generated=bool(lv) and lv in known_lvs,
                )
                groups[key] = entry
            entry.files.append(
                ArchiveFile(
                    path=path,
                    relative=str(path.relative_to(folder)),
                    category=classify(PurePath(path.name)),
                    size=size,
                    mtime=mtime,
                )
            )
            entry.total_size += size
            if parsed:
                if not entry.title:
                    entry.title = str(parsed.get("title") or "")
                started_at = parsed.get("started_at")
                if isinstance(started_at, datetime) and (
                    entry.started_at is None or started_at < entry.started_at
                ):
                    entry.started_at = started_at
            elif entry.started_at is None:
                entry.started_at = datetime.fromtimestamp(mtime)

    for entry in groups.values():
        entry.files.sort(key=lambda item: item.path.name.casefold())
    return sorted(
        groups.values(),
        key=lambda item: (item.started_at or datetime.max, item.folder, item.lv),
    )


def build_inventory(
    *,
    platform_root: Path,
    recording_root: Path,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> Inventory:
    broadcasts = scan_broadcasts(
        platform_root, on_progress=on_progress, should_cancel=should_cancel
    )
    generated_lvs = {entry.lv.casefold() for entry in broadcasts if entry.has_page}
    recordings = scan_recordings(
        recording_root,
        generated_lvs=generated_lvs,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    return Inventory(
        platform_root=Path(platform_root),
        recording_root=Path(recording_root),
        broadcasts=broadcasts,
        recordings=recordings,
    )


# --- 削除 -----------------------------------------------------------


@dataclass
class DeleteResult:
    deleted: list[Path] = field(default_factory=list)
    deleted_bytes: int = 0
    failed: list[tuple[Path, str]] = field(default_factory=list)
    recycled: bool = False


FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_NOCONFIRMMKDIR = 0x0200
RECYCLE_BATCH_SIZE = 100


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def recycle_bin_available() -> bool:
    return os.name == "nt"


def move_to_recycle_bin(paths: Sequence[Path]) -> None:
    """SHFileOperationW でごみ箱へ入れる。失敗したら例外にする。"""
    if os.name != "nt":
        raise RuntimeError("ごみ箱への移動はWindowsでのみ使えます")
    targets = [str(Path(path).resolve()) for path in paths]
    if not targets:
        return
    operation = _SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    operation.pFrom = "\0".join(targets) + "\0\0"
    operation.pTo = None
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT | FOF_NOCONFIRMMKDIR
    operation.fAnyOperationsAborted = 0
    operation.hNameMappings = None
    operation.lpszProgressTitle = None
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if code != 0:
        raise OSError(f"ごみ箱へ移せません (SHFileOperationW={code})")
    if operation.fAnyOperationsAborted:
        raise OSError("ごみ箱への移動が中断されました")


def _delete_permanently(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def delete_paths(
    paths: Sequence[Path],
    *,
    use_recycle_bin: bool = True,
    on_progress: ProgressCallback | None = None,
) -> DeleteResult:
    """選ばれたパスを消す。ごみ箱経由が既定で、失敗しても完全削除へは倒さない。"""
    result = DeleteResult(recycled=bool(use_recycle_bin) and recycle_bin_available())
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = Path(path)
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        if resolved.exists():
            unique.append(resolved)

    sizes = {str(path): path_size(path) for path in unique}

    if result.recycled:
        for index in range(0, len(unique), RECYCLE_BATCH_SIZE):
            batch = unique[index : index + RECYCLE_BATCH_SIZE]
            if on_progress is not None:
                on_progress(f"ごみ箱へ移動中 {min(index + len(batch), len(unique))}/{len(unique)} 件")
            try:
                move_to_recycle_bin(batch)
            except Exception as exc:
                for path in batch:
                    result.failed.append((path, str(exc)))
                continue
            for path in batch:
                if path.exists():
                    result.failed.append((path, "削除されませんでした"))
                    continue
                result.deleted.append(path)
                result.deleted_bytes += sizes.get(str(path), 0)
        return result

    for index, path in enumerate(unique, start=1):
        if on_progress is not None and index % 50 == 0:
            on_progress(f"削除中 {index}/{len(unique)} 件")
        try:
            _delete_permanently(path)
        except OSError as exc:
            result.failed.append((path, str(exc)))
            continue
        result.deleted.append(path)
        result.deleted_bytes += sizes.get(str(path), 0)
    return result


def prune_empty_directories(root: Path) -> list[Path]:
    """削除後に残った空ディレクトリを、rootを残して片付ける。"""
    removed: list[Path] = []
    root = Path(root)
    if not root.is_dir():
        return removed
    for directory in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            next(directory.iterdir())
        except StopIteration:
            try:
                directory.rmdir()
            except OSError:
                continue
            removed.append(directory)
        except OSError:
            continue
    return removed

"""保存先を移す。

方針は3つ。
- 消す前に必ず書き込んで検証する。コピー→サイズ照合→元を削除、の順を崩さない。
- 途中で止めても壊れない。移行先に同名同サイズが既にあれば飛ばすので、そのまま再開できる。
- 生成HTMLの参照は全て相対パスなので、ツリーごと動かせばリンクは切れない。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


COPY_CHUNK_BYTES = 4 * 1024 * 1024
PARTIAL_SUFFIX = ".nwa-part"

ProgressCallback = Callable[["MigrationProgress"], None]
CancelCallback = Callable[[], bool]


@dataclass(frozen=True)
class MigrationItem:
    source: Path
    destination: Path
    size: int


@dataclass
class MigrationPlan:
    source_root: Path
    destination_root: Path
    items: list[MigrationItem] = field(default_factory=list)
    skipped: list[MigrationItem] = field(default_factory=list)
    conflicts: list[MigrationItem] = field(default_factory=list)
    same_volume: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.items)

    @property
    def file_count(self) -> int:
        return len(self.items)


@dataclass
class MigrationProgress:
    current: Path
    done_files: int
    total_files: int
    done_bytes: int
    total_bytes: int


@dataclass
class MigrationResult:
    moved: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    moved_bytes: int = 0
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed and not self.cancelled


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _same_volume(left: Path, right: Path) -> bool:
    left_drive = str(left.drive or left.anchor or "").lower()
    right_drive = str(right.drive or right.anchor or "").lower()
    return bool(left_drive) and left_drive == right_drive


def validate(source_root: Path, destination_root: Path) -> None:
    """移してはいけない組み合わせを弾く。異常時は ValueError。"""
    source = Path(str(source_root)).expanduser()
    destination = Path(str(destination_root)).expanduser()
    if not str(destination):
        raise ValueError("移行先が空です")
    if not source.is_dir():
        raise ValueError(f"移行元が見つかりません: {source}")
    try:
        if source.resolve() == destination.resolve():
            raise ValueError("移行元と移行先が同じです")
    except OSError:
        pass
    if _is_within(destination, source):
        raise ValueError("移行先を移行元の内側には指定できません")
    if _is_within(source, destination):
        raise ValueError("移行先が移行元を含んでいます")


def build_plan(source_root: Path | str, destination_root: Path | str) -> MigrationPlan:
    """移すファイルを数える。既に移り終えているものは skipped に分ける。"""
    source = Path(str(source_root)).expanduser()
    destination = Path(str(destination_root)).expanduser()
    validate(source, destination)

    plan = MigrationPlan(
        source_root=source,
        destination_root=destination,
        same_volume=_same_volume(source, destination),
    )
    for entry in sorted(source.rglob("*")):
        try:
            if not entry.is_file() or entry.is_symlink():
                continue
            size = entry.stat().st_size
        except OSError:
            continue
        if entry.name.endswith(PARTIAL_SUFFIX):
            continue
        target = destination / entry.relative_to(source)
        item = MigrationItem(source=entry, destination=target, size=size)
        try:
            if target.exists():
                if target.stat().st_size == size:
                    plan.skipped.append(item)
                else:
                    plan.conflicts.append(item)
                continue
        except OSError:
            pass
        plan.items.append(item)
    return plan


def check_free_space(plan: MigrationPlan) -> None:
    """移行先の空きが足りなければ ValueError。同一ドライブなら不要。"""
    if plan.same_volume:
        return
    probe = plan.destination_root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(str(probe)).free
    except OSError:
        return
    required = plan.total_bytes
    if free < required:
        raise ValueError(
            f"移行先の空き容量が足りません。必要 {required / 1073741824:.1f} GB / "
            f"空き {free / 1073741824:.1f} GB"
        )


def _copy_then_verify(item: MigrationItem, should_cancel: CancelCallback | None) -> bool:
    """一時ファイルへ写し、サイズを照合してから本名に置く。中断なら False。"""
    item.destination.parent.mkdir(parents=True, exist_ok=True)
    partial = item.destination.with_name(item.destination.name + PARTIAL_SUFFIX)
    copied = 0
    try:
        with open(item.source, "rb") as reader, open(partial, "wb") as writer:
            while True:
                if should_cancel is not None and should_cancel():
                    raise InterruptedError
                chunk = reader.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except InterruptedError:
        partial.unlink(missing_ok=True)
        return False
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    if copied != item.size or partial.stat().st_size != item.size:
        partial.unlink(missing_ok=True)
        raise OSError(
            f"コピー結果のサイズが一致しません: {item.source} "
            f"({item.size} → {partial.stat().st_size if partial.exists() else 0})"
        )
    shutil.copystat(item.source, partial, follow_symlinks=False)
    partial.replace(item.destination)
    return True


def _move_one(item: MigrationItem, same_volume: bool, should_cancel: CancelCallback | None) -> bool:
    """1ファイル移す。中断なら False。同一ドライブは rename で済ませる。"""
    if same_volume:
        item.destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(item.source, item.destination)
            return True
        except OSError:
            pass  # ドライブ判定が外れていた場合はコピーへ落とす
    if not _copy_then_verify(item, should_cancel):
        return False
    item.source.unlink()
    return True


def prune_empty_dirs(root: Path) -> int:
    """移行後に空になったディレクトリを畳む。root 自体は残す。"""
    removed = 0
    if not root.is_dir():
        return removed
    for directory in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not directory.is_dir():
            continue
        try:
            next(directory.iterdir())
        except StopIteration:
            try:
                directory.rmdir()
                removed += 1
            except OSError:
                continue
        except OSError:
            continue
    return removed


def run(
    plan: MigrationPlan,
    *,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> MigrationResult:
    """計画どおりに移す。1件失敗しても止めず、最後にまとめて返す。"""
    result = MigrationResult(skipped=[item.source for item in plan.skipped])
    total_files = plan.file_count
    total_bytes = plan.total_bytes
    done_files = 0
    done_bytes = 0

    for item in plan.items:
        if should_cancel is not None and should_cancel():
            result.cancelled = True
            break
        if on_progress is not None:
            on_progress(
                MigrationProgress(
                    current=item.source,
                    done_files=done_files,
                    total_files=total_files,
                    done_bytes=done_bytes,
                    total_bytes=total_bytes,
                )
            )
        try:
            if not _move_one(item, plan.same_volume, should_cancel):
                result.cancelled = True
                break
        except OSError as exc:
            result.failed.append((item.source, str(exc)))
            continue
        result.moved.append(item.source)
        result.moved_bytes += item.size
        done_files += 1
        done_bytes += item.size

    if on_progress is not None:
        on_progress(
            MigrationProgress(
                current=plan.destination_root,
                done_files=done_files,
                total_files=total_files,
                done_bytes=done_bytes,
                total_bytes=total_bytes,
            )
        )
    if not result.failed and not result.cancelled:
        prune_empty_dirs(plan.source_root)
    return result

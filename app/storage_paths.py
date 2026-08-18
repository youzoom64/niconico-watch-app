"""生成物と録画の保存先を読み書きする。

保存先は2系統ある。
- 生成物: config.json の target_root。配下に platform/niconico/<account_id>/broadcast/<lv>/ が並ぶ。
- 録画: SlNicoLiveRec_config.json の StorageLocation。録画アプリ側の設定なのでこちらから書き換える。

どちらも「空欄なら既定を継承」はしない。読めなかった場合も実パスを返す。
"""

from __future__ import annotations

import contextlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import tracker


SLNICO_STORAGE_KEY = "StorageLocation"


@dataclass(frozen=True)
class RootStatus:
    """保存先ひとつぶんの現況。"""

    path: Path
    exists: bool
    total_bytes: int
    free_bytes: int
    used_bytes: int

    @property
    def drive(self) -> str:
        return str(self.path.drive or self.path.anchor or "")


def _disk_usage(path: Path) -> tuple[int, int]:
    """存在する祖先まで遡ってドライブの総容量と空きを返す。"""
    probe = path
    while True:
        try:
            usage = shutil.disk_usage(str(probe))
            return int(usage.total), int(usage.free)
        except OSError:
            parent = probe.parent
            if parent == probe:
                return 0, 0
            probe = parent


def directory_size(path: Path) -> int:
    """配下の実ファイル合計サイズ。走査できないものは飛ばす。"""
    if not path.is_dir():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def describe_root(path: Path, *, measure: bool = False) -> RootStatus:
    resolved = Path(str(path)).expanduser()
    total, free = _disk_usage(resolved)
    return RootStatus(
        path=resolved,
        exists=resolved.is_dir(),
        total_bytes=total,
        free_bytes=free,
        used_bytes=directory_size(resolved) if measure else 0,
    )


# --- 生成物の保存先 -------------------------------------------------


def read_target_root() -> Path:
    """config.json の target_root。未設定でも既定の実パスを返す。"""
    try:
        value = str(tracker.load_config().target_root or "").strip()
    except Exception:
        value = ""
    return Path(value).expanduser() if value else Path(tracker.DEFAULT_TARGET_ROOT)


def write_target_root(path: Path | str) -> Path:
    resolved = Path(str(path or "").strip()).expanduser()
    if not str(resolved):
        raise ValueError("生成物の保存先が空です")
    resolved.mkdir(parents=True, exist_ok=True)
    tracker.save_config_values({"target_root": str(resolved)})
    return resolved


def platform_root_of(target_root: Path | str) -> Path:
    """target_root から実際に生成物が積まれる platform/niconico を組み立てる。"""
    return Path(str(target_root)).expanduser() / "platform" / "niconico"


# --- 録画の保存先 ---------------------------------------------------


def slnico_config_path() -> Path:
    """使用中の SlNicoLiveRec_config.json。exe の隣にある。"""
    try:
        config = tracker.load_config()
        exe = Path(str(config.slnico_live_rec_exe or "").strip())
        candidate = exe.parent / "SlNicoLiveRec_config.json"
        if candidate.is_file():
            return candidate
    except Exception:
        pass
    return Path(tracker.DEFAULT_SLNICO_CONFIG)


def read_recording_root() -> Path:
    """録画アプリの保存先。tracker の解決と同じ結果を返す。"""
    try:
        return Path(tracker.slnico_storage_root())
    except Exception:
        return Path(tracker.DEFAULT_SLNICO_RECORDING_ROOT)


def write_recording_root(path: Path | str) -> Path:
    """SlNicoLiveRec_config.json の StorageLocation だけを書き換える。

    認証情報や未知のキーには触らない。書き込みは一時ファイル経由。
    """
    resolved = Path(str(path or "").strip()).expanduser()
    if not str(resolved):
        raise ValueError("録画の保存先が空です")
    config_path = slnico_config_path()
    if not config_path.is_file():
        raise FileNotFoundError(
            "SlNicoLiveRecの設定ファイルが見つかりません。"
            f"録画アプリを一度起動して終了してください: {config_path}"
        )
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"SlNicoLiveRec設定の読込に失敗しました: {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"SlNicoLiveRec設定の形式が不正です: {config_path}")

    resolved.mkdir(parents=True, exist_ok=True)
    raw[SLNICO_STORAGE_KEY] = str(resolved)
    temporary_path = config_path.with_suffix(config_path.suffix + ".tmp")
    try:
        temporary_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(config_path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            temporary_path.unlink()
        raise RuntimeError(f"SlNicoLiveRec設定の保存に失敗しました: {config_path}: {exc}") from exc
    return resolved


def format_bytes(size: int) -> str:
    value = float(max(int(size), 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} TB"

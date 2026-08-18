from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import tracker


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SlNicoLiveRecへ監視アプリ推奨設定を適用します。"
    )
    parser.add_argument("exe", help="SlNicoLiveRec.exeのパス")
    args = parser.parse_args()
    print(f"[SlNicoLiveRec] 設定適用開始: {args.exe}", flush=True)
    try:
        config_path = tracker.apply_recommended_slnico_settings(args.exe)
    except Exception as exc:
        print(f"[SlNicoLiveRec] エラー: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"[SlNicoLiveRec] 設定適用完了: {config_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

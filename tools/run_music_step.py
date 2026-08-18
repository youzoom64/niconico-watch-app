"""曲が生成されていない放送に対して、step06だけ（必要ならHTML再生成まで）を回す。

元動画が削除済みの放送でも動く。step01/step02を含まないため前処理を要求しない。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app import tracker


MUSIC_STEP = "step06_music_generator"
HTML_STEPS = [
    "step12_html_generator",
    "step13_index_generator",
    "step14_modern_list_generator",
]
UPLOAD_STEP = "step15_lolipop_uploader"


def report(message: str) -> None:
    """stdoutが差し替えられていても影響を受けない出力先へ書く。"""
    stream = sys.__stderr__ or sys.stderr
    stream.write(message + "\n")
    stream.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lv", action="append", required=True, help="対象LV。複数指定可")
    parser.add_argument("--account-id", required=True)
    parser.add_argument(
        "--with-html",
        action="store_true",
        help="step12〜14でHTMLを作り直す。曲をページへ反映する時に付ける",
    )
    parser.add_argument(
        "--with-upload",
        action="store_true",
        help="step15でアップロードする。1件ずつ上げるとFTPが詰まるため既定は無効",
    )
    parser.add_argument(
        "--upload-only",
        action="store_true",
        help="step15だけ実行する。曲とHTMLが既に出来ている放送を公開する時に使う",
    )
    args = parser.parse_args()

    if args.upload_only:
        steps = [UPLOAD_STEP]
    else:
        steps = [MUSIC_STEP]
        if args.with_html:
            steps += HTML_STEPS
        if args.with_upload:
            steps.append(UPLOAD_STEP)

    lvs = tracker.sort_broadcast_lvs_oldest_first(list(args.lv))
    print(f"対象 {len(lvs)}件 / 実行Step={','.join(steps)}", flush=True)

    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []
    for index, lv in enumerate(lvs, 1):
        print(f"\n[{index}/{len(lvs)}] {lv} 開始", flush=True)
        try:
            result = tracker.run_legacy_archiver_steps(
                lv,
                account_id=args.account_id,
                steps=steps,
                force_overwrite_existing_html=True,
                single_broadcast_scope=True,
                # run_legacy_archiver_steps内はstdoutがteeへ差し替わり、
                # そのteeがこのcallbackを呼ぶ。printで書くと再帰するため実stderrへ出す。
                progress_callback=lambda message, current=lv: report(
                    f"  {current}: {message}"
                ),
            )
        except Exception as exc:
            failed.append((lv, f"{type(exc).__name__}: {exc}"))
            print(f"[{index}/{len(lvs)}] {lv} 失敗: {type(exc).__name__}: {exc}", flush=True)
            continue
        music = (result.get("steps", {}).get(MUSIC_STEP, {}).get("result") or {})
        if music.get("music_generated"):
            succeeded.append(lv)
            print(f"[{index}/{len(lvs)}] {lv} 曲を生成", flush=True)
        else:
            reason = str(music.get("reason") or "不明")
            failed.append((lv, reason))
            print(f"[{index}/{len(lvs)}] {lv} 曲なし: {reason}", flush=True)

    print(f"\n完了: 成功{len(succeeded)}件 / 失敗{len(failed)}件")
    for lv, reason in failed:
        print(f"  失敗 {lv}: {reason}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

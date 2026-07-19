from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import tracker  # noqa: E402


def validate_finalize_result(result: dict, *, transcribe: bool) -> None:
    errors: list[str] = []
    if transcribe:
        failed_segments = [
            row
            for row in result.get("segment_transcriptions") or []
            if str(row.get("reason") or "") in {"failed", "mp4_missing"}
        ]
        if failed_segments:
            errors.append(f"segment transcription failures={len(failed_segments)}")
    legacy_error = str(result.get("legacy_archiver_error") or "").strip()
    if legacy_error:
        errors.append(f"legacy archiver failed: {legacy_error}")
    html_file = (
        result.get("legacy_archiver", {})
        .get("steps", {})
        .get("step12_html_generator", {})
        .get("result", {})
        .get("html_file")
    )
    if not html_file:
        errors.append("step12 HTML output is missing")
    if errors:
        raise RuntimeError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run NicoNico finalize pipeline with visible logs.")
    parser.add_argument("--lv", required=True)
    parser.add_argument("--broadcaster-id", default="")
    parser.add_argument("--target-dir", default="")
    parser.add_argument("--input-dir", default="")
    parser.add_argument("--timeline-mode", choices=("live", "timeshift"), default="live")
    parser.add_argument("--segment-path", action="append", default=[])
    parser.add_argument("--result-json", default="")
    parser.add_argument("--no-transcribe", action="store_true")
    parser.add_argument("--whisper-model", default="large-v3")
    parser.add_argument("--prepare-live-inputs", action="store_true")
    parser.add_argument("--queue-attempt", type=int, default=0)
    args = parser.parse_args()

    if args.queue_attempt > 0:
        tracker.set_finalize_queue_worker_pid(
            args.lv,
            os.getpid(),
            attempts=args.queue_attempt,
        )

    print("=" * 80, flush=True)
    print(f"[finalize] start lv={args.lv} broadcaster_id={args.broadcaster_id}", flush=True)
    print(f"[finalize] cwd={Path.cwd()}", flush=True)
    print(f"[finalize] python={sys.executable}", flush=True)
    print("=" * 80, flush=True)

    try:
        preparation = None
        if args.prepare_live_inputs:
            print(f"[finalize] prepare live inputs lv={args.lv}", flush=True)
            preparation = tracker.prepare_recording_finalize_inputs(args.lv, args.broadcaster_id)
            with tracker.connect() as conn:
                conn.execute(
                    "UPDATE recording_jobs SET status = 'finalizing', updated_at = ? WHERE lv = ?",
                    (tracker.now_micro(), args.lv),
                )
                conn.commit()
        result = tracker.run_finalize_pipeline_for_lv(
            args.lv,
            broadcaster_id=args.broadcaster_id,
            input_dir=args.input_dir or None,
            transcribe=not args.no_transcribe,
            whisper_model=args.whisper_model,
            timeline_mode=args.timeline_mode,
            segment_paths=[Path(path) for path in args.segment_path] or None,
        )
        if preparation is not None:
            result["queue_preparation"] = preparation
        validate_finalize_result(result, transcribe=not args.no_transcribe)
        if args.result_json:
            result_path = Path(args.result_json)
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        with tracker.connect() as conn:
            tracker.record_recording_event(
                conn,
                lv=args.lv,
                broadcaster_id=args.broadcaster_id,
                broadcaster_name="",
                watch_url=f"https://live.nicovideo.jp/watch/{args.lv}",
                recorder="postprocess",
                pid=None,
                event_type="finalize_pipeline_done",
                event_at=tracker.now_micro(),
                started_at=None,
                ended_at=None,
                duration_us=None,
                exit_code=None,
                target_dir=args.target_dir,
                payload=result,
            )
            conn.commit()
        print("[finalize] done", flush=True)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str), flush=True)
        return 0
    except Exception:
        print("[finalize] failed", flush=True)
        traceback.print_exc()
        try:
            with tracker.connect() as conn:
                tracker.record_recording_event(
                    conn,
                    lv=args.lv,
                    broadcaster_id=args.broadcaster_id,
                    broadcaster_name="",
                    watch_url=f"https://live.nicovideo.jp/watch/{args.lv}",
                    recorder="postprocess",
                    pid=None,
                    event_type="finalize_pipeline_failed",
                    event_at=tracker.now_micro(),
                    started_at=None,
                    ended_at=None,
                    duration_us=None,
                    exit_code=None,
                    target_dir=args.target_dir,
                    payload={"error": traceback.format_exc()},
                )
                conn.commit()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

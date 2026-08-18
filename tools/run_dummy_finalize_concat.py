from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import tracker


LV = "lv999990001"
BROADCASTER_ID = "999990001"
TITLE = "dummy finalize concat"


def run_ffmpeg(args: list[str]) -> None:
    subprocess.run(args, capture_output=True, text=True, timeout=120, check=True)


def make_segment(path: Path, color: str, label: str, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=1280x720:r=30:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}:sample_rate=48000",
            "-vf",
            f"drawtext=text='{label}':fontcolor=white:fontsize=48:x=60:y=60",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-shortest",
            str(path),
        ]
    )


def reset_db_rows(lv: str) -> None:
    with tracker.connect() as conn:
        for table in ("recording_segments", "recording_gaps", "postprocess_jobs", "recording_jobs", "recording_events"):
            conn.execute(f"DELETE FROM {table} WHERE lv = ?", (lv,))
        conn.commit()


def seed_gap_rows(lv: str, gaps: list[dict[str, object]]) -> None:
    now = tracker.now_micro()
    with tracker.connect() as conn:
        conn.execute(
            """
            INSERT INTO recording_jobs
                (lv, broadcaster_id, broadcaster_name, watch_url, recorder, target_dir, status, started_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lv,
                BROADCASTER_ID,
                "dummy broadcaster",
                f"https://live.nicovideo.jp/watch/{lv}",
                "dummy_finalize_concat",
                str(ROOT / "tmp" / "dummy_finalize_concat" / "target"),
                "dummy_finalize_test",
                now,
                now,
            ),
        )
        for gap in gaps:
            conn.execute(
                """
                INSERT INTO recording_gaps
                    (lv, gap_start, gap_end, duration_us, fill_type, status,
                     generated_video_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'black_silent_video', 'pending', '', ?, ?)
                """,
                (
                    lv,
                    str(gap["gap_start"]),
                    str(gap["gap_end"]),
                    int(gap["duration_us"]),
                    now,
                    now,
                ),
            )
        conn.commit()


def build_dummy_source(work_dir: Path, lv: str) -> tuple[Path, list[dict[str, object]]]:
    source_dir = work_dir / "source"
    if source_dir.exists():
        shutil.rmtree(source_dir)
    source_dir.mkdir(parents=True, exist_ok=True)

    base = datetime(2026, 6, 22, 12, 0, 0)
    starts = [base, base + timedelta(seconds=8), base + timedelta(seconds=15)]
    files = [
        source_dir / f"{lv}_{starts[0].strftime('%Y_%m%d_%H%M%S')}_{TITLE}_red.ts",
        source_dir / f"{lv}_{starts[1].strftime('%Y_%m%d_%H%M%S')}_{TITLE}_green.ts",
        source_dir / f"{lv}_{starts[2].strftime('%Y_%m%d_%H%M%S')}_{TITLE}_blue.ts",
    ]
    make_segment(files[0], "red", "segment 1", 3.0)
    make_segment(files[1], "green", "segment 2", 3.0)
    make_segment(files[2], "blue", "segment 3", 3.0)

    rows = tracker.recording_segment_creation_rows(lv, storage_root=source_dir)
    if len(rows) != 3:
        raise RuntimeError(f"dummy segment count mismatch: {len(rows)}")

    gap_specs: list[dict[str, object]] = []
    for index in (1, 2):
        previous = rows[index - 1]
        current = rows[index]
        previous_created = tracker.iso_to_datetime(str(previous["creation_time"]))
        current_created = tracker.iso_to_datetime(str(current["creation_time"]))
        duration_us = int((current_created - previous_created).total_seconds() * 1_000_000)
        if duration_us <= 0:
            raise RuntimeError("dummy file creation times are not increasing")
        gap_specs.append(
            {
                "gap_start": (current_created - timedelta(microseconds=duration_us)).isoformat(timespec="microseconds"),
                "gap_end": current_created.isoformat(timespec="microseconds"),
                "duration_us": min(duration_us, 1_500_000),
            }
        )
    return source_dir, gap_specs


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dummy finalize concat without real Nico recording files.")
    parser.add_argument("--lv", default=LV)
    parser.add_argument("--output-dir", default=str(ROOT / "tmp" / "dummy_finalize_concat"))
    parser.add_argument("--keep-db", action="store_true")
    args = parser.parse_args()

    lv = str(args.lv)
    work_dir = Path(args.output_dir)
    target_dir = work_dir / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    if not args.keep_db:
        reset_db_rows(lv)
    source_dir, gaps = build_dummy_source(work_dir, lv)
    seed_gap_rows(lv, gaps)
    output_path = target_dir / f"{lv}_joined.ts"
    plan = tracker.concat_slnico_segments_with_gaps(source_dir, output_path, lv=lv)
    result = {
        "lv": lv,
        "source_dir": str(source_dir),
        "output_path": str(output_path),
        "segments": len(plan["segments"]),
        "gaps": len(plan["gaps"]),
        "unmatched_gaps": len(plan.get("unmatched_gaps", [])),
        "parts": [part["type"] for part in plan["parts"]],
        "gap_source": plan.get("gap_source"),
        "concat_list_path": plan.get("concat_list_path"),
    }
    result_path = work_dir / f"{lv}_dummy_finalize_concat_result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

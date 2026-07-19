from __future__ import annotations

import os
import json
import sys
import time
from pathlib import Path
from typing import BinaryIO


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import tracker  # noqa: E402


_LAST_HEARTBEAT_MONOTONIC = 0.0
_HEARTBEAT_ENABLED = False


def touch_dispatcher_heartbeat(*, force: bool = False) -> None:
    global _HEARTBEAT_ENABLED, _LAST_HEARTBEAT_MONOTONIC
    if force:
        _HEARTBEAT_ENABLED = True
    if not _HEARTBEAT_ENABLED:
        return
    current = time.monotonic()
    if not force and current - _LAST_HEARTBEAT_MONOTONIC < 5.0:
        return
    tracker.update_finalize_dispatcher_state(os.getpid(), started=force)
    _LAST_HEARTBEAT_MONOTONIC = current


def acquire_singleton_lock() -> BinaryIO | None:
    """Hold one OS lock so only one dispatcher can drain the queue."""
    lock_path = tracker.TMP_DIR / "finalize_dispatcher.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        return None
    return handle


def release_singleton_lock(handle: BinaryIO) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def running_queue_row() -> dict | None:
    with tracker.connect() as conn:
        row = conn.execute(
            """
            SELECT lv, worker_pid, attempts, started_at
            FROM finalize_queue
            WHERE status = 'running'
            ORDER BY started_at, lv
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def recorded_worker_outcome(lv: str, started_at: str) -> str:
    with tracker.connect() as conn:
        row = conn.execute(
            """
            SELECT event_type
            FROM recording_events
            WHERE lv = ?
              AND event_type IN ('finalize_pipeline_done', 'finalize_pipeline_failed')
              AND event_at >= ?
            ORDER BY event_at DESC, id DESC
            LIMIT 1
            """,
            (lv, started_at or ""),
        ).fetchone()
    return str(row["event_type"] or "") if row else ""


def reconcile_interrupted_worker() -> bool:
    """Wait for an orphan worker, then recover its durable queue state."""
    row = running_queue_row()
    if row is None:
        return False
    lv = str(row["lv"])
    worker_pid = int(row.get("worker_pid") or 0)
    if worker_pid > 0 and tracker.is_process_running(worker_pid):
        print(f"[dispatcher] wait existing worker lv={lv} pid={worker_pid}", flush=True)
        time.sleep(2.0)
        return True
    result_path = tracker.TMP_DIR / "finalize_results" / f"{lv}_attempt{int(row.get('attempts') or 0)}.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        result = None
    if isinstance(result, dict):
        tracker.finish_finalize_queue_item(lv, success=True, result=result)
        print(f"[dispatcher] recovered completed worker lv={lv}", flush=True)
        return True
    outcome = recorded_worker_outcome(lv, str(row.get("started_at") or ""))
    if outcome == "finalize_pipeline_done":
        tracker.finish_finalize_queue_item(lv, success=True, result={"lv": lv, "recovered": True})
        print(f"[dispatcher] recovered completed worker lv={lv}", flush=True)
    elif outcome == "finalize_pipeline_failed":
        tracker.finish_finalize_queue_item(lv, success=False, error="finalize worker failed")
        print(f"[dispatcher] recovered failed worker lv={lv}", flush=True)
    else:
        launch_age = tracker._timestamp_age_seconds(str(row.get("started_at") or ""))
        if launch_age < tracker.FINALIZE_WORKER_LAUNCH_GRACE_SECONDS:
            print(
                f"[dispatcher] wait worker self-registration lv={lv} age={launch_age:.1f}s",
                flush=True,
            )
            time.sleep(1.0)
            return True
        tracker.requeue_interrupted_finalize_item(lv, error="finalize dispatcher or worker was interrupted")
        print(f"[dispatcher] requeued interrupted worker lv={lv}", flush=True)
    return True


def drain_queue() -> int:
    empty_checks = 0
    while True:
        touch_dispatcher_heartbeat()
        if reconcile_interrupted_worker():
            empty_checks = 0
            continue
        preparation_state = tracker.reconcile_oldest_preparing_finalize_item()
        if preparation_state == "waiting":
            time.sleep(0.5)
            continue
        if preparation_state == "recovered":
            empty_checks = 0
            continue
        job = tracker.claim_next_finalize_queue_item()
        if job is None:
            if tracker.finalize_queue_has_work():
                time.sleep(0.5)
                continue
            empty_checks += 1
            if empty_checks < 10:
                time.sleep(0.5)
                continue
            print("[dispatcher] queue empty", flush=True)
            return 0
        empty_checks = 0
        lv = str(job["lv"])
        broadcaster_id = str(job.get("broadcaster_id") or "")
        target_dir = str(job.get("target_dir") or "")
        source_kind = str(job.get("source_kind") or "live")
        input_dir = str(job.get("input_dir") or "")
        timeline_mode = str(job.get("timeline_mode") or "live")
        try:
            segment_paths = [Path(path) for path in json.loads(str(job.get("segment_paths_json") or "[]"))]
        except (TypeError, ValueError, json.JSONDecodeError):
            segment_paths = []
        result_dir = tracker.TMP_DIR / "finalize_results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_path = result_dir / f"{lv}_attempt{int(job.get('attempts') or 0)}.json"
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(
            f"[dispatcher] start lv={lv} attempt={int(job.get('attempts') or 0)}",
            flush=True,
        )
        process = tracker.start_visible_finalize_pipeline_process(
            lv=lv,
            broadcaster_id=broadcaster_id,
            target_dir=target_dir,
            input_dir=input_dir or None,
            timeline_mode=timeline_mode,
            segment_paths=segment_paths or None,
            transcribe=bool(int(job.get("transcribe") if job.get("transcribe") is not None else 1)),
            whisper_model=str(job.get("whisper_model") or "large-v3"),
            prepare_live_inputs=source_kind == "live",
            queue_attempt=int(job.get("attempts") or 0),
            result_json_path=result_path,
        )
        if not process:
            tracker.finish_finalize_queue_item(lv, success=False, error="failed to start finalize worker")
            continue
        tracker.set_finalize_queue_worker_pid(
            lv,
            int(process.pid),
            attempts=int(job.get("attempts") or 0),
            only_if_empty=True,
        )
        while process.poll() is None:
            touch_dispatcher_heartbeat()
            time.sleep(1.0)
        exit_code = int(process.returncode or 0)
        if exit_code == 0:
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                error = f"finalize result JSON is unavailable: {type(exc).__name__}: {exc}"
                tracker.finish_finalize_queue_item(lv, success=False, error=error)
                print(f"[dispatcher] failed lv={lv} result_json={error}", flush=True)
                continue
            tracker.finish_finalize_queue_item(lv, success=True, result=result)
            print(f"[dispatcher] done lv={lv}", flush=True)
        else:
            error = f"finalize worker exited with code {exit_code}"
            tracker.finish_finalize_queue_item(lv, success=False, error=error)
            print(f"[dispatcher] failed lv={lv} exit_code={exit_code}", flush=True)


def main() -> int:
    lock_handle = acquire_singleton_lock()
    if lock_handle is None:
        print("[dispatcher] another dispatcher is already running", flush=True)
        return 0
    try:
        touch_dispatcher_heartbeat(force=True)
        return drain_queue()
    finally:
        tracker.clear_finalize_dispatcher_state(os.getpid())
        release_singleton_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())

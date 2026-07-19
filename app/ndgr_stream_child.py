from __future__ import annotations

import asyncio
import faulthandler
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import ndgr_realtime


APP_ROOT = Path(__file__).resolve().parents[1]
CRASH_LOG_DIR = APP_ROOT / "data" / "logs" / "ndgr_child"


def emit(event: str, **payload: Any) -> None:
    data = {
        "event": event,
        "pid": os.getpid(),
        "time": time.time(),
        **payload,
    }
    print(json.dumps(data, ensure_ascii=False, default=str), flush=True)


async def run_stream(lv: str) -> int:
    stop_event = asyncio.Event()
    source = ndgr_realtime.NDGRCommentSource()
    count = 0
    started = time.monotonic()
    last_comment: dict[str, Any] = {}
    emit("status", lv=lv, text="NDGR接続中")
    emit("log", lv=lv, level="INFO", message=f"child stream start lv={lv}")
    try:
        async for comment in source.stream(lv=lv, stop_event=stop_event):
            count += 1
            row = dict(comment)
            last_comment = row
            emit("status", lv=lv, text="受信中")
            emit("comment", lv=lv, comment=row)
    except Exception as exc:
        emit(
            "error",
            lv=lv,
            error_type=type(exc).__name__,
            message=str(exc),
            traceback=traceback.format_exc(),
        )
        return 2
    finally:
        elapsed = time.monotonic() - started
        emit(
            "log",
            lv=lv,
            level="INFO",
            message=(
                f"child stream finally lv={lv} comments={count} elapsed={elapsed:.1f}s "
                f"last_no={last_comment.get('no') or ''} "
                f"last_user={last_comment.get('user_id') or last_comment.get('raw_user_id') or last_comment.get('hashed_user_id') or ''}"
            ),
        )
    emit("finished", lv=lv, comments=count, elapsed=elapsed)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: ndgr_stream_child.py <lv>", file=sys.stderr, flush=True)
        return 64
    lv = str(argv[1]).strip()
    if not lv:
        print("lv is empty", file=sys.stderr, flush=True)
        return 64

    CRASH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    crash_path = CRASH_LOG_DIR / f"{lv}_{os.getpid()}_faulthandler.log"
    with crash_path.open("w", encoding="utf-8") as crash_file:
        faulthandler.enable(file=crash_file, all_threads=True)
        emit(
            "log",
            lv=lv,
            level="INFO",
            message=(
                f"child process boot lv={lv} pid={os.getpid()} "
                f"python={sys.executable} crash_log={crash_path}"
            ),
        )
        return asyncio.run(run_stream(lv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

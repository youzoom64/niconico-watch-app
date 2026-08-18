from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = ROOT / "legacy_archiver"
for value in (ROOT, ROOT / "app", LEGACY_ROOT):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from app import tracker
from archive_db import list_broadcast_data


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def emit(event: str, **values: Any) -> None:
    payload = {"timestamp": stamp(), "event": event, **values}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def save_checkpoint(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def load_checkpoint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def row_sort_key(row: dict[str, Any]) -> tuple[float, str]:
    for key in ("start_time", "begin_time", "open_time"):
        try:
            return float(row.get(key) or 0), str(row.get("lv_value") or "")
        except (TypeError, ValueError):
            continue
    return 0.0, str(row.get("lv_value") or "")


def ready_lvs(broadcaster_id: str) -> tuple[list[str], str]:
    rows = [
        row
        for row in list_broadcast_data(broadcaster_id)
        if str(row.get("lv_value") or "").startswith("lv")
    ]
    rows.sort(key=row_sort_key)
    lvs = [str(row["lv_value"]) for row in rows]
    if not lvs:
        raise RuntimeError(f"Step12-ready放送がありません: {broadcaster_id}")
    return lvs, lvs[-1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="指定配信者のStep12-ready全放送をStep12から公開まで再実行する"
    )
    parser.add_argument("--broadcaster-id", required=True)
    parser.add_argument("--expected-count", type=int, default=0)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument(
        "--include-media",
        action="store_true",
        help="Step15でMP3とスクリーンショットも再送する",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="既存チェックポイントを破棄して最初から実行する",
    )
    args = parser.parse_args()

    broadcaster_id = str(args.broadcaster_id).strip()
    checkpoint = args.checkpoint or (
        ROOT / "tmp" / f"broadcaster_{broadcaster_id}_step12_to_end_checkpoint.json"
    )
    lvs, anchor_lv = ready_lvs(broadcaster_id)
    if args.expected_count and len(lvs) != args.expected_count:
        raise RuntimeError(
            f"対象件数が変わっています: expected={args.expected_count} actual={len(lvs)}"
        )

    state = {} if args.restart else load_checkpoint(checkpoint)
    if state and state.get("broadcaster_id") != broadcaster_id:
        raise RuntimeError("チェックポイントの配信者IDが一致しません")
    state.setdefault("broadcaster_id", broadcaster_id)
    state.setdefault("targets", lvs)
    state.setdefault("step12_done", [])
    state.setdefault("global_done", False)
    state.setdefault("step15_done", [])
    state.setdefault("failures", [])
    state["anchor_lv"] = anchor_lv
    state["include_media"] = bool(args.include_media)
    state["status"] = "running"
    state["updated_at"] = stamp()
    save_checkpoint(checkpoint, state)

    if state["targets"] != lvs:
        raise RuntimeError("チェックポイント作成後に対象LV一覧が変わっています")

    config = tracker.load_config()
    emit(
        "start",
        broadcaster_id=broadcaster_id,
        target_count=len(lvs),
        anchor_lv=anchor_lv,
        include_media=bool(args.include_media),
        checkpoint=str(checkpoint),
    )

    try:
        for index, lv in enumerate(lvs, start=1):
            if lv in state["step12_done"]:
                emit("step12_skip", lv=lv, index=index, total=len(lvs))
                continue
            emit("step12_start", lv=lv, index=index, total=len(lvs))
            tracker.run_legacy_archiver_steps(
                lv,
                account_id=broadcaster_id,
                steps=["step12_html_generator"],
                config=config,
                force_overwrite_existing_html=True,
            )
            state["step12_done"].append(lv)
            state["updated_at"] = stamp()
            save_checkpoint(checkpoint, state)
            emit("step12_done", lv=lv, index=index, total=len(lvs))

        if not state["global_done"]:
            emit("global_start", lv=anchor_lv, steps=["step13", "step14"])
            tracker.run_legacy_archiver_steps(
                anchor_lv,
                account_id=broadcaster_id,
                steps=["step13_index_generator", "step14_modern_list_generator"],
                config=config,
            )
            state["global_done"] = True
            state["updated_at"] = stamp()
            save_checkpoint(checkpoint, state)
            emit("global_done", lv=anchor_lv)

        for index, lv in enumerate(lvs, start=1):
            if lv in state["step15_done"]:
                emit("step15_skip", lv=lv, index=index, total=len(lvs))
                continue
            emit("step15_start", lv=lv, index=index, total=len(lvs))
            tracker.run_legacy_archiver_steps(
                lv,
                account_id=broadcaster_id,
                steps=["step15_lolipop_uploader"],
                config=config,
                upload_html_only=not args.include_media,
            )
            state["step15_done"].append(lv)
            state["updated_at"] = stamp()
            save_checkpoint(checkpoint, state)
            emit("step15_done", lv=lv, index=index, total=len(lvs))
    except Exception as error:
        state["status"] = "failed"
        state["updated_at"] = stamp()
        state["failures"].append(
            {"timestamp": stamp(), "type": type(error).__name__, "error": str(error)}
        )
        save_checkpoint(checkpoint, state)
        emit("failed", error_type=type(error).__name__, error=str(error))
        raise

    state["status"] = "complete"
    state["updated_at"] = stamp()
    save_checkpoint(checkpoint, state)
    emit(
        "complete",
        broadcaster_id=broadcaster_id,
        step12_done=len(state["step12_done"]),
        step15_done=len(state["step15_done"]),
        include_media=bool(args.include_media),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

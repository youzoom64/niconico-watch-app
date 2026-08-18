from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import tracker  # noqa: E402


def parse_iso_epoch(value: Any) -> tuple[int | None, str | None]:
    text = str(value or "").strip()
    if not text:
        return None, None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is not None:
            local_dt = datetime.fromtimestamp(dt.timestamp())
            return int(dt.timestamp()), local_dt.isoformat()
        return int(dt.timestamp()), dt.isoformat()
    except ValueError:
        return None, text


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_source_rows(simple_db: Path, source_lv: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(simple_db))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT
                n.id AS normalized_id,
                n.raw_event_id,
                n.lv,
                n.event_kind,
                n.no,
                n.user_id,
                n.raw_user_id,
                n.hashed_user_id,
                n.account_status,
                n.vpos,
                n.commands,
                n.content,
                n.display_text,
                n.payload_json,
                n.created_at AS normalized_created_at,
                r.source AS raw_source,
                r.page_index,
                r.message_id,
                r.received_at,
                r.raw_json
            FROM normalized_events n
            JOIN raw_events r ON r.id = n.raw_event_id
            WHERE n.lv = ?
              AND n.event_kind = 'chat'
              AND COALESCE(n.content, '') <> ''
            ORDER BY datetime(r.received_at), CAST(n.no AS INTEGER), n.id
            """,
            (source_lv,),
        ).fetchall()
    finally:
        conn.close()


def target_start_time(conn: sqlite3.Connection, target_lv: str) -> int | None:
    row = conn.execute(
        """
        SELECT start_time, open_time, begin_time
        FROM broadcast_archive_meta
        WHERE lv = ?
        """,
        (target_lv,),
    ).fetchone()
    if not row:
        return None
    for key in ("start_time", "open_time", "begin_time"):
        value = int_or_none(row[key])
        if value:
            return value
    return None


def backup_existing_comments(conn: sqlite3.Connection, target_lv: str) -> Path | None:
    rows = conn.execute(
        """
        SELECT *
        FROM archive_comments
        WHERE lv = ?
        ORDER BY broadcast_seconds ASC, no ASC, id ASC
        """,
        (target_lv,),
    ).fetchall()
    if not rows:
        return None
    backup_dir = ROOT / "tmp" / "comment_import_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"{target_lv}_archive_comments_{stamp}.json"
    path.write_text(
        json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def source_row_to_archive_row(
    row: sqlite3.Row,
    *,
    source_lv: str,
    target_lv: str,
    start_time: int | None,
    offset_seconds: float,
) -> dict[str, Any]:
    date_value, posted_at = parse_iso_epoch(row["received_at"])
    vpos = int_or_none(row["vpos"])
    if date_value is not None and start_time:
        broadcast_seconds = max(0.0, float(date_value - start_time) + offset_seconds)
    elif vpos is not None:
        broadcast_seconds = max(0.0, (vpos / 100.0) + offset_seconds)
    else:
        broadcast_seconds = max(0.0, offset_seconds)

    raw_user_id = row["raw_user_id"]
    hashed_user_id = row["hashed_user_id"]
    user_id = str(row["user_id"] or raw_user_id or hashed_user_id or "anonymous")
    commands = str(row["commands"] or "")
    account_status = str(row["account_status"] or "")
    raw_payload = {
        "imported_from": "simple_comment_viewer",
        "source_lv": source_lv,
        "target_lv": target_lv,
        "source": row["raw_source"],
        "page_index": row["page_index"],
        "message_id": row["message_id"],
        "received_at": row["received_at"],
        "normalized": {
            "id": row["normalized_id"],
            "raw_event_id": row["raw_event_id"],
            "no": row["no"],
            "user_id": row["user_id"],
            "raw_user_id": raw_user_id,
            "hashed_user_id": hashed_user_id,
            "account_status": account_status,
            "vpos": row["vpos"],
            "commands": commands,
            "content": row["content"],
            "display_text": row["display_text"],
            "payload_json": row["payload_json"],
        },
        "raw_json": row["raw_json"],
    }
    return {
        "lv": target_lv,
        "no": int_or_none(row["no"]),
        "comment_id": row["message_id"],
        "user_id": user_id,
        "raw_user_id": "" if raw_user_id is None else str(raw_user_id),
        "hashed_user_id": "" if hashed_user_id is None else str(hashed_user_id),
        "user_name": "",
        "text": str(row["content"] or ""),
        "date": date_value,
        "posted_at": posted_at,
        "received_at": str(row["received_at"] or ""),
        "vpos": vpos,
        "broadcast_seconds": broadcast_seconds,
        "timeline_block": int(broadcast_seconds // 10 * 10),
        "premium": 1 if account_status.lower() == "premium" else 0,
        "anonymity": 1 if "184" in commands.split() else 0,
        "mail": commands,
        "source": f"simple_comment_viewer:{source_lv}",
        "raw_json": json.dumps(raw_payload, ensure_ascii=False, default=str),
    }


def insert_archive_comments(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT INTO archive_comments
            (lv, no, comment_id, user_id, raw_user_id, hashed_user_id, user_name, text,
             date, posted_at, received_at, vpos, broadcast_seconds, timeline_block,
             premium, anonymity, mail, source, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["lv"],
                row["no"],
                row["comment_id"],
                row["user_id"],
                row["raw_user_id"],
                row["hashed_user_id"],
                row["user_name"],
                row["text"],
                row["date"],
                row["posted_at"],
                row["received_at"],
                row["vpos"],
                row["broadcast_seconds"],
                row["timeline_block"],
                row["premium"],
                row["anonymity"],
                row["mail"],
                row["source"],
                row["raw_json"],
                tracker.now_micro(),
            )
            for row in rows
        ],
    )


def rebuild_ranking(conn: sqlite3.Connection, target_lv: str) -> int:
    rows = conn.execute(
        """
        SELECT user_id, user_name, text, broadcast_seconds, premium, anonymity
        FROM archive_comments
        WHERE lv = ?
        ORDER BY broadcast_seconds ASC, no ASC, id ASC
        """,
        (target_lv,),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        user_id = str(row["user_id"] or "anonymous")
        current = grouped.get(user_id)
        seconds = float(row["broadcast_seconds"] or 0.0)
        if current is None:
            grouped[user_id] = {
                "user_id": user_id,
                "user_name": str(row["user_name"] or ""),
                "comment_count": 1,
                "first_comment": str(row["text"] or ""),
                "first_comment_time": seconds,
                "last_comment": str(row["text"] or ""),
                "last_comment_time": seconds,
                "premium": int(row["premium"] or 0),
                "anonymity": int(row["anonymity"] or 0),
            }
            continue
        current["comment_count"] += 1
        current["last_comment"] = str(row["text"] or "")
        current["last_comment_time"] = seconds
        current["premium"] = max(int(current["premium"] or 0), int(row["premium"] or 0))
        current["anonymity"] = max(int(current["anonymity"] or 0), int(row["anonymity"] or 0))

    ranking = sorted(grouped.values(), key=lambda item: (-item["comment_count"], item["first_comment_time"], item["user_id"]))
    conn.executemany(
        """
        INSERT INTO archive_comment_ranking
            (lv, user_id, user_name, comment_count, first_comment, first_comment_time,
             last_comment, last_comment_time, premium, anonymity, rank, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                target_lv,
                item["user_id"],
                item["user_name"],
                item["comment_count"],
                item["first_comment"],
                item["first_comment_time"],
                item["last_comment"],
                item["last_comment_time"],
                item["premium"],
                item["anonymity"],
                index,
                tracker.now_micro(),
            )
            for index, item in enumerate(ranking, 1)
        ],
    )
    return len(ranking)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Simple Comment Viewer comments into watch-app archive DB.")
    parser.add_argument("--source-lv", required=True)
    parser.add_argument("--target-lv", required=True)
    parser.add_argument(
        "--simple-db",
        type=Path,
        default=ROOT.parent / "niconico-simple-comment-viewer" / "data" / "simple_comment_viewer.sqlite3",
    )
    parser.add_argument("--target-dir", type=Path, default=None)
    parser.add_argument("--offset-seconds", type=float, default=0.0)
    parser.add_argument("--append", action="store_true", help="Do not clear existing target comments first.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_rows = load_source_rows(args.simple_db, args.source_lv)
    if not source_rows:
        raise SystemExit(f"source comments not found: {args.simple_db} {args.source_lv}")

    with tracker.connect() as conn:
        start_time = target_start_time(conn, args.target_lv)
        archive_rows = [
            source_row_to_archive_row(
                row,
                source_lv=args.source_lv,
                target_lv=args.target_lv,
                start_time=start_time,
                offset_seconds=args.offset_seconds,
            )
            for row in source_rows
        ]
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "source_lv": args.source_lv,
                        "target_lv": args.target_lv,
                        "source_rows": len(source_rows),
                        "archive_rows": len(archive_rows),
                        "target_start_time": start_time,
                        "first": archive_rows[0],
                        "last": archive_rows[-1],
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0

        backup_path = None
        if not args.append:
            backup_path = backup_existing_comments(conn, args.target_lv)
            conn.execute("DELETE FROM archive_comments WHERE lv = ?", (args.target_lv,))
            conn.execute("DELETE FROM archive_comment_ranking WHERE lv = ?", (args.target_lv,))

        insert_archive_comments(conn, archive_rows)
        ranking_count = rebuild_ranking(conn, args.target_lv)
        legacy_files = tracker.export_legacy_archive_files_from_ndgr(conn, args.target_lv, target_dir=args.target_dir)
        conn.commit()

    print(
        json.dumps(
            {
                "source_lv": args.source_lv,
                "target_lv": args.target_lv,
                "imported_comments": len(archive_rows),
                "ranking_users": ranking_count,
                "backup_path": str(backup_path) if backup_path else "",
                "legacy_files": legacy_files,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

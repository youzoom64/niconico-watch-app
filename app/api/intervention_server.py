from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
from contextlib import closing
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import tracker


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8794
COMMENT_OFFSET_CONFIRM_PATH = "/api/archive-comment-offset/confirm"
PREVIEW_PATH_PATTERN = re.compile(r"^/preview/(lv\d+)(?:/(.*))?$")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="niconico-watch-app local intervention API")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), InterventionHandler)
    print(f"niconico-watch intervention API listening on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


class InterventionHandler(BaseHTTPRequestHandler):
    server_version = "NiconicoWatchInterventionAPI/0.1"

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self.write_json(
                    {
                        "ok": True,
                        "service": "niconico-watch-app",
                        "db": str(tracker.DB_PATH),
                        "auth_enabled": bool(load_api_key()),
                    }
                )
                return
            if parsed.path == "/api/special-users":
                self.handle_get_special_users(parse_qs(parsed.query))
                return
            if PREVIEW_PATH_PATTERN.fullmatch(parsed.path):
                self.handle_get_preview(parsed.path)
                return
            self.write_error_json(HTTPStatus.NOT_FOUND, "not_found")
        except RequestAborted:
            return
        except ValueError as exc:
            self.write_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_OPTIONS(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != COMMENT_OFFSET_CONFIRM_PATH:
            self.write_error_json(HTTPStatus.NOT_FOUND, "not_found")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.write_comment_offset_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path in {"/api/special-users", "/api/db/special-users/upsert"}:
                self.require_local_and_auth()
                payload = self.read_json_body()
                self.write_json({"ok": True, "special_user": upsert_special_user(payload)})
                return
            if parsed.path == "/api/special-users/detected":
                self.require_local_and_auth()
                payload = self.read_json_body()
                self.write_json({"ok": True, "result": record_special_user_detection(payload)})
                return
            if parsed.path == COMMENT_OFFSET_CONFIRM_PATH:
                self.require_local()
                payload = self.read_json_body()
                result = tracker.confirm_archive_comment_offset(
                    normalize_text(payload.get("lv")),
                    payload.get("offset_seconds"),
                    normalize_text(payload.get("confirm_token")),
                )
                self.write_json({"ok": True, "result": result})
                return
            self.write_error_json(HTTPStatus.NOT_FOUND, "not_found")
        except RequestAborted:
            return
        except ValueError as exc:
            self.write_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def handle_get_special_users(self, query: dict[str, list[str]]) -> None:
        self.require_local_and_auth()
        user_id = first_query_value(query, "user_id")
        with closing(tracker.connect()) as conn:
            if user_id:
                row = conn.execute(
                    """
                    SELECT user_id, label, note, enabled, created_at, updated_at
                    FROM special_users
                    WHERE user_id = ?
                    """,
                    (user_id,),
                ).fetchone()
                self.write_json({"ok": True, "special_user": dict(row) if row else None})
                return
            rows = conn.execute(
                """
                SELECT user_id, label, note, enabled, created_at, updated_at
                FROM special_users
                ORDER BY updated_at DESC, user_id
                LIMIT 200
                """
            ).fetchall()
            self.write_json({"ok": True, "special_users": [dict(row) for row in rows]})

    def handle_get_preview(self, request_path: str) -> None:
        self.require_local()
        target = resolve_preview_file(request_path)
        if target is None or not target.is_file():
            self.write_error_json(HTTPStatus.NOT_FOUND, "preview_not_found")
            return
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix.casefold() in {".html", ".js", ".json", ".css"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def require_local_and_auth(self) -> None:
        self.require_local()
        expected = load_api_key()
        if expected and self.headers.get("X-API-Key", "") != expected:
            self.write_error_json(HTTPStatus.UNAUTHORIZED, "invalid_api_key")
            raise RequestAborted

    def require_local(self) -> None:
        host = str(self.client_address[0])
        if host not in {"127.0.0.1", "::1", "localhost"}:
            self.write_error_json(HTTPStatus.FORBIDDEN, "local_only")
            raise RequestAborted

    def read_json_body(self) -> dict[str, Any]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            self.write_error_json(HTTPStatus.BAD_REQUEST, "invalid_content_length")
            raise RequestAborted from exc
        if size <= 0:
            return {}
        raw = self.rfile.read(size)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self.write_error_json(HTTPStatus.BAD_REQUEST, "invalid_json")
            raise RequestAborted from exc
        if not isinstance(data, dict):
            self.write_error_json(HTTPStatus.BAD_REQUEST, "json_object_required")
            raise RequestAborted
        return data

    def write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(int(status))
        if urlparse(self.path).path == COMMENT_OFFSET_CONFIRM_PATH:
            self.write_comment_offset_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_comment_offset_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")

    def write_error_json(self, status: HTTPStatus, error: str) -> None:
        self.write_json({"ok": False, "error": error}, status)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


class RequestAborted(Exception):
    pass


def preview_html_paths(lv: str) -> list[Path]:
    with closing(tracker.connect()) as conn:
        return tracker.generated_archive_html_paths_for_lv(conn, lv)


def resolve_preview_file(request_path: str) -> Path | None:
    match = PREVIEW_PATH_PATTERN.fullmatch(unquote(request_path))
    if not match:
        return None
    lv, relative = match.groups()
    html_paths = preview_html_paths(lv)
    if not html_paths:
        return None
    relative = str(relative or "mobile").strip("/")
    if relative in {"", "mobile"}:
        candidates = [path for path in html_paths if path.name.casefold().endswith("_mobile.html")]
        return candidates[0] if candidates else None
    if relative in {"pc", "desktop"}:
        candidates = [path for path in html_paths if not path.name.casefold().endswith("_mobile.html")]
        return candidates[0] if candidates else None
    base_dir = html_paths[0].parent.resolve()
    candidate = (base_dir / relative).resolve()
    try:
        candidate.relative_to(base_dir)
    except ValueError:
        return None
    return candidate


def load_api_key() -> str:
    value = os.environ.get("NICONICO_WATCH_API_KEY", "").strip()
    if value:
        return value
    try:
        raw = json.loads(tracker.CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        return ""
    return str(raw.get("intervention_api_key") or raw.get("local_api_key") or "").strip()


def first_query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return str(values[0]).strip() if values else ""


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def parse_int_or_none(value: Any) -> int | None:
    text = normalize_text(value)
    return int(text) if text.isdigit() else None


def upsert_special_user(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = normalize_text(payload.get("user_id") or payload.get("special_user_id"))
    if not user_id:
        raise ValueError("user_id is required")
    label = normalize_text(payload.get("label") or payload.get("special_user_name"))
    note = normalize_text(payload.get("note"))
    enabled = 1 if payload.get("enabled", True) else 0
    if payload.get("dry_run", False):
        return {
            "dry_run": True,
            "user_id": user_id,
            "label": label,
            "note": note,
            "enabled": enabled,
        }
    current_time = tracker.now()
    with closing(tracker.connect()) as conn:
        conn.execute(
            """
            INSERT INTO special_users (user_id, label, note, created_at, updated_at, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                label = CASE WHEN excluded.label != '' THEN excluded.label ELSE special_users.label END,
                note = CASE WHEN excluded.note != '' THEN excluded.note ELSE special_users.note END,
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
            """,
            (user_id, label, note, current_time, current_time, enabled),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT user_id, label, note, enabled, created_at, updated_at
            FROM special_users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else {"user_id": user_id}


def record_special_user_detection(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = normalize_text(payload.get("special_user_id") or payload.get("user_id"))
    if not user_id:
        raise ValueError("special_user_id is required")
    lv = normalize_text(payload.get("lv"))
    broadcaster_id = normalize_text(payload.get("broadcaster_id"))
    broadcaster_name = normalize_text(payload.get("broadcaster_name"))
    comment_no = parse_int_or_none(payload.get("comment_no"))
    comment_text = normalize_text(payload.get("comment_text") or payload.get("text"))
    label = normalize_text(payload.get("special_user_name") or payload.get("label"))
    source = normalize_text(payload.get("source") or "intervention_api")
    note = normalize_text(payload.get("note")) or " ".join(
        part for part in (source, lv, f"no={comment_no}" if comment_no is not None else "", comment_text) if part
    )
    if payload.get("dry_run", False):
        return {
            "dry_run": True,
            "user_id": user_id,
            "lv": lv,
            "broadcaster_id": broadcaster_id,
            "would_link_broadcaster": bool(broadcaster_id),
            "would_record_hit": bool(lv and broadcaster_id),
            "note": note,
        }
    current_time = tracker.now()

    with closing(tracker.connect()) as conn:
        conn.execute(
            """
            INSERT INTO special_users (user_id, label, note, created_at, updated_at, enabled)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                label = CASE WHEN excluded.label != '' THEN excluded.label ELSE special_users.label END,
                note = CASE WHEN excluded.note != '' THEN excluded.note ELSE special_users.note END,
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (user_id, label, note, current_time, current_time),
        )
        linked = False
        if broadcaster_id:
            tracker.auto_link_special_user_broadcaster(
                conn,
                user_id=user_id,
                broadcaster_id=broadcaster_id,
                broadcaster_name=broadcaster_name,
            )
            linked = True
        hit_recorded = False
        if lv and broadcaster_id:
            conn.execute(
                """
                INSERT INTO special_user_broadcast_hits
                    (lv, user_id, broadcaster_id, broadcaster_name, first_comment_no,
                     first_comment_text, first_seen_at, last_seen_at, comment_count,
                     html_upload_requested)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(lv, user_id, broadcaster_id) DO UPDATE SET
                    broadcaster_name = CASE
                        WHEN excluded.broadcaster_name != '' THEN excluded.broadcaster_name
                        ELSE special_user_broadcast_hits.broadcaster_name
                    END,
                    last_seen_at = excluded.last_seen_at,
                    comment_count = special_user_broadcast_hits.comment_count + 1,
                    html_upload_requested = MAX(
                        special_user_broadcast_hits.html_upload_requested,
                        excluded.html_upload_requested
                    )
                """,
                (
                    lv,
                    user_id,
                    broadcaster_id,
                    broadcaster_name,
                    comment_no,
                    comment_text,
                    current_time,
                    current_time,
                    1 if payload.get("html_upload_requested", False) else 0,
                ),
            )
            hit_recorded = True
        conn.commit()
    return {
        "user_id": user_id,
        "lv": lv,
        "broadcaster_id": broadcaster_id,
        "linked_broadcaster": linked,
        "hit_recorded": hit_recorded,
    }


if __name__ == "__main__":
    raise SystemExit(run())

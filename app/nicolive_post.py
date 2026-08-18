from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websockets

import tracker
from niconico_ids import extract_nicolive_id


ROOT = Path(__file__).resolve().parent
DB_PATH = tracker.DB_PATH


class NicolivePostError(RuntimeError):
    pass


@dataclass(frozen=True)
class NicolivePageData:
    live_id: str
    title: str
    broadcaster_id: str | None
    broadcaster_name: str | None
    websocket_url: str
    begin_time: int
    end_time: int
    is_logged_in: bool
    login_user_name: str | None


@dataclass(frozen=True)
class MessageServerData:
    view_uri: str
    vpos_base_time_ms: int
    hashed_user_id: str | None


def load_latest_user_session(db_path: Path = DB_PATH) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT user_session
            FROM niconico_sessions
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if not row or not str(row[0]).strip():
        raise NicolivePostError("保存済み user_session が見つからない")
    return str(row[0]).strip()


def fetch_page_data(live_id_or_url: str, user_session: str) -> NicolivePageData:
    live_id = extract_nicolive_id(live_id_or_url)
    if not live_id:
        raise NicolivePostError(f"有効な放送IDを含んでいない: {live_id_or_url}")

    response = requests.get(
        f"https://live.nicovideo.jp/watch/{live_id}",
        headers={
            "Cookie": f"user_session={user_session}",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
        },
        timeout=20,
    )
    response.raise_for_status()

    match = re.search(
        r'<script[^>]+id=["\']embedded-data["\'][^>]+data-props=["\']([^"\']+)["\']',
        response.text,
    )
    if not match:
        raise NicolivePostError("embedded-data の data-props が見つからない")

    props = json.loads(html.unescape(match.group(1)))
    site = props.get("site") or {}
    relive = site.get("relive") or {}
    program = props.get("program") or {}
    social_group = props.get("socialGroup") or {}
    supplier = program.get("supplier") or {}
    user = props.get("user") or {}
    provider_type = str(program.get("providerType") or "")
    if provider_type == "community":
        broadcaster_id = _string_or_none(supplier.get("programProviderId"))
        broadcaster_name = _string_or_none(supplier.get("name"))
    else:
        broadcaster_id = _string_or_none(social_group.get("id"))
        broadcaster_name = _string_or_none(social_group.get("name"))
    websocket_url = str(relive.get("webSocketUrl") or "")
    if not websocket_url:
        raise NicolivePostError("認証済み webSocketUrl が見つからない")

    return NicolivePageData(
        live_id=str(program.get("nicoliveProgramId") or live_id),
        title=str(program.get("title") or ""),
        broadcaster_id=broadcaster_id,
        broadcaster_name=broadcaster_name,
        websocket_url=websocket_url,
        begin_time=int(program.get("beginTime") or -1),
        end_time=int(program.get("endTime") or -1),
        is_logged_in=bool(user.get("isLoggedIn")),
        login_user_name=user.get("nickname"),
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


async def _wait_message_server(ws: Any, timeout_sec: float) -> MessageServerData:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
        event = json.loads(raw)
        event_type = event.get("type")
        if event_type == "ping":
            await ws.send(json.dumps({"type": "pong"}, ensure_ascii=False))
            await ws.send(json.dumps({"type": "keepSeat"}, ensure_ascii=False))
        elif event_type == "messageServer":
            data = event.get("data") or {}
            base_time = data.get("vposBaseTime")
            if not data.get("viewUri") or not base_time:
                raise NicolivePostError(f"messageServer の形式が想定外: {data}")
            return MessageServerData(
                view_uri=str(data["viewUri"]),
                vpos_base_time_ms=_parse_iso_time_ms(str(base_time)),
                hashed_user_id=data.get("hashedUserId"),
            )
        elif event_type == "disconnect":
            raise NicolivePostError(f"WebSocket disconnected: {event.get('data')}")
    raise NicolivePostError("messageServer の受信がタイムアウトした")


def _parse_iso_time_ms(value: str) -> int:
    from datetime import datetime

    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp() * 1000)


async def post_comment_async(
    live_id_or_url: str,
    text: str,
    *,
    user_session: str | None = None,
    is_anonymous: bool = True,
    dry_run: bool = True,
    wait_after_send_sec: float = 3.0,
) -> dict[str, Any]:
    session = user_session or load_latest_user_session()
    page_data = fetch_page_data(live_id_or_url, session)
    if not page_data.is_logged_in:
        raise NicolivePostError("ログイン状態のページデータではない")

    async with websockets.connect(page_data.websocket_url) as ws:
        await ws.send(json.dumps({"type": "startWatching", "data": {"reconnect": False}}, ensure_ascii=False))
        message_server = await _wait_message_server(ws, timeout_sec=15)
        vpos = round((int(time.time() * 1000) - message_server.vpos_base_time_ms) / 10)
        payload = {
            "type": "postComment",
            "data": {
                "text": text,
                "isAnonymous": is_anonymous,
                "vpos": vpos,
            },
        }
        if dry_run:
            return {
                "dry_run": True,
                "live_id": page_data.live_id,
                "title": page_data.title,
                "broadcaster_id": page_data.broadcaster_id,
                "broadcaster_name": page_data.broadcaster_name,
                "login_user_name": page_data.login_user_name,
                "message_server_view_uri": message_server.view_uri,
                "hashed_user_id": message_server.hashed_user_id,
                "payload": payload,
            }

        await ws.send(json.dumps(payload, ensure_ascii=False))
        events: list[dict[str, Any]] = []
        deadline = time.monotonic() + wait_after_send_sec
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            events.append(event)
            if event.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}, ensure_ascii=False))
                await ws.send(json.dumps({"type": "keepSeat"}, ensure_ascii=False))
            if event.get("type") in {"postCommentResult", "error", "disconnect"}:
                break
        return {
            "dry_run": False,
            "live_id": page_data.live_id,
            "title": page_data.title,
            "broadcaster_id": page_data.broadcaster_id,
            "broadcaster_name": page_data.broadcaster_name,
            "login_user_name": page_data.login_user_name,
            "message_server_view_uri": message_server.view_uri,
            "hashed_user_id": message_server.hashed_user_id,
            "payload": payload,
            "events": events,
        }


def post_comment(live_id_or_url: str, text: str, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(post_comment_async(live_id_or_url, text, **kwargs))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("live")
    parser.add_argument("text")
    parser.add_argument("--send", action="store_true", help="実際にコメントを送信する")
    parser.add_argument("--anonymous", action="store_true", default=True)
    parser.add_argument("--no-anonymous", action="store_false", dest="anonymous")
    args = parser.parse_args()

    result = post_comment(
        args.live,
        args.text,
        dry_run=not args.send,
        is_anonymous=args.anonymous,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

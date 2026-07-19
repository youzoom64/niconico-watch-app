from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
import sys
from typing import Any


class RawComment(dict[str, Any]):
    pass


class NDGRCommentSource:
    async def stream(self, *, lv: str, stop_event: asyncio.Event) -> AsyncIterator[RawComment]:
        try:
            from ndgr_client import NDGRClient  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "NDGRClient is not available in this Python environment. "
                f"Python={sys.version.split()[0]}; import error={type(exc).__name__}: {exc}"
            ) from exc

        try:
            client = NDGRClient(lv, verbose=False, console_output=False)
        except Exception as exc:
            raise RuntimeError(f"NDGRClient initialization failed: {exc}") from exc

        if not hasattr(client, "streamComments"):
            raise RuntimeError("NDGRClient has no supported streamComments() method.")

        try:
            async for item in client.streamComments():
                if stop_event.is_set():
                    break
                yield RawComment(item if isinstance(item, dict) else _ndgr_comment_to_raw(item))
        except Exception as exc:
            raise RuntimeError(f"NDGR comment stream failed: {exc}") from exc
        finally:
            httpx_client = getattr(client, "httpx_client", None)
            close = getattr(httpx_client, "aclose", None)
            if close:
                await close()


def _ndgr_comment_to_raw(comment: Any) -> dict[str, Any]:
    at = getattr(comment, "at", None)
    raw_user_id = getattr(comment, "raw_user_id", None)
    hashed_user_id = getattr(comment, "hashed_user_id", None)
    account_status = getattr(comment, "account_status", None)
    user_id = str(raw_user_id) if raw_user_id not in {None, 0, "0", ""} else str(hashed_user_id or "anonymous")

    return {
        "source": "ndgr",
        "no": getattr(comment, "no", None),
        "comment_id": getattr(comment, "id", None),
        "live_id": getattr(comment, "live_id", None),
        "user_id": user_id,
        "raw_user_id": raw_user_id,
        "hashed_user_id": hashed_user_id,
        "text": getattr(comment, "content", None) or "",
        "vpos": getattr(comment, "vpos", None),
        "posted_at": at.isoformat() if isinstance(at, datetime) else None,
        "is_premium": account_status == "Premium" if account_status is not None else None,
        "is_anonymous": raw_user_id in {None, 0, "0", ""},
        "account_status": account_status,
        "mail": " ".join(
            str(value)
            for value in (
                getattr(comment, "position", None),
                getattr(comment, "size", None),
                getattr(comment, "color", None),
                getattr(comment, "font", None),
            )
            if value
        )
        or None,
        "received_at": datetime.now().isoformat(timespec="seconds"),
    }

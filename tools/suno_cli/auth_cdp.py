"""Suno CLI専用ChromeからCDP経由で認証を取り込む。"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

import websocket


PORT = 9448
SUNO_EXE = Path(__file__).resolve().with_name("suno.exe")
STATUS_FILE = Path(__file__).resolve().with_name("auth_cdp_status.json")


def finish(payload: dict, exit_code: int) -> int:
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


def main() -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=5) as response:
            version = json.loads(response.read().decode("utf-8"))
        ws_url = str(version.get("webSocketDebuggerUrl") or "")
        if not ws_url:
            raise RuntimeError("認証用ChromeのCDP接続先が見つかりません")
        socket = websocket.create_connection(ws_url, timeout=10, origin="http://127.0.0.1")
        try:
            socket.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while True:
                message = json.loads(socket.recv())
                if message.get("id") == 1:
                    break
        finally:
            socket.close()
        if message.get("error"):
            raise RuntimeError(str(message["error"].get("message") or message["error"]))
        cookies = message.get("result", {}).get("cookies", [])
        suno_cookies = [row for row in cookies if "suno.com" in str(row.get("domain") or "")]
        clients = [row for row in suno_cookies if row.get("name") == "__client" and row.get("value")]
        if not clients:
            raise RuntimeError("認証用ChromeでSunoへログインしてください")
        preferred = next(
            (row for row in clients if "auth.suno.com" in str(row.get("domain") or "")), clients[0]
        )
        parts = []
        seen = set()
        for row in [preferred, *suno_cookies]:
            name = str(row.get("name") or "")
            value = str(row.get("value") or "")
            if name and value and name not in seen:
                seen.add(name)
                parts.append(f"{name}={value}")
        result = subprocess.run(
            [str(SUNO_EXE), "auth", "--cookie", "; ".join(parts)],
            cwd=str(SUNO_EXE.parent), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError("suno-cliがSunoセッションを受理しませんでした")
        return finish({"ok": True, "source": "dedicated_chrome_cdp"}, 0)
    except BaseException as exc:
        return finish({"ok": False, "error_type": type(exc).__name__, "error": str(exc)}, 1)


if __name__ == "__main__":
    raise SystemExit(main())

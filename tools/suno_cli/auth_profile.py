"""選択したChromeプロファイルからSuno認証を取り込む管理者用補助。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import rookiepy


ROOT = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
SUNO_EXE = Path(__file__).resolve().with_name("suno.exe")
STATUS_FILE = Path(__file__).resolve().with_name("auth_profile_status.json")


def finish(payload: dict, exit_code: int) -> int:
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


def main() -> int:
    if len(sys.argv) != 2:
        return finish({"ok": False, "error": "profile is required"}, 2)
    profile = sys.argv[1]
    if profile != "Default" and not profile.startswith("Profile "):
        return finish({"ok": False, "error": "invalid profile"}, 2)
    cookie_db = ROOT / profile / "Network" / "Cookies"
    local_state = ROOT / "Local State"
    if not cookie_db.is_file() or not local_state.is_file():
        return finish({"ok": False, "error": "Chrome profile files not found"}, 2)
    try:
        cookies = rookiepy.chromium_based(
            str(local_state), str(cookie_db), ["suno.com", "auth.suno.com", "clerk.suno.com"]
        )
        suno_cookies = [row for row in cookies if "suno.com" in str(row.get("domain") or "")]
        clients = [row for row in suno_cookies if row.get("name") == "__client" and row.get("value")]
        if not clients:
            raise RuntimeError("selected profile has no usable Suno __client cookie")
        preferred = next(
            (row for row in clients if "auth.suno.com" in str(row.get("domain") or "")),
            clients[0],
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
            raise RuntimeError("suno-cli rejected the selected browser session")
        return finish({"ok": True, "profile": profile}, 0)
    except BaseException as exc:
        return finish({
            "ok": False,
            "profile": profile,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }, 1)


if __name__ == "__main__":
    raise SystemExit(main())

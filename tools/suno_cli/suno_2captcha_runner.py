"""Run suno.exe and solve generation hCaptcha through 2Captcha when required."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SUNO_EXE = ROOT / "suno.exe"
SHARED_ENV = Path(os.environ["NICONICO_SHARED_ENV"]) if os.environ.get("NICONICO_SHARED_ENV") else None
SITE_KEY = "d65453de-3f1a-4aac-9366-a0f06e52b2ce"
WEBSITE_URL = "https://suno.com/create"
CREATE_TASK_URL = "https://api.2captcha.com/createTask"
GET_RESULT_URL = "https://api.2captcha.com/getTaskResult"
# 2Captcha側のhCaptcha解決が3分を超えることがあるため、待ち時間は7分まで許容する。
SOLVE_POLL_INTERVAL = 5
SOLVE_POLL_ATTEMPTS = 84


def load_api_key() -> str:
    value = os.environ.get("TWOCAPTCHA_API_KEY", "").strip()
    if value:
        return value
    if SHARED_ENV is not None and SHARED_ENV.is_file():
        for raw_line in SHARED_ENV.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == "TWOCAPTCHA_API_KEY":
                return value.strip().strip("\"'")
    return ""


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"2Captcha通信失敗: {exc}") from exc


def captcha_required() -> bool:
    result = subprocess.run(
        [str(SUNO_EXE), "doctor", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    try:
        payload = json.loads(result.stdout)
        checks = payload.get("data", {}).get("checks", [])
        check = next(row for row in checks if row.get("name") == "captcha_preflight")
        detail = str(check.get("detail") or "").lower()
        return "required: true" in detail
    except (json.JSONDecodeError, StopIteration, AttributeError):
        return False


def solve_hcaptcha(api_key: str) -> str:
    created = post_json(
        CREATE_TASK_URL,
        {
            "clientKey": api_key,
            "task": {
                "type": "HCaptchaTaskProxyless",
                "websiteURL": WEBSITE_URL,
                "websiteKey": SITE_KEY,
                "isInvisible": True,
            },
        },
    )
    if created.get("errorId"):
        raise RuntimeError(
            f"2Captcha受付失敗: {created.get('errorCode', 'UNKNOWN')}: "
            f"{created.get('errorDescription', '')}"
        )
    task_id = created.get("taskId")
    if not task_id:
        raise RuntimeError("2CaptchaからtaskIdが返りませんでした")

    for _ in range(SOLVE_POLL_ATTEMPTS):
        time.sleep(SOLVE_POLL_INTERVAL)
        result = post_json(GET_RESULT_URL, {"clientKey": api_key, "taskId": task_id})
        if result.get("errorId"):
            raise RuntimeError(
                f"2Captcha解決失敗: {result.get('errorCode', 'UNKNOWN')}: "
                f"{result.get('errorDescription', '')}"
            )
        if result.get("status") != "ready":
            continue
        solution = result.get("solution") or {}
        token = str(solution.get("gRecaptchaResponse") or solution.get("token") or "").strip()
        if not token:
            raise RuntimeError("2Captchaから空のトークンが返りました")
        return token
    raise RuntimeError(
        f"2Captchaの解決が{SOLVE_POLL_ATTEMPTS * SOLVE_POLL_INTERVAL}秒以内に完了しませんでした"
    )


CHROME_PROFILE = ROOT / "chrome_profile"
BROWSER_CDP_PORT = 9451
# ヘッドレスの既定UAには "HeadlessChrome" が入り、hCaptchaが challenge-expired で弾く。
# 通常Chromeを名乗るだけで通るため、ここを必ず差し替える。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
BROWSER_TOKEN_JS = """
async () => {
  const SITEKEY = '%s';
  if (typeof window.hcaptcha === 'undefined') {
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = 'https://js.hcaptcha.com/1/api.js?render=explicit&onload=__hcReady';
      window.__hcReady = () => res();
      s.onerror = () => rej(new Error('hcaptcha script load blocked'));
      document.head.appendChild(s);
      setTimeout(() => rej(new Error('hcaptcha script timeout')), 20000);
    });
  }
  const box = document.createElement('div');
  document.body.appendChild(box);
  const id = window.hcaptcha.render(box, { sitekey: SITEKEY, size: 'invisible' });
  const r = await window.hcaptcha.execute(id, { async: true });
  return (r && r.response) || window.hcaptcha.getResponse(id) || '';
}
""" % SITE_KEY


def find_chrome() -> Path | None:
    candidates = []
    configured = str(os.environ.get("NICONICO_CHROME_PATH") or "").strip()
    if configured:
        candidates.append(Path(configured))
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = str(os.environ.get(variable) or "").strip()
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return next((c for c in candidates if c.is_file()), None)


def cdp_page_websocket(port: int, timeout_seconds: int = 40) -> str:
    """suno.comを開いているページのWebSocket URLを返す。"""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                targets = json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(1)
            continue
        for t in targets:
            if t.get("type") == "page" and "suno.com" in str(t.get("url") or ""):
                return str(t.get("webSocketDebuggerUrl") or "")
        time.sleep(1)
    raise RuntimeError("suno.comのCDPページが見つかりません")


def evaluate_browser_token(ws_url: str, websocket) -> str:
    """CDPのRuntime.evaluateでhCaptchaを通し、トークンを1つ取る。"""
    ws = websocket.create_connection(ws_url, timeout=120)
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": f"({BROWSER_TOKEN_JS})()",
                "awaitPromise": True,
                "returnByValue": True,
            },
        }))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise RuntimeError(f"CDPエラー: {message['error']}")
            result = message.get("result", {})
            if result.get("exceptionDetails"):
                detail = result["exceptionDetails"].get("text") or ""
                exc = (result["exceptionDetails"].get("exception") or {}).get("description") or ""
                raise RuntimeError(f"ブラウザ内で失敗: {detail} {exc}".strip())
            token = str((result.get("result") or {}).get("value") or "").strip()
            if not token:
                raise RuntimeError("ブラウザから空のトークンが返りました")
            return token
    finally:
        ws.close()


def solve_hcaptcha_with_browser() -> str:
    """ログイン済みプロファイルのChromeでhCaptchaを通し、トークンを取る。

    2Captchaのワーカーはこの方式(hsw)のcaptchaを解けない。実ブラウザの
    評判で通す門なので、こちらのプロファイルで取るのが正攻法。
    """
    import websocket  # websocket-client

    chrome = find_chrome()
    if chrome is None:
        raise RuntimeError("Chromeが見つかりません")
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    command = [
        str(chrome),
        f"--user-data-dir={CHROME_PROFILE}",
        f"--remote-debugging-port={BROWSER_CDP_PORT}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        "--headless=new",
        "--window-size=1280,900",
        f"--user-agent={BROWSER_UA}",
        "https://suno.com/create",
    ]
    process = subprocess.Popen(
        command,
        cwd=str(CHROME_PROFILE),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        cdp_page_websocket(BROWSER_CDP_PORT)
        # suno.comはクライアント側で遷移するため、直後に評価すると
        # "Execution context was destroyed" になる。落ち着くまで待って再試行する。
        last_error: BaseException | None = None
        for attempt in range(1, 4):
            time.sleep(6 if attempt == 1 else 4)
            try:
                return evaluate_browser_token(
                    cdp_page_websocket(BROWSER_CDP_PORT), websocket
                )
            except BaseException as exc:
                last_error = exc
                print(
                    f"ブラウザ取得リトライ {attempt}/3: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                    flush=True,
                )
        raise RuntimeError(f"ブラウザからトークンを取得できません: {last_error}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def audio_urls(payload: object) -> list[str]:
    found: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate = value.get("audio_url")
            if isinstance(candidate, str) and candidate.startswith(("https://", "http://")):
                if candidate not in found:
                    found.append(candidate)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return found


def run_suno(args: list[str]) -> int:
    process = subprocess.run(
        [str(SUNO_EXE), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.stdout:
        print(process.stdout, end="", flush=True)
    if process.stderr:
        print(process.stderr, end="", file=sys.stderr, flush=True)
    if process.returncode != 0:
        return process.returncode
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        return process.returncode
    urls = audio_urls(payload)
    if os.environ.get("SUNO_OPEN_AUDIO_URLS", "1") != "0":
        for url in urls:
            webbrowser.open_new_tab(url)
        if urls:
            print(f"生MP3をブラウザで開きました: {len(urls)}件", flush=True)
    return process.returncode


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("suno引数がありません", file=sys.stderr)
        return 2
    try:
        if args[0] in {"generate", "describe", "extend", "cover", "remaster"} and captcha_required():
            token = ""
            # まずログイン済みブラウザで取る。2Captchaはこの方式を解けないため保険扱い。
            print("CAPTCHA要求を検出: ブラウザで解決中...", file=sys.stderr, flush=True)
            try:
                token = solve_hcaptcha_with_browser()
                print("ブラウザ解決完了: Sunoへ送信します", file=sys.stderr, flush=True)
            except BaseException as exc:
                print(f"ブラウザ解決失敗: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if not token:
                api_key = load_api_key()
                if not api_key:
                    raise RuntimeError("共通envにTWOCAPTCHA_API_KEYがありません")
                print("2Captchaへ解決依頼中...", file=sys.stderr, flush=True)
                token = solve_hcaptcha(api_key)
                print("2Captcha解決完了: Sunoへ送信します", file=sys.stderr, flush=True)
            args += ["--token", token]
        return run_suno(args)
    except BaseException as exc:
        print(f"エラー: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

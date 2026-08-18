import os
import json
import requests
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from archive_db import load_broadcast_data as load_broadcast_data_from_db, update_broadcast_data

APP_ROOT = Path(__file__).resolve().parents[2]
SUNO_CLI_DIR = Path(
    os.environ.get("NICONICO_SUNO_CLI_DIR", str(APP_ROOT / "tools" / "suno_cli"))
).expanduser()
SUNO_RUNNER = SUNO_CLI_DIR / "suno_2captcha_runner.py"
SUNO_AUTH_SCRIPT = SUNO_CLI_DIR / "auth_cdp.py"
SUNO_AUTH_STATUS = SUNO_CLI_DIR / "auth_cdp_status.json"
SUNO_CHROME_PROFILE = SUNO_CLI_DIR / "chrome_profile"
SUNO_PYTHON = Path(os.environ.get("NICONICO_SUNO_PYTHON", sys.executable)).expanduser()
SUNO_SHARED_ENV = Path(
    os.environ.get("NICONICO_WATCH_SHARED_ENV", str(APP_ROOT / ".env"))
).expanduser()
SUNO_CDP_PORT = 9448
SUNO_CDP_WAIT_SECONDS = 40
SUNO_PAGE_WAIT_SECONDS = 8
SUNO_CHROME_CLOSE_WAIT_SECONDS = 5

# 直近の失敗理由。生成関数がNoneを返した理由をprocess()へ伝える。
LAST_FAILURE = {}


def find_chrome_executable():
    configured = str(os.environ.get("NICONICO_CHROME_PATH") or "").strip()
    candidates = [Path(configured).expanduser()] if configured else []
    discovered = shutil.which("chrome") or shutil.which("chrome.exe")
    if discovered:
        candidates.append(Path(discovered))
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = str(os.environ.get(variable) or "").strip()
        if root:
            candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def clear_failure():
    LAST_FAILURE.clear()


def set_failure(reason, detail=""):
    """失敗理由を記録する。detailは長くなりがちなので末尾を切る。"""
    text = " ".join(str(detail or "").split())
    LAST_FAILURE.clear()
    LAST_FAILURE.update({"reason": reason, "detail": text[-800:]})
    return None


def classify_cli_failure(stderr, returncode):
    """runnerのstderrから、対処が変わる失敗だけを見分ける。"""
    text = str(stderr or "")
    lowered = text.lower()
    if "zero_balance" in lowered:
        return "twocaptcha_zero_balance"
    if "ログインしてください" in text or "unauthorized" in lowered or "401" in text:
        return "suno_auth_expired"
    captcha_failure_markers = (
        "2captcha受付失敗",
        "2captcha解決失敗",
        "2captcha通信失敗",
        "2captchaからtaskid",
        "2captchaから空のトークン",
        "2captchaの解決が",
    )
    if any(marker in lowered for marker in captcha_failure_markers):
        return "captcha_failed"
    return f"cli_exit_{returncode}"


# CAPTCHA待ちやCLIの一時的な失敗は次の再処理まで待たず、その場でもう一度だけ試す。
RETRYABLE_FAILURES = {"captcha_failed", "twocaptcha_failed"}
SUBSCRIPTION_RETRY_WAIT_SECONDS = (15, 30, 60)


def is_retryable_failure(reason):
    text = str(reason or "")
    return text in RETRYABLE_FAILURES or text.startswith("cli_exit_")


def generate_music_with_subscription_retry(generation_args, lv_value, attempts=4):
    """サブスク生成を、一時的な失敗に限って指定回数まで試す。"""
    for attempt in range(1, attempts + 1):
        result = generate_music_with_subscription(**generation_args, lv_value=lv_value)
        if result:
            return result
        reason = LAST_FAILURE.get("reason")
        if attempt >= attempts or not is_retryable_failure(reason):
            return None
        wait_seconds = SUBSCRIPTION_RETRY_WAIT_SECONDS[
            min(attempt - 1, len(SUBSCRIPTION_RETRY_WAIT_SECONDS) - 1)
        ]
        print(
            f"Sunoサブスク生成リトライ: {attempt}/{attempts} 失敗 [{reason}] "
            f"{wait_seconds}秒後に再試行します"
        )
        time.sleep(wait_seconds)
    return None


def process(pipeline_data):
    """Step06: AI音楽生成"""
    try:
        lv_value = pipeline_data['lv_value']
        config = pipeline_data['config']
        
        print(f"Step06 開始: {lv_value}")
        clear_failure()

        # 1. AI音楽生成機能が有効か確認
        if not config["ai_features"].get("enable_ai_music", False):
            print("AI音楽生成機能が無効です。処理をスキップします。")
            return {"music_generated": False, "reason": "feature_disabled"}
        
        # 2. 放送データDB読み込み
        broadcast_data = load_broadcast_data(lv_value)
        
        # 3. 要約テキストの確認
        summary_text = broadcast_data.get('summary_text', '')
        if not summary_text.strip():
            print("要約テキストが見つかりません。音楽生成をスキップします。")
            return {"music_generated": False, "reason": "no_summary"}
        
        # 4. 生成方法と認証設定を確認
        music_settings = config.get("music_settings", {})
        provider = str(music_settings.get("provider", "api") or "api").lower()
        suno_api_key = config["api_settings"].get("suno_api_key", "")
        if provider == "api" and not suno_api_key:
            print("Suno API Keyが設定されていません。音楽生成をスキップします。")
            return {"music_generated": False, "reason": "no_api_key"}
        
        # 5. 音楽生成
        generation_args = {
            "title": broadcast_data.get('live_title', 'タイトル不明'),
            "summary": summary_text,
            "style": music_settings.get("style", "J-Pop, Upbeat"),
            "model": music_settings.get("model", "V4"),
            "instrumental": music_settings.get("instrumental", False),
            "prompt_instruction": config.get("ai_prompts", {}).get("music_prompt", ""),
        }
        if provider == "subscription":
            music_result = generate_music_with_subscription_retry(
                generation_args,
                lv_value=lv_value,
            )
        else:
            music_result = generate_music_from_summary(
                **generation_args,
                api_key=suno_api_key,
            )

        
        if music_result:
            # 6. DBに結果を保存
            save_broadcast_data(lv_value, {"music_generation": music_result})

            print(f"Step06 完了: {lv_value} - 音楽生成成功")
            return {"music_generated": True, "task_id": music_result.get("task_id")}
        else:
            # 6. 失敗理由をDBへ残す。成功済みのmusic_generationは壊さない。
            reason = LAST_FAILURE.get("reason") or "generation_failed"
            detail = LAST_FAILURE.get("detail") or ""
            save_broadcast_data(lv_value, {"music_generation_error": {
                "reason": reason,
                "detail": detail,
                "provider": provider,
                "failed_at": datetime.now().isoformat(),
            }})
            print(f"Step06 完了: {lv_value} - 音楽生成失敗 [{reason}] {detail}")
            return {"music_generated": False, "reason": reason, "detail": detail}
        
    except Exception as e:
        print(f"Step06 エラー: {str(e)}")
        raise

def load_broadcast_data(lv_value):
    """放送データDBを読み込み"""
    broadcast_data = load_broadcast_data_from_db(lv_value)
    if broadcast_data:
        return broadcast_data
    raise Exception(f"放送データDBが見つかりません: {lv_value}")

def save_broadcast_data(lv_value, updates):
    """放送データDBに追記保存"""
    update_broadcast_data(lv_value, updates)

def generate_music_from_summary(
    title,
    summary,
    api_key,
    style="J-Pop",
    model="V4",
    instrumental=False,
    prompt_instruction="",
):
    """要約から音楽を生成"""
    try:
        print(f"音楽生成開始: {title}")
        print(f"要約: {summary[:100]}...")
        
        suno_api = SunoAPI(api_key)
        
        # 要約テキストをそのまま歌詞として使用
        lyrics = create_music_prompt(summary, prompt_instruction)
        
        # 音楽生成実行
        result = suno_api.generate_music(
            prompt=lyrics,
            custom_mode=True,
            instrumental=instrumental,  # ← ここも引数で受け取った値を使用
            model=model,                # ← ここも引数で受け取った値を使用
            style=style,                # ← ここも引数で受け取った値を使用
            title=title
        )
        
        if result:
            return {
                "task_id": result["task_id"],
                "songs": result["songs"],
                "music_prompt": lyrics,
                "generated_at": datetime.now().isoformat(),
                "title": title,
                "status": result.get("status", "generated"),
                "settings": {          # 生成時の設定を記録
                    "style": style,
                    "model": model,
                    "instrumental": instrumental
                }
            }

        return set_failure("api_generation_failed", "SunoAPIから楽曲が返りませんでした")

    except Exception as e:
        print(f"音楽生成エラー: {str(e)}")
        return set_failure("api_error", e)


def create_music_prompt(summary, prompt_instruction=""):
    """要約テキストを歌詞として使用（V4は最大3000文字）"""
    instruction = str(prompt_instruction or "").strip()
    source = f"{instruction}\n\n{summary}".strip() if instruction else summary
    lyrics = source[:3000] if len(source) > 3000 else source
    return lyrics


def normalize_subscription_model(model):
    value = str(model or "").strip().upper().replace(".", "_").replace("-", "_")
    return {
        "V5_5": "v5.5",
        "V5": "v5",
        "V4_5PLUS": "v4.5+",
        "V4_5_ALL": "v4.5-all",
        "V4_5ALL": "v4.5-all",
        "V4_5": "v4.5",
        "V4": "v4",
    }.get(value, "v5.5")


def suno_auth_chrome_pids():
    """認証用プロファイルのchrome.exeのPIDだけを返す。通常のChromeは対象外。"""
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{SUNO_CHROME_PROFILE}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(value) for value in result.stdout.split() if value.strip().isdigit()]


def suno_cdp_alive():
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{SUNO_CDP_PORT}/json/version", timeout=3
        ) as response:
            json.loads(response.read().decode("utf-8"))
        return True
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError):
        return False


def taskkill(pid, force=False):
    """taskkillはCP932で出力するため、デコード失敗で落ちないようにする。"""
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def close_suno_auth_chrome():
    """認証用Chromeを明示的に閉じる。まず通常終了、残ったら強制終了。"""
    pids = suno_auth_chrome_pids()
    if not pids:
        return True
    for pid in pids:
        taskkill(pid)
    for _ in range(SUNO_CHROME_CLOSE_WAIT_SECONDS):
        time.sleep(1)
        if not suno_auth_chrome_pids() and not suno_cdp_alive():
            return True
    leftovers = suno_auth_chrome_pids()
    if leftovers:
        for pid in leftovers:
            taskkill(pid, force=True)
        time.sleep(2)
    remaining = suno_auth_chrome_pids()
    if remaining:
        print(f"認証用Chromeが終了しません: {remaining}")
        return False
    return True


def suno_auth_failure_detail():
    if not SUNO_AUTH_STATUS.is_file():
        return ""
    try:
        payload = json.loads(SUNO_AUTH_STATUS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("error") or "")


def refresh_suno_subscription_auth():
    """毎回Chromeを開いてSuno認証を取り込み直し、Chromeを明示的に閉じる。"""
    suno_chrome = find_chrome_executable()
    if suno_chrome is None or not SUNO_AUTH_SCRIPT.is_file() or not SUNO_PYTHON.is_file():
        print("Suno認証取り込み環境が見つかりません。既存の認証で続行します。")
        return False

    close_suno_auth_chrome()
    SUNO_CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    # 認証取り込みはStorage.getCookiesでCookieジャーを読むだけなので描画は不要。
    # 既定はヘッドレス。ログインし直しなど画面が要る時だけSUNO_AUTH_HEADED=1。
    headed = os.environ.get("SUNO_AUTH_HEADED", "").strip().lower() in {"1", "true", "yes", "on"}
    command = [
        str(suno_chrome),
        f"--user-data-dir={SUNO_CHROME_PROFILE}",
        f"--remote-debugging-port={SUNO_CDP_PORT}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
    ]
    if not headed:
        command.append("--headless=new")
        command.append("--window-size=1280,900")
    command.append("https://suno.com/")
    try:
        subprocess.Popen(command, cwd=str(SUNO_CHROME_PROFILE))
    except OSError as exc:
        print(f"認証用Chromeを起動できません: {exc}")
        return False

    try:
        for _ in range(SUNO_CDP_WAIT_SECONDS):
            time.sleep(1)
            if suno_cdp_alive():
                break
        else:
            print("認証用ChromeのCDPが応答しません。既存の認証で続行します。")
            return False

        time.sleep(SUNO_PAGE_WAIT_SECONDS)
        result = subprocess.run(
            [str(SUNO_PYTHON), str(SUNO_AUTH_SCRIPT)],
            cwd=str(SUNO_AUTH_SCRIPT.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode == 0:
            print("Suno認証を取り込みました。")
            return True
        detail = suno_auth_failure_detail() or (result.stderr or "").strip()
        print(f"Suno認証の取り込みに失敗しました: {detail or 'unknown'}")
        return False
    except subprocess.SubprocessError as exc:
        print(f"Suno認証の取り込みに失敗しました: {exc}")
        return False
    finally:
        close_suno_auth_chrome()


def generate_music_with_subscription(
    title,
    summary,
    lv_value,
    style="J-Pop",
    model="V5_5",
    instrumental=False,
    prompt_instruction="",
):
    """Sunoサブスク認証を使い、既存CLI経由で生成する。"""
    runner = SUNO_RUNNER
    python_exe = SUNO_PYTHON
    shared_env = SUNO_SHARED_ENV
    if not runner.is_file() or not python_exe.is_file():
        print("Sunoサブスク実行環境が見つかりません。")
        return set_failure("environment_missing", f"{runner} / {python_exe}")
    has_2captcha_key = bool(os.environ.get("TWOCAPTCHA_API_KEY", "").strip())
    if not has_2captcha_key and shared_env.is_file():
        has_2captcha_key = any(
            line.split("=", 1)[0].strip() == "TWOCAPTCHA_API_KEY"
            and bool(line.split("=", 1)[1].strip().strip("\"'"))
            for line in shared_env.read_text(encoding="utf-8-sig").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        )
    if not has_2captcha_key:
        print("Sunoサブスクには2Captcha APIキーが必要です。設定の認証情報へ入力してください。")
        return set_failure("no_2captcha_key", "共通envにTWOCAPTCHA_API_KEYがありません")

    # 認証取り込みに失敗しても既存認証で通る可能性があるため、理由だけ控えて続行する。
    auth_detail = "" if refresh_suno_subscription_auth() else (
        suno_auth_failure_detail() or "認証取り込みに失敗"
    )

    lyrics = create_music_prompt(summary, prompt_instruction)
    output_dir = APP_ROOT / "storage" / "suno" / str(lv_value)
    command = [
        str(python_exe), str(runner), "generate",
        "--title", str(title),
        "--tags", str(style),
        "--lyrics", lyrics,
        "--model", normalize_subscription_model(model),
        "--wait",
        "--download", str(output_dir),
        "--json",
    ]
    if instrumental:
        command.append("--instrumental")

    env = os.environ.copy()
    env["SUNO_OPEN_AUDIO_URLS"] = "0"
    print(f"Sunoサブスク生成開始: {title}")
    result = subprocess.run(
        command,
        cwd=str(runner.parent),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # CAPTCHA待ちが最大7分になったぶん、生成待ちごと入る余裕を持たせる。
        timeout=1200,
    )
    stderr = result.stderr.strip()
    if stderr:
        print(stderr)
    if result.returncode != 0:
        print(f"Sunoサブスク生成失敗: exit={result.returncode}")
        detail = f"{auth_detail} / {stderr}".strip(" /") if auth_detail else stderr
        return set_failure(classify_cli_failure(stderr, result.returncode), detail)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"Sunoサブスク結果の解析失敗: {exc}")
        return set_failure("bad_cli_output", f"{exc}: {result.stdout[:300]}")
    clips = payload.get("data", []) if isinstance(payload, dict) else []
    if isinstance(clips, dict):
        clips = clips.get("clips") or clips.get("songs") or []
    songs = []
    for clip in clips if isinstance(clips, list) else []:
        if not isinstance(clip, dict):
            continue
        audio_url = str(clip.get("audio_url") or "").strip()
        if not audio_url:
            continue
        songs.append({
            "id": clip.get("id"),
            "title": clip.get("title") or title,
            "duration": clip.get("duration"),
            "urls": [audio_url],
            "primary_url": audio_url,
            "image_url": clip.get("image_url"),
            "tags": clip.get("tags") or style,
            "model": clip.get("model_name") or normalize_subscription_model(model),
        })
    if not songs:
        print("Sunoサブスク生成結果に音声URLがありません。")
        return set_failure("no_audio_url", result.stdout[:300])
    return {
        "task_id": str(songs[0].get("id") or ""),
        "songs": songs,
        "music_prompt": lyrics,
        "generated_at": datetime.now().isoformat(),
        "title": title,
        "status": "ready",
        "provider": "subscription",
        "settings": {
            "style": style,
            "model": model,
            "instrumental": instrumental,
        },
    }

class SunoAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.sunoapi.org/api/v1"
        self.generate_url = f"{self.base_url}/generate"
        self.details_url = f"{self.base_url}/generate/record-info"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self.last_request_time = 0
    
    def _rate_limit(self):
        """レート制限: 0.5秒間隔"""
        current_time = time.time()
        if current_time - self.last_request_time < 0.5:
            time.sleep(0.5)
        self.last_request_time = time.time()
    
    def generate_music(self, prompt, custom_mode=False, instrumental=False, 
                      model="V4", style=None, title=None):
        """音楽生成"""
        self._rate_limit()
        
        data = {
            "customMode": custom_mode,
            "instrumental": instrumental,
            "model": model,
            "prompt": prompt,
            "callBackUrl": "https://example.com/callback"
        }
        
        if custom_mode and style:
            data["style"] = style
        if custom_mode and title:
            data["title"] = title
        
        try:
            response = requests.post(
                self.generate_url,
                headers=self.headers,
                json=data,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("code") != 200 or not result.get("data"):
                    print(
                        "API did not return taskId properly: "
                        f"code={result.get('code')} msg={result.get('msg') or result.get('message')} "
                        f"data={result.get('data')}"
                    )
                    return None
                    
                task_id = result["data"]["taskId"]
                print(f"音楽生成開始 - TaskID: {task_id}")
                
                # 完了まで待機してURLを取得
                songs = self._wait_for_completion(task_id)
                if songs:
                    return {
                        "task_id": task_id,
                        "songs": songs,
                        "status": "ready"
                    }
                return None
                
            elif response.status_code == 429:
                print("クレジット不足")
                return None
            elif response.status_code == 430:
                print("リクエスト頻度過多")
                return None
            else:
                print(f"API エラー {response.status_code}: {response.text}")
                return None
                
        except Exception as e:
            print(f"リクエスト失敗: {e}")
            return None
    
    def _wait_for_completion(self, task_id):
        """タスク完了まで待機して楽曲情報を取得"""
        print("生成を待機中...")
        
        for attempt in range(24):  # 最大4分待機
            time.sleep(10)
            print(f"   {(attempt+1)*10}秒経過...")
            
            self._rate_limit()
            try:
                response = requests.get(
                    self.details_url,
                    headers=self.headers,
                    params={"taskId": task_id},
                    timeout=30
                )
                
                if response.status_code != 200:
                    print(f"詳細取得エラー: {response.status_code}")
                    continue
                
                details_data = response.json()
                status = details_data.get("data", {}).get("status")
                print(f"現在のステータス: {status}")
                
                if status == "SUCCESS":
                    print("生成完了!")
                    songs = self._extract_valid_songs(details_data)
                    return songs
                    
                elif status in ["CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED", "CALLBACK_EXCEPTION", "SENSITIVE_WORD_ERROR"]:
                    print(f"タスク失敗: {status}")
                    return None
                    
            except Exception as e:
                print(f"ステータス確認失敗: {e}")
                continue
        
        print("タイムアウト")
        return None
    
    def _extract_valid_songs(self, details_data):
        """楽曲データから有効なURLを持つ楽曲を抽出"""
        response_data = details_data.get("data", {})
        songs = response_data.get("response", {}).get("sunoData", [])
        
        if not songs:
            return []
        
        print(f"{len(songs)}曲が生成されました")
        valid_songs = []
        
        for i, song in enumerate(songs, 1):
            audio_urls = [
                song.get('audioUrl'),
                song.get('sourceAudioUrl'), 
                song.get('streamAudioUrl'),
                song.get('sourceStreamAudioUrl')
            ]
            
            valid_audio_urls = []
            for url in audio_urls:
                if url:
                    try:
                        head_response = requests.head(url, timeout=5)
                        if head_response.status_code == 200:
                            valid_audio_urls.append(url)
                    except:
                        pass
            
            if valid_audio_urls:
                song_info = {
                    'id': song.get('id'),
                    'title': song.get('title'),
                    'duration': song.get('duration'),
                    'urls': valid_audio_urls,
                    'primary_url': valid_audio_urls[0],
                    'image_url': song.get('imageUrl'),
                    'tags': song.get('tags'),
                    'model': song.get('modelName')
                }
                valid_songs.append(song_info)
        
        return valid_songs

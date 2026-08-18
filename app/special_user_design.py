"""スペシャルユーザーページの見た目設定。

編集はブラウザで開くページから行い、保存するとDBに入る。
生成した個別/一覧HTMLには、この設定から作ったCSSを差し込む。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import tracker

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "special_user_design_editor.html"
DESIGN_STYLE_MARKER = "special-user-design"
LINK_PARTICLE_MARKER = "special-user-link-particles"
PAGE_KIND_DETAIL = "detail"
PAGE_KIND_LINK = "link"

DEFAULT_DESIGN: dict[str, Any] = {
    "background": {
        "color": "#000000",
        "scanline": True,
        "scanline_opacity": 0.12,
    },
    "particles": {
        "enabled": True,
        "colors": ["#ff2d55", "#ffd60a"],
        "count": 90,
        "size": 2.4,
        "speed": 0.35,
        "opacity": 0.85,
        "link_enabled": True,
        "link_color": "#ff2d55",
        "link_width": 0.6,
        "link_distance": 130,
        "link_opacity": 0.35,
        "mouse_reaction": True,
    },
    # Step11のリンク一覧が実際に使うParticleJS（左右の炎＋雪）。
    "link_particles": {
        "side_mode": "flame",
        "fire_enabled": True,
        "fire_frequency": 27,
        "fire_scale": 2.0,
        "fire_life": 200,
        "fire_acceleration": 0.2265,
        "fire_hue": 17,
        "fire_hue_variance": 32,
        "firework_frequency": 8,
        "firework_left_x": 200,
        "firework_right_x": 1720,
        "firework_y": 340,
        "firework_position_variance": 18,
        "firework_direction": 0,
        "firework_speed": 5.5,
        "firework_speed_variance": 1.5,
        "firework_spread": 360,
        "firework_gravity": 0.055,
        "firework_gravity_direction": 90,
        "firework_friction": 0.018,
        "firework_life": 95,
        "firework_scale": 0.65,
        "firework_alpha": 1.0,
        "snow_enabled": True,
        "snow_frequency": 20,
        "snow_scale": 0.15,
        "snow_scale_variance": 0.32,
        "snow_speed": 1.2,
        "snow_direction_variance": 33,
        "snow_alpha": 0.51,
    },
    "font": {
        "family": '"Noto Serif JP", serif',
        "base_size": 16,
        "color": "#ffffff",
    },
    # アイコンの揺れ。テンプレートの wave-canvas 相当
    "icon": {
        "wave_enabled": True,
        "wave_amplitude": 3.0,
        "wave_frequency": 0.05,
        "wave_speed": 0.03,
        "sepia": True,
        "size": 400,
    },
    # アイコンにかぶせる暗い縁取り。テンプレートの .background-circle 相当
    "vignette": {
        "enabled": True,
        "color": "#000000",
        "inner": 0,
        "outer": 70,
    },
    # 初期値はテンプレートの見た目に合わせてある
    "elements": {
        "broadcast_title": {"label": "放送タイトル", "size": 32, "color": "#ffffff", "family": "", "weight": "700"},
        "start_time": {"label": "開始時刻", "size": 16, "color": "#ffffff", "family": "", "weight": "700"},
        "section_heading": {"label": "見出し「対象者」", "size": 70, "color": "#ff0000", "family": "", "weight": "700"},
        "analysis_heading": {"label": "見出し「コメント分析結果」", "size": 50, "color": "#ff0000", "family": "", "weight": "700"},
        "user_name": {"label": "ユーザー名", "size": 50, "color": "#ff0000", "family": "", "weight": "700"},
        "list_link": {"label": "一覧へのリンク", "size": 16, "color": "#add8e6", "family": "", "weight": "700"},
        "account_id": {"label": "アカウントID", "size": 16, "color": "#ff0000", "family": "", "weight": "700"},
        "table_header": {"label": "表の見出し", "size": 16, "color": "#ffffff", "family": "", "weight": "700"},
        "comment_text": {"label": "コメント本文", "size": 25, "color": "#ffffff", "family": "", "weight": "700"},
        "analysis_text": {"label": "分析本文", "size": 20, "color": "#ff0000", "family": "", "weight": "400"},
    },
}

# user_detail.html の並び順に合わせたセレクタ。テンプレートを触ったらここも直す。
# body: h1(タイトル) p(開始時刻) h1(対象者) div p(ユーザー名) p(一覧リンク) p(アカID)
#       div div#chat-data h1(分析見出し) p>b(分析本文)
ELEMENT_SELECTORS = {
    "broadcast_title": "body > h1:nth-of-type(1)",
    "start_time": "body > p:nth-of-type(1)",
    "section_heading": "body > h1:nth-of-type(2)",
    "analysis_heading": "body > h1:nth-of-type(3)",
    "user_name": "body > p:nth-of-type(2)",
    "list_link": "body > p:nth-of-type(3)",
    "account_id": "body > p:nth-of-type(4)",
    "table_header": "#chat-data th",
    "comment_text": "#chat-data td b",
    "analysis_text": "body > p:nth-of-type(5) > b",
}

LINK_ELEMENT_DEFAULTS: dict[str, dict[str, Any]] = {
    "list_heading": {"label": "一覧見出し", "size": 30, "color": "#ffffff", "family": "", "weight": "700"},
    "start_time": {"label": "開始時間", "size": 30, "color": "#ffffff", "family": "", "weight": "700"},
    "first_comment": {"label": "初コメ", "size": 30, "color": "#ffffff", "family": "", "weight": "700"},
    "last_comment": {"label": "最終コメ", "size": 30, "color": "#ffffff", "family": "", "weight": "700"},
    "toggle_button": {"label": "コメント表示ボタン", "size": 16, "color": "#000000", "family": "", "weight": "400"},
    "detail_link": {"label": "個別ページリンク", "size": 30, "color": "#add8e6", "family": "", "weight": "700"},
    "table_header": {"label": "表の見出し", "size": 16, "color": "#ff0000", "family": "", "weight": "700"},
    "comment_text": {"label": "コメント本文", "size": 25, "color": "#ff0000", "family": "", "weight": "700"},
}

# Step11が生成する user_list.html の実DOMに合わせる。
LINK_ELEMENT_SELECTORS = {
    "list_heading": ".comment-analysis",
    "start_time": ".link-item .start-time",
    "first_comment": ".link-item .comment-preview p:nth-child(1)",
    "last_comment": ".link-item .comment-preview p:nth-child(2)",
    "toggle_button": ".link-item .toggle-button",
    "detail_link": ".link-item .broadcast-link a",
    "table_header": ".chat-data th",
    "comment_text": ".chat-data td b",
}


def normalize_page_kind(page_kind: str) -> str:
    return PAGE_KIND_LINK if str(page_kind).strip().lower() == PAGE_KIND_LINK else PAGE_KIND_DETAIL


def default_design(page_kind: str = PAGE_KIND_DETAIL) -> dict[str, Any]:
    design = json.loads(json.dumps(DEFAULT_DESIGN))
    if normalize_page_kind(page_kind) == PAGE_KIND_LINK:
        design["elements"] = json.loads(json.dumps(LINK_ELEMENT_DEFAULTS))
    return design


def element_selectors(page_kind: str = PAGE_KIND_DETAIL) -> dict[str, str]:
    if normalize_page_kind(page_kind) == PAGE_KIND_LINK:
        return LINK_ELEMENT_SELECTORS
    return ELEMENT_SELECTORS


def ensure_table(conn, page_kind: str = PAGE_KIND_DETAIL) -> str:
    table_name = (
        "special_user_link_design"
        if normalize_page_kind(page_kind) == PAGE_KIND_LINK
        else "special_user_design"
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            user_id TEXT PRIMARY KEY,
            design_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    return table_name


def merge_design(
    stored: dict[str, Any] | None,
    page_kind: str = PAGE_KIND_DETAIL,
) -> dict[str, Any]:
    design = default_design(page_kind)
    if not isinstance(stored, dict):
        return design
    for section, values in stored.items():
        if section == "elements" and isinstance(values, dict):
            for name, element in values.items():
                if name in design["elements"] and isinstance(element, dict):
                    design["elements"][name].update(element)
        elif section in design and isinstance(values, dict):
            design[section].update(values)
    return design


def load_design(
    user_id: str,
    page_kind: str = PAGE_KIND_DETAIL,
) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    if not user_id:
        return merge_design(None, page_kind)
    with tracker.connect() as conn:
        table_name = ensure_table(conn, page_kind)
        row = conn.execute(
            f"SELECT design_json FROM {table_name} WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return merge_design(None, page_kind)
    try:
        return merge_design(json.loads(row["design_json"] or "{}"), page_kind)
    except json.JSONDecodeError:
        return merge_design(None, page_kind)


def save_design(
    user_id: str,
    design: dict[str, Any],
    page_kind: str = PAGE_KIND_DETAIL,
) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id が空です")
    merged = merge_design(design, page_kind)
    with tracker.connect() as conn:
        table_name = ensure_table(conn, page_kind)
        conn.execute(
            f"""
            INSERT INTO {table_name} (user_id, design_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                design_json = excluded.design_json,
                updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(merged, ensure_ascii=False), tracker.now()),
        )
        conn.commit()
    return merged


def render_css(
    design: dict[str, Any],
    page_kind: str = PAGE_KIND_DETAIL,
) -> str:
    """生成HTMLへ差し込むCSS。既存のインラインstyleに勝つよう !important を付ける。"""
    design = merge_design(design, page_kind)
    selectors = element_selectors(page_kind)
    font = design["font"]
    background = design["background"]
    rules = [
        "body{"
        f"background-color:{background['color']}!important;"
        f"font-family:{font['family']}!important;"
        f"font-size:{font['base_size']}px!important;"
        f"color:{font['color']}!important;"
        "}"
    ]
    for name, element in design["elements"].items():
        selector = selectors.get(name)
        if not selector:
            continue
        parts = [
            f"font-size:{element['size']}px!important",
            f"color:{element['color']}!important",
            f"font-weight:{element.get('weight') or '400'}!important",
        ]
        family = str(element.get("family") or "").strip()
        if family:
            parts.append(f"font-family:{family}!important")
        rules.append(selector + "{" + ";".join(parts) + ";}")

    vignette = design["vignette"]
    if vignette["enabled"]:
        rules.append(
            ".background-circle{background:radial-gradient(circle at center,"
            f"transparent 0%,transparent {vignette['inner']}%,"
            f"{vignette['color']} {vignette['outer']}%,{vignette['color']} 100%)!important;}}"
        )
    else:
        rules.append(".background-circle{background:none!important;}")

    icon = design["icon"]
    rules.append(
        f".image-container{{width:{icon['size']}px!important;height:{icon['size']}px!important;}}"
    )
    rules.append(
        f".foreground-image{{width:{icon['size']}px!important;"
        f"filter:sepia({100 if icon['sepia'] else 0}%)!important;}}"
    )
    return "\n".join(rules)


def design_style_block(
    user_id: str,
    page_kind: str = PAGE_KIND_DETAIL,
) -> str:
    design = load_design(user_id, page_kind)
    css = render_css(design, page_kind)
    block = f'<style id="{DESIGN_STYLE_MARKER}">\n{css}\n</style>\n'
    if normalize_page_kind(page_kind) == PAGE_KIND_LINK:
        particle_json = json.dumps(design["link_particles"], ensure_ascii=False)
        block += f"""<script id="{LINK_PARTICLE_MARKER}">
window.addEventListener("load", function () {{
  const p = {particle_json};
  if (!window.particleSystem1 || !window.particleSystem2 || !window.snowParticleSystem) return;
  const fire = x => {{
    const fireworks = p.side_mode === "firework";
    return {{bgColor:"#00000",width:1920,height:1080,
    emitFrequency:p.fire_enabled?(fireworks?p.firework_frequency:p.fire_frequency):0,
    startX:x,startXVariance:fireworks?p.firework_position_variance:0,
    startY:fireworks?p.firework_y:800,startYVariance:fireworks?p.firework_position_variance:0,
    initialDirection:fireworks?p.firework_direction:90,
    initialDirectionVariance:fireworks?p.firework_spread:360,
    initialSpeed:fireworks?p.firework_speed:0.2,
    initialSpeedVariance:fireworks?p.firework_speed_variance:0,
    friction:fireworks?String(p.firework_friction):"0.063",
    accelerationSpeed:fireworks?p.firework_gravity:p.fire_acceleration,
    accelerationDirection:fireworks?p.firework_gravity_direction:270,
    startScale:fireworks?p.firework_scale:p.fire_scale,startScaleVariance:fireworks?0.35:0,
    finishScale:0,finishScaleVariance:0.31,
    lifeSpan:fireworks?p.firework_life:p.fire_life,lifeSpanVariance:fireworks?30:7,
    startAlpha:fireworks?p.firework_alpha:1,startAlphaVariance:"0",
    finishAlpha:0,finishAlphaVariance:1,shapeIdList:["blur_circle"],
    startColor:{{hue:String(p.fire_hue),hueVariance:String(p.fire_hue_variance),
      saturation:"91",saturationVariance:15,luminance:"56",luminanceVariance:"16"}},
    blendMode:true,alphaCurveType:fireworks?"0":"1",VERSION:"1.0.0"}};
  }};
  const snow = {{bgColor:"#00000",width:1920,height:1080,
    emitFrequency:p.snow_enabled?p.snow_frequency:0,startX:960,startXVariance:1920,startY:-8,startYVariance:0,
    initialDirection:90,initialDirectionVariance:p.snow_direction_variance,
    initialSpeed:p.snow_speed,initialSpeedVariance:0.3,friction:0,accelerationSpeed:0,accelerationDirection:81.6,
    startScale:p.snow_scale,startScaleVariance:p.snow_scale_variance,
    finishScale:p.snow_scale,finishScaleVariance:"0",lifeSpan:1000,lifeSpanVariance:"0",
    startAlpha:String(p.snow_alpha),startAlphaVariance:"1",finishAlpha:"1",finishAlphaVariance:"0",
    shapeIdList:["blur_circle"],startColor:{{hue:"0",hueVariance:"0",saturation:"0",
      saturationVariance:0,luminance:"100",luminanceVariance:"47"}},
    blendMode:true,alphaCurveType:"0",VERSION:"1.0.0"}};
  window.particleSystem1.clear();
  window.particleSystem2.clear();
  window.particleSystem1.importFromJson(fire(p.side_mode==="firework"?p.firework_left_x:200));
  window.particleSystem2.importFromJson(fire(p.side_mode==="firework"?p.firework_right_x:1720));
  window.snowParticleSystem.clear();
  window.snowParticleSystem.importFromJson(snow);
}});
</script>
"""
    return block


def apply_design_to_html(
    path: Path,
    user_id: str,
    page_kind: str = PAGE_KIND_DETAIL,
) -> bool:
    """生成済みHTMLへCSSを差し込む。既に入っていれば入れ替える。"""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    import re

    text = re.sub(
        rf'<style id="{DESIGN_STYLE_MARKER}">.*?</style>\n?',
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        rf'<script id="{LINK_PARTICLE_MARKER}">.*?</script>\n?',
        "",
        text,
        flags=re.DOTALL,
    )
    block = design_style_block(user_id, page_kind)
    if "</head>" in text:
        text = text.replace("</head>", block + "</head>", 1)
    else:
        text = block + text
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


SAMPLE_LV = "lv000000000"
SAMPLE_COMMENTS = [
    {"no": 1, "date": "1785248885", "broadcast_seconds": 320, "text": "こんばんはー", "premium": "", "name": ""},
    {"no": 2, "date": "1785249130", "broadcast_seconds": 565, "text": "今日は何の話をするんです？", "premium": "", "name": ""},
    {"no": 3, "date": "1785249780", "broadcast_seconds": 1215, "text": "それは面白い見方ですね", "premium": "", "name": ""},
    {"no": 4, "date": "1785250420", "broadcast_seconds": 1855, "text": "www", "premium": "", "name": ""},
    {"no": 5, "date": "1785251200", "broadcast_seconds": 2635, "text": "また来ます、おつかれさまでした", "premium": "", "name": ""},
]


def build_sample_page(user_id: str, user_label: str = "") -> Path:
    """生成テンプレートにダミーデータを流し込んだ見本ページを作る。

    実際の生成と同じ step11 の関数を通すので、出来上がりは本物と同じ構造になる。
    """
    import shutil
    import tempfile

    user_id = str(user_id or "").strip() or "0"
    step11 = tracker.import_step11_module()
    template_dir = str(tracker.ROOT / "legacy_archiver" / "templates")

    sample_dir = Path(tempfile.gettempdir()) / "niconico_design_preview" / user_id
    if sample_dir.exists():
        shutil.rmtree(sample_dir, ignore_errors=True)
    sample_dir.mkdir(parents=True, exist_ok=True)
    step11.copy_static_files(template_dir, str(sample_dir))

    user_data = {
        "user_id": user_id,
        "user_name": user_label or f"ユーザー{user_id}",
        "comments": [dict(comment) for comment in SAMPLE_COMMENTS],
    }
    broadcast_data = {
        "lv_value": SAMPLE_LV,
        "live_num": SAMPLE_LV[2:],
        "live_title": "デザイン確認用のサンプル放送",
        "start_time": "1785248885",
        "broadcaster": "サンプル配信者",
    }
    # AIは呼ばない。分析欄は基本統計だけの見本にする。
    config = {
        "special_users_config": {
            "users": {
                user_id: {
                    "user_id": user_id,
                    "display_name": user_data["user_name"],
                    "analysis_enabled": False,
                    "analysis_prompt": "",
                    "template": "user_detail.html",
                    "description": "",
                }
            }
        }
    }
    step11.create_user_detail_page(
        user_data, broadcast_data, template_dir, str(sample_dir), SAMPLE_LV, config
    )
    step11.update_user_list_page(
        user_data, broadcast_data, template_dir, str(sample_dir), SAMPLE_LV
    )
    return sample_dir


def build_editor_html(
    user_id: str,
    sample_dir: Path | None,
    page_kind: str = PAGE_KIND_DETAIL,
) -> str:
    """テンプレートから作った見本ページに、編集UIを差し込んだものを返す。"""
    if sample_dir is None:
        raise RuntimeError("見本ページが用意できていません")
    page_kind = normalize_page_kind(page_kind)
    sample_name = (
        f"{user_id}_list.html"
        if page_kind == PAGE_KIND_LINK
        else f"{user_id}_{SAMPLE_LV}_detail.html"
    )
    sample_path = Path(sample_dir) / sample_name
    html = sample_path.read_text(encoding="utf-8", errors="ignore")
    ui = TEMPLATE_PATH.read_text(encoding="utf-8")
    ui = ui.replace(
        "{{DESIGN_JSON}}",
        json.dumps(load_design(user_id, page_kind), ensure_ascii=False),
    )
    ui = ui.replace(
        "{{SELECTORS_JSON}}",
        json.dumps(element_selectors(page_kind), ensure_ascii=False),
    )
    ui = ui.replace(
        "{{EDITOR_TITLE}}",
        "リンクページデザイン" if page_kind == PAGE_KIND_LINK else "ページデザイン",
    )
    ui = ui.replace("{{PAGE_KIND_JSON}}", json.dumps(page_kind))
    if "</body>" in html:
        return html.replace("</body>", ui + "\n</body>", 1)
    return html + ui


class DesignEditorHandler(BaseHTTPRequestHandler):
    server_version = "SpecialUserDesignEditor/1"

    def log_message(self, *_args) -> None:  # サーバーの標準ログは出さない
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        user_id = getattr(self.server, "user_id", "")
        page_kind = getattr(self.server, "page_kind", PAGE_KIND_DETAIL)
        if path in ("/", "/index.html"):
            try:
                html = build_editor_html(
                    user_id,
                    getattr(self.server, "sample_dir", None),
                    page_kind,
                )
            except Exception as exc:
                self._send(500, str(exc).encode("utf-8"), "text/plain; charset=utf-8")
                return
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path.startswith(("/css/", "/js/", "/assets/")):
            sample_dir = getattr(self.server, "sample_dir", None)
            if sample_dir is None:
                self._send(500, b"sample not ready", "text/plain; charset=utf-8")
                return
            target = (Path(sample_dir) / path.lstrip("/")).resolve()
            try:
                target.relative_to(Path(sample_dir).resolve())
            except ValueError:
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            if not target.is_file():
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            suffix = target.suffix.lower()
            content_type = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
            }.get(suffix, "application/octet-stream")
            self._send(200, target.read_bytes(), content_type)
            return
        if path == "/api/design":
            body = json.dumps(
                load_design(user_id, page_kind),
                ensure_ascii=False,
            ).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/design":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        user_id = getattr(self.server, "user_id", "")
        page_kind = getattr(self.server, "page_kind", PAGE_KIND_DETAIL)
        try:
            design = json.loads(raw.decode("utf-8"))
            saved = save_design(user_id, design, page_kind)
        except Exception as exc:
            self._send(
                400,
                json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )
            return
        self._send(
            200,
            json.dumps({"ok": True, "design": saved}, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )


_SERVERS: dict[tuple[str, str], ThreadingHTTPServer] = {}
_LOCK = threading.Lock()


def start_editor_server(
    user_id: str,
    user_label: str = "",
    page_kind: str = PAGE_KIND_DETAIL,
) -> str:
    """ユーザーごとに編集用サーバーを立て、URLを返す。既に動いていれば使い回す。"""
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id が空です")
    page_kind = normalize_page_kind(page_kind)
    server_key = (user_id, page_kind)
    with _LOCK:
        existing = _SERVERS.get(server_key)
        if existing is not None:
            return f"http://127.0.0.1:{existing.server_address[1]}/"
        server = ThreadingHTTPServer(("127.0.0.1", 0), DesignEditorHandler)
        server.user_id = user_id  # type: ignore[attr-defined]
        server.user_label = user_label or user_id  # type: ignore[attr-defined]
        server.page_kind = page_kind  # type: ignore[attr-defined]
        server.sample_dir = build_sample_page(user_id, user_label)  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _SERVERS[server_key] = server
        return f"http://127.0.0.1:{server.server_address[1]}/"


def start_link_editor_server(user_id: str, user_label: str = "") -> str:
    """Step11生成のリンク一覧ダミーを、個別ページと同じ編集UIで開く。"""
    return start_editor_server(user_id, user_label, PAGE_KIND_LINK)


def stop_all_editor_servers() -> None:
    with _LOCK:
        for server in _SERVERS.values():
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        _SERVERS.clear()

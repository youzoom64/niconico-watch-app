from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = ROOT / "legacy_archiver"
if str(LEGACY_ROOT) not in sys.path:
    sys.path.insert(0, str(LEGACY_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "app") not in sys.path:
    sys.path.insert(0, str(ROOT / "app"))

from generated_html_paths import is_pc_archive_html_candidate
from processors import step12_html_generator as step12
from processors.step12_html_generator import build_html_filename
from processors.step12_mobile_html_generator import (
    build_mobile_archive,
    inject_mobile_switch_link,
    mobile_html_filename,
)


def _sample_inputs():
    comments = [
        {
            "no": index + 1,
            "broadcast_seconds": float(index * 3),
            "timeline_block": (index * 3 // 10) * 10,
            "user_id": f"user-{index % 5}",
            "user_name": f"利用者{index % 5}",
            "text": "遅延データだけに存在するコメント" if index == 0 else f"コメント{index}",
        }
        for index in range(205)
    ]
    transcripts = [
        {
            "timestamp": index * 10,
            "start": index * 10,
            "end": index * 10 + 8,
            "speaker": "話者",
            "text": f"発言{index}",
            "positive_score": 0.25,
            "negative_score": -0.1,
            "center_score": 0.05,
        }
        for index in range(65)
    ]
    blocks = [
        {
            "start_seconds": index * 10,
            "end_seconds": index * 10 + 10,
            "time_range": f"00:{index // 6:02d}:{(index % 6) * 10:02d}",
            "screenshot_path": f"./screenshot/lv-test/{index * 10}.jpg",
            "positive_score": 0.25,
            "negative_score": -0.1,
            "center_score": 0.05,
        }
        for index in range(65)
    ]
    ranking = {
        "ranking": [
            {"rank": index + 1, "user_id": f"user-{index}", "user_name": f"利用者{index}", "comment_count": 20 - index}
            for index in range(5)
        ]
    }
    broadcast = {
        "live_title": "テスト放送",
        "broadcaster": "配信者",
        "start_time": 1_700_000_000,
        "summary_text": "短いAI要約",
        "elapsed_time": "00:10:50",
        "word_ranking": [{"word": "テスト", "count": 12}],
    }
    return broadcast, {"transcripts": transcripts}, {"comments": comments}, ranking, {"transcript_blocks": blocks}


def test_filename_rules_keep_pc_name_and_mobile_suffix():
    pc_name = build_html_filename("lv1", 'A:B/C?')
    assert pc_name == "lv1_A_B_C_.html"
    assert mobile_html_filename(pc_name) == "lv1_A_B_C__mobile.html"

    long_pc_name = build_html_filename("lv1", "長" * 300)
    mobile_name = mobile_html_filename(long_pc_name)
    assert len(mobile_name) <= 200
    assert mobile_name.endswith("_mobile.html")


def test_mobile_archive_initial_shell_is_light_and_heavy_data_is_sharded(tmp_path):
    broadcast, transcripts, comments, ranking, timeline = _sample_inputs()
    pc_name = build_html_filename("lv-test", broadcast["live_title"])
    result = build_mobile_archive(
        broadcast_dir=tmp_path,
        lv_value="lv-test",
        pc_filename=pc_name,
        broadcast_data=broadcast,
        transcript_data=transcripts,
        comments_data=comments,
        ranking_data=ranking,
        timeline_data=timeline,
        audio_source="./lv-test_audio.mp3",
    )

    mobile_path = Path(result["mobile_html_file"])
    shell = mobile_path.read_text(encoding="utf-8")
    data_dir = Path(result["mobile_data_dir"])

    assert result["timeline_shard_count"] == 3
    assert result["comment_count"] == 205
    assert mobile_path.name.endswith("_mobile.html")
    assert mobile_path.stat().st_size < 100_000
    assert "遅延データだけに存在するコメント" not in shell
    assert "<img" not in shell
    assert "<canvas" not in shell
    assert "Chart.js" not in shell
    assert 'preload="none"' in shell
    assert f'href="{pc_name}?desktop=1"' in shell
    assert 'data-view="adaptive"' in shell
    assert '"timelineBlockCount":65' in shell
    assert "IntersectionObserver" in shell
    assert "ResizeObserver" in shell
    assert "pruneDistantBlocks" in shell
    assert "slot.setAttribute('aria-hidden','true')" in shell
    assert "transcript-lane" in shell
    assert "comment-lane" in shell
    assert "grid-template-columns:minmax(0,1fr) minmax(0,1fr);align-items:stretch" in shell
    assert ".lane{min-width:0;height:100%" in shell
    assert ".timeline-lanes{grid-template-columns:1fr}" not in shell
    assert "@media (min-width:900px)" in shell
    assert "commentsPerPage" not in shell
    assert "loadScript('comments.js')" in shell
    assert "loadScript('emotion.js')" in shell
    assert (data_dir / "comments.js").exists()
    assert (data_dir / "emotion.js").exists()
    assert len(list(data_dir.glob("timeline_*.js"))) == 3
    assert "遅延データだけに存在するコメント" in (data_dir / "comments.js").read_text(encoding="utf-8")
    first_timeline_shard = (data_dir / "timeline_000.js").read_text(encoding="utf-8")
    assert "遅延データだけに存在するコメント" in first_timeline_shard
    assert "発言0" in first_timeline_shard


def test_pc_html_is_unchanged_for_all_devices_and_gui_candidate_excludes_mobile(tmp_path):
    pc_name = "lv1_放送.html"
    mobile_name = mobile_html_filename(pc_name)
    pc_document = "<html><head></head><body><main>PC</main></body></html>"
    assert inject_mobile_switch_link(pc_document, mobile_name) == pc_document

    pc_path = tmp_path / pc_name
    mobile_path = tmp_path / mobile_name
    raw_path = tmp_path / "lv1.html"
    for path in (pc_path, mobile_path, raw_path):
        path.write_text("x", encoding="utf-8")
    assert is_pc_archive_html_candidate(pc_path, "lv1")
    assert not is_pc_archive_html_candidate(mobile_path, "lv1")
    assert not is_pc_archive_html_candidate(raw_path, "lv1")


def test_pc_style_virtual_archive_keeps_pc_layout_and_only_materialises_visible_blocks(tmp_path):
    broadcast, transcripts, comments, ranking, timeline = _sample_inputs()
    pc_name = build_html_filename("lv-test", broadcast["live_title"])
    pc_html = """<!doctype html>
<html><head><style>
.container { display:flex; gap:20px; }
.time-block { height:180px; overflow:hidden; }
#timeline2 .time-block .comment-list { overflow-y:auto; }
</style></head><body>
<div id="pc-layout-marker">PC版の既存構造</div>
<script>
(() => {
  if(!new URLSearchParams(location.search).has('desktop')&&window.matchMedia('(max-width: 760px)').matches){
    const target=document.querySelector('.mobile-version-link');
    location.replace(target.href);
  }
})();
</script>
<div class="stat-item"><strong>来場者数:</strong> 人</div>
<div class="stat-item"><strong>配信時間:</strong> </div>
<a class="mobile-version-link" href="old_mobile.html">スマホ版を開く</a>
<div class="container"><div id="old-heavy-timeline">全件展開された旧タイムライン</div></div>
<div class="section ai-chat-section"><h2>終了後会話</h2></div>
</body></html>"""
    result = build_mobile_archive(
        broadcast_dir=tmp_path,
        lv_value="lv-test",
        pc_filename=pc_name,
        broadcast_data=broadcast,
        transcript_data=transcripts,
        comments_data=comments,
        ranking_data=ranking,
        timeline_data=timeline,
        audio_source="./lv-test_audio.mp3",
        pc_html=pc_html,
    )

    shell = Path(result["mobile_html_file"]).read_text(encoding="utf-8")
    assert "PC版の既存構造" in shell
    assert ".container { display:flex; gap:20px; }" in shell
    assert "全件展開された旧タイムライン" not in shell
    assert "放送者文字おこしのタイムライン" in shell
    assert "コメントのタイムライン" in shell
    assert shell.count('class="time-block virtual-time-block"') == 130
    assert 'class="comment-list"' in shell
    assert "flex-direction: row !important" in shell
    assert "min-width: 0 !important" in shell
    assert 'href="lv-test_テスト放送.html?desktop=1"' in shell
    assert "PC版を開く" in shell
    assert "<strong>来場者数:</strong> 不明" in shell
    assert "<strong>配信時間:</strong> 00:10:50" in shell
    assert "content-visibility: auto" in shell
    assert '.virtual-time-block[data-loaded="true"]' in shell
    assert "overflow: visible !important" in shell
    assert "right.scrollHeight" in shell
    assert "left.style.minHeight=target+'px'" in shell
    assert "right.style.minHeight=target+'px'" in shell
    assert "manualMinHeight" in shell
    assert "virtual-transcript-meta" in shell
    assert "row.time,row.speaker" in shell
    assert "position: static !important" in shell
    assert "transform: none !important" in shell
    assert ".mobile-version-link" in shell
    assert "width: max-content !important" in shell
    assert "location.replace(target.href)" not in shell
    assert "renderTranscriptBlock" in shell
    assert "renderCommentBlock" in shell
    assert "遅延データだけに存在するコメント" not in shell
    assert Path(result["mobile_html_file"]).stat().st_size < 250_000


def test_step12_process_generates_only_pc_html_and_keeps_pc_db_path(tmp_path, monkeypatch):
    lv_value = "lv-test"
    account_dir = tmp_path / "account"
    broadcast_dir = account_dir / lv_value
    broadcast_dir.mkdir(parents=True)
    broadcast = {
        "live_title": "同時生成テスト",
        "broadcaster": "配信者",
        "start_time": 1_700_000_000,
        "summary_text": "要約",
        "elapsed_time": "00:00:10",
    }
    updates = []
    monkeypatch.setattr(step12, "find_account_directory", lambda *_args: str(account_dir))
    monkeypatch.setattr(step12, "load_broadcast_data_from_db", lambda _lv: dict(broadcast))
    monkeypatch.setattr(step12, "load_transcript_payload", lambda _lv: {"transcripts": []})
    monkeypatch.setattr(step12, "load_comments_payload", lambda _lv: {"comments": []})
    monkeypatch.setattr(step12, "load_ranking_payload", lambda *_args: {"ranking": []})
    monkeypatch.setattr(
        step12,
        "generate_complete_html",
        lambda *_args: "<html><head></head><body><main>PC</main></body></html>",
    )
    monkeypatch.setattr(step12, "update_broadcast_data", lambda lv, values: updates.append((lv, values)))

    result = step12.process({
        "lv_value": lv_value,
        "config": {},
        "platform_directory": str(tmp_path),
        "account_id": "account",
    })

    pc_path = Path(result["html_file"])
    assert pc_path.exists()
    assert pc_path.name.endswith("同時生成テスト.html")
    assert result["mobile_generated"] is False
    assert list(broadcast_dir.glob("*_mobile.html")) == []
    assert list(broadcast_dir.glob("*_mobile_data")) == []
    assert "nico-mobile-switch" not in pc_path.read_text(encoding="utf-8")
    assert updates == [(lv_value, {"html_file_path": pc_path.name})]


def test_mobile_archive_preserves_existing_html_while_refreshing_shards(tmp_path):
    broadcast, transcripts, comments, ranking, timeline = _sample_inputs()
    pc_name = build_html_filename("lv-test", broadcast["live_title"])
    first = build_mobile_archive(
        broadcast_dir=tmp_path,
        lv_value="lv-test",
        pc_filename=pc_name,
        broadcast_data=broadcast,
        transcript_data=transcripts,
        comments_data=comments,
        ranking_data=ranking,
        timeline_data=timeline,
        audio_source="./lv-test_audio.mp3",
    )
    mobile_path = Path(first["mobile_html_file"])
    mobile_path.write_text("<html><body>手動編集済み</body></html>", encoding="utf-8")

    second = build_mobile_archive(
        broadcast_dir=tmp_path,
        lv_value="lv-test",
        pc_filename=pc_name,
        broadcast_data=broadcast,
        transcript_data=transcripts,
        comments_data=comments,
        ranking_data=ranking,
        timeline_data=timeline,
        audio_source="./lv-test_audio.mp3",
    )

    assert second["mobile_html_preserved"] is True
    assert mobile_path.read_text(encoding="utf-8") == "<html><body>手動編集済み</body></html>"
    assert (Path(second["mobile_data_dir"]) / "comments.js").is_file()


def test_step12_process_preserves_existing_pc_and_ignores_legacy_mobile_html(tmp_path, monkeypatch):
    lv_value = "lv-existing"
    account_dir = tmp_path / "account"
    broadcast_dir = account_dir / lv_value
    broadcast_dir.mkdir(parents=True)
    title = "既存ページ"
    pc_name = build_html_filename(lv_value, title)
    pc_path = broadcast_dir / pc_name
    pc_document = '<html><head></head><body><div id="timeline2">手動編集PC</div></body></html>'
    pc_path.write_text(pc_document, encoding="utf-8")
    mobile_path = broadcast_dir / mobile_html_filename(pc_name)
    mobile_path.write_text("<html><body>手動編集MOBILE</body></html>", encoding="utf-8")

    monkeypatch.setattr(step12, "find_account_directory", lambda *_args: str(account_dir))
    monkeypatch.setattr(
        step12,
        "load_broadcast_data_from_db",
        lambda _lv: {
            "live_title": title,
            "html_file_path": pc_name,
            "broadcaster": "配信者",
            "elapsed_time": "00:00:10",
        },
    )
    monkeypatch.setattr(step12, "load_transcript_payload", lambda _lv: {"transcripts": []})
    monkeypatch.setattr(step12, "load_comments_payload", lambda _lv: {"comments": []})
    monkeypatch.setattr(step12, "load_ranking_payload", lambda *_args: {"ranking": []})
    monkeypatch.setattr(
        step12,
        "generate_complete_html",
        lambda *_args: (_ for _ in ()).throw(AssertionError("既存HTMLを再生成してはいけない")),
    )
    monkeypatch.setattr(step12, "update_broadcast_data", lambda *_args: None)

    result = step12.process(
        {
            "lv_value": lv_value,
            "config": {},
            "platform_directory": str(tmp_path),
            "account_id": "account",
        }
    )

    assert result["pc_html_preserved"] is True
    assert result["mobile_generated"] is False
    assert pc_path.read_text(encoding="utf-8") == pc_document
    assert mobile_path.read_text(encoding="utf-8") == "<html><body>手動編集MOBILE</body></html>"
    assert list(broadcast_dir.glob("*_mobile_data")) == []

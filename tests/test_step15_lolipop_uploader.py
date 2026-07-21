from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from legacy_archiver.processors import step15_lolipop_uploader as step15


def _write_index(root: Path, records: list[dict]) -> None:
    payload = json.dumps(records, ensure_ascii=False)
    (root / "index.html").write_text(
        f'<script id="archive-data" type="application/json">{payload}</script>',
        encoding="utf-8",
    )


def _settings(*, enabled: bool = True) -> dict:
    return {
        "enable_auto_upload": enabled,
        "target_id": "lolipop-main",
        "remote_directory_template": "niconico/{account_id}",
        "python_exe": __import__("sys").executable,
        "cli_path": __file__,
        "http_verify": True,
        "timeout_seconds": 60,
        "auto_start_credentials_api": False,
    }


def test_collect_publish_paths_keeps_only_completed_referenced_html(tmp_path: Path) -> None:
    completed = tmp_path / "lv1" / "lv1_完成.html"
    completed.parent.mkdir()
    completed.write_text('<section id="timeline2"></section>', encoding="utf-8")
    raw_stub = tmp_path / "lv2" / "lv2.html"
    raw_stub.parent.mkdir()
    raw_stub.write_text("<html>raw niconico page</html>", encoding="utf-8")
    mobile = tmp_path / "lv1" / "lv1_完成_mobile.html"
    mobile.write_text('<section id="timeline2"></section>', encoding="utf-8")
    mobile_data = tmp_path / "lv1" / "lv1_mobile_data" / "comments.js"
    mobile_data.parent.mkdir()
    mobile_data.write_text("const comments = [];", encoding="utf-8")
    audio = tmp_path / "lv1" / "lv1_audio.mp3"
    audio.write_bytes(b"mp3")
    screenshot = tmp_path / "lv1" / "screenshot" / "lv1" / "0.jpg"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"jpg")
    tag_page = tmp_path / "tags" / "tag_yosino.html"
    tag_page.parent.mkdir()
    tag_page.write_text("tag", encoding="utf-8")
    unrelated_tag_page = tmp_path / "tags" / "tag_other.html"
    unrelated_tag_page.write_text("other", encoding="utf-8")
    _write_index(
        tmp_path,
        [
            {"lv": "lv1", "url": "lv1/lv1_完成.html", "tags": ["yosino"]},
            {"lv": "lv2", "url": "lv2/lv2.html", "tags": ["other"]},
            {"url": "lv1/lv1_完成_mobile.html"},
            {"url": "../outside.html"},
        ],
    )

    paths, skipped = step15.collect_publish_paths(tmp_path, "lv1")

    assert paths == [
        "index.html",
        "tags/tag_yosino.html",
        "lv1/lv1_完成.html",
        "lv1/lv1_audio.mp3",
        "lv1/screenshot",
    ]
    assert skipped == []
    assert all("lv2" not in path for path in paths)
    assert "tags/tag_other.html" not in paths


def test_process_runs_upload_targets_with_exact_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    detail = tmp_path / "lv123" / "lv123_完成.html"
    detail.parent.mkdir()
    detail.write_text('<div id="timeline2"></div>', encoding="utf-8")
    unrelated = tmp_path / "lv999" / "lv999_完成.html"
    unrelated.parent.mkdir()
    unrelated.write_text('<div id="timeline2"></div>', encoding="utf-8")
    _write_index(
        tmp_path,
        [
            {"lv": "lv123", "url": "lv123/lv123_完成.html"},
            {"lv": "lv999", "url": "lv999/lv999_完成.html"},
        ],
    )
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "success": True,
                    "details": {"uploaded": 2, "skipped": 0},
                    "verification": {"success": True},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(step15, "find_account_directory", lambda *_args: str(tmp_path))
    monkeypatch.setattr(step15.subprocess, "run", fake_run)

    result = step15.process(
        {
            "account_id": "39532023",
            "lv_value": "lv123",
            "platform_directory": str(tmp_path),
            "config": {"upload_settings": _settings()},
        }
    )

    command = captured["command"]
    assert command[2:8] == [
        "upload",
        "--target",
        "lolipop-main",
        "--source-root",
        str(tmp_path.resolve()),
        "--remote-dir",
    ]
    assert "niconico/39532023" in command
    assert command.count("--path") == 2
    assert "index.html" in command
    assert "lv123/lv123_完成.html" in command
    assert "lv999/lv999_完成.html" not in command
    assert "--verify-after" in command
    assert "--force-overwrite" in command
    assert "--http-verify" in command
    assert result["uploaded"] is True
    assert result["verification_success"] is True


def test_process_html_only_excludes_audio_and_screenshots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    detail = tmp_path / "lv123" / "lv123_完成.html"
    detail.parent.mkdir()
    detail.write_text('<div id="timeline2"></div>', encoding="utf-8")
    (detail.parent / "lv123_audio.mp3").write_bytes(b"mp3")
    screenshot = detail.parent / "screenshot" / "lv123" / "0.jpg"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"jpg")
    _write_index(tmp_path, [{"lv": "lv123", "url": "lv123/lv123_完成.html"}])
    captured: dict[str, object] = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "success": True,
                    "details": {"uploaded": 2, "skipped": 0},
                    "verification": {"success": True},
                }
            ),
            stderr="",
        )

    settings = _settings()
    settings["html_only"] = True
    monkeypatch.setattr(step15, "find_account_directory", lambda *_args: str(tmp_path))
    monkeypatch.setattr(step15.subprocess, "run", fake_run)

    result = step15.process(
        {
            "account_id": "39532023",
            "lv_value": "lv123",
            "platform_directory": str(tmp_path),
            "config": {"upload_settings": settings},
        }
    )

    command = captured["command"]
    assert command.count("--path") == 2
    assert "index.html" in command
    assert "lv123/lv123_完成.html" in command
    assert "lv123/lv123_audio.mp3" not in command
    assert "lv123/screenshot" not in command
    assert result["html_only"] is True


def test_changed_older_html_is_added_and_traversal_is_rejected(tmp_path: Path) -> None:
    old_detail = tmp_path / "lv999" / "lv999_完成.html"
    old_detail.parent.mkdir()
    old_detail.write_text('<div id="timeline2"></div>', encoding="utf-8")
    old_mobile = tmp_path / "lv999" / "lv999_完成_mobile.html"
    old_mobile.write_text('<div id="timeline2"></div>', encoding="utf-8")
    outside = tmp_path.parent / "outside.html"
    outside.write_text("outside", encoding="utf-8")

    accepted, rejected = step15.collect_changed_html_paths(
        tmp_path,
        {
            "step13_index_generator": {
                "updated_html_paths": [
                    "lv999/lv999_完成.html",
                    "lv999/lv999_完成_mobile.html",
                    "../outside.html",
                    "lv999/missing.html",
                    "lv999/not-media.mp3",
                ]
            }
        },
    )

    assert accepted == ["lv999/lv999_完成.html"]
    assert rejected == [
        "../outside.html",
        "lv999/lv999_完成_mobile.html",
        "lv999/missing.html",
        "lv999/not-media.mp3",
    ]


def test_process_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        step15,
        "find_account_directory",
        lambda *_args: pytest.fail("directory lookup must not run"),
    )
    result = step15.process(
        {
            "account_id": "39532023",
            "platform_directory": "unused",
            "config": {"upload_settings": _settings(enabled=False)},
        }
    )
    assert result == {"uploaded": False, "reason": "feature_disabled"}


def test_process_raises_on_upload_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    detail = tmp_path / "lv123" / "lv123_完成.html"
    detail.parent.mkdir()
    detail.write_text('<div id="timeline2"></div>', encoding="utf-8")
    _write_index(tmp_path, [{"lv": "lv123", "url": "lv123/lv123_完成.html"}])
    monkeypatch.setattr(step15, "find_account_directory", lambda *_args: str(tmp_path))
    monkeypatch.setattr(
        step15.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=4,
            stdout=json.dumps({"success": False, "error": "接続失敗"}),
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="自動アップロード失敗"):
        step15.process(
            {
                "account_id": "39532023",
                "lv_value": "lv123",
                "platform_directory": str(tmp_path),
                "config": {"upload_settings": _settings()},
            }
        )


def test_credentials_api_is_started_when_health_check_fails_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checks = iter((False, True))
    started: dict[str, object] = {}

    monkeypatch.setattr(
        step15,
        "credentials_api_healthy",
        lambda _url: next(checks),
    )
    monkeypatch.setattr(step15.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        step15.subprocess,
        "Popen",
        lambda command, **kwargs: started.update(command=command, kwargs=kwargs),
    )

    step15.ensure_credentials_api(
        {
            "auto_start_credentials_api": True,
            "credentials_api_python_exe": __import__("sys").executable,
            "credentials_api_workdir": str(tmp_path),
            "credentials_api_module": "scripts.password_manager.api_main",
            "credentials_api_start_timeout_seconds": 2,
        }
    )

    assert started["command"][1:] == [
        "-m",
        "scripts.password_manager.api_main",
    ]

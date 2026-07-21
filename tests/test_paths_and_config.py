import json
from pathlib import Path

import tracker


def test_broadcast_save_directory_is_created(tmp_path):
    config = tracker.load_config()
    config.target_root = str(tmp_path)
    result = tracker.ensure_broadcast_target_dirs(config, "lv123", broadcaster_id="456")
    assert result == tmp_path / "platform" / "niconico" / "456" / "broadcast" / "lv123"
    assert result.is_dir()


def test_config_defaults_are_filled(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"target_root": str(tmp_path)}), encoding="utf-8")
    monkeypatch.setattr(tracker, "CONFIG_PATH", config_path)
    config = tracker.load_config()
    assert config.poll_seconds == 60
    assert config.recording_segment_seconds == 1800
    assert config.archive_upload_target_id == "lolipop-main"


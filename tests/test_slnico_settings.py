from __future__ import annotations

import json

import tracker


def test_apply_recommended_slnico_settings_preserves_credentials(tmp_path):
    recorder_dir = tmp_path / "SlNicoLiveRec1082"
    recorder_dir.mkdir()
    exe = recorder_dir / "SlNicoLiveRec.exe"
    exe.write_bytes(b"exe")
    config_path = recorder_dir / "SlNicoLiveRec_config.json"
    config_path.write_text(
        json.dumps(
            {
                "UserSession": "secret-session",
                "UnknownFutureSetting": "keep-me",
                "ConvertFormat": False,
                "CloseWindowOnExit": False,
            }
        ),
        encoding="utf-8",
    )

    result = tracker.apply_recommended_slnico_settings(exe)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert result == config_path
    assert saved["UserSession"] == "secret-session"
    assert saved["UnknownFutureSetting"] == "keep-me"
    assert saved["ConvertFormat"] is True
    assert saved["CloseWindowOnExit"] is True
    assert saved["FilenameFormat"] == "{id}_{year}_{month}{day}_{hour}{minute}{second}_{title}"
    assert saved["FolderFormat"] == "{supplier_id}_{author}"
    assert saved["Login"] == 2

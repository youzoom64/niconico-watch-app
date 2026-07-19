from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import tracker


@pytest.fixture(autouse=True)
def clear_timeshift_model_cache() -> None:
    with tracker._TIMESHIFT_FASTER_WHISPER_MODEL_CACHE_LOCK:
        tracker._TIMESHIFT_FASTER_WHISPER_MODEL_CACHE.clear()
    yield
    with tracker._TIMESHIFT_FASTER_WHISPER_MODEL_CACHE_LOCK:
        tracker._TIMESHIFT_FASTER_WHISPER_MODEL_CACHE.clear()


def install_fake_transcription_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    segment_count: int = 1,
) -> tuple[Path, list[tuple[str, str, str]], list[dict[str, Any]]]:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")
    model_loads: list[tuple[str, str, str]] = []
    logs: list[dict[str, Any]] = []

    class FakeWhisperModel:
        def __init__(self, model_size: str, *, device: str, compute_type: str) -> None:
            model_loads.append((model_size, device, compute_type))

        def transcribe(self, _audio_path: str, *, vad_filter: bool):
            assert vad_filter is True
            segments = [
                SimpleNamespace(start=float(index), end=float(index + 1), text=f"segment {index + 1}")
                for index in range(segment_count)
            ]
            return iter(segments), SimpleNamespace(language="ja", duration=float(segment_count))

    faster_whisper = ModuleType("faster_whisper")
    faster_whisper.WhisperModel = FakeWhisperModel
    fake_torch = ModuleType("torch")
    fake_torch.cuda = SimpleNamespace(
        is_available=lambda: False,
        device_count=lambda: 0,
        get_device_name=lambda _index: "",
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(tracker, "probe_media_duration_seconds", lambda _path: float(segment_count))
    monkeypatch.setattr(
        tracker,
        "postprocess_log",
        lambda lv, stage, level, message, payload=None, **_kwargs: logs.append(
            {
                "lv": lv,
                "stage": stage,
                "level": level,
                "message": message,
                "payload": payload or {},
            }
        ),
    )

    class FakeConsoleProgress:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def update(self, *_args, **_kwargs) -> None:
            pass

        def finish(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(tracker, "ConsoleProgress", FakeConsoleProgress)
    monkeypatch.setattr(tracker, "connect", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        tracker,
        "persist_transcription_rows",
        lambda _conn, _lv, rows, **_kwargs: {"segments": len(rows)},
    )
    monkeypatch.setattr(
        tracker,
        "export_legacy_transcript_file_from_db",
        lambda _conn, lv, **_kwargs: {"lv": lv, "segments": segment_count},
    )
    return audio_path, model_loads, logs


def run_transcription(audio_path: Path, lv: str, *, compute_type: str = "int8") -> None:
    tracker.transcribe_audio_with_faster_whisper(
        lv,
        audio_path,
        model_size="medium",
        device="cpu",
        compute_type=compute_type,
        mark_postprocess_done=False,
    )


def test_timeshift_process_reuses_model_for_matching_cache_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, model_loads, logs = install_fake_transcription_runtime(monkeypatch, tmp_path)
    monkeypatch.setenv("NICONICO_WATCH_APP_ROLE", "timeshift")

    run_transcription(audio_path, "lv100")
    run_transcription(audio_path, "lv101")
    run_transcription(audio_path, "lv102", compute_type="float32")

    assert model_loads == [
        ("medium", "cpu", "int8"),
        ("medium", "cpu", "float32"),
    ]
    messages = [row["message"] for row in logs]
    assert sum("モデル新規ロード開始" in message for message in messages) == 2
    assert sum("モデルキャッシュ再利用" in message for message in messages) == 1


def test_monitor_process_does_not_use_timeshift_model_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, model_loads, logs = install_fake_transcription_runtime(monkeypatch, tmp_path)
    monkeypatch.setenv("NICONICO_WATCH_APP_ROLE", "monitor")

    run_transcription(audio_path, "lv200")
    run_transcription(audio_path, "lv201")

    assert model_loads == [
        ("medium", "cpu", "int8"),
        ("medium", "cpu", "int8"),
    ]
    assert tracker._TIMESHIFT_FASTER_WHISPER_MODEL_CACHE == {}
    assert not any("モデルキャッシュ再利用" in row["message"] for row in logs)


def test_segment_progress_logs_are_throttled_but_include_first_and_last_segment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, _model_loads, logs = install_fake_transcription_runtime(
        monkeypatch,
        tmp_path,
        segment_count=100,
    )
    monkeypatch.setenv("NICONICO_WATCH_APP_ROLE", "timeshift")

    run_transcription(audio_path, "lv300")

    progress_logs = [row for row in logs if row["message"].startswith("FasterWhisper進捗")]
    assert len(progress_logs) == 2
    assert progress_logs[0]["payload"]["segments"] == 1
    assert progress_logs[-1]["payload"]["segments"] == 100
    assert all(row["level"] == "INFO" for row in progress_logs)

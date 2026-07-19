from __future__ import annotations

import json
import shutil
import subprocess
import wave
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import tracker
from legacy_archiver import archive_db
from legacy_archiver.processors import step09_screenshot_generator as step09
from legacy_archiver.processors import step12_html_generator as step12
from legacy_archiver.processors import step12_mobile_html_generator as step12_mobile


@pytest.fixture()
def isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "tracker.db"
    monkeypatch.setattr(tracker, "DB_PATH", path)
    monkeypatch.setattr(tracker, "DATA_DIR", tmp_path)
    return path


def sample_timeline_plan() -> dict:
    return tracker.timeline_plan_from_recording_parts(
        {
            "lv": "lv-test",
            "parts": [
                {"type": "gap", "duration_seconds": 5.0, "reason": "late_start"},
                {"type": "segment", "path": "first.mp4", "duration_seconds": 1800.0},
                {"type": "gap", "duration_seconds": 2.5, "reason": "restart"},
                {"type": "segment", "path": "second.mp4", "duration_seconds": 45.0},
            ],
        }
    )


def test_legacy_pipeline_destination_uses_broadcast_broadcaster_not_recording_account(
    isolated_db: Path,
) -> None:
    with tracker.connect() as conn:
        conn.execute(
            """
            INSERT INTO broadcast_archive_meta(lv, broadcaster_id, fetched_at)
            VALUES (?, ?, ?)
            """,
            ("lv-target", "broadcast-owner", datetime.now().isoformat()),
        )
        conn.commit()

    pipeline_data = tracker.build_legacy_pipeline_data(
        "lv-target",
        recording_segment_timeline=sample_timeline_plan(),
    )

    assert pipeline_data["account_id"] == "broadcast-owner"


def test_timeline_offsets_cover_segments_gaps_and_short_final_segment() -> None:
    plan = sample_timeline_plan()
    assert plan["total_duration_seconds"] == pytest.approx(1852.5)
    assert plan["segments"][0]["timeline_start_seconds"] == pytest.approx(5.0)
    assert plan["segments"][1]["timeline_start_seconds"] == pytest.approx(1807.5)
    assert tracker.select_recording_segment_for_timeline_second(plan, 1806.0) is None
    selected = tracker.select_recording_segment_for_timeline_second(plan, 1810.0)
    assert selected is not None
    assert selected["segment_index"] == 1
    assert selected["local_seconds"] == pytest.approx(2.5)


def test_transcript_rows_are_shifted_to_broadcast_timeline() -> None:
    rows = tracker.normalize_transcription_rows_for_timeline(
        [{"start_seconds": 1.25, "end_seconds": 3.5, "text": "区間2"}],
        timeline_offset_seconds=1807.5,
        segment_index_base=1_000_000,
    )
    assert rows == [
        {
            "start_seconds": 1808.75,
            "end_seconds": 1811.0,
            "text": "区間2",
            "local_start_seconds": 1.25,
            "local_end_seconds": 3.5,
            "timeline_offset_seconds": 1807.5,
            "segment_index": 1_000_000,
        }
    ]


def test_transcript_rows_are_clamped_to_canonical_segment_end() -> None:
    rows = tracker.normalize_transcription_rows_for_timeline(
        [
            {"start_seconds": 1794.0, "end_seconds": 1799.0, "text": "tail"},
            {"start_seconds": 1800.0, "end_seconds": 1799.0, "text": "outside"},
        ],
        timeline_offset_seconds=5.0,
        timeline_end_seconds=1803.734,
        segment_index_base=1_000_000,
    )

    assert rows[0]["local_start_seconds"] == pytest.approx(1794.0)
    assert rows[0]["local_end_seconds"] == pytest.approx(1798.734)
    assert rows[0]["start_seconds"] == pytest.approx(1799.0)
    assert rows[0]["end_seconds"] == pytest.approx(1803.734)
    assert rows[1]["local_start_seconds"] == pytest.approx(1798.734)
    assert rows[1]["local_end_seconds"] == pytest.approx(1798.734)
    assert rows[1]["start_seconds"] == pytest.approx(1803.734)
    assert rows[1]["end_seconds"] == pytest.approx(1803.734)
    assert all(row["end_seconds"] >= row["start_seconds"] for row in rows)
    assert all(row["local_end_seconds"] >= row["local_start_seconds"] for row in rows)


def test_final_alignment_validator_checks_segment_bounds_and_local_clock(
    isolated_db: Path,
) -> None:
    plan = sample_timeline_plan()
    stamp = tracker.now_micro()
    with tracker.connect() as conn:
        conn.execute(
            """
            INSERT INTO archive_transcript_segments
                (lv, segment_index, start_seconds, end_seconds, text, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lv-align",
                1_000_000,
                1808.5,
                1809.5,
                "valid",
                json.dumps({"local_start_seconds": 1.0, "timeline_offset_seconds": 1807.5}),
                stamp,
            ),
        )
        conn.commit()
        valid = tracker.validate_archive_timeline_alignment(conn, "lv-align", plan)
        conn.execute(
            "UPDATE archive_transcript_segments SET start_seconds = 1806.0 WHERE lv = ?",
            ("lv-align",),
        )
        conn.commit()
        invalid = tracker.validate_archive_timeline_alignment(conn, "lv-align", plan)
    assert valid["valid"] is True
    assert valid["invalid_transcript_count"] == 0
    assert invalid["valid"] is False
    assert invalid["invalid_transcript_count"] == 1


def test_segment_transcript_retry_is_idempotent_and_preserves_other_segments(isolated_db: Path) -> None:
    with tracker.connect() as conn:
        first = tracker.normalize_transcription_rows_for_timeline(
            [{"start": 0.0, "end": 1.0, "text": "first"}],
            timeline_offset_seconds=5.0,
        )
        second = tracker.normalize_transcription_rows_for_timeline(
            [{"start": 0.0, "end": 1.0, "text": "second"}],
            timeline_offset_seconds=1807.5,
            segment_index_base=1_000_000,
        )
        tracker.persist_transcription_rows(
            conn,
            "lv-test",
            first,
            source_audio_path="first.wav",
            model="test",
            replace_scope="source",
        )
        tracker.persist_transcription_rows(
            conn,
            "lv-test",
            second,
            source_audio_path="second.wav",
            model="test",
            replace_scope="source",
        )
        tracker.persist_transcription_rows(
            conn,
            "lv-test",
            first,
            source_audio_path="first.wav",
            model="test-retry",
            replace_scope="source",
        )
        conn.commit()
        rows = conn.execute(
            "SELECT text, source_audio_path, model FROM archive_transcript_segments ORDER BY start_seconds"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("first", "first.wav", "test-retry"),
        ("second", "second.wav", "test"),
    ]


def test_recording_event_accepts_datetime_payload(isolated_db: Path) -> None:
    value = datetime(2026, 7, 15, 12, 34, 56)
    with tracker.connect() as conn:
        tracker.record_recording_event(
            conn,
            lv="lv-test",
            broadcaster_id="1",
            broadcaster_name="test",
            watch_url="https://example.invalid/lv-test",
            recorder="test",
            pid=None,
            event_type="datetime_payload",
            event_at=tracker.now_micro(),
            started_at=None,
            ended_at=None,
            duration_us=None,
            exit_code=None,
            target_dir="",
            payload={"at": value},
        )
        conn.commit()
        payload = json.loads(conn.execute("SELECT payload_json FROM recording_events").fetchone()[0])
    assert payload["at"] == "2026-07-15 12:34:56"


def test_live_timeline_uses_every_started_event_and_ignores_file_ctime(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lv = "lv999000001"
    files = [
        tmp_path / f"{lv}_2026_0715_120008_first.mp4",
        tmp_path / f"{lv}_2026_0715_120020_second.mp4",
        tmp_path / f"{lv}_2026_0715_120035_third.mp4",
    ]
    for path in files:
        path.touch()
    durations = {files[0].name: 10.0, files[1].name: 12.0, files[2].name: 5.0}
    monkeypatch.setattr(tracker, "probe_media_duration_seconds", lambda path: durations[Path(path).name])

    broadcast_start = datetime(2026, 7, 15, 12, 0, 0)
    starts = [
        datetime(2026, 7, 15, 12, 0, 8),
        datetime(2026, 7, 15, 12, 0, 20),
        datetime(2026, 7, 15, 12, 0, 35),
    ]
    with tracker.connect() as conn:
        conn.execute(
            "INSERT INTO broadcast_archive_meta (lv, open_time, fetched_at) VALUES (?, ?, ?)",
            (lv, int(broadcast_start.timestamp()), tracker.now_micro()),
        )
        for index, started_at in enumerate(starts):
            tracker.record_recording_event(
                conn,
                lv=lv,
                broadcaster_id="1",
                broadcaster_name="test",
                watch_url=f"https://example.invalid/{lv}",
                recorder="test",
                pid=100 + index,
                event_type="started",
                event_at=started_at.isoformat(timespec="microseconds"),
                started_at=started_at.isoformat(timespec="microseconds"),
                ended_at=None,
                duration_us=None,
                exit_code=None,
                target_dir=str(tmp_path),
            )
        conn.commit()

    plan = tracker.build_recording_segment_timeline_plan(tmp_path, lv=lv)
    assert [row["start_time_source"] for row in plan["segments"]] == [
        "recording_events.started_at",
        "recording_events.started_at",
        "recording_events.started_at",
    ]
    assert [row["timeline_start_seconds"] for row in plan["segments"]] == pytest.approx([8.0, 20.0, 35.0])
    inter_gaps = [
        row["duration_seconds"]
        for row in plan["gaps"]
        if row.get("reason") == "segment_media_clock_gap"
    ]
    assert inter_gaps == pytest.approx([2.0, 3.0])
    assert plan["total_duration_seconds"] == pytest.approx(40.0)
    assert tracker.validate_recording_timeline_plan(plan, require_complete=True)["valid"] is True


def test_timeshift_timeline_is_explicit_zero_and_preserves_drop_order(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "dropped-second-name.mp4"
    second = tmp_path / "dropped-first-name.mp4"
    first.touch()
    second.touch()
    durations = {first.name: 4.0, second.name: 6.0}
    monkeypatch.setattr(tracker, "probe_media_duration_seconds", lambda path: durations[Path(path).name])
    plan = tracker.build_recording_segment_timeline_plan(
        tmp_path,
        lv="lv999000002",
        timeline_mode="timeshift",
        segment_paths=[first, second],
    )
    assert plan["timeline_mode"] == "timeshift"
    assert plan["initial_offset_seconds"] == 0.0
    assert [Path(row["path"]).name for row in plan["segments"]] == [first.name, second.name]
    assert [row["timeline_start_seconds"] for row in plan["segments"]] == pytest.approx([0.0, 4.0])
    assert plan["total_duration_seconds"] == pytest.approx(10.0)
    assert tracker.validate_recording_timeline_plan(plan, require_complete=True)["valid"] is True


def test_sentiment_round_trip_keeps_segment_identity_and_local_timing(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NICONICO_WATCH_APP_DB", str(isolated_db))
    with tracker.connect() as conn:
        stamp = tracker.now_micro()
        for segment_index, start in ((0, 8.5), (1_000_000, 20.5)):
            raw = {
                "local_start_seconds": 0.5,
                "local_end_seconds": 1.5,
                "timeline_offset_seconds": start - 0.5,
            }
            conn.execute(
                """
                INSERT INTO archive_transcript_segments
                    (lv, segment_index, start_seconds, end_seconds, text, source_audio_path,
                     model, raw_json, created_at)
                VALUES ('lv-emotion', ?, ?, ?, ?, ?, 'test', ?, ?)
                """,
                (
                    segment_index,
                    start,
                    start + 1.0,
                    f"row-{segment_index}",
                    f"segment-{segment_index}.wav",
                    json.dumps(raw),
                    stamp,
                ),
            )
        conn.commit()

    payload = archive_db.load_transcript_payload("lv-emotion")
    assert [row["segment_index"] for row in payload["transcripts"]] == [0, 1_000_000]
    for row in payload["transcripts"]:
        row["positive_score"] = 0.75
    archive_db.save_transcript_sentiment_scores("lv-emotion", payload["transcripts"])

    with tracker.connect() as conn:
        rows = conn.execute(
            "SELECT segment_index, raw_json FROM archive_transcript_segments ORDER BY segment_index"
        ).fetchall()
    assert [json.loads(row["raw_json"])["positive_score"] for row in rows] == [0.75, 0.75]
    assert [json.loads(row["raw_json"])["local_start_seconds"] for row in rows] == [0.5, 0.5]


def test_emotion_graph_uses_canonical_duration_and_resumes_after_gap() -> None:
    timeline = step12.create_timeline_blocks(
        {
            "transcripts": [
                {"segment_index": 0, "start": 5.0, "end": 7.0, "text": "first", "positive_score": 0.4},
                {
                    "segment_index": 1_000_000,
                    "start": 75.0,
                    "end": 77.0,
                    "text": "second",
                    "positive_score": 0.8,
                },
            ]
        },
        {"comments": []},
        "lv-emotion",
        {"video_duration": 100.0, "elapsed_time": "00:08:20"},
    )
    blocks = timeline["transcript_blocks"]
    assert [block["start_seconds"] for block in blocks] == list(range(0, 100, 10))
    assert blocks[0]["positive_score"] == pytest.approx(0.4)
    assert blocks[5]["positive_score"] == 0.0
    assert blocks[7]["positive_score"] == pytest.approx(0.8)
    series = step12.build_emotion_chart_series(
        blocks,
        {
            "segments": [
                {"timeline_start_seconds": 0.0, "timeline_end_seconds": 40.0},
                {"timeline_start_seconds": 50.0, "timeline_end_seconds": 100.0},
            ],
            "gaps": [{"timeline_start_seconds": 40.0, "timeline_end_seconds": 50.0}],
        },
    )
    gap_start_index = series["segments"].index(40.0)
    assert series["positive"][gap_start_index] is None
    assert 55.0 not in series["segments"]
    assert any(
        second > 50.0 and value == pytest.approx(0.8)
        for second, value in zip(series["segments"], series["positive"])
        if value is not None
    )


def test_emotion_graph_does_not_average_across_gap_inside_one_ten_second_block() -> None:
    series = step12.build_emotion_chart_series(
        [
            {
                "start_seconds": 900.0,
                "transcripts": [
                    {"start": 900.5, "positive_score": 0.2},
                    {"start": 910.2, "positive_score": 0.8},
                ],
            }
        ],
        {
            "segments": [
                {"timeline_start_seconds": 0.0, "timeline_end_seconds": 901.3},
                {"timeline_start_seconds": 909.87, "timeline_end_seconds": 1800.0},
            ],
            "gaps": [{"timeline_start_seconds": 901.3, "timeline_end_seconds": 909.87}],
        },
    )
    non_null_points = [
        second
        for second, value in zip(series["segments"], series["positive"])
        if value is not None
    ]
    assert non_null_points == pytest.approx([900.5, 910.2])
    assert not any(901.3 <= second < 909.87 for second in non_null_points)
    gap_points = [
        second
        for second, value in zip(series["segments"], series["positive"])
        if value is None
    ]
    assert gap_points == pytest.approx([901.3, 909.869999])


def test_mobile_emotion_payload_uses_final_transcript_times_and_gap_boundaries() -> None:
    payload = step12_mobile._emotion_payload(
        [
            {
                "start": 0,
                "center": 0.0,
                "positive": 0.0,
                "negative": 0.0,
                "transcripts": [
                    {"start": 11.2, "center": 0.3, "positive": 0.5, "negative": 0.2},
                    {"start": 75.4, "center": 0.2, "positive": 0.7, "negative": 0.1},
                ],
            }
        ],
        {"gaps": [{"timeline_start_seconds": 40.0, "timeline_end_seconds": 50.0}]},
    )
    assert payload["seconds"] == pytest.approx([11.2, 40.0, 49.999999, 75.4])
    assert payload["positive"] == [0.5, None, None, 0.7]


def test_rebase_recovers_local_time_from_previous_segment_offset_and_is_idempotent(
    isolated_db: Path,
    tmp_path: Path,
) -> None:
    video = tmp_path / "segment.mp4"
    audio = tmp_path / "segment.wav"
    video.touch()
    audio.touch()
    stamp = tracker.now_micro()
    with tracker.connect() as conn:
        conn.execute(
            """
            INSERT INTO recording_segments
                (lv, source_path, segment_index, status, timeline_start_seconds,
                 audio_wav_path, transcript_status, created_at, updated_at)
            VALUES ('lv-rebase', ?, 0, 'processed', 7.5, ?, 'done', ?, ?)
            """,
            (str(video), str(audio), stamp, stamp),
        )
        conn.execute(
            """
            INSERT INTO archive_transcript_segments
                (lv, segment_index, start_seconds, end_seconds, text, source_audio_path,
                 model, raw_json, created_at)
            VALUES ('lv-rebase', 0, 10.0, 11.0, 'text', ?, 'test', '{}', ?)
            """,
            (str(audio), stamp),
        )
        conn.commit()
    plan = tracker.timeline_plan_from_recording_parts(
        {
            "timeline_mode": "live",
            "parts": [
                {"type": "gap", "duration_seconds": 8.0},
                {
                    "type": "segment",
                    "path": str(video),
                    "duration_seconds": 20.0,
                    "start_time_source": "recording_events.started_at",
                },
            ],
        }
    )
    with tracker.connect() as conn:
        tracker.rebase_recording_segment_transcripts(conn, "lv-rebase", plan)
        conn.commit()
        first = conn.execute(
            "SELECT start_seconds, raw_json FROM archive_transcript_segments WHERE lv='lv-rebase'"
        ).fetchone()
        tracker.rebase_recording_segment_transcripts(conn, "lv-rebase", plan)
        conn.commit()
        second = conn.execute(
            "SELECT start_seconds, raw_json FROM archive_transcript_segments WHERE lv='lv-rebase'"
        ).fetchone()
    assert first["start_seconds"] == pytest.approx(10.5)
    assert second["start_seconds"] == pytest.approx(10.5)
    assert json.loads(second["raw_json"])["local_start_seconds"] == pytest.approx(2.5)


def test_rebase_clamps_existing_whisper_tail_to_canonical_segment_end(
    isolated_db: Path,
    tmp_path: Path,
) -> None:
    video = tmp_path / "lv350922790.mp4"
    audio = tmp_path / "lv350922790.wav"
    video.touch()
    audio.touch()
    stamp = tracker.now_micro()
    raw = {
        "segment_index": 574,
        "start_seconds": 1794.0,
        "end_seconds": 1799.0,
        "text": "tail",
        "local_start_seconds": 1794.0,
        "local_end_seconds": 1799.0,
        "timeline_offset_seconds": 0.0,
    }
    with tracker.connect() as conn:
        conn.execute(
            """
            INSERT INTO recording_segments
                (lv, source_path, segment_index, status, timeline_start_seconds,
                 duration_seconds, audio_wav_path, transcript_status, created_at, updated_at)
            VALUES ('lv350922790', ?, 0, 'processed', 0.0, 1798.734, ?, 'done', ?, ?)
            """,
            (str(video), str(audio), stamp, stamp),
        )
        conn.execute(
            """
            INSERT INTO archive_transcript_segments
                (lv, segment_index, start_seconds, end_seconds, text, source_audio_path,
                 model, raw_json, created_at)
            VALUES ('lv350922790', 574, 1794.0, 1799.0, 'tail', ?, 'test', ?, ?)
            """,
            (str(audio), json.dumps(raw), stamp),
        )
        conn.commit()
    plan = tracker.timeline_plan_from_recording_parts(
        {
            "timeline_mode": "timeshift",
            "parts": [
                {
                    "type": "segment",
                    "path": str(video),
                    "duration_seconds": 1798.734,
                }
            ],
        }
    )

    with tracker.connect() as conn:
        tracker.rebase_recording_segment_transcripts(conn, "lv350922790", plan)
        conn.commit()
        first = conn.execute(
            """
            SELECT start_seconds, end_seconds, raw_json
            FROM archive_transcript_segments
            WHERE lv = 'lv350922790'
            """
        ).fetchone()
        alignment = tracker.validate_archive_timeline_alignment(conn, "lv350922790", plan)
        tracker.rebase_recording_segment_transcripts(conn, "lv350922790", plan)
        conn.commit()
        second = conn.execute(
            """
            SELECT start_seconds, end_seconds, raw_json
            FROM archive_transcript_segments
            WHERE lv = 'lv350922790'
            """
        ).fetchone()

    first_raw = json.loads(first["raw_json"])
    second_raw = json.loads(second["raw_json"])
    assert first["start_seconds"] == pytest.approx(1794.0)
    assert first["end_seconds"] == pytest.approx(1798.734)
    assert first_raw["local_start_seconds"] == pytest.approx(1794.0)
    assert first_raw["local_end_seconds"] == pytest.approx(1798.734)
    assert first_raw["start_seconds"] == pytest.approx(first["start_seconds"])
    assert first_raw["end_seconds"] == pytest.approx(first["end_seconds"])
    assert first["end_seconds"] >= first["start_seconds"]
    assert first_raw["local_end_seconds"] >= first_raw["local_start_seconds"]
    assert alignment["valid"] is True
    assert alignment["invalid_transcript_count"] == 0
    assert second["start_seconds"] == pytest.approx(first["start_seconds"])
    assert second["end_seconds"] == pytest.approx(first["end_seconds"])
    assert second_raw == first_raw


def test_comment_offset_confirmation_rewrites_desktop_and_mobile_html_and_locks_state(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lv = "lv999000003"
    target = tmp_path / lv
    target.mkdir()
    desktop = target / f"{lv}_title.html"
    mobile = target / f"{lv}_title_mobile.html"
    marker = (
        '<script id="nico-comment-offset-state" type="application/json">'
        '{"lv":"lv999000003","offsetSeconds":0,"confirmed":false,"confirmToken":"token"}'
        "</script>"
    )
    desktop.write_text(f"<html>{marker}</html>", encoding="utf-8")
    mobile.write_text(f"<html>{marker}</html>", encoding="utf-8")
    monkeypatch.setattr(tracker, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(tracker, "broadcast_target_dir", lambda *_args, **_kwargs: target)
    with tracker.connect() as conn:
        conn.execute(
            "INSERT INTO broadcast_archive_meta (lv, broadcaster_id, fetched_at) VALUES (?, '1', ?)",
            (lv, tracker.now_micro()),
        )
        stamp = tracker.now_micro()
        conn.execute(
            """
            INSERT INTO archive_comment_time_adjustments
                (lv, offset_seconds, confirmed, confirm_token, created_at, updated_at)
            VALUES (?, 0, 0, 'token', ?, ?)
            """,
            (lv, stamp, stamp),
        )
        conn.commit()

    result = tracker.confirm_archive_comment_offset(lv, 7, "token")
    assert result["confirmed"] is True
    assert len(result["html_paths"]) == 2
    for path in (desktop, mobile):
        source = path.read_text(encoding="utf-8")
        match = tracker.COMMENT_OFFSET_STATE_PATTERN.search(source)
        assert match is not None
        state_match = __import__("re").search(
            r'<script id="nico-comment-offset-state" type="application/json">(.*?)</script>',
            source,
        )
        assert state_match is not None
        state = json.loads(state_match.group(1))
        assert state["offsetSeconds"] == 7
        assert state["confirmed"] is True
    with tracker.connect() as conn:
        row = conn.execute(
            "SELECT offset_seconds, confirmed FROM archive_comment_time_adjustments WHERE lv = ?",
            (lv,),
        ).fetchone()
    assert tuple(row) == (7, 1)
    assert tracker.confirm_archive_comment_offset(lv, 7, "token")["already_confirmed"] is True
    with pytest.raises(ValueError, match="already confirmed"):
        tracker.confirm_archive_comment_offset(lv, 8, "token")


def test_generated_html_detection_excludes_completed_timeshift_lv(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lv = "lv999000004"
    target = tmp_path / lv
    target.mkdir()
    html_path = target / f"{lv}_done.html"
    html_path.write_text("done", encoding="utf-8")
    monkeypatch.setattr(tracker, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(tracker, "broadcast_target_dir", lambda *_args, **_kwargs: target)
    with tracker.connect() as conn:
        conn.execute(
            "INSERT INTO broadcast_archive_meta (lv, broadcaster_id, fetched_at) VALUES (?, '1', ?)",
            (lv, tracker.now_micro()),
        )
        conn.commit()
    assert tracker.existing_generated_archive_html(lv) == html_path.resolve()


def test_audio_alignment_plan_matches_real_timeshift_stream_offsets() -> None:
    delayed = tracker.build_audio_alignment_plan(
        {
            "streams": [
                {"codec_type": "video", "start_time": "0.000000", "duration": "1796.466667"},
                {"codec_type": "audio", "start_time": "6.018000", "duration": "1790.549333"},
            ],
            "format": {"start_time": "0.000000", "duration": "1796.567333"},
        }
    )
    assert delayed["target_samples"] == 28_745_077
    assert delayed["leading_silence_samples"] == 96_288
    assert delayed["head_trim_samples"] == 0

    ten_milliseconds = tracker.build_audio_alignment_plan(
        {
            "streams": [
                {"codec_type": "video", "start_time": "0.000000", "duration": "1212.000000"},
                {"codec_type": "audio", "start_time": "0.010000", "duration": "1211.989333"},
            ],
            "format": {"start_time": "0.000000", "duration": "1212.000000"},
        }
    )
    assert ten_milliseconds["target_samples"] == 19_392_000
    assert ten_milliseconds["leading_silence_samples"] == 160
    assert ten_milliseconds["head_trim_samples"] == 0


def test_extract_audio_uses_alignment_filter_and_atomic_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = tmp_path / "source.mp4"
    wav_path = tmp_path / "source.wav"
    mp3_path = tmp_path / "source.mp3"
    video.touch()
    wav_path.write_bytes(b"old-wav")
    mp3_path.write_bytes(b"old-mp3")
    alignment = tracker.build_audio_alignment_plan(
        {
            "streams": [
                {"codec_type": "video", "start_time": "0", "duration": "1796.466667"},
                {"codec_type": "audio", "start_time": "6.018", "duration": "1790.549333"},
            ],
            "format": {"start_time": "0", "duration": "1796.567333"},
        }
    )
    commands: list[list[str]] = []

    def fake_logged(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(f"staged-{len(commands)}".encode())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tracker, "probe_media_audio_timeline", lambda *_args, **_kwargs: alignment)
    monkeypatch.setattr(tracker, "probe_media_duration_seconds", lambda *_args, **_kwargs: 1796.567333)
    monkeypatch.setattr(tracker, "run_subprocess_with_stage_log", fake_logged)

    result = tracker.extract_audio_from_video(video, wav_path, mp3_path=mp3_path)

    assert wav_path.read_bytes() == b"staged-1"
    assert mp3_path.read_bytes() == b"staged-2"
    assert result["audio_alignment"] == alignment
    extract_filter = commands[0][commands[0].index("-af") + 1]
    assert "adelay=96288S:all=1" in extract_filter
    assert "apad=whole_len=28745077" in extract_filter
    assert "atrim=end_sample=28745077" in extract_filter
    assert Path(commands[0][-1]) != wav_path
    assert Path(commands[0][-1]).suffix == ".wav"
    assert Path(commands[1][-1]) != mp3_path
    assert not Path(commands[0][-1]).exists()
    assert not Path(commands[1][-1]).exists()


def test_extract_audio_failure_keeps_existing_outputs_and_removes_parts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = tmp_path / "source.mp4"
    wav_path = tmp_path / "source.wav"
    mp3_path = tmp_path / "source.mp3"
    video.touch()
    wav_path.write_bytes(b"old-wav")
    mp3_path.write_bytes(b"old-mp3")
    alignment = tracker.build_audio_alignment_plan(
        {
            "streams": [
                {"codec_type": "video", "start_time": "0", "duration": "10"},
                {"codec_type": "audio", "start_time": "0", "duration": "10"},
            ],
            "format": {"start_time": "0", "duration": "10"},
        }
    )
    staged_paths: list[Path] = []

    def fail_second_command(command, **_kwargs):
        output = Path(command[-1])
        staged_paths.append(output)
        output.write_bytes(b"partial")
        if len(staged_paths) == 2:
            raise RuntimeError("encode interrupted")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tracker, "probe_media_audio_timeline", lambda *_args, **_kwargs: alignment)
    monkeypatch.setattr(tracker, "probe_media_duration_seconds", lambda *_args, **_kwargs: 10.0)
    monkeypatch.setattr(tracker, "run_subprocess_with_stage_log", fail_second_command)

    with pytest.raises(RuntimeError, match="encode interrupted"):
        tracker.extract_audio_from_video(video, wav_path, mp3_path=mp3_path)

    assert wav_path.read_bytes() == b"old-wav"
    assert mp3_path.read_bytes() == b"old-mp3"
    assert staged_paths
    assert all(not path.exists() for path in staged_paths)


def test_real_ffmpeg_extract_pads_delayed_audio_to_canonical_duration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe are required")
    video = tmp_path / "delayed.mp4"
    wav_path = tmp_path / "delayed.wav"
    mp3_path = tmp_path / "delayed.mp3"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=10:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-filter_complex",
            "[1:a]asetpts=PTS+0.5/TB[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(tracker, "TMP_DIR", tmp_path / "runtime")

    alignment = tracker.probe_media_audio_timeline(video)
    assert alignment["leading_silence_samples"] > 6_000
    tracker.extract_audio_from_video(video, wav_path, mp3_path=mp3_path)

    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getnchannels() == 1
        assert wav_file.getnframes() == alignment["target_samples"]
        silence_frames = max(1, int(alignment["leading_silence_samples"]) - 128)
        assert set(wav_file.readframes(silence_frames)) <= {0}
    assert abs(tracker.probe_media_duration_seconds(mp3_path) - 2.0) <= 0.25


def test_recording_segment_state_upsert_tracks_audio_and_completion(
    isolated_db: Path,
    tmp_path: Path,
) -> None:
    segment = tmp_path / "segment.mp4"
    wav = tmp_path / "segment.wav"
    mp3 = tmp_path / "segment.mp3"
    for path in (segment, wav, mp3):
        path.touch()
    with tracker.connect() as conn:
        tracker.update_recording_segment_transcript_state(
            conn,
            lv="lv-test",
            broadcaster_id="1",
            segment_path=segment,
            segment_index=1,
            started_at="2026-07-15T12:30:00",
            ended_at="2026-07-15T12:30:25",
            duration_seconds=25.0,
            timeline_start_seconds=1802.5,
            status="running",
        )
        tracker.update_recording_segment_transcript_state(
            conn,
            lv="lv-test",
            broadcaster_id="1",
            segment_path=segment,
            segment_index=1,
            started_at="2026-07-15T12:30:00",
            ended_at="2026-07-15T12:30:25",
            duration_seconds=25.0,
            timeline_start_seconds=1802.5,
            status="done",
            wav_path=wav,
            mp3_path=mp3,
            model="test:model",
        )
        conn.commit()
        row = conn.execute("SELECT * FROM recording_segments WHERE lv = 'lv-test'").fetchone()
    assert row["transcript_status"] == "done"
    assert row["audio_wav_path"] == str(wav)
    assert row["audio_mp3_path"] == str(mp3)
    assert row["timeline_start_seconds"] == pytest.approx(1802.5)


def test_segment_screenshot_selects_video_and_local_second_across_gap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.touch()
    second.touch()
    plan = tracker.timeline_plan_from_recording_parts(
        {
            "parts": [
                {"type": "gap", "duration_seconds": 5.0},
                {"type": "segment", "path": str(first), "duration_seconds": 10.0},
                {"type": "gap", "duration_seconds": 5.0},
                {"type": "segment", "path": str(second), "duration_seconds": 10.0},
            ]
        }
    )
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).touch()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(step09.subprocess, "run", fake_run)
    count = step09.generate_segment_screenshots(plan, tmp_path / "shots", 30.0, 150, 80)
    assert count == 4
    media_commands = [command for command in commands if "-ss" in command]
    assert [(command[command.index("-ss") + 1], Path(command[command.index("-i") + 1]).name) for command in media_commands] == [
        ("5.000000", "first.mp4"),
        ("0.000000", "second.mp4"),
        ("9.999000", "second.mp4"),
    ]
    assert (tmp_path / "shots" / "0.jpg").exists()


def test_segment_screenshot_rerun_removes_old_axis_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = tmp_path / "segment.mp4"
    video.touch()
    shots = tmp_path / "shots"
    shots.mkdir()
    (shots / "9990.jpg").touch()
    plan = tracker.timeline_plan_from_recording_parts(
        {"parts": [{"type": "segment", "path": str(video), "duration_seconds": 10.0}]}
    )

    def fake_run(command, **_kwargs):
        Path(command[-1]).touch()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(step09.subprocess, "run", fake_run)
    assert step09.generate_segment_screenshots(plan, shots, 10.0, 80, 60) == 2
    assert not (shots / "9990.jpg").exists()
    assert (shots / "0.jpg").exists()
    assert (shots / "10.jpg").exists()


def test_mp3_concat_uses_only_segment_audio_and_silent_gap(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_video = tmp_path / "first.mp4"
    second_video = tmp_path / "second.mp4"
    first_audio = tmp_path / "first.mp3"
    second_audio = tmp_path / "second.mp3"
    for path in (first_video, second_video, first_audio, second_audio):
        path.touch()
    plan = tracker.timeline_plan_from_recording_parts(
        {
            "parts": [
                {"type": "segment", "path": str(first_video), "duration_seconds": 10.0},
                {"type": "gap", "duration_seconds": 2.0},
                {"type": "segment", "path": str(second_video), "duration_seconds": 10.0},
            ]
        }
    )
    with tracker.connect() as conn:
        stamp = tracker.now_micro()
        for index, (video, audio) in enumerate(((first_video, first_audio), (second_video, second_audio))):
            conn.execute(
                """
                INSERT INTO recording_segments
                    (lv, source_path, file_type, segment_index, status, audio_mp3_path,
                     transcript_status, created_at, updated_at)
                VALUES (?, ?, 'mp4', ?, 'processed', ?, 'done', ?, ?)
                """,
                ("lv-test", str(video), index, str(audio), stamp, stamp),
            )
        conn.commit()
    commands: list[list[str]] = []

    def fake_gap(path, _duration, *, lv=None):
        Path(path).touch()
        return Path(path)

    def fake_logged(command, **_kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"output")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tracker, "create_silent_gap_mp3", fake_gap)
    monkeypatch.setattr(
        tracker,
        "probe_media_duration_seconds",
        lambda path: 10.0 if Path(path) in {first_audio, second_audio} else 22.0,
    )
    monkeypatch.setattr(tracker, "run_subprocess_with_stage_log", fake_logged)
    output = tmp_path / "lv-test_audio.mp3"
    result = tracker.concat_recording_segment_audio("lv-test", plan, output)
    assert result["mp3_path"] == str(output)
    assert len(commands) == 1
    assert any("concat=n=3:v=0:a=1[joined]" in argument for argument in commands[0])
    assert all(not argument.lower().endswith(".mp4") for argument in commands[0])


def test_mp3_concat_does_not_use_failed_segment_audio(
    isolated_db: Path,
    tmp_path: Path,
) -> None:
    video = tmp_path / "failed.mp4"
    audio = tmp_path / "failed.mp3"
    video.touch()
    audio.touch()
    plan = tracker.timeline_plan_from_recording_parts(
        {"parts": [{"type": "segment", "path": str(video), "duration_seconds": 10.0}]}
    )
    with tracker.connect() as conn:
        stamp = tracker.now_micro()
        conn.execute(
            """
            INSERT INTO recording_segments
                (lv, source_path, file_type, segment_index, status, audio_mp3_path,
                 transcript_status, created_at, updated_at)
            VALUES ('lv-failed', ?, 'mp4', 0, 'failed', ?, 'failed', ?, ?)
            """,
            (str(video), str(audio), stamp, stamp),
        )
        conn.commit()

    with pytest.raises(FileNotFoundError, match="録画区間MP3"):
        tracker.concat_recording_segment_audio("lv-failed", plan, tmp_path / "output.mp3")


def test_mp3_concat_rejects_truncated_segment_before_ffmpeg(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = tmp_path / "lv350953394.mp4"
    audio = tmp_path / "lv350953394.mp3"
    video.touch()
    audio.touch()
    plan = tracker.timeline_plan_from_recording_parts(
        {"parts": [{"type": "segment", "path": str(video), "duration_seconds": 1212.0}]}
    )
    with tracker.connect() as conn:
        stamp = tracker.now_micro()
        conn.execute(
            """
            INSERT INTO recording_segments
                (lv, source_path, file_type, segment_index, status, audio_mp3_path,
                 transcript_status, created_at, updated_at)
            VALUES ('lv350953394', ?, 'mp4', 0, 'processed', ?, 'done', ?, ?)
            """,
            (str(video), str(audio), stamp, stamp),
        )
        conn.commit()
    monkeypatch.setattr(tracker, "probe_media_duration_seconds", lambda *_args: 445.6066)

    with pytest.raises(RuntimeError, match="drift=-766.393400s"):
        tracker.concat_recording_segment_audio("lv350953394", plan, tmp_path / "output.mp3")


def test_mp3_concat_failure_keeps_existing_output_and_removes_part(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    video = tmp_path / "segment.mp4"
    audio = tmp_path / "segment.mp3"
    output = tmp_path / "output.mp3"
    video.touch()
    audio.touch()
    output.write_bytes(b"old-output")
    plan = tracker.timeline_plan_from_recording_parts(
        {"parts": [{"type": "segment", "path": str(video), "duration_seconds": 1.0}]}
    )
    with tracker.connect() as conn:
        stamp = tracker.now_micro()
        conn.execute(
            """
            INSERT INTO recording_segments
                (lv, source_path, file_type, segment_index, status, audio_mp3_path,
                 transcript_status, created_at, updated_at)
            VALUES ('lv-atomic', ?, 'mp4', 0, 'processed', ?, 'done', ?, ?)
            """,
            (str(video), str(audio), stamp, stamp),
        )
        conn.commit()
    staged_paths: list[Path] = []

    def fail_concat(command, **_kwargs):
        staged = Path(command[-1])
        staged_paths.append(staged)
        staged.write_bytes(b"partial-output")
        raise RuntimeError("concat interrupted")

    monkeypatch.setattr(tracker, "probe_media_duration_seconds", lambda *_args: 1.0)
    monkeypatch.setattr(tracker, "run_subprocess_with_stage_log", fail_concat)

    with pytest.raises(RuntimeError, match="concat interrupted"):
        tracker.concat_recording_segment_audio("lv-atomic", plan, output)

    assert output.read_bytes() == b"old-output"
    assert staged_paths
    assert all(not path.exists() for path in staged_paths)


def test_real_ffmpeg_mp3_concat_keeps_silent_gap(
    isolated_db: Path,
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    first_video = tmp_path / "first.mp4"
    second_video = tmp_path / "second.mp4"
    first_audio = tmp_path / "first.mp3"
    second_audio = tmp_path / "second.mp3"
    first_video.touch()
    second_video.touch()
    for frequency, output in ((440, first_audio), (660, second_audio)):
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=16000:duration=1",
                "-ac",
                "1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                str(output),
            ],
            check=True,
            capture_output=True,
        )
    plan = tracker.timeline_plan_from_recording_parts(
        {
            "parts": [
                {"type": "segment", "path": str(first_video), "duration_seconds": 1.0},
                {"type": "gap", "duration_seconds": 0.5},
                {"type": "segment", "path": str(second_video), "duration_seconds": 1.0},
            ]
        }
    )
    with tracker.connect() as conn:
        stamp = tracker.now_micro()
        for index, (video, audio) in enumerate(((first_video, first_audio), (second_video, second_audio))):
            conn.execute(
                """
                INSERT INTO recording_segments
                    (lv, source_path, file_type, segment_index, status, audio_mp3_path,
                     transcript_status, created_at, updated_at)
                VALUES (?, ?, 'mp4', ?, 'processed', ?, 'done', ?, ?)
                """,
                ("lv-real", str(video), index, str(audio), stamp, stamp),
            )
        conn.commit()
    output = tmp_path / "lv-real_audio.mp3"
    result = tracker.concat_recording_segment_audio("lv-real", plan, output)
    duration = tracker.probe_media_duration_seconds(output)
    assert 2.3 <= duration <= 2.8
    assert result["duration_drift_seconds"] == pytest.approx(duration - 2.5, abs=0.001)


def test_finalize_skips_video_concat_and_whole_audio_transcription(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    plan = tracker.timeline_plan_from_recording_parts(
        {
            "timeline_mode": "timeshift",
            "parts": [{"type": "segment", "path": str(source / "one.mp4"), "duration_seconds": 25.0}],
        }
    )
    (source / "one.mp4").touch()
    monkeypatch.setattr(tracker, "load_config", lambda: SimpleNamespace(recording_account_id="1"))
    monkeypatch.setattr(tracker, "broadcast_target_dir", lambda *_args, **_kwargs: target)
    monkeypatch.setattr(tracker, "register_recording_segments", lambda *_args, **_kwargs: [{"path": "one"}])
    monkeypatch.setattr(tracker, "register_recording_gaps_from_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(tracker, "build_recording_segment_timeline_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(
        tracker,
        "ensure_recording_segment_transcriptions",
        lambda *_args, **_kwargs: [{"segment_path": "one.mp4", "reason": "disabled", "transcribed": False}],
    )

    def fail_video_concat(*_args, **_kwargs):
        raise AssertionError("video concat must not run")

    def fail_whole_transcribe(*_args, **_kwargs):
        raise AssertionError("whole-broadcast transcription must not run")

    monkeypatch.setattr(tracker, "concat_slnico_segments_with_gaps", fail_video_concat)
    monkeypatch.setattr(tracker, "transcribe_audio_with_faster_whisper", fail_whole_transcribe)
    monkeypatch.setattr(tracker, "transcribe_audio_with_whisperx", fail_whole_transcribe)
    monkeypatch.setattr(
        tracker,
        "concat_recording_segment_audio",
        lambda _lv, _plan, output: {"mp3_path": str(output), "total_duration_seconds": 25.0},
    )
    monkeypatch.setattr(tracker, "export_legacy_transcript_file_from_db", lambda *_args, **_kwargs: {"segments": 0})
    monkeypatch.setattr(tracker, "export_legacy_archive_files_from_ndgr", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(tracker, "record_broadcaster_monitor_special_user_hits_from_archive", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(tracker, "run_legacy_archiver_steps", lambda *_args, **_kwargs: {"steps": {}})
    result = tracker.run_finalize_pipeline_for_lv(
        "lv-test",
        broadcaster_id="1",
        input_dir=source,
        transcribe=False,
    )
    assert result["video_concat_skipped"] is True
    assert result["joined_video_path"] == ""
    assert result["mp3_path"].endswith("lv-test_audio.mp3")


def test_closed_segment_postprocess_defers_transcription_until_broadcast_end(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started: list[dict] = []
    segment_path = tmp_path / "lv-test_2026_0715_120000_segment.mp4"
    segment_path.touch()

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs)

        def start(self):
            started[-1]["started"] = True
            started[-1]["target"]()

    monkeypatch.setattr(tracker.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        tracker,
        "recording_segment_for_process_exit",
        lambda *_args, **_kwargs: {"path": str(segment_path), "selection_basis": "test"},
    )
    monkeypatch.setattr(tracker, "ensure_recording_segment_mp4", lambda *_args, **_kwargs: segment_path)

    def fail_realtime_transcription(*_args, **_kwargs):
        raise AssertionError("closed segments must not be transcribed before broadcast end")

    monkeypatch.setattr(tracker, "process_completed_recording_segment", fail_realtime_transcription)
    tracker.start_segment_mp4_conversion_after_exit(
        lv="lv-test",
        broadcaster_id="1",
        broadcaster_name="test",
        watch_url="https://example.invalid/lv-test",
        recorder="test",
        pid=123,
        started_at="2026-07-15T12:00:00",
        ended_at="2026-07-15T12:30:00",
        exit_code=0,
        target_dir="target",
    )
    assert started[0]["daemon"] is True
    assert started[0]["started"] is True
    assert started[0]["name"] == "segment-mp4-lv-test-123"
    with tracker.connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM recording_events WHERE lv = 'lv-test' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    payload = json.loads(str(row["payload_json"]))
    assert payload["segment_pipeline"]["transcribed"] is False
    assert payload["segment_pipeline"]["reason"] == "deferred_until_broadcast_end"


def test_finalize_queue_is_durable_deduplicated_and_strictly_serial(
    isolated_db: Path,
) -> None:
    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn, lv="lv-first", broadcaster_id="1", target_dir="first"
        )
        tracker.mark_finalize_queue_ready(conn, "lv-first")
        assert tracker.reserve_finalize_queue_item(
            conn, lv="lv-second", broadcaster_id="2", target_dir="second"
        )
        tracker.mark_finalize_queue_ready(conn, "lv-second")
        conn.execute(
            "UPDATE finalize_queue SET queued_at = '2026-07-16T10:00:00.000001' WHERE lv = 'lv-first'"
        )
        conn.execute(
            "UPDATE finalize_queue SET queued_at = '2026-07-16T10:00:00.000002' WHERE lv = 'lv-second'"
        )
        assert not tracker.reserve_finalize_queue_item(
            conn, lv="lv-first", broadcaster_id="1", target_dir="duplicate"
        )
        conn.commit()

    first = tracker.claim_next_finalize_queue_item()
    assert first is not None
    assert first["lv"] == "lv-first"
    assert tracker.claim_next_finalize_queue_item() is None

    tracker.finish_finalize_queue_item("lv-first", success=True)
    second = tracker.claim_next_finalize_queue_item()
    assert second is not None
    assert second["lv"] == "lv-second"


def test_finalize_dispatcher_serializes_timeshift_and_live_in_fifo_order(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_finalize_dispatcher as dispatcher

    monkeypatch.setattr(tracker, "TMP_DIR", isolated_db.parent / "runtime")

    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn,
            lv="lv-timeshift",
            broadcaster_id="1",
            target_dir="timeshift",
            source_kind="timeshift_local",
            timeline_mode="timeshift",
            input_dir="input",
            segment_paths=["first.mp4", "second.mp4"],
        )
        tracker.mark_finalize_queue_ready(conn, "lv-timeshift")
        assert tracker.reserve_finalize_queue_item(
            conn,
            lv="lv-live",
            broadcaster_id="2",
            target_dir="live",
        )
        tracker.mark_finalize_queue_ready(conn, "lv-live")
        for index, lv in enumerate(("lv-timeshift", "lv-live"), start=1):
            queued_at = f"2026-07-16T10:00:00.00000{index}"
            conn.execute(
                "UPDATE finalize_queue SET created_at = ?, queued_at = ? WHERE lv = ?",
                (queued_at, queued_at, lv),
            )
        conn.commit()

    started: list[dict] = []

    class FakeProcess:
        def __init__(self, kwargs: dict) -> None:
            self.kwargs = kwargs
            self.pid = 9000 + len(started)
            self.returncode: int | None = None

        def wait(self) -> int:
            started.append(self.kwargs)
            Path(str(self.kwargs["result_json_path"])).write_text(
                json.dumps({"lv": self.kwargs["lv"]}),
                encoding="utf-8",
            )
            return 0

        def poll(self) -> int | None:
            if self.returncode is None:
                self.returncode = self.wait()
            return self.returncode

    monkeypatch.setattr(
        tracker,
        "start_visible_finalize_pipeline_process",
        lambda **kwargs: FakeProcess(kwargs),
    )
    monkeypatch.setattr(dispatcher.time, "sleep", lambda _seconds: None)

    assert dispatcher.drain_queue() == 0
    assert [row["lv"] for row in started] == ["lv-timeshift", "lv-live"]
    assert started[0]["timeline_mode"] == "timeshift"
    assert started[0]["input_dir"] == "input"
    assert started[0]["prepare_live_inputs"] is False
    assert started[0]["queue_attempt"] == 1
    assert [str(path) for path in started[0]["segment_paths"]] == ["first.mp4", "second.mp4"]
    assert started[1]["timeline_mode"] == "live"
    assert started[1]["prepare_live_inputs"] is True
    assert started[1]["queue_attempt"] == 1
    with tracker.connect() as conn:
        rows = conn.execute(
            "SELECT lv, status FROM finalize_queue ORDER BY queued_at"
        ).fetchall()
    assert [(row["lv"], row["status"]) for row in rows] == [
        ("lv-timeshift", "done"),
        ("lv-live", "done"),
    ]


def test_failed_finalize_queue_item_can_be_reserved_and_queued_again(
    isolated_db: Path,
) -> None:
    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn, lv="lv-retry", broadcaster_id="1", target_dir="first"
        )
        tracker.mark_finalize_queue_ready(conn, "lv-retry")
        conn.commit()

    tracker.finish_finalize_queue_item("lv-retry", success=False, error="first failure")

    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn,
            lv="lv-retry",
            broadcaster_id="2",
            target_dir="second",
            source_kind="timeshift_local",
            timeline_mode="timeshift",
            input_dir="input",
            segment_paths=["retry.mp4"],
        )
        tracker.mark_finalize_queue_ready(conn, "lv-retry")
        conn.commit()
        row = conn.execute(
            """
            SELECT status, broadcaster_id, target_dir, source_kind, timeline_mode,
                   input_dir, segment_paths_json, attempts, error
            FROM finalize_queue
            WHERE lv = 'lv-retry'
            """
        ).fetchone()

    assert row is not None
    assert row["status"] == "queued"
    assert row["broadcaster_id"] == "2"
    assert row["target_dir"] == "second"
    assert row["source_kind"] == "timeshift_local"
    assert row["timeline_mode"] == "timeshift"
    assert row["input_dir"] == "input"
    assert json.loads(str(row["segment_paths_json"])) == ["retry.mp4"]
    assert row["attempts"] == 0
    assert row["error"] is None


def test_dead_live_preparing_owner_is_recovered_before_later_timeshift(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn,
            lv="lv-live-preparing",
            broadcaster_id="1",
            target_dir="live",
            source_kind="live",
        )
        assert tracker.reserve_finalize_queue_item(
            conn,
            lv="lv-timeshift-later",
            broadcaster_id="2",
            target_dir="timeshift",
            source_kind="timeshift_local",
            timeline_mode="timeshift",
        )
        tracker.mark_finalize_queue_ready(conn, "lv-timeshift-later")
        conn.execute(
            """
            UPDATE finalize_queue
            SET created_at = '2026-07-16T10:00:00.000001',
                updated_at = '2026-07-16T10:00:00.000001'
            WHERE lv = 'lv-live-preparing'
            """
        )
        conn.execute(
            """
            UPDATE finalize_queue
            SET created_at = '2026-07-16T10:00:00.000002',
                queued_at = '2026-07-16T10:00:00.000002'
            WHERE lv = 'lv-timeshift-later'
            """
        )
        conn.commit()

    assert tracker.claim_next_finalize_queue_item() is None

    monkeypatch.setattr(tracker, "is_process_running", lambda _pid: False)
    assert tracker.reconcile_oldest_preparing_finalize_item() == "recovered"

    first = tracker.claim_next_finalize_queue_item()
    assert first is not None
    assert first["lv"] == "lv-live-preparing"
    assert tracker.claim_next_finalize_queue_item() is None

    tracker.finish_finalize_queue_item("lv-live-preparing", success=True)
    second = tracker.claim_next_finalize_queue_item()
    assert second is not None
    assert second["lv"] == "lv-timeshift-later"


def test_waiting_running_item_restarts_missing_dispatcher(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn, lv="lv-running", broadcaster_id="1", target_dir="target"
        )
        tracker.mark_finalize_queue_ready(conn, "lv-running")
        conn.commit()
    assert tracker.claim_next_finalize_queue_item() is not None

    starts: list[str] = []

    def restart_dispatcher() -> bool:
        starts.append("start")
        tracker.finish_finalize_queue_item(
            "lv-running", success=True, result={"lv": "lv-running", "recovered": True}
        )
        return True

    monkeypatch.setattr(tracker, "start_finalize_dispatcher_process", restart_dispatcher)
    monkeypatch.setattr(tracker.time, "sleep", lambda _seconds: None)

    result = tracker.wait_for_finalize_queue_item("lv-running", timeout_seconds=2)

    assert starts == ["start"]
    assert result == {"lv": "lv-running", "recovered": True}


def test_active_dispatcher_heartbeat_prevents_duplicate_process_start(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn, lv="lv-heartbeat", broadcaster_id="1", target_dir="target"
        )
        tracker.mark_finalize_queue_ready(conn, "lv-heartbeat")
        conn.commit()
    tracker.update_finalize_dispatcher_state(4321, started=True)

    monkeypatch.setattr(tracker, "is_process_running", lambda pid: int(pid or 0) == 4321)
    monkeypatch.setattr(tracker, "_FINALIZE_DISPATCHER_PROCESS", None)

    def fail_spawn(*_args, **_kwargs):
        raise AssertionError("an active dispatcher must suppress duplicate process creation")

    monkeypatch.setattr(tracker.subprocess, "Popen", fail_spawn)

    assert tracker.start_finalize_dispatcher_process() is True


def test_interrupted_timeshift_worker_recovers_from_result_without_event(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_finalize_dispatcher as dispatcher

    runtime_dir = isolated_db.parent / "runtime"
    monkeypatch.setattr(tracker, "TMP_DIR", runtime_dir)
    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn,
            lv="lv-timeshift-recover",
            broadcaster_id="1",
            target_dir="",
            source_kind="timeshift_local",
            timeline_mode="timeshift",
        )
        tracker.mark_finalize_queue_ready(conn, "lv-timeshift-recover")
        conn.commit()
    claimed = tracker.claim_next_finalize_queue_item()
    assert claimed is not None
    tracker.set_finalize_queue_worker_pid("lv-timeshift-recover", 9876)
    result_path = runtime_dir / "finalize_results" / "lv-timeshift-recover_attempt1.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps({"lv": "lv-timeshift-recover", "from_result": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tracker, "is_process_running", lambda _pid: False)

    assert dispatcher.reconcile_interrupted_worker() is True

    with tracker.connect() as conn:
        row = conn.execute(
            "SELECT status, result_json FROM finalize_queue WHERE lv = 'lv-timeshift-recover'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "done"
    assert json.loads(str(row["result_json"]))["from_result"] is True


def test_pidless_running_item_waits_for_worker_self_registration_grace(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_finalize_dispatcher as dispatcher

    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn, lv="lv-launch-grace", broadcaster_id="1", target_dir="target"
        )
        tracker.mark_finalize_queue_ready(conn, "lv-launch-grace")
        conn.commit()
    assert tracker.claim_next_finalize_queue_item() is not None
    monkeypatch.setattr(tracker, "is_process_running", lambda _pid: False)
    monkeypatch.setattr(dispatcher, "recorded_worker_outcome", lambda *_args: "")
    monkeypatch.setattr(dispatcher.time, "sleep", lambda _seconds: None)

    assert dispatcher.reconcile_interrupted_worker() is True
    with tracker.connect() as conn:
        row = conn.execute(
            "SELECT status, worker_pid FROM finalize_queue WHERE lv = 'lv-launch-grace'"
        ).fetchone()
    assert row is not None
    assert row["status"] == "running"
    assert row["worker_pid"] is None


def test_worker_self_registration_wins_launcher_pid_race(
    isolated_db: Path,
) -> None:
    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn, lv="lv-worker-pid", broadcaster_id="1", target_dir="target"
        )
        tracker.mark_finalize_queue_ready(conn, "lv-worker-pid")
        conn.commit()
    claimed = tracker.claim_next_finalize_queue_item()
    assert claimed is not None
    attempt = int(claimed["attempts"])

    tracker.set_finalize_queue_worker_pid(
        "lv-worker-pid", 1111, attempts=attempt, only_if_empty=True
    )
    tracker.set_finalize_queue_worker_pid("lv-worker-pid", 2222, attempts=attempt)
    tracker.set_finalize_queue_worker_pid(
        "lv-worker-pid", 3333, attempts=attempt, only_if_empty=True
    )

    with tracker.connect() as conn:
        row = conn.execute(
            "SELECT worker_pid FROM finalize_queue WHERE lv = 'lv-worker-pid'"
        ).fetchone()
    assert row is not None
    assert row["worker_pid"] == 2222


def test_finalize_worker_records_timeshift_failure_without_target_dir(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_finalize_pipeline as runner

    def fail_finalize(*_args, **_kwargs):
        raise RuntimeError("timeshift failure")

    monkeypatch.setattr(runner.tracker, "run_finalize_pipeline_for_lv", fail_finalize)
    monkeypatch.setattr(
        runner.sys,
        "argv",
        ["run_finalize_pipeline.py", "--lv", "lv-timeshift-failure", "--timeline-mode", "timeshift"],
    )

    assert runner.main() == 1

    with tracker.connect() as conn:
        row = conn.execute(
            """
            SELECT event_type, target_dir
            FROM recording_events
            WHERE lv = 'lv-timeshift-failure'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert row["event_type"] == "finalize_pipeline_failed"
    assert row["target_dir"] == ""


def test_finalize_worker_rejects_partial_transcription_result(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import run_finalize_pipeline as runner

    monkeypatch.setattr(
        runner.tracker,
        "run_finalize_pipeline_for_lv",
        lambda *_args, **_kwargs: {
            "segment_transcriptions": [{"reason": "failed", "error": "whisper failed"}],
            "legacy_archiver": {
                "steps": {
                    "step12_html_generator": {
                        "result": {"html_file": "partial.html"}
                    }
                }
            },
        },
    )
    monkeypatch.setattr(
        runner.sys,
        "argv",
        ["run_finalize_pipeline.py", "--lv", "lv-partial", "--timeline-mode", "timeshift"],
    )

    assert runner.main() == 1

    with tracker.connect() as conn:
        row = conn.execute(
            """
            SELECT event_type
            FROM recording_events
            WHERE lv = 'lv-partial'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    assert row is not None
    assert row["event_type"] == "finalize_pipeline_failed"


def test_finalize_queue_blocks_stale_lv_rerecording(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tracker, "ensure_broadcast_target_dirs", lambda *_args, **_kwargs: tmp_path)
    with tracker.connect() as conn:
        assert tracker.reserve_finalize_queue_item(
            conn, lv="lv-ended", broadcaster_id="1", target_dir=str(tmp_path)
        )
        tracker.mark_finalize_queue_ready(conn, "lv-ended")
        conn.commit()
        result = tracker.start_recording_for_broadcast(
            conn,
            {"lv": "lv-ended", "broadcaster_id": "1"},
            SimpleNamespace(),
        )
    assert result == {
        "started": False,
        "reason": "finalize_in_progress",
        "queue_status": "queued",
    }


def test_dead_recorder_waits_for_exit_watcher_instead_of_starting_duplicate(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tracker, "ensure_broadcast_target_dirs", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(tracker, "is_process_running", lambda _pid: False)
    with tracker.connect() as conn:
        stamp = tracker.now_micro()
        conn.execute(
            """
            INSERT INTO recording_jobs
                (lv, recorder, pid, status, started_at, updated_at)
            VALUES ('lv-exiting', 'test', 4321, 'recording', ?, ?)
            """,
            (stamp, stamp),
        )
        conn.commit()
        result = tracker.start_recording_for_broadcast(
            conn,
            {"lv": "lv-exiting", "broadcaster_id": "1"},
            SimpleNamespace(),
        )
        status = conn.execute(
            "SELECT status FROM recording_jobs WHERE lv = 'lv-exiting'"
        ).fetchone()["status"]
    assert result == {"started": False, "reason": "process_exit_pending", "pid": 4321}
    assert status == "exited"


def test_closed_segment_selection_cannot_pick_new_restart_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_path = Path("lv123_2026_0715_120001_old.mp4")
    restarted_path = Path("lv123_2026_0715_123005_new.mp4")
    monkeypatch.setattr(
        tracker,
        "recording_segment_creation_rows",
        lambda _lv: [
            {
                "path": str(old_path),
                "creation_time": "2026-07-15T12:30:05",
                "creation_time_ns": 1,
                "last_write_time": "2026-07-15T12:30:00",
                "last_write_time_ns": 1,
            },
            {
                "path": str(restarted_path),
                "creation_time": "2026-07-15T12:30:06",
                "creation_time_ns": 2,
                "last_write_time": "2026-07-15T12:30:06",
                "last_write_time_ns": 2,
            },
        ],
    )
    selected = tracker.recording_segment_for_process_exit(
        "lv123",
        "2026-07-15T12:00:00.500000",
        "2026-07-15T12:30:00.000000",
    )
    assert selected is not None
    assert Path(selected["path"]).name == old_path.name
    assert selected["selection_basis"] == "filename_started_at_nearest_process_started_at"
    assert selected["filename_start_delta_seconds"] == pytest.approx(0.5)


def test_broadcaster_html_generation_defaults_on_and_survives_name_update(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tracker, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(
        tracker,
        "ensure_broadcast_target_dirs",
        lambda *_args, **_kwargs: tmp_path,
    )

    tracker.save_monitored_broadcaster(
        broadcaster_id="123",
        broadcaster_name="before",
    )
    with tracker.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(monitored_broadcasters)").fetchall()
        }
        initial = conn.execute(
            """
            SELECT html_generation_enabled
            FROM monitored_broadcasters
            WHERE broadcaster_id = '123'
            """
        ).fetchone()
    assert "html_generation_enabled" in columns
    assert initial is not None and initial["html_generation_enabled"] == 1

    tracker.update_monitored_broadcaster_setting(
        "123",
        "html_generation_enabled",
        False,
    )
    tracker.save_monitored_broadcaster(
        broadcaster_id="123",
        broadcaster_name="after",
    )
    with tracker.connect() as conn:
        updated = conn.execute(
            """
            SELECT broadcaster_name, html_generation_enabled
            FROM monitored_broadcasters
            WHERE broadcaster_id = '123'
            """
        ).fetchone()
    assert updated is not None
    assert updated["broadcaster_name"] == "after"
    assert updated["html_generation_enabled"] == 0


def test_archive_tags_are_scoped_to_the_broadcast_owner_even_without_custom_switch(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(tracker, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(tracker, "ensure_broadcast_target_dirs", lambda *_args, **_kwargs: tmp_path)
    tracker.save_monitored_broadcaster(broadcaster_id="39532023", broadcaster_name="yosino")
    tracker.save_monitored_broadcaster(broadcaster_id="other", broadcaster_name="other")
    tracker.save_monitored_broadcaster_details(
        "39532023",
        {"archive_tags": "ハムちゃん\nさくま\nハムちゃん"},
    )
    tracker.save_monitored_broadcaster_details("other", {"archive_tags": "別人物"})
    stamp = tracker.now()
    with tracker.connect() as conn:
        conn.executemany(
            """
            INSERT INTO broadcasts(lv, broadcaster_id, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("lv-yosino", "39532023", stamp, stamp),
                ("lv-other", "other", stamp, stamp),
            ],
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(monitored_broadcasters)")}
        conn.commit()

    yosino = tracker.apply_monitored_broadcaster_feature_overrides(
        "lv-yosino", {"tags": ["共通タグ"]}
    )
    other = tracker.apply_monitored_broadcaster_feature_overrides(
        "lv-other", {"tags": ["共通タグ"]}
    )

    assert "archive_tags" in columns
    assert yosino["tags"] == ["ハムちゃん", "さくま"]
    assert other["tags"] == ["別人物"]


def test_html_generation_off_stops_residual_recorders_without_queueing_finalize(
    isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stamp = tracker.now_micro()
    with tracker.connect() as conn:
        conn.execute(
            """
            INSERT INTO monitored_broadcasters
                (broadcaster_id, broadcaster_name, enabled,
                 html_generation_enabled, created_at, updated_at)
            VALUES ('123', 'test broadcaster', 1, 0, ?, ?)
            """,
            (stamp, stamp),
        )
        conn.execute(
            """
            INSERT INTO recording_jobs
                (lv, broadcaster_id, broadcaster_name, watch_url, recorder,
                 pid, status, target_dir, started_at, updated_at)
            VALUES
                ('lv100', '123', 'test broadcaster',
                 'https://live.nicovideo.jp/watch/lv100', 'test-recorder',
                 4321, 'recording', 'target', ?, ?)
            """,
            (stamp, stamp),
        )
        conn.commit()

    monkeypatch.setattr(
        tracker,
        "is_supported_broadcast_history_provider_id",
        lambda _broadcaster_id: True,
    )
    monkeypatch.setattr(
        tracker,
        "check_live_still_on_air_by_broadcaster_api",
        lambda *_args, **_kwargs: {
            "checked": True,
            "on_air": False,
            "source": "test",
            "meta": {"lv": "lv100", "broadcaster_id": "123"},
        },
    )
    monkeypatch.setattr(tracker, "save_broadcast_archive_meta", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tracker,
        "terminate_recording_processes_for_lv",
        lambda *_args, **_kwargs: [9876],
    )
    monkeypatch.setattr(tracker, "postprocess_log", lambda *_args, **_kwargs: None)

    def fail_finalize_start(**_kwargs):
        raise AssertionError("HTML生成OFFの放送を終了キューへ入れてはいけない")

    monkeypatch.setattr(
        tracker,
        "start_finalize_pipeline_after_recording_end",
        fail_finalize_start,
    )

    result = tracker.finalize_recording_if_broadcast_ended(
        lv="lv100",
        broadcaster_id="123",
        broadcaster_name="test broadcaster",
        watch_url="https://live.nicovideo.jp/watch/lv100",
        recorder="test-recorder",
        previous_pid=4321,
        exit_code=0,
        ended_at=stamp,
        target_dir="target",
        source_event="test",
    )

    assert result == {
        "finalized": False,
        "finalize_queued": False,
        "reason": "html_generation_disabled",
        "killed_pids": [9876],
    }
    with tracker.connect() as conn:
        job = conn.execute(
            "SELECT status, pid FROM recording_jobs WHERE lv = 'lv100'"
        ).fetchone()
        queue_count = conn.execute(
            "SELECT COUNT(*) AS count FROM finalize_queue WHERE lv = 'lv100'"
        ).fetchone()["count"]
        skipped_event = conn.execute(
            """
            SELECT payload_json
            FROM recording_events
            WHERE lv = 'lv100'
              AND event_type = 'broadcast_ended_finalize_skipped'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    assert job is not None
    assert job["status"] == "finalize_skipped"
    assert job["pid"] is None
    assert queue_count == 0
    assert skipped_event is not None
    payload = json.loads(str(skipped_event["payload_json"]))
    assert payload["html_generation_enabled"] is False
    assert payload["killed_pids"] == [9876]


def test_existing_recording_video_paths_prefers_local_segment_files(
    isolated_db: Path,
    tmp_path: Path,
) -> None:
    source_first = tmp_path / "lv100_part1.mp4"
    source_second = tmp_path / "lv100_part2.mp4"
    source_first.write_bytes(b"first")
    source_second.write_bytes(b"second")
    stamp = tracker.now_micro()
    with tracker.connect() as conn:
        for index, source in enumerate((source_first, source_second)):
            conn.execute(
                """
                INSERT INTO recording_segments
                    (lv, source_path, target_path, segment_index, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "lv100",
                    str(source),
                    str(tmp_path / f"missing_{index}.mp4"),
                    index,
                    stamp,
                    stamp,
                ),
            )
        conn.commit()

    result = tracker.existing_recording_video_paths_by_lv(
        ["lv100", "lv200", "lv100"]
    )

    assert result == {
        "lv100": [source_first, source_second],
        "lv200": [],
    }

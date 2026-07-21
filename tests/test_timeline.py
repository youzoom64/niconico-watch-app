import tracker


def test_comment_time_maps_to_video_segment_local_time():
    plan = {
        "segments": [
            {"path": "part1.mp4", "timeline_start_seconds": 100.0, "timeline_end_seconds": 130.0},
            {"path": "part2.mp4", "timeline_start_seconds": 140.0, "timeline_end_seconds": 170.0},
        ]
    }
    selected = tracker.select_recording_segment_for_timeline_second(plan, 152.5)
    assert selected["path"] == "part2.mp4"
    assert selected["local_seconds"] == 12.5
    assert tracker.select_recording_segment_for_timeline_second(plan, 135.0) is None


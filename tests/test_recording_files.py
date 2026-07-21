from pathlib import Path

import tracker


def test_recording_files_are_sorted_by_filename_time():
    paths = [
        Path("lv1_2026_0721_020000_title.mp4"),
        Path("lv1_2026_0721_010000_title.mp4"),
    ]
    result = tracker.sort_recording_paths_chronologically(paths)
    assert [path.name for path in result] == [paths[1].name, paths[0].name]


def test_mp4_wins_over_ts_for_same_recording():
    result = tracker.deduplicate_recording_paths(
        [Path("lv1_2026_0721_010000_title.ts"), Path("lv1_2026_0721_010000_title.mp4")]
    )
    assert result == [Path("lv1_2026_0721_010000_title.mp4")]


def test_duplicate_recording_path_is_removed():
    path = Path("lv1_2026_0721_010000_title.mp4")
    assert tracker.deduplicate_recording_paths([path, path, path]) == [path]


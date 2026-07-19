from niconico_ids import extract_channel_slug, extract_nicolive_id, extract_user_id


def test_extract_nicolive_id_from_plain_id() -> None:
    assert extract_nicolive_id("lv350788732") == "lv350788732"
    assert extract_nicolive_id("jk1") == "jk1"


def test_extract_nicolive_id_from_watch_url() -> None:
    assert extract_nicolive_id("https://live.nicovideo.jp/watch/lv350788732") == "lv350788732"
    assert extract_nicolive_id("https://live.nicovideo.jp/watch/lv350788732?ref=recent") == "lv350788732"


def test_extract_user_id_from_plain_id() -> None:
    assert extract_user_id("98313532") == "98313532"


def test_extract_user_id_from_user_url() -> None:
    assert extract_user_id("https://www.nicovideo.jp/user/98313532/") == "98313532"
    assert extract_user_id("https://www.nicovideo.jp/user/98313532/live_programs") == "98313532"


def test_extract_channel_id() -> None:
    assert extract_user_id("ch2627923") == "ch2627923"
    assert extract_user_id("https://ch.nicovideo.jp/ch2627923") == "ch2627923"
    assert extract_user_id("https://live.nicovideo.jp/watch/lv1?channel_id=ch2627923") == "ch2627923"


def test_extract_channel_slug() -> None:
    assert extract_channel_slug("https://ch.nicovideo.jp/realdabista?ref=WatchPage") == "realdabista"

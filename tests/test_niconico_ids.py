from niconico_ids import extract_nicolive_id


def test_lv_number_normalization():
    assert extract_nicolive_id(" lv351000909 ") == "lv351000909"
    assert extract_nicolive_id("https://live.nicovideo.jp/watch/lv351000909?ref=test") == "lv351000909"
    assert extract_nicolive_id("対象はlv351000909です") == "lv351000909"
    assert extract_nicolive_id("not-a-live-id") is None


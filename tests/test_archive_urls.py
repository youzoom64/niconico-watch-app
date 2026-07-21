import pytest

from processors.step13_index_generator import build_public_archive_index_url


def test_archive_index_url_generation():
    assert build_public_archive_index_url("39532023") == (
        "https://warehouse.bitter.jp/niconico/39532023/index.html"
    )
    assert build_public_archive_index_url("123", "https://example.test/{account_id}/") == (
        "https://example.test/123/"
    )


def test_archive_index_url_requires_account_id():
    with pytest.raises(ValueError):
        build_public_archive_index_url("")


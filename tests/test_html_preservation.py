from __future__ import annotations

import json
import re
from pathlib import Path

from legacy_archiver.processors.html_preservation import (
    read_page_tags,
    update_json_script_blocks,
    upsert_page_tags,
)


def test_json_script_update_preserves_everything_outside_managed_payloads(tmp_path: Path) -> None:
    path = tmp_path / "index.html"
    original = """<!doctype html><html><head>
<style id="manual-style">.kept{color:red}</style></head><body>
<div id="manual-content">手で直した内容</div>
<script id="archive-data" type="application/json">[{"old": true}]</script>
<script id="tag-data" type="application/json">{"old": 1}</script>
<script id="special-data" type="application/json">[]</script>
</body></html>"""
    path.write_text(original, encoding="utf-8")

    changed = update_json_script_blocks(
        path,
        {
            "archive-data": [{"lv": "lv1", "tags": ["人物A"]}],
            "tag-data": {"人物A": 1},
            "special-data": [{"label": "保存"}],
        },
    )

    updated = path.read_text(encoding="utf-8")
    assert changed is True
    assert '<style id="manual-style">.kept{color:red}</style>' in updated
    assert '<div id="manual-content">手で直した内容</div>' in updated
    assert json.loads(re.search(r'id="archive-data"[^>]*>(.*?)</script>', updated).group(1))[0]["lv"] == "lv1"


def test_page_tag_block_is_additive_and_idempotent() -> None:
    original = '<html><body><main id="manual">本文</main></body></html>'

    first, tags = upsert_page_tags(original, ["人物A"])
    second, tags = upsert_page_tags(first, ["人物B", "人物A"])
    third, tags = upsert_page_tags(second, ["人物B"])

    assert tags == ["人物A", "人物B"]
    assert read_page_tags(third) == ["人物A", "人物B"]
    assert '<main id="manual">本文</main>' in third
    assert third == second
    assert third.count("NICONICO-MANAGED:ARCHIVE-TAGS:START") == 1

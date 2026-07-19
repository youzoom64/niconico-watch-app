from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ROOT = ROOT / "legacy_archiver"
for path in (ROOT, LEGACY_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

from app.tracker import build_legacy_pipeline_data, load_config
from archive_db import (
    load_broadcast_data,
    load_comments_payload,
    load_ranking_payload,
    load_transcript_payload,
)
from processors.step12_html_generator import create_timeline_blocks, select_timeline_audio_source
from processors.step12_mobile_html_generator import build_mobile_archive
from utils import find_account_directory


def file_fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest().upper(),
    }


def regenerate_mobile_html(lv: str, account_id: str) -> dict[str, Any]:
    """PC版を読み取り専用の入力として、スマホ版だけを再生成する。"""
    lv_value = str(lv).strip()
    account_value = str(account_id).strip()
    config = load_config()
    pipeline_data = build_legacy_pipeline_data(lv_value, account_id=account_value, config=config)
    account_dir = Path(find_account_directory(pipeline_data["platform_directory"], account_value))
    broadcast_dir = account_dir / lv_value
    broadcast_data = load_broadcast_data(lv_value)
    pc_name = str(broadcast_data.get("html_file_path") or "").strip()
    if not pc_name:
        raise FileNotFoundError(f"DB html_file_path is empty: {lv_value}")
    pc_path = broadcast_dir / pc_name
    if not pc_path.is_file() or pc_path.stem.lower().endswith("_mobile"):
        raise FileNotFoundError(f"PC HTML not found: {pc_path}")

    pc_before = file_fingerprint(pc_path)
    pc_html = pc_path.read_text(encoding="utf-8")
    transcript_data = load_transcript_payload(lv_value)
    comments_data = load_comments_payload(lv_value)
    ranking_data = load_ranking_payload(lv_value, comments_data)
    debug_output = io.StringIO()
    with contextlib.redirect_stdout(debug_output):
        timeline_data = create_timeline_blocks(
            transcript_data,
            comments_data,
            lv_value,
            broadcast_data,
        )
    result = build_mobile_archive(
        broadcast_dir=broadcast_dir,
        lv_value=lv_value,
        pc_filename=pc_path.name,
        broadcast_data=broadcast_data,
        transcript_data=transcript_data,
        comments_data=comments_data,
        ranking_data=ranking_data,
        timeline_data=timeline_data,
        audio_source=select_timeline_audio_source(str(broadcast_dir), lv_value),
        pc_html=pc_html,
    )
    pc_after = file_fingerprint(pc_path)
    if pc_after != pc_before:
        raise RuntimeError(
            "PC HTML changed during mobile-only generation: "
            + json.dumps({"before": pc_before, "after": pc_after}, ensure_ascii=False)
        )
    return {
        "mode": "mobile_only",
        "lv": lv_value,
        "account_id": account_value,
        "pc_unchanged": True,
        "pc_before": pc_before,
        "pc_after": pc_after,
        "suppressed_debug_lines": len(debug_output.getvalue().splitlines()),
        **result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Step12のスマホ版だけを安全に再生成する")
    parser.add_argument("--lv", required=True)
    parser.add_argument("--account-id", required=True)
    args = parser.parse_args()
    print(json.dumps(regenerate_mobile_html(args.lv, args.account_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

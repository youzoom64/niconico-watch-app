from pathlib import Path


def is_pc_archive_html_candidate(path: Path, lv: str) -> bool:
    """PC用の総合アーカイブHTMLだけを候補として扱う。"""
    name = path.name.lower()
    lv_lower = str(lv or "").strip().lower()
    if not path.is_file() or not lv_lower:
        return False
    if name == f"{lv_lower}.html" or not name.startswith(f"{lv_lower}_"):
        return False
    return name.endswith(".html") and not path.stem.lower().endswith("_mobile")

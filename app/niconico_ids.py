from __future__ import annotations

import re
from urllib.parse import urlparse


NICONICO_LIVE_ID_RE = re.compile(r"(?:^|/)(lv\d+|jk\d+)(?:[/?#]|$)")
NICONICO_USER_ID_RE = re.compile(r"(?:^|/)(?:user|my|users)/(\d+)(?:[/?#]|$)")
NICONICO_CHANNEL_ID_RE = re.compile(r"(?:^|/)(ch\d+)(?:[/?#]|$)")


def extract_nicolive_id(value: str) -> str | None:
    text = value.strip()
    if re.fullmatch(r"(lv|jk)\d+", text):
        return text

    parsed = urlparse(text)
    target = parsed.path if parsed.scheme and parsed.netloc else text
    match = NICONICO_LIVE_ID_RE.search(target)
    if match:
        return match.group(1)

    match = re.search(r"(lv\d+|jk\d+)", text)
    return match.group(1) if match else None


def extract_user_id(value: str) -> str | None:
    text = value.strip()
    if re.fullmatch(r"\d+|ch\d+", text):
        return text

    parsed = urlparse(text)
    target = parsed.path if parsed.scheme and parsed.netloc else text
    match = NICONICO_CHANNEL_ID_RE.search(target)
    if match:
        return match.group(1)

    match = NICONICO_USER_ID_RE.search(target)
    if match:
        return match.group(1)

    match = re.search(r"(?:user_id=|/user/)(\d+)|(?:channel_id=)(ch\d+)|(ch\d+)", text)
    if match:
        return next(group for group in match.groups() if group)
    return None


def extract_channel_slug(value: str) -> str | None:
    text = value.strip()
    parsed = urlparse(text)
    if parsed.netloc != "ch.nicovideo.jp":
        return None
    slug = parsed.path.strip("/").split("/", 1)[0]
    return slug or None


def is_valid_nicolive_id(value: str) -> bool:
    return extract_nicolive_id(value) == value.strip()

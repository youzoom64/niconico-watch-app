from __future__ import annotations

import json
import re
from typing import Iterable

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


SERVER_NAME = "NiconicoWatchAppTimeshiftHandoffV1"


def normalize_urls(values: Iterable[object]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        for line in str(value or "").splitlines():
            url = line.strip()
            if not url or url in seen:
                continue
            seen.add(url)
            normalized.append(url)
    return normalized


def normalize_local_files(values: Iterable[object]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        path = str(value or "").strip()
        key = path.casefold()
        if not path or key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    return normalized


def normalize_local_lvs(values: Iterable[object]) -> list[str]:
    """動画が残っていない放送をLVだけで指定するための正規化。"""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in re.findall(r"lv\d+", str(value or ""), flags=re.IGNORECASE):
            lv = token.lower()
            if lv in seen:
                continue
            seen.add(lv)
            normalized.append(lv)
    return normalized


def encode_add_local_targets_message(
    paths: Iterable[object],
    lvs: Iterable[object],
) -> bytes:
    """動画パスとLV指定を1メッセージで渡す。二重起動を避けるため分割しない。"""
    payload = {
        "action": "add_local_targets",
        "paths": normalize_local_files(paths),
        "lvs": normalize_local_lvs(lvs),
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode_add_local_targets_message(raw: bytes) -> tuple[list[str], list[str]]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], []
    if not isinstance(payload, dict) or payload.get("action") != "add_local_targets":
        return [], []
    paths = payload.get("paths")
    lvs = payload.get("lvs")
    return (
        normalize_local_files(paths) if isinstance(paths, list) else [],
        normalize_local_lvs(lvs) if isinstance(lvs, list) else [],
    )


def send_local_targets(
    paths: Iterable[object],
    lvs: Iterable[object],
    *,
    timeout_ms: int = 500,
) -> bool:
    normalized_paths = normalize_local_files(paths)
    normalized_lvs = normalize_local_lvs(lvs)
    if not normalized_paths and not normalized_lvs:
        return False
    return _send_message(
        encode_add_local_targets_message(normalized_paths, normalized_lvs),
        timeout_ms=timeout_ms,
    )


def encode_add_urls_message(urls: Iterable[object]) -> bytes:
    payload = {
        "action": "add_urls",
        "urls": normalize_urls(urls),
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode_add_urls_message(raw: bytes) -> list[str]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("action") != "add_urls":
        return []
    urls = payload.get("urls")
    if not isinstance(urls, list):
        return []
    return normalize_urls(urls)


def encode_add_local_files_message(paths: Iterable[object]) -> bytes:
    payload = {
        "action": "add_local_files",
        "paths": normalize_local_files(paths),
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def encode_edit_tag_message(url: object) -> bytes:
    return (json.dumps({"action": "edit_tag", "url": str(url or "").strip()}, ensure_ascii=False) + "\n").encode("utf-8")


def decode_edit_tag_message(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    return str(payload.get("url") or "").strip() if isinstance(payload, dict) and payload.get("action") == "edit_tag" else ""


def decode_add_local_files_message(raw: bytes) -> list[str]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("action") != "add_local_files":
        return []
    paths = payload.get("paths")
    if not isinstance(paths, list):
        return []
    return normalize_local_files(paths)


def encode_ack_message(count: int) -> bytes:
    payload = {"status": "ok", "count": max(0, int(count))}
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode_ack_message(raw: bytes) -> bool:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok"


def _send_message(message: bytes, *, timeout_ms: int) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(max(1, int(timeout_ms))):
        socket.abort()
        return False
    if int(socket.write(message)) < 0:
        socket.abort()
        return False
    socket.flush()
    if socket.bytesToWrite() > 0 and not socket.waitForBytesWritten(max(1, int(timeout_ms))):
        socket.abort()
        return False
    if not socket.waitForReadyRead(max(1, int(timeout_ms))) and socket.bytesAvailable() <= 0:
        socket.abort()
        return False
    raw_ack = bytes(socket.readAll()).split(b"\n", 1)[0]
    acknowledged = decode_ack_message(raw_ack)
    socket.disconnectFromServer()
    return acknowledged


def send_urls(urls: Iterable[object], *, timeout_ms: int = 500) -> bool:
    normalized = normalize_urls(urls)
    if not normalized:
        return False
    return _send_message(encode_add_urls_message(normalized), timeout_ms=timeout_ms)


def send_local_files(paths: Iterable[object], *, timeout_ms: int = 500) -> bool:
    normalized = normalize_local_files(paths)
    if not normalized:
        return False
    return _send_message(
        encode_add_local_files_message(normalized),
        timeout_ms=timeout_ms,
    )


def send_tag_edit_url(url: object, *, timeout_ms: int = 500) -> bool:
    value = str(url or "").strip()
    return bool(value) and _send_message(encode_edit_tag_message(value), timeout_ms=timeout_ms)


def normalize_special_user_jobs(values: Iterable[object]) -> list[dict[str, str]]:
    """{user_id, lv} の組を正規化する。スペシャルユーザーのHTML生成待ちを表す。"""
    jobs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values or []:
        if not isinstance(value, dict):
            continue
        user_id = str(value.get("user_id") or "").strip()
        lv = str(value.get("lv") or "").strip()
        if not user_id or not lv or (user_id, lv) in seen:
            continue
        seen.add((user_id, lv))
        jobs.append({"user_id": user_id, "lv": lv})
    return jobs


def encode_special_user_jobs_message(jobs: Iterable[object]) -> bytes:
    payload = {
        "action": "special_user_jobs",
        "jobs": normalize_special_user_jobs(jobs),
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode_special_user_jobs_message(raw: bytes) -> list[dict[str, str]]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("action") != "special_user_jobs":
        return []
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return []
    return normalize_special_user_jobs(jobs)


def send_special_user_jobs(jobs: Iterable[object], *, timeout_ms: int = 500) -> bool:
    normalized = normalize_special_user_jobs(jobs)
    if not normalized:
        return False
    return _send_message(encode_special_user_jobs_message(normalized), timeout_ms=timeout_ms)


class TimeshiftHandoffServer(QObject):
    urls_received = pyqtSignal(list)
    local_files_received = pyqtSignal(list)
    local_targets_received = pyqtSignal(list, list)
    tag_edit_received = pyqtSignal(str)
    special_user_jobs_received = pyqtSignal(list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._accept_connections)
        self._buffers: dict[QLocalSocket, bytearray] = {}

    def start(self) -> bool:
        QLocalServer.removeServer(SERVER_NAME)
        return bool(self.server.listen(SERVER_NAME))

    def stop(self) -> None:
        for socket in list(self._buffers):
            socket.abort()
            socket.deleteLater()
        self._buffers.clear()
        self.server.close()
        QLocalServer.removeServer(SERVER_NAME)

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            self._buffers[socket] = bytearray()
            socket.readyRead.connect(lambda target=socket: self._read_socket(target))
            socket.disconnected.connect(lambda target=socket: self._socket_disconnected(target))
            self._read_socket(socket)

    def _read_socket(self, socket: QLocalSocket) -> None:
        buffer = self._buffers.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        while b"\n" in buffer:
            raw_line, _, remainder = buffer.partition(b"\n")
            buffer[:] = remainder
            urls = decode_add_urls_message(bytes(raw_line))
            if urls:
                self.urls_received.emit(urls)
                socket.write(encode_ack_message(len(urls)))
                socket.flush()
                continue
            target_paths, target_lvs = decode_add_local_targets_message(bytes(raw_line))
            if target_paths or target_lvs:
                self.local_targets_received.emit(target_paths, target_lvs)
                socket.write(encode_ack_message(len(target_paths) + len(target_lvs)))
                socket.flush()
                continue
            paths = decode_add_local_files_message(bytes(raw_line))
            if paths:
                self.local_files_received.emit(paths)
                socket.write(encode_ack_message(len(paths)))
                socket.flush()
                continue
            tag_url = decode_edit_tag_message(bytes(raw_line))
            if tag_url:
                self.tag_edit_received.emit(tag_url)
                socket.write(encode_ack_message(1))
                socket.flush()
                continue
            special_jobs = decode_special_user_jobs_message(bytes(raw_line))
            if special_jobs:
                self.special_user_jobs_received.emit(special_jobs)
                socket.write(encode_ack_message(len(special_jobs)))
                socket.flush()

    def _socket_disconnected(self, socket: QLocalSocket) -> None:
        self._read_socket(socket)
        self._buffers.pop(socket, None)
        socket.deleteLater()

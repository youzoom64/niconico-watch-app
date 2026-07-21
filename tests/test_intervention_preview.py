from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from api import intervention_server


def test_resolve_preview_file_selects_mobile_desktop_and_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    mobile = tmp_path / "lv123_title_mobile.html"
    desktop = tmp_path / "lv123_title.html"
    asset = tmp_path / "screenshot" / "lv123" / "10.jpg"
    asset.parent.mkdir(parents=True)
    mobile.write_text("mobile", encoding="utf-8")
    desktop.write_text("desktop", encoding="utf-8")
    asset.write_bytes(b"jpeg")
    monkeypatch.setattr(intervention_server, "preview_html_paths", lambda _lv: [desktop, mobile])

    assert intervention_server.resolve_preview_file("/preview/lv123/mobile") == mobile
    assert intervention_server.resolve_preview_file("/preview/lv123/pc") == desktop
    assert intervention_server.resolve_preview_file("/preview/lv123/screenshot/lv123/10.jpg") == asset
    assert intervention_server.resolve_preview_file("/preview/lv123/%2e%2e/secret.txt") is None


def test_preview_http_route_serves_mobile_html_and_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    mobile = tmp_path / "lv123_title_mobile.html"
    asset = tmp_path / "mobile_data" / "timeline.js"
    asset.parent.mkdir(parents=True)
    mobile.write_text("<!doctype html><title>mobile preview</title>", encoding="utf-8")
    asset.write_text("window.preview = true;", encoding="utf-8")
    monkeypatch.setattr(intervention_server, "preview_html_paths", lambda _lv: [mobile])

    server = ThreadingHTTPServer(("127.0.0.1", 0), intervention_server.InterventionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/preview/lv123/mobile", timeout=3) as response:
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert "mobile preview" in response.read().decode("utf-8")
        with urlopen(f"{base_url}/preview/lv123/mobile_data/timeline.js", timeout=3) as response:
            assert response.status == 200
            assert response.read() == b"window.preview = true;"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"{base_url}/preview/lv123/%2e%2e/secret.txt", timeout=3)
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

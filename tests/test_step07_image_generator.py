from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from legacy_archiver.processors import step07_image_generator as step07


def _valid_png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(20, 80, 140)).save(buffer, format="PNG")
    return buffer.getvalue()


VALID_PNG = _valid_png_bytes()


def test_generate_openai_image_passes_all_image_settings_to_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeImages:
        def generate(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(VALID_PNG).decode("ascii"))]
            )

    class FakeOpenAI:
        def __init__(self, *, api_key):
            captured["api_key"] = api_key
            self.images = FakeImages()

    monkeypatch.setattr(step07, "OpenAI", FakeOpenAI)

    result = step07.generate_openai_image(
        "画像プロンプト",
        "openai-key",
        model="gpt-image-2",
        size="1024x1024",
        quality="low",
    )

    assert result == VALID_PNG
    assert captured == {
        "api_key": "openai-key",
        "model": "gpt-image-2",
        "prompt": "画像プロンプト",
        "size": "1024x1024",
        "quality": "low",
        "output_format": "png",
        "n": 1,
    }


def _config(*, enabled: bool = True, openai_key: str = "openai-key") -> dict:
    return {
        "ai_features": {"enable_summary_image": enabled},
        "api_settings": {
            "openai_api_key": openai_key,
            "imgur_api_key": "imgur-client-id",
        },
        "image_settings": {
            "model": "gpt-image-2",
            "size": "1024x1024",
            "quality": "low",
        },
        "ai_prompts": {"image_prompt": "抽象的な要約画像を生成してください。"},
    }


def _broadcast(tmp_path: Path, *, summary: str = "要約本文です。") -> dict:
    return {
        "lv_value": "lv123456789",
        "live_title": "検証用の放送タイトル",
        "summary_text": summary,
        "broadcast_directory_path": str(tmp_path.resolve()),
    }


def _install_broadcast_db_fakes(
    monkeypatch: pytest.MonkeyPatch,
    broadcast: dict,
) -> list[tuple[str, dict]]:
    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(step07, "load_broadcast_data", lambda _lv: dict(broadcast))
    monkeypatch.setattr(
        step07,
        "save_broadcast_data",
        lambda lv, updates: saved.append((lv, updates)),
    )
    return saved


def test_process_calls_configured_openai_image_api_and_saves_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lv_value = "lv123456789"
    broadcast = _broadcast(tmp_path)
    saved = _install_broadcast_db_fakes(monkeypatch, broadcast)
    expected_output = (tmp_path / f"{lv_value}_summary.png").resolve()
    captured: dict[str, object] = {}

    def fake_generate(prompt, api_key, *, model, size, quality):
        captured.update(
            prompt=prompt,
            openai_api_key=api_key,
            model=model,
            size=size,
            quality=quality,
        )
        return VALID_PNG

    def fake_upload(image_data, api_key, title):
        captured.update(
            uploaded_bytes=image_data,
            imgur_api_key=api_key,
            upload_title=title,
        )
        return "https://i.imgur.com/summary.png"

    monkeypatch.setattr(step07, "generate_openai_image", fake_generate)
    monkeypatch.setattr(step07, "upload_to_imgur", fake_upload)

    result = step07.process({"lv_value": lv_value, "config": _config()})

    assert result["image_generated"] is True
    assert result["image_url"] == "https://i.imgur.com/summary.png"
    assert Path(result["local_path"]).resolve() == expected_output
    assert expected_output.read_bytes() == VALID_PNG
    assert broadcast["live_title"] in str(captured["prompt"])
    assert broadcast["summary_text"] in str(captured["prompt"])
    assert captured["openai_api_key"] == "openai-key"
    assert captured["model"] == "gpt-image-2"
    assert captured["size"] == "1024x1024"
    assert captured["quality"] == "low"
    assert captured["uploaded_bytes"] == VALID_PNG
    assert captured["imgur_api_key"] == "imgur-client-id"
    assert captured["upload_title"] == broadcast["live_title"]
    image_generation = saved[0][1]["image_generation"]
    assert image_generation["model"] == "gpt-image-2"
    assert image_generation["size"] == "1024x1024"
    assert image_generation["quality"] == "low"
    assert image_generation["prompt_engine"] == "openai_image_api"
    assert image_generation["generator"] == "images.generate"
    assert "cli_model" not in image_generation


def test_process_raises_when_openai_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved = _install_broadcast_db_fakes(monkeypatch, _broadcast(tmp_path))
    monkeypatch.setattr(
        step07,
        "generate_openai_image",
        lambda *_args, **_kwargs: pytest.fail("Image API must not run without a key"),
    )

    with pytest.raises(RuntimeError, match="OpenAI API Key"):
        step07.process(
            {"lv_value": "lv123456789", "config": _config(openai_key="")}
        )

    assert saved == []


def test_process_propagates_openai_image_api_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved = _install_broadcast_db_fakes(monkeypatch, _broadcast(tmp_path))

    def fail_generate(*_args, **_kwargs):
        raise RuntimeError("OpenAI Image API failure")

    monkeypatch.setattr(step07, "generate_openai_image", fail_generate)
    monkeypatch.setattr(
        step07,
        "upload_to_imgur",
        lambda *_args, **_kwargs: pytest.fail("Imgur must not run after API failure"),
    )

    with pytest.raises(RuntimeError, match="OpenAI Image API failure"):
        step07.process({"lv_value": "lv123456789", "config": _config()})

    assert saved == []


def test_process_rejects_corrupt_openai_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved = _install_broadcast_db_fakes(monkeypatch, _broadcast(tmp_path))
    monkeypatch.setattr(step07, "generate_openai_image", lambda *_args, **_kwargs: b"not-a-png")
    monkeypatch.setattr(
        step07,
        "upload_to_imgur",
        lambda *_args, **_kwargs: pytest.fail("Invalid output must not be uploaded"),
    )

    with pytest.raises(RuntimeError, match="有効な画像ではありません"):
        step07.process({"lv_value": "lv123456789", "config": _config()})

    assert saved == []


def test_process_raises_when_imgur_upload_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lv_value = "lv123456789"
    saved = _install_broadcast_db_fakes(monkeypatch, _broadcast(tmp_path))
    expected_output = (tmp_path / f"{lv_value}_summary.png").resolve()
    monkeypatch.setattr(step07, "generate_openai_image", lambda *_args, **_kwargs: VALID_PNG)
    monkeypatch.setattr(step07, "upload_to_imgur", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match=r"Imgur.*失敗"):
        step07.process({"lv_value": lv_value, "config": _config()})

    assert expected_output.is_file()
    assert saved == []


def test_process_skips_when_feature_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        step07,
        "load_broadcast_data",
        lambda *_args, **_kwargs: pytest.fail("DB must not be loaded when disabled"),
    )
    result = step07.process({"lv_value": "lv123456789", "config": _config(enabled=False)})
    assert result == {"image_generated": False, "reason": "feature_disabled"}


def test_process_skips_when_summary_is_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    saved = _install_broadcast_db_fakes(monkeypatch, _broadcast(tmp_path, summary="  \n"))
    result = step07.process({"lv_value": "lv123456789", "config": _config()})
    assert result == {"image_generated": False, "reason": "no_summary"}
    assert saved == []

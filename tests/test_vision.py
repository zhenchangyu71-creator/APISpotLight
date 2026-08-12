"""Tests for screenshot vision analysis (OpenAI-compatible)."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from api_spotlight.vision import analyze_screenshots


def _png_bytes() -> bytes:
    # Minimal 1x1 PNG
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


def _jpeg_bytes() -> bytes:
    # Minimal JPEG (1x1)
    return base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
        "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIy"
        "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIA"
        "AhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEB"
        "AQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAGfAP/E"
        "ABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAQUCf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAI"
        "AQMBAT8Bf//EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQIBAT8Bf//EABQQAQAAAAAAAAAAAAAAAA"
        "AAAAD/2gAIAQEABj8Cf//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAT8hf//Z"
    )


class FakeTransport(httpx.BaseTransport):
    """Records requests and returns scripted chat/completions responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(500, json={"error": "no scripted response"})
        payload = self.responses.pop(0)
        return httpx.Response(200, json=payload)


def _chat_payload(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": content,
                }
            }
        ]
    }


def test_analyze_screenshots_missing_credentials_returns_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    img = tmp_path / "page.png"
    img.write_bytes(_png_bytes())

    requirements, warnings = analyze_screenshots([str(img)])
    assert requirements == []
    assert warnings
    assert any("credential" in w.lower() or "api key" in w.lower() or "OPENAI" in w for w in warnings)


def test_analyze_screenshots_cleans_fenced_json_and_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("VISION_MODEL", "vision-test")

    img = tmp_path / "ui.png"
    img.write_bytes(_png_bytes())

    fenced = (
        "Here are the APIs:\n"
        "```json\n"
        '[{"method": "GET", "path": "/users", "confidence": 0.81}]\n'
        "```\n"
    )
    transport = FakeTransport([_chat_payload(fenced)])
    client = httpx.Client(transport=transport)

    requirements, warnings = analyze_screenshots([str(img)], client=client)
    assert warnings == []
    assert len(requirements) == 1
    assert requirements[0]["method"] == "GET"
    assert requirements[0]["path"] == "/users"
    assert requirements[0]["confidence"] == 0.81
    assert requirements[0]["source"] == ["screenshot"]


def test_analyze_screenshots_one_request_per_image_with_correct_mime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("VISION_MODEL", "vision-test")

    png = tmp_path / "a.png"
    jpg = tmp_path / "b.jpg"
    png.write_bytes(_png_bytes())
    jpg.write_bytes(_jpeg_bytes())

    transport = FakeTransport(
        [
            _chat_payload(
                json.dumps(
                    [{"method": "GET", "path": "/from-png", "confidence": 0.6}]
                )
            ),
            _chat_payload(
                json.dumps(
                    [{"method": "POST", "path": "/from-jpg", "confidence": 0.55}]
                )
            ),
        ]
    )
    client = httpx.Client(transport=transport)

    requirements, warnings = analyze_screenshots(
        [str(png), str(jpg)], client=client
    )
    assert warnings == []
    assert len(transport.requests) == 2
    assert len(requirements) == 2

    bodies = [json.loads(r.content.decode("utf-8")) for r in transport.requests]
    urls = [str(r.url) for r in transport.requests]
    assert all("/chat/completions" in u for u in urls)

    # Each request embeds one image as a data URL with correct MIME
    data_urls: list[str] = []
    for body in bodies:
        assert body["model"] == "vision-test"
        content = body["messages"][0]["content"]
        image_parts = [
            part for part in content if part.get("type") == "image_url"
        ]
        assert len(image_parts) == 1
        data_urls.append(image_parts[0]["image_url"]["url"])

    assert data_urls[0].startswith("data:image/png;base64,")
    assert data_urls[1].startswith("data:image/jpeg;base64,")

    keys = {(r["method"], r["path"]) for r in requirements}
    assert ("GET", "/from-png") in keys
    assert ("POST", "/from-jpg") in keys


def test_analyze_screenshots_uses_timeout_and_degrades_request_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("VISION_MODEL", "vision-test")

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(_png_bytes())
    second.write_bytes(_png_bytes())

    class RecordingClient:
        def __init__(self) -> None:
            self.timeouts: list[float | None] = []

        def post(self, url: str, **kwargs: Any) -> httpx.Response:
            self.timeouts.append(kwargs.get("timeout"))
            if len(self.timeouts) == 1:
                raise httpx.ReadTimeout("vision timed out")
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json=_chat_payload(
                    '[{"method":"GET","path":"/users","confidence":0.8}]'
                ),
            )

    client = RecordingClient()
    requirements, warnings = analyze_screenshots(  # type: ignore[arg-type]
        [str(first), str(second)], client=client
    )

    assert client.timeouts == [30.0, 30.0]
    assert [item["path"] for item in requirements] == ["/users"]
    assert any("timed out" in warning.lower() for warning in warnings)


def test_analyze_screenshots_rejects_invalid_model_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("VISION_MODEL", "vision-test")

    img = tmp_path / "bad.png"
    img.write_bytes(_png_bytes())

    invalid = json.dumps(
        [
            {"method": "GET", "path": "/ok", "confidence": 0.7},
            {"method": "FETCH", "path": "/bad", "confidence": 0.7},
            {"method": "POST", "path": "missing-slash", "confidence": 0.7},
            {"method": "PUT", "path": "/x", "confidence": 1.5},
            {"method": "DELETE", "path": "/y"},
            "not-an-object",
        ]
    )
    transport = FakeTransport([_chat_payload(invalid)])
    client = httpx.Client(transport=transport)

    requirements, warnings = analyze_screenshots([str(img)], client=client)
    assert len(requirements) == 1
    assert requirements[0]["path"] == "/ok"
    assert warnings  # invalid items produce warnings

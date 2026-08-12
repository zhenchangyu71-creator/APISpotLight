"""Screenshot vision analysis via OpenAI-compatible chat/completions."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

import httpx

_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
)
_VISION_TIMEOUT_SECONDS = 30.0
_FENCE_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)\s*```",
    re.IGNORECASE,
)

_VISION_PROMPT = (
    "Identify API endpoints shown or implied by this UI screenshot. "
    "Respond with ONLY a JSON array of objects, each with keys: "
    "method (HTTP verb), path (must start with /), confidence (0.0-1.0). "
    "If none, return []."
)


def analyze_screenshots(
    paths: list[str],
    client: httpx.Client | None = None,
) -> tuple[list[dict], list[str]]:
    """Analyze screenshots one-by-one; return requirements and warnings.

    Missing vision credentials yield a warning and empty requirements instead
    of raising at the module/API boundary.
    """
    warnings: list[str] = []
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    model = os.environ.get("VISION_MODEL", "").strip()

    if not api_key or not base_url or not model:
        warnings.append(
            "Vision credentials missing: set OPENAI_API_KEY, OPENAI_BASE_URL, "
            "and VISION_MODEL to enable screenshot analysis."
        )
        return [], warnings

    owns_client = client is None
    http = client or httpx.Client()
    requirements: list[dict] = []

    try:
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.is_file():
                warnings.append(f"Screenshot not found: {path}")
                continue
            try:
                items, item_warnings = _analyze_one(
                    path, http, api_key=api_key, base_url=base_url, model=model
                )
            except (httpx.TimeoutException, httpx.RequestError) as exc:
                warnings.append(f"Vision request failed for {path}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - accumulate, continue
                warnings.append(f"Vision request failed for {path}: {exc}")
                continue
            requirements.extend(items)
            warnings.extend(item_warnings)
    finally:
        if owns_client:
            http.close()

    return requirements, warnings


def _analyze_one(
    path: Path,
    client: httpx.Client,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> tuple[list[dict], list[str]]:
    mime = _mime_for(path)
    data_url = _to_data_url(path, mime)
    url = f"{base_url}/chat/completions"
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url},
                    },
                ],
            }
        ],
    }
    response = client.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=_VISION_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    content = _extract_message_content(payload)
    return _parse_model_json(content, source_label=str(path))


def _mime_for(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed and guessed.startswith("image/"):
        return guessed
    suffix = path.suffix.lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }
    return mapping.get(suffix, "application/octet-stream")


def _to_data_url(path: Path, mime: str) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_message_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Vision response is not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Vision response missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("Vision response missing message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("Vision response content must be a string")
    return content


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _parse_model_json(
    content: str, *, source_label: str
) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    cleaned = _strip_fences(content)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        warnings.append(
            f"Vision JSON parse failed for {source_label}: {exc}"
        )
        return [], warnings

    if not isinstance(data, list):
        warnings.append(
            f"Vision JSON must be an array for {source_label}"
        )
        return [], warnings

    requirements: list[dict] = []
    for idx, item in enumerate(data):
        validated = _validate_item(item)
        if validated is None:
            warnings.append(
                f"Invalid vision item[{idx}] for {source_label}: {item!r}"
            )
            continue
        requirements.append(validated)
    return requirements, warnings


def _validate_item(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    method = item.get("method")
    path = item.get("path")
    confidence = item.get("confidence")
    if not isinstance(method, str):
        return None
    method_u = method.strip().upper()
    if method_u not in _HTTP_METHODS:
        return None
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return None
    conf = float(confidence)
    if conf < 0.0 or conf > 1.0:
        return None
    return {
        "method": method_u,
        "path": path,
        "confidence": conf,
        "source": ["screenshot"],
    }

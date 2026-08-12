"""Mock file evidence parsing and requirement merging."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Conservative: quoted "METHOD /path" keys (JSON and common JS module.exports)
_METHOD_PATH_KEY = re.compile(
    r'["\'](GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE)\s+(/[^"\']*)["\']',
    re.IGNORECASE,
)

_SUPPORTED_EXTENSIONS = frozenset({".json", ".js"})


def parse_mock_paths(paths: list[str]) -> tuple[list[dict], list[str]]:
    """Parse mock files/directories for METHOD /path keys.

    Returns requirements with ``source=["mock"]`` and accumulated warnings.
    Directories are scanned recursively; only ``.json`` and ``.js`` are read.
    """
    requirements: list[dict] = []
    warnings: list[str] = []
    seen: set[tuple[str, str]] = set()

    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            for file_path in sorted(path.rglob("*")):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                    continue
                _parse_one_file(file_path, requirements, warnings, seen)
        elif path.is_file():
            if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                warnings.append(
                    f"Unsupported mock file extension (skipped): {path}"
                )
                continue
            _parse_one_file(path, requirements, warnings, seen)
        else:
            warnings.append(f"Mock path not found: {path}")

    return requirements, warnings


def extract_mock_field_evidence(
    paths: list[str],
) -> tuple[list[dict], list[str]]:
    """Extract field bags from mock JSON/JS bodies for hybrid field lookup.

    Each evidence item:
    ``{fields, method?, path?, source_file?}``
    """
    from api_spotlight.field_lookup import extract_fields_from_obj

    evidence: list[dict] = []
    warnings: list[str] = []

    for raw in paths:
        path = Path(raw).expanduser()
        files: list[Path] = []
        if path.is_dir():
            files = [
                f
                for f in sorted(path.rglob("*"))
                if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTENSIONS
            ]
        elif path.is_file():
            if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                warnings.append(
                    f"Unsupported mock file extension (skipped): {path}"
                )
                continue
            files = [path]
        else:
            warnings.append(f"Mock path not found: {path}")
            continue

        for file_path in files:
            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeError:
                warnings.append(
                    f"Failed to read mock file {file_path}: not valid UTF-8"
                )
                continue
            except OSError as exc:
                warnings.append(f"Failed to read mock file {file_path}: {exc}")
                continue

            if file_path.suffix.lower() != ".json":
                # JS: still collect METHOD keys; bodies are harder — skip field bags
                continue

            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                warnings.append(f"Failed to parse mock JSON {file_path}: {exc}")
                continue
            if not isinstance(data, dict):
                warnings.append(f"Mock JSON root must be an object: {file_path}")
                continue

            saw_method_key = False
            for raw_key, value in data.items():
                parsed = _parse_method_path_key(str(raw_key))
                if parsed is None:
                    continue
                saw_method_key = True
                method, api_path = parsed
                fields = extract_fields_from_obj(value)
                if not fields:
                    continue
                evidence.append(
                    {
                        "method": method,
                        "path": api_path,
                        "fields": fields,
                        "source_file": str(file_path),
                    }
                )

            if not saw_method_key:
                fields = extract_fields_from_obj(data)
                if fields:
                    evidence.append(
                        {
                            "fields": fields,
                            "source_file": str(file_path),
                        }
                    )

    return evidence, warnings


def merge_requirements(*groups: list[dict]) -> list[dict]:
    """Deduplicate by method+path; merge ``source`` as an ordered unique list.

    Screenshot ``confidence`` is preserved when present.
    """
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    for group in groups:
        for item in group:
            method = str(item.get("method", "")).upper()
            path = str(item.get("path", ""))
            if not method or not path:
                continue
            key = (method, path)
            sources = _normalize_sources(item.get("source"))
            if key not in merged:
                record: dict[str, Any] = {
                    "method": method,
                    "path": path,
                    "source": list(sources),
                }
                if "confidence" in item:
                    record["confidence"] = item["confidence"]
                merged[key] = record
                order.append(key)
            else:
                existing = merged[key]
                existing["source"] = _merge_sources(
                    existing.get("source", []), sources
                )
                if "confidence" in item and (
                    "confidence" not in existing
                    or item["confidence"] > existing["confidence"]
                ):
                    existing["confidence"] = item["confidence"]

    return [merged[k] for k in order]


def _parse_one_file(
    path: Path,
    requirements: list[dict],
    warnings: list[str],
    seen: set[tuple[str, str]],
) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        warnings.append(f"Failed to read mock file {path}: not valid UTF-8")
        return
    except OSError as exc:
        warnings.append(f"Failed to read mock file {path}: {exc}")
        return

    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            keys = _extract_keys_from_json(text, path)
        else:
            keys = _extract_keys_from_js(text, path)
    except ValueError as exc:
        warnings.append(str(exc))
        return

    for method, api_path in keys:
        key = (method, api_path)
        if key in seen:
            continue
        seen.add(key)
        requirements.append(
            {"method": method, "path": api_path, "source": ["mock"]}
        )


def _extract_keys_from_json(text: str, path: Path) -> list[tuple[str, str]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse mock JSON {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Mock JSON root must be an object: {path}")

    results: list[tuple[str, str]] = []
    for raw_key in data:
        parsed = _parse_method_path_key(str(raw_key))
        if parsed is not None:
            results.append(parsed)
    return results


def _extract_keys_from_js(text: str, path: Path) -> list[tuple[str, str]]:
    """Extract METHOD /path keys from common ``module.exports = { ... }`` mocks."""
    matches = list(_METHOD_PATH_KEY.finditer(text))
    if not matches:
        # File exists and looks like JS but has no recognizable keys
        if "module.exports" not in text and "exports." not in text:
            raise ValueError(
                f"Failed to parse mock JS (no module.exports / METHOD keys): {path}"
            )
        return []

    results: list[tuple[str, str]] = []
    seen_local: set[tuple[str, str]] = set()
    for match in matches:
        method = match.group(1).upper()
        api_path = match.group(2)
        key = (method, api_path)
        if key in seen_local:
            continue
        seen_local.add(key)
        results.append(key)
    return results


def _parse_method_path_key(raw: str) -> tuple[str, str] | None:
    match = re.fullmatch(
        r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE)\s+(/.*)",
        raw.strip(),
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).upper(), match.group(2)


def _normalize_sources(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _merge_sources(existing: list[str], incoming: list[str]) -> list[str]:
    result: list[str] = []
    for src in list(existing) + list(incoming):
        if src not in result:
            result.append(src)
    return result

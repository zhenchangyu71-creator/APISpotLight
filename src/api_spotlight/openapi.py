"""Local OpenAPI loading, requirement matching, and operation extraction."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

HTTP_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)
_PARAM_SEGMENT = re.compile(r"^\{[^/]+\}$")
_LOCAL_COMPONENT_REF = re.compile(r"^#/components/([^/]+)/(.+)$")


def load_openapi(source: str) -> dict:
    """Load an OpenAPI 3.x document from a local JSON or YAML file only."""
    if _looks_like_remote_url(source):
        raise ValueError(
            "OpenAPI source must be a local file path, not a remote URL: "
            f"{source}"
        )

    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"OpenAPI file not found: {path}")

    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif suffix == ".json":
        data = json.loads(text)
    else:
        data = _load_unknown_suffix(text)

    if not isinstance(data, dict):
        raise ValueError(f"OpenAPI root must be an object: {path}")
    return data


def match_requirements(document: dict, requirements: list[dict]) -> list[dict]:
    """Match requirements to document operations; retain unmatched entries."""
    operations = _index_operations(document)
    candidates: list[dict] = []
    for requirement in requirements:
        method = str(requirement.get("method", "")).upper()
        path = str(requirement.get("path", ""))
        source = _normalize_source(requirement.get("source"))
        confidence = requirement.get("confidence")

        match = _find_match(operations, method, path)
        if match is None:
            candidate = {
                "method": method,
                "path": path,
                "source": source,
                "match_type": "unmatched",
                "selected": False,
                "doc_summary": "",
            }
            if confidence is not None:
                candidate["confidence"] = confidence
            candidates.append(candidate)
            continue

        match_type, canonical_path, summary = match
        candidate = {
            "method": method,
            "path": canonical_path,
            "source": source,
            "match_type": match_type,
            "selected": True,
            "doc_summary": summary,
        }
        if confidence is not None:
            candidate["confidence"] = confidence
        candidates.append(candidate)
    return _dedupe_candidates(candidates)


def extract_operations(
    document: dict, selected: list[dict]
) -> tuple[dict, list[str]]:
    """Build a slim OpenAPI doc for selected operations and collect $ref warnings."""
    warnings: list[str] = []
    paths_out: dict[str, Any] = {}
    collected_nodes: list[Any] = []

    for item in selected:
        if item.get("selected") is False:
            continue
        method = str(item.get("method", "")).lower()
        path = str(item.get("path", ""))
        path_item = (document.get("paths") or {}).get(path)
        if not isinstance(path_item, dict) or method not in path_item:
            warnings.append(
                f"Selected operation not found: {method.upper()} {path}. "
                "For an unmatched candidate, replace path with the full canonical "
                "OpenAPI path and keep selected=true."
            )
            continue

        operation = copy.deepcopy(path_item[method])
        slim_path_item = paths_out.setdefault(path, {})
        if "parameters" in path_item and "parameters" not in slim_path_item:
            params = copy.deepcopy(path_item["parameters"])
            slim_path_item["parameters"] = params
            collected_nodes.append(params)
        slim_path_item[method] = operation
        collected_nodes.append(operation)

    components = _collect_local_components(document, collected_nodes, warnings)
    slim: dict[str, Any] = {
        "openapi": document.get("openapi", "3.0.3"),
        "info": copy.deepcopy(
            document.get("info", {"title": "Extracted APIs", "version": "0.0.0"})
        ),
        "paths": paths_out,
    }
    if components:
        slim["components"] = components
    return slim, warnings


def _looks_like_remote_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _load_unknown_suffix(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return yaml.safe_load(text)


def _index_operations(document: dict) -> list[tuple[str, str, dict]]:
    indexed: list[tuple[str, str, dict]] = []
    for path, path_item in (document.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            indexed.append((method.upper(), path, operation))
    return indexed


def _find_match(
    operations: list[tuple[str, str, dict]], method: str, path: str
) -> tuple[str, str, str] | None:
    for op_method, op_path, operation in operations:
        if op_method != method:
            continue
        if op_path == path:
            return "exact", op_path, _summary(operation)
    for op_method, op_path, operation in operations:
        if op_method != method:
            continue
        if _template_matches(op_path, path):
            return "template", op_path, _summary(operation)
    return None


def _summary(operation: dict) -> str:
    summary = operation.get("summary") or operation.get("operationId") or ""
    return str(summary)


def _template_matches(template_path: str, concrete_path: str) -> bool:
    template_parts = _split_path(template_path)
    concrete_parts = _split_path(concrete_path)
    if len(template_parts) != len(concrete_parts):
        return False
    for template_part, concrete_part in zip(template_parts, concrete_parts):
        if _PARAM_SEGMENT.match(template_part):
            continue
        if template_part != concrete_part:
            return False
    return "{" in template_path


def _split_path(path: str) -> list[str]:
    return [part for part in path.split("/") if part != ""]


def _collect_local_components(
    document: dict, roots: list[Any], warnings: list[str]
) -> dict[str, Any]:
    components_src = document.get("components") or {}
    if not isinstance(components_src, dict):
        return {}

    collected: dict[str, dict[str, Any]] = {}
    pending = list(roots)
    seen_refs: set[str] = set()

    while pending:
        node = pending.pop()
        for ref in _iter_refs(node):
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            match = _LOCAL_COMPONENT_REF.match(ref)
            if match is None:
                if ref.startswith("#/"):
                    warnings.append(f"Unsupported local $ref outside components: {ref}")
                else:
                    warnings.append(f"Skipping non-local $ref: {ref}")
                continue
            section, name = match.group(1), match.group(2)
            section_obj = components_src.get(section)
            if not isinstance(section_obj, dict) or name not in section_obj:
                warnings.append(f"Missing $ref target: {ref}")
                continue
            target = copy.deepcopy(section_obj[name])
            collected.setdefault(section, {})[name] = target
            pending.append(target)

    return collected


def _normalize_source(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for candidate in candidates:
        key = (str(candidate.get("method", "")), str(candidate.get("path", "")))
        if key not in merged:
            merged[key] = candidate
            order.append(key)
            continue

        existing = merged[key]
        for source in _normalize_source(candidate.get("source")):
            if source not in existing["source"]:
                existing["source"].append(source)
        if "confidence" in candidate and (
            "confidence" not in existing
            or candidate["confidence"] > existing["confidence"]
        ):
            existing["confidence"] = candidate["confidence"]
    return [merged[key] for key in order]


def _iter_refs(node: Any):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield ref
        for value in node.values():
            yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)

"""Two-stage page API discovery and confirmed export workflows."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from api_spotlight.evidence import (
    extract_mock_field_evidence,
    merge_requirements,
    parse_mock_paths,
)
from api_spotlight.exporters import (
    assert_local_nonempty_path,
    load_candidates,
    resolve_output_format,
    write_candidates,
    write_export,
)
from api_spotlight.field_lookup import build_field_index, score_apis_for_fields
from api_spotlight.openapi import (
    extract_operations,
    load_openapi,
    match_requirements,
)
from api_spotlight.torna import load_openapi_from_torna, parse_torna_doc_url
from api_spotlight.vision import analyze_screenshots


def find_page_apis(
    mock_paths: list[str],
    screenshot_paths: list[str],
    output_dir: str,
    vision_enabled: bool = True,
    api_doc_path: str | None = None,
    api_doc_source: str | None = None,
) -> dict:
    """Stage 1: gather evidence, match OpenAPI, write candidate review files."""
    out = assert_local_nonempty_path(output_dir, label="output_dir")
    source = _select_api_doc_source(api_doc_path, api_doc_source)

    warnings: list[str] = []

    mock_reqs, mock_warnings = parse_mock_paths(list(mock_paths or []))
    warnings.extend(mock_warnings)

    field_evidence, field_warnings = extract_mock_field_evidence(
        list(mock_paths or [])
    )
    warnings.extend(field_warnings)

    vision_reqs: list[dict] = []
    if vision_enabled and screenshot_paths:
        vision_reqs, vision_warnings = analyze_screenshots(list(screenshot_paths))
        warnings.extend(vision_warnings)

    requirements = merge_requirements(mock_reqs, vision_reqs)
    if not requirements and not field_evidence:
        raise ValueError(
            "No API evidence found from mock files or vision analysis. "
            "Provide parseable mock paths and/or configure vision credentials "
            "(OPENAI_API_KEY, OPENAI_BASE_URL, VISION_MODEL) with screenshots."
        )

    document, source_warnings, is_torna = resolve_api_document(source)
    warnings.extend(source_warnings)
    cache_path: Path | None = None
    if is_torna:
        out.mkdir(parents=True, exist_ok=True)
        cache_path = out / "full-openapi.from-torna.json"
        cache_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    candidates = match_requirements(document, requirements) if requirements else []
    candidates = _apply_field_lookup(document, candidates, field_evidence)

    json_path, md_path = write_candidates(candidates, out)
    matched = sum(1 for c in candidates if c.get("match_type") != "unmatched")
    unmatched = sum(1 for c in candidates if c.get("match_type") == "unmatched")

    result = {
        "candidates_path": str(json_path),
        "markdown_path": str(md_path),
        "discovered": len(candidates),
        "matched": matched,
        "unmatched": unmatched,
        "warnings": warnings,
    }
    if cache_path is not None:
        result["openapi_cache_path"] = str(cache_path)
    return result


def _apply_field_lookup(
    document: dict,
    candidates: list[dict],
    field_evidence: list[dict],
) -> list[dict]:
    """Upgrade path hits with fields; add field candidates for unmatched/field-only."""
    if not field_evidence:
        return candidates

    index = build_field_index(document)
    by_key = {
        (str(c.get("method", "")).upper(), str(c.get("path", ""))): c
        for c in candidates
    }
    unmatched_keys = {
        (str(c.get("method", "")).upper(), str(c.get("path", "")))
        for c in candidates
        if c.get("match_type") == "unmatched"
    }

    for bag in field_evidence:
        fields = list(bag.get("fields") or [])
        if not fields:
            continue
        method = str(bag.get("method") or "").upper()
        path = str(bag.get("path") or "")
        has_path = bool(method and path)
        hits = score_apis_for_fields(index, fields, top_n=3)
        if not hits:
            continue

        if has_path:
            for cand in candidates:
                if cand.get("method") != method:
                    continue
                if cand.get("match_type") == "unmatched":
                    continue
                for hit in hits:
                    if hit["method"] == method and hit["path"] == cand.get("path"):
                        _merge_field_into_candidate(cand, hit, fields)
                        break

        should_add_field_candidates = (not has_path) or (
            (method, path) in unmatched_keys
        )
        if not should_add_field_candidates:
            continue

        # Field-only / unmatched path: require ≥2 distinctive field hits
        strong_hits = [
            h
            for h in hits
            if len(h.get("hit_fields") or []) >= 2 and int(h.get("score") or 0) >= 6
        ]
        for hit in strong_hits:
            key = (hit["method"], hit["path"])
            if key in by_key and by_key[key].get("match_type") != "unmatched":
                _merge_field_into_candidate(by_key[key], hit, fields)
                continue
            candidate = {
                "method": hit["method"],
                "path": hit["path"],
                "source": ["mock"],
                "match_type": "field",
                "selected": True,
                "doc_summary": hit.get("doc_summary") or "",
                "hit_fields": list(hit.get("hit_fields") or []),
                "score": hit.get("score"),
            }
            if bag.get("source_file"):
                candidate["source_files"] = [str(bag["source_file"])]
            by_key[key] = candidate

    return _dedupe_field_candidates(list(by_key.values()))


def _merge_field_into_candidate(
    candidate: dict, hit: dict | None, fields: list[str]
) -> None:
    if hit is None:
        return
    if candidate.get("path") != hit.get("path"):
        return
    if candidate.get("method") != hit.get("method"):
        return
    hit_fields = list(candidate.get("hit_fields") or [])
    for field in hit.get("hit_fields") or fields:
        if field not in hit_fields:
            hit_fields.append(field)
    candidate["hit_fields"] = hit_fields
    score = hit.get("score")
    if score is not None:
        candidate["score"] = max(int(candidate.get("score") or 0), int(score))
    if candidate.get("match_type") in {"exact", "template", "path+field"}:
        candidate["match_type"] = "path+field"
    elif candidate.get("match_type") != "field":
        candidate["match_type"] = "path+field"


def _dedupe_field_candidates(candidates: list[dict]) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for candidate in candidates:
        key = (str(candidate.get("method", "")), str(candidate.get("path", "")))
        if key not in merged:
            merged[key] = candidate
            order.append(key)
            continue
        existing = merged[key]
        # Prefer matched over unmatched
        if existing.get("match_type") == "unmatched" and candidate.get(
            "match_type"
        ) != "unmatched":
            merged[key] = candidate
            continue
        if candidate.get("match_type") == "unmatched":
            continue
        for source in candidate.get("source") or []:
            if source not in existing.get("source", []):
                existing.setdefault("source", []).append(source)
        for field in candidate.get("hit_fields") or []:
            existing.setdefault("hit_fields", [])
            if field not in existing["hit_fields"]:
                existing["hit_fields"].append(field)
        if candidate.get("score") is not None:
            existing["score"] = max(
                int(existing.get("score") or 0), int(candidate["score"])
            )
        if existing.get("match_type") in {"exact", "template"} and candidate.get(
            "hit_fields"
        ):
            existing["match_type"] = "path+field"
        elif candidate.get("match_type") == "path+field":
            existing["match_type"] = "path+field"
    return [merged[k] for k in order]

def export_confirmed_apis(
    output_path: str,
    candidates_path: str | None = None,
    confirmed_apis: list[dict] | None = None,
    output_format: str = "openapi",
    api_doc_path: str | None = None,
    api_doc_source: str | None = None,
) -> dict:
    """Stage 2: export only selected candidate operations from the full document."""
    source = _select_api_doc_source(api_doc_path, api_doc_source)
    out = assert_local_nonempty_path(output_path, label="output_path")

    if (candidates_path is None) == (confirmed_apis is None):
        raise ValueError(
            "Provide exactly one of candidates_path or confirmed_apis."
        )

    source_path = None
    if not _is_http_url(source):
        source_path = assert_local_nonempty_path(source, label="api_doc_source")
    if source_path is not None and out.resolve() == source_path.resolve():
        raise ValueError(
            "output_path must not overwrite api_doc_path "
            f"(same resolved path: {out.resolve()})"
        )

    # Validate format early before any write
    resolve_output_format(output_format, out)

    if candidates_path is not None:
        candidates_file = assert_local_nonempty_path(
            candidates_path, label="candidates_path"
        )
        candidates = load_candidates(candidates_file)
    else:
        if not isinstance(confirmed_apis, list) or not all(
            isinstance(item, dict) for item in confirmed_apis
        ):
            raise ValueError("confirmed_apis must be a list of candidate objects.")
        candidates = confirmed_apis

    selected = [c for c in candidates if c.get("selected") is True]
    if not selected:
        raise ValueError(
            "No candidates marked selected=true. Confirm APIs in the candidates "
            "file before exporting."
        )

    document, source_warnings, _ = resolve_api_document(source)
    slim, extract_warnings = extract_operations(document, selected)

    exported = _count_operations(slim)
    if exported == 0:
        raise ValueError(
            "No operations could be exported: all selected path/method pairs "
            "were missing from the API document."
        )

    written = write_export(slim, out, output_format)
    return {
        "output_path": str(written),
        "exported": exported,
        "warnings": [*source_warnings, *extract_warnings],
    }


def resolve_api_document(source: str) -> tuple[dict, list[str], bool]:
    """Load a local OpenAPI file or a complete Torna project document."""
    if _is_http_url(source):
        parsed = parse_torna_doc_url(source)
        if parsed["kind"] != "project":
            raise ValueError(
                "Torna 单接口链接不能作为 API 文档来源；请使用 "
                "Torna 项目链接 #/project/doc/{projectId}。"
            )
        document, meta = load_openapi_from_torna(source)
        return document, list(meta.get("warnings") or []), True

    local_path = assert_local_nonempty_path(source, label="api_doc_source")
    return load_openapi(str(local_path)), [], False


def _select_api_doc_source(
    api_doc_path: str | None, api_doc_source: str | None
) -> str:
    if api_doc_path is not None and api_doc_source is not None:
        if api_doc_path != api_doc_source:
            raise ValueError(
                "api_doc_path and api_doc_source must not be different when both "
                "are provided."
            )
        return api_doc_source
    source = api_doc_source if api_doc_source is not None else api_doc_path
    if source is None:
        raise ValueError("Provide api_doc_source or api_doc_path.")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("api_doc_source/api_doc_path must not be empty.")
    return source


def _is_http_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _count_operations(document: dict) -> int:
    exported = 0
    for path_item in (document.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() == "parameters":
                continue
            if isinstance(operation, dict):
                exported += 1
    return exported

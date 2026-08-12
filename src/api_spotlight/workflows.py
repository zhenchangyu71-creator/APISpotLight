"""Two-stage page API discovery and confirmed export workflows."""

from __future__ import annotations

from api_spotlight.evidence import merge_requirements, parse_mock_paths
from api_spotlight.exporters import (
    assert_local_nonempty_path,
    load_candidates,
    resolve_output_format,
    write_candidates,
    write_export,
)
from api_spotlight.openapi import extract_operations, load_openapi, match_requirements
from api_spotlight.vision import analyze_screenshots


def find_page_apis(
    mock_paths: list[str],
    screenshot_paths: list[str],
    api_doc_path: str,
    output_dir: str,
    vision_enabled: bool = True,
) -> dict:
    """Stage 1: gather evidence, match OpenAPI, write candidate review files."""
    assert_local_nonempty_path(output_dir, label="output_dir")
    assert_local_nonempty_path(api_doc_path, label="api_doc_path")

    warnings: list[str] = []

    mock_reqs, mock_warnings = parse_mock_paths(list(mock_paths or []))
    warnings.extend(mock_warnings)

    vision_reqs: list[dict] = []
    if vision_enabled and screenshot_paths:
        vision_reqs, vision_warnings = analyze_screenshots(list(screenshot_paths))
        warnings.extend(vision_warnings)

    requirements = merge_requirements(mock_reqs, vision_reqs)
    if not requirements:
        raise ValueError(
            "No API evidence found from mock files or vision analysis. "
            "Provide parseable mock paths and/or configure vision credentials "
            "(OPENAI_API_KEY, OPENAI_BASE_URL, VISION_MODEL) with screenshots."
        )

    document = load_openapi(api_doc_path)
    candidates = match_requirements(document, requirements)

    json_path, md_path = write_candidates(candidates, output_dir)
    matched = sum(1 for c in candidates if c.get("match_type") != "unmatched")
    unmatched = sum(1 for c in candidates if c.get("match_type") == "unmatched")

    return {
        "candidates_path": str(json_path),
        "markdown_path": str(md_path),
        "discovered": len(candidates),
        "matched": matched,
        "unmatched": unmatched,
        "warnings": warnings,
    }


def export_confirmed_apis(
    api_doc_path: str,
    output_path: str,
    candidates_path: str | None = None,
    confirmed_apis: list[dict] | None = None,
    output_format: str = "openapi",
) -> dict:
    """Stage 2: export only selected candidate operations from the full document."""
    api_doc = assert_local_nonempty_path(api_doc_path, label="api_doc_path")
    out = assert_local_nonempty_path(output_path, label="output_path")

    if (candidates_path is None) == (confirmed_apis is None):
        raise ValueError(
            "Provide exactly one of candidates_path or confirmed_apis."
        )

    if out.resolve() == api_doc.resolve():
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

    document = load_openapi(str(api_doc))
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
        "warnings": list(extract_warnings),
    }


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

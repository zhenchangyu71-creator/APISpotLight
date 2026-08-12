"""Write candidate review artifacts and confirmed OpenAPI/Markdown exports."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import yaml

_EXPLICIT_FORMATS = {
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "markdown": "markdown",
    "md": "markdown",
}


def write_candidates(
    candidates: list[dict], output_dir: str | Path
) -> tuple[Path, Path]:
    """Write stable ``candidates.json`` and ``candidates.md`` under output_dir."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "candidates.json"
    md_path = out / "candidates.md"
    json_path.write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_candidates_markdown(candidates), encoding="utf-8")
    return json_path, md_path


def write_export(
    document: dict,
    output_path: str | Path,
    output_format: str,
) -> Path:
    """Serialize a slim OpenAPI document as JSON, YAML, or Markdown."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fmt = resolve_output_format(output_format, path)

    if fmt == "json":
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    elif fmt == "yaml":
        path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    elif fmt == "markdown":
        path.write_text(_openapi_markdown(document), encoding="utf-8")
    else:
        raise ValueError(f"Unsupported output_format: {output_format}")
    return path


def resolve_output_format(output_format: str, path: Path) -> str:
    """Resolve serialization format.

    Explicit ``json`` / ``yaml`` / ``markdown`` always win.
    ``openapi`` chooses YAML only for ``.yaml``/``.yml`` suffixes; otherwise JSON
    (including ``.md`` paths). Unsupported values raise ``ValueError``.
    """
    fmt = (output_format or "").strip().lower()
    if not fmt:
        raise ValueError("Unsupported output_format: empty")

    if fmt in _EXPLICIT_FORMATS:
        return _EXPLICIT_FORMATS[fmt]

    if fmt == "openapi":
        if path.suffix.lower() in {".yaml", ".yml"}:
            return "yaml"
        return "json"

    raise ValueError(f"Unsupported output_format: {output_format}")


def load_candidates(candidates_path: str | Path) -> list[dict]:
    path = Path(candidates_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Candidates file must be a JSON array: {path}")
    return data


def assert_local_nonempty_path(value: str, *, label: str) -> Path:
    """Require a non-empty local filesystem path (reject remote URLs)."""
    if value is None or not str(value).strip():
        raise ValueError(f"{label} must be a non-empty local path")
    text = str(value).strip()
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        raise ValueError(f"{label} must be a local path, not a remote URL: {text}")
    return Path(text).expanduser()


def _candidates_markdown(candidates: list[dict]) -> str:
    show_confidence = any("confidence" in item for item in candidates)
    if show_confidence:
        header = (
            "| Selected | Method | Path | Match | Source | Confidence | Summary |"
        )
        divider = (
            "|----------|--------|------|-------|--------|------------|---------|"
        )
    else:
        header = "| Selected | Method | Path | Match | Source | Summary |"
        divider = "|----------|--------|------|-------|--------|---------|"

    lines = ["# API Candidates", "", header, divider]
    unmatched = 0
    for item in candidates:
        selected = "yes" if item.get("selected") else "no"
        method = item.get("method", "")
        path = item.get("path", "")
        match_type = item.get("match_type", "")
        source = item.get("source") or []
        if isinstance(source, str):
            source_text = source
        else:
            source_text = ", ".join(str(s) for s in source)
        summary = str(item.get("doc_summary") or "").replace("|", "\\|")
        if match_type == "unmatched":
            unmatched += 1
        if show_confidence:
            conf = item.get("confidence", "")
            conf_text = "" if conf == "" or conf is None else str(conf)
            lines.append(
                f"| {selected} | {method} | `{path}` | {match_type} | "
                f"{source_text} | {conf_text} | {summary} |"
            )
        else:
            lines.append(
                f"| {selected} | {method} | `{path}` | {match_type} | "
                f"{source_text} | {summary} |"
            )
    lines.extend(["", f"Total: {len(candidates)} · Unmatched: {unmatched}", ""])
    return "\n".join(lines)


def _openapi_markdown(document: dict) -> str:
    info = document.get("info") or {}
    title = info.get("title") or "Exported APIs"
    version = info.get("version") or ""
    lines = [f"# {title}", ""]
    if version:
        lines.append(f"Version: {version}")
        lines.append("")

    paths = document.get("paths") or {}
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() in {"parameters", "summary", "description", "servers"}:
                continue
            if not isinstance(operation, dict):
                continue
            summary = operation.get("summary") or operation.get("operationId") or ""
            lines.append(f"## {method.upper()} `{path}`")
            lines.append("")
            if summary:
                lines.append(str(summary))
                lines.append("")
            if operation.get("operationId"):
                lines.append(f"- operationId: `{operation['operationId']}`")
                lines.append("")

    schemas = ((document.get("components") or {}).get("schemas")) or {}
    if schemas:
        lines.append("## Schemas")
        lines.append("")
        for name in sorted(schemas):
            lines.append(f"- `{name}`")
        lines.append("")
    return "\n".join(lines)

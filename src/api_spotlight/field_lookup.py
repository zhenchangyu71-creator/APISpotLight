"""Field-based API lookup against OpenAPI parameter names and descriptions."""

from __future__ import annotations

from typing import Any

from api_spotlight.openapi import HTTP_METHODS

_MIN_SCORE = 3  # at least one exact field-name hit
_EXACT_NAME = 3
_DESC_CONTAINS = 1
# Wrapper / pagination noise — ignored for indexing and scoring
_GENERIC_FIELDS = frozenset(
    {
        "id",
        "code",
        "data",
        "msg",
        "message",
        "success",
        "list",
        "total",
        "page",
        "pageindex",
        "pagesize",
        "rows",
        "items",
        "result",
        "error",
        "status",
        "count",
    }
)


def extract_fields_from_obj(obj: Any, *, _out: set[str] | None = None) -> list[str]:
    """Recursively collect object keys; skip list indexes and empty names."""
    found = _out if _out is not None else set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = str(key).strip()
            if name and not name.isdigit() and name.lower() not in _GENERIC_FIELDS:
                found.add(name)
            extract_fields_from_obj(value, _out=found)
    elif isinstance(obj, list):
        for item in obj:
            extract_fields_from_obj(item, _out=found)
    return sorted(found)


def build_field_index(document: dict) -> dict[str, list[dict]]:
    """Map lowercased field name → operation occurrences with descriptions."""
    index: dict[str, list[dict]] = {}
    schemas = ((document.get("components") or {}).get("schemas")) or {}
    paths = document.get("paths") or {}

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_params = path_item.get("parameters") if isinstance(
            path_item.get("parameters"), list
        ) else []
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            summary = str(operation.get("summary") or operation.get("operationId") or "")
            entries: list[tuple[str, str, str]] = []  # name, location, description

            for param in list(path_params) + list(operation.get("parameters") or []):
                if not isinstance(param, dict):
                    continue
                pname = str(param.get("name") or "").strip()
                if not pname:
                    continue
                entries.append(
                    (
                        pname,
                        str(param.get("in") or "parameter"),
                        str(param.get("description") or ""),
                    )
                )

            for name, desc in _schema_fields(
                operation.get("requestBody"), schemas, location="request"
            ):
                entries.append((name, "request", desc))
            for code, response in (operation.get("responses") or {}).items():
                for name, desc in _schema_fields(
                    response, schemas, location=f"response:{code}"
                ):
                    entries.append((name, f"response:{code}", desc))

            for pname, location, description in entries:
                key = pname.lower()
                index.setdefault(key, []).append(
                    {
                        "method": method.upper(),
                        "path": path,
                        "field": pname,
                        "location": location,
                        "description": description,
                        "doc_summary": summary,
                    }
                )
    return index


def score_apis_for_fields(
    index: dict[str, list[dict]],
    fields: list[str],
    *,
    top_n: int = 3,
    min_score: int = _MIN_SCORE,
) -> list[dict]:
    """Score operations for mock fields; require at least one exact name hit."""
    scores: dict[tuple[str, str], dict[str, Any]] = {}
    normalized = [str(f).strip() for f in fields if str(f).strip()]

    for field in normalized:
        key = field.lower()
        for occ in index.get(key, []):
            op_key = (occ["method"], occ["path"])
            bucket = scores.setdefault(
                op_key,
                {
                    "method": occ["method"],
                    "path": occ["path"],
                    "score": 0,
                    "hit_fields": [],
                    "doc_summary": occ.get("doc_summary") or "",
                    "_exact": set(),
                    "_descs": [],
                },
            )
            if field not in bucket["hit_fields"]:
                bucket["hit_fields"].append(field)
            if key not in bucket["_exact"]:
                bucket["score"] += _EXACT_NAME
                bucket["_exact"].add(key)
            desc = str(occ.get("description") or "")
            if desc:
                bucket["_descs"].append(desc)

    # Description bonus only on operations that already have exact hits
    for bucket in scores.values():
        descs = " ".join(bucket.pop("_descs", [])).lower()
        if not descs:
            bucket.pop("_exact", None)
            continue
        for field in normalized:
            key = field.lower()
            if key in bucket.get("_exact", set()):
                continue
            if key in descs:
                if field not in bucket["hit_fields"]:
                    bucket["hit_fields"].append(field)
                bucket["score"] += _DESC_CONTAINS
        bucket.pop("_exact", None)

    ranked = []
    for bucket in scores.values():
        bucket.pop("_exact", None)
        if bucket["score"] < min_score:
            continue
        ranked.append(
            {
                "method": bucket["method"],
                "path": bucket["path"],
                "score": bucket["score"],
                "hit_fields": list(bucket["hit_fields"]),
                "doc_summary": bucket["doc_summary"],
            }
        )
    ranked.sort(key=lambda x: (-x["score"], -len(x["hit_fields"]), x["path"]))
    return ranked[: max(0, top_n)]


def _schema_fields(
    node: Any, schemas: dict, *, location: str, _seen: set[str] | None = None
) -> list[tuple[str, str]]:
    """Yield (property_name, description) from request/response content schemas."""
    seen = _seen if _seen is not None else set()
    out: list[tuple[str, str]] = []
    if not isinstance(node, dict):
        return out

    if "$ref" in node and isinstance(node["$ref"], str):
        ref = node["$ref"]
        name = ref.rsplit("/", 1)[-1]
        if name in seen:
            return out
        seen.add(name)
        target = schemas.get(name)
        if isinstance(target, dict):
            return _schema_fields(target, schemas, location=location, _seen=seen)
        return out

    # OpenAPI content wrapper
    content = node.get("content")
    if isinstance(content, dict):
        for media in content.values():
            if isinstance(media, dict):
                out.extend(
                    _schema_fields(
                        media.get("schema"), schemas, location=location, _seen=seen
                    )
                )
        return out

    props = node.get("properties")
    if isinstance(props, dict):
        for prop_name, prop_schema in props.items():
            desc = ""
            if isinstance(prop_schema, dict):
                desc = str(prop_schema.get("description") or "")
            out.append((str(prop_name), desc))
            if isinstance(prop_schema, dict):
                out.extend(
                    _schema_fields(
                        prop_schema, schemas, location=location, _seen=seen
                    )
                )

    items = node.get("items")
    if isinstance(items, dict):
        out.extend(_schema_fields(items, schemas, location=location, _seen=seen))

    for key in ("allOf", "oneOf", "anyOf"):
        combo = node.get(key)
        if isinstance(combo, list):
            for part in combo:
                out.extend(
                    _schema_fields(part, schemas, location=location, _seen=seen)
                )
    return out

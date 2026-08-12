"""Authenticated Torna project loading and OpenAPI conversion."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import httpx

_SUCCESS_CODES = {"0", "20000"}
_HTTP_METHODS = {
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
}
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_DOC_CONCURRENCY = 6


def parse_torna_doc_url(url: str) -> dict:
    """Parse a Torna project or single-document hash URL."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid Torna URL: {url}")

    fragment_path = parsed.fragment.split("?", 1)[0].strip("/")
    parts = fragment_path.split("/")
    if len(parts) == 3 and parts[:2] == ["project", "doc"] and parts[2]:
        identifier = parts[2]
        kind = "project"
    elif len(parts) == 2 and parts[0] == "view" and parts[1]:
        identifier = parts[1]
        kind = "doc"
    else:
        raise ValueError(
            "Unsupported Torna URL; expected #/project/doc/{id} or #/view/{id}"
        )

    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {"origin": origin, "project_id": identifier, "kind": kind}


def flatten_projects(projects_payload: Any) -> list[dict]:
    """Flatten projects nested under Torna spaces."""
    root = _payload_data(projects_payload)
    projects: list[dict] = []

    def visit(node: Any, *, in_project_list: bool = False) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item, in_project_list=in_project_list)
            return
        if not isinstance(node, dict):
            return

        nested_keys = ("projects", "projectList", "project_list")
        nested = [node[key] for key in nested_keys if isinstance(node.get(key), list)]
        if nested:
            for children in nested:
                visit(children, in_project_list=True)
            return

        project_id = node.get("projectId") or node.get("id")
        if in_project_list and project_id is not None:
            item = dict(node)
            item["id"] = str(project_id)
            item["name"] = str(node.get("name") or node.get("projectName") or project_id)
            projects.append(item)

    visit(root)
    return projects


def flatten_doc_tree(tree: list) -> list[dict]:
    """Return endpoint leaves from a nested Torna document tree."""
    leaves: list[dict] = []

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        children = next(
            (
                node[key]
                for key in ("children", "items", "list")
                if isinstance(node.get(key), list)
            ),
            None,
        )
        if children:
            for child in children:
                visit(child)
            return

        doc_id = node.get("docId") or node.get("id")
        method = node.get("httpMethod") or node.get("method")
        path = node.get("url") or node.get("path")
        if doc_id is None or not method or not path:
            return
        leaf = dict(node)
        leaf["doc_id"] = str(doc_id)
        leaves.append(leaf)

    for root in tree:
        visit(root)
    return leaves


def torna_detail_to_operation(
    detail: dict,
) -> tuple[str, str, dict] | None:
    """Convert one Torna detail to an OpenAPI method, path, and operation."""
    method_raw = (
        detail.get("httpMethod")
        or detail.get("method")
        or detail.get("requestMethod")
    )
    path_raw = detail.get("path") or detail.get("url") or detail.get("requestUrl")
    if not method_raw or not path_raw:
        return None

    method = str(method_raw).strip().lower()
    if method not in _HTTP_METHODS:
        return None
    path = _normalize_api_path(str(path_raw))
    if not path:
        return None

    summary = str(detail.get("name") or detail.get("title") or path)
    operation: dict[str, Any] = {
        "summary": summary,
    }
    description = detail.get("description")
    if description:
        operation["description"] = str(description)
    operation["responses"] = {"default": {"description": "Torna response"}}
    return method, path, operation


def details_to_openapi(
    details: list[dict], *, title: str = "Torna Export"
) -> dict:
    """Build a minimal matching-oriented OpenAPI document from Torna details."""
    paths: dict[str, dict[str, Any]] = {}
    for raw_detail in details:
        detail = _payload_data(raw_detail)
        if not isinstance(detail, dict):
            continue
        converted = torna_detail_to_operation(detail)
        if converted is None:
            continue
        method, path, operation = converted
        paths.setdefault(path, {})[method] = operation
    return {
        "openapi": "3.0.3",
        "info": {"title": title, "version": "1.0.0"},
        "paths": paths,
    }


def fetch_json(
    client: httpx.Client,
    origin: str,
    path: str,
    params: dict | None = None,
) -> dict:
    """Fetch and validate one authenticated Torna JSON response."""
    token = os.environ.get("TORNA_TOKEN", "").strip()
    if not token:
        raise ValueError("TORNA_TOKEN is not configured")

    timeout = _env_float("TORNA_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS)
    response = client.get(
        f"{origin.rstrip('/')}/{path.lstrip('/')}",
        params=params,
        headers={
            "token": token,
            "Authorization": token,
            "X-Token": token,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Torna response must be a JSON object")

    code = str(payload.get("code", ""))
    message = str(payload.get("msg") or payload.get("message") or "")
    if code == "1000" or "login" in message.lower():
        raise ValueError("TORNA_TOKEN 无效或未登录")
    if code not in _SUCCESS_CODES:
        raise ValueError(
            _sanitize(f"Torna request failed (code {code or 'missing'}): {message}")
        )
    return payload


def test_torna_connection(
    doc_url: str, *, client: httpx.Client | None = None
) -> dict:
    """Check token, project resolution, and document-tree access."""
    try:
        parsed = parse_torna_doc_url(doc_url)
    except Exception as exc:  # noqa: BLE001 - public diagnostic boundary
        return {"ok": False, "error": _sanitize(str(exc))}

    base = {
        "ok": False,
        "origin": parsed["origin"],
        "project_id": parsed["project_id"],
    }
    if parsed["kind"] != "project":
        return {
            **base,
            "error": "请使用 Torna 项目链接：#/project/doc/{projectId}",
        }
    if not os.environ.get("TORNA_TOKEN", "").strip():
        return {**base, "error": "TORNA_TOKEN is not configured"}

    owns_client = client is None
    http = client or httpx.Client()
    try:
        project = _resolve_project(http, parsed["origin"], parsed["project_id"])
        tree_payload = fetch_json(
            http,
            parsed["origin"],
            "/doc/view/dataByProject",
            params={"projectId": parsed["project_id"]},
        )
        tree = _payload_data(tree_payload)
        leaves = flatten_doc_tree(tree if isinstance(tree, list) else [])
        return {
            "ok": True,
            "origin": parsed["origin"],
            "project_id": parsed["project_id"],
            "project_name": project["name"],
            "api_count": len(leaves),
            "sample_paths": [
                f"{str(item.get('httpMethod') or item.get('method')).upper()} "
                f"{item.get('url') or item.get('path')}"
                for item in leaves[:5]
            ],
            "warnings": [],
        }
    except Exception as exc:  # noqa: BLE001 - public diagnostic boundary
        return {**base, "error": _sanitize(str(exc))}
    finally:
        if owns_client:
            http.close()


def load_openapi_from_torna(
    doc_url: str,
    *,
    client: httpx.Client | None = None,
    concurrency: int | None = None,
) -> tuple[dict, dict]:
    """Pull a Torna project tree and details, then build OpenAPI."""
    parsed = parse_torna_doc_url(doc_url)
    if parsed["kind"] != "project":
        raise ValueError("Torna OpenAPI loading requires a #/project/doc/{id} URL")
    if not os.environ.get("TORNA_TOKEN", "").strip():
        raise ValueError("TORNA_TOKEN is not configured")

    worker_count = concurrency
    if worker_count is None:
        worker_count = _env_int("TORNA_DOC_CONCURRENCY", _DEFAULT_DOC_CONCURRENCY)
    if worker_count < 1:
        raise ValueError("Torna document concurrency must be at least 1")

    owns_client = client is None
    http = client or httpx.Client()
    try:
        project = _resolve_project(http, parsed["origin"], parsed["project_id"])
        tree_payload = fetch_json(
            http,
            parsed["origin"],
            "/doc/view/dataByProject",
            params={"projectId": parsed["project_id"]},
        )
        tree = _payload_data(tree_payload)
        leaves = flatten_doc_tree(tree if isinstance(tree, list) else [])
        if not leaves:
            raise ValueError("Torna project contains no API document leaves")

        details_by_index: dict[int, dict] = {}
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    fetch_json,
                    http,
                    parsed["origin"],
                    "/doc/view",
                    {"id": leaf["doc_id"]},
                ): (index, leaf["doc_id"])
                for index, leaf in enumerate(leaves)
            }
            for future in as_completed(futures):
                index, doc_id = futures[future]
                try:
                    payload = future.result()
                    detail = _payload_data(payload)
                    if not isinstance(detail, dict):
                        raise ValueError("detail data is not an object")
                    details_by_index[index] = detail
                except Exception as exc:  # noqa: BLE001 - partial failures are warnings
                    warnings.append(
                        f"Torna detail {doc_id} failed: {_sanitize(str(exc))}"
                    )

        if not details_by_index:
            raise ValueError("all Torna document details failed")
        details = [details_by_index[index] for index in sorted(details_by_index)]
        document = details_to_openapi(details, title=project["name"])
        meta = {
            "origin": parsed["origin"],
            "project_id": parsed["project_id"],
            "project_name": project["name"],
            "api_count": len(leaves),
            "fetched": len(details),
            "sample_paths": [
                f"{str(item.get('httpMethod') or item.get('method')).upper()} "
                f"{item.get('url') or item.get('path')}"
                for item in leaves[:5]
            ],
            "warnings": warnings,
        }
        return document, meta
    finally:
        if owns_client:
            http.close()


def _resolve_project(
    client: httpx.Client, origin: str, project_id: str
) -> dict:
    payload = fetch_json(client, origin, "/doc/view/projects")
    projects = flatten_projects(payload)
    for project in projects:
        if str(project.get("id")) == str(project_id):
            return project
    examples = ", ".join(project["name"] for project in projects[:5]) or "none"
    raise ValueError(
        f"Torna project {project_id} was not found. Available examples: {examples}"
    )


def _payload_data(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and "code" in payload:
        return payload["data"]
    return payload


def _normalize_api_path(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    path = parsed.path if parsed.scheme and parsed.netloc else value.split("?", 1)[0]
    if not path:
        return "/"
    return path if path.startswith("/") else f"/{path}"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _sanitize(message: str) -> str:
    token = os.environ.get("TORNA_TOKEN", "").strip()
    return message.replace(token, "[redacted]") if token else message

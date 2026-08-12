"""Tests for the Torna project-document client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from api_spotlight.torna import (
    details_to_openapi,
    fetch_json,
    flatten_doc_tree,
    flatten_projects,
    load_openapi_from_torna,
    parse_torna_doc_url,
    test_torna_connection as check_torna_connection,
    torna_detail_to_operation,
)

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_URL = "http://torna.example.test/#/project/doc/project-erp"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _response(request: httpx.Request, payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, request=request, json=payload)


def _project_transport(
    *,
    detail_failures: set[str] | None = None,
) -> httpx.MockTransport:
    failures = detail_failures or set()
    projects = _fixture("torna_projects.json")
    tree = _fixture("torna_tree.json")
    list_detail = _fixture("torna_doc_detail.json")
    create_detail = {
        "code": 20000,
        "data": {
            "id": "doc-create",
            "name": "Create order",
            "httpMethod": "POST",
            "path": "/api/orders",
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/doc/view/projects":
            return _response(request, projects)
        if request.url.path == "/doc/view/dataByProject":
            assert request.url.params["projectId"] == "project-erp"
            return _response(request, tree)
        if request.url.path == "/doc/view":
            doc_id = request.url.params["id"]
            if doc_id in failures:
                return _response(request, {"code": 500, "msg": "detail failed"})
            payload = list_detail if doc_id == "doc-list" else create_detail
            return _response(request, payload)
        return _response(request, {"code": 404, "msg": "unexpected route"}, 404)

    return httpx.MockTransport(handler)


def test_parse_torna_project_url() -> None:
    assert parse_torna_doc_url(
        "http://192.168.2.220:7700/#/project/doc/RqXBwzEl"
    ) == {
        "origin": "http://192.168.2.220:7700",
        "project_id": "RqXBwzEl",
        "kind": "project",
    }


def test_parse_torna_view_url_is_doc_kind() -> None:
    parsed = parse_torna_doc_url("https://torna.example/#/view/doc-42")
    assert parsed == {
        "origin": "https://torna.example",
        "project_id": "doc-42",
        "kind": "doc",
    }


def test_parse_torna_doc_url_rejects_unknown_hash_route() -> None:
    with pytest.raises(ValueError, match="Torna"):
        parse_torna_doc_url("https://torna.example/#/space/abc")


def test_flatten_projects_reads_projects_nested_in_spaces() -> None:
    projects = flatten_projects(_fixture("torna_projects.json"))
    assert [(item["id"], item["name"]) for item in projects] == [
        ("project-erp", "ERP API"),
        ("project-stock", "Stock API"),
        ("project-crm", "CRM API"),
    ]


def test_flatten_doc_tree_returns_only_endpoint_leaves() -> None:
    leaves = flatten_doc_tree(_fixture("torna_tree.json")["data"])
    assert [(item["doc_id"], item["httpMethod"], item["url"]) for item in leaves] == [
        ("doc-list", "GET", "/api/orders"),
        ("doc-create", "POST", "/api/orders"),
    ]


def test_torna_detail_to_operation_prefers_method_and_path() -> None:
    detail = _fixture("torna_doc_detail.json")["data"]
    assert torna_detail_to_operation(detail) == (
        "get",
        "/api/orders",
        {
            "summary": "List orders",
            "description": "Returns all orders.",
            "responses": {"default": {"description": "Torna response"}},
        },
    )


def test_details_to_openapi_indexes_operations_by_path_and_method() -> None:
    detail = _fixture("torna_doc_detail.json")["data"]
    document = details_to_openapi([detail], title="ERP API")
    assert document["info"]["title"] == "ERP API"
    assert document["paths"]["/api/orders"]["get"]["summary"] == "List orders"


def test_fetch_json_sends_compatible_token_headers_and_env_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TORNA_TOKEN", "super-secret")
    monkeypatch.setenv("TORNA_TIMEOUT_SECONDS", "12.5")

    class RecordingClient:
        request: httpx.Request | None = None
        timeout: float | None = None

        def get(self, url: str, **kwargs: Any) -> httpx.Response:
            self.timeout = kwargs["timeout"]
            self.request = httpx.Request("GET", url, headers=kwargs["headers"])
            return _response(self.request, {"code": "20000", "data": {"ready": True}})

    client = RecordingClient()
    payload = fetch_json(  # type: ignore[arg-type]
        client, "https://torna.example", "/doc/view/projects"
    )
    assert payload["data"] == {"ready": True}
    assert client.timeout == 12.5
    assert client.request is not None
    assert client.request.headers["token"] == "super-secret"
    assert client.request.headers["authorization"] == "super-secret"
    assert client.request.headers["x-token"] == "super-secret"


def test_fetch_json_rejects_login_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TORNA_TOKEN", "bad-secret")
    request = httpx.Request("GET", "https://torna.example/doc/view/projects")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: _response(
                request, {"code": 1000, "msg": "Please login first"}
            )
        )
    )
    with pytest.raises(ValueError, match="TORNA_TOKEN.*无效|未登录"):
        fetch_json(client, "https://torna.example", "/doc/view/projects")


def test_connection_without_token_returns_safe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TORNA_TOKEN", raising=False)
    result = check_torna_connection(PROJECT_URL)
    assert result["ok"] is False
    assert "TORNA_TOKEN" in result["error"]
    assert "token" not in {key.lower() for key in result}


def test_connection_missing_project_returns_examples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TORNA_TOKEN", "super-secret")
    client = httpx.Client(transport=_project_transport())
    result = check_torna_connection(
        "http://torna.example.test/#/project/doc/missing", client=client
    )
    assert result["ok"] is False
    assert "ERP API" in result["error"]
    assert "super-secret" not in repr(result)


def test_connection_success_counts_tree_leaves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TORNA_TOKEN", "super-secret")
    client = httpx.Client(transport=_project_transport())
    result = check_torna_connection(PROJECT_URL, client=client)
    assert result == {
        "ok": True,
        "origin": "http://torna.example.test",
        "project_id": "project-erp",
        "project_name": "ERP API",
        "api_count": 2,
        "sample_paths": ["GET /api/orders", "POST /api/orders"],
        "warnings": [],
    }
    assert "super-secret" not in repr(result)


def test_load_openapi_keeps_partial_details_and_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TORNA_TOKEN", "super-secret")
    client = httpx.Client(
        transport=_project_transport(detail_failures={"doc-create"})
    )
    document, meta = load_openapi_from_torna(
        PROJECT_URL, client=client, concurrency=2
    )
    assert set(document["paths"]) == {"/api/orders"}
    assert set(document["paths"]["/api/orders"]) == {"get"}
    assert meta["api_count"] == 2
    assert meta["fetched"] == 1
    assert len(meta["warnings"]) == 1
    assert "doc-create" in meta["warnings"][0]
    assert "super-secret" not in repr(meta)


def test_load_openapi_raises_when_all_details_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TORNA_TOKEN", "super-secret")
    client = httpx.Client(
        transport=_project_transport(detail_failures={"doc-list", "doc-create"})
    )
    with pytest.raises(ValueError, match="all Torna document details failed"):
        load_openapi_from_torna(PROJECT_URL, client=client)

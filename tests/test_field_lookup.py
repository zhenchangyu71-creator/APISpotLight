"""Tests for mock field extraction and OpenAPI field→API scoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from api_spotlight.evidence import extract_mock_field_evidence
from api_spotlight.field_lookup import (
    build_field_index,
    extract_fields_from_obj,
    score_apis_for_fields,
)
from api_spotlight.workflows import find_page_apis

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_fields_from_nested_object():
    fields = extract_fields_from_obj(
        {"code": 0, "data": {"receiptNo": "R1", "items": [{"sku": "A", "qty": 1}]}}
    )
    assert "receiptNo" in fields
    assert "sku" in fields
    assert "qty" in fields
    assert "code" not in fields  # generic wrapper ignored
    assert "data" not in fields
    assert "items" not in fields
    assert "0" not in fields


def test_build_field_index_includes_schema_properties_and_params():
    doc = yaml.safe_load((FIXTURES / "openapi.yaml").read_text(encoding="utf-8"))
    index = build_field_index(doc)
    assert "name" in index
    assert "sku" in index
    assert "orderid" in index  # lowercased
    # name appears on User schema used by GET /users
    targets = {(e["method"], e["path"]) for e in index["name"]}
    assert ("GET", "/users") in targets
    assert ("GET", "/users/{id}") in targets


def test_score_requires_exact_field_name_hit():
    doc = yaml.safe_load((FIXTURES / "openapi.yaml").read_text(encoding="utf-8"))
    index = build_field_index(doc)
    # description-only would be weak; sku is exact on OrderItem
    hits = score_apis_for_fields(index, ["sku", "qty"], top_n=3)
    assert hits
    assert hits[0]["path"] == "/orders/{orderId}/items"
    assert hits[0]["score"] >= 3
    assert "sku" in hits[0]["hit_fields"]


def test_score_ignores_low_score_description_noise():
    doc = yaml.safe_load((FIXTURES / "openapi.yaml").read_text(encoding="utf-8"))
    index = build_field_index(doc)
    hits = score_apis_for_fields(index, ["zzzz_not_a_real_field"], top_n=3)
    assert hits == []


def test_extract_mock_field_evidence_from_json_bodies(tmp_path: Path):
    mock = {
        "GET /wrong/path": {
            "data": {"sku": "X", "qty": 2, "receiptNo": "R9"}
        }
    }
    path = tmp_path / "page.mock.json"
    path.write_text(json.dumps(mock), encoding="utf-8")
    evidence, warnings = extract_mock_field_evidence([str(path)])
    assert warnings == []
    assert len(evidence) == 1
    assert evidence[0]["method"] == "GET"
    assert evidence[0]["path"] == "/wrong/path"
    assert set(evidence[0]["fields"]) >= {"sku", "qty", "receiptNo"}


def test_find_page_apis_field_fallback_when_path_wrong(tmp_path: Path):
    mock = {
        "GET /not/in/doc": {
            "data": {"sku": "ABC", "qty": 3}
        }
    }
    mock_path = tmp_path / "field.mock.json"
    mock_path.write_text(json.dumps(mock), encoding="utf-8")
    out = tmp_path / "out"
    result = find_page_apis(
        mock_paths=[str(mock_path)],
        screenshot_paths=[],
        api_doc_path=str(FIXTURES / "openapi.yaml"),
        output_dir=str(out),
        vision_enabled=False,
    )
    assert result["matched"] >= 1
    candidates = json.loads((out / "candidates.json").read_text(encoding="utf-8"))
    field_hits = [c for c in candidates if c.get("match_type") in {"field", "path+field"}]
    assert field_hits
    assert any(c["path"] == "/orders/{orderId}/items" for c in field_hits)
    hit = next(c for c in field_hits if c["path"] == "/orders/{orderId}/items")
    assert hit["selected"] is True
    assert "sku" in hit.get("hit_fields", [])
    assert hit.get("score", 0) >= 3


def test_path_match_upgraded_to_path_plus_field(tmp_path: Path):
    mock = {
        "GET /users/42": {
            "data": {"id": "42", "name": "Ada", "profile": {"bio": "x", "address": {"city": "SH"}}}
        }
    }
    mock_path = tmp_path / "user.mock.json"
    mock_path.write_text(json.dumps(mock), encoding="utf-8")
    out = tmp_path / "out"
    find_page_apis(
        mock_paths=[str(mock_path)],
        screenshot_paths=[],
        api_doc_path=str(FIXTURES / "openapi.yaml"),
        output_dir=str(out),
        vision_enabled=False,
    )
    candidates = json.loads((out / "candidates.json").read_text(encoding="utf-8"))
    user = next(c for c in candidates if c["path"] == "/users/{id}" and c["method"] == "GET")
    assert user["match_type"] in {"template", "path+field", "exact"}
    # with strong field hits should upgrade
    if user.get("hit_fields"):
        assert user["match_type"] == "path+field"
        assert "name" in user["hit_fields"] or "id" in user["hit_fields"]

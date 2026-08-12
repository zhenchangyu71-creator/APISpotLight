"""Tests for OpenAPI loading, matching, and operation extraction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from api_spotlight.openapi import extract_operations, load_openapi, match_requirements

FIXTURES = Path(__file__).parent / "fixtures"
OPENAPI_YAML = FIXTURES / "openapi.yaml"


@pytest.fixture
def document() -> dict:
    return load_openapi(str(OPENAPI_YAML))


def test_load_openapi_yaml(document: dict) -> None:
    assert document["openapi"].startswith("3.")
    assert "/users/{id}" in document["paths"]
    assert "User" in document["components"]["schemas"]


def test_load_openapi_json(tmp_path: Path, document: dict) -> None:
    json_path = tmp_path / "openapi.json"
    json_path.write_text(json.dumps(document), encoding="utf-8")
    loaded = load_openapi(str(json_path))
    assert loaded["info"]["title"] == document["info"]["title"]
    assert set(loaded["paths"]) == set(document["paths"])


def test_load_openapi_rejects_missing_file() -> None:
    with pytest.raises((FileNotFoundError, OSError, ValueError)):
        load_openapi(str(FIXTURES / "does-not-exist.yaml"))


def test_load_openapi_rejects_remote_url() -> None:
    with pytest.raises(ValueError):
        load_openapi("https://example.com/openapi.json")


def test_exact_match(document: dict) -> None:
    requirements = [{"method": "GET", "path": "/users", "source": ["mock"]}]
    candidates = match_requirements(document, requirements)
    assert len(candidates) == 1
    assert candidates[0]["method"] == "GET"
    assert candidates[0]["path"] == "/users"
    assert candidates[0]["match_type"] == "exact"
    assert candidates[0]["selected"] is True
    assert "mock" in candidates[0]["source"]


def test_match_requirements_accepts_source_string_or_list(document: dict) -> None:
    """source may arrive as a bare string or a list; never character-split a string."""
    as_string = match_requirements(
        document, [{"method": "GET", "path": "/users", "source": "mock"}]
    )
    as_list = match_requirements(
        document, [{"method": "GET", "path": "/users", "source": ["mock"]}]
    )
    assert as_string[0]["source"] == ["mock"]
    assert as_list[0]["source"] == ["mock"]


def test_extract_operations_deepcopies_nested_objects(document: dict) -> None:
    selected = [{"method": "GET", "path": "/users/{id}", "selected": True}]
    original_summary = document["paths"]["/users/{id}"]["get"]["summary"]
    original_city_type = document["components"]["schemas"]["Address"]["properties"][
        "city"
    ]["type"]

    slim, warnings = extract_operations(document, selected)
    assert warnings == []

    slim["paths"]["/users/{id}"]["get"]["summary"] = "MUTATED"
    slim["components"]["schemas"]["Address"]["properties"]["city"]["type"] = "integer"

    assert document["paths"]["/users/{id}"]["get"]["summary"] == original_summary
    assert (
        document["components"]["schemas"]["Address"]["properties"]["city"]["type"]
        == original_city_type
    )


def test_template_match_segment_safe(document: dict) -> None:
    requirements = [{"method": "GET", "path": "/users/42", "source": ["mock"]}]
    candidates = match_requirements(document, requirements)
    assert candidates[0]["path"] == "/users/{id}"
    assert candidates[0]["match_type"] == "template"
    assert candidates[0]["selected"] is True


def test_match_requirements_dedupes_canonical_template_matches(document: dict) -> None:
    requirements = [
        {
            "method": "GET",
            "path": "/users/42",
            "source": ["mock"],
            "confidence": 0.4,
        },
        {
            "method": "GET",
            "path": "/users/99",
            "source": ["screenshot", "mock"],
            "confidence": 0.9,
        },
    ]

    candidates = match_requirements(document, requirements)

    assert len(candidates) == 1
    assert candidates[0]["path"] == "/users/{id}"
    assert candidates[0]["source"] == ["mock", "screenshot"]
    assert candidates[0]["confidence"] == 0.9
    assert candidates[0]["match_type"] == "template"
    assert candidates[0]["doc_summary"] == "Get user by id"


def test_template_match_does_not_cross_segment_boundaries(document: dict) -> None:
    """/users/42/extra must not match /users/{id}."""
    requirements = [
        {"method": "GET", "path": "/users/42/extra", "source": ["mock"]},
    ]
    candidates = match_requirements(document, requirements)
    assert candidates[0]["match_type"] == "unmatched"
    assert candidates[0]["selected"] is False
    assert candidates[0]["path"] == "/users/42/extra"


def test_method_mismatch_is_unmatched(document: dict) -> None:
    requirements = [{"method": "DELETE", "path": "/users/42", "source": ["mock"]}]
    candidates = match_requirements(document, requirements)
    assert candidates[0]["match_type"] == "unmatched"
    assert candidates[0]["selected"] is False
    assert candidates[0]["method"] == "DELETE"
    assert candidates[0]["path"] == "/users/42"


def test_unmatched_requirements_are_retained(document: dict) -> None:
    requirements = [
        {"method": "GET", "path": "/users", "source": ["mock"]},
        {"method": "GET", "path": "/missing", "source": ["screenshot"], "confidence": 0.4},
    ]
    candidates = match_requirements(document, requirements)
    assert len(candidates) == 2
    unmatched = [c for c in candidates if c["match_type"] == "unmatched"]
    assert len(unmatched) == 1
    assert unmatched[0]["path"] == "/missing"
    assert unmatched[0]["selected"] is False
    assert "screenshot" in unmatched[0]["source"]


def test_extract_operations_keeps_path_level_parameters(document: dict) -> None:
    selected = [{"method": "GET", "path": "/users/{id}", "selected": True}]
    slim, warnings = extract_operations(document, selected)
    assert warnings == []
    path_item = slim["paths"]["/users/{id}"]
    assert "parameters" in path_item
    assert path_item["parameters"][0]["name"] == "id"
    assert "get" in path_item
    assert "post" not in path_item


def test_extract_operations_recursively_collects_local_component_refs(
    document: dict,
) -> None:
    selected = [{"method": "GET", "path": "/users/{id}", "selected": True}]
    slim, warnings = extract_operations(document, selected)
    assert warnings == []
    schemas = slim["components"]["schemas"]
    assert set(schemas) == {"User", "Profile", "Address"}
    assert "Unused" not in schemas
    assert "UserUpdate" not in schemas
    assert schemas["User"]["properties"]["profile"] == {
        "$ref": "#/components/schemas/Profile"
    }
    assert schemas["Profile"]["properties"]["address"] == {
        "$ref": "#/components/schemas/Address"
    }


def test_extract_operations_collects_nested_request_body_refs(document: dict) -> None:
    selected = [{"method": "POST", "path": "/users/{id}", "selected": True}]
    slim, _warnings = extract_operations(document, selected)
    schemas = slim["components"]["schemas"]
    assert "UserUpdate" in schemas
    assert "User" in schemas
    assert "Profile" in schemas
    assert "Address" in schemas


def test_fixture_yaml_is_valid_openapi_shape() -> None:
    raw = yaml.safe_load(OPENAPI_YAML.read_text(encoding="utf-8"))
    assert "paths" in raw and "components" in raw

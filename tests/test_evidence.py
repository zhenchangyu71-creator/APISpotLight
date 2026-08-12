"""Tests for mock evidence parsing and requirement merging."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_spotlight.evidence import merge_requirements, parse_mock_paths

FIXTURES = Path(__file__).parent / "fixtures"
MOCK_JSON = FIXTURES / "basic.mock.json"
MOCK_JS = FIXTURES / "basic.mock.js"


def test_parse_mock_json_object_keys() -> None:
    requirements, warnings = parse_mock_paths([str(MOCK_JSON)])
    assert warnings == []
    by_key = {(r["method"], r["path"]): r for r in requirements}
    assert ("GET", "/users") in by_key
    assert ("POST", "/users") in by_key
    assert ("GET", "/users/42") in by_key
    assert by_key[("GET", "/users")]["source"] == ["mock"]
    # Non METHOD/path keys must be ignored
    assert all(r["path"] != "not-an-api-key" for r in requirements)


def test_parse_mock_js_module_exports_keys() -> None:
    requirements, warnings = parse_mock_paths([str(MOCK_JS)])
    assert warnings == []
    by_key = {(r["method"], r["path"]): r for r in requirements}
    assert ("GET", "/orders") in by_key
    assert ("PUT", "/orders/1") in by_key
    assert ("DELETE", "/orders/1") in by_key
    assert by_key[("GET", "/orders")]["source"] == ["mock"]


def test_parse_mock_directory_recurses_supported_extensions_only(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "mocks" / "nested"
    nested.mkdir(parents=True)
    (nested / "ok.mock.json").write_text(
        json.dumps({"GET /nested": {"ok": True}}),
        encoding="utf-8",
    )
    (nested / "ok.mock.js").write_text(
        'module.exports = { "POST /nested": {} };\n',
        encoding="utf-8",
    )
    (nested / "ignore.txt").write_text(
        '"GET /should-not-parse": {}\n',
        encoding="utf-8",
    )
    (nested / "ignore.py").write_text(
        'APIS = {"GET /also-ignored": {}}\n',
        encoding="utf-8",
    )

    requirements, warnings = parse_mock_paths([str(tmp_path / "mocks")])
    assert warnings == []
    keys = {(r["method"], r["path"]) for r in requirements}
    assert ("GET", "/nested") in keys
    assert ("POST", "/nested") in keys
    assert ("GET", "/should-not-parse") not in keys
    assert ("GET", "/also-ignored") not in keys


def test_parse_mock_malformed_files_accumulate_warnings(tmp_path: Path) -> None:
    bad_json = tmp_path / "bad.mock.json"
    bad_json.write_text("{ not valid json", encoding="utf-8")
    bad_js = tmp_path / "bad.mock.js"
    bad_js.write_text("this is not a mock module", encoding="utf-8")
    missing = tmp_path / "missing.mock.json"

    requirements, warnings = parse_mock_paths(
        [str(bad_json), str(bad_js), str(missing), str(MOCK_JSON)]
    )
    assert len(warnings) >= 2
    assert any("bad.mock.json" in w for w in warnings)
    # Still returns successful parses
    assert any(r["path"] == "/users" for r in requirements)


def test_parse_mock_non_utf8_file_warns_and_continues(tmp_path: Path) -> None:
    """Non-UTF-8 mock must warn and not abort parsing later valid files."""
    binary_mock = tmp_path / "latin1.mock.json"
    # Invalid UTF-8 sequence; also not valid JSON when decoded as latin-1
    binary_mock.write_bytes(b'{"GET /bad": {"x": "\xff\xfe"}}')
    good = tmp_path / "good.mock.json"
    good.write_text(
        json.dumps({"GET /after-binary": {"ok": True}}),
        encoding="utf-8",
    )

    requirements, warnings = parse_mock_paths([str(binary_mock), str(good)])
    assert any("latin1.mock.json" in w for w in warnings)
    # Warning must not embed raw file bytes / undecodable content
    assert all(b"\xff" not in w.encode("utf-8", errors="surrogateescape") for w in warnings)
    assert not any("\xff" in w or "\xfe" in w for w in warnings)
    assert any(r["path"] == "/after-binary" for r in requirements)
    assert all(r["path"] != "/bad" for r in requirements)


def test_merge_requirements_dedupes_by_method_and_path() -> None:
    merged = merge_requirements(
        [{"method": "GET", "path": "/users", "source": ["mock"]}],
        [
            {
                "method": "get",
                "path": "/users",
                "source": ["screenshot"],
                "confidence": 0.7,
            }
        ],
    )
    assert len(merged) == 1
    assert merged[0]["method"] == "GET"
    assert merged[0]["path"] == "/users"
    assert merged[0]["source"] == ["mock", "screenshot"]
    assert merged[0]["confidence"] == 0.7


def test_merge_requirements_preserves_source_order_and_dedupes_sources() -> None:
    merged = merge_requirements(
        [{"method": "POST", "path": "/a", "source": ["mock", "screenshot"]}],
        [{"method": "POST", "path": "/a", "source": ["screenshot", "mock"]}],
        [{"method": "GET", "path": "/b", "source": ["screenshot"], "confidence": 0.5}],
    )
    assert len(merged) == 2
    post = next(r for r in merged if r["path"] == "/a")
    assert post["source"] == ["mock", "screenshot"]
    get = next(r for r in merged if r["path"] == "/b")
    assert get["confidence"] == 0.5


def test_merge_requirements_keeps_first_confidence_when_absent_later() -> None:
    merged = merge_requirements(
        [
            {
                "method": "GET",
                "path": "/x",
                "source": ["screenshot"],
                "confidence": 0.9,
            }
        ],
        [{"method": "GET", "path": "/x", "source": ["mock"]}],
    )
    assert merged[0]["confidence"] == 0.9
    assert merged[0]["source"] == ["screenshot", "mock"]


def test_merge_requirements_keeps_highest_existing_confidence() -> None:
    merged = merge_requirements(
        [
            {
                "method": "GET",
                "path": "/x",
                "source": ["screenshot"],
                "confidence": 0.4,
            }
        ],
        [
            {
                "method": "GET",
                "path": "/x",
                "source": ["screenshot"],
                "confidence": 0.9,
            }
        ],
    )

    assert merged[0]["confidence"] == 0.9

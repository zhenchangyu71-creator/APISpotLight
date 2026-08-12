"""Integration tests for stage-1 candidate finding and stage-2 confirmed export."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from api_spotlight.workflows import export_confirmed_apis, find_page_apis

FIXTURES = Path(__file__).parent / "fixtures"
OPENAPI_YAML = FIXTURES / "openapi.yaml"
MOCK_JSON = FIXTURES / "basic.mock.json"


@pytest.fixture
def clear_vision_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "VISION_MODEL"):
        monkeypatch.delenv(key, raising=False)


def test_stage1_mock_generates_matched_and_unmatched_candidates(
    tmp_path: Path, clear_vision_env: None
) -> None:
    """basic.mock.json: GET /users exact, GET /users/42 template, POST /users unmatched."""
    out = tmp_path / "out"
    result = find_page_apis(
        mock_paths=[str(MOCK_JSON)],
        screenshot_paths=[],
        api_doc_path=str(OPENAPI_YAML),
        output_dir=str(out),
        vision_enabled=False,
    )

    assert result["discovered"] == 3
    assert result["matched"] == 2
    assert result["unmatched"] == 1
    assert result["candidates_path"].endswith("candidates.json")
    assert result["markdown_path"].endswith("candidates.md")
    assert Path(result["candidates_path"]).is_file()
    assert Path(result["markdown_path"]).is_file()
    # Return value must not embed the full OpenAPI document
    assert "openapi" not in result
    assert "paths" not in result
    assert "components" not in result

    candidates = json.loads(Path(result["candidates_path"]).read_text(encoding="utf-8"))
    assert isinstance(candidates, list)
    assert len(candidates) == 3

    by_key = {(c["method"], c["path"]): c for c in candidates}
    assert by_key[("GET", "/users")]["match_type"] == "exact"
    assert by_key[("GET", "/users")]["selected"] is True
    assert by_key[("GET", "/users/{id}")]["match_type"] == "template"
    assert by_key[("GET", "/users/{id}")]["selected"] is True
    assert by_key[("POST", "/users")]["match_type"] == "unmatched"
    assert by_key[("POST", "/users")]["selected"] is False

    md = Path(result["markdown_path"]).read_text(encoding="utf-8")
    assert "GET" in md and "/users" in md
    assert "unmatched" in md.lower() or "Unmatched" in md


def test_stage1_vision_missing_credentials_degrades_with_warning(
    tmp_path: Path, clear_vision_env: None
) -> None:
    shot = tmp_path / "page.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")  # placeholder; vision should not call HTTP

    result = find_page_apis(
        mock_paths=[str(MOCK_JSON)],
        screenshot_paths=[str(shot)],
        api_doc_path=str(OPENAPI_YAML),
        output_dir=str(tmp_path / "out"),
        vision_enabled=True,
    )

    assert result["discovered"] >= 1
    assert result["matched"] >= 1
    assert any("vision" in w.lower() or "credential" in w.lower() for w in result["warnings"])
    assert Path(result["candidates_path"]).is_file()


def test_stage1_stats_use_canonical_deduped_candidates(
    tmp_path: Path,
    clear_vision_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api_spotlight.workflows as workflows_mod

    monkeypatch.setattr(
        workflows_mod,
        "parse_mock_paths",
        lambda paths: (
            [
                {"method": "GET", "path": "/users/42", "source": ["mock"]},
                {"method": "GET", "path": "/users/99", "source": ["screenshot"]},
            ],
            [],
        ),
    )

    result = find_page_apis(
        mock_paths=["unused.mock.json"],
        screenshot_paths=[],
        api_doc_path=str(OPENAPI_YAML),
        output_dir=str(tmp_path / "out"),
        vision_enabled=False,
    )

    assert result["discovered"] == 1
    assert result["matched"] == 1
    assert result["unmatched"] == 0
    candidates = json.loads(Path(result["candidates_path"]).read_text(encoding="utf-8"))
    assert candidates[0]["path"] == "/users/{id}"
    assert candidates[0]["source"] == ["mock", "screenshot"]


def test_stage1_fails_when_mock_and_vision_yield_no_evidence(
    tmp_path: Path, clear_vision_env: None
) -> None:
    empty_dir = tmp_path / "empty_mocks"
    empty_dir.mkdir()

    with pytest.raises((ValueError, RuntimeError), match="(?i)evidence|mock|vision|no "):
        find_page_apis(
            mock_paths=[str(empty_dir)],
            screenshot_paths=[],
            api_doc_path=str(OPENAPI_YAML),
            output_dir=str(tmp_path / "out"),
            vision_enabled=True,
        )


def test_stage2_exports_selected_only_with_recursive_refs(
    tmp_path: Path, clear_vision_env: None
) -> None:
    stage1 = find_page_apis(
        mock_paths=[str(MOCK_JSON)],
        screenshot_paths=[],
        api_doc_path=str(OPENAPI_YAML),
        output_dir=str(tmp_path / "stage1"),
        vision_enabled=False,
    )
    candidates_path = Path(stage1["candidates_path"])
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))

    # User edits: keep only GET /users/{id}
    for item in candidates:
        item["selected"] = item["method"] == "GET" and item["path"] == "/users/{id}"
    candidates_path.write_text(
        json.dumps(candidates, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    out_json = tmp_path / "exports" / "slim.json"
    result = export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        candidates_path=str(candidates_path),
        output_path=str(out_json),
        output_format="json",
    )

    assert result["output_path"] == str(out_json)
    assert result["exported"] == 1
    assert "openapi" not in result
    assert "paths" not in result
    assert out_json.is_file()

    slim = json.loads(out_json.read_text(encoding="utf-8"))
    assert set(slim["paths"]) == {"/users/{id}"}
    assert "get" in slim["paths"]["/users/{id}"]
    assert "post" not in slim["paths"]["/users/{id}"]
    assert set(slim["components"]["schemas"]) == {"User", "Profile", "Address"}
    assert "Unused" not in slim["components"]["schemas"]
    assert "UserUpdate" not in slim["components"]["schemas"]


def test_stage2_exports_yaml_and_markdown(
    tmp_path: Path, clear_vision_env: None
) -> None:
    stage1 = find_page_apis(
        mock_paths=[str(MOCK_JSON)],
        screenshot_paths=[],
        api_doc_path=str(OPENAPI_YAML),
        output_dir=str(tmp_path / "stage1"),
        vision_enabled=False,
    )
    candidates_path = Path(stage1["candidates_path"])
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    for item in candidates:
        item["selected"] = item["match_type"] != "unmatched"
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")

    yaml_path = tmp_path / "exports" / "slim.yaml"
    yaml_result = export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        candidates_path=str(candidates_path),
        output_path=str(yaml_path),
        output_format="yaml",
    )
    assert yaml_result["exported"] == 2
    loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    assert "/users" in loaded["paths"]
    assert "/users/{id}" in loaded["paths"]

    md_path = tmp_path / "exports" / "slim.md"
    md_result = export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        candidates_path=str(candidates_path),
        output_path=str(md_path),
        output_format="markdown",
    )
    assert md_result["exported"] == 2
    md_text = md_path.read_text(encoding="utf-8")
    assert "GET" in md_text
    assert "/users" in md_text
    assert "openapi" not in md_result


def test_stage2_fails_when_nothing_selected(
    tmp_path: Path, clear_vision_env: None
) -> None:
    stage1 = find_page_apis(
        mock_paths=[str(MOCK_JSON)],
        screenshot_paths=[],
        api_doc_path=str(OPENAPI_YAML),
        output_dir=str(tmp_path / "stage1"),
        vision_enabled=False,
    )
    candidates_path = Path(stage1["candidates_path"])
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    for item in candidates:
        item["selected"] = False
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")

    with pytest.raises((ValueError, RuntimeError), match="(?i)selected|confirm"):
        export_confirmed_apis(
            api_doc_path=str(OPENAPI_YAML),
            candidates_path=str(candidates_path),
            output_path=str(tmp_path / "exports" / "empty.json"),
            output_format="json",
        )


def test_stage2_warns_when_confirmed_path_missing(
    tmp_path: Path, clear_vision_env: None
) -> None:
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(
        json.dumps(
            [
                {
                    "method": "GET",
                    "path": "/does-not-exist",
                        "match_type": "unmatched",
                    "selected": True,
                    "source": ["mock"],
                    "doc_summary": "",
                },
                {
                    "method": "GET",
                    "path": "/users",
                    "match_type": "exact",
                    "selected": True,
                    "source": ["mock"],
                    "doc_summary": "List users",
                },
            ]
        ),
        encoding="utf-8",
    )

    out = tmp_path / "slim.json"
    result = export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        candidates_path=str(candidates_path),
        output_path=str(out),
        output_format="json",
    )

    assert result["exported"] == 1
    assert any(
        "does-not-exist" in w
        and "full canonical OpenAPI path" in w
        and "selected=true" in w
        for w in result["warnings"]
    )
    slim = json.loads(out.read_text(encoding="utf-8"))
    assert set(slim["paths"]) == {"/users"}


def test_stage2_exports_unmatched_after_canonical_path_confirmation(
    tmp_path: Path, clear_vision_env: None
) -> None:
    confirmed = [
        {
            "method": "GET",
            "path": "/users/{id}",
            "match_type": "unmatched",
            "selected": True,
            "source": ["screenshot"],
            "doc_summary": "",
        }
    ]
    out = tmp_path / "confirmed-unmatched.json"

    result = export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        output_path=str(out),
        confirmed_apis=confirmed,
        output_format="json",
    )

    assert result["exported"] == 1
    exported = json.loads(out.read_text(encoding="utf-8"))
    assert set(exported["paths"]) == {"/users/{id}"}
    assert "get" in exported["paths"]["/users/{id}"]


def test_stage2_accepts_confirmed_apis_directly(
    tmp_path: Path, clear_vision_env: None
) -> None:
    out = tmp_path / "direct.json"
    result = export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        output_path=str(out),
        confirmed_apis=[
            {
                "method": "GET",
                "path": "/users",
                "match_type": "exact",
                "selected": True,
                "source": ["mock"],
                "doc_summary": "List users",
            }
        ],
        output_format="json",
    )

    assert result["exported"] == 1
    assert set(json.loads(out.read_text(encoding="utf-8"))["paths"]) == {"/users"}


def test_stage2_requires_exactly_one_candidate_source(
    tmp_path: Path, clear_vision_env: None
) -> None:
    candidates_path = _selected_users_candidates(tmp_path)
    common = {
        "api_doc_path": str(OPENAPI_YAML),
        "output_path": str(tmp_path / "out.json"),
        "output_format": "json",
    }

    with pytest.raises(ValueError, match="(?i)exactly one|either|candidates_path|confirmed_apis"):
        export_confirmed_apis(**common)

    with pytest.raises(ValueError, match="(?i)exactly one|either|candidates_path|confirmed_apis"):
        export_confirmed_apis(
            **common,
            candidates_path=str(candidates_path),
            confirmed_apis=[],
        )


def test_stage1_creates_output_dir_with_stable_filenames(
    tmp_path: Path, clear_vision_env: None
) -> None:
    nested = tmp_path / "a" / "b" / "c"
    assert not nested.exists()
    result = find_page_apis(
        mock_paths=[str(MOCK_JSON)],
        screenshot_paths=[],
        api_doc_path=str(OPENAPI_YAML),
        output_dir=str(nested),
        vision_enabled=False,
    )
    assert nested.is_dir()
    assert Path(result["candidates_path"]).name == "candidates.json"
    assert Path(result["markdown_path"]).name == "candidates.md"
    assert Path(result["candidates_path"]).parent == nested
    assert Path(result["markdown_path"]).parent == nested


def _selected_users_candidates(tmp_path: Path) -> Path:
    path = tmp_path / "candidates.json"
    path.write_text(
        json.dumps(
            [
                {
                    "method": "GET",
                    "path": "/users",
                    "match_type": "exact",
                    "selected": True,
                    "source": ["mock"],
                    "doc_summary": "List users",
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_output_format_explicit_json_beats_md_suffix(
    tmp_path: Path, clear_vision_env: None
) -> None:
    """Explicit json/yaml/markdown wins; openapi must never treat .md as Markdown."""
    candidates_path = _selected_users_candidates(tmp_path)

    json_as_md = tmp_path / "export.md"
    export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        candidates_path=str(candidates_path),
        output_path=str(json_as_md),
        output_format="json",
    )
    parsed = json.loads(json_as_md.read_text(encoding="utf-8"))
    assert "paths" in parsed
    assert parsed["paths"]["/users"]["get"]["summary"] == "List users"

    openapi_md = tmp_path / "openapi-default.md"
    export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        candidates_path=str(candidates_path),
        output_path=str(openapi_md),
        output_format="openapi",
    )
    openapi_parsed = json.loads(openapi_md.read_text(encoding="utf-8"))
    assert "paths" in openapi_parsed

    yaml_out = tmp_path / "from-openapi.yaml"
    export_confirmed_apis(
        api_doc_path=str(OPENAPI_YAML),
        candidates_path=str(candidates_path),
        output_path=str(yaml_out),
        output_format="openapi",
    )
    assert "/users" in yaml.safe_load(yaml_out.read_text(encoding="utf-8"))["paths"]

    with pytest.raises(ValueError, match="(?i)unsupported|output_format"):
        export_confirmed_apis(
            api_doc_path=str(OPENAPI_YAML),
            candidates_path=str(candidates_path),
            output_path=str(tmp_path / "bad.out"),
            output_format="pdf",
        )


def test_stage2_fails_when_all_selected_paths_missing_without_writing(
    tmp_path: Path, clear_vision_env: None
) -> None:
    candidates_path = tmp_path / "candidates.json"
    candidates_path.write_text(
        json.dumps(
            [
                {
                    "method": "GET",
                    "path": "/gone-a",
                    "match_type": "exact",
                    "selected": True,
                    "source": ["mock"],
                    "doc_summary": "",
                },
                {
                    "method": "POST",
                    "path": "/gone-b",
                    "match_type": "exact",
                    "selected": True,
                    "source": ["mock"],
                    "doc_summary": "",
                },
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "exports" / "should-not-exist.json"
    with pytest.raises(ValueError, match="(?i)export|missing|not found|zero|0"):
        export_confirmed_apis(
            api_doc_path=str(OPENAPI_YAML),
            candidates_path=str(candidates_path),
            output_path=str(out),
            output_format="json",
        )
    assert not out.exists()


def test_rejects_empty_and_overwriting_paths(
    tmp_path: Path, clear_vision_env: None
) -> None:
    candidates_path = _selected_users_candidates(tmp_path)

    with pytest.raises(ValueError, match="(?i)output_dir|empty"):
        find_page_apis(
            mock_paths=[str(MOCK_JSON)],
            screenshot_paths=[],
            api_doc_path=str(OPENAPI_YAML),
            output_dir="",
            vision_enabled=False,
        )

    with pytest.raises(ValueError, match="(?i)output_path|empty"):
        export_confirmed_apis(
            api_doc_path=str(OPENAPI_YAML),
            candidates_path=str(candidates_path),
            output_path="",
            output_format="json",
        )

    with pytest.raises(ValueError, match="(?i)candidates_path|empty"):
        export_confirmed_apis(
            api_doc_path=str(OPENAPI_YAML),
            candidates_path="",
            output_path=str(tmp_path / "out.json"),
            output_format="json",
        )

    with pytest.raises(ValueError, match="(?i)candidates|remote|url|local"):
        export_confirmed_apis(
            api_doc_path=str(OPENAPI_YAML),
            candidates_path="https://example.com/candidates.json",
            output_path=str(tmp_path / "out.json"),
            output_format="json",
        )

    with pytest.raises(ValueError, match="(?i)overwrite|same|api_doc"):
        export_confirmed_apis(
            api_doc_path=str(OPENAPI_YAML),
            candidates_path=str(candidates_path),
            output_path=str(OPENAPI_YAML),
            output_format="yaml",
        )


def test_stage1_candidates_markdown_includes_confidence(
    tmp_path: Path, clear_vision_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional nicety: confidence column when present on candidates."""
    import api_spotlight.workflows as workflows_mod

    def fake_analyze(paths, client=None):
        return (
            [
                {
                    "method": "GET",
                    "path": "/missing-from-doc",
                    "confidence": 0.42,
                    "source": ["screenshot"],
                }
            ],
            [],
        )

    monkeypatch.setattr(workflows_mod, "analyze_screenshots", fake_analyze)
    shot = tmp_path / "ui.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = find_page_apis(
        mock_paths=[str(MOCK_JSON)],
        screenshot_paths=[str(shot)],
        api_doc_path=str(OPENAPI_YAML),
        output_dir=str(tmp_path / "out"),
        vision_enabled=True,
    )
    md = Path(result["markdown_path"]).read_text(encoding="utf-8")
    assert "0.42" in md
    assert "Confidence" in md or "confidence" in md

# APISpotLight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python FastMCP server that finds page-relevant APIs from mock files and screenshots, writes a human-reviewable candidate list, then exports only confirmed APIs from a full OpenAPI document.

**Architecture:** Keep parsing, matching, reference collection, vision analysis, candidate generation, and confirmed export as isolated modules. The two MCP tools are thin orchestration wrappers around testable Python functions; all large artifacts are written to disk and tool responses contain paths and statistics.

**Tech Stack:** Python 3.11+, FastMCP, httpx, PyYAML, pytest.

## Global Constraints

- The API source is a local Torna-exported OpenAPI 3.x JSON/YAML file, not the Torna hash URL.
- Stage 1 writes `candidates.json` and `candidates.md`.
- Stage 2 exports only `selected=true` entries and recursively includes referenced `components` objects.
- Vision uses an OpenAI-compatible endpoint configured by `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `VISION_MODEL`.
- Candidate `source` is an ordered unique list: `["mock"]`, `["screenshot"]`, or `["mock", "screenshot"]`.
- Canonical dedupe runs after matching by `(method, document path)`; confidence keeps the highest existing value.
- Vision requests use a 30-second timeout and request failures degrade to warnings.
- Missing vision credentials degrade to mock-only when mock evidence exists.
- Never silently discard unmatched requirements.
- Do not commit or log credentials.

---

### Task 1: Project foundation and OpenAPI core

**Files:**
- Create: `pyproject.toml`
- Create: `src/api_spotlight/__init__.py`
- Create: `src/api_spotlight/openapi.py`
- Test: `tests/test_openapi.py`
- Test fixture: `tests/fixtures/openapi.yaml`

**Interfaces:**
- Produces: `load_openapi(source: str) -> dict`
- Produces: `match_requirements(document: dict, requirements: list[dict]) -> list[dict]`
- Produces: `extract_operations(document: dict, selected: list[dict]) -> tuple[dict, list[str]]`

- [ ] Write tests for JSON/YAML loading, exact matching, segment-safe `{id}` matching, method mismatch, unmatched retention, and recursive `$ref` extraction.
- [ ] Run `python -m pytest tests/test_openapi.py -q`; verify failure because `api_spotlight.openapi` is absent.
- [ ] Implement strict local-file loading, canonical path matching, candidate records, path-level parameters retention, and recursive local `#/components/...` collection.
- [ ] Run `python -m pytest tests/test_openapi.py -q`; expect all tests to pass.

Key required behavior:

```python
requirements = [{"method": "GET", "path": "/users/42", "source": ["mock"]}]
candidates = match_requirements(document, requirements)
assert candidates[0]["path"] == "/users/{id}"
assert candidates[0]["match_type"] == "template"
assert candidates[0]["selected"] is True
```

### Task 2: Mock and screenshot evidence extraction

**Files:**
- Create: `src/api_spotlight/evidence.py`
- Create: `src/api_spotlight/vision.py`
- Test: `tests/test_evidence.py`
- Test: `tests/test_vision.py`
- Test fixtures: `tests/fixtures/basic.mock.json`, `tests/fixtures/basic.mock.js`

**Interfaces:**
- Consumes: none from Task 1.
- Produces: `parse_mock_paths(paths: list[str]) -> tuple[list[dict], list[str]]`
- Produces: `merge_requirements(*groups: list[dict]) -> list[dict]`
- Produces: `analyze_screenshots(paths: list[str], client: httpx.Client | None = None) -> tuple[list[dict], list[str]]`

- [ ] Write tests for JSON object keys, JS `module.exports` keys, directories, malformed files, dedupe/source merging, fenced model JSON, and missing credentials.
- [ ] Run the two test files and verify they fail because modules are absent.
- [ ] Implement conservative regex extraction for `"METHOD /path"` keys, recursive supported-file scanning, warning accumulation, data-URL image payloads, and strict model JSON validation.
- [ ] Run the two test files; expect all tests to pass.

Required merge behavior:

```python
merged = merge_requirements(
    [{"method": "GET", "path": "/users", "source": ["mock"]}],
    [{"method": "get", "path": "/users", "source": ["screenshot"], "confidence": 0.7}],
)
assert merged[0]["source"] == ["mock", "screenshot"]
```

### Task 3: Two-stage workflows and exporters

**Files:**
- Create: `src/api_spotlight/workflows.py`
- Create: `src/api_spotlight/exporters.py`
- Test: `tests/test_workflows.py`

**Interfaces:**
- Consumes: all Task 1 and Task 2 interfaces.
- Produces: `find_page_apis(mock_paths, screenshot_paths, api_doc_path, output_dir, vision_enabled=True) -> dict`
- Produces: `export_confirmed_apis(api_doc_path, output_path, candidates_path=None, confirmed_apis=None, output_format="openapi") -> dict`

- [ ] Write integration tests that generate canonical-deduped candidates, preserve an unmatched requirement, accept exactly one of `candidates_path`/`confirmed_apis`, and export a valid reduced OpenAPI document after unmatched path confirmation.
- [ ] Run `python -m pytest tests/test_workflows.py -q`; verify failure because workflows are absent.
- [ ] Implement stage 1 artifact writing and summary results.
- [ ] Implement stage 2 validation, selected-only extraction, OpenAPI JSON/YAML and Markdown output.
- [ ] Run workflow tests, then the full suite.

Stage 1 response shape:

```python
{
    "candidates_path": ".../candidates.json",
    "markdown_path": ".../candidates.md",
    "discovered": 3,
    "matched": 2,
    "unmatched": 1,
    "warnings": [],
}
```

### Task 4: FastMCP server and operator documentation

**Files:**
- Create: `src/api_spotlight/server.py`
- Create: `README.md`
- Create: `.env.example`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `find_page_apis` and `export_confirmed_apis`.
- Produces MCP tools named exactly `find_page_apis` and `export_confirmed_apis`.

- [ ] Write a server import/registration smoke test.
- [ ] Run it and verify failure because the server is absent.
- [ ] Register both FastMCP tools with typed arguments and descriptions.
- [ ] Document installation, Cursor MCP configuration, Torna export prerequisite, candidate confirmation editing, and both tool calls.
- [ ] Run `python -m pytest -q` and `python -m compileall -q src`.
- [ ] Run a local fixture-based end-to-end command and inspect both generated artifacts.

## Self-review

- Spec coverage: both stages, source attribution, match types, human confirmation, `$ref` closure, degradation behavior, and file-based output are assigned to tasks.
- Placeholder scan: no deferred implementation steps.
- Type consistency: workflow and server signatures use the same names throughout.

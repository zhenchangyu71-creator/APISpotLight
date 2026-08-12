# Torna Link Pull + Slash Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull full project APIs from a Torna project doc URL via `TORNA_TOKEN`, expose `test_torna_connection`, extend stage-1/2 to accept that URL, and ship `/查找页面接口` + `/导出确认接口`.

**Architecture:** Add `torna.py` for URL parse, authenticated HTTP, tree/detail fetch, and Torna→OpenAPI conversion. Resolve `api_doc_source` (alias `api_doc_path`) to either a local OpenAPI file or a Torna-built document. Register a third MCP tool and Cursor plugin command wrappers.

**Tech Stack:** Python 3.11+, httpx, FastMCP, pytest; Cursor `commands/*.md` + `.cursor-plugin/plugin.json` + `mcp.json`.

## Global Constraints

- Auth only via env `TORNA_TOKEN` (never tool args, logs, candidates, or returns).
- Project URL form: `http(s)://{host}/#/project/doc/{projectId}`.
- Success codes: `0` or `20000` (str/int); login fail on `1000` / login msg.
- Default timeout 30s; detail concurrency default 6.
- Stage-1 Torna pulls write `output_dir/full-openapi.from-torna.json` for stage-2 reuse.
- Keep two-stage confirmation; do not skip human review.
- Never commit secrets; do not claim live pull success without `TORNA_TOKEN` evidence.

---

### Task 1: Torna client core (parse, auth, tree, convert)

**Files:**
- Create: `src/api_spotlight/torna.py`
- Create: `tests/fixtures/torna_projects.json`
- Create: `tests/fixtures/torna_tree.json`
- Create: `tests/fixtures/torna_doc_detail.json`
- Test: `tests/test_torna.py`

**Interfaces:**
- Produces: `parse_torna_doc_url(url: str) -> dict`
- Produces: `flatten_projects(projects_payload) -> list[dict]`
- Produces: `flatten_doc_tree(tree: list) -> list[dict]`
- Produces: `torna_detail_to_operation(detail: dict) -> tuple[str, str, dict] | None`  # method, path, operation
- Produces: `details_to_openapi(details: list[dict], *, title: str = "Torna Export") -> dict`
- Produces: `fetch_json(client, origin, path, params=None) -> dict`
- Produces: `test_torna_connection(doc_url: str, *, client=None) -> dict`
- Produces: `load_openapi_from_torna(doc_url: str, *, client=None, concurrency: int | None = None) -> tuple[dict, dict]`  # openapi, meta

- [ ] Write failing tests for project URL parse, `#/view/` rejection for project-source kind, missing token, login code 1000, project resolve, tree flatten, detail→path/method, `test_torna_connection` ok shape, partial detail failures as warnings.
- [ ] Run: `.venv/bin/python -m pytest tests/test_torna.py -q` → expect import/missing failures.
- [ ] Implement `torna.py` with httpx, env `TORNA_TOKEN` / `TORNA_TIMEOUT_SECONDS` / `TORNA_DOC_CONCURRENCY`, ThreadPoolExecutor for details, sanitize errors (no token).
- [ ] Run tests green.

Key behaviors:

```python
assert parse_torna_doc_url(
    "http://torna.example.com:7700/#/project/doc/project-erp"
) == {"origin": "http://torna.example.com:7700", "project_id": "project-erp", "kind": "project"}

# #/view/x → kind "doc" (not usable as full project source)
```

`test_torna_connection` returns `ok/origin/project_id/project_name/api_count/sample_paths/warnings` or `ok=false,error=...`.

### Task 2: Wire Torna into workflows + MCP tool

**Files:**
- Modify: `src/api_spotlight/workflows.py`
- Modify: `src/api_spotlight/server.py`
- Modify: `tests/test_workflows.py`
- Modify: `tests/test_server.py`
- Test: add cases in `tests/test_workflows.py` for Torna source via monkeypatched loader

**Interfaces:**
- Consumes: Task 1 interfaces
- Produces: `find_page_apis(..., api_doc_path: str | None = None, api_doc_source: str | None = None, ...)` accepting either alias
- Produces: `export_confirmed_apis(..., api_doc_path / api_doc_source same aliasing)`
- Produces: stage1 may return `openapi_cache_path` when Torna used
- Produces: MCP tool `test_torna_connection`

- [ ] Add failing tests: local path still works; Torna URL uses loader; writes `full-openapi.from-torna.json`; export can use cache path; server lists 3 tools with schemas.
- [ ] Implement `resolve_api_document(source) -> tuple[dict, str | None]` helper in workflows (or thin wrapper): local → `load_openapi`; Torna project URL → `load_openapi_from_torna` + optional cache write.
- [ ] Register `test_torna_connection` on FastMCP.
- [ ] Keep `assert_local_nonempty_path` only for true local paths; Torna URLs skip local-file assert.
- [ ] Full suite green.

### Task 3: Slash commands + plugin packaging + docs

**Files:**
- Create: `commands/查找页面接口.md`
- Create: `commands/导出确认接口.md`
- Create: `.cursor-plugin/plugin.json`
- Create: `mcp.json`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Produces: Cursor plugin metadata pointing at commands + mcpServers
- Produces: MCP config with `TORNA_TOKEN` env mapping and `python -m api_spotlight.server`

- [ ] Write command markdown mirroring TestMasterPlugin style: check MCP, call `test_torna_connection` when URL, then `find_page_apis` / `export_confirmed_apis`, prefer cache path for stage 2.
- [ ] Update README: Torna link usage, token setup, slash commands, live test URL example.
- [ ] `.env.example` add `TORNA_TOKEN`, `TORNA_TIMEOUT_SECONDS`, `TORNA_DOC_CONCURRENCY`.
- [ ] Run full pytest + compileall.

### Task 4: Live Torna connection verification

**Files:**
- Create: `scripts/verify_torna_connection.py` (optional thin CLI)
- Or run via Python -c / pytest mark

- [ ] If `TORNA_TOKEN` unset: run script/tool and assert clear failure message; document that live success is blocked pending token.
- [ ] If `TORNA_TOKEN` set: call `test_torna_connection` with the real Torna project URL (do not hardcode internal hosts in docs), require `ok is True` and `api_count > 0`; print project_name and sample_paths.
- [ ] Do not fake success.

## Self-review

- Spec coverage: connection tool, project URL pull, OpenAPI convert, workflow aliases, cache file, slash commands, plugin/mcp config, live test — all tasked.
- No placeholders.
- Aliases `api_doc_path` / `api_doc_source` consistent across Task 2–3.

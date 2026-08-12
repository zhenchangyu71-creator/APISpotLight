# Mock Field API Lookup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or execute inline with TDD.

**Goal:** Hybrid matching — path first, then score mock fields against OpenAPI parameter names/descriptions to locate APIs.

**Architecture:** Extract fields from mock bodies; build a field→operation index from the loaded OpenAPI; score unmatched/field-only evidence; merge into candidates with `hit_fields`/`score`/`match_type`.

**Tech Stack:** Existing Python package, pytest.

## Global Constraints

- Hybrid only: do not replace path matching.
- Exact field-name hit required (score ≥ 3) to add field candidates.
- Never put secrets in outputs.
- Keep stage-2 confirmation unchanged.

---

### Task 1: Field extract + OpenAPI field index + scoring

**Files:**
- Create: `src/api_spotlight/field_lookup.py`
- Modify: `src/api_spotlight/evidence.py` (export bodies/fields from mocks)
- Test: `tests/test_field_lookup.py`
- Fixtures as needed under `tests/fixtures/`

**Interfaces:**
- `extract_fields_from_obj(obj) -> list[str]`
- `extract_mock_field_evidence(paths) -> tuple[list[dict], list[str]]`  
  each evidence: `{fields: list[str], method?: str, path?: str, source_file?: str}`
- `build_field_index(document: dict) -> dict[str, list[dict]]`
- `score_apis_for_fields(index, fields: list[str], *, top_n=3) -> list[dict]`  
  returns `{method, path, score, hit_fields, doc_summary?}`

- [ ] TDD then implement.

### Task 2: Wire into find_page_apis + candidate export display

**Files:**
- Modify: `src/api_spotlight/workflows.py`
- Modify: `src/api_spotlight/openapi.py` merge helpers if needed
- Modify: `src/api_spotlight/exporters.py` (markdown show hit_fields/score)
- Modify: `tests/test_workflows.py`
- Modify: `commands/查找页面接口.md` briefly

- [ ] After path match, run field scoring for unmatched + field-only evidence; merge as `field` / `path+field`.
- [ ] Full pytest green.

### Task 3: Demo on ReceiptOrder fixture (optional smoke)

- [ ] Small fixture mock with wrong path but ReceiptOrder-like fields → matches documented operation in fixture OpenAPI.

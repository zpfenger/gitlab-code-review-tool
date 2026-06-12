# Token Usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. User override for this run: work on the current branch and do not commit code.

**Goal:** Persist successful LLM token usage and expose token usage detail, statistics, and per-record token totals.

**Architecture:** Add a `token_usage_log` table plus a small `llm_usage` service that parses OpenAI-compatible `usage` payloads and safely writes logs. LLM callers return `LLMResult(content, usage)` while preserving existing string-like behavior where practical; caller layers persist usage only after the related business row has an id. API and template changes read token totals with batch aggregation by `(biz_type, biz_id)`.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite-compatible aggregation, Jinja2 templates, Chart.js, pytest.

---

### Task 1: Model And Usage Parser

**Files:**
- Create: `app/models/token_usage.py`
- Create: `app/services/llm_usage.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_models/test_token_usage.py`
- Test: `tests/test_services/test_llm_usage.py`

- [ ] **Step 1: Write failing model and parser tests**

Add tests that assert:
- `TokenUsageLog.__tablename__ == "token_usage_log"`
- model columns include `biz_type`, `biz_id`, `project_name`, `author`, `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `created_at_ts`
- `parse_usage` returns `TokenUsage` for valid usage
- `parse_usage` returns `None` for missing or non-integer usage values
- `record_token_usage` skips `None` usage and never raises on database failures

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_models/test_token_usage.py tests/test_services/test_llm_usage.py -q
```

Expected: fail because the new model and service do not exist yet.

- [ ] **Step 3: Implement model and shared service**

Create `TokenUsageLog`, `TokenUsage`, `LLMResult`, `parse_usage`, `record_token_usage`, `aggregate_token_usage_by_biz`, and `empty_token_totals`.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_models/test_token_usage.py tests/test_services/test_llm_usage.py -q
```

Expected: pass.

### Task 2: LLM Return Values And Persistence Hooks

**Files:**
- Modify: `app/services/code_reviewer.py`
- Modify: `app/services/webhook_reviewer.py`
- Modify: `app/services/efficiency_llm.py`
- Modify: `app/services/task_executor.py`
- Modify: `app/services/webhook_worker.py`
- Modify: `app/services/efficiency_aggregator.py`
- Test: `tests/test_services/test_code_reviewer.py`
- Test: `tests/test_services/test_efficiency_llm.py`
- Test: `tests/test_services/test_efficiency_aggregator.py`

- [ ] **Step 1: Write failing service tests**

Add tests that assert:
- `CodeReviewer.review()` exposes parsed usage from a successful API response
- `efficiency_llm.call_and_parse()` includes `usage` when `call_llm()` returns an `LLMResult`
- `EfficiencyAggregator` records one `efficiency` token row after a successful daily score row is committed

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_services/test_code_reviewer.py tests/test_services/test_efficiency_llm.py tests/test_services/test_efficiency_aggregator.py -q
```

Expected: fail on usage-related expectations.

- [ ] **Step 3: Implement LLM usage plumbing**

Update LLM callers to parse `usage` from response JSON and return `LLMResult`. Update task executor, webhook worker, and efficiency aggregator to call `record_token_usage` after the corresponding business record has an id.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_services/test_code_reviewer.py tests/test_services/test_efficiency_llm.py tests/test_services/test_efficiency_aggregator.py -q
```

Expected: pass.

### Task 3: Token Usage API

**Files:**
- Create: `app/api/token_usage.py`
- Modify: `app/main.py`
- Test: `tests/test_api/test_token_usage.py`

- [ ] **Step 1: Write failing API tests**

Add tests for:
- non-admin users receive 403 from `/api/token-usage`
- admin users can page and filter detail rows
- `/api/token-usage/stats` returns summary totals, today/month totals, business-type totals, daily trend, and model totals

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_api/test_token_usage.py -q
```

Expected: fail because router is missing.

- [ ] **Step 3: Implement API router and include it**

Add list and stats endpoints under `/api/token-usage`, using existing `ApiResponse` and `require_system_admin`.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_api/test_token_usage.py -q
```

Expected: pass.

### Task 4: Business API Token Columns

**Files:**
- Modify: `app/api/webhook_reviews.py`
- Modify: `app/api/reports.py`
- Modify: `app/api/efficiency.py`
- Test: `tests/test_api/test_efficiency.py`
- Test: new focused assertions in token usage API/service tests

- [ ] **Step 1: Write failing API assertions**

Add or update tests to assert:
- webhook list/detail rows include `token_usage`
- efficiency list/detail rows include `token_usage`
- report list items include `token_usage` when matching usage rows exist

- [ ] **Step 2: Run targeted tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_api/test_efficiency.py tests/test_api/test_token_usage.py -q
```

Expected: fail on missing `token_usage` fields.

- [ ] **Step 3: Add batch token aggregation to business APIs**

Use `aggregate_token_usage_by_biz` to avoid N+1 queries. For report files, use best-effort matching through report `TaskLog` rows and report usage rows; old or unmatched reports return `None`.

- [ ] **Step 4: Run targeted tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_api/test_efficiency.py tests/test_api/test_token_usage.py -q
```

Expected: pass.

### Task 5: Pages And Navigation

**Files:**
- Create: `app/templates/token_usage.html`
- Modify: `app/templates/base.html`
- Modify: `app/templates/webhook_reviews.html`
- Modify: `app/templates/reports.html`
- Modify: `app/templates/efficiency.html`
- Modify: `app/static/js/efficiency.js`
- Modify: `app/main.py`

- [ ] **Step 1: Add token usage page route and template**

Create `/token-usage` as a system-admin-only page. The page calls the API for summary cards, charts, filters, and paginated detail rows.

- [ ] **Step 2: Add admin navigation**

Add a “Token 统计” nav item under the admin-only navigation section.

- [ ] **Step 3: Add business-page token columns**

Show `Token 消耗` in webhook and efficiency tables, and show token text near report file entries. Missing usage renders `-`.

### Task 6: Final Verification

**Files:** all changed files.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests/test_services/test_llm_usage.py tests/test_models/test_token_usage.py tests/test_services/test_code_reviewer.py tests/test_services/test_efficiency_llm.py tests/test_services/test_efficiency_aggregator.py tests/test_api/test_token_usage.py tests/test_api/test_efficiency.py -q
```

Expected: pass.

- [ ] **Step 2: Run broader regression if time permits**

Run:

```powershell
python -m pytest tests -q
```

Expected: pass or report unrelated pre-existing failures with evidence.

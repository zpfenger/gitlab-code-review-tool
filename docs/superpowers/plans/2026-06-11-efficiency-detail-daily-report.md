# Efficiency Detail Daily Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add project-separated daily review report entries to the efficiency detail drawer, open report content in a new modal, and remove the drawer's visible today-commit list.

**Architecture:** Keep report body loading centralized in `/api/reports/content`. Extend `/api/efficiency/detail` to return only report entries that the same user can open through `/api/reports/content`, and fix the content endpoint's `path` mode so it reads the real relative file path instead of rebuilding a sanitized filename.

**Tech Stack:** FastAPI, SQLAlchemy, Jinja templates, plain JavaScript, pytest, existing `ApiResponse`, existing session auth and project permission helpers.

---

## File Structure

- Modify `app/api/reports.py`
  - Fix `/api/reports/content` path-mode behavior for literal relative paths such as `project-a/daily/2026-06-10/Zhang Peng.md`.
  - Keep the existing individual-parameter mode unchanged.

- Modify `tests/test_api/test_project_admin_report_permissions.py`
  - Add a regression test proving `path` mode reads a report whose real filename contains a space.

- Modify `app/api/efficiency.py`
  - Add daily report discovery helpers close to `/detail`.
  - Extend single-day `/api/efficiency/detail` response with `daily_reports`.
  - Preserve existing `commits` response data for compatibility.

- Modify `tests/test_api/test_efficiency.py`
  - Add tests for `daily_reports` discovery, exact matching, permission filtering, and `summary=None`.

- Modify `app/templates/efficiency.html`
  - Add a new daily report modal.
  - Add small styling for report entry buttons if needed.

- Modify `app/static/js/efficiency.js`
  - Render `daily_reports` above the trend chart.
  - Add report-modal open/close and content loading.
  - Remove visible “今日提交” rendering from `renderDrawer`.

- Create `tests/test_templates/test_efficiency_daily_report_ui.py`
  - Static checks that the modal exists, the report section appears before trend, and “今日提交” is no longer rendered by the efficiency JS.

---

### Task 1: Fix Report Content Path Mode

**Files:**
- Modify: `app/api/reports.py`
- Test: `tests/test_api/test_project_admin_report_permissions.py`

- [ ] **Step 1: Write the failing path-mode regression test**

Append this test to `tests/test_api/test_project_admin_report_permissions.py`:

```python
def test_content_path_reads_literal_filename_with_spaces(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """path 模式应读取真实相对路径，不能把空格重建成下划线。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    target_dir = report_dir / "project-a" / "daily" / "2026-06-09"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "Zhang Peng.md").write_text("space author content", encoding="utf-8")

    response = client.get(
        "/api/reports/content",
        params={"path": "project-a/daily/2026-06-09/Zhang Peng.md"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["content"] == "space author content"
    assert data["path"] == "project-a/daily/2026-06-09/Zhang Peng.md"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
python -m pytest tests/test_api/test_project_admin_report_permissions.py::test_content_path_reads_literal_filename_with_spaces -q
```

Expected: FAIL with HTTP `404`, because current code rebuilds `Zhang_Peng.md`.

- [ ] **Step 3: Implement literal path reading for `path` mode**

In `app/api/reports.py`, change `get_report_content` so `path` mode preserves the parsed real relative path after validation:

```python
@router.get("/content")
async def get_report_content(
    path: Optional[str] = Query(None, min_length=1),
    project: Optional[str] = Query(None, min_length=1, max_length=100),
    report_type: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    report_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    author: Optional[str] = Query(None, min_length=1, max_length=100),
    current_user: User = Depends(get_current_user_full),
    db: Session = Depends(get_db),
):
    """Get content of a specific report"""
    literal_relative_path = None

    if path:
        project, report_type, report_date, author = _parse_report_path(path)
        literal_relative_path = Path(path).as_posix()

    if not all([project, report_type, report_date, author]):
        raise HTTPException(status_code=400, detail="Missing required parameters")

    assert project is not None and author is not None

    allowed_project_names = _get_user_allowed_project_names(current_user, db)
    if project not in allowed_project_names:
        raise HTTPException(status_code=403, detail="您没有权限查看此项目的报告")

    project_name_to_id = _get_project_name_to_id_map(db)
    proj_id = project_name_to_id.get(project)
    if proj_id is not None and should_limit_to_self_for_project(
        current_user, proj_id, db
    ) and not is_self_identity(current_user, author):
        raise HTTPException(status_code=403, detail="您没有权限查看他人的报告")

    if literal_relative_path is not None:
        relative_path = literal_relative_path
    else:
        safe_project = _sanitize_filename(project)
        safe_author = _sanitize_filename(author)
        relative_path = f"{safe_project}/{report_type}/{report_date}/{safe_author}.md"

    full_path = _validate_path(relative_path)

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    try:
        content = full_path.read_text(encoding="utf-8")
        return ApiResponse(
            success=True,
            data={
                "content": content,
                "path": relative_path,
                "size": full_path.stat().st_size,
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read report: {str(e)}")
```

- [ ] **Step 4: Run the focused report test**

Run:

```powershell
python -m pytest tests/test_api/test_project_admin_report_permissions.py::test_content_path_reads_literal_filename_with_spaces -q
```

Expected: PASS.

- [ ] **Step 5: Run the report permission tests**

Run:

```powershell
python -m pytest tests/test_api/test_project_admin_report_permissions.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Run:

```powershell
git add app/api/reports.py tests/test_api/test_project_admin_report_permissions.py
git commit -m "fix: read report content from literal path"
```

---

### Task 2: Add Daily Report Metadata to Efficiency Detail

**Files:**
- Modify: `app/api/efficiency.py`
- Modify: `tests/test_api/test_efficiency.py`

- [ ] **Step 1: Add imports and test helper**

In `tests/test_api/test_efficiency.py`, add these imports near the top:

```python
from pathlib import Path

from app.api import reports as reports_api
```

Add this helper below `_seed`:

```python
def _write_daily_report(base_dir, project, stat_date, filename, content="report"):
    report_dir = Path(base_dir) / project / "daily" / stat_date.isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / filename).write_text(content, encoding="utf-8")
```

- [ ] **Step 2: Write failing tests for daily report discovery**

Append these tests to `tests/test_api/test_efficiency.py` before the range aggregation section:

```python
def test_detail_returns_daily_reports_by_project_and_author_name(
    client, db_session, login_as_admin, tmp_path, monkeypatch
):
    """系统管理员能看到按项目区分的日报入口，filename 使用相对路径语义。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", tmp_path)
    d = date(2026, 6, 10)
    db_session.add_all([
        Project(name="project-a", project_id=201, is_active=True),
        Project(name="project-b", project_id=202, is_active=True),
    ])
    db_session.commit()
    _seed(db_session, "alice@example.com", "Alice", d, projects=["project-a", "project-b"])
    _write_daily_report(tmp_path, "project-a", d, "Alice.md", "a report")
    _write_daily_report(tmp_path, "project-b", d, "alice.md", "b report")

    resp = client.get(
        "/api/efficiency/detail",
        params={"email": "alice@example.com", "date": d.isoformat()},
    )

    assert resp.status_code == 200
    reports = resp.json()["data"]["daily_reports"]
    assert [item["project"] for item in reports] == ["project-a", "project-b"]
    assert reports[0]["author"] == "Alice"
    assert reports[0]["filename"] == "project-a/daily/2026-06-10/Alice.md"
    assert reports[1]["filename"] == "project-b/daily/2026-06-10/alice.md"


def test_detail_daily_reports_empty_when_summary_missing(
    client, login_as_admin, tmp_path, monkeypatch
):
    """没有 summary 时仍返回 daily_reports 空数组，避免前端判断缺字段。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", tmp_path)
    d = date(2026, 6, 10)

    resp = client.get(
        "/api/efficiency/detail",
        params={"email": "missing@example.com", "date": d.isoformat()},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"] is None
    assert data["daily_reports"] == []


def test_detail_daily_reports_match_full_email_but_not_email_prefix(
    client, db_session, login_as_admin, tmp_path, monkeypatch
):
    """邮箱完整值可匹配，邮箱前缀不能匹配，避免误关联。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", tmp_path)
    d = date(2026, 6, 10)
    db_session.add(Project(name="project-a", project_id=203, is_active=True))
    db_session.commit()
    _seed(db_session, "zhang@example.com", "Display Name", d, projects=["project-a"])
    _write_daily_report(tmp_path, "project-a", d, "zhang@example.com.md", "email report")
    _write_daily_report(tmp_path, "project-a", d, "zhang.md", "prefix report")

    resp = client.get(
        "/api/efficiency/detail",
        params={"email": "zhang@example.com", "date": d.isoformat()},
    )

    assert resp.status_code == 200
    reports = resp.json()["data"]["daily_reports"]
    assert len(reports) == 1
    assert reports[0]["author"] == "zhang@example.com"
    assert reports[0]["filename"] == "project-a/daily/2026-06-10/zhang@example.com.md"


def test_detail_daily_reports_hidden_when_content_would_forbid_self_identity(
    client, db_session, app, member_user, tmp_path, monkeypatch
):
    """普通用户账号身份不匹配报告 stem 时，不展示点开会 403 的日报入口。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", tmp_path)
    d = date(2026, 6, 10)
    project = Project(name="project-a", project_id=204, is_active=True)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    db_session.execute(
        project_members.insert().values(project_id=project.id, user_id=member_user.id)
    )
    db_session.commit()
    _seed(db_session, member_user.email, "Zhang Peng", d, projects=["project-a"])
    _write_daily_report(tmp_path, "project-a", d, "Zhang Peng.md", "self mismatch")

    app.dependency_overrides[get_current_user_full] = lambda: member_user
    resp = client.get(
        "/api/efficiency/detail",
        params={"email": member_user.email, "date": d.isoformat()},
    )
    app.dependency_overrides.pop(get_current_user_full, None)

    assert resp.status_code == 200
    assert resp.json()["data"]["daily_reports"] == []
```

- [ ] **Step 3: Run daily report tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_api/test_efficiency.py -q
```

Expected: FAIL because `daily_reports` does not exist in the `/detail` response.

- [ ] **Step 4: Add report-discovery helpers to `app/api/efficiency.py`**

Add these imports near the top of `app/api/efficiency.py`:

```python
from pathlib import Path
from app.api import reports as reports_api
from app.core.permissions import is_self_identity, should_limit_to_self_for_project
```

Keep the existing `from app.core.permissions import can_view_person_detail, can_view_person_detail_for_project` line, then merge imports so `app.core.permissions` is imported once:

```python
from app.core.permissions import (
    can_view_person_detail,
    can_view_person_detail_for_project,
    is_self_identity,
    should_limit_to_self_for_project,
)
```

Add these helpers above `/detail`:

```python
def _report_match_candidates(summary: EmployeeEfficiencyDaily) -> set:
    """返回日报文件 stem 的忽略大小写精确匹配候选值。"""
    return {
        value.strip().lower()
        for value in (summary.author_name, summary.author_email)
        if value and value.strip()
    }


def _is_report_visible_to_current_user(
    current_user: User,
    project_name: str,
    report_author: str,
    db: Session,
    allowed_project_names: set,
    project_name_to_id: dict,
) -> bool:
    """与 /api/reports/content 保持一致的日报入口可见性判断。"""
    if project_name not in allowed_project_names:
        return False

    proj_id = project_name_to_id.get(project_name)
    if proj_id is not None and should_limit_to_self_for_project(
        current_user, proj_id, db
    ) and not is_self_identity(current_user, report_author):
        return False

    return True


def _build_daily_reports_for_summary(
    summary: Optional[EmployeeEfficiencyDaily],
    current_user: User,
    db: Session,
) -> list:
    """查找 summary 对应日期和项目下当前用户可打开的日报文件。"""
    if summary is None:
        return []

    try:
        projects = json.loads(summary.projects_involved or "[]")
    except (TypeError, ValueError):
        projects = []

    if not projects:
        return []

    candidates = _report_match_candidates(summary)
    if not candidates:
        return []

    allowed_project_names = reports_api._get_user_allowed_project_names(
        current_user, db
    )
    project_name_to_id = reports_api._get_project_name_to_id_map(db)
    report_date = summary.stat_date.isoformat()
    daily_reports = []

    for project_name in sorted(set(projects)):
        date_dir = (
            reports_api.ALLOWED_REPORT_DIR
            / project_name
            / "daily"
            / report_date
        )
        if not date_dir.exists() or not date_dir.is_dir():
            continue

        for report_file in sorted(date_dir.glob("*.md")):
            report_author = report_file.stem
            if report_author.strip().lower() not in candidates:
                continue
            if not _is_report_visible_to_current_user(
                current_user,
                project_name,
                report_author,
                db,
                allowed_project_names,
                project_name_to_id,
            ):
                continue

            relative_path = report_file.relative_to(
                reports_api.ALLOWED_REPORT_DIR
            ).as_posix()
            daily_reports.append({
                "project": project_name,
                "type": "daily",
                "date": report_date,
                "author": report_author,
                "filename": relative_path,
                "size": report_file.stat().st_size,
            })

    return daily_reports
```

- [ ] **Step 5: Add `daily_reports` to single-day detail response**

In `get_detail`, after `commits_data` is built, add:

```python
    daily_reports = _build_daily_reports_for_summary(
        summary, current_user, db
    )
```

Then return:

```python
    return ApiResponse(
        success=True,
        data={
            "summary": _serialize(summary) if summary else None,
            "trend": trend_data,
            "commits": commits_data,
            "daily_reports": daily_reports,
        },
    )
```

- [ ] **Step 6: Run focused efficiency tests**

Run:

```powershell
python -m pytest tests/test_api/test_efficiency.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

Run:

```powershell
git add app/api/efficiency.py tests/test_api/test_efficiency.py
git commit -m "feat: include daily reports in efficiency detail"
```

---

### Task 3: Add Daily Report Modal and Remove Visible Commit List

**Files:**
- Modify: `app/templates/efficiency.html`
- Modify: `app/static/js/efficiency.js`
- Create: `tests/test_templates/test_efficiency_daily_report_ui.py`

- [ ] **Step 1: Write static UI tests**

Create `tests/test_templates/test_efficiency_daily_report_ui.py`:

```python
from pathlib import Path


def test_efficiency_template_defines_daily_report_modal():
    """人员详情页应有独立的日报详情弹窗。"""
    template = Path("app/templates/efficiency.html").read_text(encoding="utf-8")

    assert 'id="dailyReportModal"' in template
    assert 'id="dailyReportModalTitle"' in template
    assert 'id="dailyReportModalBody"' in template
    assert 'id="dailyReportModalClose"' in template


def test_efficiency_js_renders_daily_reports_before_trend_and_removes_commits():
    """抽屉中日报入口应位于近 7 天趋势上方，不再展示今日提交区块。"""
    script = Path("app/static/js/efficiency.js").read_text(encoding="utf-8")

    assert "function renderDailyReports" in script
    assert "function openDailyReportModal" in script
    assert "当前人员审查报告-日报" in script
    assert script.index("当前人员审查报告-日报") < script.index("近 7 天趋势")
    assert "今日提交" not in script
```

- [ ] **Step 2: Run UI tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_templates/test_efficiency_daily_report_ui.py -q
```

Expected: FAIL because modal and JS functions do not exist yet, and “今日提交” is still rendered.

- [ ] **Step 3: Add modal markup to `app/templates/efficiency.html`**

Insert this block between the recompute modal and the detail drawer:

```html
<!-- 日报详情弹窗 -->
<div class="modal-overlay" id="dailyReportModal">
    <div class="modal-content" style="max-width: 900px;">
        <div class="modal-header">
            <h5 style="margin:0; font-weight:600;" id="dailyReportModalTitle">日报详情</h5>
            <button class="btn-close-modal" id="dailyReportModalClose">&times;</button>
        </div>
        <div class="modal-body" id="dailyReportModalBody">
            <div style="color: var(--color-slate-500);">加载中...</div>
        </div>
    </div>
</div>
```

In the page `<style>` block, add:

```css
    .daily-report-list { display: flex; flex-direction: column; gap: var(--space-2); }
    .daily-report-item { display: flex; align-items: center; justify-content: space-between;
                         width: 100%; text-align: left; }
    .daily-report-item span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 4: Add report rendering and modal functions to `app/static/js/efficiency.js`**

Add these functions before `renderDrawer`:

```javascript
    function renderDailyReports(reports) {
        if (!reports || !reports.length) {
            return '<div class="text-muted small">无日报</div>';
        }

        var buttons = reports.map(function (r, idx) {
            return '<button type="button" class="btn btn-sm btn-secondary daily-report-item" data-report-index="' + idx + '">' +
                '<span><i class="bi bi-file-earmark-text"></i> ' + escapeHtml(r.project || '-') + '</span>' +
                '<span class="text-muted small">' + escapeHtml(r.date || '') + '</span>' +
                '</button>';
        }).join('');

        return '<div class="daily-report-list">' + buttons + '</div>';
    }

    function bindDailyReportLinks(reports) {
        document.querySelectorAll('.daily-report-item[data-report-index]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var idx = Number(btn.dataset.reportIndex);
                if (!Number.isNaN(idx) && reports[idx]) {
                    openDailyReportModal(reports[idx]);
                }
            });
        });
    }

    function openDailyReportModal(report) {
        var modal = document.getElementById('dailyReportModal');
        var title = document.getElementById('dailyReportModalTitle');
        var body = document.getElementById('dailyReportModalBody');
        modal.classList.add('active');
        title.textContent = (report.project || '-') + ' / ' + (report.date || '-') + ' / ' + (report.author || '-');
        body.innerHTML = '<div class="text-muted">加载中...</div>';

        apiRequest('/api/reports/content?path=' + encodeURIComponent(report.filename || ''))
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    body.innerHTML = '<div class="text-danger">日报加载失败</div>';
                    return null;
                }
                return resp.json();
            })
            .then(function (json) {
                if (!json) return;
                var content = json.data && json.data.content ? json.data.content : '';
                body.innerHTML = '<div class="markdown-body">' + renderMarkdown(content || '无内容') + '</div>';
            })
            .catch(function () {
                body.innerHTML = '<div class="text-danger">日报加载失败</div>';
            });
    }

    function closeDailyReportModal() {
        document.getElementById('dailyReportModal').classList.remove('active');
    }
```

- [ ] **Step 5: Update `renderDrawer`**

Replace the start of `renderDrawer`:

```javascript
        var s = data.summary || {};
        var work = s.work_summary || [];
        var commits = data.commits || [];
        var trend = data.trend || [];
```

with:

```javascript
        var s = data.summary || {};
        var work = s.work_summary || [];
        var trend = data.trend || [];
        var dailyReports = data.daily_reports || [];
```

Delete the `commitHtml` block completely.

Replace the HTML composition after “今日主要工作” with this order:

```javascript
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">当前人员审查报告-日报</div>' +
            '  ' + renderDailyReports(dailyReports) +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">近 7 天趋势</div>' +
            '  <div id="chartTrend"></div>' +
            '</div>';
```

After `document.getElementById('drawerBody').innerHTML = html;`, call:

```javascript
        bindDailyReportLinks(dailyReports);
```

Keep `renderTrendChart(trend);` after binding.

- [ ] **Step 6: Bind modal close events in DOMContentLoaded**

Inside the existing `DOMContentLoaded` callback in `app/static/js/efficiency.js`, after monthly modal close binding, add:

```javascript
        document.getElementById('dailyReportModalClose').addEventListener('click', closeDailyReportModal);
        document.getElementById('dailyReportModal').addEventListener('click', function (e) {
            if (e.target === this) closeDailyReportModal();
        });
```

- [ ] **Step 7: Run static UI tests**

Run:

```powershell
python -m pytest tests/test_templates/test_efficiency_daily_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 3**

Run:

```powershell
git add app/templates/efficiency.html app/static/js/efficiency.js tests/test_templates/test_efficiency_daily_report_ui.py
git commit -m "feat: show efficiency daily report modal"
```

---

### Task 4: Full Verification and Regression Sweep

**Files:**
- No new files.
- Verify modified tests and related API/template tests.

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests/test_api/test_project_admin_report_permissions.py tests/test_api/test_efficiency.py tests/test_templates/test_efficiency_daily_report_ui.py -q
```

Expected: PASS.

- [ ] **Step 2: Run nearby report and template tests**

Run:

```powershell
python -m pytest tests/test_api/test_personal_visibility_permissions.py tests/test_templates -q
```

Expected: PASS.

- [ ] **Step 3: Inspect final diff**

Run:

```powershell
git diff --stat HEAD
git diff HEAD -- app/api/reports.py app/api/efficiency.py app/static/js/efficiency.js app/templates/efficiency.html
```

Expected: Diff contains only the report path-mode fix, efficiency `daily_reports` response, report modal, and removal of visible “今日提交” rendering.

- [ ] **Step 4: Commit verification fixes if any were needed**

If Step 1 or Step 2 required a fix, commit the exact files changed:

```powershell
git add app/api/reports.py app/api/efficiency.py app/static/js/efficiency.js app/templates/efficiency.html tests/test_api/test_project_admin_report_permissions.py tests/test_api/test_efficiency.py tests/test_templates/test_efficiency_daily_report_ui.py
git commit -m "test: cover efficiency daily report detail"
```

If no fixes were needed after Task 3, skip this commit.

---

## Self-Review

- Spec coverage:
  - Report entries above trend: Task 3.
  - Project-separated `daily_reports` with project name: Task 2.
  - New modal for report details: Task 3.
  - Remove visible today-commit list while preserving `commits`: Task 3 and Task 2.
  - Path-mode report content compatibility for spaces: Task 1.
  - Permission symmetry with `/api/reports/content`: Task 2.
  - Exact stem matching and no email prefix fallback: Task 2.

- Placeholder scan:
  - The plan contains no `TBD`, no unspecified error handling, and each code-changing step includes exact code.

- Type consistency:
  - API field is `daily_reports`.
  - Report relative path field is `filename`, matching `/api/reports`.
  - Frontend calls `/api/reports/content?path=${encodeURIComponent(report.filename)}`.

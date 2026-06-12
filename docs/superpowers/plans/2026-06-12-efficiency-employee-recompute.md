# 人员能效补算 — 指定人员重算 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Git 提交说明**：依据本项目 engineer-professional 规范（未经主动要求不计划/执行 git 操作），本计划**不含 git 提交步骤**。每个任务以"运行测试通过"收尾，提交时机由用户决定。

**Goal:** 在现有"立即补算"功能中增加按邮箱指定人员重算的能力，按天与按月均支持，仅重算指定人员、不影响其他人记录。

**Architecture:** 采用方案 A——在两个聚合器 `aggregate()` 方法新增可选参数 `only_emails`（默认 None=全员，向后兼容），复用现有补算端点/后台线程/进度轮询/锁机制。邮箱大小写不敏感匹配（与现有 `excluded_emails` 约定一致）。force 复选框继续控制覆盖：not force 只补缺失人员，force 覆盖指定人员。

**Tech Stack:** Python / FastAPI / SQLAlchemy / pytest（后端测试）；原生 JS + Jinja2 模板（前端，无自动化测试设施，手动验证）。

**设计文档:** `docs/superpowers/specs/2026-06-12-efficiency-employee-recompute-design.md`

---

## 文件结构（改动映射）

| 文件 | 职责/改动 |
|------|-----------|
| `app/services/efficiency_aggregator.py` | `aggregate` 加 `only_emails` 参数；写入循环按小写邮箱过滤 |
| `app/services/efficiency_monthly_aggregator.py` | `aggregate` 加 `only_emails` 参数；daily 查询用 `func.lower(...).in_()` 过滤；顶部加 `func` 导入 |
| `app/api/efficiency.py` | 新增 `_normalize_emails`、`_existing_daily_emails`、`_existing_monthly_emails` 函数；两个请求体加 `emails`；`_recompute_task`/`_reset_recompute_state` 加 `target_emails`；两端点解析邮箱+按人员跳过+透传；两个 `_run_*` 线程加 `only_emails` 参数与编排 |
| `app/templates/efficiency.html` | `#recomputeModal` 新增邮箱 `<textarea>` |
| `app/static/js/efficiency.js` | `parseEmailsInput` 解析；`openRecomputeModal` 清空；`confirmRecompute` 加 `emails`；完成通知区分全员/指定人员 |
| 测试文件 | 见各任务 |

约定（全计划统一，避免签名漂移）：
- 聚合器方法签名：`aggregate(self, target_date, only_emails: Optional[Set[str]] = None)` / `aggregate(self, year_month, only_emails: Optional[Set[str]] = None)`
- `only_emails` 永远是**小写**字符串集合或 `None`
- `_normalize_emails(emails: Optional[list]) -> list[str]`：strip + 小写 + 去空 + 去重，保序
- `_existing_daily_emails(db, stat_date, emails: set) -> set` / `_existing_monthly_emails(db, year_month, emails: set) -> set`：返回小写集合
- 请求体字段：`emails: Optional[list[str]] = None`
- 线程签名：`_run_daily_recompute(start, end, force, only_emails=None)` / `_run_monthly_recompute(year_month, force, only_emails=None)`
- 状态字段：`_recompute_task["target_emails"]`（list，空=全员）

---

## Task 1: 按天聚合器 `aggregate` 支持 `only_emails`

**Files:**
- Modify: `app/services/efficiency_aggregator.py`（`aggregate` 方法，约 56-90 行）
- Test: `tests/test_services/test_efficiency_aggregator.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_services/test_efficiency_aggregator.py` 末尾追加：

```python
def test_aggregate_only_emails_filters_others(
    db_session, gitlab_client_factory, llm_mock
):
    """only_emails 指定单人时，仅写入该人，其他作者被跳过"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {
        "main": [
            _make_commit("sha-a", "alice@b.com", "Alice"),
            _make_commit("sha-c", "carol@b.com", "Carol"),
        ],
    }
    diffs = {
        "sha-a": [{"diff": "+a", "new_path": "x.py", "old_path": "x.py",
                   "new_file": False, "deleted_file": False, "renamed_file": False}],
        "sha-c": [{"diff": "+c", "new_path": "y.py", "old_path": "y.py",
                   "new_file": False, "deleted_file": False, "renamed_file": False}],
    }
    client = gitlab_client_factory(commits, diffs)

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda p: client,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27), only_emails={"alice@b.com"})

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    assert rows[0].author_email == "alice@b.com"


def test_aggregate_only_emails_case_insensitive(
    db_session, gitlab_client_factory, llm_mock
):
    """only_emails 为小写，commit 邮箱含大写时仍能匹配"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {"main": [_make_commit("sha-a", "Alice@B.com", "Alice")]}
    diffs = {"sha-a": [{"diff": "+a", "new_path": "x.py", "old_path": "x.py",
                        "new_file": False, "deleted_file": False, "renamed_file": False}]}
    client = gitlab_client_factory(commits, diffs)

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda p: client,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27), only_emails={"alice@b.com"})

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    assert rows[0].author_email == "Alice@B.com"  # 原始大小写写入


def test_aggregate_only_emails_none_writes_all(
    db_session, gitlab_client_factory, llm_mock
):
    """only_emails=None（默认）时写入全部作者（向后兼容回归）"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {"main": [
        _make_commit("sha-a", "alice@b.com", "Alice"),
        _make_commit("sha-c", "carol@b.com", "Carol"),
    ]}
    diffs = {
        "sha-a": [{"diff": "+a", "new_path": "x.py", "old_path": "x.py",
                   "new_file": False, "deleted_file": False, "renamed_file": False}],
        "sha-c": [{"diff": "+c", "new_path": "y.py", "old_path": "y.py",
                   "new_file": False, "deleted_file": False, "renamed_file": False}],
    }
    client = gitlab_client_factory(commits, diffs)

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda p: client,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27))

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 2
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_services/test_efficiency_aggregator.py::test_aggregate_only_emails_filters_others -v`
Expected: FAIL，报 `aggregate() got an unexpected keyword argument 'only_emails'`

- [ ] **Step 3: 实现 — 修改 `aggregate` 签名与写入循环**

在 `app/services/efficiency_aggregator.py`，将 `aggregate` 方法签名（第 56 行）改为：

```python
    def aggregate(self, target_date: date, only_emails: Optional[Set[str]] = None) -> Dict[str, Any]:
        """对指定日期做一次聚合（幂等，重复调用会 UPSERT）

        Args:
            target_date: 目标日期
            only_emails: 若提供（小写邮箱集合），仅写入这些作者；None 表示全员
        """
```

将写入循环（原第 73-81 行）改为：

```python
        success = 0
        failed = 0
        for email, data in per_author.items():
            if only_emails is not None and email.lower() not in only_emails:
                continue
            try:
                self._upsert_author(email, data, target_date)
                success += 1
            except Exception as e:
                logger.exception(f"写入 {email} 能效记录失败: {e}")
                failed += 1
```

（`Optional`、`Set` 已在文件顶部 `from typing import Callable, Dict, Optional, Set, Any` 导入，无需新增。）

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_services/test_efficiency_aggregator.py -v`
Expected: PASS（新增 3 个 + 原有用例全绿）

---

## Task 2: 按月聚合器 `aggregate` 支持 `only_emails`

**Files:**
- Modify: `app/services/efficiency_monthly_aggregator.py`（顶部导入 + `aggregate` 方法 61-96 行）
- Test: `tests/test_services/test_efficiency_monthly_aggregator.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_services/test_efficiency_monthly_aggregator.py` 末尾追加：

```python
def test_aggregate_only_emails_filters(session, llm_mock):
    """only_emails 指定单人时，仅聚合该人"""
    _seed_daily(session, "alice@b.com", "Alice", date(2026, 5, 1), score=80)
    _seed_daily(session, "carol@b.com", "Carol", date(2026, 5, 1), score=70)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
        llm_interval=0,
    )
    result = agg.aggregate("2026-05", only_emails={"alice@b.com"})

    assert result["authors_total"] == 1
    rows = session.query(EmployeeEfficiencyMonthly).all()
    assert len(rows) == 1
    assert rows[0].author_email == "alice@b.com"


def test_aggregate_only_emails_case_insensitive(session, llm_mock):
    """only_emails 为小写，daily 表邮箱含大写时仍匹配"""
    _seed_daily(session, "Alice@B.com", "Alice", date(2026, 5, 1), score=80)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
        llm_interval=0,
    )
    result = agg.aggregate("2026-05", only_emails={"alice@b.com"})

    assert result["authors_total"] == 1
    rows = session.query(EmployeeEfficiencyMonthly).all()
    assert len(rows) == 1


def test_aggregate_only_emails_none_writes_all(session, llm_mock):
    """only_emails=None（默认）聚合全部作者（向后兼容回归）"""
    _seed_daily(session, "alice@b.com", "Alice", date(2026, 5, 1), score=80)
    _seed_daily(session, "carol@b.com", "Carol", date(2026, 5, 1), score=70)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
        llm_interval=0,
    )
    result = agg.aggregate("2026-05")

    assert result["authors_total"] == 2
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_services/test_efficiency_monthly_aggregator.py::test_aggregate_only_emails_filters -v`
Expected: FAIL，报 `aggregate() got an unexpected keyword argument 'only_emails'`

- [ ] **Step 3: 实现 — 导入 `func` + 修改签名与查询**

在 `app/services/efficiency_monthly_aggregator.py` 顶部导入区（第 15 行 `from sqlalchemy.orm import Session` 之后）新增：

```python
from sqlalchemy import func
```

将 `from typing import Any, Dict, List, Optional`（第 12 行）改为：

```python
from typing import Any, Dict, List, Optional, Set
```

将 `aggregate` 方法签名（第 61 行）改为：

```python
    def aggregate(self, year_month: str, only_emails: Optional[Set[str]] = None) -> Dict[str, Any]:
```

在 daily 查询构造之后、`daily_rows = query.all()`（原第 87 行）之前，紧接 `excluded_emails` 过滤块（原第 82-85 行）后新增过滤：

```python
        # 指定人员过滤（大小写不敏感）
        if only_emails:
            query = query.filter(
                func.lower(EmployeeEfficiencyDaily.author_email).in_(list(only_emails))
            )
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_services/test_efficiency_monthly_aggregator.py -v`
Expected: PASS（新增 3 个 + 原有用例全绿）

---

## Task 3: `_normalize_emails` 邮箱规范化函数 + 请求体加 `emails`

**Files:**
- Modify: `app/api/efficiency.py`（新增函数；`RecomputeRequest` 864 行、`MonthlyRecomputeRequest` 1134 行）
- Test: `tests/test_api/test_efficiency.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api/test_efficiency.py` 末尾追加：

```python
def test_normalize_emails_strip_lower_dedup():
    """规范化：去空白、转小写、去重、去空，保序"""
    from app.api.efficiency import _normalize_emails
    assert _normalize_emails([" Alice@B.com ", "carol@b.com", "ALICE@b.com", ""]) \
        == ["alice@b.com", "carol@b.com"]


def test_normalize_emails_none_and_empty():
    """None 或空列表返回空列表"""
    from app.api.efficiency import _normalize_emails
    assert _normalize_emails(None) == []
    assert _normalize_emails([]) == []
    assert _normalize_emails(["  ", ""]) == []
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_api/test_efficiency.py::test_normalize_emails_strip_lower_dedup -v`
Expected: FAIL，报 `cannot import name '_normalize_emails'`

- [ ] **Step 3: 实现 — 新增 `_normalize_emails` 函数 + 请求体字段**

在 `app/api/efficiency.py` 的 `_apply_excluded_emails_filter` 函数之后（约第 138 行后）新增：

```python
def _normalize_emails(emails: Optional[list]) -> list:
    """规范化邮箱列表：strip + 小写 + 去空 + 去重（保序）。

    与现有 excluded_emails 的大小写不敏感约定保持一致。
    """
    if not emails:
        return []
    seen = set()
    result = []
    for e in emails:
        if not e:
            continue
        norm = e.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result
```

将 `RecomputeRequest`（第 864-867 行）改为：

```python
class RecomputeRequest(BaseModel):
    start_date: str
    end_date: str
    force: bool = False
    emails: Optional[list] = None
```

将 `MonthlyRecomputeRequest`（第 1134-1136 行）改为：

```python
class MonthlyRecomputeRequest(BaseModel):
    year_month: str
    force: bool = False
    emails: Optional[list] = None
```

（`Optional` 已在第 14 行 `from typing import Optional` 导入。）

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_api/test_efficiency.py::test_normalize_emails_strip_lower_dedup tests/test_api/test_efficiency.py::test_normalize_emails_none_and_empty -v`
Expected: PASS

---

## Task 4: 已有记录查询 helper `_existing_daily_emails` / `_existing_monthly_emails`

**Files:**
- Modify: `app/api/efficiency.py`（新增两个函数，紧接 `_normalize_emails` 之后）
- Test: `tests/test_api/test_efficiency.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api/test_efficiency.py` 末尾追加（复用文件内已有的 `_seed` helper 与 `db_session` fixture）：

```python
def test_existing_daily_emails_case_insensitive(db_session):
    """返回指定邮箱中当天已有 daily 记录的集合（小写）"""
    from app.api.efficiency import _existing_daily_emails
    d = date(2026, 5, 27)
    _seed(db_session, "Alice@B.com", "Alice", d)
    _seed(db_session, "carol@b.com", "Carol", d)

    result = _existing_daily_emails(
        db_session, d, {"alice@b.com", "dave@b.com"}
    )
    assert result == {"alice@b.com"}


def test_existing_daily_emails_empty_input(db_session):
    """空输入返回空集合，不查询"""
    from app.api.efficiency import _existing_daily_emails
    assert _existing_daily_emails(db_session, date(2026, 5, 27), set()) == set()


def test_existing_monthly_emails_case_insensitive(db_session):
    """返回指定邮箱中该月已有 monthly 记录的集合（小写）"""
    from app.api.efficiency import _existing_monthly_emails
    from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
    db_session.add(EmployeeEfficiencyMonthly(
        author_email="Alice@B.com", author_name="Alice", year_month="2026-05",
        commits_count=3, additions=100, deletions=20, files_changed=5,
        new_files=0, deleted_files=0, active_days=2,
        projects_involved=json.dumps(["proj-a"]),
        review_score=85, review_grade="良好", review_summary="ok",
        work_summary=json.dumps(["A"]), summary_top_n=5, llm_status="success",
    ))
    db_session.commit()

    result = _existing_monthly_emails(
        db_session, "2026-05", {"alice@b.com", "dave@b.com"}
    )
    assert result == {"alice@b.com"}
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_api/test_efficiency.py::test_existing_daily_emails_case_insensitive -v`
Expected: FAIL，报 `cannot import name '_existing_daily_emails'`

- [ ] **Step 3: 实现 — 新增两个查询 helper**

在 `app/api/efficiency.py` 的 `_normalize_emails` 函数之后新增：

```python
def _existing_daily_emails(db: Session, stat_date, emails: set) -> set:
    """返回 emails 中在 stat_date 已有 daily 记录的邮箱（小写集合）"""
    if not emails:
        return set()
    rows = (
        db.query(EmployeeEfficiencyDaily.author_email)
        .filter(
            func.lower(EmployeeEfficiencyDaily.author_email).in_(list(emails)),
            EmployeeEfficiencyDaily.stat_date == stat_date,
        )
        .all()
    )
    return {r[0].lower() for r in rows}


def _existing_monthly_emails(db: Session, year_month: str, emails: set) -> set:
    """返回 emails 中在 year_month 已有 monthly 记录的邮箱（小写集合）"""
    if not emails:
        return set()
    rows = (
        db.query(EmployeeEfficiencyMonthly.author_email)
        .filter(
            func.lower(EmployeeEfficiencyMonthly.author_email).in_(list(emails)),
            EmployeeEfficiencyMonthly.year_month == year_month,
        )
        .all()
    )
    return {r[0].lower() for r in rows}
```

（`func` 已在第 19 行导入；`EmployeeEfficiencyDaily`/`EmployeeEfficiencyMonthly`/`Session` 已导入。）

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_api/test_efficiency.py -k existing_ -v`
Expected: PASS（3 个用例）

---

## Task 5: 状态字段 `target_emails` + 按天 `recompute` 端点 + `_run_daily_recompute`

**Files:**
- Modify: `app/api/efficiency.py`（`_recompute_task` 46 行、`_reset_recompute_state` 65 行、`recompute` 端点 870 行、`_run_daily_recompute` 140 行）
- Test: `tests/test_api/test_efficiency.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api/test_efficiency.py` 末尾追加：

```python
def test_recompute_accepts_emails_and_sets_target(
    client, db_session, login_as_admin, monkeypatch
):
    """按天补算接受 emails，规范化后写入任务状态 target_emails，并透传给线程"""
    import app.api.efficiency as eff

    captured = {}

    def _fake_thread(target=None, args=(), daemon=None):
        captured["target"] = target
        captured["args"] = args

        class _T:
            def start(self_inner):
                captured["started"] = True
        return _T()

    monkeypatch.setattr(eff.threading, "Thread", _fake_thread)

    today = date.today().isoformat()
    resp = client.post(
        "/api/efficiency/recompute",
        json={"start_date": today, "end_date": today,
              "force": True, "emails": [" Alice@B.com ", "alice@b.com"]},
    )
    assert resp.status_code == 200
    # 任务状态记录规范化后的 target_emails
    assert eff._recompute_task["target_emails"] == ["alice@b.com"]
    # 透传给线程的 only_emails 为小写集合
    # args = (s, e, force, only_emails)
    assert captured["args"][3] == {"alice@b.com"}
    # 还原 is_running，避免污染后续用例
    eff._recompute_task["is_running"] = False


def test_run_daily_recompute_skips_existing_when_not_force(
    db_session, monkeypatch
):
    """指定人员 + not force：当天已有记录的人被跳过，缺失的人用 remaining 调 aggregate"""
    import app.api.efficiency as eff
    from app.models import Settings

    d = date(2026, 5, 27)
    # alice 已有当天记录，bob 没有
    _seed(db_session, "alice@b.com", "Alice", d)
    settings = Settings(global_gitlab_url="http://gl", global_gitlab_token=None)
    db_session.add(settings)
    db_session.commit()

    monkeypatch.setattr(eff, "SessionLocal", lambda: db_session)

    agg_instance = MagicMock()
    agg_instance.aggregate.return_value = {
        "authors_total": 1, "authors_success": 1, "authors_failed": 0,
    }
    with patch("app.services.efficiency_aggregator.EfficiencyAggregator",
               return_value=agg_instance):
        eff._reset_recompute_state()
        eff._recompute_task["is_running"] = True
        eff._run_daily_recompute(
            d, d, force=False, only_emails={"alice@b.com", "bob@b.com"}
        )

    # not force：alice 已存在被剔除，只用 {"bob@b.com"} 调 aggregate
    agg_instance.aggregate.assert_called_once_with(d, only_emails={"bob@b.com"})
```

> 注：`MagicMock` 与 `patch` 已在文件其它测试用到，需确认 `test_efficiency.py` 顶部已 `from unittest.mock import ...`。若没有，在导入区添加 `from unittest.mock import MagicMock, patch`（见 Step 3 末尾提示）。

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_api/test_efficiency.py::test_recompute_accepts_emails_and_sets_target -v`
Expected: FAIL（`target_emails` 不存在 / 线程 args 只有 3 个元素）

- [ ] **Step 3: 实现**

**(a) 状态字典加字段** — `_recompute_task`（第 46-62 行）在 `"error": None,` 之前加 `"target_emails": [],`：

```python
_recompute_task = {
    "is_running": False,
    "task_type": None,        # "daily" | "monthly"
    "start_date": None,
    "end_date": None,
    "year_month": None,
    "total_days": 0,
    "processed_days": 0,
    "skipped_days": 0,
    "failed_days": 0,
    "current_date": None,     # 当前正在处理的日期
    "processed": [],
    "skipped": [],
    "failed": [],
    "cancelled": False,
    "target_emails": [],      # 指定人员邮箱（空=全员）
    "error": None,
}
```

`_reset_recompute_state`（第 65-83 行）同样在 update 字典里加 `"target_emails": [],`。

**(b) `recompute` 端点**（第 870-925 行）改造——解析 emails、写 target_emails、透传：

```python
@router.post("/recompute")
async def recompute(
    body: RecomputeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """管理员手动补算指定日期范围的人员能效数据（异步执行）"""
    logger.info(f"收到补算请求: start_date={body.start_date}, end_date={body.end_date}, "
                f"force={body.force}, emails={body.emails}")

    if not current_user.is_system_admin():
        raise HTTPException(403, "仅系统管理员可补算")

    try:
        s = date.fromisoformat(body.start_date)
        e = date.fromisoformat(body.end_date)
    except ValueError as ex:
        logger.error(f"日期格式错误: {ex}")
        raise HTTPException(400, f"日期格式错误: {ex}")

    if s > e:
        logger.error(f"开始日期 {s} 晚于结束日期 {e}")
        raise HTTPException(400, "开始日期不能晚于结束日期")

    normalized = _normalize_emails(body.emails)
    only_emails = set(normalized) if normalized else None

    with _recompute_lock:
        if _recompute_task["is_running"]:
            raise HTTPException(409, "补算任务正在执行中，请稍后再试")
        _recompute_task.update({
            "is_running": True,
            "task_type": "daily",
            "start_date": body.start_date,
            "end_date": body.end_date,
            "year_month": None,
            "total_days": (e - s).days + 1,
            "processed_days": 0,
            "skipped_days": 0,
            "failed_days": 0,
            "current_date": None,
            "processed": [],
            "skipped": [],
            "failed": [],
            "cancelled": False,
            "target_emails": normalized,
            "error": None,
        })

    t = threading.Thread(
        target=_run_daily_recompute,
        args=(s, e, body.force, only_emails),
        daemon=True,
    )
    t.start()

    return ApiResponse(
        success=True,
        message="补算任务已启动，请在页面查看进度",
        data={"task_type": "daily", "total_days": (e - s).days + 1,
              "target_emails": normalized},
    )
```

**(c) `_run_daily_recompute`**（第 140 行）改签名并改写循环内跳过/调用逻辑。签名改为：

```python
def _run_daily_recompute(start: date, end: date, force: bool, only_emails: set | None = None):
    """后台线程：按天补算人员能效数据（only_emails 非空时仅重算指定人员）"""
```

将循环体内"非 force 跳过 + aggregate 调用"部分（原第 193-222 行）替换为：

```python
            # 计算本日应处理的目标邮箱集合
            if only_emails is None:
                # 全员模式：非 force 时整天已有记录则跳过
                if not force:
                    existing = (
                        db.query(EmployeeEfficiencyDaily)
                        .filter_by(stat_date=current)
                        .count()
                    )
                    if existing > 0:
                        with _recompute_lock:
                            _recompute_task["skipped"].append(current.isoformat())
                            _recompute_task["skipped_days"] += 1
                            _recompute_task["processed_days"] += 1
                        current += timedelta(days=1)
                        continue
                targets = None
            else:
                # 指定人员模式：force 覆盖全部；非 force 只补缺失
                if force:
                    targets = set(only_emails)
                else:
                    done = _existing_daily_emails(db, current, only_emails)
                    targets = set(only_emails) - done
                if not targets:
                    with _recompute_lock:
                        _recompute_task["skipped"].append(current.isoformat())
                        _recompute_task["skipped_days"] += 1
                        _recompute_task["processed_days"] += 1
                    current += timedelta(days=1)
                    continue

            try:
                aggregator.aggregate(current, only_emails=targets)
                with _recompute_lock:
                    _recompute_task["processed"].append(current.isoformat())
                    _recompute_task["processed_days"] += 1
            except Exception as ex:
                logger.exception(f"补算 {current} 失败")
                with _recompute_lock:
                    _recompute_task["failed"].append(
                        {"date": current.isoformat(), "error": str(ex)}
                    )
                    _recompute_task["failed_days"] += 1
                    _recompute_task["processed_days"] += 1

            current += timedelta(days=1)
```

> 注意：全员模式下 `targets = None`，`aggregate(current, only_emails=None)` 即原全员行为，与 Task 1 的默认参数一致。

**(d)** 若 `tests/test_api/test_efficiency.py` 顶部缺少 mock 导入，在导入区添加：

```python
from unittest.mock import MagicMock, patch
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_api/test_efficiency.py -k "recompute or run_daily" -v`
Expected: PASS（含原有 `test_recompute_requires_admin` 回归）

---

## Task 6: 按月 `monthly_recompute` 端点 + `_run_monthly_recompute`

**Files:**
- Modify: `app/api/efficiency.py`（`monthly_recompute` 端点 1139 行、`_run_monthly_recompute` 242 行）
- Test: `tests/test_api/test_efficiency_monthly.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_api/test_efficiency_monthly.py` 末尾追加。先确认文件顶部具备所需导入（`date`、`json`、`MagicMock/patch`、`EmployeeEfficiencyMonthly`、登录 fixture）。该文件已有 `login_as_admin` 等 fixture 与 monthly 数据构造方式；若缺 `from unittest.mock import patch` 请在导入区补上。

```python
def test_monthly_recompute_specified_all_existing_not_force_skips(
    client, db_session, login_as_admin
):
    """指定人员 + not force + 全部已有月度记录 → 同步返回跳过，不启动任务"""
    from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
    db_session.add(EmployeeEfficiencyMonthly(
        author_email="alice@b.com", author_name="Alice", year_month="2026-05",
        commits_count=3, additions=100, deletions=20, files_changed=5,
        new_files=0, deleted_files=0, active_days=2,
        projects_involved=json.dumps(["proj-a"]),
        review_score=85, review_grade="良好", review_summary="ok",
        work_summary=json.dumps(["A"]), summary_top_n=5, llm_status="success",
    ))
    db_session.commit()

    resp = client.post(
        "/api/efficiency/monthly/recompute",
        json={"year_month": "2026-05", "force": False,
              "emails": ["alice@b.com"]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data.get("skipped") is True


def test_monthly_recompute_specified_partial_missing_starts_with_remaining(
    client, db_session, login_as_admin, monkeypatch
):
    """指定人员 + not force + 部分缺失 → 启动任务，only_emails 仅含缺失者"""
    import app.api.efficiency as eff
    from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
    db_session.add(EmployeeEfficiencyMonthly(
        author_email="alice@b.com", author_name="Alice", year_month="2026-05",
        commits_count=3, additions=100, deletions=20, files_changed=5,
        new_files=0, deleted_files=0, active_days=2,
        projects_involved=json.dumps(["proj-a"]),
        review_score=85, review_grade="良好", review_summary="ok",
        work_summary=json.dumps(["A"]), summary_top_n=5, llm_status="success",
    ))
    db_session.commit()

    captured = {}

    def _fake_thread(target=None, args=(), daemon=None):
        captured["args"] = args

        class _T:
            def start(self_inner):
                pass
        return _T()

    monkeypatch.setattr(eff.threading, "Thread", _fake_thread)

    resp = client.post(
        "/api/efficiency/monthly/recompute",
        json={"year_month": "2026-05", "force": False,
              "emails": ["alice@b.com", "bob@b.com"]},
    )
    assert resp.status_code == 200
    # args = (year_month, force, only_emails)
    assert captured["args"][2] == {"bob@b.com"}
    eff._recompute_task["is_running"] = False
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_api/test_efficiency_monthly.py::test_monthly_recompute_specified_all_existing_not_force_skips -v`
Expected: FAIL（端点未处理 emails；可能 500 或断言失败）

- [ ] **Step 3: 实现**

**(a) `monthly_recompute` 端点**（第 1139-1201 行）改造。将权限/格式校验之后、`with _recompute_lock:` 之前的"非 force 检查"块（原第 1155-1167 行）替换为分支逻辑：

```python
    normalized = _normalize_emails(body.emails)

    if normalized:
        # 指定人员模式
        if body.force:
            only_emails = set(normalized)
        else:
            done = _existing_monthly_emails(db, body.year_month, set(normalized))
            remaining = set(normalized) - done
            if not remaining:
                return ApiResponse(
                    success=True,
                    message=f"指定人员的 {body.year_month} 月度记录均已存在，如需重算请勾选 force",
                    data={"skipped": True},
                )
            only_emails = remaining
    else:
        # 全员模式
        only_emails = None
        if not body.force:
            existing = (
                db.query(EmployeeEfficiencyMonthly)
                .filter_by(year_month=body.year_month)
                .count()
            )
            if existing > 0:
                return ApiResponse(
                    success=True,
                    message=f"已存在 {existing} 条月度记录，如需重算请勾选 force",
                    data={"skipped": True, "existing": existing},
                )
```

接着将状态 update 块加入 `"target_emails": normalized,`（在 `"error": None,` 之前），并将线程启动改为透传 `only_emails`：

```python
    with _recompute_lock:
        if _recompute_task["is_running"]:
            raise HTTPException(409, "补算任务正在执行中，请稍后再试")
        _recompute_task.update({
            "is_running": True,
            "task_type": "monthly",
            "start_date": None,
            "end_date": None,
            "year_month": body.year_month,
            "total_days": 1,
            "processed_days": 0,
            "skipped_days": 0,
            "failed_days": 0,
            "current_date": body.year_month,
            "processed": [],
            "skipped": [],
            "failed": [],
            "cancelled": False,
            "target_emails": normalized,
            "error": None,
        })

    t = threading.Thread(
        target=_run_monthly_recompute,
        args=(body.year_month, body.force, only_emails),
        daemon=True,
    )
    t.start()

    return ApiResponse(
        success=True,
        message="月度补算任务已启动",
        data={"task_type": "monthly", "year_month": body.year_month,
              "target_emails": normalized},
    )
```

**(b) `_run_monthly_recompute`**（第 242 行）改签名并透传：

```python
def _run_monthly_recompute(year_month: str, force: bool, only_emails: set | None = None):
    """后台线程：补算月度能效数据（only_emails 非空时仅重算指定人员）"""
```

将其中 `result = aggregator.aggregate(year_month)`（原第 267 行）改为：

```python
        result = aggregator.aggregate(year_month, only_emails=only_emails)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_api/test_efficiency_monthly.py -k recompute -v`
Expected: PASS（含原有 `test_monthly_recompute_requires_admin` 回归）

---

## Task 7: 前端 — modal 邮箱输入框 + JS 解析与通知

**Files:**
- Modify: `app/templates/efficiency.html`（`#recomputeModal`，约 241-259 行）
- Modify: `app/static/js/efficiency.js`（`openRecomputeModal` 726 行、`confirmRecompute` 744 行、完成通知 819-830 行）

> 本项目无 JS 自动化测试设施，本任务以代码实现 + 手动验证收尾。

- [ ] **Step 1: 模板 — 在 modal 内新增邮箱输入框**

在 `app/templates/efficiency.html` 的 `#recomputeModal` 内，将 `modal-body`（第 247-253 行）改为在 force 复选框上方插入邮箱输入：

```html
        <div class="modal-body">
            <p id="recomputeModalDesc" style="margin-bottom: var(--space-3);"></p>
            <div style="margin-bottom: var(--space-3);">
                <label for="recomputeEmails" style="display:block; margin-bottom: var(--space-1); font-weight:500;">
                    指定人员邮箱（可选）
                </label>
                <textarea id="recomputeEmails" rows="3"
                    placeholder="多个邮箱用逗号或换行分隔；留空则补算全员"
                    style="width:100%; box-sizing:border-box; padding:var(--space-2); border:1px solid var(--color-slate-300); border-radius:var(--radius-sm); font-family:inherit; font-size:0.875rem; resize:vertical;"></textarea>
            </div>
            <label style="display:flex; align-items:center; gap:var(--space-2); cursor:pointer; user-select:none;">
                <input type="checkbox" id="recomputeForce" />
                <span>强制覆盖（已存在数据的日期也会重新计算）</span>
            </label>
        </div>
```

- [ ] **Step 2: JS — 新增邮箱解析函数**

在 `app/static/js/efficiency.js` 的"补算按钮"区块（`var _pollTimer = null;` 第 724 行附近）下方新增解析函数：

```javascript
    function parseEmailsInput(raw) {
        if (!raw) return [];
        return raw.split(/[\s,;，；]+/)
            .map(function (s) { return s.trim(); })
            .filter(function (s) { return s.length > 0; });
    }
```

- [ ] **Step 3: JS — `openRecomputeModal` 清空输入框**

在 `openRecomputeModal`（第 726-738 行）中，`document.getElementById('recomputeForce').checked = false;`（第 736 行）之后新增一行：

```javascript
        document.getElementById('recomputeEmails').value = '';
```

- [ ] **Step 4: JS — `confirmRecompute` 解析并加入 emails**

在 `confirmRecompute`（第 744 行）中，将 force 读取与 body 构造部分（第 745-758 行）改为：

```javascript
    function confirmRecompute() {
        var force = document.getElementById('recomputeForce').checked;
        var emails = parseEmailsInput(document.getElementById('recomputeEmails').value);
        closeRecomputeModal();

        var btn = document.getElementById('btnRecompute');
        btn.disabled = true;

        var url, body;
        if (STATE.mode === 'monthly') {
            url = '/api/efficiency/monthly/recompute';
            body = { year_month: STATE.yearMonth, force: force };
        } else {
            url = '/api/efficiency/recompute';
            body = { start_date: STATE.startDate, end_date: STATE.endDate, force: force };
        }
        if (emails.length) {
            body.emails = emails;
        }
```

（其余 `apiRequest(...)` 部分保持不变。）

- [ ] **Step 5: JS — 完成通知区分全员/指定人员**

在 `pollRecomputeStatus` 的完成分支（第 819-830 行），将通知文案改为根据 `d.target_emails` 区分：

```javascript
                if (!d.is_running) {
                    stopRecomputePolling();
                    // 显示完成通知
                    var scope = (d.target_emails && d.target_emails.length)
                        ? '指定人员（' + d.target_emails.length + ' 人）'
                        : '';
                    if (d.error) {
                        showNotification('补算异常：' + d.error, 'danger');
                    } else if (d.task_type === 'monthly') {
                        showNotification(scope ? scope + '月度补算完成' : '月度补算完成', 'success');
                    } else {
                        var msg = (scope ? scope + ' ' : '') + '补算完成：处理 '
                            + (d.processed || []).length + ' 天，'
                            + '跳过 ' + (d.skipped || []).length + ' 天，'
                            + '失败 ' + (d.failed || []).length + ' 天';
                        showNotification(msg, 'success');
                    }
                }
```

> 上述代码对应 `pollRecomputeStatus` 的 `if (!d.is_running)` 完成分支（约 817-830 行）：在 `stopRecomputePolling()` 后计算 `scope`，再把它拼进 `monthly` 与 `daily` 两条既有通知文案。

- [ ] **Step 6: 手动验证**

启动应用，进入人员能效页面，以系统管理员登录：

1. **按天-全员回归**：切到按天模式，点"立即补算"，邮箱留空，确认 → 任务正常启动并完成（行为同改造前）
2. **按天-指定人员**：输入一个真实存在提交的邮箱（含大小写混合验证大小写不敏感），不勾 force → 仅该人当天缺失数据被补算；勾 force → 该人数据被覆盖；其他人数据不变
3. **按天-多邮箱**：用逗号/换行输入多个邮箱 → 均被处理
4. **按月-指定人员**：切月度模式，输入邮箱，验证 not force 跳过已存在 / force 覆盖；完成通知显示"指定人员（N 人）"
5. **无数据邮箱**：输入不存在的邮箱 → 不报错，完成通知处理 0 天/人
6. **并发保护**：补算进行中再次点击 → 提示"任务正在执行中"

---

## Task 8: 全量回归测试

- [ ] **Step 1: 运行能效相关全部测试**

Run:
```bash
pytest tests/test_services/test_efficiency_aggregator.py \
       tests/test_services/test_efficiency_monthly_aggregator.py \
       tests/test_services/test_efficiency_hook.py \
       tests/test_api/test_efficiency.py \
       tests/test_api/test_efficiency_monthly.py -v
```
Expected: 全部 PASS，无回归

- [ ] **Step 2: 运行完整测试套件**

Run: `pytest -q`
Expected: 全部 PASS（确认改动未影响其他模块）

---

## 自审查记录

- **Spec 覆盖**：邮箱多输入（Task 7）、force 控制覆盖（Task 5/6）、聚合器过滤（Task 1/2）、大小写不敏感（Task 1/2/4）、按人员跳过（Task 5/6）、target_emails 通知（Task 5/6/7）、无数据宽松处理（Task 7 手动验证 + aggregate 自然不写入）、权限不变（依赖原有校验）——均有对应任务。
- **Placeholder**：无 TBD/TODO；每个代码步骤含完整代码。
- **类型/签名一致性**：`only_emails` 全程为"小写集合或 None"；`aggregate(target_date/year_month, only_emails=None)`、`_run_daily_recompute(start,end,force,only_emails=None)`、`_run_monthly_recompute(year_month,force,only_emails=None)`、`_normalize_emails -> list`、`_existing_*_emails -> set`、请求体 `emails: Optional[list]`、状态 `target_emails: list`——跨任务一致。

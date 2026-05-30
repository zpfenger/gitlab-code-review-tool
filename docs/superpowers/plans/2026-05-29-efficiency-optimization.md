# 人员能效功能优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展人员能效系统，支持月度汇总、日期区间查询、Tab 切换三大功能

**Architecture:** 新增 `employee_efficiency_monthly` 表 + `EfficiencyMonthlyAggregator` 服务，复用现有 API 端点扩展区间查询能力，前端增加 Tab 切换和弹窗交互

**Tech Stack:** Python 3.x, FastAPI, SQLAlchemy, APScheduler, ECharts, pytest

---

## 文件结构

### 新增文件
| 文件 | 职责 |
|------|------|
| `app/models/employee_efficiency_monthly.py` | 月度汇总 ORM 模型 |
| `app/services/efficiency_monthly_aggregator.py` | 月度聚合服务（读 daily → 聚合 → LLM → 写 monthly） |
| `tests/test_models/test_employee_efficiency_monthly.py` | 月度模型单元测试 |
| `tests/test_services/test_efficiency_monthly_aggregator.py` | 月度聚合服务测试 |
| `tests/test_api/test_efficiency_monthly.py` | 月度 API 测试 |

### 修改文件
| 文件 | 修改内容 |
|------|----------|
| `app/models/__init__.py` | 导出 `EmployeeEfficiencyMonthly` |
| `app/api/efficiency.py` | 新增 `/monthly/list`、`/monthly/detail` 端点 |
| `app/services/scheduler.py` | 新增月度定时任务注册 |
| `app/static/js/efficiency.js` | Tab 切换、区间查询、弹窗交互 |
| `app/templates/efficiency.html` | Tab UI、日期区间选择器、弹窗 HTML |

---

## Task 1: 月度汇总模型

**Files:**
- Create: `app/models/employee_efficiency_monthly.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_models/test_employee_efficiency_monthly.py`

- [ ] **Step 1: 编写月度模型测试**

```python
# tests/test_models/test_employee_efficiency_monthly.py
"""EmployeeEfficiencyMonthly 模型测试"""
import json
import os
import tempfile
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly


@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    eng = create_engine(f"sqlite:///{path}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = Session()
    yield s
    s.close()


def test_create_monthly_record(session):
    """创建月度记录，字段正确赋值"""
    row = EmployeeEfficiencyMonthly(
        author_email="a@b.com",
        author_name="Alice",
        year_month="2026-05",
        commits_count=50,
        additions=1000,
        deletions=500,
        files_changed=30,
        new_files=5,
        deleted_files=2,
        active_days=20,
        projects_involved=json.dumps(["proj-a", "proj-b"]),
        review_score=85,
        review_grade="良好",
        review_summary="本月整体良好",
        work_summary=json.dumps(["完成A", "修复B"]),
        summary_top_n=10,
        llm_status="success",
    )
    session.add(row)
    session.commit()

    saved = session.query(EmployeeEfficiencyMonthly).first()
    assert saved.author_email == "a@b.com"
    assert saved.year_month == "2026-05"
    assert saved.commits_count == 50
    assert saved.active_days == 20
    assert saved.review_score == 85
    assert json.loads(saved.projects_involved) == ["proj-a", "proj-b"]
    assert json.loads(saved.work_summary) == ["完成A", "修复B"]


def test_unique_constraint_email_month(session):
    """同人同月只能有一条记录（UPSERT 幂等基础）"""
    row1 = EmployeeEfficiencyMonthly(
        author_email="a@b.com", author_name="Alice",
        year_month="2026-05", commits_count=10,
    )
    session.add(row1)
    session.commit()

    row2 = EmployeeEfficiencyMonthly(
        author_email="a@b.com", author_name="Alice",
        year_month="2026-05", commits_count=20,
    )
    session.add(row2)
    with pytest.raises(Exception):  # IntegrityError
        session.commit()
    session.rollback()


def test_optional_llm_fields(session):
    """LLM 字段可为空（pending 状态）"""
    row = EmployeeEfficiencyMonthly(
        author_email="a@b.com", author_name="Alice",
        year_month="2026-05", commits_count=10,
        llm_status="pending",
    )
    session.add(row)
    session.commit()

    saved = session.query(EmployeeEfficiencyMonthly).first()
    assert saved.review_score is None
    assert saved.review_grade is None
    assert saved.review_summary is None
    assert saved.work_summary is None
    assert saved.llm_error is None


def test_repr(session):
    """__repr__ 包含关键信息"""
    row = EmployeeEfficiencyMonthly(
        author_email="a@b.com", author_name="Alice",
        year_month="2026-05", review_score=85,
    )
    assert "a@b.com" in repr(row)
    assert "2026-05" in repr(row)
```

- [ ] **Step 2: 运行测试确认失败**

运行: `pytest tests/test_models/test_employee_efficiency_monthly.py -v`
预期: FAIL — `ModuleNotFoundError: No module named 'app.models.employee_efficiency_monthly'`

- [ ] **Step 3: 实现月度模型**

```python
# app/models/employee_efficiency_monthly.py
"""人员能效月度汇总表（人 × 月聚合）"""
from sqlalchemy import Column, String, Integer, Text, Index, UniqueConstraint
from app.models.base import BaseModel


class EmployeeEfficiencyMonthly(BaseModel):
    """人员能效月度汇总表

    每行记录某人某月的代码量汇总、LLM 月度评分、月度工作总结。
    由 EfficiencyMonthlyAggregator 在每月1日定时任务中生成。
    """
    __tablename__ = "employee_efficiency_monthly"

    # 人员维度
    author_email = Column(String(200), nullable=False, comment="提交者邮箱")
    author_name = Column(String(100), nullable=False, comment="提交者显示名")
    year_month = Column(String(7), nullable=False, comment="统计月份，格式 YYYY-MM")

    # 代码量统计（从 daily 求和）
    commits_count = Column(Integer, nullable=False, default=0, comment="提交次数")
    additions = Column(Integer, nullable=False, default=0, comment="新增行数")
    deletions = Column(Integer, nullable=False, default=0, comment="删除行数")
    files_changed = Column(Integer, nullable=False, default=0, comment="涉及文件数")
    new_files = Column(Integer, nullable=False, default=0, comment="新建文件数")
    deleted_files = Column(Integer, nullable=False, default=0, comment="删除文件数")
    active_days = Column(Integer, nullable=False, default=0, comment="本月活跃天数")

    # 涉及项目（JSON 数组，合并去重）
    projects_involved = Column(Text, nullable=False, default="[]",
                                comment='涉及项目名 JSON 数组')

    # LLM 月度产出
    review_score = Column(Integer, nullable=True, comment="月度平均评分 0-100")
    review_grade = Column(String(10), nullable=True,
                           comment="等级：优秀/良好/一般/待改进")
    review_summary = Column(Text, nullable=True, comment="LLM 月度评分简述")
    work_summary = Column(Text, nullable=True,
                           comment="LLM 月度工作总结 JSON 数组")
    summary_top_n = Column(Integer, nullable=True, default=10,
                            comment="生成时使用的 top_n")

    # 状态
    llm_status = Column(String(20), nullable=False, default="pending",
                         comment="pending/success/failed/skipped")
    llm_error = Column(Text, nullable=True, comment="LLM 失败原因")

    __table_args__ = (
        UniqueConstraint("author_email", "year_month",
                          name="uq_employee_efficiency_monthly_email_month"),
        Index("idx_employee_efficiency_monthly_year_month", "year_month"),
        Index("idx_employee_efficiency_monthly_email_month",
              "author_email", "year_month"),
    )

    def __repr__(self):
        return (f"<EmployeeEfficiencyMonthly(email='{self.author_email}', "
                f"month={self.year_month}, score={self.review_score})>")
```

- [ ] **Step 4: 更新模型导出**

在 `app/models/__init__.py` 中添加:

```python
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
```

并在 `__all__` 中添加 `'EmployeeEfficiencyMonthly'`。

- [ ] **Step 5: 运行测试确认通过**

运行: `pytest tests/test_models/test_employee_efficiency_monthly.py -v`
预期: 4 个测试全部 PASS

- [ ] **Step 6: 提交**

```bash
git add app/models/employee_efficiency_monthly.py app/models/__init__.py tests/test_models/test_employee_efficiency_monthly.py
git commit -m "feat: add EmployeeEfficiencyMonthly model with tests"
```

---

## Task 2: 月度 LLM Prompt 与解析

**Files:**
- Modify: `app/services/efficiency_llm.py`
- Test: `tests/test_services/test_efficiency_llm.py` (在现有文件中追加)

- [ ] **Step 1: 编写月度 LLM 解析测试**

在 `tests/test_services/test_efficiency_llm.py` 中追加:

```python
# ── 月度 LLM 测试 ──────────────────────────────────
def test_parse_monthly_score():
    """解析月度总分"""
    from app.services.efficiency_llm import parse_score
    text = "## 月度总分：82 分"
    assert parse_score(text) == 82


def test_parse_monthly_work_summary():
    """解析月度工作总结"""
    from app.services.efficiency_llm import parse_work_summary
    text = """## 月度主要工作（不超过 10 条）
1. 完成用户模块重构
2. 修复支付系统 Bug
3. 优化查询性能
"""
    result = parse_work_summary(text, top_n=10)
    assert len(result) == 3
    assert "完成用户模块重构" in result[0]


def test_parse_monthly_review_summary():
    """解析月度评分简述"""
    from app.services.efficiency_llm import parse_review_summary
    text = """## 月度评分简述
本月代码质量整体良好，完成了多个核心模块的重构工作。

## 月度主要工作
1. 重构
"""
    result = parse_review_summary(text)
    assert "本月代码质量整体良好" in result
```

- [ ] **Step 2: 运行测试确认通过**

运行: `pytest tests/test_services/test_efficiency_llm.py -v`
预期: 全部 PASS（现有解析函数已兼容月度格式，因为使用的是通用正则）

- [ ] **Step 3: 添加月度 Prompt 模板**

在 `app/services/efficiency_llm.py` 中追加月度 Prompt:

```python
# ── 月度 Prompt 模板 ──────────────────────────────────
EFFICIENCY_MONTHLY_SYSTEM_PROMPT = """你是一位资深的软件开发工程师，需要对员工 {author_name} 在 {year_month} 月度的代码提交进行综合评审，并总结本月主要工作成果。

### 评分目标：
1. 注释（5分）：注释要"有用"不冗余，只注释"为什么这么做"
2. 业务逻辑校验（30分）：是否符合需求文档的核心规则、异常处理是否合理
3. 性能优化点（40分）：是否存在性能瓶颈、缓存策略是否合理
4. 安全风险排查（10分）：是否存在安全漏洞、敏感数据脱敏
5. 代码架构与扩展性（10分）：是否遵循 SOLID、有无过度耦合
6. 编码规范（5分）：命名/注释/格式统一性

### 输出格式（严格按照）：
请按以下 Markdown 结构输出，确保所有标记都存在，便于程序解析：

## 月度评分简述
（2-3 句话概括本月整体表现和代码质量趋势）

## 月度评分明细
- 注释（5分）：x 分，说明
- 业务逻辑校验（30分）：x 分，说明
- 性能优化点（40分）：x 分，说明
- 安全风险排查（10分）：x 分，说明
- 代码架构与扩展性（10分）：x 分，说明
- 编码规范（5分）：x 分，说明

## 月度主要工作（不超过 {top_n} 条）
1. xxx
2. xxx
（按对业务的影响和工作量排序）

## 月度总分：XX 分
"""


EFFICIENCY_MONTHLY_USER_PROMPT = """以下是员工 {author_name} 在 {year_month} 的代码提交数据概览。

### 本月数据：
- 活跃天数：{active_days} 天
- 提交次数：{commits_count} 次
- 代码变更：+{additions} / -{deletions}
- 涉及项目：{projects}

### 每日评分详情：
{daily_scores_summary}

请按系统提示的格式输出月度评分简述、月度评分明细、月度主要工作（不超过 {top_n} 条）和月度总分。"""
```

- [ ] **Step 4: 添加月度 Prompt 构造函数**

```python
def build_monthly_system_prompt(author_name: str, year_month: str,
                                 top_n: int = 10) -> str:
    return EFFICIENCY_MONTHLY_SYSTEM_PROMPT.format(
        author_name=author_name, year_month=year_month, top_n=top_n,
    )


def build_monthly_user_prompt(author_name: str, year_month: str,
                               active_days: int, commits_count: int,
                               additions: int, deletions: int,
                               projects: str, daily_scores_summary: str,
                               top_n: int = 10) -> str:
    return EFFICIENCY_MONTHLY_USER_PROMPT.format(
        author_name=author_name, year_month=year_month,
        active_days=active_days, commits_count=commits_count,
        additions=additions, deletions=deletions,
        projects=projects, daily_scores_summary=daily_scores_summary,
        top_n=top_n,
    )
```

- [ ] **Step 5: 添加月度 LLM 调用函数**

```python
def call_monthly_llm(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    year_month: str,
    active_days: int,
    commits_count: int,
    additions: int,
    deletions: int,
    projects: str,
    daily_scores_summary: str,
    top_n: int = 10,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    timeout: int = 240,
    max_retries: int = 3,
    retry_delay: int = 10,
) -> Optional[str]:
    """同步调用 LLM 生成月度总结，返回原始 markdown 文本"""
    import time

    messages = [
        {"role": "system", "content": build_monthly_system_prompt(
            author_name=author_name, year_month=year_month, top_n=top_n,
        )},
        {"role": "user", "content": build_monthly_user_prompt(
            author_name=author_name, year_month=year_month,
            active_days=active_days, commits_count=commits_count,
            additions=additions, deletions=deletions,
            projects=projects, daily_scores_summary=daily_scores_summary,
            top_n=top_n,
        )},
    ]

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = (data.get("choices", [{}])[0]
                              .get("message", {})
                              .get("content"))
                if content:
                    return content
                logger.warning("月度 LLM 返回空内容")
                return None
        except httpx.TimeoutException:
            logger.warning(f"月度 LLM 请求超时 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
        except httpx.HTTPStatusError as e:
            logger.error(f"月度 LLM 请求失败: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"月度 LLM 请求异常: {type(e).__name__}: {e}")
            return None

    logger.error("月度 LLM 达到最大重试次数")
    return None
```

- [ ] **Step 6: 添加月度解析封装**

```python
def call_and_parse_monthly(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    year_month: str,
    active_days: int,
    commits_count: int,
    additions: int,
    deletions: int,
    projects: str,
    daily_scores_summary: str,
    top_n: int = 10,
    **llm_kwargs,
) -> Dict[str, object]:
    """调用月度 LLM 并解析结果"""
    raw = call_monthly_llm(
        api_url=api_url, api_key=api_key, model=model,
        author_name=author_name, year_month=year_month,
        active_days=active_days, commits_count=commits_count,
        additions=additions, deletions=deletions,
        projects=projects, daily_scores_summary=daily_scores_summary,
        top_n=top_n, **llm_kwargs,
    )
    if raw is None:
        return {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }

    logger.debug(f"月度 LLM 原始输出 [{author_name}/{year_month}]: {raw[:500]}...")

    score = parse_score(raw)
    if score == 0:
        logger.warning(f"月度评分解析失败 [{author_name}/{year_month}]")

    return {
        "raw": raw,
        "score": score,
        "grade": map_score_to_grade(score) if score > 0 else None,
        "work_summary": parse_work_summary(raw, top_n=top_n),
        "review_summary": parse_review_summary(raw),
        "success": True,
    }
```

- [ ] **Step 7: 运行全部 efficiency_llm 测试**

运行: `pytest tests/test_services/test_efficiency_llm.py -v`
预期: 全部 PASS

- [ ] **Step 8: 提交**

```bash
git add app/services/efficiency_llm.py tests/test_services/test_efficiency_llm.py
git commit -m "feat: add monthly LLM prompt, call, and parse functions"
```

---

## Task 3: 月度聚合服务

**Files:**
- Create: `app/services/efficiency_monthly_aggregator.py`
- Test: `tests/test_services/test_efficiency_monthly_aggregator.py`

- [ ] **Step 1: 编写月度聚合服务测试**

```python
# tests/test_services/test_efficiency_monthly_aggregator.py
"""EfficiencyMonthlyAggregator 测试"""
import json
import os
import tempfile
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from app.database import Base
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
from app.services.efficiency_monthly_aggregator import EfficiencyMonthlyAggregator


@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    eng = create_engine(f"sqlite:///{path}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = Session()
    yield s
    s.close()


def _seed_daily(session, email, name, d, *, score=80, commits=3,
                adds=100, dels=20, projects=None):
    session.add(EmployeeEfficiencyDaily(
        author_email=email, author_name=name, stat_date=d,
        commits_count=commits, additions=adds, deletions=dels,
        files_changed=5, new_files=0, deleted_files=0,
        projects_involved=json.dumps(projects or ["proj-a"]),
        review_score=score, review_grade="良好",
        review_summary="ok", work_summary=json.dumps(["A", "B"]),
        summary_top_n=5, llm_status="success",
    ))
    session.commit()


@pytest.fixture
def llm_mock():
    with patch("app.services.efficiency_monthly_aggregator.call_and_parse_monthly") as m:
        m.return_value = {
            "raw": "## 月度总分：85 分",
            "score": 85,
            "grade": "良好",
            "work_summary": ["完成A", "修复B"],
            "review_summary": "本月整体良好",
            "success": True,
        }
        yield m


def test_aggregate_single_author(session, llm_mock):
    """单作者多天数据聚合为一条月度记录"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1),
                score=80, commits=3, adds=100, dels=20)
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 2),
                score=90, commits=5, adds=200, dels=50)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    result = agg.aggregate("2026-05")

    assert result["authors_total"] == 1
    assert result["authors_success"] == 1

    rows = session.query(EmployeeEfficiencyMonthly).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.author_email == "a@b.com"
    assert r.year_month == "2026-05"
    assert r.commits_count == 8      # 3 + 5
    assert r.additions == 300        # 100 + 200
    assert r.deletions == 70         # 20 + 50
    assert r.active_days == 2
    assert r.review_score == 85      # LLM 返回的月度评分
    assert r.llm_status == "success"


def test_review_score_arithmetic_average(session, llm_mock):
    """review_score 取日度评分的算术平均值"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1), score=80)
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 2), score=90)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    # mock 返回的 score 会被覆盖为算术平均
    llm_mock.return_value["score"] = 0  # 故意设为 0
    agg.aggregate("2026-05")

    row = session.query(EmployeeEfficiencyMonthly).first()
    # 算术平均: (80 + 90) / 2 = 85
    assert row.review_score == 85


def test_projects_merge_dedup(session, llm_mock):
    """多天涉及项目合并去重"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1),
                projects=["proj-a", "proj-b"])
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 2),
                projects=["proj-b", "proj-c"])

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate("2026-05")

    row = session.query(EmployeeEfficiencyMonthly).first()
    projects = json.loads(row.projects_involved)
    assert set(projects) == {"proj-a", "proj-b", "proj-c"}


def test_upsert_idempotent(session, llm_mock):
    """重复聚合不产生重复行"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1), score=80)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate("2026-05")
    agg.aggregate("2026-05")

    rows = session.query(EmployeeEfficiencyMonthly).all()
    assert len(rows) == 1


def test_llm_failure_records_error(session):
    """LLM 失败时统计数据入库，llm_status=failed"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1), score=80)

    with patch("app.services.efficiency_monthly_aggregator.call_and_parse_monthly") as m:
        m.return_value = {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }
        agg = EfficiencyMonthlyAggregator(
            db=session,
            llm_config={"api_url": "x", "api_key": "x", "model": "m"},
        )
        agg.aggregate("2026-05")

    row = session.query(EmployeeEfficiencyMonthly).first()
    assert row.commits_count == 3       # 统计数据正常入库
    assert row.llm_status == "failed"
    assert row.review_score is None


def test_empty_month_returns_zero(session, llm_mock):
    """无数据月份返回 0 作者"""
    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    result = agg.aggregate("2026-05")
    assert result["authors_total"] == 0
    assert result["authors_success"] == 0


def test_json_defensive_parsing(session, llm_mock):
    """projects_involved 或 work_summary 为无效 JSON 时优雅降级"""
    session.add(EmployeeEfficiencyDaily(
        author_email="a@b.com", author_name="Alice",
        stat_date=date(2026, 5, 1),
        commits_count=1, additions=10, deletions=2,
        files_changed=1, new_files=0, deleted_files=0,
        projects_involved="invalid-json",  # 故意无效
        review_score=80, review_grade="良好",
        review_summary="ok", work_summary="also-invalid",
        summary_top_n=5, llm_status="success",
    ))
    session.commit()

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    # 不应抛异常
    result = agg.aggregate("2026-05")
    assert result["authors_total"] == 1
```

- [ ] **Step 2: 运行测试确认失败**

运行: `pytest tests/test_services/test_efficiency_monthly_aggregator.py -v`
预期: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现月度聚合服务**

```python
# app/services/efficiency_monthly_aggregator.py
"""人员能效月度聚合服务

职责：
1. 读取指定月份的所有 daily 数据
2. 按 author_email 分组聚合（求和 + 去重）
3. 调用 LLM 生成月度总结（串行，2 秒间隔）
4. UPSERT 写入 employee_efficiency_monthly
"""
from __future__ import annotations
import json
import time
from datetime import date
from typing import Any, Dict, List

from loguru import logger
from sqlalchemy.orm import Session

from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
from app.services.efficiency_llm import call_and_parse_monthly


def _safe_json_loads(text: str, default=None):
    """防御性 JSON 解析，失败返回 default"""
    if not text:
        return default if default is not None else []
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"JSON 解析失败，原始值: {text[:100]}")
        return default if default is not None else []


class EfficiencyMonthlyAggregator:
    """月度能效聚合器：从 daily 表读取 → 聚合 → LLM → 写入 monthly 表"""

    def __init__(
        self,
        db: Session,
        llm_config: Dict[str, Any],
        top_n: int = 10,
        llm_interval: int = 2,
    ):
        self.db = db
        self.llm_config = llm_config
        self.top_n = top_n
        self.llm_interval = llm_interval

    def aggregate(self, year_month: str) -> Dict[str, Any]:
        """对指定月份做一次聚合（幂等，重复调用会 UPSERT）

        Args:
            year_month: 格式 "YYYY-MM"

        Returns:
            {"year_month", "authors_total", "authors_success", "authors_failed"}
        """
        logger.info(f"开始月度能效聚合: {year_month}")

        # 1. 查询该月所有 daily 数据
        daily_rows = (
            self.db.query(EmployeeEfficiencyDaily)
            .filter(
                EmployeeEfficiencyDaily.stat_date >= f"{year_month}-01",
                EmployeeEfficiencyDaily.stat_date < _next_month(year_month),
            )
            .all()
        )

        if not daily_rows:
            logger.warning(f"月份 {year_month} 无 daily 数据")
            return {
                "year_month": year_month,
                "authors_total": 0,
                "authors_success": 0,
                "authors_failed": 0,
            }

        # 2. 按 author_email 分组
        grouped: Dict[str, List[EmployeeEfficiencyDaily]] = {}
        for row in daily_rows:
            grouped.setdefault(row.author_email, []).append(row)

        logger.info(f"月度聚合: {year_month}, 共 {len(grouped)} 位作者")

        # 3. 逐个作者聚合（串行，避免 LLM 限流）
        success = 0
        failed = 0
        for i, (email, records) in enumerate(grouped.items(), 1):
            try:
                self._aggregate_author(email, records, year_month)
                success += 1
                logger.info(f"月度聚合进度: [{i}/{len(grouped)}] {email} 完成")
            except Exception as e:
                logger.exception(f"月度聚合失败 [{email}]: {e}")
                failed += 1

            # LLM 限流间隔
            if i < len(grouped):
                time.sleep(self.llm_interval)

        result = {
            "year_month": year_month,
            "authors_total": len(grouped),
            "authors_success": success,
            "authors_failed": failed,
        }
        logger.info(f"月度能效聚合完成: {result}")
        return result

    def _aggregate_author(
        self,
        email: str,
        daily_records: List[EmployeeEfficiencyDaily],
        year_month: str,
    ) -> None:
        """聚合单个作者的月度数据并 UPSERT"""
        first = daily_records[0]

        # 统计字段求和
        commits_count = sum(r.commits_count or 0 for r in daily_records)
        additions = sum(r.additions or 0 for r in daily_records)
        deletions = sum(r.deletions or 0 for r in daily_records)
        files_changed = sum(r.files_changed or 0 for r in daily_records)
        new_files = sum(r.new_files or 0 for r in daily_records)
        deleted_files = sum(r.deleted_files or 0 for r in daily_records)
        active_days = len(daily_records)

        # 项目合并去重
        all_projects = set()
        for r in daily_records:
            all_projects.update(_safe_json_loads(r.projects_involved, []))

        # review_score 算术平均
        scores = [r.review_score for r in daily_records
                  if r.review_score is not None and r.review_score > 0]
        avg_score = round(sum(scores) / len(scores)) if scores else None

        # 构造 LLM 所需的每日评分摘要
        daily_summary_parts = []
        for r in sorted(daily_records, key=lambda x: x.stat_date):
            score_str = f"{r.review_score}分" if r.review_score else "未评分"
            daily_summary_parts.append(
                f"- {r.stat_date}: {score_str}, "
                f"+{r.additions}/-{r.deletions}, "
                f"{r.commits_count}次提交"
            )
        daily_scores_summary = "\n".join(daily_summary_parts)

        # 调用 LLM
        llm_result = call_and_parse_monthly(
            api_url=self.llm_config["api_url"],
            api_key=self.llm_config["api_key"],
            model=self.llm_config["model"],
            author_name=first.author_name,
            year_month=year_month,
            active_days=active_days,
            commits_count=commits_count,
            additions=additions,
            deletions=deletions,
            projects="、".join(sorted(all_projects)),
            daily_scores_summary=daily_scores_summary,
            top_n=self.top_n,
            max_tokens=self.llm_config.get("max_tokens", 4096),
            temperature=self.llm_config.get("temperature", 0.7),
            timeout=self.llm_config.get("timeout", 240),
            max_retries=self.llm_config.get("max_retries", 3),
            retry_delay=self.llm_config.get("retry_delay", 10),
        )

        # UPSERT
        existing = (
            self.db.query(EmployeeEfficiencyMonthly)
            .filter_by(author_email=email, year_month=year_month)
            .first()
        )

        values = dict(
            author_email=email,
            author_name=first.author_name,
            year_month=year_month,
            commits_count=commits_count,
            additions=additions,
            deletions=deletions,
            files_changed=files_changed,
            new_files=new_files,
            deleted_files=deleted_files,
            active_days=active_days,
            projects_involved=json.dumps(sorted(all_projects),
                                          ensure_ascii=False),
            summary_top_n=self.top_n,
        )

        if llm_result["success"]:
            # 月度评分使用算术平均，LLM 返回的作为参考
            values.update(
                review_score=avg_score,
                review_grade=_map_score_to_grade(avg_score),
                review_summary=llm_result["review_summary"],
                work_summary=json.dumps(llm_result["work_summary"],
                                         ensure_ascii=False),
                llm_status="success",
                llm_error=None,
            )
        else:
            values.update(
                review_score=avg_score,
                review_grade=_map_score_to_grade(avg_score),
                review_summary=None,
                work_summary=None,
                llm_status="failed",
                llm_error="LLM call failed or returned empty",
            )

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            self.db.add(EmployeeEfficiencyMonthly(**values))
        self.db.commit()


def _next_month(year_month: str) -> str:
    """计算下一个月的第一天（用于日期范围过滤）"""
    year, month = int(year_month[:4]), int(year_month[5:7])
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"


def _map_score_to_grade(score: int | None) -> str | None:
    """分数映射到等级"""
    if score is None:
        return None
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "一般"
    return "待改进"
```

- [ ] **Step 4: 运行测试确认通过**

运行: `pytest tests/test_services/test_efficiency_monthly_aggregator.py -v`
预期: 8 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/efficiency_monthly_aggregator.py tests/test_services/test_efficiency_monthly_aggregator.py
git commit -m "feat: add EfficiencyMonthlyAggregator with tests"
```

---

## Task 4: 月度 API 端点

**Files:**
- Modify: `app/api/efficiency.py`
- Test: `tests/test_api/test_efficiency_monthly.py`

- [ ] **Step 1: 编写月度 API 测试**

```python
# tests/test_api/test_efficiency_monthly.py
"""月度能效 API 测试"""
import json
import os
import tempfile
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, get_db
from app.api.efficiency import router as efficiency_router
from app.api.users import get_current_user_full
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
from app.models.user import User, Role
from app.security import security_service


@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    eng = create_engine(f"sqlite:///{path}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def db_session(engine):
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def app(db_session):
    a = FastAPI()
    a.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    a.include_router(efficiency_router)

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    a.dependency_overrides[get_db] = _override_get_db
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def _ensure_role(db, name):
    role = db.query(Role).filter_by(name=name).first()
    if not role:
        role = Role(name=name, description=name, is_system_role=True)
        db.add(role)
        db.commit()
        db.refresh(role)
    return role


def _make_user(db, username, email, role_names):
    user = User(
        username=username,
        password_hash=security_service.hash_password("pass"),
        email=email,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    for rn in role_names:
        role = _ensure_role(db, rn)
        user.roles.append(role)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_user(db_session):
    return _make_user(db_session, "admin", "admin@x.com", ["system_admin"])


@pytest.fixture
def login_as_admin(app, admin_user):
    app.dependency_overrides[get_current_user_full] = lambda: admin_user
    yield admin_user
    app.dependency_overrides.pop(get_current_user_full, None)


def _seed_monthly(db, email, name, ym, *, score=80, commits=50,
                   adds=1000, dels=200, active_days=20):
    db.add(EmployeeEfficiencyMonthly(
        author_email=email, author_name=name, year_month=ym,
        commits_count=commits, additions=adds, deletions=dels,
        files_changed=30, new_files=5, deleted_files=2,
        active_days=active_days,
        projects_involved=json.dumps(["proj-a"]),
        review_score=score, review_grade="良好",
        review_summary="月度总结", work_summary=json.dumps(["A", "B"]),
        summary_top_n=10, llm_status="success",
    ))
    db.commit()


def _seed_daily(db, email, name, d, *, score=80, commits=3, adds=100, dels=20):
    db.add(EmployeeEfficiencyDaily(
        author_email=email, author_name=name, stat_date=d,
        commits_count=commits, additions=adds, deletions=dels,
        files_changed=5, new_files=0, deleted_files=0,
        projects_involved=json.dumps(["proj-a"]),
        review_score=score, review_grade="良好",
        review_summary="ok", work_summary=json.dumps(["A"]),
        summary_top_n=5, llm_status="success",
    ))
    db.commit()


# ── /monthly/list 测试 ──────────────────────

def test_monthly_list_requires_login(client):
    resp = client.get("/api/efficiency/monthly/list")
    assert resp.status_code == 401


def test_monthly_list_returns_items(client, db_session, login_as_admin):
    _seed_monthly(db_session, "a@b.com", "Alice", "2026-05", score=85)
    _seed_monthly(db_session, "c@d.com", "Carol", "2026-05", score=70)

    resp = client.get("/api/efficiency/monthly/list?year_month=2026-05")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert len(data["items"]) == 2
    assert data["team_stats"]["person_count"] == 2


def test_monthly_list_sort_by_score(client, db_session, login_as_admin):
    _seed_monthly(db_session, "a@b.com", "Alice", "2026-05", score=70)
    _seed_monthly(db_session, "b@b.com", "Bob", "2026-05", score=95)

    resp = client.get(
        "/api/efficiency/monthly/list?year_month=2026-05"
        "&sort_by=score&order=desc"
    )
    items = resp.json()["data"]["items"]
    assert items[0]["author_email"] == "b@b.com"
    assert items[1]["author_email"] == "a@b.com"


# ── /monthly/detail 测试 ──────────────────────

def test_monthly_detail_returns_summary_and_trend(
    client, db_session, login_as_admin
):
    _seed_monthly(db_session, "a@b.com", "Alice", "2026-05", score=85)
    _seed_daily(db_session, "a@b.com", "Alice", date(2026, 5, 1), score=80)
    _seed_daily(db_session, "a@b.com", "Alice", date(2026, 5, 2), score=90)

    resp = client.get(
        "/api/efficiency/monthly/detail?email=a@b.com&year_month=2026-05"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"]["author_email"] == "a@b.com"
    assert data["summary"]["review_score"] == 85
    assert len(data["daily_trend"]) == 2


def test_monthly_detail_not_found(client, db_session, login_as_admin):
    resp = client.get(
        "/api/efficiency/monthly/detail?email=nobody@x.com&year_month=2026-05"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"] is None
```

- [ ] **Step 2: 运行测试确认失败**

运行: `pytest tests/test_api/test_efficiency_monthly.py -v`
预期: FAIL — 404（端点不存在）

- [ ] **Step 3: 在 efficiency.py 中添加月度端点**

在 `app/api/efficiency.py` 末尾追加:

```python
# ──────────────── /monthly/list ────────────────

MONTHLY_SORT_FIELDS = {
    "score": EmployeeEfficiencyMonthly.review_score,
    "additions": EmployeeEfficiencyMonthly.additions,
    "deletions": EmployeeEfficiencyMonthly.deletions,
    "commits": EmployeeEfficiencyMonthly.commits_count,
    "files_changed": EmployeeEfficiencyMonthly.files_changed,
    "active_days": EmployeeEfficiencyMonthly.active_days,
}


def _serialize_monthly(row: EmployeeEfficiencyMonthly) -> dict:
    return {
        "id": row.id,
        "author_email": row.author_email,
        "author_name": row.author_name,
        "year_month": row.year_month,
        "commits_count": row.commits_count,
        "additions": row.additions,
        "deletions": row.deletions,
        "files_changed": row.files_changed,
        "new_files": row.new_files,
        "deleted_files": row.deleted_files,
        "active_days": row.active_days,
        "projects_involved": json.loads(row.projects_involved or "[]"),
        "review_score": row.review_score,
        "review_grade": row.review_grade,
        "review_summary": row.review_summary,
        "work_summary": (
            json.loads(row.work_summary) if row.work_summary else []
        ),
        "llm_status": row.llm_status,
        "llm_error": row.llm_error,
    }


def _restrict_monthly_query(query, current_user: User, db: Session):
    """复用 daily 表的权限逻辑裁剪 monthly 查询"""
    if current_user.is_system_admin():
        return query
    if current_user.is_project_admin():
        names = _allowed_project_names(current_user, db) or []
        if not names:
            return query.filter(False)
        conds = [
            EmployeeEfficiencyMonthly.projects_involved.like(f'%"{n}"%')
            for n in names
        ]
        return query.filter(or_(*conds))
    if current_user.is_project_member():
        if not current_user.email:
            return query.filter(False)
        return query.filter(
            EmployeeEfficiencyMonthly.author_email == current_user.email
        )
    return query.filter(False)


@router.get("/monthly/list")
async def monthly_list(
    year_month: str = Query(...),
    sort_by: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """月度列表 + 团队概览"""
    q = db.query(EmployeeEfficiencyMonthly)
    q = _restrict_monthly_query(q, current_user, db)
    q = q.filter(EmployeeEfficiencyMonthly.year_month == year_month)

    sort_col = MONTHLY_SORT_FIELDS.get(sort_by,
                                        EmployeeEfficiencyMonthly.review_score)
    direction = desc if order == "desc" else asc
    q = q.order_by(direction(sort_col))

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    # 团队概览
    stats_q = db.query(EmployeeEfficiencyMonthly)
    stats_q = _restrict_monthly_query(stats_q, current_user, db)
    stats_q = stats_q.filter(
        EmployeeEfficiencyMonthly.year_month == year_month
    )

    agg = stats_q.with_entities(
        func.count(EmployeeEfficiencyMonthly.id).label("n"),
        func.coalesce(
            func.sum(EmployeeEfficiencyMonthly.commits_count), 0
        ).label("total_commits"),
        func.coalesce(
            func.sum(EmployeeEfficiencyMonthly.additions), 0
        ).label("total_additions"),
        func.coalesce(
            func.sum(EmployeeEfficiencyMonthly.deletions), 0
        ).label("total_deletions"),
        func.coalesce(
            func.avg(EmployeeEfficiencyMonthly.review_score), 0
        ).label("avg_score"),
        func.coalesce(
            func.sum(EmployeeEfficiencyMonthly.active_days), 0
        ).label("total_active_days"),
    ).one()

    team_stats = {
        "person_count": int(agg.n or 0),
        "total_commits": int(agg.total_commits or 0),
        "total_additions": int(agg.total_additions or 0),
        "total_deletions": int(agg.total_deletions or 0),
        "avg_score": round(float(agg.avg_score or 0), 1),
        "total_active_days": int(agg.total_active_days or 0),
    }

    return ApiResponse(
        success=True,
        data={
            "items": [_serialize_monthly(r) for r in items],
            "total": total,
            "team_stats": team_stats,
        },
    )


# ──────────────── /monthly/detail ────────────────

@router.get("/monthly/detail")
async def monthly_detail(
    email: str = Query(...),
    year_month: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """月度详情 = 月度 summary + 每日 trend"""
    # 权限检查
    if not current_user.is_system_admin():
        if (current_user.is_project_member()
                and not current_user.is_project_admin()):
            if (current_user.email or "").lower() != email.lower():
                raise HTTPException(403, "无权查看他人月度能效详情")

    summary = (
        db.query(EmployeeEfficiencyMonthly)
        .filter_by(author_email=email, year_month=year_month)
        .first()
    )

    # 查询该月每日数据用于趋势图
    from datetime import date as date_type
    ym_start = date_type.fromisoformat(f"{year_month}-01")
    ym_end = date_type.fromisoformat(_next_month_str(year_month))

    daily_rows = (
        db.query(EmployeeEfficiencyDaily)
        .filter(
            EmployeeEfficiencyDaily.author_email == email,
            EmployeeEfficiencyDaily.stat_date >= ym_start,
            EmployeeEfficiencyDaily.stat_date < ym_end,
        )
        .order_by(EmployeeEfficiencyDaily.stat_date.asc())
        .all()
    )

    daily_trend = [
        {
            "stat_date": r.stat_date.isoformat(),
            "commits_count": r.commits_count,
            "additions": r.additions,
            "deletions": r.deletions,
            "review_score": r.review_score,
        }
        for r in daily_rows
    ]

    return ApiResponse(
        success=True,
        data={
            "summary": _serialize_monthly(summary) if summary else None,
            "daily_trend": daily_trend,
        },
    )


def _next_month_str(year_month: str) -> str:
    """返回下月第一天的 ISO 字符串"""
    year, month = int(year_month[:4]), int(year_month[5:7])
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"
```

同时需要在文件顶部的 import 区域添加:

```python
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
```

- [ ] **Step 4: 运行测试确认通过**

运行: `pytest tests/test_api/test_efficiency_monthly.py -v`
预期: 6 个测试全部 PASS

- [ ] **Step 5: 运行现有测试确认无回归**

运行: `pytest tests/test_api/test_efficiency.py -v`
预期: 7 个测试全部 PASS

- [ ] **Step 6: 提交**

```bash
git add app/api/efficiency.py tests/test_api/test_efficiency_monthly.py
git commit -m "feat: add monthly list and detail API endpoints"
```

---

## Task 4.5: 现有 /list 端点区间聚合

**Files:**
- Modify: `app/api/efficiency.py`
- Test: `tests/test_api/test_efficiency.py` (追加)

- [ ] **Step 1: 编写区间聚合测试**

在 `tests/test_api/test_efficiency.py` 末尾追加:

```python
# ── 区间聚合测试 ──────────────────────────

def test_list_range_aggregation(client, db_session, login_as_admin):
    """区间查询时按人员聚合（同一人多天合并为一行）"""
    d1 = date.today() - timedelta(days=3)
    d2 = date.today() - timedelta(days=2)
    d3 = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", d1, score=80, commits=3, adds=100, dels=20)
    _seed(db_session, "a@b.com", "Alice", d2, score=90, commits=5, adds=200, dels=50)
    _seed(db_session, "a@b.com", "Alice", d3, score=70, commits=2, adds=50, dels=10)

    resp = client.get(
        f"/api/efficiency/list?start_date={d1.isoformat()}"
        f"&end_date={d3.isoformat()}"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # 同一人聚合为一行
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["author_email"] == "a@b.com"
    assert item["commits_count"] == 10       # 3+5+2
    assert item["additions"] == 350          # 100+200+50
    assert item["deletions"] == 80           # 20+50+10
    # review_score 取算术平均
    assert item["review_score"] == 80        # (80+90+70)/3 = 80
    # team_stats 也基于聚合后的数据
    assert data["team_stats"]["person_count"] == 1
    assert data["team_stats"]["total_commits"] == 10


def test_list_range_single_day_no_aggregation(client, db_session, login_as_admin):
    """单日查询不聚合（保持现有行为）"""
    d = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", d, score=80, commits=3, adds=100, dels=20)

    resp = client.get(f"/api/efficiency/list?date={d.isoformat()}")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["commits_count"] == 3
```

- [ ] **Step 2: 运行新测试确认失败**

运行: `pytest tests/test_api/test_efficiency.py::test_list_range_aggregation -v`
预期: FAIL — 聚合逻辑不存在，返回 3 行而非 1 行

- [ ] **Step 3: 修改 /list 端点添加区间聚合逻辑**

在 `app/api/efficiency.py` 的 `list_efficiency` 函数中，当 `mode == "range"` 时添加聚合逻辑:

```python
@router.get("/list")
async def list_efficiency(
    date_str: Optional[str] = Query(None, alias="date"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """列表 + 团队概览

    单日模式：返回每人当日记录（现有行为）
    区间模式：返回每人区间汇总（聚合后）
    """
    mode, target, start, end = _resolve_date_filter(
        date_str, start_date, end_date
    )

    q = db.query(EmployeeEfficiencyDaily)
    q = _restrict_query_by_user(q, current_user, db)
    q = _apply_date_filter(q, mode, target, start, end)

    if mode == "range":
        # 区间模式：按 author_email 聚合
        return _list_range_aggregated(
            q, db, current_user, sort_by, order, limit, offset,
            start, end,
        )

    # 单日模式：保持现有行为
    sort_col = SORT_FIELDS.get(sort_by, EmployeeEfficiencyDaily.review_score)
    direction = desc if order == "desc" else asc
    q = q.order_by(direction(sort_col))

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    stats_q = db.query(EmployeeEfficiencyDaily)
    stats_q = _restrict_query_by_user(stats_q, current_user, db)
    stats_q = _apply_date_filter(stats_q, mode, target, start, end)

    agg = stats_q.with_entities(
        func.count(EmployeeEfficiencyDaily.id).label("n"),
        func.coalesce(func.sum(EmployeeEfficiencyDaily.commits_count), 0).label("total_commits"),
        func.coalesce(func.sum(EmployeeEfficiencyDaily.additions), 0).label("total_additions"),
        func.coalesce(func.sum(EmployeeEfficiencyDaily.deletions), 0).label("total_deletions"),
        func.coalesce(func.avg(EmployeeEfficiencyDaily.review_score), 0).label("avg_score"),
    ).one()

    team_stats = {
        "person_count": int(agg.n or 0),
        "total_commits": int(agg.total_commits or 0),
        "total_additions": int(agg.total_additions or 0),
        "total_deletions": int(agg.total_deletions or 0),
        "avg_score": round(float(agg.avg_score or 0), 1),
    }

    return ApiResponse(
        success=True,
        data={
            "items": [_serialize(r) for r in items],
            "total": total,
            "team_stats": team_stats,
        },
    )
```

然后添加聚合辅助函数:

```python
def _list_range_aggregated(
    base_query, db, current_user, sort_by, order, limit, offset,
    start, end,
):
    """区间模式：按 author_email 聚合统计"""
    import json as _json

    rows = base_query.all()

    # 按 author_email 分组聚合
    grouped = {}
    for r in rows:
        email = r.author_email
        if email not in grouped:
            grouped[email] = {
                "author_email": email,
                "author_name": r.author_name,
                "commits_count": 0,
                "additions": 0,
                "deletions": 0,
                "files_changed": 0,
                "scores": [],
                "projects": set(),
            }
        g = grouped[email]
        g["commits_count"] += r.commits_count or 0
        g["additions"] += r.additions or 0
        g["deletions"] += r.deletions or 0
        g["files_changed"] += r.files_changed or 0
        if r.review_score is not None and r.review_score > 0:
            g["scores"].append(r.review_score)
        try:
            g["projects"].update(_json.loads(r.projects_involved or "[]"))
        except (ValueError, TypeError):
            pass

    # 构造结果列表
    items = []
    for g in grouped.values():
        scores = g["scores"]
        avg_score = round(sum(scores) / len(scores)) if scores else None
        items.append({
            "author_email": g["author_email"],
            "author_name": g["author_name"],
            "commits_count": g["commits_count"],
            "additions": g["additions"],
            "deletions": g["deletions"],
            "files_changed": g["files_changed"],
            "review_score": avg_score,
            "review_grade": _map_score_to_grade(avg_score),
            "projects_involved": sorted(g["projects"]),
        })

    # 排序
    sort_key_map = {
        "score": lambda x: x.get("review_score") or 0,
        "additions": lambda x: x["additions"],
        "deletions": lambda x: x["deletions"],
        "commits": lambda x: x["commits_count"],
        "files_changed": lambda x: x["files_changed"],
    }
    sort_fn = sort_key_map.get(sort_by, sort_key_map["score"])
    items.sort(key=sort_fn, reverse=(order == "desc"))

    total = len(items)
    items = items[offset:offset + limit]

    # 团队概览（基于聚合后数据）
    team_stats = {
        "person_count": total,
        "total_commits": sum(g["commits_count"] for g in grouped.values()),
        "total_additions": sum(g["additions"] for g in grouped.values()),
        "total_deletions": sum(g["deletions"] for g in grouped.values()),
        "avg_score": round(
            sum(i["review_score"] or 0 for i in items) /
            max(len([i for i in items if i["review_score"]]), 1), 1
        ) if items else 0,
    }

    return ApiResponse(
        success=True,
        data={"items": items, "total": total, "team_stats": team_stats},
    )


def _map_score_to_grade(score):
    """分数映射到等级（API 层复用）"""
    if score is None:
        return None
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "一般"
    return "待改进"
```

- [ ] **Step 4: 运行全部效率 API 测试**

运行: `pytest tests/test_api/test_efficiency.py -v`
预期: 9 个测试全部 PASS（7 旧 + 2 新）

- [ ] **Step 5: 提交**

```bash
git add app/api/efficiency.py tests/test_api/test_efficiency.py
git commit -m "feat: add range aggregation to /list endpoint"
```

---

## Task 5: 月度定时任务

**Files:**
- Modify: `app/services/scheduler.py`

- [ ] **Step 1: 在 scheduler.py 中添加月度能效任务函数**

在 `app/services/scheduler.py` 文件末尾追加:

```python
def run_monthly_efficiency_aggregation():
    """每月1日凌晨执行，汇总上月数据"""
    from datetime import date
    from app.database import SessionLocal
    from app.models import Settings
    from app.security import security_service
    from app.services.efficiency_monthly_aggregator import EfficiencyMonthlyAggregator

    today = date.today()
    # 计算上月
    if today.month == 1:
        year = today.year - 1
        month = 12
    else:
        year = today.year
        month = today.month - 1

    year_month = f"{year}-{month:02d}"
    logger.info(f"开始月度能效聚合任务: {year_month}")

    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings:
            logger.error("未找到系统配置，跳过月度聚合")
            return

        llm_cfg = {
            "api_url": settings.llm_api_url,
            "api_key": (
                security_service.decrypt(settings.llm_api_key)
                if settings.llm_api_key
                else ""
            ),
            "model": settings.llm_model,
            "timeout": settings.llm_timeout,
            "max_retries": settings.llm_max_retries,
            "retry_delay": settings.llm_retry_delay,
        }
        top_n = getattr(settings, "efficiency_work_summary_top_n", 10) or 10

        aggregator = EfficiencyMonthlyAggregator(
            db=db,
            llm_config=llm_cfg,
            top_n=top_n,
        )
        result = aggregator.aggregate(year_month)
        logger.info(f"月度能效聚合任务完成: {result}")
    except Exception as e:
        logger.exception(f"月度能效聚合任务失败: {e}")
    finally:
        db.close()
```

- [ ] **Step 2: 注册定时任务**

在应用启动处（通常是 `main.py` 或初始化代码中）注册月度任务。找到设置定时任务的位置，添加:

```python
scheduler.setup_monthly_task(
    day=1,
    time="02:00",
    callback=run_monthly_efficiency_aggregation,
    job_id="monthly_efficiency",
)
```

- [ ] **Step 3: 验证语法正确**

运行: `python -c "from app.services.scheduler import run_monthly_efficiency_aggregation; print('OK')"`
预期: OK

- [ ] **Step 4: 提交**

```bash
git add app/services/scheduler.py
git commit -m "feat: add monthly efficiency aggregation scheduled task"
```

---

## Task 6: 前端 Tab 切换与日期区间选择器

**Files:**
- Modify: `app/templates/efficiency.html`
- Modify: `app/static/js/efficiency.js`

- [ ] **Step 1: 修改 HTML 模板**

在 `app/templates/efficiency.html` 中:

1. 在 `<style>` 区域追加:

```css
.efficiency-tabs { display: flex; gap: var(--space-2); margin-bottom: var(--space-4); }
.efficiency-tabs .tab { padding: var(--space-2) var(--space-4); border: 1px solid var(--color-slate-300);
                         border-radius: var(--radius-md); background: var(--color-white);
                         cursor: pointer; font-size: var(--text-sm); color: var(--color-slate-600); }
.efficiency-tabs .tab.active { background: var(--color-primary); color: var(--color-white);
                                 border-color: var(--color-primary); }
.filter-daily, .filter-monthly { display: flex; gap: var(--space-2); align-items: center; }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 1060;
                  display: none; justify-content: center; align-items: center; }
.modal-overlay.active { display: flex; }
.modal-content { background: var(--color-white); border-radius: var(--radius-lg);
                  max-width: 800px; width: 90%; max-height: 80vh; overflow-y: auto;
                  box-shadow: 0 8px 32px rgba(0,0,0,0.12); }
.modal-header { padding: var(--space-4) var(--space-5); border-bottom: 1px solid var(--color-slate-200);
                 display: flex; justify-content: space-between; align-items: center; }
.modal-body { padding: var(--space-5); }
.daily-detail-table { width: 100%; }
.daily-detail-table th, .daily-detail-table td { padding: var(--space-2) var(--space-3);
                                                   text-align: left; border-bottom: 1px solid var(--color-slate-100); }
.daily-detail-table tbody tr { cursor: pointer; }
.daily-detail-table tbody tr:hover { background: var(--color-slate-50); }
```

2. 替换页面头部的查询条件区域:

```html
<!-- Tab 切换 -->
<div class="efficiency-tabs">
    <button class="tab active" data-mode="daily" id="tabDaily">按天</button>
    <button class="tab" data-mode="monthly" id="tabMonthly">按月</button>
</div>

<!-- 查询条件 -->
<div class="filter-bar" style="display: flex; gap: var(--space-2); align-items: center; margin-bottom: var(--space-4);">
    <!-- 按天模式 -->
    <div class="filter-daily" id="filterDaily">
        <input type="date" id="startDate" class="form-input" style="width: auto;" />
        <span>~</span>
        <input type="date" id="endDate" class="form-input" style="width: auto;" />
    </div>
    <!-- 按月模式 -->
    <div class="filter-monthly" id="filterMonthly" style="display:none">
        <input type="month" id="filterMonth" class="form-input" style="width: auto;" />
    </div>
    <button class="btn btn-secondary" id="btnRefresh">
        <i class="bi bi-arrow-clockwise"></i> 刷新
    </button>
    <button class="btn btn-primary" id="btnRecompute" style="display:none">
        <i class="bi bi-arrow-repeat"></i> 立即补算
    </button>
</div>
```

注意：移除原来的 `<input type="date" id="filterDate" />`，替换为上述结构。

3. 在 `<!-- 详情抽屉 -->` 之前添加两个弹窗:

```html>
<!-- 区间明细弹窗 -->
<div class="modal-overlay" id="rangeDetailModal">
    <div class="modal-content">
        <div class="modal-header">
            <h5 style="margin:0; font-weight:600;" id="rangeModalTitle">区间明细</h5>
            <button class="btn-close-modal" id="rangeModalClose">&times;</button>
        </div>
        <div class="modal-body" id="rangeModalBody">
            <div style="color: var(--color-slate-500);">加载中...</div>
        </div>
    </div>
</div>

<!-- 月度详情弹窗 -->
<div class="modal-overlay" id="monthlyDetailModal">
    <div class="modal-content">
        <div class="modal-header">
            <h5 style="margin:0; font-weight:600;" id="monthlyModalTitle">月度详情</h5>
            <button class="btn-close-modal" id="monthlyModalClose">&times;</button>
        </div>
        <div class="modal-body" id="monthlyModalBody">
            <div style="color: var(--color-slate-500);">加载中...</div>
        </div>
    </div>
</div>
```

- [ ] **Step 2: 重写 efficiency.js**

完整重写 `app/static/js/efficiency.js`:

```javascript
/* 人员能效页面 - Tab 切换 + 区间查询 + 月度查询 + 详情弹窗 */
(function () {
    'use strict';

    var STATE = {
        mode: 'daily',        // 'daily' | 'monthly'
        startDate: '',
        endDate: '',
        yearMonth: '',
        sort_by: 'score',
        order: 'desc',
        items: [],
        teamStats: null,
        isAdmin: false,
    };

    var GRADE_CLASS = {
        '优秀': 'grade-excellent',
        '良好': 'grade-good',
        '一般': 'grade-average',
        '待改进': 'grade-poor',
    };

    var chartCodeTop, chartGradePie, chartTrend, chartMonthlyTrend;

    // ── 工具 ──────────────────────────────────────
    function fmtDate(d) {
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + day;
    }

    function yesterday() {
        var d = new Date();
        d.setDate(d.getDate() - 1);
        return fmtDate(d);
    }

    function currentMonth() {
        var d = new Date();
        var y = d.getFullYear();
        var m = String(d.getMonth() + 1).padStart(2, '0');
        return y + '-' + m;
    }

    function gradeBadge(grade) {
        var cls = GRADE_CLASS[grade] || 'grade-none';
        return '<span class="grade-badge ' + cls + '">' + (grade || '-') + '</span>';
    }

    function escapeHtml(s) {
        if (s == null) return '';
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ── Tab 切换 ──────────────────────────────────
    function switchTab(mode) {
        STATE.mode = mode;
        document.querySelectorAll('.efficiency-tabs .tab').forEach(function (t) {
            t.classList.toggle('active', t.dataset.mode === mode);
        });
        document.getElementById('filterDaily').style.display = mode === 'daily' ? '' : 'none';
        document.getElementById('filterMonthly').style.display = mode === 'monthly' ? '' : 'none';
        loadData();
    }

    // ── 数据加载 ─────────────────────────────────
    function loadData() {
        if (STATE.mode === 'daily') {
            loadDailyList();
        } else {
            loadMonthlyList();
        }
    }

    function loadDailyList() {
        var params = new URLSearchParams({
            start_date: STATE.startDate,
            end_date: STATE.endDate,
            sort_by: STATE.sort_by,
            order: STATE.order,
            limit: '500',
        });
        apiRequest('/api/efficiency/list?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    renderEmpty('数据加载失败');
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (!json) return;
                if (!json.success) {
                    renderEmpty(json.message || '加载失败');
                    return;
                }
                STATE.items = json.data.items || [];
                STATE.teamStats = json.data.team_stats || {};
                renderStats();
                renderTable();
                renderCharts();
            })
            .catch(function (err) {
                console.error('loadDailyList failed:', err);
                renderEmpty('网络异常，请稍后重试');
            });
    }

    function loadMonthlyList() {
        var params = new URLSearchParams({
            year_month: STATE.yearMonth,
            sort_by: STATE.sort_by,
            order: STATE.order,
            limit: '500',
        });
        apiRequest('/api/efficiency/monthly/list?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    renderEmpty('数据加载失败');
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (!json) return;
                if (!json.success) {
                    renderEmpty(json.message || '加载失败');
                    return;
                }
                STATE.items = json.data.items || [];
                STATE.teamStats = json.data.team_stats || {};
                renderStats();
                renderMonthlyTable();
                renderCharts();
            })
            .catch(function (err) {
                console.error('loadMonthlyList failed:', err);
                renderEmpty('网络异常，请稍后重试');
            });
    }

    // ── 渲染：团队概览 ──────────────────────────
    function renderStats() {
        var s = STATE.teamStats || {};
        document.getElementById('stat-commits').textContent = s.total_commits != null ? s.total_commits : '-';
        document.getElementById('stat-add').textContent = '+' + (s.total_additions || 0);
        document.getElementById('stat-del').textContent = '-' + (s.total_deletions || 0);
        document.getElementById('stat-avg').textContent = s.avg_score != null ? s.avg_score : '-';
        document.getElementById('stat-count').textContent = s.person_count || 0;
    }

    // ── 渲染：表格 ───────────────────────────────
    function renderEmpty(msg) {
        var cols = STATE.mode === 'daily' ? 9 : 10;
        document.getElementById('efficiencyTbody').innerHTML =
            '<tr><td colspan="' + cols + '" class="text-center text-muted py-4">' + msg + '</td></tr>';
    }

    function renderTable() {
        // 按天模式表格（区间汇总）
        if (!STATE.items.length) {
            var hint = STATE.isAdmin ? '请点击右上方"立即补算"。' : '请联系管理员。';
            renderEmpty('该日期范围无数据。' + hint);
            return;
        }
        var rows = STATE.items.map(function (it) {
            return '<tr data-email="' + escapeHtml(it.author_email) + '">' +
                '<td>' + escapeHtml(it.author_name) + '</td>' +
                '<td><span class="text-muted small">' + escapeHtml(it.author_email) + '</span></td>' +
                '<td>' + escapeHtml(it.commits_count) + '</td>' +
                '<td class="text-success">+' + escapeHtml(it.additions) + '</td>' +
                '<td class="text-danger">-' + escapeHtml(it.deletions) + '</td>' +
                '<td>' + escapeHtml(it.files_changed) + '</td>' +
                '<td>' + (it.review_score != null ? escapeHtml(it.review_score) : '-') + '</td>' +
                '<td>' + gradeBadge(it.review_grade) + '</td>' +
                '<td><span class="text-muted small">' +
                escapeHtml((it.projects_involved || []).join('，')) + '</span></td>' +
                '</tr>';
        }).join('');
        document.getElementById('efficiencyTbody').innerHTML = rows;

        // 区间模式：点击行 → 弹窗显示每日明细
        var isRange = STATE.startDate !== STATE.endDate;
        document.querySelectorAll('#efficiencyTbody tr[data-email]').forEach(function (tr) {
            tr.addEventListener('click', function () {
                if (isRange) {
                    openRangeDetailModal(tr.dataset.email);
                } else {
                    openDrawer(tr.dataset.email);
                }
            });
        });
    }

    function renderMonthlyTable() {
        // 按月模式表格
        if (!STATE.items.length) {
            renderEmpty('该月无数据。请点击"立即补算"生成月度汇总。');
            return;
        }
        var rows = STATE.items.map(function (it) {
            return '<tr data-email="' + escapeHtml(it.author_email) + '">' +
                '<td>' + escapeHtml(it.author_name) + '</td>' +
                '<td><span class="text-muted small">' + escapeHtml(it.author_email) + '</span></td>' +
                '<td>' + escapeHtml(it.active_days) + '</td>' +
                '<td>' + escapeHtml(it.commits_count) + '</td>' +
                '<td class="text-success">+' + escapeHtml(it.additions) + '</td>' +
                '<td class="text-danger">-' + escapeHtml(it.deletions) + '</td>' +
                '<td>' + escapeHtml(it.files_changed) + '</td>' +
                '<td>' + (it.review_score != null ? escapeHtml(it.review_score) : '-') + '</td>' +
                '<td>' + gradeBadge(it.review_grade) + '</td>' +
                '<td><span class="text-muted small">' +
                escapeHtml((it.projects_involved || []).join('，')) + '</span></td>' +
                '</tr>';
        }).join('');
        document.getElementById('efficiencyTbody').innerHTML = rows;

        // 点击行 → 月度详情弹窗
        document.querySelectorAll('#efficiencyTbody tr[data-email]').forEach(function (tr) {
            tr.addEventListener('click', function () {
                openMonthlyDetailModal(tr.dataset.email);
            });
        });
    }

    // ── 渲染：ECharts ────────────────────────────
    function renderCharts() {
        renderCodeTopChart();
        renderGradePieChart();
    }

    function renderCodeTopChart() {
        if (!chartCodeTop) {
            chartCodeTop = echarts.init(document.getElementById('chartCodeTop'));
        }
        var top = STATE.items.slice()
            .sort(function (a, b) { return (b.additions + b.deletions) - (a.additions + a.deletions); })
            .slice(0, 10).reverse();
        chartCodeTop.setOption({
            title: { text: '代码量 TOP 10', left: 'left', textStyle: { fontSize: 14 } },
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            legend: { right: 10, data: ['新增', '删除'] },
            grid: { left: 100, right: 30, top: 50, bottom: 20 },
            xAxis: { type: 'value' },
            yAxis: { type: 'category', data: top.map(function (t) { return t.author_name; }) },
            series: [
                { name: '新增', type: 'bar', stack: 'total', color: '#28a745',
                  data: top.map(function (t) { return t.additions; }) },
                { name: '删除', type: 'bar', stack: 'total', color: '#dc3545',
                  data: top.map(function (t) { return t.deletions; }) },
            ],
        });
    }

    function renderGradePieChart() {
        if (!chartGradePie) {
            chartGradePie = echarts.init(document.getElementById('chartGradePie'));
        }
        var buckets = { '优秀': 0, '良好': 0, '一般': 0, '待改进': 0, '未评': 0 };
        STATE.items.forEach(function (it) {
            var key = it.review_grade || '未评';
            buckets[key] = (buckets[key] || 0) + 1;
        });
        var pieData = Object.keys(buckets)
            .filter(function (k) { return buckets[k] > 0; })
            .map(function (k) { return { name: k, value: buckets[k] }; });
        chartGradePie.setOption({
            title: { text: '评分分布', left: 'left', textStyle: { fontSize: 14 } },
            tooltip: { trigger: 'item' },
            color: ['#28a745', '#0d6efd', '#ffc107', '#dc3545', '#999'],
            series: [{
                type: 'pie', radius: ['40%', '70%'], center: ['50%', '55%'],
                data: pieData,
                label: { formatter: '{b}: {c}' },
            }],
        });
    }

    // ── 区间明细弹窗 ─────────────────────────────
    function openRangeDetailModal(email) {
        var modal = document.getElementById('rangeDetailModal');
        modal.classList.add('active');
        document.getElementById('rangeModalTitle').textContent =
            email + ' (' + STATE.startDate + ' ~ ' + STATE.endDate + ')';
        document.getElementById('rangeModalBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        var params = new URLSearchParams({
            email: email,
            start_date: STATE.startDate,
            end_date: STATE.endDate,
        });
        apiRequest('/api/efficiency/range/detail?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    document.getElementById('rangeModalBody').innerHTML =
                        '<div class="text-danger">加载失败</div>';
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (json) renderRangeDetailModal(json.data || {});
            })
            .catch(function () {
                document.getElementById('rangeModalBody').innerHTML =
                    '<div class="text-danger">网络异常</div>';
            });
    }

    function renderRangeDetailModal(data) {
        var summary = data.summary || {};
        var details = data.daily_details || [];

        var summaryHtml =
            '<div style="display:flex; gap:var(--space-4); margin-bottom:var(--space-4); flex-wrap:wrap;">' +
            '  <span>提交：<strong>' + (summary.commits_count || 0) + '</strong> 次</span>' +
            '  <span>代码：<strong class="text-success">+' + (summary.additions || 0) + '</strong>' +
            '  / <strong class="text-danger">-' + (summary.deletions || 0) + '</strong></span>' +
            '  <span>平均分：<strong>' + (summary.review_score_avg != null ? summary.review_score_avg : '-') + '</strong></span>' +
            '</div>';

        var tableRows = details.map(function (d) {
            return '<tr data-email="' + escapeHtml(data.author_email) +
                '" data-date="' + escapeHtml(d.stat_date) + '">' +
                '<td>' + escapeHtml(d.stat_date) + '</td>' +
                '<td>' + escapeHtml(d.commits_count) + '</td>' +
                '<td class="text-success">+' + escapeHtml(d.additions) + '</td>' +
                '<td class="text-danger">-' + escapeHtml(d.deletions) + '</td>' +
                '<td>' + (d.review_score != null ? escapeHtml(d.review_score) : '-') + '</td>' +
                '<td>' + gradeBadge(d.review_grade) + '</td>' +
                '<td><button class="btn btn-sm btn-secondary btn-detail">详情</button></td>' +
                '</tr>';
        }).join('');

        var tableHtml =
            '<table class="daily-detail-table">' +
            '<thead><tr>' +
            '<th>日期</th><th>提交</th><th>新增</th><th>删除</th><th>评分</th><th>等级</th><th>操作</th>' +
            '</tr></thead>' +
            '<tbody>' + (tableRows || '<tr><td colspan="7" class="text-muted">无数据</td></tr>') + '</tbody>' +
            '</table>';

        document.getElementById('rangeModalBody').innerHTML = summaryHtml + tableHtml;

        // 行内"详情"按钮 → 关闭弹窗，打开抽屉
        document.querySelectorAll('#rangeDetailModal .btn-detail').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                var tr = btn.closest('tr');
                var em = tr.dataset.email;
                var dt = tr.dataset.date;
                closeRangeDetailModal();
                // 临时设置日期用于抽屉查询
                STATE.startDate = dt;
                STATE.endDate = dt;
                openDrawer(em);
            });
        });
    }

    function closeRangeDetailModal() {
        document.getElementById('rangeDetailModal').classList.remove('active');
    }

    // ── 月度详情弹窗 ─────────────────────────────
    function openMonthlyDetailModal(email) {
        var modal = document.getElementById('monthlyDetailModal');
        modal.classList.add('active');
        document.getElementById('monthlyModalTitle').textContent =
            email + ' ' + STATE.yearMonth + ' 月度详情';
        document.getElementById('monthlyModalBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        var params = new URLSearchParams({
            email: email,
            year_month: STATE.yearMonth,
        });
        apiRequest('/api/efficiency/monthly/detail?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    document.getElementById('monthlyModalBody').innerHTML =
                        '<div class="text-danger">加载失败</div>';
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (json) renderMonthlyDetailModal(json.data || {});
            })
            .catch(function () {
                document.getElementById('monthlyModalBody').innerHTML =
                    '<div class="text-danger">网络异常</div>';
            });
    }

    function renderMonthlyDetailModal(data) {
        var s = data.summary || {};
        var trend = data.daily_trend || [];
        var work = s.work_summary || [];

        var workHtml = work.length
            ? work.map(function (w) { return '<li>' + escapeHtml(w) + '</li>'; }).join('')
            : '<li class="text-muted">无</li>';

        var html =
            '<div class="mb-3" style="display:flex; gap:var(--space-4); align-items:center;">' +
            '  <div style="font-size:2rem; font-weight:700; color:var(--color-primary);">' +
            (s.review_score != null ? escapeHtml(s.review_score) : '-') + '</div>' +
            '  ' + gradeBadge(s.review_grade) +
            '  <span class="text-muted">活跃 ' + (s.active_days || 0) + ' 天</span>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">月度评分简述</div>' +
            '  <div>' + escapeHtml(s.review_summary || '-') + '</div>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">月度主要工作</div>' +
            '  <ol class="mb-0">' + workHtml + '</ol>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">每日趋势</div>' +
            '  <div id="chartMonthlyTrend" style="width:100%;height:280px;"></div>' +
            '</div>';
        document.getElementById('monthlyModalBody').innerHTML = html;
        renderMonthlyTrendChart(trend);
    }

    function renderMonthlyTrendChart(trend) {
        if (chartMonthlyTrend) { chartMonthlyTrend.dispose(); chartMonthlyTrend = null; }
        var el = document.getElementById('chartMonthlyTrend');
        if (!el) return;
        chartMonthlyTrend = echarts.init(el);
        chartMonthlyTrend.setOption({
            tooltip: { trigger: 'axis' },
            legend: { data: ['新增', '删除', '评分'] },
            grid: { left: 40, right: 40, top: 30, bottom: 30 },
            xAxis: { type: 'category', data: trend.map(function (t) { return t.stat_date; }) },
            yAxis: [
                { type: 'value', name: '代码量' },
                { type: 'value', name: '评分', min: 0, max: 100 },
            ],
            series: [
                { name: '新增', type: 'line', color: '#28a745',
                  data: trend.map(function (t) { return t.additions; }) },
                { name: '删除', type: 'line', color: '#dc3545',
                  data: trend.map(function (t) { return t.deletions; }) },
                { name: '评分', type: 'line', yAxisIndex: 1, color: '#0d6efd',
                  data: trend.map(function (t) { return t.review_score; }),
                  markLine: { data: [
                      { yAxis: 90, lineStyle: { color: '#28a745', type: 'dashed' } },
                      { yAxis: 60, lineStyle: { color: '#dc3545', type: 'dashed' } },
                  ] } },
            ],
        });
    }

    function closeMonthlyDetailModal() {
        document.getElementById('monthlyDetailModal').classList.remove('active');
        if (chartMonthlyTrend) { chartMonthlyTrend.dispose(); chartMonthlyTrend = null; }
    }

    // ── 详情抽屉（复用现有） ─────────────────────
    function openDrawer(email) {
        document.getElementById('drawerOverlay').classList.add('active');
        document.getElementById('detailDrawer').classList.add('active');
        document.getElementById('drawerTitle').textContent = email + ' (' + STATE.startDate + ')';
        document.getElementById('drawerBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        var params = new URLSearchParams({
            email: email,
            date: STATE.startDate,
            trend_days: '7',
        });
        apiRequest('/api/efficiency/detail?' + params.toString())
            .then(function (resp) {
                if (!resp || !resp.ok) {
                    document.getElementById('drawerBody').innerHTML =
                        '<div class="text-danger">加载详情失败</div>';
                    return;
                }
                return resp.json();
            })
            .then(function (json) {
                if (json) renderDrawer(json.data || {});
            })
            .catch(function (err) {
                console.error('openDrawer failed:', err);
                document.getElementById('drawerBody').innerHTML =
                    '<div class="text-danger">网络异常，请稍后重试</div>';
            });
    }

    function renderDrawer(data) {
        var s = data.summary || {};
        var work = s.work_summary || [];
        var commits = data.commits || [];
        var trend = data.trend || [];

        var workHtml = work.length
            ? work.map(function (w) { return '<li>' + escapeHtml(w) + '</li>'; }).join('')
            : '<li class="text-muted">无</li>';

        var commitHtml = commits.length
            ? commits.map(function (c) {
                return '<li class="mb-2">' +
                    '<code>' + c.commit_sha.substring(0, 8) + '</code> ' +
                    '<span class="badge bg-secondary">' + escapeHtml(c.branch) + '</span> ' +
                    '<span class="text-muted">' + c.commit_date + '</span>' +
                    '</li>';
            }).join('')
            : '<li class="text-muted">无</li>';

        var html =
            '<div class="mb-3">' +
            '  <strong>综合评分:</strong> ' + (s.review_score != null ? escapeHtml(s.review_score) : '-') +
            '  ' + gradeBadge(s.review_grade) +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">评分简述</div>' +
            '  <div>' + escapeHtml(s.review_summary || '-') + '</div>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small">今日主要工作</div>' +
            '  <ol class="mb-0">' + workHtml + '</ol>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">近 7 天趋势</div>' +
            '  <div id="chartTrend"></div>' +
            '</div>' +
            '<div class="mb-3">' +
            '  <div class="text-muted small mb-2">今日提交 (' + commits.length + ')</div>' +
            '  <ul class="list-unstyled small">' + commitHtml + '</ul>' +
            '</div>';
        document.getElementById('drawerBody').innerHTML = html;
        renderTrendChart(trend);
    }

    function renderTrendChart(trend) {
        if (chartTrend) { chartTrend.dispose(); chartTrend = null; }
        var el = document.getElementById('chartTrend');
        if (!el) return;
        chartTrend = echarts.init(el);
        chartTrend.setOption({
            tooltip: { trigger: 'axis' },
            legend: { data: ['新增', '删除', '评分'] },
            grid: { left: 40, right: 40, top: 30, bottom: 30 },
            xAxis: { type: 'category', data: trend.map(function (t) { return t.stat_date; }) },
            yAxis: [
                { type: 'value', name: '代码量' },
                { type: 'value', name: '评分', min: 0, max: 100 },
            ],
            series: [
                { name: '新增', type: 'line', color: '#28a745',
                  data: trend.map(function (t) { return t.additions; }) },
                { name: '删除', type: 'line', color: '#dc3545',
                  data: trend.map(function (t) { return t.deletions; }) },
                { name: '评分', type: 'line', yAxisIndex: 1, color: '#0d6efd',
                  data: trend.map(function (t) { return t.review_score; }),
                  markLine: { data: [
                      { yAxis: 90, lineStyle: { color: '#28a745', type: 'dashed' } },
                      { yAxis: 60, lineStyle: { color: '#dc3545', type: 'dashed' } },
                  ] } },
            ],
        });
    }

    function closeDrawer() {
        document.getElementById('drawerOverlay').classList.remove('active');
        document.getElementById('detailDrawer').classList.remove('active');
        if (chartTrend) { chartTrend.dispose(); chartTrend = null; }
    }

    // ── 排序交互 ─────────────────────────────────
    function bindSort() {
        document.querySelectorAll('#efficiencyTable th[data-sort]').forEach(function (th) {
            th.addEventListener('click', function () {
                var field = th.dataset.sort;
                if (STATE.sort_by === field) {
                    STATE.order = STATE.order === 'desc' ? 'asc' : 'desc';
                } else {
                    STATE.sort_by = field;
                    STATE.order = 'desc';
                }
                document.querySelectorAll('#efficiencyTable th').forEach(function (t) {
                    t.classList.remove('sorted');
                });
                th.classList.add('sorted');
                loadData();
            });
        });
    }

    // ── 补算按钮 ────────────────────────────────
    function checkAdminAndBindRecompute() {
        apiRequest('/api/auth/me')
            .then(function (resp) {
                if (!resp) return;
                return resp.json();
            })
            .then(function (data) {
                if (!data) return;
                STATE.isAdmin = data.roles && data.roles.indexOf('system_admin') !== -1;
                if (STATE.isAdmin) {
                    document.getElementById('btnRecompute').style.display = '';
                }
            })
            .catch(function (err) {
                console.error('checkAdmin failed:', err);
            });

        document.getElementById('btnRecompute').addEventListener('click', function () {
            if (STATE.mode === 'monthly') {
                // 月度补算
                if (!confirm('确认补算 ' + STATE.yearMonth + ' 的月度能效？')) return;
                var btn = document.getElementById('btnRecompute');
                btn.disabled = true;
                apiRequest('/api/efficiency/recompute_monthly', {
                    method: 'POST',
                    body: JSON.stringify({ year_month: STATE.yearMonth, force: false }),
                })
                    .then(function (r) {
                        if (r && r.ok) {
                            showNotification('月度补算完成', 'success');
                            loadData();
                        } else {
                            showNotification('月度补算失败', 'danger');
                        }
                    })
                    .catch(function () {
                        showNotification('补算请求失败', 'danger');
                    })
                    .finally(function () {
                        btn.disabled = false;
                    });
            } else {
                // 日度补算（使用结束日期）
                if (!confirm('确认补算 ' + STATE.endDate + ' 的人员能效？')) return;
                var btn = document.getElementById('btnRecompute');
                btn.disabled = true;
                apiRequest('/api/efficiency/recompute', {
                    method: 'POST',
                    body: JSON.stringify({ date: STATE.endDate, force: false }),
                })
                    .then(function (r) {
                        if (r && r.ok) {
                            showNotification('补算完成', 'success');
                            loadData();
                        } else {
                            showNotification('补算失败', 'danger');
                        }
                    })
                    .catch(function () {
                        showNotification('补算请求失败', 'danger');
                    })
                    .finally(function () {
                        btn.disabled = false;
                    });
            }
        });
    }

    // ── 窗口 resize ─────────────────────────────
    function handleResize() {
        if (chartCodeTop) chartCodeTop.resize();
        if (chartGradePie) chartGradePie.resize();
        if (chartTrend) chartTrend.resize();
        if (chartMonthlyTrend) chartMonthlyTrend.resize();
    }

    // ── 表头适配月度模式 ────────────────────────
    function updateTableHeader() {
        var thead = document.querySelector('#efficiencyTable thead tr');
        if (STATE.mode === 'monthly') {
            thead.innerHTML =
                '<th style="min-width:100px;">姓名</th>' +
                '<th style="min-width:160px;">邮箱</th>' +
                '<th style="min-width:70px;" data-sort="active_days">活跃天数 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="commits">提交 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="additions">新增 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="deletions">删除 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;" data-sort="files_changed">文件 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;" data-sort="score" class="sorted">评分 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;">等级</th>' +
                '<th style="min-width:140px;">涉及项目</th>';
        } else {
            thead.innerHTML =
                '<th style="min-width:100px;">姓名</th>' +
                '<th style="min-width:160px;">邮箱</th>' +
                '<th style="min-width:80px;" data-sort="commits">提交 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="additions">新增 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:80px;" data-sort="deletions">删除 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;" data-sort="files_changed">文件 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;" data-sort="score" class="sorted">评分 <i class="bi bi-arrow-down-up sort-icon"></i></th>' +
                '<th style="min-width:70px;">等级</th>' +
                '<th style="min-width:140px;">涉及项目</th>';
        }
        bindSort();
    }

    // ── 入口 ─────────────────────────────────────
    document.addEventListener('DOMContentLoaded', function () {
        // 初始化日期
        STATE.startDate = yesterday();
        STATE.endDate = yesterday();
        STATE.yearMonth = currentMonth();

        document.getElementById('startDate').value = STATE.startDate;
        document.getElementById('endDate').value = STATE.endDate;
        document.getElementById('filterMonth').value = STATE.yearMonth;

        // Tab 切换
        document.getElementById('tabDaily').addEventListener('click', function () {
            updateTableHeader();
            switchTab('daily');
        });
        document.getElementById('tabMonthly').addEventListener('click', function () {
            updateTableHeader();
            switchTab('monthly');
        });

        // 日期变化
        document.getElementById('startDate').addEventListener('change', function (e) {
            STATE.startDate = e.target.value || yesterday();
            loadData();
        });
        document.getElementById('endDate').addEventListener('change', function (e) {
            STATE.endDate = e.target.value || yesterday();
            loadData();
        });
        document.getElementById('filterMonth').addEventListener('change', function (e) {
            STATE.yearMonth = e.target.value || currentMonth();
            loadData();
        });

        document.getElementById('btnRefresh').addEventListener('click', loadData);

        // 弹窗关闭
        document.getElementById('rangeModalClose').addEventListener('click', closeRangeDetailModal);
        document.getElementById('rangeDetailModal').addEventListener('click', function (e) {
            if (e.target === this) closeRangeDetailModal();
        });
        document.getElementById('monthlyModalClose').addEventListener('click', closeMonthlyDetailModal);
        document.getElementById('monthlyDetailModal').addEventListener('click', function (e) {
            if (e.target === this) closeMonthlyDetailModal();
        });

        // 抽屉关闭
        document.getElementById('drawerClose').addEventListener('click', closeDrawer);
        document.getElementById('drawerOverlay').addEventListener('click', closeDrawer);

        bindSort();
        checkAdminAndBindRecompute();
        loadData();
        window.addEventListener('resize', throttle(handleResize, 150));
    });
})();
```

- [ ] **Step 3: 手动验证前端功能**

启动开发服务器，访问人员能效页面，验证:
1. Tab 切换正常
2. 按天模式下日期区间选择正常
3. 按月模式下月份选择正常
4. 表格数据加载正常

- [ ] **Step 4: 提交**

```bash
git add app/templates/efficiency.html app/static/js/efficiency.py
git commit -m "feat: add tab switching, date range picker, and modal dialogs"
```

---

## Task 7: 月度补算 API 端点

**Files:**
- Modify: `app/api/efficiency.py`
- Test: `tests/test_api/test_efficiency_monthly.py` (追加)

- [ ] **Step 1: 追加月度补算测试**

在 `tests/test_api/test_efficiency_monthly.py` 末尾追加:

```python
# ── /monthly/recompute 测试 ──────────────────────

def test_monthly_recompute_requires_admin(client, db_session):
    """非管理员调用月度补算应返回 403"""
    from app.models.user import User, Role

    role = db_session.query(Role).filter_by(name="project_member").first()
    if not role:
        role = Role(name="project_member", description="m", is_system_role=True)
        db_session.add(role)
        db_session.commit()

    user = User(
        username="member", password_hash=security_service.hash_password("pass"),
        email="member@x.com", is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    user.roles.append(role)
    db_session.commit()

    app.dependency_overrides[get_current_user_full] = lambda: user
    resp = client.post(
        "/api/efficiency/monthly/recompute",
        json={"year_month": "2026-05"},
    )
    assert resp.status_code == 403
    app.dependency_overrides.pop(get_current_user_full, None)
```

- [ ] **Step 2: 实现月度补算端点**

在 `app/api/efficiency.py` 末尾追加:

```python
# ──────────────── /monthly/recompute ────────────────

class MonthlyRecomputeRequest(BaseModel):
    year_month: str
    force: bool = False


@router.post("/monthly/recompute")
async def monthly_recompute(
    body: MonthlyRecomputeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """管理员手动补算指定月份的月度能效数据"""
    if not current_user.is_system_admin():
        raise HTTPException(403, "仅系统管理员可补算月度数据")

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

    from app.models import Settings
    from app.security import security_service
    from app.services.efficiency_monthly_aggregator import EfficiencyMonthlyAggregator

    settings = db.query(Settings).first()
    if not settings:
        raise HTTPException(400, "系统配置缺失")

    llm_cfg = {
        "api_url": settings.llm_api_url,
        "api_key": (
            security_service.decrypt(settings.llm_api_key)
            if settings.llm_api_key
            else ""
        ),
        "model": settings.llm_model,
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
        "retry_delay": settings.llm_retry_delay,
    }
    top_n = getattr(settings, "efficiency_work_summary_top_n", 10) or 10

    try:
        aggregator = EfficiencyMonthlyAggregator(
            db=db,
            llm_config=llm_cfg,
            top_n=top_n,
        )
        result = aggregator.aggregate(body.year_month)
        return ApiResponse(success=True, data=result)
    except Exception as e:
        logger.exception(f"月度补算 {body.year_month} 失败")
        raise HTTPException(500, f"月度补算失败: {e}")
```

- [ ] **Step 3: 运行全部月度测试**

运行: `pytest tests/test_api/test_efficiency_monthly.py -v`
预期: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add app/api/efficiency.py tests/test_api/test_efficiency_monthly.py
git commit -m "feat: add monthly recompute endpoint"
```

---

## Task 8: 集成验证与回归测试

**Files:**
- 无新文件

- [ ] **Step 1: 运行全部测试**

运行: `pytest tests/ -v`
预期: 全部 PASS（包括现有测试和新增测试）

- [ ] **Step 2: 验证数据库迁移**

运行: `python -c "from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly; print(EmployeeEfficiencyMonthly.__tablename__)"`
预期: `employee_efficiency_monthly`

- [ ] **Step 3: 验证 API 路由注册**

运行: `python -c "from app.api.efficiency import router; print([r.path for r in router.routes])"`
预期: 包含 `/monthly/list`、`/monthly/detail`、`/monthly/recompute`

- [ ] **Step 4: 提交最终状态**

```bash
git add -A
git commit -m "chore: final integration verification"
```

---

## 自检清单

- [ ] 所有新文件都有对应测试
- [ ] 所有测试通过
- [ ] 现有测试无回归
- [ ] JSON 解析使用防御性 `_safe_json_loads`
- [ ] LLM 调用串行 + 间隔 + 重试
- [ ] review_score 使用算术平均
- [ ] UPSERT 幂等
- [ ] 权限控制复用现有逻辑
- [ ] 前端 Tab 切换和弹窗交互完整

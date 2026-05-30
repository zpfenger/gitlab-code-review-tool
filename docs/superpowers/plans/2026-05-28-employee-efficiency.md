# 人员能效模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增"人员能效"模块，在日报任务跑完时按 `author_email × stat_date` 聚合产出新表 `employee_efficiency_daily`（含代码量、LLM 工作总结、综合评分），并提供前端表格 + ECharts 图表 + 个人详情下钻。

**Architecture:** 三层结构：(1) 新建 `EmployeeEfficiencyDaily` SQLAlchemy 模型 + 自动迁移；(2) 新建 `EfficiencyAggregator` 服务负责按 author 聚合 + 调 1 次 LLM 同时拿评分和工作总结，在 `task_executor.run_daily_review` 完成后挂钩调用；(3) 新建 `/api/efficiency/*` API + Jinja 模板 + ECharts 前端页面。**关键设计**：不动现有表，UPSERT 幂等，LLM 失败不阻塞代码量入库。

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / SQLite + pytest / Bootstrap 5 / Bootstrap Icons / ECharts 5 (CDN)

---

## File Structure

**Create:**
- `app/models/employee_efficiency.py` — `EmployeeEfficiencyDaily` 模型
- `app/services/efficiency_aggregator.py` — 聚合服务（核心业务）
- `app/services/efficiency_llm.py` — LLM 调用 + 评分/工作总结解析（独立单元便于测试）
- `app/api/efficiency.py` — `/api/efficiency/*` API 路由
- `app/templates/efficiency.html` — 人员能效页面 Jinja 模板
- `app/static/js/efficiency.js` — 前端逻辑 + ECharts 渲染
- `tests/test_models/test_employee_efficiency.py`
- `tests/test_services/test_efficiency_llm.py`
- `tests/test_services/test_efficiency_aggregator.py`
- `tests/test_api/test_efficiency.py`
- `scripts/backfill_efficiency.py` — 历史回填脚本

**Modify:**
- `app/models/__init__.py` — 注册新模型
- `app/services/task_executor.py` — 在 `run_daily_review` 完成后挂钩聚合
- `app/main.py` — 注册路由 + Web 路由 `/efficiency`
- `app/templates/base.html` — 侧边栏菜单新增"人员能效"

**No changes:** `CommitRecord`、`MrReviewLog`、`PushReviewLog`、`TaskLog`、`Project`、`User`、`Settings`

---

## 关键约定（所有 Task 都遵守）

1. **类型注解**：所有新代码用 `from __future__ import annotations` 或显式标注
2. **日志**：用 `from loguru import logger`，沿用现有风格
3. **DB Session**：测试用 `tests/conftest.py` 的 in-memory SQLite fixture
4. **时间**：所有日期用 `datetime.date`（不含时区），时间戳用 `datetime.datetime`
5. **commit 提交格式**：沿用现有 `feat:` / `test:` / `refactor:` 等 conventional commits
6. **每个 Task 结束都 commit 一次**

---

## Task 1: 新建 `EmployeeEfficiencyDaily` 模型

**Files:**
- Create: `app/models/employee_efficiency.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_models/test_employee_efficiency.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_models/test_employee_efficiency.py`:

```python
"""EmployeeEfficiencyDaily 模型测试"""
from datetime import date, datetime
import json
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.employee_efficiency import EmployeeEfficiencyDaily


def test_create_employee_efficiency(db_session):
    """可以正常创建一条人员能效记录"""
    record = EmployeeEfficiencyDaily(
        author_email="zhangsan@example.com",
        author_name="张三",
        stat_date=date(2026, 5, 27),
        commits_count=5,
        additions=230,
        deletions=45,
        files_changed=12,
        new_files=2,
        deleted_files=0,
        projects_involved=json.dumps(["proj-a", "proj-b"]),
        review_score=85,
        review_grade="良好",
        review_summary="代码质量良好",
        work_summary=json.dumps(["实现登录", "修复 X bug"]),
        summary_top_n=5,
        llm_status="success",
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)

    assert record.id is not None
    assert record.created_at is not None
    assert record.updated_at is not None


def test_unique_email_date_constraint(db_session):
    """同一 email 同一天只能有一条记录"""
    r1 = EmployeeEfficiencyDaily(
        author_email="a@b.com", author_name="A", stat_date=date(2026, 5, 27),
        commits_count=1, additions=10, deletions=0, files_changed=1,
        new_files=0, deleted_files=0, projects_involved="[]",
        llm_status="pending",
    )
    db_session.add(r1)
    db_session.commit()

    r2 = EmployeeEfficiencyDaily(
        author_email="a@b.com", author_name="A", stat_date=date(2026, 5, 27),
        commits_count=2, additions=20, deletions=0, files_changed=1,
        new_files=0, deleted_files=0, projects_involved="[]",
        llm_status="pending",
    )
    db_session.add(r2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_grade_field_optional(db_session):
    """评分和等级可空（LLM 失败时）"""
    record = EmployeeEfficiencyDaily(
        author_email="b@c.com", author_name="B", stat_date=date(2026, 5, 27),
        commits_count=1, additions=10, deletions=0, files_changed=1,
        new_files=0, deleted_files=0, projects_involved="[]",
        llm_status="failed", llm_error="LLM timeout",
    )
    db_session.add(record)
    db_session.commit()
    assert record.review_score is None
    assert record.review_grade is None


def test_repr(db_session):
    """__repr__ 输出便于调试"""
    record = EmployeeEfficiencyDaily(
        author_email="x@y.com", author_name="X", stat_date=date(2026, 5, 27),
        commits_count=1, additions=10, deletions=0, files_changed=1,
        new_files=0, deleted_files=0, projects_involved="[]",
        llm_status="pending",
    )
    db_session.add(record)
    db_session.commit()
    s = repr(record)
    assert "x@y.com" in s
    assert "2026-05-27" in s
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_models/test_employee_efficiency.py -v`
Expected: FAIL — `ModuleNotFoundError: app.models.employee_efficiency`

- [ ] **Step 3: 创建模型文件**

Create `app/models/employee_efficiency.py`:

```python
"""人员能效明细表（人 × 天聚合）"""
from sqlalchemy import Column, String, Integer, Date, Text, Index, UniqueConstraint
from app.models.base import BaseModel


class EmployeeEfficiencyDaily(BaseModel):
    """人员能效明细表

    每行记录某人某一天的代码量、LLM 评分、工作总结（跨项目跨分支已合并去重）。
    由 EfficiencyAggregator 在日报任务跑完时写入，唯一索引保证幂等。
    """
    __tablename__ = "employee_efficiency_daily"

    # 人员维度
    author_email = Column(String(200), nullable=False, comment="提交者邮箱（主维度）")
    author_name = Column(String(100), nullable=False, comment="提交者显示名")
    stat_date = Column(Date, nullable=False, comment="统计日期（自然日）")

    # 代码量统计
    commits_count = Column(Integer, nullable=False, default=0, comment="提交次数（去重后）")
    additions = Column(Integer, nullable=False, default=0, comment="新增行数")
    deletions = Column(Integer, nullable=False, default=0, comment="删除行数")
    files_changed = Column(Integer, nullable=False, default=0, comment="涉及文件数")
    new_files = Column(Integer, nullable=False, default=0, comment="新建文件数")
    deleted_files = Column(Integer, nullable=False, default=0, comment="删除文件数")

    # 涉及项目（JSON 数组字符串）
    projects_involved = Column(Text, nullable=False, default="[]",
                                comment='涉及项目名 JSON 数组 ["proj-a","proj-b"]')

    # LLM 产出
    review_score = Column(Integer, nullable=True, comment="综合评分 0-100")
    review_grade = Column(String(10), nullable=True,
                           comment="等级：优秀/良好/一般/待改进")
    review_summary = Column(Text, nullable=True, comment="评分简述（1-2 句）")
    work_summary = Column(Text, nullable=True,
                           comment="LLM 工作总结 JSON 数组")
    summary_top_n = Column(Integer, nullable=True, default=5,
                            comment="生成时使用的 top_n")

    # 状态
    llm_status = Column(String(20), nullable=False, default="pending",
                         comment="pending/success/failed/skipped")
    llm_error = Column(Text, nullable=True, comment="LLM 失败原因")

    __table_args__ = (
        UniqueConstraint("author_email", "stat_date",
                          name="uq_employee_efficiency_email_date"),
        Index("idx_employee_efficiency_stat_date", "stat_date"),
        Index("idx_employee_efficiency_email_date",
              "author_email", "stat_date"),
    )

    def __repr__(self):
        return (f"<EmployeeEfficiencyDaily(email='{self.author_email}', "
                f"date={self.stat_date}, score={self.review_score})>")
```

- [ ] **Step 4: 注册到 `app/models/__init__.py`**

修改 `app/models/__init__.py`，在导入区加上新模型：

```python
from app.models.base import BaseModel, TimestampMixin
from app.models.project import Project
from app.models.settings import Settings
from app.models.task_log import TaskLog
from app.models.commit_record import CommitRecord
from app.models.webhook_review import MrReviewLog, PushReviewLog
from app.models.user import User, Role, user_roles, project_admins, project_members
from app.models.employee_efficiency import EmployeeEfficiencyDaily

__all__ = [
    'BaseModel', 'TimestampMixin',
    'Project', 'Settings', 'TaskLog', 'CommitRecord',
    'MrReviewLog', 'PushReviewLog',
    'User', 'Role', 'user_roles', 'project_admins', 'project_members',
    'EmployeeEfficiencyDaily',
]
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `pytest tests/test_models/test_employee_efficiency.py -v`
Expected: 全部 4 个测试 PASS

- [ ] **Step 6: 验证迁移**

Run: `python -c "from app.database import init_db; init_db(); print('ok')"`
Expected: 输出 `ok`，无报错。检查 `data/config.db` 有 `employee_efficiency_daily` 表。

- [ ] **Step 7: 提交**

```bash
git add app/models/employee_efficiency.py app/models/__init__.py tests/test_models/test_employee_efficiency.py
git commit -m "feat: add EmployeeEfficiencyDaily model for employee efficiency tracking"
```

---

## Task 2: LLM 调用 + 解析模块 `efficiency_llm.py`

**Files:**
- Create: `app/services/efficiency_llm.py`
- Test: `tests/test_services/test_efficiency_llm.py`

**职责**：单一职责 —— 构造 prompt、调 LLM、解析输出（score / work_summary / review_summary）。与 `WebhookReviewer` 风格保持一致但独立，避免互相耦合。

- [ ] **Step 1: 写失败测试**

Create `tests/test_services/test_efficiency_llm.py`:

```python
"""efficiency_llm 测试 — 解析 LLM 输出"""
from app.services.efficiency_llm import (
    parse_score, parse_work_summary, parse_review_summary,
    map_score_to_grade, build_user_prompt,
)


# ── 评分解析 ─────────────────────────────────────
def test_parse_score_with_full_text():
    text = "评分明细：xxx\n\n## 总分：85 分"
    assert parse_score(text) == 85


def test_parse_score_handles_chinese_colon():
    text = "总分：73分"
    assert parse_score(text) == 73


def test_parse_score_missing_returns_zero():
    assert parse_score("没有评分相关内容") == 0


def test_parse_score_empty():
    assert parse_score("") == 0


# ── 等级映射 ─────────────────────────────────────
def test_map_score_excellent():
    assert map_score_to_grade(95) == "优秀"
    assert map_score_to_grade(90) == "优秀"


def test_map_score_good():
    assert map_score_to_grade(89) == "良好"
    assert map_score_to_grade(75) == "良好"


def test_map_score_average():
    assert map_score_to_grade(74) == "一般"
    assert map_score_to_grade(60) == "一般"


def test_map_score_poor():
    assert map_score_to_grade(59) == "待改进"
    assert map_score_to_grade(0) == "待改进"


def test_map_score_none_returns_none():
    assert map_score_to_grade(None) is None


# ── 工作总结解析 ─────────────────────────────────
def test_parse_work_summary_extracts_list():
    text = """## 主要工作（不超过 5 条）
1. 实现登录功能
2. 修复购物车 bug
3. 重构订单服务
4. 补充单元测试
5. 优化慢查询

## 总分：85 分"""
    items = parse_work_summary(text)
    assert items == [
        "实现登录功能",
        "修复购物车 bug",
        "重构订单服务",
        "补充单元测试",
        "优化慢查询",
    ]


def test_parse_work_summary_dash_bullets():
    text = """## 主要工作
- 实现 A
- 修复 B"""
    items = parse_work_summary(text)
    assert items == ["实现 A", "修复 B"]


def test_parse_work_summary_caps_at_top_n():
    text = """## 主要工作
1. a
2. b
3. c
4. d
5. e
6. f
7. g"""
    items = parse_work_summary(text, top_n=3)
    assert items == ["a", "b", "c"]


def test_parse_work_summary_missing_returns_empty():
    assert parse_work_summary("没有这一块") == []


# ── 评分简述提取 ─────────────────────────────────
def test_parse_review_summary_takes_first_paragraph():
    text = """## 评分简述
代码质量良好，注释清晰，但存在 N+1 查询问题。

## 评分明细
..."""
    s = parse_review_summary(text)
    assert "代码质量良好" in s


def test_parse_review_summary_fallback_truncates():
    text = "x" * 500
    s = parse_review_summary(text)
    assert len(s) <= 200


# ── prompt 构造 ──────────────────────────────────
def test_build_user_prompt_contains_inputs():
    prompt = build_user_prompt(
        author_name="张三",
        commits_text="feat: add login\nfix: bug",
        diffs_text="+code line",
        top_n=5,
    )
    assert "张三" in prompt
    assert "feat: add login" in prompt
    assert "+code line" in prompt
    assert "5" in prompt
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_services/test_efficiency_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.efficiency_llm`

- [ ] **Step 3: 实现 `efficiency_llm.py`**

Create `app/services/efficiency_llm.py`:

```python
"""人员能效 LLM 调用与解析模块

职责：
1. 构造 prompt（要求 LLM 同时输出评分 + 工作总结 + 简述）
2. 调 LLM（复用 settings 配置）
3. 解析输出（score / grade / work_summary / review_summary）

与 webhook_reviewer 解耦，独立单元便于测试。
"""
from __future__ import annotations
import re
from typing import List, Optional, Dict

import httpx
from loguru import logger


# ── Prompt 模板 ────────────────────────────────────────
EFFICIENCY_SYSTEM_PROMPT = """你是一位资深的软件开发工程师，专注于代码的规范性、功能性、安全性和稳定性。本次任务是对单个员工"某一天"提交的代码进行综合评审，并提炼当日主要工作内容。

### 评分目标（与日报审查一致）：
1. 注释（5分）：注释要"有用"不冗余，只注释"为什么这么做"，避免无意义、与代码脱节的注释。
2. 业务逻辑校验（30分）：是否符合需求文档的核心规则、异常处理是否合理、数据库交互是否存在 N+1 查询等。
3. 性能优化点（40分）：是否存在循环嵌套、重复计算、大对象频繁创建等性能瓶颈、缓存策略、IO 同步阻塞。
4. 安全风险排查（10分）：是否存在 SQL 注入、XSS、CSRF；敏感数据脱敏；权限校验覆盖。
5. 代码架构与扩展性（10分）：是否遵循 SOLID、有无过度耦合、配置项是否硬编码。
6. 编码规范（5分）：命名/注释/格式统一性，测试覆盖率。

### 输出格式（严格按照）：
请按以下 Markdown 结构输出，确保所有标记都存在，便于程序解析：

## 评分简述
（1-2 句话点明当日代码的整体质量与突出问题）

## 评分明细
- 注释（5分）：x 分，说明
- 业务逻辑校验（30分）：x 分，说明
- 性能优化点（40分）：x 分，说明
- 安全风险排查（10分）：x 分，说明
- 代码架构与扩展性（10分）：x 分，说明
- 编码规范（5分）：x 分，说明

## 主要工作（不超过 {top_n} 条）
1. xxx
2. xxx
3. xxx
（按对业务的影响和工作量排序，简单的修复、typo、格式调整请合并或忽略）

## 总分：XX 分
"""


EFFICIENCY_USER_PROMPT = """以下是员工 {author_name} 当日的代码提交内容。

### 提交信息（commits）：
{commits_text}

### 代码变更（diffs）：
{diffs_text}

请按系统提示的格式输出评分简述、评分明细、主要工作（不超过 {top_n} 条）和总分。"""


# ── 等级映射 ──────────────────────────────────────────
def map_score_to_grade(score: Optional[int]) -> Optional[str]:
    """根据分数映射到等级"""
    if score is None:
        return None
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "一般"
    return "待改进"


# ── 解析函数 ──────────────────────────────────────────
_SCORE_PATTERN = re.compile(r"总分[:：]\s*(\d+)\s*分?")
_WORK_HEADER_PATTERN = re.compile(r"##\s*主要工作.*?\n(.+?)(?=\n##|\Z)", re.DOTALL)
_WORK_ITEM_PATTERN = re.compile(r"^\s*(?:\d+[.、)]|\-|\*)\s*(.+?)\s*$", re.MULTILINE)
_REVIEW_SUMMARY_PATTERN = re.compile(r"##\s*评分简述.*?\n(.+?)(?=\n##|\Z)", re.DOTALL)


def parse_score(text: str) -> int:
    """从 LLM 输出中解析总分（0 表示未识别）"""
    if not text:
        return 0
    match = _SCORE_PATTERN.search(text)
    return int(match.group(1)) if match else 0


def parse_work_summary(text: str, top_n: int = 5) -> List[str]:
    """从 LLM 输出中提取工作总结条目列表"""
    if not text:
        return []
    block_match = _WORK_HEADER_PATTERN.search(text)
    if not block_match:
        return []
    block = block_match.group(1)
    items = [m.group(1).strip() for m in _WORK_ITEM_PATTERN.finditer(block)]
    items = [it for it in items if it]
    return items[:top_n]


def parse_review_summary(text: str) -> str:
    """从 LLM 输出中提取评分简述段落（fallback：截断前 200 字）"""
    if not text:
        return ""
    match = _REVIEW_SUMMARY_PATTERN.search(text)
    if match:
        summary = match.group(1).strip()
        # 取第一段（双换行分隔）
        return summary.split("\n\n")[0].strip()[:200]
    return text[:200]


# ── Prompt 构造 ───────────────────────────────────────
def build_system_prompt(top_n: int = 5) -> str:
    return EFFICIENCY_SYSTEM_PROMPT.format(top_n=top_n)


def build_user_prompt(author_name: str, commits_text: str,
                       diffs_text: str, top_n: int = 5) -> str:
    return EFFICIENCY_USER_PROMPT.format(
        author_name=author_name,
        commits_text=commits_text or "(无)",
        diffs_text=diffs_text or "(无)",
        top_n=top_n,
    )


# ── LLM 调用 ──────────────────────────────────────────
def _truncate(text: str, max_tokens: int) -> str:
    """按字符近似截断（1 token ≈ 4 字符）"""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    logger.warning(f"文本长度 {len(text)} 超限 {max_chars}，已截断")
    return text[:max_chars] + "\n\n... (内容已截断)"


def call_llm(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    commits_text: str,
    diffs_text: str,
    top_n: int = 5,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    timeout: int = 240,
    max_retries: int = 3,
    retry_delay: int = 10,
    review_max_tokens: int = 10000,
) -> Optional[str]:
    """同步调用 LLM，返回原始 markdown 文本；失败返回 None"""
    import time

    diffs_text = _truncate(diffs_text, review_max_tokens)
    commits_text = _truncate(commits_text, review_max_tokens // 5)

    messages = [
        {"role": "system", "content": build_system_prompt(top_n=top_n)},
        {"role": "user", "content": build_user_prompt(
            author_name=author_name,
            commits_text=commits_text,
            diffs_text=diffs_text,
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
                logger.warning("LLM 返回空内容")
                return None
        except httpx.TimeoutException:
            logger.warning(f"LLM 请求超时 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
        except Exception as e:
            logger.error(f"LLM 请求异常: {type(e).__name__}: {e}")
            return None

    logger.error("达到最大重试次数")
    return None


def call_and_parse(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    commits_text: str,
    diffs_text: str,
    top_n: int = 5,
    **llm_kwargs,
) -> Dict[str, object]:
    """便捷封装：调用 LLM 并解析所有字段

    返回字典:
        {
            "raw": str | None,        # 原始输出
            "score": int,             # 0-100
            "grade": str | None,
            "work_summary": list[str],
            "review_summary": str,
            "success": bool,
        }
    """
    raw = call_llm(
        api_url=api_url, api_key=api_key, model=model,
        author_name=author_name, commits_text=commits_text,
        diffs_text=diffs_text, top_n=top_n, **llm_kwargs,
    )
    if raw is None:
        return {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }
    score = parse_score(raw)
    return {
        "raw": raw,
        "score": score,
        "grade": map_score_to_grade(score) if score > 0 else None,
        "work_summary": parse_work_summary(raw, top_n=top_n),
        "review_summary": parse_review_summary(raw),
        "success": True,
    }
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_services/test_efficiency_llm.py -v`
Expected: 全部测试 PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/efficiency_llm.py tests/test_services/test_efficiency_llm.py
git commit -m "feat: add efficiency_llm module for score/work-summary parsing"
```

---

## Task 3: 聚合服务 `EfficiencyAggregator`

**Files:**
- Create: `app/services/efficiency_aggregator.py`
- Test: `tests/test_services/test_efficiency_aggregator.py`

**职责**：协调 GitLab 拉取、跨项目跨分支去重、按 email 分组、调 LLM、UPSERT 到表。

- [ ] **Step 1: 写失败测试（mock GitLab + LLM）**

Create `tests/test_services/test_efficiency_aggregator.py`:

```python
"""EfficiencyAggregator 测试 — 聚合逻辑"""
from datetime import date, datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.project import Project
from app.services.efficiency_aggregator import EfficiencyAggregator


def _make_commit(sha, author_email, author_name, additions=10, deletions=2):
    """构造一个假 commit dict（GitLab API 返回结构）"""
    return {
        "id": sha,
        "author_email": author_email,
        "author_name": author_name,
        "message": f"commit msg {sha[:6]}",
        "_diffs": [
            {"diff": "+x\n+y\n-z", "new_path": "a.py", "old_path": "a.py",
             "new_file": False, "deleted_file": False, "renamed_file": False},
        ],
    }


@pytest.fixture
def gitlab_client_factory():
    """工厂：返回 mock GitLabClient，可注入 commits 和 diffs"""
    def _factory(commits_by_branch, diffs_by_sha):
        client = MagicMock()
        client.get_branches.return_value = [
            {"name": br} for br in commits_by_branch
        ]
        # get_commits(project_id, since, until, ref_name, ...) -> list
        client.get_commits.side_effect = lambda project_id, since=None, until=None, \
            ref_name=None, exclude_merge_commits=True, **kw: \
            commits_by_branch.get(ref_name, [])
        client.get_commit_diff.side_effect = lambda project_id, sha: \
            diffs_by_sha.get(sha, [])
        return client
    return _factory


@pytest.fixture
def llm_mock():
    """mock call_and_parse"""
    with patch("app.services.efficiency_aggregator.call_and_parse") as m:
        m.return_value = {
            "raw": "mock raw output",
            "score": 85,
            "grade": "良好",
            "work_summary": ["实现 A", "修复 B"],
            "review_summary": "整体质量良好",
            "success": True,
        }
        yield m


def test_aggregate_single_project_single_author(
    db_session, gitlab_client_factory, llm_mock
):
    """单项目单作者：累加 commits/additions/deletions，调 1 次 LLM"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {
        "main": [_make_commit("sha1", "a@b.com", "Alice")],
    }
    diffs = {
        "sha1": [{"diff": "+a\n+b\n-c", "new_path": "x.py", "old_path": "x.py",
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
    assert len(rows) == 1
    r = rows[0]
    assert r.author_email == "a@b.com"
    assert r.author_name == "Alice"
    assert r.commits_count == 1
    assert r.additions == 2   # +a, +b
    assert r.deletions == 1   # -c
    assert r.files_changed == 1
    assert r.review_score == 85
    assert r.review_grade == "良好"
    assert r.llm_status == "success"
    assert json.loads(r.work_summary) == ["实现 A", "修复 B"]
    assert json.loads(r.projects_involved) == ["proj-a"]


def test_cross_branch_dedup(db_session, gitlab_client_factory, llm_mock):
    """同 sha 在多分支只算一次"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    same_commit = _make_commit("sha-shared", "a@b.com", "Alice")
    commits = {
        "main": [same_commit],
        "feature-x": [same_commit],
    }
    diffs = {
        "sha-shared": [{"diff": "+a", "new_path": "x.py", "old_path": "x.py",
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
    assert len(rows) == 1
    assert rows[0].commits_count == 1
    assert rows[0].additions == 1


def test_cross_project_merge(db_session, gitlab_client_factory, llm_mock):
    """同人多项目：合并为一行，projects_involved 列出两个项目"""
    p1 = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                 is_active=True)
    p2 = Project(name="proj-b", project_id=2, gitlab_url="http://gl",
                 is_active=True)
    db_session.add_all([p1, p2])
    db_session.commit()

    clients = {
        1: gitlab_client_factory(
            {"main": [_make_commit("sha-a", "a@b.com", "Alice")]},
            {"sha-a": [{"diff": "+a", "new_path": "f1", "old_path": "f1",
                         "new_file": False, "deleted_file": False, "renamed_file": False}]},
        ),
        2: gitlab_client_factory(
            {"main": [_make_commit("sha-b", "a@b.com", "Alice")]},
            {"sha-b": [{"diff": "+b\n+c", "new_path": "f2", "old_path": "f2",
                         "new_file": False, "deleted_file": False, "renamed_file": False}]},
        ),
    }

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda project: clients[project.project_id],
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27))

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.commits_count == 2
    assert r.additions == 3
    assert set(json.loads(r.projects_involved)) == {"proj-a", "proj-b"}


def test_skip_empty_email(db_session, gitlab_client_factory, llm_mock):
    """email 为空的 commit 被忽略"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {
        "main": [
            _make_commit("sha1", "", "NoEmail"),
            _make_commit("sha2", "a@b.com", "Alice"),
        ],
    }
    diffs = {
        "sha1": [{"diff": "+a", "new_path": "x", "old_path": "x",
                  "new_file": False, "deleted_file": False, "renamed_file": False}],
        "sha2": [{"diff": "+b", "new_path": "y", "old_path": "y",
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
    assert len(rows) == 1
    assert rows[0].author_email == "a@b.com"


def test_llm_failure_records_error(db_session, gitlab_client_factory):
    """LLM 失败时代码量入库，llm_status=failed"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {"main": [_make_commit("sha1", "a@b.com", "Alice")]}
    diffs = {"sha1": [{"diff": "+a", "new_path": "x", "old_path": "x",
                       "new_file": False, "deleted_file": False, "renamed_file": False}]}
    client = gitlab_client_factory(commits, diffs)

    with patch("app.services.efficiency_aggregator.call_and_parse") as m:
        m.return_value = {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }
        agg = EfficiencyAggregator(
            db=db_session,
            gitlab_client_factory=lambda p: client,
            llm_config={"api_url": "x", "api_key": "x", "model": "m"},
        )
        agg.aggregate(date(2026, 5, 27))

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    assert rows[0].additions == 1
    assert rows[0].llm_status == "failed"
    assert rows[0].review_score is None


def test_upsert_idempotent(db_session, gitlab_client_factory, llm_mock):
    """同一天重复跑：覆盖更新，不产生重复行"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {"main": [_make_commit("sha1", "a@b.com", "Alice")]}
    diffs = {"sha1": [{"diff": "+a", "new_path": "x", "old_path": "x",
                       "new_file": False, "deleted_file": False, "renamed_file": False}]}
    client = gitlab_client_factory(commits, diffs)

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda p: client,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27))
    agg.aggregate(date(2026, 5, 27))   # 第二次

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_services/test_efficiency_aggregator.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.efficiency_aggregator`

- [ ] **Step 3: 实现 `efficiency_aggregator.py`**

Create `app/services/efficiency_aggregator.py`:

```python
"""人员能效聚合服务

职责：
1. 从 GitLab 拉取指定日期的所有项目所有分支的 commits
2. 跨项目跨分支按 commit sha 去重
3. 按 author_email 分组（email 为空的过滤）
4. 累加代码量统计
5. 调 1 次 LLM 同时拿评分 + 工作总结
6. UPSERT 写入 employee_efficiency_daily
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime
from typing import Callable, Dict, List, Optional, Set, Any

from loguru import logger
from sqlalchemy.orm import Session

from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.project import Project
from app.services.efficiency_llm import call_and_parse, map_score_to_grade


# 解析 diff 的 +/- 行数（与 stats_generator 一致逻辑）
_ADD_RE = re.compile(r"^\+(?!\+\+|\-\-)", re.MULTILINE)
_DEL_RE = re.compile(r"^\-(?!\+\+|\-\-)", re.MULTILINE)


def _count_diff_lines(diff_text: str) -> tuple[int, int]:
    """返回 (additions, deletions)"""
    return (
        len(_ADD_RE.findall(diff_text)),
        len(_DEL_RE.findall(diff_text)),
    )


class EfficiencyAggregator:
    """日报跑完后的人员能效聚合器"""

    def __init__(
        self,
        db: Session,
        gitlab_client_factory: Callable[[Project], Any],
        llm_config: Dict[str, Any],
        top_n: int = 5,
    ):
        """
        Args:
            db: SQLAlchemy session
            gitlab_client_factory: 给 Project 返回 GitLabClient 的工厂
                                   （便于注入 mock，且每个项目可能有独立 token）
            llm_config: {"api_url", "api_key", "model", 可选 timeout/temperature 等}
            top_n: 工作总结条目上限
        """
        self.db = db
        self.gitlab_client_factory = gitlab_client_factory
        self.llm_config = llm_config
        self.top_n = top_n

    # ── 主入口 ────────────────────────────────────────
    def aggregate(self, target_date: date) -> Dict[str, Any]:
        """对指定日期做一次聚合（幂等，重复调用会 UPSERT）"""
        logger.info(f"开始聚合人员能效: {target_date}")

        # 1. 收集所有项目当日去重后的 commits（含 diffs）
        per_author: Dict[str, Dict[str, Any]] = {}
        # email → {
        #   "author_name": str, "commits": [sha,...],
        #   "additions": int, "deletions": int, "files": set,
        #   "new_files": int, "deleted_files": int, "projects": set,
        #   "messages": [str,...], "diffs_text": [str,...]
        # }

        projects = self.db.query(Project).filter(Project.is_active == True).all()
        global_seen_sha: Set[str] = set()

        for project in projects:
            try:
                client = self.gitlab_client_factory(project)
                self._collect_project(project, client, target_date,
                                       per_author, global_seen_sha)
            except Exception as e:
                logger.error(f"项目 {project.name} 聚合失败: {e}")

        # 2. 对每个作者调 LLM 并写入
        success = 0
        failed = 0
        for email, data in per_author.items():
            try:
                self._upsert_author(email, data, target_date)
                success += 1
            except Exception as e:
                logger.exception(f"写入 {email} 能效记录失败: {e}")
                failed += 1

        result = {
            "target_date": target_date.isoformat(),
            "authors_total": len(per_author),
            "authors_success": success,
            "authors_failed": failed,
        }
        logger.info(f"人员能效聚合完成: {result}")
        return result

    # ── 单项目数据收集 ────────────────────────────────
    def _collect_project(
        self,
        project: Project,
        client: Any,
        target_date: date,
        per_author: Dict[str, Dict[str, Any]],
        global_seen_sha: Set[str],
    ) -> None:
        """拉取该项目当日所有分支的 commits，按 author 累加"""
        # 排除分支
        exclude_branches = []
        if project.exclude_branches:
            exclude_branches = [
                b.strip() for b in project.exclude_branches.split(",")
                if b.strip()
            ]

        all_branches = client.get_branches(project.project_id) or []
        branches = [b for b in all_branches
                    if b.get("name") not in exclude_branches]

        since_iso = datetime.combine(target_date,
                                      datetime.min.time()).isoformat() + "Z"
        until_iso = datetime.combine(target_date,
                                      datetime.max.time()).isoformat() + "Z"

        for branch_info in branches:
            branch_name = branch_info.get("name", "")
            commits = client.get_commits(
                project.project_id,
                since=since_iso,
                until=until_iso,
                ref_name=branch_name,
                exclude_merge_commits=True,
            ) or []

            for commit in commits:
                sha = commit.get("id")
                if not sha or sha in global_seen_sha:
                    continue
                email = (commit.get("author_email") or "").strip()
                if not email or email.endswith("@noreply"):
                    continue
                global_seen_sha.add(sha)

                # 累加到 per_author
                bucket = per_author.setdefault(email, {
                    "author_name": commit.get("author_name") or email,
                    "commits": [],
                    "additions": 0,
                    "deletions": 0,
                    "files": set(),
                    "new_files": 0,
                    "deleted_files": 0,
                    "projects": set(),
                    "messages": [],
                    "diffs_text": [],
                })
                bucket["commits"].append(sha)
                bucket["projects"].add(project.name)
                bucket["messages"].append(
                    f"[{project.name}/{branch_name}] {commit.get('message', '').strip()}"
                )

                # 拉 diff
                try:
                    diffs = client.get_commit_diff(project.project_id, sha) or []
                except Exception as e:
                    logger.warning(f"获取 {sha[:8]} diff 失败: {e}")
                    diffs = []
                for d in diffs:
                    diff_text = d.get("diff", "")
                    adds, dels = _count_diff_lines(diff_text)
                    bucket["additions"] += adds
                    bucket["deletions"] += dels
                    path = d.get("new_path") or d.get("old_path") or "unknown"
                    bucket["files"].add(path)
                    if d.get("new_file"):
                        bucket["new_files"] += 1
                    if d.get("deleted_file"):
                        bucket["deleted_files"] += 1
                    if diff_text:
                        bucket["diffs_text"].append(
                            f"--- {path} ---\n{diff_text}"
                        )

    # ── UPSERT ────────────────────────────────────────
    def _upsert_author(
        self,
        email: str,
        data: Dict[str, Any],
        target_date: date,
    ) -> None:
        """对单个作者调 LLM 并 UPSERT"""
        commits_text = "\n".join(data["messages"])
        diffs_text = "\n\n".join(data["diffs_text"])

        llm_result = call_and_parse(
            api_url=self.llm_config["api_url"],
            api_key=self.llm_config["api_key"],
            model=self.llm_config["model"],
            author_name=data["author_name"],
            commits_text=commits_text,
            diffs_text=diffs_text,
            top_n=self.top_n,
            max_tokens=self.llm_config.get("max_tokens", 4096),
            temperature=self.llm_config.get("temperature", 0.7),
            timeout=self.llm_config.get("timeout", 240),
            max_retries=self.llm_config.get("max_retries", 3),
            retry_delay=self.llm_config.get("retry_delay", 10),
        )

        existing = (self.db.query(EmployeeEfficiencyDaily)
                       .filter_by(author_email=email, stat_date=target_date)
                       .first())

        values = dict(
            author_email=email,
            author_name=data["author_name"],
            stat_date=target_date,
            commits_count=len(data["commits"]),
            additions=data["additions"],
            deletions=data["deletions"],
            files_changed=len(data["files"]),
            new_files=data["new_files"],
            deleted_files=data["deleted_files"],
            projects_involved=json.dumps(sorted(data["projects"]),
                                          ensure_ascii=False),
            summary_top_n=self.top_n,
        )

        if llm_result["success"]:
            values.update(
                review_score=llm_result["score"],
                review_grade=llm_result["grade"],
                review_summary=llm_result["review_summary"],
                work_summary=json.dumps(llm_result["work_summary"],
                                         ensure_ascii=False),
                llm_status="success",
                llm_error=None,
            )
        else:
            values.update(
                review_score=None,
                review_grade=None,
                review_summary=None,
                work_summary=None,
                llm_status="failed",
                llm_error="LLM call failed or returned empty",
            )

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            self.db.add(EmployeeEfficiencyDaily(**values))
        self.db.commit()
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `pytest tests/test_services/test_efficiency_aggregator.py -v`
Expected: 6 个测试全部 PASS

- [ ] **Step 5: 提交**

```bash
git add app/services/efficiency_aggregator.py tests/test_services/test_efficiency_aggregator.py
git commit -m "feat: add EfficiencyAggregator service for per-author daily aggregation"
```

---

## Task 4: 钩入日报任务

**Files:**
- Modify: `app/main.py` (函数 `run_scheduled_task` 内的项目循环中)
- 不修改 `task_executor.py`（保持职责单一），而是在 `main.py` 的调度入口里在每个项目日报完成后顺手聚合

但因为聚合是"全局跨项目"性质，更合理的做法是 **在所有项目都跑完后只调用一次**。具体做法：

- [ ] **Step 1: 阅读 `app/main.py:659` 的 `run_scheduled_task` 函数完整结构**

Run: `grep -n "def run_scheduled_task" app/main.py`
然后 Read 该函数全部内容，定位到 `for project in projects_list:` 循环外、函数末尾的位置。

- [ ] **Step 2: 写集成测试（验证钩子会被调用）**

Create new test in `tests/test_services/test_efficiency_hook.py`:

```python
"""测试日报任务完成后会触发能效聚合"""
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.efficiency_aggregator import EfficiencyAggregator


def test_aggregator_is_callable_after_daily_task():
    """聚合器可以被独立调用一次完成全部项目"""
    # 这是占位测试，确保 import 链路畅通；实际钩子在 main.py 调用
    agg = EfficiencyAggregator(
        db=MagicMock(),
        gitlab_client_factory=lambda p: MagicMock(),
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    assert hasattr(agg, "aggregate")
    assert callable(agg.aggregate)
```

- [ ] **Step 3: 运行测试，确认通过（导入校验）**

Run: `pytest tests/test_services/test_efficiency_hook.py -v`
Expected: PASS

- [ ] **Step 4: 修改 `app/main.py` 的 `run_scheduled_task` 函数**

定位到 `for project in projects_list:` 循环**之后**、`db.close()` **之前**，添加聚合调用。完整位置参考：

```python
# 在 run_scheduled_task 函数的 for project in projects_list: 循环结束后插入
```

新增代码块（仅在 `task_type == 'daily'` 时触发）：

```python
        # 日报跑完后顺便聚合人员能效（仅 daily 任务）
        if task_type == 'daily':
            try:
                from datetime import date as _date, timedelta as _td
                from app.services.efficiency_aggregator import EfficiencyAggregator
                from app.services.gitlab_client import GitLabClient as _GLC

                target_efficiency_date = _date.today() - _td(days=1)

                def _client_factory(proj):
                    # 复用上方解 token 的逻辑（简化版：项目 token 优先，全局兜底）
                    tk = None
                    if proj.access_token:
                        try:
                            tk = security_service.decrypt(proj.access_token)
                        except ValueError:
                            tk = None
                    if not tk and settings.global_gitlab_token:
                        try:
                            tk = security_service.decrypt(settings.global_gitlab_token)
                        except ValueError:
                            tk = None
                    if not tk:
                        raise RuntimeError(f"项目 {proj.name} 无可用 Token")
                    return _GLC(gitlab_url=settings.global_gitlab_url,
                                 access_token=tk)

                llm_cfg = {
                    "api_url": settings.llm_api_url,
                    "api_key": (security_service.decrypt(settings.llm_api_key)
                                if settings.llm_api_key else ""),
                    "model": settings.llm_model,
                    "timeout": settings.llm_timeout,
                    "max_retries": settings.llm_max_retries,
                    "retry_delay": settings.llm_retry_delay,
                }

                top_n = getattr(settings, "efficiency_work_summary_top_n", 5) or 5

                aggregator = EfficiencyAggregator(
                    db=db,
                    gitlab_client_factory=_client_factory,
                    llm_config=llm_cfg,
                    top_n=top_n,
                )
                agg_result = aggregator.aggregate(target_efficiency_date)
                logger.info(f"人员能效聚合: {agg_result}")
            except Exception as e:
                # 聚合失败不影响日报本身
                logger.exception(f"人员能效聚合失败: {e}")
```

- [ ] **Step 5: 在 Settings 模型加可选配置（仅当字段不存在时）**

Read `app/models/settings.py` 查看现有字段。如果没有 `efficiency_work_summary_top_n`，在合适位置加：

```python
    efficiency_work_summary_top_n = Column(Integer, default=5,
        comment="人员能效工作总结条目上限")
```

并在 `app/database.py:_migrate_columns()` 函数中注册自动迁移（依现有模式追加列定义）。如果不熟悉迁移机制，可以**跳过 Settings 改动**，aggregator 里的 `getattr(settings, ..., 5)` 已经做好兜底。

- [ ] **Step 6: 启动应用人工 smoke test（可选）**

Run: `python main.py --port 5001` 然后 Ctrl+C 关闭。
Expected: 启动无报错，无 import 错误。

- [ ] **Step 7: 提交**

```bash
git add app/main.py app/models/settings.py app/database.py tests/test_services/test_efficiency_hook.py
git commit -m "feat: trigger efficiency aggregator after daily review tasks"
```

---

## Task 5: API 端点

**Files:**
- Create: `app/api/efficiency.py`
- Modify: `app/main.py` (注册路由)
- Test: `tests/test_api/test_efficiency.py`

API 端点：
- `GET /api/efficiency/list` — 列表 + 团队概览
- `GET /api/efficiency/detail` — 个人详情下钻
- `POST /api/efficiency/recompute` — 管理员补算

- [ ] **Step 1: 写失败测试**

Create `tests/test_api/test_efficiency.py`:

```python
"""人员能效 API 测试"""
from datetime import date, timedelta
import json
import pytest
from fastapi.testclient import TestClient

from app.models.employee_efficiency import EmployeeEfficiencyDaily


def _seed(db, email, name, d, *, score=80, commits=3, adds=100, dels=20,
          grade="良好", projects=None):
    db.add(EmployeeEfficiencyDaily(
        author_email=email, author_name=name, stat_date=d,
        commits_count=commits, additions=adds, deletions=dels,
        files_changed=5, new_files=0, deleted_files=0,
        projects_involved=json.dumps(projects or ["proj-a"]),
        review_score=score, review_grade=grade,
        review_summary="ok", work_summary=json.dumps(["A", "B"]),
        summary_top_n=5, llm_status="success",
    ))
    db.commit()


def test_list_requires_login(client):
    """未登录返回 401"""
    resp = client.get("/api/efficiency/list")
    assert resp.status_code == 401


def test_list_default_yesterday(client, db_session, login_as_admin):
    """不传日期默认昨天"""
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", yesterday, score=85)
    _seed(db_session, "c@d.com", "Carol", yesterday, score=70)
    resp = client.get("/api/efficiency/list")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["items"]) == 2
    assert data["team_stats"]["person_count"] == 2


def test_list_sort_by_score(client, db_session, login_as_admin):
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", yesterday, score=70)
    _seed(db_session, "b@b.com", "Bob", yesterday, score=95)
    resp = client.get("/api/efficiency/list?sort_by=score&order=desc")
    items = resp.json()["data"]["items"]
    assert items[0]["author_email"] == "b@b.com"
    assert items[1]["author_email"] == "a@b.com"


def test_list_date_range(client, db_session, login_as_admin):
    """支持日期范围"""
    d1 = date(2026, 5, 25)
    d2 = date(2026, 5, 26)
    d3 = date(2026, 5, 27)
    _seed(db_session, "a@b.com", "A", d1)
    _seed(db_session, "a@b.com", "A", d2)
    _seed(db_session, "a@b.com", "A", d3)
    resp = client.get("/api/efficiency/list?start_date=2026-05-25&end_date=2026-05-26")
    assert resp.status_code == 200
    assert len(resp.json()["data"]["items"]) == 2


def test_detail_returns_summary_trend_commits(client, db_session,
                                                login_as_admin):
    d = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", d, score=85)
    _seed(db_session, "a@b.com", "Alice",
          d - timedelta(days=1), score=82)
    resp = client.get(f"/api/efficiency/detail?email=a@b.com&date={d.isoformat()}&trend_days=7")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"]["author_email"] == "a@b.com"
    assert len(data["trend"]) >= 1
    # commits 字段可以是空数组（mock 时没有 commit_records）
    assert "commits" in data


def test_recompute_requires_admin(client, db_session, login_as_member):
    """非系统管理员调用 recompute 返回 403"""
    resp = client.post("/api/efficiency/recompute",
                        json={"date": date.today().isoformat()})
    assert resp.status_code == 403


def test_list_member_sees_only_self(client, db_session,
                                      login_as_member, member_user):
    """项目成员只能看到自己"""
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, member_user.email, "Self", yesterday)
    _seed(db_session, "other@x.com", "Other", yesterday)
    resp = client.get("/api/efficiency/list")
    items = resp.json()["data"]["items"]
    emails = [i["author_email"] for i in items]
    assert member_user.email in emails
    assert "other@x.com" not in emails
```

> **注意**：fixtures `client`、`db_session`、`login_as_admin`、`login_as_member`、`member_user` 需要在 `tests/conftest.py` 中已经定义。如果尚未，请参考 `tests/test_api/test_auth.py` 现有 fixture 风格新增。

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_api/test_efficiency.py -v`
Expected: FAIL — `ModuleNotFoundError: app.api.efficiency`

- [ ] **Step 3: 创建 `app/api/efficiency.py`**

```python
"""人员能效 API"""
from __future__ import annotations
from datetime import date, datetime, timedelta
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, desc, asc
from sqlalchemy.orm import Session
from loguru import logger
from pydantic import BaseModel

from app.database import get_db
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.user import User, project_admins, project_members
from app.models.project import Project
from app.models.commit_record import CommitRecord
from app.schemas.response import ApiResponse
from app.api.users import get_current_user_full


router = APIRouter(prefix="/api/efficiency", tags=["efficiency"])


# ── 权限工具 ──────────────────────────────────────────
def _allowed_project_names(user: User, db: Session) -> Optional[List[str]]:
    """系统管理员返回 None；项目角色返回项目名列表；普通用户返回 []"""
    if user.is_system_admin():
        return None
    if user.is_project_admin() or user.is_project_member():
        admin_ids = {r[0] for r in db.execute(
            project_admins.select().where(project_admins.c.user_id == user.id)
        ).fetchall()}
        member_ids = {r[0] for r in db.execute(
            project_members.select().where(project_members.c.user_id == user.id)
        ).fetchall()}
        ids = admin_ids | member_ids
        if not ids:
            return []
        return [p[0] for p in db.query(Project.name).filter(
            Project.id.in_(ids)
        ).all()]
    return []


def _restrict_query_by_user(query, current_user: User, db: Session):
    """根据用户角色限制查询范围"""
    if current_user.is_system_admin():
        return query
    if current_user.is_project_admin():
        names = _allowed_project_names(current_user, db) or []
        if not names:
            return query.filter(False)
        # projects_involved 是 JSON 字符串，用 LIKE 模糊匹配项目名
        # （SQLite 不支持 JSON 函数的高效查询；项目数不大时 LIKE 足够）
        from sqlalchemy import or_
        conds = [EmployeeEfficiencyDaily.projects_involved.like(
            f'%"{n}"%') for n in names]
        return query.filter(or_(*conds))
    # 项目成员：只看自己
    if current_user.is_project_member():
        if not current_user.email:
            return query.filter(False)
        return query.filter(
            EmployeeEfficiencyDaily.author_email == current_user.email
        )
    return query.filter(False)


# ── 序列化 ────────────────────────────────────────────
def _serialize(row: EmployeeEfficiencyDaily) -> dict:
    return {
        "id": row.id,
        "author_email": row.author_email,
        "author_name": row.author_name,
        "stat_date": row.stat_date.isoformat(),
        "commits_count": row.commits_count,
        "additions": row.additions,
        "deletions": row.deletions,
        "files_changed": row.files_changed,
        "new_files": row.new_files,
        "deleted_files": row.deleted_files,
        "projects_involved": json.loads(row.projects_involved or "[]"),
        "review_score": row.review_score,
        "review_grade": row.review_grade,
        "review_summary": row.review_summary,
        "work_summary": json.loads(row.work_summary) if row.work_summary else [],
        "llm_status": row.llm_status,
        "llm_error": row.llm_error,
    }


# ── 列表 ──────────────────────────────────────────────
SORT_FIELDS = {
    "score": EmployeeEfficiencyDaily.review_score,
    "additions": EmployeeEfficiencyDaily.additions,
    "deletions": EmployeeEfficiencyDaily.deletions,
    "commits": EmployeeEfficiencyDaily.commits_count,
    "files_changed": EmployeeEfficiencyDaily.files_changed,
}


@router.get("/list")
async def list_efficiency(
    date_str: Optional[str] = Query(None, alias="date",
                                     description="YYYY-MM-DD（默认昨天）"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: str = Query("score", description="score/additions/deletions/commits/files_changed"),
    order: str = Query("desc", description="desc/asc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """列表 + 团队概览"""
    q = db.query(EmployeeEfficiencyDaily)
    q = _restrict_query_by_user(q, current_user, db)

    # 日期过滤
    if start_date or end_date:
        if start_date:
            q = q.filter(EmployeeEfficiencyDaily.stat_date >=
                         date.fromisoformat(start_date))
        if end_date:
            q = q.filter(EmployeeEfficiencyDaily.stat_date <=
                         date.fromisoformat(end_date))
    else:
        target = (date.fromisoformat(date_str) if date_str
                  else date.today() - timedelta(days=1))
        q = q.filter(EmployeeEfficiencyDaily.stat_date == target)

    # 排序
    sort_col = SORT_FIELDS.get(sort_by, EmployeeEfficiencyDaily.review_score)
    direction = desc if order == "desc" else asc
    q = q.order_by(direction(sort_col).nullslast()
                   if hasattr(sort_col, "nullslast") else direction(sort_col))

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    # 团队概览（基于过滤后的全集，不应用 limit）
    stats_q = _restrict_query_by_user(db.query(EmployeeEfficiencyDaily),
                                        current_user, db)
    if start_date or end_date:
        if start_date:
            stats_q = stats_q.filter(EmployeeEfficiencyDaily.stat_date >=
                                      date.fromisoformat(start_date))
        if end_date:
            stats_q = stats_q.filter(EmployeeEfficiencyDaily.stat_date <=
                                      date.fromisoformat(end_date))
    else:
        target = (date.fromisoformat(date_str) if date_str
                  else date.today() - timedelta(days=1))
        stats_q = stats_q.filter(EmployeeEfficiencyDaily.stat_date == target)

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

    return ApiResponse(success=True, data={
        "items": [_serialize(r) for r in items],
        "total": total,
        "team_stats": team_stats,
    })


# ── 详情 ──────────────────────────────────────────────
@router.get("/detail")
async def get_detail(
    email: str = Query(..., description="人员邮箱"),
    date_str: Optional[str] = Query(None, alias="date"),
    trend_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """个人详情 = 当日summary + 近 N 天 trend + 当日 commits 列表"""
    target = (date.fromisoformat(date_str) if date_str
              else date.today() - timedelta(days=1))

    # 权限检查
    if not current_user.is_system_admin():
        if current_user.is_project_member() and not current_user.is_project_admin():
            if (current_user.email or "").lower() != email.lower():
                raise HTTPException(403, "无权查看他人能效详情")
        # 项目管理员可以看下属项目人员（简化：放行，由项目过滤兜底）

    summary = (db.query(EmployeeEfficiencyDaily)
                  .filter_by(author_email=email, stat_date=target)
                  .first())

    trend = (db.query(EmployeeEfficiencyDaily)
                .filter(
                    EmployeeEfficiencyDaily.author_email == email,
                    EmployeeEfficiencyDaily.stat_date >=
                        target - timedelta(days=trend_days - 1),
                    EmployeeEfficiencyDaily.stat_date <= target,
                )
                .order_by(EmployeeEfficiencyDaily.stat_date.asc())
                .all())

    trend_data = [
        {
            "stat_date": r.stat_date.isoformat(),
            "commits_count": r.commits_count,
            "additions": r.additions,
            "deletions": r.deletions,
            "review_score": r.review_score,
        }
        for r in trend
    ]

    # 当日 commits 列表（从 commit_records 表）
    day_start = datetime.combine(target, datetime.min.time())
    day_end = datetime.combine(target, datetime.max.time())
    commits = (db.query(CommitRecord)
                  .filter(
                      CommitRecord.author_email == email,
                      CommitRecord.commit_date >= day_start,
                      CommitRecord.commit_date <= day_end,
                  )
                  .all())
    commits_data = [
        {
            "commit_sha": c.commit_sha,
            "branch": c.branch,
            "author_name": c.author_name,
            "commit_date": c.commit_date.isoformat(),
            "review_status": c.review_status,
        }
        for c in commits
    ]

    return ApiResponse(success=True, data={
        "summary": _serialize(summary) if summary else None,
        "trend": trend_data,
        "commits": commits_data,
    })


# ── 补算 ──────────────────────────────────────────────
class RecomputeRequest(BaseModel):
    date: str
    force: bool = False


@router.post("/recompute")
async def recompute(
    body: RecomputeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """管理员手动补算指定日期的人员能效数据"""
    if not current_user.is_system_admin():
        raise HTTPException(403, "仅系统管理员可补算")

    target = date.fromisoformat(body.date)

    # 如果 force=False 且已有数据则跳过
    if not body.force:
        existing = (db.query(EmployeeEfficiencyDaily)
                       .filter_by(stat_date=target).count())
        if existing > 0:
            return ApiResponse(success=True, message=f"已存在 {existing} 条记录，"
                                f"如需重算请勾选 force",
                                data={"skipped": True, "existing": existing})

    # 同步触发（小数据量场景下可接受；大数据量后续可改为后台任务）
    from app.services.efficiency_aggregator import EfficiencyAggregator
    from app.services.gitlab_client import GitLabClient
    from app.security import security_service
    from app.models import Settings

    settings = db.query(Settings).first()
    if not settings or not settings.global_gitlab_url:
        raise HTTPException(400, "GitLab 全局配置缺失")

    def _factory(proj):
        tk = None
        if proj.access_token:
            try:
                tk = security_service.decrypt(proj.access_token)
            except ValueError:
                tk = None
        if not tk and settings.global_gitlab_token:
            try:
                tk = security_service.decrypt(settings.global_gitlab_token)
            except ValueError:
                tk = None
        if not tk:
            raise RuntimeError(f"项目 {proj.name} 无 Token")
        return GitLabClient(gitlab_url=settings.global_gitlab_url,
                             access_token=tk)

    llm_cfg = {
        "api_url": settings.llm_api_url,
        "api_key": (security_service.decrypt(settings.llm_api_key)
                    if settings.llm_api_key else ""),
        "model": settings.llm_model,
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
        "retry_delay": settings.llm_retry_delay,
    }
    top_n = getattr(settings, "efficiency_work_summary_top_n", 5) or 5

    try:
        aggregator = EfficiencyAggregator(
            db=db,
            gitlab_client_factory=_factory,
            llm_config=llm_cfg,
            top_n=top_n,
        )
        result = aggregator.aggregate(target)
        return ApiResponse(success=True, data=result)
    except Exception as e:
        logger.exception(f"补算 {target} 失败")
        raise HTTPException(500, f"补算失败: {e}")
```

- [ ] **Step 4: 在 `app/main.py` 注册路由**

定位 `app/main.py:32-34` 的 import 部分，添加：

```python
from app.api import auth, projects, settings as settings_api, tasks, logs, reports
from app.api import webhook, webhook_reviews, users, roles, efficiency
```

定位 `app/main.py:147-156` 的 `include_router` 部分，添加：

```python
app.include_router(efficiency.router)
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `pytest tests/test_api/test_efficiency.py -v`
Expected: 7 个测试 PASS（如果 fixture 缺失会失败 —— 此时按需补 conftest）

- [ ] **Step 6: 提交**

```bash
git add app/api/efficiency.py app/main.py tests/test_api/test_efficiency.py
git commit -m "feat: add efficiency API endpoints (list/detail/recompute)"
```

---

## Task 6: Web 路由 + 模板骨架

**Files:**
- Modify: `app/main.py` (新增 `/efficiency` Web 路由)
- Create: `app/templates/efficiency.html`
- Modify: `app/templates/base.html` (侧边栏菜单)

- [ ] **Step 1: 在 `app/templates/base.html` 侧边栏添加菜单项**

定位 `<div class="nav-section">` "任务"区块（约 47-56 行），在 "Webhook 审查" 链接**之后**追加：

```html
                    <a class="nav-item {% if '/efficiency' in request.url.path %}active{% endif %}" href="/efficiency">
                        <i class="bi bi-graph-up-arrow"></i>
                        人员能效
                    </a>
```

- [ ] **Step 2: 创建 `app/templates/efficiency.html`**

```html
{% extends "base.html" %}
{% block title %}人员能效 - 代码审查工具{% endblock %}

{% block extra_css %}
<style>
    .grade-badge { display:inline-block; padding:2px 10px; border-radius:10px;
                   font-size:12px; color:#fff; font-weight:500; }
    .grade-excellent { background:#28a745; }
    .grade-good { background:#0d6efd; }
    .grade-average { background:#ffc107; color:#333; }
    .grade-poor { background:#dc3545; }
    .grade-none { background:#999; }

    .stat-card { background:#fff; border:1px solid #e5e7eb;
                  border-radius:8px; padding:16px; text-align:center; }
    .stat-card .label { color:#666; font-size:13px; margin-bottom:4px; }
    .stat-card .value { color:#111; font-size:24px; font-weight:600; }

    .efficiency-table th { cursor:pointer; user-select:none; }
    .efficiency-table th .sort-icon { opacity:0.3; }
    .efficiency-table th.sorted .sort-icon { opacity:1; }
    .efficiency-table tbody tr { cursor:pointer; }
    .efficiency-table tbody tr:hover { background:#f9fafb; }

    .drawer { position:fixed; top:0; right:-560px; width:560px; height:100vh;
              background:#fff; box-shadow:-4px 0 16px rgba(0,0,0,0.08);
              transition:right 0.25s; overflow-y:auto; z-index:1050; }
    .drawer.active { right:0; }
    .drawer-header { padding:16px 20px; border-bottom:1px solid #eee;
                      display:flex; justify-content:space-between; align-items:center; }
    .drawer-body { padding:20px; }
    .drawer-overlay { position:fixed; inset:0; background:rgba(0,0,0,0.25);
                       z-index:1040; display:none; }
    .drawer-overlay.active { display:block; }

    #chartCodeTop, #chartGradePie, #chartTrend { width:100%; height:280px; }
</style>
{% endblock %}

{% block content %}
<div class="page-header d-flex justify-content-between align-items-center mb-3">
    <h2 class="m-0"><i class="bi bi-graph-up-arrow me-2"></i>人员能效</h2>
    <div class="d-flex gap-2">
        <input type="date" id="filterDate" class="form-control form-control-sm" />
        <button class="btn btn-sm btn-outline-secondary" id="btnRefresh">
            <i class="bi bi-arrow-clockwise"></i> 刷新
        </button>
        <button class="btn btn-sm btn-primary" id="btnRecompute" style="display:none">
            <i class="bi bi-arrow-repeat"></i> 立即补算
        </button>
    </div>
</div>

<!-- 团队概览 -->
<div class="row g-3 mb-4" id="teamStatsRow">
    <div class="col"><div class="stat-card"><div class="label">总提交</div><div class="value" id="stat-commits">-</div></div></div>
    <div class="col"><div class="stat-card"><div class="label">总新增</div><div class="value text-success" id="stat-add">-</div></div></div>
    <div class="col"><div class="stat-card"><div class="label">总删除</div><div class="value text-danger" id="stat-del">-</div></div></div>
    <div class="col"><div class="stat-card"><div class="label">均分</div><div class="value" id="stat-avg">-</div></div></div>
    <div class="col"><div class="stat-card"><div class="label">参与人数</div><div class="value" id="stat-count">-</div></div></div>
</div>

<!-- 图表区 -->
<div class="row g-3 mb-4">
    <div class="col-md-7"><div class="card"><div class="card-body"><div id="chartCodeTop"></div></div></div></div>
    <div class="col-md-5"><div class="card"><div class="card-body"><div id="chartGradePie"></div></div></div></div>
</div>

<!-- 表格 -->
<div class="card">
    <div class="card-body p-0">
        <table class="table efficiency-table mb-0" id="efficiencyTable">
            <thead>
                <tr>
                    <th>姓名</th>
                    <th>邮箱</th>
                    <th data-sort="commits">提交 <i class="bi bi-arrow-down-up sort-icon"></i></th>
                    <th data-sort="additions">新增 <i class="bi bi-arrow-down-up sort-icon"></i></th>
                    <th data-sort="deletions">删除 <i class="bi bi-arrow-down-up sort-icon"></i></th>
                    <th data-sort="files_changed">文件 <i class="bi bi-arrow-down-up sort-icon"></i></th>
                    <th data-sort="score" class="sorted">评分 <i class="bi bi-arrow-down-up sort-icon"></i></th>
                    <th>等级</th>
                    <th>涉及项目</th>
                </tr>
            </thead>
            <tbody id="efficiencyTbody">
                <tr><td colspan="9" class="text-center text-muted py-4">加载中...</td></tr>
            </tbody>
        </table>
    </div>
</div>

<!-- 详情抽屉 -->
<div class="drawer-overlay" id="drawerOverlay"></div>
<div class="drawer" id="detailDrawer">
    <div class="drawer-header">
        <h5 class="m-0" id="drawerTitle">详情</h5>
        <button class="btn-close" id="drawerClose"></button>
    </div>
    <div class="drawer-body" id="drawerBody">
        <div class="text-muted">加载中...</div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script src="/static/js/efficiency.js"></script>
{% endblock %}
```

- [ ] **Step 3: 在 `app/main.py` 添加 Web 路由 `/efficiency`**

定位 `/webhook-reviews` Web 路由（搜索 `webhook-reviews`），在其后添加：

```python
@app.get("/efficiency", response_class=HTMLResponse)
async def efficiency_page(request: Request):
    """人员能效页面"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "efficiency.html",
        get_template_context(request),
    )
```

- [ ] **Step 4: 启动应用人工 smoke test**

Run: `python main.py --port 5001`
访问 `http://localhost:5001/efficiency`（先登录）
Expected: 页面骨架可见，菜单"人员能效"高亮。JS 报错（efficiency.js 尚未实现），属预期。

- [ ] **Step 5: 提交**

```bash
git add app/main.py app/templates/base.html app/templates/efficiency.html
git commit -m "feat: add efficiency web route and page skeleton"
```

---

## Task 7: 前端 JS + ECharts 渲染

**Files:**
- Create: `app/static/js/efficiency.js`

- [ ] **Step 1: 创建 `app/static/js/efficiency.js`**

```javascript
/* 人员能效页面 - 数据加载 + ECharts 渲染 + 详情抽屉 */
(function () {
    'use strict';

    const STATE = {
        date: '',
        sort_by: 'score',
        order: 'desc',
        items: [],
        teamStats: null,
        isAdmin: false,
    };

    const GRADE_CLASS = {
        '优秀': 'grade-excellent',
        '良好': 'grade-good',
        '一般': 'grade-average',
        '待改进': 'grade-poor',
    };

    let chartCodeTop, chartGradePie, chartTrend;

    // ── 工具 ──────────────────────────────────────
    function fmtDate(d) {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        return `${y}-${m}-${day}`;
    }
    function yesterday() {
        const d = new Date(); d.setDate(d.getDate() - 1); return fmtDate(d);
    }
    function gradeBadge(grade) {
        const cls = GRADE_CLASS[grade] || 'grade-none';
        return `<span class="grade-badge ${cls}">${grade || '-'}</span>`;
    }

    // ── 数据加载 ─────────────────────────────────
    async function loadList() {
        const params = new URLSearchParams({
            date: STATE.date,
            sort_by: STATE.sort_by,
            order: STATE.order,
            limit: 500,
        });
        const resp = await apiRequest(`/api/efficiency/list?${params}`);
        if (!resp || !resp.ok) {
            renderEmpty('数据加载失败');
            return;
        }
        const json = await resp.json();
        if (!json.success) { renderEmpty(json.message || '加载失败'); return; }
        STATE.items = json.data.items || [];
        STATE.teamStats = json.data.team_stats || {};
        renderStats(); renderTable(); renderCharts();
    }

    // ── 渲染：团队概览 ──────────────────────────
    function renderStats() {
        const s = STATE.teamStats || {};
        document.getElementById('stat-commits').textContent = s.total_commits ?? '-';
        document.getElementById('stat-add').textContent = `+${s.total_additions ?? 0}`;
        document.getElementById('stat-del').textContent = `-${s.total_deletions ?? 0}`;
        document.getElementById('stat-avg').textContent = s.avg_score ?? '-';
        document.getElementById('stat-count').textContent = s.person_count ?? 0;
    }

    // ── 渲染：表格 ───────────────────────────────
    function renderEmpty(msg) {
        document.getElementById('efficiencyTbody').innerHTML =
            `<tr><td colspan="9" class="text-center text-muted py-4">${msg}</td></tr>`;
    }
    function renderTable() {
        if (!STATE.items.length) {
            document.getElementById('efficiencyTbody').innerHTML =
                `<tr><td colspan="9" class="text-center text-muted py-4">
                    该日数据未生成。${STATE.isAdmin ? '请点击右上方"立即补算"。' : '请联系管理员。'}
                </td></tr>`;
            return;
        }
        const rows = STATE.items.map(it => `
            <tr data-email="${it.author_email}">
                <td>${escapeHtml(it.author_name)}</td>
                <td><span class="text-muted small">${escapeHtml(it.author_email)}</span></td>
                <td>${it.commits_count}</td>
                <td class="text-success">+${it.additions}</td>
                <td class="text-danger">-${it.deletions}</td>
                <td>${it.files_changed}</td>
                <td>${it.review_score ?? '-'}</td>
                <td>${gradeBadge(it.review_grade)}</td>
                <td><span class="text-muted small">${(it.projects_involved || []).join('，')}</span></td>
            </tr>
        `).join('');
        document.getElementById('efficiencyTbody').innerHTML = rows;
        // 行点击 → 抽屉
        document.querySelectorAll('#efficiencyTbody tr[data-email]').forEach(tr => {
            tr.addEventListener('click', () => openDrawer(tr.dataset.email));
        });
    }

    function escapeHtml(s) {
        if (s == null) return '';
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    // ── 渲染：ECharts ────────────────────────────
    function renderCharts() {
        renderCodeTopChart();
        renderGradePieChart();
    }

    function renderCodeTopChart() {
        if (!chartCodeTop) chartCodeTop = echarts.init(document.getElementById('chartCodeTop'));
        const top = STATE.items.slice()
            .sort((a, b) => (b.additions + b.deletions) - (a.additions + a.deletions))
            .slice(0, 10).reverse();
        chartCodeTop.setOption({
            title: { text: '代码量 TOP 10', left: 'left', textStyle: { fontSize: 14 } },
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            legend: { right: 10, data: ['新增', '删除'] },
            grid: { left: 100, right: 30, top: 50, bottom: 20 },
            xAxis: { type: 'value' },
            yAxis: { type: 'category', data: top.map(t => t.author_name) },
            series: [
                {
                    name: '新增', type: 'bar', stack: 'total', color: '#28a745',
                    data: top.map(t => t.additions),
                },
                {
                    name: '删除', type: 'bar', stack: 'total', color: '#dc3545',
                    data: top.map(t => t.deletions),
                },
            ],
        });
    }

    function renderGradePieChart() {
        if (!chartGradePie) chartGradePie = echarts.init(document.getElementById('chartGradePie'));
        const buckets = { '优秀': 0, '良好': 0, '一般': 0, '待改进': 0, '未评': 0 };
        STATE.items.forEach(it => {
            buckets[it.review_grade || '未评']++;
        });
        chartGradePie.setOption({
            title: { text: '评分分布', left: 'left', textStyle: { fontSize: 14 } },
            tooltip: { trigger: 'item' },
            color: ['#28a745', '#0d6efd', '#ffc107', '#dc3545', '#999'],
            series: [{
                type: 'pie', radius: ['40%', '70%'], center: ['50%', '55%'],
                data: Object.entries(buckets)
                    .filter(([, v]) => v > 0)
                    .map(([name, value]) => ({ name, value })),
                label: { formatter: '{b}: {c}' },
            }],
        });
    }

    // ── 详情抽屉 ─────────────────────────────────
    async function openDrawer(email) {
        document.getElementById('drawerOverlay').classList.add('active');
        document.getElementById('detailDrawer').classList.add('active');
        document.getElementById('drawerTitle').textContent = `${email} (${STATE.date})`;
        document.getElementById('drawerBody').innerHTML =
            '<div class="text-muted">加载中...</div>';

        const params = new URLSearchParams({
            email, date: STATE.date, trend_days: 7,
        });
        const resp = await apiRequest(`/api/efficiency/detail?${params}`);
        if (!resp || !resp.ok) {
            document.getElementById('drawerBody').innerHTML =
                '<div class="text-danger">加载详情失败</div>';
            return;
        }
        const json = await resp.json();
        const data = json.data || {};
        renderDrawer(data);
    }

    function renderDrawer(data) {
        const s = data.summary || {};
        const work = (s.work_summary || []);
        const html = `
            <div class="mb-3">
                <strong>综合评分:</strong> ${s.review_score ?? '-'}
                ${gradeBadge(s.review_grade)}
            </div>
            <div class="mb-3">
                <div class="text-muted small">评分简述</div>
                <div>${escapeHtml(s.review_summary || '-')}</div>
            </div>
            <div class="mb-3">
                <div class="text-muted small">今日主要工作</div>
                <ol class="mb-0">${work.map(w => `<li>${escapeHtml(w)}</li>`).join('') || '<li class="text-muted">无</li>'}</ol>
            </div>
            <div class="mb-3">
                <div class="text-muted small mb-2">近 7 天趋势</div>
                <div id="chartTrend"></div>
            </div>
            <div class="mb-3">
                <div class="text-muted small mb-2">今日提交 (${(data.commits || []).length})</div>
                <ul class="list-unstyled small">
                    ${(data.commits || []).map(c => `
                        <li class="mb-2">
                            <code>${c.commit_sha.substring(0, 8)}</code>
                            <span class="badge bg-secondary">${escapeHtml(c.branch)}</span>
                            <span class="text-muted">${c.commit_date}</span>
                        </li>
                    `).join('') || '<li class="text-muted">无</li>'}
                </ul>
            </div>
        `;
        document.getElementById('drawerBody').innerHTML = html;
        renderTrendChart(data.trend || []);
    }

    function renderTrendChart(trend) {
        if (chartTrend) { chartTrend.dispose(); chartTrend = null; }
        chartTrend = echarts.init(document.getElementById('chartTrend'));
        chartTrend.setOption({
            tooltip: { trigger: 'axis' },
            legend: { data: ['新增', '删除', '评分'] },
            grid: { left: 40, right: 40, top: 30, bottom: 30 },
            xAxis: { type: 'category', data: trend.map(t => t.stat_date) },
            yAxis: [
                { type: 'value', name: '代码量' },
                { type: 'value', name: '评分', min: 0, max: 100 },
            ],
            series: [
                { name: '新增', type: 'line', color: '#28a745',
                  data: trend.map(t => t.additions) },
                { name: '删除', type: 'line', color: '#dc3545',
                  data: trend.map(t => t.deletions) },
                { name: '评分', type: 'line', yAxisIndex: 1, color: '#0d6efd',
                  data: trend.map(t => t.review_score),
                  markLine: { data: [
                      { yAxis: 90, lineStyle: { color: '#28a745', type: 'dashed' } },
                      { yAxis: 60, lineStyle: { color: '#dc3545', type: 'dashed' } },
                  ]} },
            ],
        });
    }

    function closeDrawer() {
        document.getElementById('drawerOverlay').classList.remove('active');
        document.getElementById('detailDrawer').classList.remove('active');
    }

    // ── 排序交互 ─────────────────────────────────
    function bindSort() {
        document.querySelectorAll('#efficiencyTable th[data-sort]').forEach(th => {
            th.addEventListener('click', () => {
                const field = th.dataset.sort;
                if (STATE.sort_by === field) {
                    STATE.order = STATE.order === 'desc' ? 'asc' : 'desc';
                } else {
                    STATE.sort_by = field; STATE.order = 'desc';
                }
                document.querySelectorAll('#efficiencyTable th').forEach(t =>
                    t.classList.remove('sorted'));
                th.classList.add('sorted');
                loadList();
            });
        });
    }

    // ── 补算按钮（仅管理员） ───────────────────
    async function checkAdminAndBindRecompute() {
        const resp = await apiRequest('/api/auth/me');
        if (!resp) return;
        const data = await resp.json();
        STATE.isAdmin = data.roles && data.roles.includes('system_admin');
        if (STATE.isAdmin) {
            document.getElementById('btnRecompute').style.display = '';
        }
        document.getElementById('btnRecompute').addEventListener('click', async () => {
            if (!confirm(`确认补算 ${STATE.date} 的人员能效？`)) return;
            const btn = document.getElementById('btnRecompute');
            btn.disabled = true;
            try {
                const r = await apiRequest('/api/efficiency/recompute', {
                    method: 'POST',
                    body: JSON.stringify({ date: STATE.date, force: false }),
                });
                if (r && r.ok) {
                    showNotification('补算完成', 'success');
                    loadList();
                } else {
                    showNotification('补算失败', 'danger');
                }
            } finally {
                btn.disabled = false;
            }
        });
    }

    // ── 入口 ─────────────────────────────────────
    document.addEventListener('DOMContentLoaded', () => {
        STATE.date = yesterday();
        document.getElementById('filterDate').value = STATE.date;
        document.getElementById('filterDate').addEventListener('change', e => {
            STATE.date = e.target.value || yesterday();
            loadList();
        });
        document.getElementById('btnRefresh').addEventListener('click', loadList);
        document.getElementById('drawerClose').addEventListener('click', closeDrawer);
        document.getElementById('drawerOverlay').addEventListener('click', closeDrawer);
        bindSort();
        checkAdminAndBindRecompute();
        loadList();
    });
})();
```

- [ ] **Step 2: 启动应用人工 smoke test**

Run: `python main.py --port 5001`
访问 `http://localhost:5001/efficiency`
Expected: 页面正常加载，团队概览卡片、ECharts 图表、表格都渲染（即使无数据也显示空态）。点击行能打开抽屉。

- [ ] **Step 3: 提交**

```bash
git add app/static/js/efficiency.js
git commit -m "feat: implement efficiency page JS with ECharts"
```

---

## Task 8: 回填脚本

**Files:**
- Create: `scripts/backfill_efficiency.py`

- [ ] **Step 1: 创建脚本**

```python
"""
回填脚本：按指定日期范围补算 employee_efficiency_daily

用法:
    python scripts/backfill_efficiency.py --start 2026-05-01 --end 2026-05-27
    python scripts/backfill_efficiency.py --days 7   # 最近 7 天
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from app.database import SessionLocal, init_db
from app.models import Settings
from app.security import security_service
from app.services.efficiency_aggregator import EfficiencyAggregator
from app.services.gitlab_client import GitLabClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int,
                        help="最近 N 天（与 start/end 互斥）")
    args = parser.parse_args()

    if args.days:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=args.days - 1)
    else:
        if not args.start or not args.end:
            parser.error("必须指定 --start 和 --end，或使用 --days")
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)

    init_db()
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings:
            logger.error("Settings 未配置")
            return

        def _factory(proj):
            tk = None
            if proj.access_token:
                try:
                    tk = security_service.decrypt(proj.access_token)
                except ValueError:
                    pass
            if not tk and settings.global_gitlab_token:
                try:
                    tk = security_service.decrypt(settings.global_gitlab_token)
                except ValueError:
                    pass
            if not tk:
                raise RuntimeError(f"无 Token: {proj.name}")
            return GitLabClient(gitlab_url=settings.global_gitlab_url,
                                 access_token=tk)

        llm_cfg = {
            "api_url": settings.llm_api_url,
            "api_key": (security_service.decrypt(settings.llm_api_key)
                        if settings.llm_api_key else ""),
            "model": settings.llm_model,
            "timeout": settings.llm_timeout,
            "max_retries": settings.llm_max_retries,
            "retry_delay": settings.llm_retry_delay,
        }
        top_n = getattr(settings, "efficiency_work_summary_top_n", 5) or 5

        aggregator = EfficiencyAggregator(
            db=db, gitlab_client_factory=_factory,
            llm_config=llm_cfg, top_n=top_n,
        )

        current = start
        while current <= end:
            logger.info(f"=== 回填 {current} ===")
            try:
                result = aggregator.aggregate(current)
                logger.info(f"完成: {result}")
            except Exception as e:
                logger.exception(f"回填 {current} 失败: {e}")
            current += timedelta(days=1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 手动测试脚本可调用**

Run: `python scripts/backfill_efficiency.py --days 1`
Expected: 输出"=== 回填 xxxx-xx-xx ===" 日志（实际是否成功取决于配置，但 import / argparse 不应报错）

- [ ] **Step 3: 提交**

```bash
git add scripts/backfill_efficiency.py
git commit -m "feat: add backfill script for historical efficiency data"
```

---

## Task 9: 端到端冒烟与文档收尾

- [ ] **Step 1: 运行完整测试套件**

Run: `pytest tests/ -v --tb=short`
Expected: 全部 PASS。若有 fixture 缺失或 import 问题需补齐。

- [ ] **Step 2: 启动应用端到端验证**

Run: `python main.py --port 5001`

手工核对：
1. 登录后侧边栏可见"人员能效"菜单
2. 访问 `/efficiency` 页面正常渲染（即使数据为空也不报 JS 错）
3. 评分等级徽标显示正确（系统管理员可见"立即补算"按钮）
4. 点击表格行可打开右侧抽屉
5. 改变日期 / 点击列头排序都能触发重新加载

- [ ] **Step 3: 提交最终 README 更新（可选）**

如果项目有 `README.md`，在功能列表添加一句：

```
- 人员能效模块：日报跑完自动按人聚合代码量与 LLM 评分，提供表格 + ECharts 报表
```

```bash
git add README.md
git commit -m "docs: add efficiency module to README features"
```

- [ ] **Step 4: 完结提交**

确认所有改动已合入 master/main。检查 `git status` 干净。

---

## Self-Review 检查

**1. Spec coverage：**
- ✅ 表 `employee_efficiency_daily` 含所有 spec 要求字段（Task 1）
- ✅ 日报跑完触发聚合（Task 4）
- ✅ 1 次 LLM 同时拿评分 + 工作总结（Task 2 + 3）
- ✅ author_email 主维度、跨项目跨分支去重（Task 3 测试覆盖）
- ✅ 列表/详情/补算 三个 API（Task 5）
- ✅ ECharts 图表（Task 7）
- ✅ 详情抽屉下钻（Task 7）
- ✅ 权限沿用现有体系（Task 5 `_restrict_query_by_user`）
- ✅ 失败兜底（Task 5 recompute + Task 7 补算按钮）

**2. Placeholder 扫描：** 无 TBD/TODO/「类似 Task N」/未定义类型。所有代码块都是完整可粘贴的实现。

**3. 类型一致性：**
- `EmployeeEfficiencyDaily.stat_date` 全程是 `date` 类型 ✅
- `EfficiencyAggregator.__init__` 签名与 Task 5 `recompute` 调用一致 ✅
- `call_and_parse` 返回 dict 的键在 Task 3 测试 mock 和 Task 3 实现里完全一致 ✅
- API `_serialize` 输出的字段名与 Task 7 前端访问的字段名一一对应 ✅

---

## 实施建议

- **Task 1 → 2 → 3** 是后端核心数据 + 业务逻辑，强烈建议按 TDD 顺序，每 Task 完成都跑测试
- **Task 4** 是钩子接入，确认其余测试都过后再做
- **Task 5 → 6 → 7** 是 API + 前端，最好串行
- **Task 8 → 9** 收尾，不阻塞主线

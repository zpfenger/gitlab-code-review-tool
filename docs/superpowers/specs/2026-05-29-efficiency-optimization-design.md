# 人员能效功能优化设计文档

**日期**: 2026-05-29
**状态**: 已批准
**方案**: 方案一 - 扩展现有架构

---

## 1. 需求概述

### 1.1 业务需求

1. **月度人员能效汇总表**：根据人员能效明细表汇总月度情况，工作总结等内容需要调用 LLM 进行汇总
2. **Tab 页面切换**：增加按天/按月两个 Tab 页面
3. **日期区间查询**：按天模式下支持开始/结束日期区间选择，汇总区间数据
4. **月度查询**：按月模式下查询指定月的月度汇总数据

### 1.2 需求确认

| 需求点 | 方案 |
|--------|------|
| 月度汇总存储 | 新建 `employee_efficiency_monthly` 表 |
| 月度工作总结 | 统计字段求和 + LLM 重新生成月度总结 |
| 按天区间查询 | 区间汇总 + 弹窗明细 + 侧边抽屉 |
| 按月查询 | 月度汇总 + 详情弹窗 |
| 生成时机 | 定时任务每月1日自动生成 |

---

## 2. 架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (efficiency.js)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │   Tab 切换    │  │  日期选择器   │  │   数据展示区域    │   │
│  │  按天 / 按月  │  │ 区间 / 年月   │  │  表格 + 图表     │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     API 层 (efficiency.py)                   │
│  ┌──────────────────┐  ┌──────────────────┐                 │
│  │  /list (现有)     │  │  /monthly/list   │                 │
│  │  /detail (现有)   │  │  /monthly/detail │                 │
│  │  /recompute (现有)│  │  /range/summary  │                 │
│  │                   │  │  /range/detail   │                 │
│  └──────────────────┘  └──────────────────┘                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    数据模型层                                 │
│  ┌──────────────────────┐  ┌──────────────────────────┐     │
│  │  EmployeeEfficiency   │  │  EmployeeEfficiency       │     │
│  │  Daily (现有)         │  │  Monthly (新增)           │     │
│  └──────────────────────┘  └──────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    服务层                                     │
│  ┌──────────────────────┐  ┌──────────────────────────┐     │
│  │  EfficiencyAggregator │  │  EfficiencyMonthly       │     │
│  │  (现有 - 日聚合)      │  │  Aggregator (新增)       │     │
│  └──────────────────────┘  └──────────────────────────┘     │
│  ┌──────────────────────┐  ┌──────────────────────────┐     │
│  │  efficiency_llm       │  │  scheduler               │     │
│  │  (现有 - LLM调用)     │  │  (新增月度任务)           │     │
│  └──────────────────────┘  └──────────────────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

**日度数据流（现有）：**
```
GitLab API → EfficiencyAggregator → LLM 评分 → employee_efficiency_daily
```

**月度数据流（新增）：**
```
employee_efficiency_daily → EfficiencyMonthlyAggregator → LLM 月度总结 → employee_efficiency_monthly
```

**前端查询流：**
```
按天模式：
  单日查询 → /api/efficiency/list (现有)
  区间查询 → /api/efficiency/range/summary → 弹窗 → /api/efficiency/range/detail → 抽屉

按月模式：
  月度查询 → /api/efficiency/monthly/list → 弹窗 → /api/efficiency/monthly/detail
```

---

## 3. 数据模型设计

### 3.1 新增表：employee_efficiency_monthly

```python
"""人员能效月度汇总表"""
from sqlalchemy import Column, String, Integer, Text, UniqueConstraint, Index
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
```

### 3.2 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| author_email | String(200) | 提交者邮箱，主维度 |
| author_name | String(100) | 提交者显示名 |
| year_month | String(7) | 统计月份，格式 "YYYY-MM" |
| commits_count | Integer | 提交次数（月度求和） |
| additions | Integer | 新增行数（月度求和） |
| deletions | Integer | 删除行数（月度求和） |
| files_changed | Integer | 涉及文件数（月度求和） |
| new_files | Integer | 新建文件数（月度求和） |
| deleted_files | Integer | 删除文件数（月度求和） |
| active_days | Integer | 本月活跃天数 |
| projects_involved | Text | 涉及项目（JSON 数组，合并去重） |
| review_score | Integer | 月度平均评分 |
| review_grade | String(10) | 等级：优秀/良好/一般/待改进 |
| review_summary | Text | LLM 月度评分简述 |
| work_summary | Text | LLM 月度工作总结（JSON 数组） |
| summary_top_n | Integer | 工作总结条目上限 |
| llm_status | String(20) | LLM 状态：pending/success/failed |
| llm_error | Text | LLM 失败原因 |

---

## 4. API 设计

### 4.1 新增端点

#### 4.1.1 月度列表查询

```
GET /api/efficiency/monthly/list
```

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| year_month | string | 是 | 查询月份，格式 "YYYY-MM" |
| sort_by | string | 否 | 排序字段，默认 "score" |
| order | string | 否 | 排序方向，默认 "desc" |
| limit | int | 否 | 返回条数，默认 100 |
| offset | int | 否 | 偏移量，默认 0 |

**响应：**
```json
{
    "success": true,
    "data": {
        "items": [
            {
                "id": 1,
                "author_email": "xxx@example.com",
                "author_name": "张三",
                "year_month": "2026-05",
                "commits_count": 50,
                "additions": 1000,
                "deletions": 500,
                "files_changed": 30,
                "new_files": 5,
                "deleted_files": 2,
                "active_days": 20,
                "projects_involved": ["project-a", "project-b"],
                "review_score": 85,
                "review_grade": "良好",
                "review_summary": "本月代码质量整体良好...",
                "work_summary": ["完成用户模块重构", "修复支付Bug..."],
                "llm_status": "success"
            }
        ],
        "total": 10,
        "team_stats": {
            "person_count": 10,
            "total_commits": 500,
            "total_additions": 10000,
            "total_deletions": 5000,
            "avg_score": 82.5,
            "total_active_days": 200
        }
    }
}
```

#### 4.1.2 月度详情查询

```
GET /api/efficiency/monthly/detail
```

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | 是 | 人员邮箱 |
| year_month | string | 是 | 查询月份 |

**响应：**
```json
{
    "success": true,
    "data": {
        "summary": {
            "id": 1,
            "author_email": "xxx@example.com",
            "author_name": "张三",
            "year_month": "2026-05",
            "commits_count": 50,
            "additions": 1000,
            "deletions": 500,
            "review_score": 85,
            "review_grade": "良好",
            "review_summary": "本月代码质量整体良好...",
            "work_summary": ["完成用户模块重构", "修复支付Bug..."]
        },
        "daily_trend": [
            {
                "stat_date": "2026-05-01",
                "commits_count": 3,
                "additions": 50,
                "deletions": 20,
                "review_score": 80
            }
        ]
    }
}
```

#### 4.1.3 日期区间汇总

```
GET /api/efficiency/range/summary
```

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| start_date | string | 是 | 开始日期，格式 "YYYY-MM-DD" |
| end_date | string | 是 | 结束日期，格式 "YYYY-MM-DD" |
| sort_by | string | 否 | 排序字段，默认 "score" |
| order | string | 否 | 排序方向，默认 "desc" |
| limit | int | 否 | 返回条数，默认 100 |
| offset | int | 否 | 偏移量，默认 0 |

**响应：**
```json
{
    "success": true,
    "data": {
        "items": [
            {
                "author_email": "xxx@example.com",
                "author_name": "张三",
                "commits_count": 30,
                "additions": 600,
                "deletions": 300,
                "files_changed": 20,
                "review_score_avg": 82,
                "active_days": 10,
                "projects_involved": ["project-a", "project-b"]
            }
        ],
        "total": 10,
        "team_stats": {
            "person_count": 10,
            "total_commits": 300,
            "total_additions": 6000,
            "total_deletions": 3000,
            "avg_score": 80.5
        }
    }
}
```

#### 4.1.4 区间明细查询

```
GET /api/efficiency/range/detail
```

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| email | string | 是 | 人员邮箱 |
| start_date | string | 是 | 开始日期 |
| end_date | string | 是 | 结束日期 |

**响应：**
```json
{
    "success": true,
    "data": {
        "author_email": "xxx@example.com",
        "author_name": "张三",
        "start_date": "2026-05-01",
        "end_date": "2026-05-15",
        "summary": {
            "commits_count": 30,
            "additions": 600,
            "deletions": 300,
            "review_score_avg": 82
        },
        "daily_details": [
            {
                "stat_date": "2026-05-01",
                "commits_count": 3,
                "additions": 50,
                "deletions": 20,
                "files_changed": 5,
                "review_score": 80,
                "review_grade": "良好",
                "work_summary": ["修复登录Bug"]
            }
        ]
    }
}
```

### 4.2 权限控制

复用现有的权限控制逻辑：
- 系统管理员：查看所有数据
- 项目管理员：查看其管理项目相关数据
- 项目成员：仅查看自己的数据

---

## 5. 服务层设计

### 5.1 EfficiencyMonthlyAggregator

**职责：**
1. 读取指定月份的所有 daily 数据
2. 按 author_email 分组聚合
3. 调用 LLM 生成月度总结
4. UPSERT 写入 monthly 表

**接口设计：**
```python
class EfficiencyMonthlyAggregator:
    def __init__(self, db: Session, llm_config: Dict[str, Any], top_n: int = 10):
        self.db = db
        self.llm_config = llm_config
        self.top_n = top_n

    def aggregate(self, year_month: str) -> Dict[str, Any]:
        """对指定月份做一次聚合（幂等）

        Args:
            year_month: 格式 "YYYY-MM"

        Returns:
            {
                "year_month": "2026-05",
                "authors_total": 10,
                "authors_success": 9,
                "authors_failed": 1
            }
        """
        pass

    def _aggregate_author(self, email: str, daily_records: List[EmployeeEfficiencyDaily]) -> Dict:
        """聚合单个作者的月度数据"""
        pass

    def _call_llm_for_monthly(self, author_name: str, year_month: str,
                               daily_summary: str, stats: Dict) -> Dict:
        """调用 LLM 生成月度总结"""
        pass
```

### 5.2 LLM Prompt 设计

**月度总结 System Prompt：**
```
你是一位资深的软件开发工程师，需要对员工 {author_name} 在 {year_month} 月度的代码提交进行综合评审，并总结本月主要工作成果。

### 评分目标：
1. 注释（5分）：注释要"有用"不冗余，只注释"为什么这么做"
2. 业务逻辑校验（30分）：是否符合需求文档的核心规则、异常处理是否合理
3. 性能优化点（40分）：是否存在性能瓶颈、缓存策略是否合理
4. 安全风险排查（10分）：是否存在安全漏洞、敏感数据脱敏
5. 代码架构与扩展性（10分）：是否遵循 SOLID、有无过度耦合
6. 编码规范（5分）：命名/注释/格式统一性

### 输出格式：
## 月度评分简述
（2-3 句话概括本月整体表现和代码质量趋势）

## 月度主要工作（不超过 {top_n} 条）
1. xxx
2. xxx
（按对业务的影响和工作量排序）

## 月度总分：XX 分
```

**月度总结 User Prompt：**
```
以下是员工 {author_name} 在 {year_month} 的代码提交数据概览。

### 本月数据：
- 活跃天数：{active_days} 天
- 提交次数：{commits_count} 次
- 代码变更：+{additions} / -{deletions}
- 涉及项目：{projects}

### 每日评分详情：
{daily_scores_summary}

请按系统提示的格式输出月度评分简述、月度主要工作（不超过 {top_n} 条）和月度总分。
```

---

## 6. 前端设计

### 6.1 页面结构

```html
<div class="efficiency-page">
    <!-- Tab 切换 -->
    <div class="efficiency-tabs">
        <button class="tab active" data-mode="daily">按天</button>
        <button class="tab" data-mode="monthly">按月</button>
    </div>

    <!-- 查询条件 -->
    <div class="filter-bar">
        <!-- 按天模式 -->
        <div class="filter-daily">
            <input type="date" id="startDate" />
            <span>~</span>
            <input type="date" id="endDate" />
        </div>
        <!-- 按月模式 -->
        <div class="filter-monthly" style="display:none">
            <input type="month" id="filterMonth" />
        </div>
        <button id="btnRefresh">查询</button>
        <button id="btnRecompute" style="display:none">补算</button>
    </div>

    <!-- 团队概览 -->
    <div class="stats-cards">...</div>

    <!-- 图表区域 -->
    <div class="charts-row">...</div>

    <!-- 数据表格 -->
    <table id="efficiencyTable">...</table>
</div>

<!-- 区间明细弹窗 -->
<div id="rangeDetailModal" class="modal">...</div>

<!-- 月度详情弹窗 -->
<div id="monthlyDetailModal" class="modal">...</div>

<!-- 现有侧边抽屉 -->
<div id="detailDrawer" class="drawer">...</div>
```

### 6.2 交互流程

#### 6.2.1 按天模式 - 区间查询

```
用户选择开始/结束日期
    │
    ▼
调用 /api/efficiency/range/summary
    │
    ▼
表格显示区间汇总数据（每人一行）
    │
    ▼
用户点击某行
    │
    ▼
弹窗显示区间明细（调用 /api/efficiency/range/detail）
    │
    ▼
弹窗内显示每日数据列表
    │
    ▼
用户点击某天
    │
    ▼
关闭弹窗，打开侧边抽屉（调用 /api/efficiency/detail）
```

#### 6.2.2 按月模式

```
用户选择年月
    │
    ▼
调用 /api/efficiency/monthly/list
    │
    ▼
表格显示月度汇总数据（每人一行）
    │
    ▼
用户点击某行
    │
    ▼
弹窗显示月度详情（调用 /api/efficiency/monthly/detail）
    │
    ▼
弹窗内显示：
- 月度评分和总结
- 每日趋势图
```

### 6.3 弹窗设计

#### 6.3.1 区间明细弹窗

```html
<div class="modal-content">
    <div class="modal-header">
        <h5>张三 (2026-05-01 ~ 2026-05-15)</h5>
        <button class="close">&times;</button>
    </div>
    <div class="modal-body">
        <!-- 区间汇总统计 -->
        <div class="range-summary">
            <span>提交：30 次</span>
            <span>代码：+600 / -300</span>
            <span>平均分：82</span>
        </div>
        <!-- 每日明细列表 -->
        <table class="daily-detail-table">
            <thead>
                <tr>
                    <th>日期</th>
                    <th>提交</th>
                    <th>新增</th>
                    <th>删除</th>
                    <th>评分</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>2026-05-01</td>
                    <td>3</td>
                    <td>+50</td>
                    <td>-20</td>
                    <td>80</td>
                    <td><button class="btn-detail">详情</button></td>
                </tr>
            </tbody>
        </table>
    </div>
</div>
```

#### 6.3.2 月度详情弹窗

```html
<div class="modal-content">
    <div class="modal-header">
        <h5>张三 2026年5月 月度详情</h5>
        <button class="close">&times;</button>
    </div>
    <div class="modal-body">
        <!-- 月度评分 -->
        <div class="monthly-score">
            <div class="score">85</div>
            <div class="grade">良好</div>
        </div>
        <!-- 月度总结 -->
        <div class="monthly-summary">
            <h6>月度评分简述</h6>
            <p>本月代码质量整体良好，完成了用户模块重构...</p>
        </div>
        <!-- 月度主要工作 -->
        <div class="monthly-work">
            <h6>月度主要工作</h6>
            <ol>
                <li>完成用户模块重构</li>
                <li>修复支付Bug</li>
            </ol>
        </div>
        <!-- 每日趋势图 -->
        <div class="daily-trend">
            <h6>每日趋势</h6>
            <div id="chartMonthlyTrend"></div>
        </div>
    </div>
</div>
```

---

## 7. 定时任务设计

### 7.1 月度聚合任务

**调度配置：**
- 时间：每月1日 02:00
- 依赖：daily 数据已完整生成（上月所有天数）

**任务流程：**
```
1. 计算上月年月（如当前是 2026-06-01，则处理 2026-05）
2. 查询上月所有 daily 数据
3. 按 author_email 分组
4. 对每个作者：
   a. 聚合统计数据（求和）
   b. 合并项目列表（去重）
   c. 调用 LLM 生成月度总结
   d. UPSERT 写入 monthly 表
5. 记录任务日志
```

### 7.2 代码实现

在 `scheduler.py` 中新增：

```python
def run_monthly_efficiency_aggregation():
    """每月1日凌晨执行，汇总上月数据"""
    from datetime import date
    import calendar

    today = date.today()
    # 计算上月
    if today.month == 1:
        year = today.year - 1
        month = 12
    else:
        year = today.year
        month = today.month - 1

    year_month = f"{year}-{month:02d}"

    logger.info(f"开始月度能效聚合: {year_month}")

    from app.models import Settings
    from app.security import security_service
    from app.services.efficiency_monthly_aggregator import EfficiencyMonthlyAggregator

    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings:
            logger.error("未找到系统配置")
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
        logger.info(f"月度能效聚合完成: {result}")
    except Exception as e:
        logger.exception(f"月度能效聚合失败: {e}")
    finally:
        db.close()
```

---

## 8. 文件清单

### 8.1 新增文件

| 文件 | 说明 |
|------|------|
| `app/models/employee_efficiency_monthly.py` | 月度汇总模型 |
| `app/services/efficiency_monthly_aggregator.py` | 月度聚合服务 |

### 8.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `app/api/efficiency.py` | 新增月度和区间查询端点 |
| `app/static/js/efficiency.js` | Tab 切换、区间查询、弹窗交互 |
| `app/services/scheduler.py` | 新增月度定时任务 |
| `app/models/__init__.py` | 导出新模型 |

### 8.3 测试文件

| 文件 | 说明 |
|------|------|
| `tests/test_models/test_employee_efficiency_monthly.py` | 月度模型测试 |
| `tests/test_services/test_efficiency_monthly_aggregator.py` | 月度聚合服务测试 |
| `tests/test_api/test_efficiency_monthly.py` | 月度 API 测试 |

---

## 9. 实施计划

### 阶段一：数据模型（1天）
- [ ] 创建 `employee_efficiency_monthly` 模型
- [ ] 编写模型测试
- [ ] 生成数据库迁移

### 阶段二：服务层（2天）
- [ ] 实现 `EfficiencyMonthlyAggregator`
- [ ] 实现 LLM 月度 Prompt
- [ ] 编写服务测试

### 阶段三：API 层（1天）
- [ ] 实现月度列表/详情端点
- [ ] 实现区间汇总/明细端点
- [ ] 编写 API 测试

### 阶段四：前端（2天）
- [ ] Tab 切换功能
- [ ] 日期区间选择器
- [ ] 区间明细弹窗
- [ ] 月度详情弹窗
- [ ] 图表适配

### 阶段五：定时任务（0.5天）
- [ ] 实现月度聚合任务
- [ ] 配置调度

### 阶段六：测试与优化（1天）
- [ ] 集成测试
- [ ] 性能优化
- [ ] 文档更新

---

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| LLM 调用失败 | 月度总结缺失 | 记录失败状态，支持手动重试 |
| 数据量大 | 查询性能差 | 添加索引，分页查询 |
| 时区问题 | 日期计算错误 | 统一使用 UTC，前端转换 |
| 并发写入 | 数据不一致 | 使用 UPSERT，唯一约束 |

---

## 11. 附录

### 11.1 参考文件

- 现有模型：`app/models/employee_efficiency.py`
- 现有 API：`app/api/efficiency.py`
- 现有服务：`app/services/efficiency_aggregator.py`
- 现有 LLM：`app/services/efficiency_llm.py`
- 现有前端：`app/static/js/efficiency.js`

### 11.2 术语表

| 术语 | 说明 |
|------|------|
| daily | 人员能效日度明细表 |
| monthly | 人员能效月度汇总表 |
| LLM | 大语言模型，用于生成工作总结 |
| UPSERT | 插入或更新操作 |

---

## 12. 审查发现与更新

**日期**: 2026-05-29
**审查工具**: plan-eng-review + 外部独立审查

### 12.1 范围缩减

| 原方案 | 缩减后 |
|--------|--------|
| 新增 `/range/summary` 独立端点 | 合并到 `/list` 端点（已支持 start_date/end_date） |
| 新增 `/range/detail` 独立端点 | 合并到 `/detail` 端点 |
| 9 个文件 | 7 个文件 |

### 12.2 架构改进

| 问题 | 解决方案 |
|------|----------|
| DRY 违反 | 提取 `BaseAggregator` 基类，公共逻辑复用 |
| 数据完整性 | 月度聚合前检查上月 daily 数据是否完整 |

### 12.3 代码质量改进

| 问题 | 解决方案 |
|------|----------|
| JSON 解析可能抛异常 | 添加 try/except 防御性解析 |

### 12.4 性能改进

| 问题 | 解决方案 |
|------|----------|
| LLM 批量调用风险 | 串行调用，2 秒间隔，失败重试 3 次 |
| 月度聚合耗时 | 添加进度日志，每处理一个作者记录一次 |

### 12.5 业务逻辑补充

| 问题 | 解决方案 |
|------|----------|
| review_score 聚合逻辑未定义 | 取当月日度评分的算术平均值 |
| 幂等性策略 | UPSERT 覆盖写，支持手动重新聚合 |
| 数据完整性检查失败 | 跳过该月，记录警告，支持手动重试 |

### 12.6 未解决问题

| 问题 | 用户决策 |
|------|----------|
| 区间查询与月度查询数据一致性 | 不处理，接受可能的延迟 |
| 区间查询性能约束 | 通过索引和分页缓解 |

### 12.7 测试计划更新

新增测试用例：
- LLM 限流和重试测试
- review_score 算术平均测试
- JSON 防御性解析测试
- 数据完整性检查测试
- 进度日志测试

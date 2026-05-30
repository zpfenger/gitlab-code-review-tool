# 人员能效模块 — 设计文档

- 状态：草案 v1（待用户确认）
- 日期：2026-05-28
- 范围：内部页面 + 数据沉淀（外部 API 延后）

---

## 1. 目标与背景

### 1.1 目标
为现有 GitLab 代码审查平台新增"人员能效"模块，让管理员可以从"人"的维度看到每天的产出与代码质量。本期专注内部页面与数据沉淀，对外接口在数据稳定后再开放。

### 1.2 痛点
- 现有 `MrReviewLog` 是按"合并请求"打分，一次 MR 可能包含多人提交，**得分无法精准归属到个人**。
- 现有 `CommitRecord` 只存提交元数据，**没有 additions/deletions 字段**，无法直接出代码量报表。
- 代码量、提交内容、质量评分散落在不同流程，**缺少"按人 × 天"聚合视图**。

### 1.3 不在本次范围
- 外部系统 API（X-API-Token 鉴权、单人/全员接口）—— 延后到二期。
- 按项目维度筛选（已确认本期只按人员维度展示）。
- 团队排名/趋势图等扩展能效接口（一期最小化）。

---

## 2. 整体方案

一句话：**日报任务跑完时，按 `author_email × stat_date` 聚合产出新表 `employee_efficiency_daily`，前端新增"人员能效"菜单做表格视图 + 详情下钻。**

### 2.1 架构示意

```
┌──────────────────┐    日报任务跑完后
│  TaskExecutor    │ ───────────────────────► 按 author 分组聚合 ─┐
│  run_daily_review│                                              │
└──────────────────┘                                              ▼
                                                  ┌──────────────────────────┐
                                                  │ EfficiencyAggregator     │
                                                  │ (新服务)                  │
                                                  │  · 算 additions/deletions │
                                                  │  · 调 LLM 一次拿评分+总结  │
                                                  └──────────┬───────────────┘
                                                             │
                                                             ▼
                                              ┌──────────────────────────────┐
                                              │ employee_efficiency_daily 表 │
                                              │ (人 × 天 聚合)                │
                                              └──────────────┬───────────────┘
                                                             │
              ┌──────────────────────────────────────────────┴───────┐
              │                                                       │
              ▼                                                       ▼
┌─────────────────────────┐                          ┌──────────────────────────┐
│  /api/efficiency/...    │                          │ /api/efficiency/recompute│
│  内部 API（session 鉴权）│                          │ 管理员手动补算           │
└────────────┬────────────┘                          └────────────┬─────────────┘
             │                                                    │
             ▼                                                    ▼
       前端"人员能效"页面                                    日报任务复用入口
       (表格 + 排序 + 详情下钻)
```

### 2.2 数据流职责
| 阶段 | 模块 | 职责 |
|---|---|---|
| 采集 | `TaskExecutor.run_daily_review` | 维持现状（拉 commits 做审查），跑完调聚合器 |
| 聚合 | `EfficiencyAggregator`（新建） | 按 author 分组、调 LLM、写入新表 |
| 存储 | `employee_efficiency_daily` 表 | 单一数据源，唯一索引保证幂等 |
| 展示 | `/api/efficiency/*` + 前端页面 | 查表为主、补算入口为辅 |

---

## 3. 数据模型

### 3.1 新表：`employee_efficiency_daily`（人员能效明细表）

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| `id` | INT PK | ✅ | 主键 |
| `author_email` | VARCHAR(200) | ✅ | 人员邮箱（聚合主维度） |
| `author_name` | VARCHAR(100) | ✅ | 提交时显示名（冗余便于展示） |
| `stat_date` | DATE | ✅ | 统计日期（自然日） |
| `commits_count` | INT | ✅ | 提交次数（跨分支跨项目去重后） |
| `additions` | INT | ✅ | 新增行数总和 |
| `deletions` | INT | ✅ | 删除行数总和 |
| `files_changed` | INT | ✅ | 涉及文件数（去重） |
| `new_files` | INT | ✅ | 新建文件数 |
| `deleted_files` | INT | ✅ | 删除文件数 |
| `projects_involved` | TEXT | ✅ | 当天涉及项目名 JSON 数组 `["proj-a","proj-b"]` |
| `review_score` | INT | ❌ | LLM 综合评分 0-100（NULL 表示未评） |
| `review_grade` | VARCHAR(10) | ❌ | 等级：`优秀`/`良好`/`一般`/`待改进` |
| `review_summary` | TEXT | ❌ | 评分简述（1-2 句） |
| `work_summary` | TEXT | ❌ | LLM 工作总结 JSON 数组 `["实现登录","修复X bug",...]` |
| `summary_top_n` | INT | ❌ | 生成时使用的 top_n（默认 5） |
| `llm_status` | VARCHAR(20) | ✅ | LLM 状态：`pending`/`success`/`failed`/`skipped` |
| `llm_error` | TEXT | ❌ | LLM 失败原因 |
| `created_at` / `updated_at` | DATETIME | ✅ | BaseModel 已有 |

**索引**
- `UNIQUE (author_email, stat_date)` —— 保证幂等，重复跑日报会 UPSERT 而不重复插入。
- `INDEX (stat_date)` —— 支持按日期范围查询。
- `INDEX (author_email, stat_date DESC)` —— 个人详情趋势查询。

**等级映射规则**（写入时计算，避免查询时反复算）
- `>= 90` → 优秀
- `75 ~ 89` → 良好
- `60 ~ 74` → 一般
- `< 60` → 待改进

### 3.2 不动现有表
- `CommitRecord`、`MrReviewLog`、`PushReviewLog`、`TaskLog`、`Project`：保持原样。
- 一是符合 SOLID 单一职责，二是避免历史数据回填。

---

## 4. 聚合逻辑（EfficiencyAggregator）

### 4.1 触发点
在 `TaskExecutor.run_daily_review` 完成（无论成功或部分成功）后，**同步调用** `EfficiencyAggregator.aggregate(project_id, target_date)`。

> 同步而非异步：日报任务本身就在后台执行，再加一层异步只增加复杂度。聚合失败不影响日报本身。

### 4.2 聚合步骤

```python
def aggregate(target_date: date) -> None:
    """
    1. 收集 target_date 当天所有项目的提交（GitLab API）
       - 跨分支去重（同一 commit_sha 只算一次）
       - 排除 merge commit
    2. 按 author_email 分组
    3. 对每个作者：
       a. 累加 additions/deletions/files_changed/new_files/deleted_files
       b. 收集涉及的项目名列表（去重）
       c. 调 1 次 LLM 同时输出评分 + top_n 工作总结
       d. 计算 review_grade
       e. UPSERT 到 employee_efficiency_daily
    """
```

**关键约束**
- 跨分支去重：用 `set(commit_sha)`，沿用 `task_executor` 现有思路。
- 跨项目合并：所有项目的 commits 拍平后再按 author 分组（同一人多项目提交合并）。
- LLM 失败不阻塞：单人 LLM 失败 → 该人记录 `llm_status='failed'` + 错误信息，代码量字段照常入库，下次补算可重试。

### 4.3 LLM 调用策略（1A 决策）

**单次调用同时输出评分 + 工作总结**，复用 `webhook_reviewer` 的日报提示词体系，扩展输出契约：

输入：该人当天所有 commit 的 message + diff（用 `_truncate_text` 限制到 `review_max_tokens`）

输出格式（约定让 LLM 同时返回）：
```
## 评分明细
（保留原日报提示词的评分明细）

## 主要工作（不超过 N 条）
1. xxx
2. xxx
...

## 总分：XX 分
```

解析逻辑：
- `review_score`：复用 `WebhookReviewer.parse_review_score` 正则。
- `work_summary`：新增正则解析"主要工作"块，提取条目存为 JSON 数组。
- `review_summary`：取 LLM 输出中第一句评分总结或截断 200 字。

**top_n 参数**：默认 5。在 `Settings` 表加配置项 `efficiency_work_summary_top_n`，也可后续支持接口覆盖。

### 4.4 失败兜底（2A 决策）
- 日报任务跳过/失败 → 当天无 `employee_efficiency_daily` 数据。
- 前端检测到该日期数据缺失 → 显示"该日数据未生成" + 管理员可见"立即补算"按钮。
- 补算按钮调 `POST /api/efficiency/recompute?date=YYYY-MM-DD`，触发 `EfficiencyAggregator.aggregate(date)`。
- 补算入口与日报任务执行器共享同一聚合方法，逻辑零分支。

---

## 5. 后端 API（内部）

所有 API 走现有 session 鉴权（`get_current_user_full`）。

### 5.1 列表查询

```
GET /api/efficiency/list
  ?date=YYYY-MM-DD              # 单日，默认昨天
  ?start_date=...&end_date=...  # 区间，与 date 互斥
  ?sort_by=score|additions|deletions|commits  # 默认 score
  ?order=desc|asc                # 默认 desc
  ?limit=50&offset=0
```

返回：
```json
{
  "success": true,
  "data": {
    "items": [
      {
        "author_email": "x@y.com",
        "author_name": "张三",
        "stat_date": "2026-05-27",
        "commits_count": 5,
        "additions": 230,
        "deletions": 45,
        "files_changed": 12,
        "projects_involved": ["proj-a", "proj-b"],
        "review_score": 85,
        "review_grade": "良好",
        "review_summary": "...",
        "work_summary": ["实现登录", "修复 X bug", "..."],
        "llm_status": "success"
      }
    ],
    "total": 30,
    "team_stats": {
      "avg_score": 82,
      "total_commits": 145,
      "total_additions": 3200,
      "total_deletions": 870,
      "person_count": 28
    }
  }
}
```

**权限过滤**（沿用现有体系）：
- 系统管理员：所有人。
- 项目管理员：只看自己管理项目下出现过提交的人员（用 `projects_involved` 与 `project_admins` 关联表过滤）。
- 项目成员：只看自己（按当前登录用户的 email 匹配）。

### 5.2 个人详情下钻

```
GET /api/efficiency/detail
  ?email=x@y.com
  ?date=YYYY-MM-DD     # 当日详情
  ?trend_days=7        # 趋势天数，默认 7，可选 7/30
```

返回：
```json
{
  "success": true,
  "data": {
    "summary": { /* 同 list 单行结构 */ },
    "trend": [
      {"stat_date": "2026-05-21", "commits": 3, "additions": 120, "score": 78},
      ...
    ],
    "commits": [
      {
        "commit_sha": "abc123",
        "project_name": "proj-a",
        "branch": "feature/x",
        "message": "fix: ...",
        "commit_date": "2026-05-27T10:23:00",
        "additions": 30,
        "deletions": 5
      }
    ]
  }
}
```

`commits` 直接查 `commit_records` 表 + 实时从 GitLab 拉 diff 行数（一个人当天的 commit 通常不多，开销可接受）。

### 5.3 手动补算（仅管理员）

```
POST /api/efficiency/recompute
  Body: { "date": "YYYY-MM-DD", "force": false }
```

- 系统管理员可见。
- `force=false`：仅缺失数据补算。`force=true`：覆盖重算（重新调 LLM）。
- 异步任务，返回 task_id，前端轮询状态。

### 5.4 失败重试

```
POST /api/efficiency/retry-llm
  Body: { "date": "YYYY-MM-DD", "emails": ["x@y.com", ...] }
```

只对 `llm_status='failed'` 的记录重跑 LLM 调用，不动代码量字段。

---

## 6. 前端

### 6.1 菜单与路由
- 新增侧边栏菜单 **"人员能效"**（图标如折线图），位于"Webhook 审查"之后。
- 路由：`/efficiency`
- 权限可见性：登录用户都可见菜单；接口层做数据过滤。

### 6.2 技术栈与依赖
- 使用 **ECharts** 做所有图表展示（团队概览卡片下方的趋势图、个人详情抽屉的趋势折线图、可选的排名柱状图）。
- 引入方式：CDN 或 npm 包（按现有前端约定选）。
- 主题：与现有页面色系对齐；评分颜色梯度（优秀=绿、良好=蓝、一般=黄、待改进=红）作为统一调色板。

### 6.3 主页面布局

```
┌─────────────────────────────────────────────────────────────┐
│ 人员能效                                                     │
├─────────────────────────────────────────────────────────────┤
│ [日期: 2026-05-27 ▼]  [人员: 全部 ▼]  [立即补算]            │
├─────────────────────────────────────────────────────────────┤
│ 团队概览                                                     │
│ ┌─────────┬─────────┬─────────┬─────────┬──────────┐       │
│ │ 总提交  │ 总新增  │ 总删除  │ 均分    │ 参与人数 │       │
│ │ 145     │ +3200   │ -870    │ 82      │ 28       │       │
│ └─────────┴─────────┴─────────┴─────────┴──────────┘       │
│                                                              │
│ [ECharts 图表区]                                             │
│  ┌──────────────────────┐  ┌──────────────────────┐         │
│  │ 代码量 TOP10 横向条形 │  │ 评分分布饼图          │         │
│  │ (新增/删除堆叠柱状)   │  │ (优秀/良好/一般/...)  │         │
│  └──────────────────────┘  └──────────────────────┘         │
├─────────────────────────────────────────────────────────────┤
│ 人员明细表（点击行查看详情）                                  │
│ ┌──────┬──────┬────┬────┬─────┬─────┬────┬────────┬─────┐  │
│ │ 姓名 │ 邮箱 │提交│新增│ 删除│ 文件│评分│ 等级   │ 项目│  │
│ ├──────┼──────┼────┼────┼─────┼─────┼────┼────────┼─────┤  │
│ │ 张三 │ ...  │ 5  │230 │ 45  │ 12  │ 85 │ 良好   │A,B │  │
│ │ 李四 │ ...  │ 8  │450 │ 120 │ 18  │ 91 │ 优秀   │ A  │  │
│ └──────┴──────┴────┴────┴─────┴─────┴────┴────────┴─────┘  │
└─────────────────────────────────────────────────────────────┘
```

**ECharts 图表说明**
- **代码量 TOP10 横向条形图**：堆叠展示每人 additions（绿）/ deletions（红），按总量降序，点击柱条可联动选中表格对应行。
- **评分分布饼图**：当日团队评分等级分布占比，点击扇区可筛选表格只显示对应等级的人员。
- 图表数据均来自 `/api/efficiency/list` 同一个接口的 `items`，前端本地聚合，**不增加后端调用**。

**交互**
- 列头点击 → 切换排序方向。默认按评分降序。
- 评分列彩色徽标（优秀=绿、良好=蓝、一般=黄、待改进=红）。
- 行可点击 → 右侧抽屉/弹窗显示个人详情。
- "立即补算"按钮：仅系统管理员可见，点击后触发当日补算。

### 6.4 个人详情抽屉

```
┌──── 张三 (2026-05-27) ─────────────────────────┐
│ 综合评分: 85 / 良好                              │
│ 评分总结: "代码质量良好，注释清晰..."             │
│                                                  │
│ 今日主要工作:                                    │
│   1. 实现用户登录功能                            │
│   2. 修复购物车价格计算 bug                      │
│   3. 重构订单服务的异常处理                      │
│   4. 补充单元测试 (覆盖率 75% → 82%)             │
│   5. 优化数据库慢查询                            │
│                                                  │
│ ── 近 7 天趋势 ──                               │
│ [ECharts 双 Y 轴折线图]                          │
│  · 左轴：代码量（新增/删除两条线）                │
│  · 右轴：评分（折线 + 阈值参考线）                │
│                                                  │
│ ── 今日提交列表 ──                              │
│ • [feature/login] fix: handle empty username    │
│   2026-05-27 10:23  +30 -5                       │
│ • [main] feat: add ...                          │
│   ...                                            │
└──────────────────────────────────────────────────┘
```

### 6.5 数据缺失态
- 该日 `employee_efficiency_daily` 无数据 → 表格区域显示空态："该日数据未生成。点击右上方'立即补算'生成。"
- 个别人员 `llm_status='failed'` → 评分列显示"评分失败"+ 重试图标。

---

## 7. 关键决策回顾

| 决策点 | 结论 |
|---|---|
| 人员标识 | `author_email`（最稳定） |
| 入库时机 | 日报任务跑完顺便聚合 |
| 表粒度 | 到人到天（跨项目跨分支合并） |
| 表名 | `employee_efficiency_daily`（人员能效明细表） |
| 行数维度 | 仅 additions / deletions（无"更新"概念） |
| 评分 + 总结 | 1 次 LLM 调用同时输出，复用日报提示词 |
| top_n | 默认 5，Settings 可配 |
| 失败兜底 | 管理员手动"立即补算"，复用日报执行器 |
| 项目筛选 | 不做，全部按人员维度 |
| 权限 | 沿用现有项目权限体系 |
| 外部 API | 本期不做，留待二期 |

---

## 8. 迁移与回填

### 8.1 数据库迁移
- 新建 `employee_efficiency_daily` 表，无需修改现有表。
- 通过 SQLAlchemy `create_all()` 或 alembic 增量迁移（依现有项目惯例选）。

### 8.2 历史数据回填
- 不强制回填。提供脚本 `scripts/backfill_efficiency.py`，可指定日期范围，按日调用 `EfficiencyAggregator.aggregate(date)`。
- 上线后由管理员按需补算最近 N 天数据。

---

## 9. 测试要点

- `EfficiencyAggregator`：跨分支去重、跨项目合并、LLM 失败兜底、UPSERT 幂等。
- 输出契约解析：评分正则、工作总结条目解析、`review_grade` 映射。
- API 权限：系统管理员/项目管理员/项目成员三档分别能看到的数据范围。
- 前端：排序、空态、详情抽屉、补算按钮的权限可见性。

---

## 10. 风险与权衡

| 风险 | 影响 | 缓解 |
|---|---|---|
| LLM 调用慢导致日报任务延长 | 30 人 × 单次 ~10s = 5 分钟 | LLM 调用串行但有重试；后续可并行化（asyncio.gather） |
| 同一人当天 commit 极多（>100）超 token 限制 | 截断后 LLM 看不全 | 现有 `_truncate_text` 已有；后续可分批调用合并结果（YAGNI 暂不做） |
| author_email 不规范（如机器人、空 commit author） | 出现奇怪行 | 聚合时过滤 email 为空或 `@noreply` 后缀的提交 |
| 历史日期数据不全 | 上线初期表为空 | 提供补算脚本，管理员按需触发 |

# Token 消耗量记录与统计 — 设计文档

日期：2026-06-11
状态：已与需求方确认

## 1. 背景与目标

系统当前有 3 处 LLM 调用（审查报告、Webhook 实时审查、人员能效评分），API 响应中的 `usage` 字段（`prompt_tokens` / `completion_tokens` / `total_tokens`）被直接丢弃。本功能将捕获并持久化每次调用的 token 消耗，提供：

1. 独立统计页面（明细 + 统计图），仅系统管理员可访问
2. 审查报告、Webhook 审查记录、人员能效三个业务页面的列表中展示每条记录的 token 消耗

### 范围决策（已确认）

- **仅记录 token 数量**，不折算费用金额
- **只记录成功的 LLM 调用**；失败/超时请求无 usage 可记；重试多次最终成功的只记成功那一次
- API 响应缺失 `usage` 字段时跳过不落库，记 warning 日志（避免 0 值污染统计）
- 业务页面以「列表加 token 列」形式展示
- 统计维度：按业务类型、按时间趋势、按模型（不做按项目统计，明细中保留 project_name 字段）
- 月度能效聚合（efficiency_monthly）已核实不直接调用 LLM，不在记录范围内

## 2. 数据模型

新增 `app/models/token_usage.py`：

```python
class TokenUsageLog(BaseModel):
    __tablename__ = "token_usage_log"

    biz_type = Column(String(50), nullable=False, comment="业务类型: report/webhook_mr/webhook_push/efficiency")
    biz_id = Column(Integer, nullable=True, comment="关联业务记录主键")
    project_name = Column(String(200), nullable=True, comment="项目名称")
    author = Column(String(200), nullable=True, comment="相关人员（能效场景）")
    model = Column(String(100), nullable=False, comment="LLM 模型名")
    prompt_tokens = Column(Integer, default=0, comment="输入 token")
    completion_tokens = Column(Integer, default=0, comment="输出 token")
    total_tokens = Column(Integer, default=0, comment="总 token")
    created_at_ts = Column(Integer, nullable=False, comment="调用时间戳")

    __table_args__ = (
        Index("idx_token_usage_biz", "biz_type", "biz_id"),
        Index("idx_token_usage_created_at_ts", "created_at_ts"),
    )
```

业务类型与关联关系（已核实调用链）：

| biz_type | 调用链 | biz_id 指向 |
|----------|--------|------------|
| `report` | task_executor → `CodeReviewer.review()` | `task_log.id`（一次任务多次调用 → 多条明细） |
| `webhook_mr` | webhook_handler → `WebhookReviewer` | `mr_review_log.id` |
| `webhook_push` | webhook_handler → `WebhookReviewer` | `push_review_log.id` |
| `efficiency` | efficiency_aggregator → `call_and_parse` | `employee_efficiency.id` |

- `biz_type` 为普通字符串列，未来新增 LLM 调用点只需新增取值，无需改表（开闭原则）
- 建表通过现有 `app/migration.py` 机制追加迁移

## 3. usage 采集方式

新增 `app/services/llm_usage.py`，usage 解析逻辑只写一处（DRY）：

```python
@dataclass(frozen=True)
class TokenUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

@dataclass(frozen=True)
class LLMResult:
    content: Optional[str]
    usage: Optional[TokenUsage]

def parse_usage(data: dict, model: str) -> Optional[TokenUsage]:
    """从 OpenAI 兼容响应中提取 usage；缺失或畸形返回 None"""
```

三处 LLM 调用函数的返回值改造（`Optional[str]` → 携带 usage）：

- `app/services/code_reviewer.py` — `CodeReviewer.review()`
- `app/services/webhook_reviewer.py` — `WebhookReviewer._call_llm()` / `review_and_strip_code()`
- `app/services/efficiency_llm.py` — `call_llm()` / `call_and_parse()`

**落库责任在调用方**（task_executor / webhook_handler / efficiency_aggregator）：只有调用方知道 biz_id——业务记录持久化拿到主键后，再插入 `token_usage_log`。

## 4. API 与独立统计页面

新增 `app/api/token_usage.py`（路由前缀 `/api/token-usage`，全部仅系统管理员可访问）：

| 端点 | 功能 |
|------|------|
| `GET /api/token-usage` | 明细分页列表，筛选：时间范围、biz_type、model、project_name |
| `GET /api/token-usage/stats` | 聚合统计，一次返回三组数据：①按业务类型汇总 ②按天趋势（区分 prompt/completion）③按模型汇总，均支持时间范围参数 |

时间范围参数沿用现有 webhook_reviews API 约定：`start_date` / `end_date`（ISO 日期字符串，end_date 含当天 23:59:59）。

新增页面 `/token-usage`（`app/templates/token_usage.html`）：

- 顶部汇总卡片：总消耗、今日、本月（total/prompt/completion 拆分）
- 中部图表区：业务类型饼图 + 按天趋势折线图 + 模型柱状图
- 底部明细表格：时间、业务类型、项目、人员、模型、prompt/completion/total，分页
- 图表库复用已本地化的 Chart.js（`/static/vendor/chartjs/`，与 webhook_reviews 页一致）
- `base.html` 导航栏新增入口，仅 system_admin 渲染；页面路由同 settings 页模式做权限校验（非管理员重定向）

## 5. 三个业务页面的 token 列

落库后通过 `(biz_type, biz_id IN (...))` 一次批量查询补充字段，不改业务表结构：

| 页面 | 展示方式 |
|------|---------|
| Webhook 审查记录（MR/Push 列表） | 每行新增「Token 消耗」列，详情中显示 prompt/completion 拆分 |
| 审查报告（reports） | 按 `task_log.id` 聚合后显示该报告任务的总消耗 |
| 人员能效（日报列表） | 每行新增「Token 消耗」列（该人当天评分调用的消耗） |

- token 列的可见性跟随记录本身的权限（能看到这条记录就能看到它的消耗），无额外权限逻辑
- 历史存量记录无 usage 数据，显示 `-`

## 6. 错误处理

- usage 落库全程 try/except 包裹，失败仅 `logger.warning`，绝不影响审查/能效主流程
- `parse_usage` 对缺失/畸形 usage 返回 None，调用方跳过落库

## 7. 测试计划

遵循仓库 TDD 规范（先写测试，80%+ 覆盖率）：

- `tests/test_models/test_token_usage.py` — 模型字段/索引
- `tests/test_services/test_llm_usage.py` — `parse_usage` 解析（正常/缺失/畸形 usage）
- 三处调用方落库行为测试（mock LLM 响应带 usage，断言 token_usage_log 写入正确 biz_type/biz_id）
- `tests/test_api/test_token_usage.py` — 明细/统计端点 + 权限（非管理员 403）

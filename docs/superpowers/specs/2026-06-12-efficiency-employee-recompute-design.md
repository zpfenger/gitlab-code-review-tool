# 人员能效补算 — 指定人员重算 设计文档

- 日期：2026-06-12
- 状态：已确认（待实现）
- 范围：在现有"立即补算"功能基础上，增加按邮箱指定人员重算的能力，按天与按月均支持

## 1. 背景与目标

现有"立即补算"（`#recomputeModal`）只能对整个日期范围或整月做**全员**补算。当某个人的能效数据算错、缺失，或新员工历史数据需要补齐时，只能整体重算，成本高且会触动其他人的数据。

目标：允许管理员在"立即补算"中输入指定人员邮箱，仅重算这些人员的能效数据，其余人员记录不受影响。按天、按月两种模式都支持。

## 2. 关键决策（来自需求澄清）

| 决策点 | 结论 |
|--------|------|
| 邮箱输入数量 | 支持多个，输入框文本按逗号/分号/换行/空格分隔 |
| 覆盖行为 | 保留 force 复选框控制；not force 只补缺失，force 覆盖已有 |
| 实现方式 | 方案 A：在现有聚合器 `aggregate()` 增加 `only_emails` 参数，复用整条补算链路 |
| 邮箱无数据/拼错 | 宽松处理，不报错；完成后通过结果统计体现 |
| UI 形态 | 复用同一 modal，新增可选邮箱输入框，留空=全员补算 |
| 权限 | 不变，仍仅系统管理员 |

## 3. 现状（实现依据）

- 端点：
  - 按天 `POST /api/efficiency/recompute`，`RecomputeRequest{start_date, end_date, force}` → 线程 `_run_daily_recompute(start, end, force)`
  - 按月 `POST /api/efficiency/monthly/recompute`，`MonthlyRecomputeRequest{year_month, force}` → 线程 `_run_monthly_recompute(year_month, force)`
  - 进度 `GET /api/efficiency/recompute/status`、取消 `POST /api/efficiency/recompute/cancel`
  - 全局单任务状态 `_recompute_task` + `_recompute_lock`，前端 3s 轮询
- 聚合器（当前均处理全员，无人员过滤参数）：
  - `EfficiencyAggregator.aggregate(target_date)`：拉取全部 active 项目当天提交 → 按 author 分组 `per_author` → 逐个 `_upsert_author`（UPSERT，不删除其他人记录）
  - `EfficiencyMonthlyAggregator.aggregate(year_month)`：从 `EmployeeEfficiencyDaily` 查全月 → 按 `author_email` 分组 → 逐个 `_aggregate_author`（UPSERT）
- 跳过逻辑现状：
  - 按天：`_run_daily_recompute` 中 not force 时，当天存在**任意**记录即跳过整天
  - 按月：`monthly_recompute` 端点中 not force 时，该月存在**任意** monthly 记录即整月跳过（直接返回，不启动任务）

> 关键约束：GitLab commit 无法可靠按 author 过滤，按天必须拉取全部提交后再按邮箱筛选写入——这是 GitLab API 限制，指定人员重算无法跳过拉取阶段，但只会 UPSERT 指定人员，不影响其他人。

## 4. 详细设计

### 4.1 聚合器接口变更（新增可选参数，默认 None=全员，向后兼容）

```python
# app/services/efficiency_aggregator.py
def aggregate(self, target_date: date, only_emails: set[str] | None = None) -> Dict[str, Any]:
    # 拉取/分组逻辑完全不变。
    # 写入循环增加过滤：
    for email, data in per_author.items():
        if only_emails is not None and email not in only_emails:
            continue
        self._upsert_author(email, data, target_date)
    # result 沿用现有 authors_total/success/failed 字段，无需新增。
```

```python
# app/services/efficiency_monthly_aggregator.py
def aggregate(self, year_month: str, only_emails: set[str] | None = None) -> Dict[str, Any]:
    # 在 daily 查询上追加过滤：
    if only_emails:
        query = query.filter(EmployeeEfficiencyDaily.author_email.in_(list(only_emails)))
    # 后续分组、_aggregate_author 不变。
```

`_upsert_author`、`_aggregate_author`、LLM 调用逻辑均不改动。

### 4.2 请求体与后台线程

```python
class RecomputeRequest(BaseModel):
    start_date: str
    end_date: str
    force: bool = False
    emails: list[str] | None = None  # 新增

class MonthlyRecomputeRequest(BaseModel):
    year_month: str
    force: bool = False
    emails: list[str] | None = None  # 新增
```

后台线程签名：
- `_run_daily_recompute(start, end, force, only_emails: set[str] | None = None)`
- `_run_monthly_recompute(year_month, force, only_emails: set[str] | None = None)`

### 4.3 force 控制下的"按人员"跳过逻辑（核心改造）

因保留 force 复选框，"跳过"判断需从整天/整月细化到按人员。

**按天**（`_run_daily_recompute`，对每个 `current` 日期）：
- `only_emails` 为空 → 维持原逻辑（当天任意记录则跳过整天）
- `only_emails` 非空：
  - **not force**：查询当天已有记录的指定邮箱集合 `existing`（`EmployeeEfficiencyDaily.stat_date == current AND author_email IN only_emails`）；`remaining = only_emails - existing`。`remaining` 为空 → 该天计入 skipped；否则 `aggregate(current, only_emails=remaining)`
  - **force**：`aggregate(current, only_emails=only_emails)`

**按月**（`monthly_recompute` 端点）：
- `emails` 为空 → 维持原逻辑（整月判断）
- `emails` 非空：
  - **not force**：查询该月已有 monthly 记录的指定邮箱集合；`remaining = 指定 - 已有`。`remaining` 为空 → 返回"已存在，无需重算"（不启动任务）；否则启动任务并传 `remaining`
  - **force**：启动任务，传全部指定邮箱
- `_run_monthly_recompute` 将 `only_emails` 透传给 `aggregate(year_month, only_emails)`

语义与全员模式保持一致：not force 只补缺失，force 覆盖已有。

### 4.4 邮箱解析、匹配与进度状态

- 前端：输入框文本按 `,` / `;` / 换行 / 空格 分隔 → 逐项 `trim` → 去空项 → 去重 → 数组；非空时放入 `body.emails`
- 后端：解析为列表后统一 `strip` + **小写规范化**为 `set`，与现有 `excluded_emails` 处理方式完全一致（构造器 `set(e.lower() ...)`、过滤 `email.lower() in ...`）
- 匹配**大小写不敏感**（daily 表 `author_email` 存原始大小写，取自 commit 仅 strip，故必须小写比较而非直接 `in_`）：
  - 按天 `aggregate`：`if only_emails is not None and email.lower() not in only_emails: continue`
  - 按月 `aggregate`：`query.filter(func.lower(EmployeeEfficiencyDaily.author_email).in_(list(only_emails)))`
  - API 层"已有记录"跳过查询：同样用 `func.lower(author_email).in_(...)` 比较
- 仅做"非空"基础校验，不做严格 RFC 邮箱校验
- `_recompute_task` 状态字典新增字段 `target_emails: list[str]`（空=全员），两个端点启动任务时写入，`/recompute/status` 返回，供前端完成通知区分全员/指定人员（页面刷新恢复轮询时仍准确）

### 4.5 前端 UI

- `app/templates/efficiency.html`：在 `#recomputeModal` 的 force 复选框上方新增
  - `<textarea id="recomputeEmails" rows="3">` + 提示文案"指定人员邮箱（可选）：多个用逗号或换行分隔，留空则补算全员"
- `app/static/js/efficiency.js`：
  - `openRecomputeModal()`：清空 `#recomputeEmails`
  - `confirmRecompute()`：解析邮箱数组，非空时加入 `body.emails`（按天/按月分支共用解析逻辑）
  - `recomputeModalDesc`：指定人员时追加"（仅重算 N 位指定人员）"
  - 完成通知：根据 `status.target_emails` 是否非空，区分"补算完成"与"指定人员重算完成"

### 4.6 错误处理与边界

- 指定邮箱当天/当月无提交数据：不报错，照常完成；该邮箱不出现在结果统计中（日志 + 通知体现实际处理人数）
- 全部指定邮箱均已有数据且 not force：按天逐日 skipped；按月返回"已存在，无需重算"
- `emails` 为空数组或全为空白：等价于全员补算（不传 `only_emails`）
- 任务并发：沿用现有 `_recompute_lock` 单任务串行约束，不变

## 5. 测试计划

- **聚合器单测**（`test_services/`）：
  - `EfficiencyAggregator.aggregate(only_emails={...})` 仅写入指定人；`only_emails=None` 全员写入（回归）
  - `EfficiencyMonthlyAggregator.aggregate(only_emails={...})` 仅聚合指定人
- **API 单测**（`test_api/test_efficiency.py`、`test_efficiency_monthly.py`）：
  - 带 `emails` 的请求体解析正确
  - 按天 not force：只补当天缺失的指定人；force：覆盖指定人
  - 按月 not force / force 同上；全已存在时正确跳过
  - `emails` 为空 → 走全员路径（向后兼容回归）
- **边界**：无数据邮箱、全已存在跳过、空白输入归一为全员

## 6. 影响文件

| 文件 | 改动 |
|------|------|
| `app/api/efficiency.py` | 两个请求体加 `emails`；两个端点解析邮箱 + 按人员跳过逻辑；两个后台线程透传；`_recompute_task` 加 `target_emails`；status 返回该字段 |
| `app/services/efficiency_aggregator.py` | `aggregate` 加 `only_emails` 参数 + 写入过滤 |
| `app/services/efficiency_monthly_aggregator.py` | `aggregate` 加 `only_emails` 参数 + 查询过滤 |
| `app/templates/efficiency.html` | `#recomputeModal` 新增邮箱输入框 |
| `app/static/js/efficiency.js` | `openRecomputeModal` / `confirmRecompute` / 描述与完成通知 |
| 测试文件 | 新增上述单测 |

## 7. 设计原则落点

- **KISS / DRY**：复用现有补算链路与进度/取消/锁机制，过滤逻辑集中在聚合器
- **SRP**：人员过滤归聚合器，跳过策略归 API 层，UI 归前端
- **向后兼容**：`only_emails=None` 即原全员行为，老调用与既有测试不受影响
- **YAGNI**：不新增独立端点/线程，不做严格邮箱校验

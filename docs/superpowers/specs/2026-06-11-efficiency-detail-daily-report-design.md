# 人员能效明细日报入口优化设计

**日期**: 2026-06-11
**状态**: 待复核
**范围**: 人员能效明细抽屉、日报详情弹窗、详情接口返回结构、报告路径读取兼容

## 1. 目标

优化人员能效明细页的个人详情视图：

1. 在“近 7 天趋势”上方展示当前人员在 `summary.stat_date` 对应日期的“审查报告-日报”入口。
2. 日报入口按项目区分，返回数据中必须包含项目名称。
3. 点击日报入口后，在新的弹窗中展示日报 Markdown 内容。
4. 删除个人详情抽屉底部的“今日提交”内容。

## 2. 推荐方案

采用方案 A：扩展 `GET /api/efficiency/detail` 的响应，让接口直接返回当前人员在所查统计日期可查看且可打开的日报列表。前端在详情抽屉内渲染日报入口，点击后复用现有 `/api/reports/content` 读取报告正文，并在新的日报详情弹窗中展示。

选择该方案的原因：

- 能效详情接口已经掌握人员、日期和权限上下文，适合一次返回详情页需要的元数据。
- 报告正文仍由 `/api/reports/content` 读取，继续复用既有路径校验、项目权限和本人可见性规则；`daily_reports` 生成时也必须使用同一套可见性判断，保证“列得出来就一定打得开”。
- 前端无需跳转到审查报告页，用户可以在人员明细里直接查看对应项目日报。

## 3. API 设计

`GET /api/efficiency/detail` 在单日模式下新增 `daily_reports` 字段：

```json
{
  "success": true,
  "data": {
    "summary": {},
    "trend": [],
    "commits": [],
    "daily_reports": [
      {
        "project": "project-a",
        "type": "daily",
        "date": "2026-06-10",
        "author": "Alice",
        "filename": "project-a/daily/2026-06-10/Alice.md",
        "size": 12345
      }
    ]
  }
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `project` | 项目名称，用于按项目分组和展示 |
| `type` | 固定为 `daily` |
| `date` | 日报日期 |
| `author` | 报告文件作者标识，即 Markdown 文件名去掉 `.md` 后的值 |
| `filename` | 与 `/api/reports` 列表接口保持一致的报告相对路径，例如 `project-a/daily/2026-06-10/Alice.md`；前端将它作为 `/api/reports/content` 的 `path` 参数 |
| `size` | 文件大小，便于后续展示或调试 |

生成规则：

- 如果单日详情没有 `summary`，返回 `daily_reports: []`。
- 根据 `summary.stat_date` 对应日期和 `summary.projects_involved` 查找 `data/reports/{project}/daily/{date}/` 下的 Markdown 日报。
- 日报文件现有结构为“项目 + 日期 + 作者文件名”，作者文件名来自日报生成时的提交作者姓名，不保证等于邮箱。
- 查找时用报告文件 `stem` 做忽略大小写的精确匹配；不做子串匹配。
- 匹配候选值仅包含 `summary.author_name` 和 `summary.author_email` 的完整值。为避免误关联，不使用邮箱前缀兜底。
- 不允许只用邮箱直接拼接报告路径；返回给前端的 `filename` 必须来自实际存在的报告文件相对路径。
- 只返回当前用户有权限查看且 `/api/reports/content` 会允许读取的项目报告。
- 每个列表项必须包含 `project` 项目名称。
- 如果没有日报，返回空数组，不影响评分、工作总结和趋势展示。

路径读取兼容：

- 现有日报写入侧只替换作者名中的 `/` 和 `\`，因此可能落盘为 `Zhang Peng.md`。
- 现有 `/api/reports/content` 在 `path` 模式下会解析 `author` 后重新 `_sanitize_filename` 并重建路径，导致含空格文件名变成 `Zhang_Peng.md` 而 404。
- 本次实现纳入修复：当 `/api/reports/content` 传入 `path` 时，服务端应在解析、权限校验和 `_validate_path` 通过后，使用传入的真实相对路径读取文件，不再用 sanitize 后的 author 重建路径。
- 单独传 `project/report_type/report_date/author` 的兼容模式保持现有行为。

## 4. 前端设计

个人详情抽屉展示顺序调整为：

1. 综合评分
2. 评分简述
3. 今日主要工作
4. 当前人员审查报告-日报
5. 近 7 天趋势

“今日提交”区块删除。

日报区域展示：

- 标题：`当前人员审查报告-日报`
- 按 `project` 分组或逐项展示项目按钮。
- 每个入口显示项目名称和日报日期。
- 点击入口后保持详情抽屉打开，同时打开新的日报详情弹窗；请求 `/api/reports/content?path=${encodeURIComponent(report.filename)}`。

日报详情弹窗：

- 标题显示：`{project} / {date} / {author}`
- 正文使用现有 Markdown 渲染与净化逻辑。
- 加载中、加载失败、无内容都显示简短状态。

## 5. 权限与错误处理

- `/api/efficiency/detail` 继续沿用 `can_view_person_detail` 限制人员详情。
- 日报列表生成时必须复用 `/api/reports/content` 的项目和本人可见性规则：先判断项目是否在当前用户可读项目内，再按项目调用 `should_limit_to_self_for_project`；如果需要限制到本人，则必须满足 `is_self_identity(current_user, report_file.stem)`。
- 这样普通用户或非管理项目的项目管理员不会看到自己点开会 403 的日报入口；如果本地账号身份值没有包含 Git 提交显示名，该日报入口也不会展示，行为与审查报告正文接口保持一致。
- `/api/reports/content` 作为正文读取的最终权限闸口。
- 报告文件不存在时，日报列表不返回该项；弹窗正文读取失败时展示“日报加载失败”。

## 6. 测试计划

后端测试：

- 详情接口返回的 `daily_reports` 包含项目名称 `project`。
- 多项目日报按项目分别返回。
- 没有日报时返回空数组。
- 无权限项目日报不返回。
- `summary` 为 `None` 时返回空 `daily_reports`。
- `author_name` 与报告文件 stem 忽略大小写精确匹配时返回日报。
- `author_email` 完整值与报告文件 stem 忽略大小写精确匹配时返回日报。
- 不用邮箱前缀做匹配，避免误关联同名前缀文件。
- 含空格作者名的日报路径，例如 `/api/reports/content?path=project-a%2Fdaily%2F2026-06-10%2FZhang%20Peng.md`，能正确读取。
- 普通用户受 `should_limit_to_self_for_project + is_self_identity` 限制时，不返回点开会 403 的日报入口。
- `filename` 字段语义与 `/api/reports` 一致，为报告相对路径。

前端测试：

- 个人详情抽屉在“近 7 天趋势”上方展示日报区域。
- 点击日报入口调用 `/api/reports/content` 并打开新的弹窗。
- “今日提交”区块不再渲染。

## 7. 非目标

- 不调整日报生成逻辑。
- 不修改报告正文接口的响应结构；只修正 `path` 模式读取真实相对路径的行为。
- 不新增报告下载、删除或跨日期浏览能力。
- 不移除 `GET /api/efficiency/detail` 现有 `commits` 字段，前端不再渲染“今日提交”区块，但后端保留该字段以兼容已有调用方。

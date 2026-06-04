# GitLab Code Review Tool

基于 **FastAPI** 的 GitLab 代码审查平台，支持两种审查模式：

1. **定时任务审查**：按项目执行日报/周报审查并生成报告。
2. **Webhook 实时审查**：接收 GitLab MR/Push 事件，调用 AI 即时审查并通知团队。

---

## 核心能力

- 用户认证与 RBAC 权限管理（系统管理员 / 项目管理员 / 普通用户）
- 项目级配置（GitLab/SVN/通知渠道可覆盖全局配置）
- 日报/周报任务执行与状态追踪
- Webhook 实时审查（MR / Push）
- 审查结果入库、查询、统计、可视化
- 审查结果回写 GitLab（MR Note / Push Note）
- 多渠道通知（企业微信）
- 敏感字段加密存储（Token / Webhook URL 等）
- **人员能效分析**（日度/月度聚合、LLM 智能评分、团队概览与个人详情）

---

## 技术栈

- **Backend**: FastAPI, SQLAlchemy
- **Frontend**: Jinja2, Bootstrap, Chart.js
- **Database**: SQLite（默认，可替换）
- **LLM**: OpenAI 兼容风格 API
- **Logging**: Loguru
- **Python**: >= 3.9

---

## 目录结构

```text
app/
  api/                    # HTTP 接口层
    auth.py               # 登录认证
    projects.py           # 项目管理
    settings.py           # 系统设置
    tasks.py              # 任务控制
    logs.py               # 任务日志
    reports.py            # 报告查询
    webhook.py            # Webhook 入口
    webhook_reviews.py    # Webhook 审查记录
    users.py              # 用户管理
    roles.py              # 角色权限管理
  models/                 # ORM 模型
    user.py               # 用户 / 角色 / 权限
    project.py            # 项目
    settings.py           # 全局配置
    task_log.py           # 任务日志
    webhook_review.py     # Webhook 审查记录
    commit_record.py      # 提交记录
    employee_efficiency.py        # 人员能效日度明细
    employee_efficiency_monthly.py # 人员能效月度汇总
  schemas/                # Pydantic 数据结构
  services/               # 核心业务服务
    im/                   # 通知渠道适配（钉钉/企业微信/飞书）
    gitlab_client.py      # GitLab API 客户端
    code_reviewer.py      # LLM 代码审查
    webhook_handler.py    # Webhook 事件处理
    webhook_worker.py     # Webhook 异步审查
    webhook_reviewer.py   # Webhook 审查逻辑
    task_executor.py      # 定时任务执行器
    scheduler.py          # APScheduler 调度
    stats_generator.py    # 统计生成
    report_merger.py      # 报告合并
    svn_uploader.py       # SVN 上传
    notifier.py           # 通知分发
    efficiency_aggregator.py       # 人员能效日度聚合
    efficiency_monthly_aggregator.py # 人员能效月度聚合
    efficiency_llm.py              # 人员能效 LLM 评分与总结
  templates/              # Jinja2 页面模板
  static/                 # 静态资源
  config.py               # 配置管理
  security.py             # 加密/解密服务
  database.py             # 数据库初始化
  main.py                 # 应用入口

tests/
  test_api/               # API 层测试
  test_models/            # 模型层测试
  test_services/          # 服务层测试
  test_security.py        # 安全模块测试
  test_config.py          # 配置模块测试
  test_schemas.py         # Schema 测试
```

---

## 快速启动

### 1) 安装依赖

```bash
pip install -r requirements.txt
```

### 2) 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 5001 --reload
```

### 3) 访问页面

| 页面 | 路径 | 权限 |
|------|------|------|
| 登录 | `/login` | 公开 |
| 首页仪表盘 | `/` | 登录用户 |
| 项目管理 | `/projects` | 登录用户（按权限过滤） |
| 系统设置 | `/settings` | 系统管理员 |
| 任务日志 | `/logs` | 登录用户（按权限过滤） |
| 报告中心 | `/reports` | 登录用户（按权限过滤） |
| Webhook 审查 | `/webhook-reviews` | 登录用户 |
| **人员能效** | `/efficiency` | 登录用户（按权限过滤） |
| 账号管理 | `/users` | 系统管理员 |
| 权限管理 | `/roles` | 系统管理员 |

---

## Linux systemd 服务部署

以下命令适用于基于 systemd 的 Linux 发行版（如 Ubuntu、CentOS、Debian 等）。

### 1) 前提条件

```bash
# 创建专用用户（可选但推荐）
sudo useradd -r -s /bin/false gitlab-review || true

# 创建安装目录
sudo mkdir -p /opt/gitlab-code-review-tool
sudo chown gitlab-review:gitlab-review /opt/gitlab-code-review-tool
```

### 2) 部署文件

将项目文件复制到安装目录：

```bash
# 方式一：使用 git clone
sudo git clone <your-repo-url> /opt/gitlab-code-review-tool

# 方式二：手动复制（从打包文件解压）
sudo tar -xzf gitlab-code-review-tool.tar.gz -C /opt/

# 设置权限
sudo chown -R gitlab-review:gitlab-review /opt/gitlab-code-review-tool

# 安装依赖（使用项目虚拟环境）
sudo -u gitlab-review /opt/gitlab-code-review-tool/venv/bin/pip install -r /opt/gitlab-code-review-tool/requirements.txt
```

### 3) 安装服务

```bash
# 复制服务文件到 systemd 目录
sudo cp /opt/gitlab-code-review-tool/scripts/gitlab-code-review.service /etc/systemd/system/

# 编辑服务配置（修改 SECRET_KEY 等环境变量）
sudo nano /etc/systemd/system/gitlab-code-review.service

# 重载 systemd 配置
sudo systemctl daemon-reload
```

> **注意**：编辑 `/etc/systemd/system/gitlab-code-review.service` 时，请根据实际环境修改：
> - `SECRET_KEY`：设置安全的随机密钥
> - `CONFIG_PATH`：配置文件路径
> - `User`/`Group`：运行用户（默认 `gitlab-review`）

### 4) 服务管理命令

```bash
# 启动服务
sudo systemctl start gitlab-code-review

# 停止服务
sudo systemctl stop gitlab-code-review

# 重启服务
sudo systemctl restart gitlab-code-review

# 重新加载配置（不中断连接）
sudo systemctl reload gitlab-code-review

# 设置开机自启
sudo systemctl enable gitlab-code-review

# 取消开机自启
sudo systemctl disable gitlab-code-review

# 查看服务状态
sudo systemctl status gitlab-code-review

# 检查服务是否正在运行
sudo systemctl is-active gitlab-code-review

# 检查服务是否设置开机自启
sudo systemctl is-enabled gitlab-code-review
```

### 5) 常用操作示例

```bash
# 完整部署并启动流程
sudo cp scripts/gitlab-code-review.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gitlab-code-review   # 启用并立即启动

# 修改配置后平滑重启
sudo systemctl edit gitlab-code-review --full    # 编辑配置
sudo systemctl daemon-reload
sudo systemctl restart gitlab-code-review

# 排查问题时实时查看日志
sudo journalctl -u gitlab-code-review -f --no-pager
```

---

## 查看日志

本系统提供两种日志查看方式：

### 1. 系统运行日志（应用层）

记录应用运行时的所有活动，包括启动信息、调度状态、任务执行、错误追踪等。

| 属性 | 说明 |
|------|------|
| 位置 | `data/logs/app.log` |
| 级别 | DEBUG（文件）/ INFO（控制台） |
| 轮转 | 10 MB 自动轮转 |
| 保留 | 30 天 |

**查看方式：**

```bash
# 方式一：命令行实时查看（Linux/macOS）
tail -f data/logs/app.log

# 方式二：查看完整日志（支持按关键字过滤）
grep "ERROR" data/logs/app.log

# 方式三：查看最近 100 行
tail -n 100 data/logs/app.log

# 方式四：Windows PowerShell
Get-Content data/logs/app.log -Tail 100 -Wait
```

### 2. 任务执行日志（数据库）

记录每次审查任务的执行详情，包含状态、时间、统计信息、错误信息等。

| 查看方式 | 路径 | 说明 |
|----------|------|------|
| Web 页面 | `/logs` | 任务日志页面，支持筛选和分页 |
| API | `GET /api/logs` | 支持 `project_id`、`status`、`start_date`、`end_date` 等参数筛选 |
| API 详情 | `GET /api/logs/{log_id}` | 查看单条任务日志的详细信息 |

**API 查询示例：**

```bash
# 查询指定项目的任务日志
curl "http://localhost:5001/api/logs?project_id=1"

# 查询失败的任务
curl "http://localhost:5001/api/logs?status=failed"

# 查询指定日期范围
curl "http://localhost:5001/api/logs?start_date=2026-04-01&end_date=2026-04-27"
```

### 3. systemd 服务日志（Linux 部署）

当以 systemd 服务方式运行时，使用 `journalctl` 查看系统日志：

```bash
# 查看服务状态
sudo systemctl status gitlab-code-review

# 实时查看服务日志
sudo journalctl -u gitlab-code-review -f

# 查看最近 200 行
sudo journalctl -u gitlab-code-review -n 200

# 仅查看错误级别日志
sudo journalctl -u gitlab-code-review -p err

# 按时间范围查看
sudo journalctl -u gitlab-code-review --since today          # 今天
sudo journalctl -u gitlab-code-review --since yesterday      # 昨天至今
sudo journalctl -u gitlab-code-review --since "-1 hour"      # 最近 1 小时
```

| 参数 | 说明 |
|------|------|
| `-f` | 实时跟踪（类似 tail -f） |
| `-n 100` | 显示最近 100 行 |
| `-p err` | 仅错误级别 |
| `--since today` | 今天以来的日志 |
| `-b` | 本次启动以来的日志 |
| `--no-pager` | 不分页，直接输出 |

---

## 主要接口

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/auth/logout` | 用户登出 |
| GET | `/api/auth/profile` | 获取当前用户信息 |
| PUT | `/api/auth/profile` | 更新个人资料 |
| PUT | `/api/auth/change-password` | 修改密码 |

### 用户管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/users` | 用户列表 |
| POST | `/api/users` | 创建用户 |
| PUT | `/api/users/{user_id}` | 更新用户 |
| DELETE | `/api/users/{user_id}` | 删除用户 |
| POST | `/api/users/{user_id}/reset-password` | 重置用户密码 |

### 角色权限

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/roles` | 角色列表 |
| GET | `/api/roles/{role_id}` | 角色详情 |
| POST | `/api/roles` | 创建角色 |
| PUT | `/api/roles/{role_id}` | 更新角色 |

### Webhook

- `POST /review/webhook`
  接收 GitLab 事件并异步处理（MR / Push）。

### Webhook 审查查询

- `GET /api/webhook-reviews`
- `GET /api/webhook-reviews/stats`
- `GET /api/webhook-reviews/{review_id}`

### 任务控制

- `POST /api/tasks/run`
- `GET /api/tasks/status`
- `POST /api/tasks/cancel`
- `POST /api/tasks/run-now/{job_id}`
- `POST /api/tasks/run-all`

### 任务日志

- `GET /api/logs`（支持分页和筛选）

### 人员能效

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/efficiency/list` | 人员能效列表 + 团队概览（支持日期筛选、排序、分页） |
| GET | `/api/efficiency/detail` | 单人详情（评分趋势、工作总结、提交记录） |
| POST | `/api/efficiency/recompute` | 手动补算日度数据（系统管理员，异步执行） |
| GET | `/api/efficiency/recompute/status` | 查询补算任务进度 |
| POST | `/api/efficiency/recompute/cancel` | 取消进行中的补算任务 |
| GET | `/api/efficiency/monthly/list` | 月度能效列表（支持月份筛选、排序） |
| GET | `/api/efficiency/monthly/detail` | 月度单人详情（月度总结、日度明细） |
| POST | `/api/efficiency/monthly/recompute` | 月度补算（系统管理员） |

### 外部接口（External API）

> 需在请求头携带 `X-API-Key` 进行认证，密钥在系统设置中配置。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/external/efficiency/daily` | 指定日期的人员能效数据 |
| GET | `/api/external/efficiency/list` | 人员能效分页列表 |

#### GET /api/external/efficiency/daily

查询指定日期的人员能效数据，支持按邮箱筛选。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `date` | string | 否 | 查询日期，格式 `YYYY-MM-DD`，默认前一天 |
| `email` | string | 否 | 按邮箱筛选，多个用英文逗号分隔；不传则返回所有人员 |

**请求示例：**

```bash
# 查询所有人员
curl -H "X-API-Key: YOUR_KEY" \
  "http://localhost:5001/api/external/efficiency/daily?date=2026-06-03"

# 查询指定人员（单个邮箱）
curl -H "X-API-Key: YOUR_KEY" \
  "http://localhost:5001/api/external/efficiency/daily?date=2026-06-03&email=zhangsan@example.com"

# 查询多个指定人员（逗号分隔）
curl -H "X-API-Key: YOUR_KEY" \
  "http://localhost:5001/api/external/efficiency/daily?date=2026-06-03&email=zhangsan@example.com,lisi@example.com"
```

**响应示例（成功）：**

```json
{
  "success": true,
  "data": {
    "date": "2026-06-03",
    "generated_at": "2026-06-04T02:15:30",
    "llm_status": "success",
    "items": [
      {
        "id": 1,
        "author_email": "zhangsan@example.com",
        "author_name": "张三",
        "stat_date": "2026-06-03",
        "commits_count": 5,
        "additions": 120,
        "deletions": 30,
        "files_changed": 8,
        "new_files": 1,
        "deleted_files": 0,
        "projects_involved": ["project-a"],
        "review_score": 85,
        "review_grade": "良好",
        "review_summary": "...",
        "work_summary": "...",
        "llm_status": "success",
        "llm_error": null
      }
    ]
  }
}
```

#### GET /api/external/efficiency/list

人员能效分页列表，支持日期范围和邮箱筛选。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `author_email` | string | 否 | 按邮箱精确筛选 |
| `start_date` | string | 否 | 开始日期 `YYYY-MM-DD` |
| `end_date` | string | 否 | 结束日期 `YYYY-MM-DD` |
| `page` | int | 否 | 页码，默认 1 |
| `page_size` | int | 否 | 每页条数，默认 20，最大 100 |

---

## 权限体系

### 系统角色

| 角色 | 说明 |
|------|------|
| system_admin | 系统管理员，可管理用户/角色/全局设置 |
| project_admin | 项目管理员，可管理授权项目 |
| user | 普通用户，仅查看授权项目 |

### 项目级权限

- 项目管理员：可查看/编辑授权的项目及其任务日志/报告
- 普通用户：仅查看授权项目的只读数据
- 系统管理员：拥有所有权限

---

## 配置说明

### 全局配置（Settings）

- GitLab 全局地址 / Token
- LLM API 地址 / 模型 / 超时 / 重试
- 审查 Prompt（日报 / 周报 / Webhook）
- 通知渠道（钉钉 / 企业微信 / 飞书）
- 支持文件扩展名（用于过滤变更）
- 调度开关与执行时间（日报/周报）
- 人员能效配置（启用开关、工作总结条目上限、日度/月度提示词模板）

### 项目配置（Project）

- 项目 GitLab 参数（可覆盖全局）
- 目标分支 / 排除分支
- 项目级通知配置（优先于全局）
- SVN 上传配置（可选）

---

## 行为规则

### Webhook 项目匹配优先级

Webhook 事件映射本地项目时按以下顺序匹配：

1. `project.id`（若缺失则使用顶层 `project_id`）
2. `project.path_with_namespace`
3. `project.name`

### 通知回退策略（企业微信）

- 优先使用项目级企业微信配置
- 项目级解密失败时回退全局配置
- 全局解密失败时显式禁用企业微信通道

---

## 审查流程

### 定时审查

调度触发 → 拉取提交 → AI 审查 → 生成日报/周报 →（可选）上传 SVN → 记录任务日志

### Webhook 审查

GitLab 事件 → 过滤事件/文件 → AI 审查 → 回写 GitLab → 发送通知 → 入库统计

### 人员能效分析

#### 功能概述

人员能效模块通过聚合 GitLab 提交数据，结合 LLM 智能分析，为团队提供代码贡献度和工作质量的量化评估。

#### 数据模型

**日度明细（EmployeeEfficiencyDaily）**
- 人员维度：提交者邮箱、显示名、统计日期
- 代码量统计：提交次数、新增/删除行数、涉及文件数、新建/删除文件数
- 项目关联：涉及项目列表（JSON 数组）
- LLM 产出：综合评分（0-100）、等级（优秀/良好/一般/待改进）、评分简述、工作总结

**月度汇总（EmployeeEfficiencyMonthly）**
- 基于日度数据聚合：月度代码量汇总、活跃天数
- LLM 月度总结：月度平均评分、月度工作总结

#### 日度聚合流程

1. 日报任务完成后自动触发（需在设置中启用）
2. 拉取所有活跃项目的所有分支提交
3. 跨项目按 commit SHA 去重
4. 按作者邮箱分组，累加代码量统计
5. 调用 LLM 生成评分和工作总结
6. UPSERT 写入日度明细表（幂等操作）

#### 月度聚合流程

1. 每月 1 日定时任务自动触发（或手动补算）
2. 读取指定月份的所有日度数据
3. 按作者聚合求和（代码量、活跃天数）
4. 调用 LLM 生成月度总结（串行调用，2 秒间隔避免限流）
5. UPSERT 写入月度汇总表

#### 补算机制

- **日度补算**：支持按日期范围补算，异步后台线程执行
- **月度补算**：支持按月份补算
- **Force 模式**：可选覆盖已有记录
- **进度监控**：实时查询补算进度（已处理/跳过/失败天数）
- **取消操作**：支持取消正在进行的补算任务

#### 页面功能

- **团队概览**：代码量 Top N 排行、等级分布饼图
- **个人详情**：评分趋势折线图、工作总结、提交记录明细
- **数据筛选**：按日期/月份筛选、排序（评分/代码量/提交数）
- **权限控制**：按项目权限过滤可见人员

---

## 测试

```bash
# 运行全部测试
pytest

# 运行关键回归测试
pytest tests/test_services/test_webhook_worker.py tests/test_services/test_notifier.py -q

# 运行人员能效相关测试
pytest tests/test_models/test_employee_efficiency.py tests/test_models/test_employee_efficiency_monthly.py tests/test_services/test_efficiency_aggregator.py tests/test_services/test_efficiency_monthly_aggregator.py tests/test_services/test_efficiency_llm.py tests/test_api/test_efficiency.py tests/test_api/test_efficiency_monthly.py -q
```

---

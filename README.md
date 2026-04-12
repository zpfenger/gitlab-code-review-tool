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
| 账号管理 | `/users` | 系统管理员 |
| 权限管理 | `/roles` | 系统管理员 |

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

---

## 测试

```bash
# 运行全部测试
pytest

# 运行关键回归测试
pytest tests/test_services/test_webhook_worker.py tests/test_services/test_notifier.py -q
```

---

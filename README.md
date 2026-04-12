# GitLab Code Review Tool

基于 **FastAPI** 的 GitLab 代码审查平台，支持两种审查模式：

1. **定时任务审查**：按项目执行日报/周报审查并生成报告。
2. **Webhook 实时审查**：接收 GitLab MR/Push 事件，调用 AI 即时审查并通知团队。

---

## 核心能力

- 项目级配置（GitLab/SVN/通知渠道可覆盖全局配置）
- 日报/周报任务执行与状态追踪
- Webhook 实时审查（MR / Push）
- 审查结果入库、查询、统计、可视化
- 审查结果回写 GitLab（MR Note / Push Note）
- 多渠道通知（钉钉 / 企业微信 / 飞书）
- 敏感字段加密存储（Token / Webhook URL 等）

---

## 技术栈

- **Backend**: FastAPI, SQLAlchemy
- **Frontend**: Jinja2, Bootstrap, Chart.js
- **Database**: SQLite（默认，可替换）
- **LLM**: OpenAI 兼容风格 API
- **Logging**: Loguru

---

## 目录结构（关键）

```text
app/
  api/                  # HTTP 接口层
  models/               # ORM 模型
  schemas/              # Pydantic 数据结构
  services/             # 核心业务服务
    im/                 # 通知渠道适配
    webhook_*.py        # Webhook 处理链路
  templates/            # 页面模板
  main.py               # 应用入口

tests/
  test_services/        # 服务层测试（Webhook/通知等）
```

---

## 快速启动

### 1) 安装依赖

```bash
pip install -r requirements.txt
```

### 2) 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 7000 --reload
```

### 3) 访问页面

- 首页：`/`
- 项目管理：`/projects`
- 系统设置：`/settings`
- Webhook 审查：`/webhook-reviews`

---

## 主要接口

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

---

## 配置说明

### 全局配置（Settings）

- GitLab 全局地址 / Token
- LLM API 地址 / 模型 / 超时 / 重试
- 审查 Prompt（日报 / 周报 / Webhook）
- 通知渠道（钉钉 / 企业微信 / 飞书）
- 支持文件扩展名（用于过滤变更）

### 项目配置（Project）

- 项目 GitLab 参数（可覆盖全局）
- 排除分支
- 项目级通知配置（优先于全局）
- SVN 上传配置（可选）

---

## 行为规则（当前实现）

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

## 审查流程简述

### 定时审查

调度触发 → 拉取提交 → AI 审查 → 生成日报/周报 →（可选）上传 SVN → 记录任务日志

### Webhook 审查

GitLab 事件 → 过滤事件/文件 → AI 审查 → 回写 GitLab → 发送通知 → 入库统计

---

## 测试

运行本次关键回归测试：

```bash
pytest tests/test_services/test_webhook_worker.py tests/test_services/test_notifier.py -q
```

---

## 安全与稳定性建议（后续）

1. 为 Webhook 审查日志增加数据库唯一约束，确保并发幂等。
2. 统一前端列表渲染转义策略，避免动态内容注入风险。
3. 任务进度从内存迁移到持久化存储（DB/Redis）。

---

## 许可证

内部项目使用（按团队规范管理）。

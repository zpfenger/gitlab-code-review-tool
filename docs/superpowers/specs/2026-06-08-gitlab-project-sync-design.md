# GitLab 项目自动同步设计

## 背景

系统已经移除项目级 GitLab Token，统一使用系统设置中的全局 GitLab URL 和全局 Access Token。项目表已有 `name`、`project_id`、`description`、`target_branches` 和 `is_active` 字段，项目管理页也已有手动创建项目和启用项目的能力。

本次新增能力：系统管理员可以用全局 Access Token 拉取该 token 可访问的所有 GitLab 项目，并自动创建本地未配置的项目。

## 范围

- 同步范围为全局 Access Token 可访问的所有 GitLab 项目。
- 只自动创建本地不存在的项目。
- 已存在的项目按 GitLab 项目 ID 跳过，不覆盖本地配置。
- 新创建项目默认启用，`is_active=True`。
- 不新增项目级 GitLab Token 字段。
- 不在本期实现预览选择、异步任务或定时自动同步。

## 后端设计

### GitLabClient

在 `app/services/gitlab_client.py` 中新增 `list_accessible_projects()`：

- 调用 `self.client.projects.list(get_all=True, simple=True)`。
- 返回统一 dict 列表，至少包含：
  - `id`
  - `name`
  - `path_with_namespace`
  - `description`
  - `web_url`
  - `default_branch`
- GitLab 认证失败抛出 `GitLabAuthError`。
- GitLab 连接失败抛出 `GitLabConnectionError`。
- 其他异常记录日志并返回空列表，由同步接口按空结果处理。

### 同步接口

在 `app/api/projects.py` 中新增：

`POST /api/projects/sync-gitlab`

权限：

- 仅系统管理员可调用。

流程：

1. 读取 `Settings` 首行配置。
2. 校验 `global_gitlab_url` 已配置。
3. 解密 `global_gitlab_token`，未配置或解密失败时返回明确错误。
4. 使用全局 URL/token 构造 `GitLabClient`。
5. 拉取 token 可访问的所有 GitLab 项目。
6. 读取本地已有 `Project.project_id` 集合。
7. 对每个 GitLab 项目：
   - 若 `id` 已存在，计入 skipped。
   - 若不存在，创建本地 `Project`。
   - `project_id` 使用 GitLab `id`。
   - `name` 优先使用 GitLab `name` 的安全名称。
   - 安全名称会把本地校验不允许的字符替换为空格，并压缩连续空白。
   - 若本地项目名冲突，改用 `path_with_namespace` 的安全名称。
   - 若仍冲突，追加 GitLab ID，保证满足本地唯一约束。
   - `description` 使用 GitLab `description`。
   - `target_branches` 留空，沿用现有“所有分支”语义。
   - `is_active=True`。
8. 一次请求内提交数据库事务。

响应：

```json
{
  "success": true,
  "message": "GitLab 项目同步完成",
  "data": {
    "created": 12,
    "skipped": 3,
    "failed": 0,
    "total": 15,
    "created_projects": [
      {"name": "demo", "project_id": 123}
    ]
  }
}
```

## 前端设计

在 `app/templates/projects.html` 的项目列表按钮区新增“同步 GitLab 项目”按钮，仅系统管理员可见。

交互：

- 点击后弹出确认。
- 请求期间禁用按钮并显示同步中。
- 成功后显示新增、跳过、失败数量。
- 同步成功后刷新页面。
- 失败时显示接口返回的错误信息。

## 错误处理

- 未配置 Settings：返回 `ApiResponse(success=False)`，提示“系统设置未配置”。
- 未配置 GitLab URL：提示“全局 GitLab URL 未配置”。
- 未配置 GitLab Token：提示“全局 GitLab Token 未配置”。
- Token 解密失败：提示“全局 GitLab Token 解密失败，请重新保存配置”。
- Token 无效或过期：提示 GitLab 认证失败。
- GitLab 网络不可达：提示连接失败并包含 GitLab URL。
- 单个项目名称冲突不应导致整个同步失败，名称会自动去重。

## 测试计划

- `GitLabClient.list_accessible_projects()` 正确映射 GitLab 项目字段。
- GitLab 认证失败时抛出 `GitLabAuthError`。
- 同步接口在未配置 GitLab URL/token 时返回失败。
- 同步接口跳过已存在 GitLab 项目 ID。
- 同步接口创建未存在项目并默认启用。
- 同步接口处理本地项目名称冲突。

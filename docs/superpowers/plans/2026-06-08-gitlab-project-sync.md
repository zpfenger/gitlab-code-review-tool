# GitLab Project Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a system-admin action that imports every GitLab project visible to the global Access Token and creates missing local projects as enabled.

**Architecture:** Extend the existing `GitLabClient` with a project-listing method, then add a system-admin-only API endpoint that reads global GitLab settings, decrypts the token, lists accessible projects, de-duplicates by GitLab project ID, sanitizes unique local names, and inserts missing `Project` rows. Add a small project-page button that calls the endpoint and reports the sync result.

**Tech Stack:** FastAPI, SQLAlchemy, python-gitlab, Jinja templates, pytest, FastAPI TestClient.

---

## File Structure

- Modify: `app/services/gitlab_client.py`
  - Responsibility: wrap python-gitlab API calls and normalize GitLab project objects.
- Modify: `tests/test_services/test_gitlab_client.py`
  - Responsibility: unit coverage for GitLab project listing and GitLab error translation.
- Modify: `app/api/projects.py`
  - Responsibility: project CRUD API, permission checks, and the new GitLab project sync endpoint.
- Create: `tests/test_api/test_projects_sync.py`
  - Responsibility: API coverage for configuration validation, admin-only access, skip/create behavior, default enablement, and name collision handling.
- Modify: `app/templates/projects.html`
  - Responsibility: add the system-admin-only sync button and client-side interaction.

---

### Task 1: GitLabClient Project Listing

**Files:**
- Modify: `tests/test_services/test_gitlab_client.py`
- Modify: `app/services/gitlab_client.py`

- [ ] **Step 1: Write failing unit tests for accessible project listing**

Append these tests inside `class TestGitLabClient` in `tests/test_services/test_gitlab_client.py`:

```python
    def test_list_accessible_projects(self, client, mock_gitlab):
        """测试列出 Access Token 可访问的项目"""
        mock_project1 = Mock()
        mock_project1.id = 101
        mock_project1.name = "Alpha"
        mock_project1.path_with_namespace = "group/alpha"
        mock_project1.description = "Alpha project"
        mock_project1.web_url = "https://gitlab.example.com/group/alpha"
        mock_project1.default_branch = "main"

        mock_project2 = Mock()
        mock_project2.id = 102
        mock_project2.name = "Beta"
        mock_project2.path_with_namespace = "group/sub/beta"
        mock_project2.description = None
        mock_project2.web_url = "https://gitlab.example.com/group/sub/beta"
        mock_project2.default_branch = None

        mock_gitlab.return_value.projects.list.return_value = [mock_project1, mock_project2]

        projects = client.list_accessible_projects()

        mock_gitlab.return_value.projects.list.assert_called_once_with(get_all=True, simple=True)
        assert projects == [
            {
                "id": 101,
                "name": "Alpha",
                "path_with_namespace": "group/alpha",
                "description": "Alpha project",
                "web_url": "https://gitlab.example.com/group/alpha",
                "default_branch": "main",
            },
            {
                "id": 102,
                "name": "Beta",
                "path_with_namespace": "group/sub/beta",
                "description": "",
                "web_url": "https://gitlab.example.com/group/sub/beta",
                "default_branch": "",
            },
        ]

    def test_list_accessible_projects_auth_error(self, client, mock_gitlab):
        """测试列出项目时认证失败"""
        mock_gitlab.return_value.projects.list.side_effect = gitlab.exceptions.GitlabAuthenticationError(
            error_message="401 Unauthorized", response_code=401
        )

        with pytest.raises(GitLabAuthError) as exc_info:
            client.list_accessible_projects()

        assert "认证失败" in str(exc_info.value)

    def test_list_accessible_projects_connection_error(self, client, mock_gitlab):
        """测试列出项目时 GitLab 连接失败"""
        mock_gitlab.return_value.projects.list.side_effect = gitlab.exceptions.GitlabConnectionError(
            "Connection refused"
        )

        with pytest.raises(GitLabConnectionError) as exc_info:
            client.list_accessible_projects()

        assert "无法连接" in str(exc_info.value)
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
pytest tests/test_services/test_gitlab_client.py::TestGitLabClient::test_list_accessible_projects tests/test_services/test_gitlab_client.py::TestGitLabClient::test_list_accessible_projects_auth_error tests/test_services/test_gitlab_client.py::TestGitLabClient::test_list_accessible_projects_connection_error -q
```

Expected: tests fail with `AttributeError: 'GitLabClient' object has no attribute 'list_accessible_projects'`.

- [ ] **Step 3: Implement `list_accessible_projects()`**

Add this method to `class GitLabClient` in `app/services/gitlab_client.py`, after `get_project_info()`:

```python
    def list_accessible_projects(self) -> List[Dict[str, Any]]:
        """
        列出当前 Access Token 可访问的所有 GitLab 项目。

        Returns:
            List[Dict]: 项目信息列表

        Raises:
            GitLabAuthError: Token 认证失败（401）
            GitLabConnectionError: 网络连接失败
        """
        try:
            projects = self.client.projects.list(get_all=True, simple=True)
            return [
                {
                    "id": project.id,
                    "name": getattr(project, "name", "") or "",
                    "path_with_namespace": getattr(project, "path_with_namespace", "") or "",
                    "description": getattr(project, "description", "") or "",
                    "web_url": getattr(project, "web_url", "") or "",
                    "default_branch": getattr(project, "default_branch", "") or "",
                }
                for project in projects
            ]
        except gitlab.exceptions.GitlabAuthenticationError as e:
            logger.error("列出 GitLab 项目失败 - 认证错误: Token 无效或已过期")
            raise GitLabAuthError(
                "列出 GitLab 项目失败: Token 认证失败，请检查 Token 是否有效且有 api 权限"
            ) from e
        except gitlab.exceptions.GitlabConnectionError as e:
            logger.error(f"列出 GitLab 项目失败 - 连接错误: {e}")
            raise GitLabConnectionError(
                f"列出 GitLab 项目失败: 无法连接到 GitLab ({self.gitlab_url})",
                gitlab_url=self.gitlab_url,
            ) from e
        except Exception as e:
            logger.error(f"列出 GitLab 项目失败: {type(e).__name__}: {e}")
            return []
```

- [ ] **Step 4: Run the GitLabClient tests and verify they pass**

Run:

```bash
pytest tests/test_services/test_gitlab_client.py -q
```

Expected: all tests in `tests/test_services/test_gitlab_client.py` pass.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add app/services/gitlab_client.py tests/test_services/test_gitlab_client.py
git commit -m "feat: list accessible gitlab projects"
```

---

### Task 2: Project Sync API

**Files:**
- Create: `tests/test_api/test_projects_sync.py`
- Modify: `app/api/projects.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_api/test_projects_sync.py` with this content:

```python
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.project import Project
from app.models.settings import Settings
from app.models.user import Role, User
from app.security import security_service


@pytest.fixture
def db_engine():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_user(db_session):
    admin_role = Role(name="system_admin", description="System Administrator", is_system_role=True)
    db_session.add(admin_role)
    db_session.commit()

    user = User(username="admin", password_hash=security_service.hash_password("admin123"))
    user.roles.append(admin_role)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def normal_user(db_session):
    user = User(username="user", password_hash=security_service.hash_password("user123"))
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def admin_session(client, admin_user):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200
    return client


def add_settings(db_session, token="gitlab-token", gitlab_url="https://gitlab.example.com"):
    settings = Settings(
        global_gitlab_url=gitlab_url,
        global_gitlab_token=security_service.encrypt(token) if token else None,
        llm_api_url="https://api.example.com/v1",
        llm_model="gpt-4",
        report_output_dir="./data/reports",
    )
    db_session.add(settings)
    db_session.commit()
    db_session.refresh(settings)
    return settings


class TestSyncGitLabProjects:
    def test_sync_requires_system_admin(self, client, normal_user, db_session):
        client.post("/api/auth/login", json={"username": "user", "password": "user123"})
        add_settings(db_session)

        response = client.post("/api/projects/sync-gitlab")

        assert response.status_code == 403

    def test_sync_requires_settings(self, admin_session):
        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["message"] == "系统设置未配置"

    def test_sync_requires_gitlab_url(self, admin_session, db_session):
        add_settings(db_session, gitlab_url="")

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["message"] == "全局 GitLab URL 未配置"

    def test_sync_requires_gitlab_token(self, admin_session, db_session):
        add_settings(db_session, token=None)

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["message"] == "全局 GitLab Token 未配置"

    @patch("app.api.projects.GitLabClient")
    def test_sync_creates_missing_projects_and_skips_existing(self, mock_client_cls, admin_session, db_session):
        add_settings(db_session)
        existing = Project(name="Existing", project_id=100, is_active=False)
        db_session.add(existing)
        db_session.commit()

        mock_client_cls.return_value.list_accessible_projects.return_value = [
            {
                "id": 100,
                "name": "Existing Remote",
                "path_with_namespace": "group/existing",
                "description": "Already configured",
                "web_url": "https://gitlab.example.com/group/existing",
                "default_branch": "main",
            },
            {
                "id": 101,
                "name": "New Project",
                "path_with_namespace": "group/new-project",
                "description": "New description",
                "web_url": "https://gitlab.example.com/group/new-project",
                "default_branch": "main",
            },
        ]

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["created"] == 1
        assert data["data"]["skipped"] == 1
        assert data["data"]["failed"] == 0
        assert data["data"]["total"] == 2
        assert data["data"]["created_projects"] == [{"name": "New Project", "project_id": 101}]

        created = db_session.query(Project).filter(Project.project_id == 101).one()
        assert created.name == "New Project"
        assert created.description == "New description"
        assert created.target_branches is None
        assert created.is_active is True

        db_session.refresh(existing)
        assert existing.name == "Existing"
        assert existing.is_active is False

    @patch("app.api.projects.GitLabClient")
    def test_sync_sanitizes_and_deduplicates_project_names(self, mock_client_cls, admin_session, db_session):
        add_settings(db_session)
        db_session.add(Project(name="Service API", project_id=200, is_active=True))
        db_session.add(Project(name="group service api", project_id=201, is_active=True))
        db_session.commit()

        mock_client_cls.return_value.list_accessible_projects.return_value = [
            {
                "id": 202,
                "name": "Service/API",
                "path_with_namespace": "group/service-api",
                "description": "",
                "web_url": "https://gitlab.example.com/group/service-api",
                "default_branch": "main",
            }
        ]

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["created"] == 1
        assert data["data"]["created_projects"] == [{"name": "group service-api", "project_id": 202}]

        created = db_session.query(Project).filter(Project.project_id == 202).one()
        assert created.name == "group service-api"
        assert created.is_active is True
```

- [ ] **Step 2: Run the API tests and verify they fail**

Run:

```bash
pytest tests/test_api/test_projects_sync.py -q
```

Expected: tests fail with `404 Not Found` for `POST /api/projects/sync-gitlab` or missing helper behavior.

- [ ] **Step 3: Add sync helpers to `app/api/projects.py`**

Update imports at the top of `app/api/projects.py`:

```python
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Set
```

Add these helpers after `_encrypt_project_sensitive()`:

```python
_PROJECT_NAME_UNSAFE_RE = re.compile(r"[^\w\-\u4e00-\u9fa5\s]+")
_PROJECT_NAME_SPACE_RE = re.compile(r"\s+")


def _sanitize_project_name(value: str, fallback: str) -> str:
    """转换为满足 Project.name 校验规则的安全名称。"""
    raw = (value or fallback or "project").replace("/", " ")
    sanitized = _PROJECT_NAME_UNSAFE_RE.sub(" ", raw)
    sanitized = _PROJECT_NAME_SPACE_RE.sub(" ", sanitized).strip()
    if not sanitized:
        sanitized = fallback or "project"
    return sanitized[:100].strip() or "project"


def _make_unique_project_name(base_name: str, existing_names: Set[str], gitlab_project_id: int) -> str:
    """生成不超过 100 字符且不与本地项目重名的项目名称。"""
    candidate = base_name[:100].strip() or f"project {gitlab_project_id}"
    if candidate not in existing_names:
        existing_names.add(candidate)
        return candidate

    counter = 1
    while True:
        suffix = f" {gitlab_project_id}" if counter == 1 else f" {gitlab_project_id}-{counter}"
        prefix_length = max(1, 100 - len(suffix))
        candidate = f"{base_name[:prefix_length].rstrip()}{suffix}"
        if candidate not in existing_names:
            existing_names.add(candidate)
            return candidate
        counter += 1


def _select_project_name(gitlab_project: Dict[str, Any], existing_names: Set[str]) -> str:
    """根据 GitLab 项目信息选择本地唯一项目名。"""
    gitlab_project_id = int(gitlab_project["id"])
    fallback = f"project {gitlab_project_id}"
    name = _sanitize_project_name(gitlab_project.get("name") or "", fallback)
    if name in existing_names:
        namespace_name = _sanitize_project_name(
            gitlab_project.get("path_with_namespace") or "",
            name,
        )
        name = namespace_name
    return _make_unique_project_name(name, existing_names, gitlab_project_id)
```

- [ ] **Step 4: Implement the sync endpoint**

Add this endpoint in `app/api/projects.py` after `create_project()` and before `get_project()`:

```python
@router.post("/sync-gitlab")
async def sync_gitlab_projects(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """使用全局 GitLab Access Token 同步可访问项目，仅创建本地缺失项目。"""
    from app.models.settings import Settings

    settings = db.query(Settings).first()
    if not settings:
        return ApiResponse(success=False, message="系统设置未配置")

    if not settings.global_gitlab_url:
        return ApiResponse(success=False, message="全局 GitLab URL 未配置")

    if not settings.global_gitlab_token:
        return ApiResponse(success=False, message="全局 GitLab Token 未配置")

    try:
        token = security_service.decrypt(settings.global_gitlab_token)
    except ValueError:
        return ApiResponse(success=False, message="全局 GitLab Token 解密失败，请重新保存配置")

    if not token:
        return ApiResponse(success=False, message="全局 GitLab Token 未配置")

    try:
        client = GitLabClient(gitlab_url=settings.global_gitlab_url, access_token=token)
        gitlab_projects = client.list_accessible_projects()
    except GitLabAuthError as e:
        return ApiResponse(success=False, message=f"GitLab 认证失败: {e}")
    except GitLabConnectionError as e:
        return ApiResponse(success=False, message=f"GitLab 连接失败: {e}")

    existing_project_ids = {row[0] for row in db.query(Project.project_id).all()}
    existing_names = {row[0] for row in db.query(Project.name).all()}

    created_projects = []
    skipped = 0
    failed = 0

    for gitlab_project in gitlab_projects:
        gitlab_project_id = gitlab_project.get("id")
        if not gitlab_project_id:
            failed += 1
            continue

        gitlab_project_id = int(gitlab_project_id)
        if gitlab_project_id in existing_project_ids:
            skipped += 1
            continue

        project_name = _select_project_name(gitlab_project, existing_names)
        project = Project(
            name=project_name,
            project_id=gitlab_project_id,
            description=gitlab_project.get("description") or None,
            target_branches=None,
            is_active=True,
        )
        db.add(project)
        existing_project_ids.add(gitlab_project_id)
        created_projects.append({"name": project_name, "project_id": gitlab_project_id})

    db.commit()

    return ApiResponse(
        success=True,
        message="GitLab 项目同步完成",
        data={
            "created": len(created_projects),
            "skipped": skipped,
            "failed": failed,
            "total": len(gitlab_projects),
            "created_projects": created_projects,
        },
    )
```

- [ ] **Step 5: Run the API tests and verify they pass**

Run:

```bash
pytest tests/test_api/test_projects_sync.py -q
```

Expected: all tests in `tests/test_api/test_projects_sync.py` pass.

- [ ] **Step 6: Run existing project and settings API tests**

Run:

```bash
pytest tests/test_api/test_settings_api.py tests/test_schemas.py tests/test_models/test_project.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit Task 2**

Run:

```bash
git add app/api/projects.py tests/test_api/test_projects_sync.py
git commit -m "feat: sync gitlab projects"
```

---

### Task 3: Project Management Page Button

**Files:**
- Modify: `app/templates/projects.html`

- [ ] **Step 1: Add the system-admin-only button**

In the panel heading button group in `app/templates/projects.html`, replace:

```html
            {% if 'system_admin' in current_user_roles or 'project_admin' in current_user_roles %}
            <button class="btn btn-primary" onclick="openCreateModal()">
                <i class="bi bi-plus-lg"></i> 新增项目
            </button>
            {% endif %}
```

with:

```html
            {% if 'system_admin' in current_user_roles %}
            <button class="btn btn-success" id="syncGitlabProjectsBtn" onclick="syncGitlabProjects()">
                <i class="bi bi-cloud-download"></i> 同步 GitLab 项目
            </button>
            {% endif %}
            {% if 'system_admin' in current_user_roles or 'project_admin' in current_user_roles %}
            <button class="btn btn-primary" onclick="openCreateModal()">
                <i class="bi bi-plus-lg"></i> 新增项目
            </button>
            {% endif %}
```

- [ ] **Step 2: Add the JavaScript sync function**

In the `<script>` block of `app/templates/projects.html`, add this function before `function filterProjects()`:

```javascript
async function syncGitlabProjects() {
    if (!confirm('将使用全局 Access Token 拉取可访问的所有 GitLab 项目，并自动创建本地缺失项目。是否继续？')) return;

    const btn = document.getElementById('syncGitlabProjectsBtn');
    const origHTML = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> 同步中';

    try {
        const response = await fetch('/api/projects/sync-gitlab', { method: 'POST' });
        const result = await response.json();
        if (result.success) {
            const data = result.data || {};
            showNotification(
                `GitLab 项目同步完成：新增 ${data.created || 0} 个，跳过 ${data.skipped || 0} 个，失败 ${data.failed || 0} 个`,
                'success'
            );
            setTimeout(() => location.reload(), 800);
        } else {
            showNotification(result.message || 'GitLab 项目同步失败', 'error');
        }
    } catch (e) {
        showNotification('请求失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = origHTML;
    }
}
```

- [ ] **Step 3: Sanity-check the rendered template text**

Run:

```bash
Select-String -Path app\templates\projects.html -Pattern "同步 GitLab 项目|syncGitlabProjects|sync-gitlab"
```

Expected: output includes the button text, JavaScript function name, and `/api/projects/sync-gitlab`.

- [ ] **Step 4: Commit Task 3**

Run:

```bash
git add app/templates/projects.html
git commit -m "feat: add gitlab project sync button"
```

---

### Task 4: Final Verification

**Files:**
- Verify: `app/services/gitlab_client.py`
- Verify: `app/api/projects.py`
- Verify: `app/templates/projects.html`
- Verify: `tests/test_services/test_gitlab_client.py`
- Verify: `tests/test_api/test_projects_sync.py`

- [ ] **Step 1: Run focused automated tests**

Run:

```bash
pytest tests/test_services/test_gitlab_client.py tests/test_api/test_projects_sync.py tests/test_api/test_settings_api.py tests/test_schemas.py tests/test_models/test_project.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run a compile check**

Run:

```bash
python -m compileall app tests
```

Expected: command exits with status 0.

- [ ] **Step 3: Review the final diff**

Run:

```bash
git diff --stat HEAD~3..HEAD
git diff HEAD~3..HEAD -- app/services/gitlab_client.py app/api/projects.py app/templates/projects.html tests/test_services/test_gitlab_client.py tests/test_api/test_projects_sync.py
```

Expected: diff only contains GitLab project sync implementation, tests, and the project-page sync button.

- [ ] **Step 4: Confirm working tree cleanliness**

Run:

```bash
git status --short
```

Expected: no output.


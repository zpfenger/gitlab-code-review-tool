# Permission Adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the old `project_member` role model with centralized project-scope permissions, GitLab-backed member synchronization, and role-aware data visibility.

**Architecture:** Add pure permission helpers in `app/core/permissions.py`, move HTTP auth dependencies into `app/api/deps.py`, and route all project/report/Webhook/efficiency/task visibility through those helpers. Keep `project_members` as the GitLab-synchronized relationship cache, move GitLab project/member sync into a service, and reuse it from manual sync plus the scheduler.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite migration helper, python-gitlab, Jinja2 templates, pytest, FastAPI TestClient, APScheduler.

---

## File Structure

- Create `app/core/__init__.py`: package marker for shared core helpers.
- Create `app/core/permissions.py`: pure permission and identity helpers.
- Modify `app/api/deps.py`: move `get_current_user_full`, `require_system_admin`, `require_project_admin` here.
- Modify `app/api/users.py`: import dependencies from `deps.py`, remove project-member role assignment paths, keep admin project fallback.
- Modify `app/api/projects.py`: keep `_filter_projects_by_permission` and `_check_project_permission` as compatibility wrappers over `app/core/permissions.py`; integrate sync service.
- Modify `app/api/logs.py`, `app/api/tasks.py`, `app/api/reports.py`, `app/api/webhook_reviews.py`, `app/api/efficiency.py`: replace local role checks with helper calls.
- Modify `app/api/roles.py`: filter `project_member` and return only current role definitions.
- Modify `app/models/settings.py`, `app/schemas/settings.py`, `app/api/settings.py`, `app/templates/settings.html`: add GitLab sync settings and scheduling refresh.
- Modify `app/database.py`: stop initializing `project_member`, add idempotent cleanup of old `user_roles` links.
- Modify `app/services/gitlab_client.py`: add `get_project_members`.
- Create `app/services/gitlab_sync.py`: manual and scheduled GitLab project/member sync service.
- Modify `app/services/scheduler.py` and `app/main.py`: register scheduled GitLab sync.
- Modify `app/templates/base.html`, `app/templates/projects.html`, `app/templates/users.html`, `app/templates/roles.html`, `app/templates/webhook_reviews.html`, `app/static/js/efficiency.js`: role labels, menus, operation visibility, and no-access detail hints.
- Create `tests/test_core/test_permissions.py`.
- Update `tests/test_api/test_efficiency.py` and `tests/test_api/test_efficiency_monthly.py`.
- Create or update `tests/test_api/test_permissions_visibility.py`.
- Update `tests/test_api/test_projects_sync.py`.
- Create `tests/test_services/test_gitlab_sync.py`.
- Update `tests/test_api/test_settings_api.py`.
- Create or update template tests under `tests/test_templates/`.

---

### Task 1: Add Permission Helpers And Move HTTP Dependencies

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/permissions.py`
- Modify: `app/api/deps.py`
- Modify imports only where tests need dependency overrides: `tests/test_api/test_efficiency.py`, `tests/test_api/test_efficiency_monthly.py`
- Test: `tests/test_core/test_permissions.py`

- [ ] **Step 1: Write failing permission helper tests**

Create `tests/test_core/test_permissions.py`:

```python
import json

from app.core.permissions import (
    can_read_project,
    can_view_person_detail,
    can_write_project,
    get_readable_project_ids,
    get_writable_project_ids,
    is_self_identity,
)
from app.models.project import Project
from app.models.user import Role, User, project_admins, project_members
from app.security import security_service


def _role(db, name):
    role = db.query(Role).filter(Role.name == name).first()
    if not role:
        role = Role(name=name, description=name, is_system_role=True)
        db.add(role)
        db.commit()
        db.refresh(role)
    return role


def _user(db, username, email=None, roles=None, nickname=None):
    user = User(
        username=username,
        nickname=nickname,
        email=email,
        password_hash=security_service.hash_password("pass123"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    for role_name in roles or []:
        user.roles.append(_role(db, role_name))
    db.commit()
    db.refresh(user)
    return user


def _project(db, name, gitlab_id):
    project = Project(name=name, project_id=gitlab_id, is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _assign(table, db, project_id, user_id):
    db.execute(table.insert().values(project_id=project_id, user_id=user_id))
    db.commit()


def test_system_admin_can_read_and_write_all_projects(db_session):
    admin = _user(db_session, "admin", "admin@example.com", ["system_admin"])
    p1 = _project(db_session, "p1", 101)
    p2 = _project(db_session, "p2", 102)

    assert get_readable_project_ids(admin, db_session) is None
    assert get_writable_project_ids(admin, db_session) is None
    assert can_read_project(admin, p1.id, db_session) is True
    assert can_write_project(admin, p2.id, db_session) is True


def test_project_admin_reads_admin_and_member_projects_but_writes_admin_only(db_session):
    user = _user(db_session, "pm", "pm@example.com", ["project_admin"])
    admin_project = _project(db_session, "admin-project", 201)
    member_project = _project(db_session, "member-project", 202)
    other_project = _project(db_session, "other-project", 203)
    _assign(project_admins, db_session, admin_project.id, user.id)
    _assign(project_members, db_session, member_project.id, user.id)

    assert get_readable_project_ids(user, db_session) == {admin_project.id, member_project.id}
    assert get_writable_project_ids(user, db_session) == {admin_project.id}
    assert can_read_project(user, member_project.id, db_session) is True
    assert can_write_project(user, member_project.id, db_session) is False
    assert can_read_project(user, other_project.id, db_session) is False


def test_plain_user_reads_member_projects_and_writes_none(db_session):
    user = _user(db_session, "plain", "plain@example.com", [])
    member_project = _project(db_session, "member-project", 301)
    other_project = _project(db_session, "other-project", 302)
    _assign(project_members, db_session, member_project.id, user.id)

    assert get_readable_project_ids(user, db_session) == {member_project.id}
    assert get_writable_project_ids(user, db_session) == set()
    assert can_read_project(user, member_project.id, db_session) is True
    assert can_write_project(user, member_project.id, db_session) is False
    assert can_read_project(user, other_project.id, db_session) is False


def test_can_view_person_detail(db_session):
    admin = _user(db_session, "admin", "admin@example.com", ["system_admin"])
    project_admin = _user(db_session, "manager", "manager@example.com", ["project_admin"])
    plain = _user(db_session, "plain", "plain@example.com", [])
    member = _user(db_session, "member", "member@example.com", [])
    outsider = _user(db_session, "outsider", "outsider@example.com", [])
    project = _project(db_session, "visible-project", 401)
    _assign(project_members, db_session, project.id, project_admin.id)
    _assign(project_members, db_session, project.id, member.id)

    assert can_view_person_detail(admin, "outsider@example.com", db_session) is True
    assert can_view_person_detail(project_admin, "manager@example.com", db_session) is True
    assert can_view_person_detail(project_admin, "member@example.com", db_session) is True
    assert can_view_person_detail(project_admin, "outsider@example.com", db_session) is False
    assert can_view_person_detail(plain, "plain@example.com", db_session) is True
    assert can_view_person_detail(plain, "member@example.com", db_session) is False


def test_is_self_identity_prefers_email_and_falls_back_to_username_or_nickname(db_session):
    user = _user(db_session, "zhangsan", "zhangsan@example.com", [], nickname="张三")

    assert is_self_identity(user, "zhangsan@example.com") is True
    assert is_self_identity(user, "张三") is True
    assert is_self_identity(user, "zhangsan") is True
    assert is_self_identity(user, "李四") is False
```

- [ ] **Step 2: Run the failing tests**

Run: `pytest tests/test_core/test_permissions.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.core'`.

- [ ] **Step 3: Add `app/core/permissions.py`**

Create `app/core/__init__.py` as an empty file.

Create `app/core/permissions.py`:

```python
from __future__ import annotations

from typing import Optional, Set

from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.user import User, project_admins, project_members


def _ids_for_user(table, user_id: int, db: Session) -> Set[int]:
    rows = db.execute(table.select().where(table.c.user_id == user_id)).fetchall()
    return {row.project_id if hasattr(row, "project_id") else row[0] for row in rows}


def get_readable_project_ids(user: User, db: Session) -> Optional[Set[int]]:
    """Return None for unrestricted users, otherwise the readable project ids."""
    if user.is_system_admin():
        return None
    admin_ids = _ids_for_user(project_admins, user.id, db)
    member_ids = _ids_for_user(project_members, user.id, db)
    return admin_ids | member_ids


def get_writable_project_ids(user: User, db: Session) -> Optional[Set[int]]:
    """Return None for unrestricted users, otherwise the writable project ids."""
    if user.is_system_admin():
        return None
    if not user.is_project_admin():
        return set()
    return _ids_for_user(project_admins, user.id, db)


def can_read_project(user: User, project_id: int, db: Session) -> bool:
    ids = get_readable_project_ids(user, db)
    return ids is None or project_id in ids


def can_write_project(user: User, project_id: int, db: Session) -> bool:
    ids = get_writable_project_ids(user, db)
    return ids is None or project_id in ids


def normalize_identity(value: object) -> str:
    return str(value or "").strip().lower()


def is_self_identity(user: User, email_or_author: object) -> bool:
    value = normalize_identity(email_or_author)
    if not value:
        return False
    if user.email and value == normalize_identity(user.email):
        return True
    fallbacks = {
        normalize_identity(user.username),
        normalize_identity(user.nickname),
    }
    fallbacks.discard("")
    return value in fallbacks


def identity_matches_user(user: User, value: object) -> bool:
    """Match current user against historical author strings that may contain a name."""
    normalized = normalize_identity(value)
    if is_self_identity(user, normalized):
        return True
    for token in (user.email, user.username, user.nickname):
        token_value = normalize_identity(token)
        if token_value and token_value in normalized:
            return True
    return False


def can_view_person_detail(user: User, target_email: str, db: Session) -> bool:
    if user.is_system_admin():
        return True
    if user.email and normalize_identity(user.email) == normalize_identity(target_email):
        return True
    if not user.is_project_admin():
        return False

    readable_ids = get_readable_project_ids(user, db)
    if not readable_ids:
        return False

    target = (
        db.query(User)
        .filter(User.email == target_email)
        .first()
    )
    if not target:
        return False

    target_member_ids = _ids_for_user(project_members, target.id, db)
    target_admin_ids = _ids_for_user(project_admins, target.id, db)
    return bool(readable_ids & (target_member_ids | target_admin_ids))
```

- [ ] **Step 4: Move HTTP dependencies to `app/api/deps.py`**

Replace `app/api/deps.py` with:

```python
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User


def get_current_user(request: Request) -> str:
    session_token = request.session.get("user")
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return session_token


def get_current_user_obj(request: Request, db: Session) -> User:
    return get_current_user_full(request, db)


def get_current_user_full(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    username = request.session.get("user")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


def get_optional_user(request: Request) -> Optional[str]:
    return request.session.get("user")


def require_system_admin(current_user: User = Depends(get_current_user_full)) -> User:
    if not current_user.is_system_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要系统管理员权限",
        )
    return current_user


def require_project_admin(current_user: User = Depends(get_current_user_full)) -> User:
    if not (current_user.is_system_admin() or current_user.is_project_admin()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要项目管理员或系统管理员权限",
        )
    return current_user
```

In `app/api/users.py`, import these three names and delete their local definitions:

```python
from app.api.deps import get_current_user, get_current_user_full, require_project_admin, require_system_admin
```

Update tests that override the dependency:

```python
from app.api.deps import get_current_user_full
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_core/test_permissions.py tests/test_api/test_efficiency.py tests/test_api/test_efficiency_monthly.py -q`

Expected: PASS for `test_core/test_permissions.py`; existing efficiency tests may still fail because they import old `project_member` semantics. Those are updated in Task 3.

- [ ] **Step 6: Commit**

```bash
git add app/core/__init__.py app/core/permissions.py app/api/deps.py app/api/users.py tests/test_core/test_permissions.py tests/test_api/test_efficiency.py tests/test_api/test_efficiency_monthly.py
git commit -m "refactor: centralize permission helpers"
```

---

### Task 2: Route Project, Logs, And Task Execution Through Permission Helpers

**Files:**
- Modify: `app/api/projects.py`
- Modify: `app/api/logs.py`
- Modify: `app/api/tasks.py`
- Modify: `app/main.py`
- Test: `tests/test_api/test_permissions_visibility.py`

- [ ] **Step 1: Write failing API visibility tests**

Create `tests/test_api/test_permissions_visibility.py`:

```python
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.project import Project
from app.models.task_log import TaskLog
from app.models.user import Role, User, project_admins, project_members
from app.security import security_service


@pytest.fixture
def db_engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _role(db, name):
    role = Role(name=name, description=name, is_system_role=True)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def _user(db, username, password, roles=None, email=None):
    user = User(
        username=username,
        email=email,
        password_hash=security_service.hash_password(password),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    for role in roles or []:
        user.roles.append(role)
    db.commit()
    db.refresh(user)
    return user


def _project(db, name, gitlab_id):
    project = Project(name=name, project_id=gitlab_id, is_active=True)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _login(client, username, password):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def test_plain_user_project_list_uses_project_members_without_project_member_role(client, db_session):
    user = _user(db_session, "plain", "pass123", email="plain@example.com")
    visible = _project(db_session, "visible", 501)
    hidden = _project(db_session, "hidden", 502)
    db_session.execute(project_members.insert().values(project_id=visible.id, user_id=user.id))
    db_session.commit()
    _login(client, "plain", "pass123")

    response = client.get("/api/projects")

    assert response.status_code == 200
    names = [item["name"] for item in response.json()["data"]]
    assert names == ["visible"]


def test_project_admin_can_update_admin_project_but_not_member_project(client, db_session):
    role = _role(db_session, "project_admin")
    user = _user(db_session, "manager", "pass123", roles=[role])
    writable = _project(db_session, "writable", 601)
    read_only = _project(db_session, "read-only", 602)
    db_session.execute(project_admins.insert().values(project_id=writable.id, user_id=user.id))
    db_session.execute(project_members.insert().values(project_id=read_only.id, user_id=user.id))
    db_session.commit()
    _login(client, "manager", "pass123")

    ok = client.put(f"/api/projects/{writable.id}", json={"description": "changed"})
    denied = client.put(f"/api/projects/{read_only.id}", json={"description": "changed"})

    assert ok.status_code == 200
    assert denied.status_code == 403


def test_plain_user_can_read_own_project_log_but_not_other_log(client, db_session):
    user = _user(db_session, "plain", "pass123")
    visible = _project(db_session, "visible", 701)
    hidden = _project(db_session, "hidden", 702)
    db_session.execute(project_members.insert().values(project_id=visible.id, user_id=user.id))
    db_session.add(TaskLog(project_id=visible.id, project_name="visible", task_type="daily", trigger_type="manual", trigger_user="a", status="success"))
    db_session.add(TaskLog(project_id=hidden.id, project_name="hidden", task_type="daily", trigger_type="manual", trigger_user="a", status="success"))
    db_session.commit()
    _login(client, "plain", "pass123")

    response = client.get("/api/logs")

    assert response.status_code == 200
    names = [item["project_name"] for item in response.json()["data"]]
    assert names == ["visible"]
```

- [ ] **Step 2: Run the failing tests**

Run: `pytest tests/test_api/test_permissions_visibility.py -q`

Expected: FAIL because `_filter_projects_by_permission` still gates `project_members` behind `is_project_member()`.

- [ ] **Step 3: Update compatibility wrappers in `projects.py`**

In `app/api/projects.py`, import:

```python
from app.core.permissions import (
    can_read_project,
    can_write_project,
    get_readable_project_ids,
)
```

Replace `_filter_projects_by_permission` and `_check_project_permission` with:

```python
def _filter_projects_by_permission(projects: List[Project], user: User, db: Session) -> List[Project]:
    allowed_ids = get_readable_project_ids(user, db)
    if allowed_ids is None:
        return projects
    return [p for p in projects if p.id in allowed_ids]


def _check_project_permission(user: User, project_id: int, require_write: bool = False, db: Session = None) -> bool:
    if db is None:
        return False
    if require_write:
        return can_write_project(user, project_id, db)
    return can_read_project(user, project_id, db)
```

Also change imports from `app.api.users` to:

```python
from app.api.deps import get_current_user_full, require_project_admin, require_system_admin
```

- [ ] **Step 4: Update logs and tasks imports**

In `app/api/logs.py`, import from `deps.py` and use `get_readable_project_ids` directly:

```python
from app.api.deps import get_current_user_full
from app.core.permissions import get_readable_project_ids
```

Build `allowed_project_ids` with:

```python
allowed_project_ids = get_readable_project_ids(current_user, db)
if allowed_project_ids is not None:
    query = query.filter(TaskLog.project_id.in_(allowed_project_ids))
```

In `get_log_detail`, use:

```python
if allowed_project_ids is not None and log.project_id not in allowed_project_ids:
    return ApiResponse.fail(code="FORBIDDEN", message="您没有权限查看此日志")
```

In `app/api/tasks.py`, import:

```python
from app.api.deps import get_current_user_full
from app.core.permissions import can_write_project, get_writable_project_ids
```

Replace write checks with `can_write_project`. In `_run_review_task`, replace the admin-only query block with:

```python
writable_ids = get_writable_project_ids(user_obj, db)
if writable_ids is not None:
    projects_list = [p for p in projects_list if p.id in writable_ids]
```

- [ ] **Step 5: Update `main.py` page filtering imports**

Replace `from app.api.projects import _filter_projects_by_permission` with:

```python
from app.core.permissions import get_readable_project_ids
```

Add a small local helper in `main.py` near the imports:

```python
def _filter_projects_for_user(projects, user, db):
    allowed_ids = get_readable_project_ids(user, db)
    if allowed_ids is None:
        return projects
    return [p for p in projects if p.id in allowed_ids]
```

Replace all `_filter_projects_by_permission(...)` calls in `main.py` with `_filter_projects_for_user(...)`.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_api/test_permissions_visibility.py tests/test_api/test_projects_sync.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/api/projects.py app/api/logs.py app/api/tasks.py app/main.py tests/test_api/test_permissions_visibility.py
git commit -m "refactor: use shared project permissions"
```

---

### Task 3: Fix Efficiency List And Detail Visibility

**Files:**
- Modify: `app/api/efficiency.py`
- Modify: `app/static/js/efficiency.js`
- Modify: `tests/test_api/test_efficiency.py`
- Modify: `tests/test_api/test_efficiency_monthly.py`

- [ ] **Step 1: Update failing daily efficiency tests**

In `tests/test_api/test_efficiency.py`, change imports to `from app.api.deps import get_current_user_full`.

Replace `member_user` with a no-role fixture:

```python
@pytest.fixture
def plain_user(db_session):
    return _make_user(db_session, "member", "member@x.com", [])


@pytest.fixture
def login_as_plain(app, plain_user):
    app.dependency_overrides[get_current_user_full] = lambda: plain_user
    yield plain_user
    app.dependency_overrides.pop(get_current_user_full, None)
```

Replace `test_list_member_sees_only_self` with:

```python
def test_list_plain_user_sees_all_people(client, db_session, login_as_plain, plain_user):
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, plain_user.email, "Self", yesterday)
    _seed(db_session, "other@x.com", "Other", yesterday)

    resp = client.get("/api/efficiency/list")

    assert resp.status_code == 200
    emails = [i["author_email"] for i in resp.json()["data"]["items"]]
    assert plain_user.email in emails
    assert "other@x.com" in emails


def test_detail_plain_user_cannot_view_other_person(client, db_session, login_as_plain, plain_user):
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, "other@x.com", "Other", yesterday)

    resp = client.get(f"/api/efficiency/detail?email=other@x.com&date={yesterday.isoformat()}")

    assert resp.status_code == 403


def test_detail_plain_user_can_view_self(client, db_session, login_as_plain, plain_user):
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, plain_user.email, "Self", yesterday)

    resp = client.get(f"/api/efficiency/detail?email={plain_user.email}&date={yesterday.isoformat()}")

    assert resp.status_code == 200
```

- [ ] **Step 2: Update failing monthly tests**

In `tests/test_api/test_efficiency_monthly.py`, change imports to `from app.api.deps import get_current_user_full`.

Replace the project-member detail test with:

```python
def test_monthly_detail_plain_user_sees_only_self(client, db_session, app):
    plain = _make_user(db_session, "member1", "m1@x.com", [])
    _seed_monthly(db_session, "other@x.com", "Other", "2026-05")
    _seed_monthly(db_session, "m1@x.com", "Member1", "2026-05")

    app.dependency_overrides[get_current_user_full] = lambda: plain
    resp = client.get(
        "/api/efficiency/monthly/detail?email=other@x.com&year_month=2026-05"
    )

    assert resp.status_code == 403
    app.dependency_overrides.pop(get_current_user_full, None)
```

Add list visibility:

```python
def test_monthly_list_plain_user_sees_all_people(client, db_session, app):
    plain = _make_user(db_session, "plain", "plain@x.com", [])
    _seed_monthly(db_session, "plain@x.com", "Plain", "2026-05")
    _seed_monthly(db_session, "other@x.com", "Other", "2026-05")
    app.dependency_overrides[get_current_user_full] = lambda: plain

    resp = client.get("/api/efficiency/monthly/list?year_month=2026-05")

    assert resp.status_code == 200
    emails = [i["author_email"] for i in resp.json()["data"]["items"]]
    assert emails == ["plain@x.com", "other@x.com"]
    app.dependency_overrides.pop(get_current_user_full, None)
```

- [ ] **Step 3: Run failing tests**

Run: `pytest tests/test_api/test_efficiency.py tests/test_api/test_efficiency_monthly.py -q`

Expected: FAIL because `efficiency.py` still filters lists and detail guards depend on `is_project_member()`.

- [ ] **Step 4: Update `app/api/efficiency.py`**

Import:

```python
from app.api.deps import get_current_user_full
from app.core.permissions import can_view_person_detail
```

Keep `_allowed_project_names` only if used by helper tests, but remove calls to `_restrict_query_by_user` from `list_efficiency` and `monthly_list`.

Replace the `/detail` guard with:

```python
    if not can_view_person_detail(current_user, email, db):
        raise HTTPException(403, "无权查看他人能效详情")
```

Replace the `/monthly/detail` guard with:

```python
    if not can_view_person_detail(current_user, email, db):
        raise HTTPException(403, "无权查看他人月度能效详情")
```

Leave recompute endpoints as system-admin only.

- [ ] **Step 5: Add front-end 403 hint**

In `app/static/js/efficiency.js`, add:

```javascript
function renderPermissionDenied(targetId) {
    var el = document.getElementById(targetId);
    if (el) {
        el.innerHTML = '<div class="text-warning">无权查看该人员明细</div>';
    }
}
```

In `openRangeDetailModal`, `openMonthlyDetailModal`, `openDrawerForDate`, and `openDrawer`, change the non-OK branch to check `resp.status === 403` and call `renderPermissionDenied(...)` with the target body id.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_api/test_efficiency.py tests/test_api/test_efficiency_monthly.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/api/efficiency.py app/static/js/efficiency.js tests/test_api/test_efficiency.py tests/test_api/test_efficiency_monthly.py
git commit -m "fix: update efficiency visibility rules"
```

---

### Task 4: Add Report And Webhook User-Level Filtering

**Files:**
- Modify: `app/api/reports.py`
- Modify: `app/api/webhook_reviews.py`
- Modify: `app/templates/webhook_reviews.html`
- Test: `tests/test_api/test_permissions_visibility.py`

- [ ] **Step 1: Add failing report and Webhook tests**

Append to `tests/test_api/test_permissions_visibility.py`:

```python
from app.models.webhook_review import MrReviewLog


def test_plain_user_webhook_reviews_only_include_self(client, db_session):
    user = _user(db_session, "plain", "pass123", email="plain@example.com")
    project = _project(db_session, "visible", 801)
    db_session.execute(project_members.insert().values(project_id=project.id, user_id=user.id))
    db_session.add(MrReviewLog(project_name="visible", author="plain@example.com", updated_at=1, score=80))
    db_session.add(MrReviewLog(project_name="visible", author="other@example.com", updated_at=2, score=80))
    db_session.commit()
    _login(client, "plain", "pass123")

    response = client.get("/api/webhook-reviews?review_type=mr")

    assert response.status_code == 200
    authors = [item["author"] for item in response.json()["data"]["items"]]
    assert authors == ["plain@example.com"]


def test_project_admin_webhook_reviews_include_readable_project_members(client, db_session):
    role = _role(db_session, "project_admin")
    user = _user(db_session, "manager", "pass123", roles=[role], email="manager@example.com")
    project = _project(db_session, "visible", 811)
    db_session.execute(project_members.insert().values(project_id=project.id, user_id=user.id))
    db_session.add(MrReviewLog(project_name="visible", author="other@example.com", updated_at=1, score=80))
    db_session.commit()
    _login(client, "manager", "pass123")

    response = client.get("/api/webhook-reviews?review_type=mr")

    assert response.status_code == 200
    assert [item["author"] for item in response.json()["data"]["items"]] == ["other@example.com"]
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_api/test_permissions_visibility.py -q`

Expected: FAIL because Webhook filtering only checks project role gates.

- [ ] **Step 3: Update reports filtering**

In `app/api/reports.py`, import:

```python
from app.api.deps import get_current_user_full, get_current_user_obj
from app.core.permissions import get_readable_project_ids, can_write_project, identity_matches_user
```

Replace `_get_user_allowed_project_names` with a helper that uses project ids:

```python
def _get_user_allowed_project_names(user: User, db: Session) -> set[str]:
    allowed_ids = get_readable_project_ids(user, db)
    query = db.query(Project.name)
    if allowed_ids is not None:
        if not allowed_ids:
            return set()
        query = query.filter(Project.id.in_(allowed_ids))
    return {row[0] for row in query.all()}
```

In `list_reports`, before appending each report:

```python
                        if not current_user.is_system_admin() and not current_user.is_project_admin():
                            if not identity_matches_user(current_user, file_author):
                                continue
```

In `get_report_content` and `download_report`, after project permission check:

```python
    if not current_user.is_system_admin() and not current_user.is_project_admin():
        if not identity_matches_user(current_user, author):
            raise HTTPException(status_code=403, detail="您没有权限查看他人的报告")
```

In `delete_report`, use:

```python
        if not can_write_project(current_user, project_obj.id, db):
            raise HTTPException(status_code=403, detail="您没有权限删除此项目的报告")
```

- [ ] **Step 4: Update Webhook filtering**

In `app/api/webhook_reviews.py`, import:

```python
from sqlalchemy import func, desc, or_
from app.api.deps import get_current_user_full
from app.core.permissions import can_write_project, get_readable_project_ids, identity_matches_user
```

Replace `_get_user_allowed_project_names` with the same project-id based implementation used in reports.

Add:

```python
def _author_conditions(model, user: User):
    values = [user.email, user.username, user.nickname]
    return [model.author.contains(v) for v in values if v]
```

After project filtering in list/detail/stats:

```python
    if not current_user.is_system_admin() and not current_user.is_project_admin():
        conds = _author_conditions(model, current_user)
        if not conds:
            query = query.filter(False)
        else:
            query = query.filter(or_(*conds))
```

For detail endpoints, after fetching item:

```python
    if not current_user.is_system_admin() and not current_user.is_project_admin():
        if not identity_matches_user(current_user, item.author):
            raise HTTPException(status_code=403, detail="您没有权限查看此审查记录")
```

For delete, replace project-admin role-only check with `can_write_project`.

- [ ] **Step 5: Update Webhook delete button permission**

In `app/templates/webhook_reviews.html`, keep the delete button driven by backend-safe behavior. Change:

```javascript
const canDelete = currentUserRoles.includes('system_admin') || currentUserRoles.includes('project_admin');
```

to:

```javascript
const canDelete = currentUserRoles.includes('system_admin') || currentUserRoles.includes('project_admin');
```

No visual change is required for this task; backend still denies project admins deleting non-writable projects. A later UI task can make row-level delete smarter if the API returns `can_delete`.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_api/test_permissions_visibility.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/api/reports.py app/api/webhook_reviews.py app/templates/webhook_reviews.html tests/test_api/test_permissions_visibility.py
git commit -m "feat: add personal report and webhook filtering"
```

---

### Task 5: Remove `project_member` Role From APIs And Manual Write Paths

**Files:**
- Modify: `app/database.py`
- Modify: `app/models/user.py`
- Modify: `app/api/roles.py`
- Modify: `app/api/users.py`
- Modify: `app/api/projects.py`
- Test: `tests/test_api/test_permissions_visibility.py`
- Test: `tests/test_api/test_settings_api.py`

- [ ] **Step 1: Add failing role and write-path tests**

Append to `tests/test_api/test_permissions_visibility.py`:

```python
def test_builtin_role_definitions_exclude_project_member(client, db_session):
    admin_role = _role(db_session, "system_admin")
    _user(db_session, "admin", "pass123", roles=[admin_role])
    _login(client, "admin", "pass123")

    response = client.get("/api/roles/definitions/builtin")

    assert response.status_code == 200
    names = [role["name"] for role in response.json()]
    assert names == ["system_admin", "project_admin"]


def test_roles_list_excludes_project_member(client, db_session):
    admin_role = _role(db_session, "system_admin")
    _role(db_session, "project_member")
    _role(db_session, "project_admin")
    _user(db_session, "admin", "pass123", roles=[admin_role])
    _login(client, "admin", "pass123")

    response = client.get("/api/roles")

    assert response.status_code == 200
    names = [role["name"] for role in response.json()]
    assert "project_member" not in names


def test_assign_member_projects_is_disabled(client, db_session):
    admin_role = _role(db_session, "system_admin")
    target = _user(db_session, "target", "pass123")
    _user(db_session, "admin", "pass123", roles=[admin_role])
    project = _project(db_session, "visible", 901)
    _login(client, "admin", "pass123")

    response = client.post(f"/api/users/{target.id}/projects/member", json=[project.id])

    assert response.status_code == 400
    assert "GitLab 同步维护" in response.json()["detail"]
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_api/test_permissions_visibility.py -q`

Expected: FAIL because `project_member` still appears and member write endpoints still write.

- [ ] **Step 3: Stop initializing `project_member` and clean historical user-role links**

In `app/database.py`, remove the `project_member` dict from `system_roles`.

Add before `_init_system_roles()` in `init_db()`:

```python
    _migrate_remove_project_member_roles()
```

Add:

```python
def _migrate_remove_project_member_roles():
    insp = inspect(engine)
    if not (insp.has_table("roles") and insp.has_table("user_roles")):
        return
    with engine.connect() as conn:
        conn.execute(text(
            "DELETE FROM user_roles "
            "WHERE role_id IN (SELECT id FROM roles WHERE name = 'project_member')"
        ))
        conn.commit()
```

Keep `Role.PROJECT_MEMBER = 'project_member'` for compatibility with old rows, but do not use it in new logic.

- [ ] **Step 4: Filter roles API**

In `app/api/roles.py`, import deps from `app.api.deps`.

In `list_roles`:

```python
roles = (
    db.query(Role)
    .filter(Role.name != Role.PROJECT_MEMBER)
    .order_by(Role.is_system_role.desc(), Role.name)
    .all()
)
```

In `get_role`, reject hidden role:

```python
if not role or role.name == Role.PROJECT_MEMBER:
    raise HTTPException(status_code=404, detail="角色不存在")
```

In `get_builtin_role_definitions`, return only `SYSTEM_ADMIN` and `PROJECT_ADMIN`.

- [ ] **Step 5: Disable manual member write endpoints**

In `app/api/users.py`, replace `assign_member_projects` body with:

```python
@router.post("/{user_id}/projects/member")
async def assign_member_projects(
    user_id: int,
    project_ids: List[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_admin),
):
    raise HTTPException(status_code=400, detail="项目成员由 GitLab 同步维护")
```

In `app/api/projects.py`, inside `add_project_member`, keep `role == "admin"` behavior. For member:

```python
    if role != "admin":
        raise HTTPException(status_code=400, detail="项目成员由 GitLab 同步维护")
```

In `remove_project_member`, replace the body after permission check with:

```python
    raise HTTPException(status_code=400, detail="项目成员由 GitLab 同步维护")
```

- [ ] **Step 6: Filter role updates**

In `update_user_roles`, reject `project_member`:

```python
    role_names = [name for name in role_names if name != Role.PROJECT_MEMBER]
```

This silently ignores old front-end submissions during rollout.

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_api/test_permissions_visibility.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/database.py app/models/user.py app/api/roles.py app/api/users.py app/api/projects.py tests/test_api/test_permissions_visibility.py
git commit -m "refactor: retire project member role"
```

---

### Task 6: Update Templates For Menus, Roles, Users, Projects, And Efficiency Hints

**Files:**
- Modify: `app/templates/base.html`
- Modify: `app/templates/projects.html`
- Modify: `app/templates/users.html`
- Modify: `app/templates/roles.html`
- Modify: `app/main.py`
- Test: `tests/test_templates/test_permission_templates.py`

- [ ] **Step 1: Add failing template tests**

Create `tests/test_templates/test_permission_templates.py`:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_base_role_map_no_project_member_label():
    html = _read("app/templates/base.html")
    assert "'project_member': '项目成员'" not in html


def test_users_template_has_no_project_member_role_checkbox():
    html = _read("app/templates/users.html")
    assert 'value="project_member"' not in html
    assert "项目成员权限" not in html


def test_roles_template_has_no_project_member_card_style():
    html = _read("app/templates/roles.html")
    assert "project_member" not in html


def test_projects_template_has_guarded_action_columns():
    html = _read("app/templates/projects.html")
    assert "can_manage" in html
    assert "show_actions" in html
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_templates/test_permission_templates.py -q`

Expected: FAIL because templates still contain `project_member` and projects do not have row-level permissions.

- [ ] **Step 3: Update `base.html` role display**

Remove the `project_member` entry from `roleMap`. Use:

```javascript
const roleMap = {
    'system_admin': '系统管理员',
    'project_admin': '项目管理员'
};
const roleNames = data.roles.map(r => roleMap[r] || r).filter(Boolean).join(', ');
roleElement.textContent = roleNames || '用户';
```

- [ ] **Step 4: Update projects page context and template**

In `app/main.py` `projects_page`, after `projects_data` is built, include:

```python
from app.core.permissions import can_write_project

for item in projects_data:
    item["can_manage"] = db_user.is_system_admin() or can_write_project(db_user, item["id"], db)
show_actions = any(item["can_manage"] for item in projects_data)
```

Pass `show_actions` into the template.

In `app/templates/projects.html`:

- Wrap checkbox header/body with `{% if show_actions %}`.
- Wrap operation header/body with `{% if show_actions %}`.
- For each row, render operations only if `project.can_manage`.
- Leave the "新增" button visible for `system_admin` and `project_admin`.

Use this pattern:

```jinja2
{% if show_actions %}
<th style="width: 40px;"><input type="checkbox" id="selectAll" onclick="toggleSelectAll()"></th>
<th style="width: 80px;">操作</th>
{% endif %}
```

```jinja2
{% if show_actions %}
<td>{% if project.can_manage %}<input type="checkbox" class="row-checkbox" value="{{ project.id }}">{% endif %}</td>
<td>{% if project.can_manage %}...operation menu...{% endif %}</td>
{% endif %}
```

- [ ] **Step 5: Update users template**

In `app/templates/users.html`:

- Remove both `project_member` role checkboxes.
- Remove `canCurrentUserAssignProjectMember`.
- Remove the `memberCol` from the project permission modal, or convert it to read-only display with text `项目成员由 GitLab 同步维护`.
- In `renderUsers`, map unknown roles to raw names but only map current two roles.
- In `saveProjectPermissions`, remove the call to `/api/users/{user_id}/projects/member`.

- [ ] **Step 6: Update roles template**

In `app/templates/roles.html`, remove the `project_member` color entry and make fallback use `project_admin`:

```javascript
const roleColors = {
    'system_admin': { bg: 'var(--color-error-light, #fee2e2)', color: 'var(--color-error, #dc2626)', icon: 'bi-shield-fill', badge: 'badge-error' },
    'project_admin': { bg: 'var(--color-warning-light, #fef3c7)', color: 'var(--color-warning, #d97706)', icon: 'bi-folder-check', badge: 'badge-warning' }
};
const colors = roleColors[role.name] || roleColors['project_admin'];
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_templates/test_permission_templates.py tests/test_templates/test_base_user_display.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/templates/base.html app/templates/projects.html app/templates/users.html app/templates/roles.html app/main.py tests/test_templates/test_permission_templates.py
git commit -m "refactor: update permission templates"
```

---

### Task 7: Add GitLab Sync Settings And Scheduler Wiring

**Files:**
- Modify: `app/models/settings.py`
- Modify: `app/schemas/settings.py`
- Modify: `app/api/settings.py`
- Modify: `app/templates/settings.html`
- Modify: `app/services/scheduler.py`
- Modify: `app/main.py`
- Test: `tests/test_api/test_settings_api.py`

- [ ] **Step 1: Add failing settings API tests**

Append to `tests/test_api/test_settings_api.py`:

```python
class TestGitLabSyncSettings:
    def test_update_gitlab_sync_settings_encrypts_default_password(self, admin_session, settings_without_api_key, db_session):
        response = admin_session.put("/api/settings", json={
            "gitlab_sync_enabled": True,
            "gitlab_sync_schedule_time": "03:30",
            "gitlab_sync_default_password": "abc123",
        })

        assert response.status_code == 200
        db_session.refresh(settings_without_api_key)
        assert settings_without_api_key.gitlab_sync_enabled is True
        assert settings_without_api_key.gitlab_sync_schedule_time == "03:30"
        assert security_service.decrypt(settings_without_api_key.gitlab_sync_default_password) == "abc123"

    def test_update_gitlab_sync_default_password_empty_preserves_old(self, admin_session, settings_without_api_key, db_session):
        settings_without_api_key.gitlab_sync_default_password = security_service.encrypt("abc123")
        db_session.commit()

        response = admin_session.put("/api/settings", json={"gitlab_sync_default_password": ""})

        assert response.status_code == 200
        db_session.refresh(settings_without_api_key)
        assert security_service.decrypt(settings_without_api_key.gitlab_sync_default_password) == "abc123"

    def test_invalid_gitlab_sync_schedule_time_returns_422(self, admin_session, settings_without_api_key):
        response = admin_session.put("/api/settings", json={"gitlab_sync_schedule_time": "3:00"})

        assert response.status_code == 422
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_api/test_settings_api.py -q`

Expected: FAIL because settings fields do not exist.

- [ ] **Step 3: Add model and schema fields**

In `app/models/settings.py`, add near scheduling config:

```python
    gitlab_sync_enabled = Column(Boolean, default=True, comment="是否启用 GitLab 项目及成员自动同步")
    gitlab_sync_schedule_time = Column(String(5), nullable=True, default="03:00", comment="GitLab 同步每日执行时间 (HH:MM)")
    gitlab_sync_default_password = Column(String(500), nullable=True, comment="GitLab 同步新用户默认密码 (加密存储)")
```

In `app/schemas/settings.py`, add to `SettingsBase`:

```python
    gitlab_sync_enabled: bool = True
    gitlab_sync_schedule_time: str = Field(default="03:00")
    gitlab_sync_default_password: Optional[str] = None
```

Add validator:

```python
    @field_validator('gitlab_sync_schedule_time')
    @classmethod
    def validate_gitlab_sync_schedule_time(cls, v: str) -> str:
        if not re.match(r'^\d{2}:\d{2}$', v):
            raise ValueError(f'GitLab 同步时间格式错误: {v}，需要 HH:MM 格式')
        return v
```

Add matching optional fields to `SettingsUpdate`.

- [ ] **Step 4: Encrypt and mask setting**

In `app/api/settings.py`, add `gitlab_sync_default_password` to `sensitive_fields`.

After building `response_data`, mask:

```python
response_data.gitlab_sync_default_password = "****" if settings.gitlab_sync_default_password else None
```

Do this in both `get_settings` and `update_settings`.

- [ ] **Step 5: Add scheduler registration helper**

In `app/main.py`, add:

```python
def run_gitlab_project_member_sync():
    from app.database import SessionLocal
    from app.services.gitlab_sync import GitLabProjectMemberSyncService

    db = SessionLocal()
    try:
        GitLabProjectMemberSyncService(db).sync()
    finally:
        db.close()
```

In startup scheduler registration:

```python
            if settings.gitlab_sync_enabled and settings.gitlab_sync_schedule_time:
                hour, minute = settings.gitlab_sync_schedule_time.split(":")
                scheduler.add_job(
                    func=run_gitlab_project_member_sync,
                    trigger_type="cron",
                    job_id="gitlab_project_member_sync",
                    hour=int(hour),
                    minute=int(minute),
                )
```

In `app/api/settings.py`, import `run_gitlab_project_member_sync` inside `_refresh_scheduler`, remove old `gitlab_project_member_sync` jobs, and add the same `scheduler.add_job(...)` block.

- [ ] **Step 6: Add settings form fields**

In `app/templates/settings.html`, add a panel under GitLab configuration:

```jinja2
<div class="panel" style="margin-bottom: var(--space-4);">
    <div class="panel-heading">
        <div class="panel-title"><i class="bi bi-arrow-repeat"></i> GitLab 同步配置</div>
        <div style="display: flex; align-items: center; gap: var(--space-2);">
            <input type="checkbox" id="gitlab_sync_enabled" name="gitlab_sync_enabled"
                   {% if settings.gitlab_sync_enabled %}checked{% endif %}
                   style="width: 18px; height: 18px; accent-color: var(--color-primary);">
            <label for="gitlab_sync_enabled" style="margin: 0; font-size: var(--text-sm);">启用自动同步</label>
        </div>
    </div>
    <div class="panel-body">
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-4);">
            <div class="form-group">
                <label for="gitlab_sync_schedule_time" class="form-label">每日同步时间</label>
                <input type="time" class="form-input" id="gitlab_sync_schedule_time"
                       name="gitlab_sync_schedule_time"
                       value="{{ settings.gitlab_sync_schedule_time or '03:00' }}">
            </div>
            <div class="form-group">
                <label for="gitlab_sync_default_password" class="form-label">新用户默认密码</label>
                <input type="password" class="form-input" id="gitlab_sync_default_password"
                       name="gitlab_sync_default_password" placeholder="留空保持原值">
            </div>
        </div>
    </div>
</div>
```

Add to submitted `data`:

```javascript
        gitlab_sync_enabled: formData.get('gitlab_sync_enabled') === 'on',
        gitlab_sync_schedule_time: formData.get('gitlab_sync_schedule_time') || '03:00',
        gitlab_sync_default_password: formData.get('gitlab_sync_default_password') || null,
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_api/test_settings_api.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/models/settings.py app/schemas/settings.py app/api/settings.py app/templates/settings.html app/main.py tests/test_api/test_settings_api.py
git commit -m "feat: add gitlab sync settings"
```

---

### Task 8: Implement GitLab Members Client And Sync Service

**Files:**
- Modify: `app/services/gitlab_client.py`
- Create: `app/services/gitlab_sync.py`
- Test: `tests/test_services/test_gitlab_sync.py`

- [ ] **Step 1: Add failing sync service tests**

Create `tests/test_services/test_gitlab_sync.py`:

```python
from unittest.mock import Mock

import pytest

from app.models.project import Project
from app.models.settings import Settings
from app.models.user import Role, User, project_admins, project_members
from app.security import security_service
from app.services.gitlab_sync import GitLabProjectMemberSyncService


def _role(db, name):
    role = db.query(Role).filter(Role.name == name).first()
    if not role:
        role = Role(name=name, description=name, is_system_role=True)
        db.add(role)
        db.commit()
        db.refresh(role)
    return role


def _settings(db):
    settings = Settings(
        global_gitlab_url="https://gitlab.example.com",
        global_gitlab_token=security_service.encrypt("token"),
        gitlab_sync_default_password=security_service.encrypt("abc123"),
        llm_api_url="https://llm.example.com",
        llm_model="gpt",
        report_output_dir="./data/reports",
    )
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def test_sync_existing_project_members_and_direct_maintainer_admin(db_session, monkeypatch):
    _role(db_session, "project_admin")
    _settings(db_session)
    project = Project(name="Existing", project_id=100, is_active=True)
    db_session.add(project)
    db_session.commit()

    client = Mock()
    client.list_accessible_projects.return_value = [{"id": 100, "name": "Existing", "description": ""}]
    client.get_project_members.return_value = [
        {"id": 1, "username": "maint", "name": "Maint", "email": "maint@example.com", "access_level": 40, "source_type": "project"},
        {"id": 2, "username": "dev", "name": "Dev", "email": "dev@example.com", "access_level": 30, "source_type": "project"},
    ]
    monkeypatch.setattr("app.services.gitlab_sync.GitLabClient", lambda url, token: client)

    result = GitLabProjectMemberSyncService(db_session).sync()

    maint = db_session.query(User).filter(User.username == "maint").one()
    dev = db_session.query(User).filter(User.username == "dev").one()
    assert result.created_users == 2
    assert db_session.execute(project_members.select().where(project_members.c.user_id == maint.id)).fetchone()
    assert db_session.execute(project_members.select().where(project_members.c.user_id == dev.id)).fetchone()
    assert db_session.execute(project_admins.select().where(project_admins.c.user_id == maint.id)).fetchone()
    assert not db_session.execute(project_admins.select().where(project_admins.c.user_id == dev.id)).fetchone()
    assert maint.has_role("project_admin")
    assert security_service.verify_password("abc123", maint.password_hash)


def test_inherited_maintainer_is_member_not_project_admin(db_session, monkeypatch):
    _role(db_session, "project_admin")
    _settings(db_session)
    project = Project(name="Existing", project_id=200, is_active=True)
    db_session.add(project)
    db_session.commit()
    client = Mock()
    client.list_accessible_projects.return_value = [{"id": 200, "name": "Existing", "description": ""}]
    client.get_project_members.return_value = [
        {"id": 3, "username": "group-owner", "name": "Group Owner", "email": "go@example.com", "access_level": 50, "source_type": "group"},
    ]
    monkeypatch.setattr("app.services.gitlab_sync.GitLabClient", lambda url, token: client)

    GitLabProjectMemberSyncService(db_session).sync()

    user = db_session.query(User).filter(User.username == "group-owner").one()
    assert db_session.execute(project_members.select().where(project_members.c.user_id == user.id)).fetchone()
    assert not db_session.execute(project_admins.select().where(project_admins.c.user_id == user.id)).fetchone()
    assert not user.has_role("project_admin")


def test_member_fetch_failure_does_not_clear_old_relationship(db_session, monkeypatch):
    _settings(db_session)
    old_user = User(username="old", password_hash=security_service.hash_password("oldpass"), is_active=True)
    project = Project(name="Existing", project_id=300, is_active=True)
    db_session.add_all([old_user, project])
    db_session.commit()
    db_session.execute(project_members.insert().values(project_id=project.id, user_id=old_user.id))
    db_session.commit()
    client = Mock()
    client.list_accessible_projects.return_value = [{"id": 300, "name": "Existing", "description": ""}]
    client.get_project_members.side_effect = RuntimeError("boom")
    monkeypatch.setattr("app.services.gitlab_sync.GitLabClient", lambda url, token: client)

    result = GitLabProjectMemberSyncService(db_session).sync()

    assert result.failed_projects == 1
    assert db_session.execute(project_members.select().where(project_members.c.user_id == old_user.id)).fetchone()
```

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_services/test_gitlab_sync.py -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Add GitLab member client method**

In `app/services/gitlab_client.py`, add:

```python
    def get_project_members(self, project_id: int) -> List[Dict[str, Any]]:
        try:
            project = self.client.projects.get(project_id)
            direct_members = project.members.list(get_all=True)
            all_members = project.members_all.list(get_all=True)
            direct_ids = {getattr(member, "id", None) for member in direct_members}
            result = []
            for member in all_members:
                source_type = "project" if getattr(member, "id", None) in direct_ids else "group"
                source = getattr(member, "source", None)
                if isinstance(source, dict):
                    source_type = source.get("type") or source_type
                result.append({
                    "id": getattr(member, "id", None),
                    "username": getattr(member, "username", "") or "",
                    "name": getattr(member, "name", "") or "",
                    "email": getattr(member, "email", "") or "",
                    "access_level": getattr(member, "access_level", 0) or 0,
                    "source_type": source_type,
                })
            return result
        except gitlab.exceptions.GitlabAuthenticationError as e:
            raise GitLabAuthError(
                f"获取项目成员失败: Token 认证失败 (项目 ID: {project_id})",
                project_id=project_id,
            ) from e
        except gitlab.exceptions.GitlabConnectionError as e:
            raise GitLabConnectionError(
                f"获取项目成员失败: 无法连接到 GitLab ({self.gitlab_url})",
                gitlab_url=self.gitlab_url,
            ) from e
```

- [ ] **Step 4: Add sync service**

Create `app/services/gitlab_sync.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.settings import Settings
from app.models.user import Role, User, project_admins, project_members
from app.security import security_service
from app.services.gitlab_client import GitLabClient


MAINTAINER = 40
OWNER = 50


@dataclass
class GitLabSyncResult:
    total: int = 0
    created: int = 0
    skipped: int = 0
    failed: int = 0
    created_users: int = 0
    skipped_users: int = 0
    synced_members: int = 0
    synced_admins: int = 0
    failed_projects: int = 0
    failed_reasons: List[Dict[str, Any]] = field(default_factory=list)
    removed_admins: List[Dict[str, Any]] = field(default_factory=list)
    created_projects: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class GitLabProjectMemberSyncService:
    def __init__(self, db: Session):
        self.db = db

    def sync(self) -> GitLabSyncResult:
        settings = self.db.query(Settings).first()
        if not settings:
            raise ValueError("系统设置未配置")
        if not settings.global_gitlab_url:
            raise ValueError("全局 GitLab URL 未配置")
        if not settings.global_gitlab_token:
            raise ValueError("全局 GitLab Token 未配置")
        token = security_service.decrypt(settings.global_gitlab_token)
        default_password = (
            security_service.decrypt(settings.gitlab_sync_default_password)
            if settings.gitlab_sync_default_password else None
        )
        client = GitLabClient(settings.global_gitlab_url, token)
        projects = client.list_accessible_projects()
        result = GitLabSyncResult(total=len(projects))

        for remote in projects:
            project = self._ensure_project(remote, result)
            try:
                members = client.get_project_members(project.project_id)
                if not members:
                    result.failed_projects += 1
                    result.failed_reasons.append({"project_id": project.project_id, "reason": "empty members"})
                    continue
                self._replace_project_members(project, members, default_password, result)
            except Exception as exc:
                self.db.rollback()
                result.failed_projects += 1
                result.failed_reasons.append({"project_id": project.project_id, "reason": str(exc)})
                logger.warning(f"同步项目成员失败 project_id={project.project_id}: {exc}")

        return result

    def _ensure_project(self, remote: Dict[str, Any], result: GitLabSyncResult) -> Project:
        gitlab_id = int(remote["id"])
        project = self.db.query(Project).filter(Project.project_id == gitlab_id).first()
        if project:
            result.skipped += 1
            return project
        name = remote.get("name") or remote.get("path_with_namespace") or f"project {gitlab_id}"
        project = Project(
            name=name[:100],
            project_id=gitlab_id,
            description=remote.get("description") or None,
            target_branches=None,
            is_active=True,
        )
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        result.created += 1
        result.created_projects.append({"name": project.name, "project_id": project.project_id})
        return project

    def _replace_project_members(
        self,
        project: Project,
        members: List[Dict[str, Any]],
        default_password: Optional[str],
        result: GitLabSyncResult,
    ) -> None:
        users = []
        for member in members:
            user = self._find_or_create_user(member, default_password, result)
            if user:
                users.append((user, member))
        if not users:
            result.failed_projects += 1
            result.failed_reasons.append({"project_id": project.project_id, "reason": "no matched users"})
            return

        old_admin_rows = self.db.execute(
            project_admins.select().where(project_admins.c.project_id == project.id)
        ).fetchall()
        system_admin_ids = {
            user.id for user in self.db.query(User).join(User.roles).filter(Role.name == Role.SYSTEM_ADMIN).all()
        }
        for row in old_admin_rows:
            if row.user_id not in system_admin_ids:
                result.removed_admins.append({"project_id": project.id, "user_id": row.user_id})

        self.db.execute(project_members.delete().where(project_members.c.project_id == project.id))
        self.db.execute(
            project_admins.delete().where(
                (project_admins.c.project_id == project.id)
                & (~project_admins.c.user_id.in_(system_admin_ids or {-1}))
            )
        )

        now = int(datetime.now().timestamp())
        project_admin_role = self.db.query(Role).filter(Role.name == Role.PROJECT_ADMIN).first()
        for user, member in users:
            self.db.execute(project_members.insert().values(project_id=project.id, user_id=user.id, assigned_at=now))
            result.synced_members += 1
            if self._is_direct_maintainer_or_owner(member):
                self.db.execute(project_admins.insert().values(project_id=project.id, user_id=user.id, assigned_at=now))
                result.synced_admins += 1
                if not user.is_system_admin() and project_admin_role and project_admin_role not in user.roles:
                    user.roles.append(project_admin_role)
        self.db.commit()

    def _find_or_create_user(
        self,
        member: Dict[str, Any],
        default_password: Optional[str],
        result: GitLabSyncResult,
    ) -> Optional[User]:
        email = (member.get("email") or "").strip()
        username = (member.get("username") or "").strip()
        user = self.db.query(User).filter(User.email == email).first() if email else None
        if not user and username:
            user = self.db.query(User).filter(User.username == username).first()
        if user:
            if not user.email and email:
                user.email = email
            if not user.nickname and member.get("name"):
                user.nickname = member.get("name")
            self.db.commit()
            return user
        if not default_password:
            result.skipped_users += 1
            return None
        base_username = username or f"gitlab-{member.get('id')}"
        final_username = base_username
        if self.db.query(User).filter(User.username == final_username).first():
            final_username = f"{base_username}-{member.get('id')}"
        user = User(
            username=final_username,
            nickname=member.get("name") or final_username,
            email=email or None,
            password_hash=security_service.hash_password(default_password),
            is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        result.created_users += 1
        return user

    def _is_direct_maintainer_or_owner(self, member: Dict[str, Any]) -> bool:
        return (
            (member.get("access_level") or 0) >= MAINTAINER
            and (member.get("source_type") or "project") == "project"
        )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_services/test_gitlab_sync.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/gitlab_client.py app/services/gitlab_sync.py tests/test_services/test_gitlab_sync.py
git commit -m "feat: add gitlab member sync service"
```

---

### Task 9: Integrate Sync Service Into Manual Endpoint And Scheduler

**Files:**
- Modify: `app/api/projects.py`
- Modify: `app/main.py`
- Modify: `app/api/settings.py`
- Test: `tests/test_api/test_projects_sync.py`

- [ ] **Step 1: Update failing projects sync tests**

In `tests/test_api/test_projects_sync.py`, patch service instead of `GitLabClient` for endpoint integration:

```python
from app.services.gitlab_sync import GitLabSyncResult
```

Add:

```python
@patch("app.api.projects.GitLabProjectMemberSyncService")
def test_sync_endpoint_returns_service_result(mock_service_cls, admin_session, db_session):
    add_settings(db_session)
    result = GitLabSyncResult(
        total=2,
        created=1,
        skipped=1,
        failed=0,
        created_users=2,
        synced_members=3,
        synced_admins=1,
    )
    mock_service_cls.return_value.sync.return_value = result

    response = admin_session.post("/api/projects/sync-gitlab")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["created"] == 1
    assert data["data"]["created_users"] == 2
    assert data["data"]["synced_members"] == 3
```

Adjust older endpoint tests to assert the service handles project creation; keep lower-level project creation tests in `tests/test_services/test_gitlab_sync.py`.

- [ ] **Step 2: Run failing tests**

Run: `pytest tests/test_api/test_projects_sync.py -q`

Expected: FAIL because endpoint still performs sync inline.

- [ ] **Step 3: Replace endpoint inline sync**

In `app/api/projects.py`, import:

```python
from app.services.gitlab_sync import GitLabProjectMemberSyncService
```

Replace `sync_gitlab_projects` body after permission dependency with:

```python
@router.post("/sync-gitlab")
async def sync_gitlab_projects(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    try:
        result = GitLabProjectMemberSyncService(db).sync()
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc))
    return ApiResponse(
        success=True,
        message="GitLab 项目及成员同步完成",
        data=result.to_dict(),
    )
```

- [ ] **Step 4: Wire scheduler to service**

In `app/main.py`, ensure `run_gitlab_project_member_sync` catches and logs exceptions:

```python
def run_gitlab_project_member_sync():
    from app.database import SessionLocal
    from app.services.gitlab_sync import GitLabProjectMemberSyncService

    db = SessionLocal()
    try:
        result = GitLabProjectMemberSyncService(db).sync()
        logger.info(f"GitLab 项目及成员同步完成: {result.to_dict()}")
    except Exception as exc:
        logger.exception(f"GitLab 项目及成员同步失败: {exc}")
    finally:
        db.close()
```

In `app/api/settings.py`, `_refresh_scheduler` must remove job id `gitlab_project_member_sync` when settings change and re-add it when enabled.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_api/test_projects_sync.py tests/test_services/test_gitlab_sync.py tests/test_api/test_settings_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/projects.py app/main.py app/api/settings.py tests/test_api/test_projects_sync.py
git commit -m "feat: integrate gitlab sync endpoint and schedule"
```

---

### Task 10: Full Regression And Documentation Check

**Files:**
- Modify only if verification exposes defects.
- Test: full relevant test suite.

- [ ] **Step 1: Run targeted permission suite**

Run:

```bash
pytest tests/test_core/test_permissions.py tests/test_api/test_permissions_visibility.py tests/test_api/test_efficiency.py tests/test_api/test_efficiency_monthly.py tests/test_api/test_projects_sync.py tests/test_services/test_gitlab_sync.py tests/test_api/test_settings_api.py tests/test_templates -q
```

Expected: PASS.

- [ ] **Step 2: Run broader API/model tests**

Run:

```bash
pytest tests/test_api tests/test_models tests/test_services/test_gitlab_client.py tests/test_migration.py -q
```

Expected: PASS.

- [ ] **Step 3: Run application compile check**

Run:

```bash
python -m compileall app tests
```

Expected: no syntax errors.

- [ ] **Step 4: Inspect remaining `project_member` references**

Run:

```bash
rg -n "project_member|项目成员权限|is_project_member" app tests --glob "!app/static/vendor/**"
```

Expected: only compatibility constants or explicit tests proving hidden behavior remain. No API branch should depend on `is_project_member()`.

- [ ] **Step 5: Commit fixes if any**

If Step 1 through Step 4 required code fixes:

```bash
git add app tests
git commit -m "test: cover permission adjustment regression"
```

If no fixes were needed, do not create an empty commit.

---

## Self-Review Checklist

- Spec coverage:
  - Role model and `project_member` retirement covered by Tasks 1, 5, 6, and 10.
  - Project read/write rules covered by Tasks 1 and 2.
  - Efficiency list/detail split covered by Task 3.
  - Report and Webhook ordinary-user filtering covered by Task 4.
  - Settings, default password, and automatic sync schedule covered by Task 7.
  - GitLab members/all mapping, direct vs inherited Maintainer/Owner, no password mutation, per-project protection, and result summary covered by Tasks 8 and 9.
  - Permission management and account management UI updates covered by Task 6.
  - Regression and reference scan covered by Task 10.
- Placeholder scan: no unfinished marker or undefined deferred step is intentionally left.
- Type consistency:
  - Permission helpers use `User`, `Session`, and `Set[int]`.
  - Sync service returns `GitLabSyncResult` and endpoint serializes with `to_dict()`.
  - Settings fields are named `gitlab_sync_enabled`, `gitlab_sync_schedule_time`, `gitlab_sync_default_password` across model, schema, API, and template.

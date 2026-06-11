"""项目管理员报告权限测试

测试项目管理员查看审查报告时的权限控制：
- 自己管理的项目：可查看所有人员报告
- 其他项目：只能查看自己的报告
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.api import reports as reports_api
from app.database import Base, get_db
from app.models.project import Project
from app.models.user import Role, User, project_admins, project_members
from app.security import security_service


@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    eng = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def db_session(engine):
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def app(db_session):
    api = FastAPI()
    api.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    api.include_router(reports_api.router)

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    api.dependency_overrides[get_db] = _override_get_db
    return api


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def report_dir():
    path = Path(tempfile.mkdtemp())
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def setup_projects_and_users(db_session):
    """创建测试项目和用户

    - project_a: 项目管理员管理的项目
    - project_b: 项目管理员不管理的项目
    - admin_user: 项目管理员（管理 project_a）
    - member_user: 普通成员（在两个项目中）
    """
    # 创建项目
    project_a = Project(name="project-a", project_id=100, is_active=True)
    project_b = Project(name="project-b", project_id=200, is_active=True)
    db_session.add_all([project_a, project_b])
    db_session.commit()
    db_session.refresh(project_a)
    db_session.refresh(project_b)

    # 创建项目管理员角色
    admin_role = Role(name="project_admin", description="项目管理员")
    db_session.add(admin_role)
    db_session.commit()

    # 创建用户（使用不包含特殊字符的邮箱，避免文件名清理问题）
    admin_user = User(
        username="admin",
        nickname="Admin",
        email="admin",
        password_hash=security_service.hash_password("pass"),
        is_active=True,
    )
    member_user = User(
        username="member",
        nickname="Member",
        email="member",
        password_hash=security_service.hash_password("pass"),
        is_active=True,
    )
    db_session.add_all([admin_user, member_user])
    db_session.commit()
    db_session.refresh(admin_user)
    db_session.refresh(member_user)

    # 分配角色
    from app.models.user import user_roles
    db_session.execute(
        user_roles.insert().values(user_id=admin_user.id, role_id=admin_role.id)
    )

    # 设置项目管理员：admin_user 管理 project_a
    db_session.execute(
        project_admins.insert().values(
            project_id=project_a.id, user_id=admin_user.id
        )
    )

    # 设置项目成员：两个用户都在两个项目中
    db_session.execute(
        project_members.insert().values(project_id=project_a.id, user_id=admin_user.id)
    )
    db_session.execute(
        project_members.insert().values(project_id=project_a.id, user_id=member_user.id)
    )
    db_session.execute(
        project_members.insert().values(project_id=project_b.id, user_id=admin_user.id)
    )
    db_session.execute(
        project_members.insert().values(project_id=project_b.id, user_id=member_user.id)
    )
    db_session.commit()

    return {
        "project_a": project_a,
        "project_b": project_b,
        "admin_user": admin_user,
        "member_user": member_user,
    }


@pytest.fixture
def login_as_admin(app, setup_projects_and_users):
    """以项目管理员身份登录"""
    admin_user = setup_projects_and_users["admin_user"]
    app.dependency_overrides[reports_api.get_current_user_full] = lambda: admin_user
    app.dependency_overrides[reports_api.get_current_user_obj] = (
        lambda request, db: admin_user
    )
    yield admin_user
    app.dependency_overrides.pop(reports_api.get_current_user_full, None)
    app.dependency_overrides.pop(reports_api.get_current_user_obj, None)


def _sanitize_filename(name: str) -> str:
    """清理文件名，与 reports.py 中的逻辑保持一致"""
    import re
    return re.sub(r'[^\w\-.]', '_', name)


def _write_report(base_dir, project, author, content):
    """写入测试报告文件"""
    report_dir = base_dir / project / "daily" / "2026-06-09"
    report_dir.mkdir(parents=True, exist_ok=True)
    # 使用清理后的文件名，与 API 行为一致
    safe_author = _sanitize_filename(author)
    (report_dir / f"{safe_author}.md").write_text(content, encoding="utf-8")


def test_admin_can_see_all_reports_in_managed_project(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """项目管理员在自己管理的项目中可以看到所有人员的报告"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    # 在 project-a（admin 管理的项目）中写入两个用户的报告
    _write_report(report_dir, "project-a", "admin", "admin report")
    _write_report(report_dir, "project-a", "member", "member report")

    response = client.get("/api/reports")

    assert response.status_code == 200
    data = response.json()["data"]
    authors = {item["author"] for item in data}
    # 应该能看到两个用户的报告
    assert "admin" in authors
    assert "member" in authors


def test_admin_can_only_see_own_reports_in_other_project(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """项目管理员在其他项目中只能看到自己的报告"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    # 在 project-b（admin 不管理的项目）中写入两个用户的报告
    _write_report(report_dir, "project-b", "admin", "admin report")
    _write_report(report_dir, "project-b", "member", "member report")

    response = client.get("/api/reports")

    assert response.status_code == 200
    data = response.json()["data"]
    authors = {item["author"] for item in data}
    # 只能看到自己的报告
    assert "admin" in authors
    assert "member" not in authors


def test_admin_can_read_content_in_managed_project(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """项目管理员可以查看自己管理项目中他人的报告内容"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    _write_report(report_dir, "project-a", "member", "member content")
    path = "project-a/daily/2026-06-09/member.md"

    response = client.get("/api/reports/content", params={"path": path})

    assert response.status_code == 200
    assert response.json()["data"]["content"] == "member content"


def test_content_path_reads_literal_filename_with_spaces(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """path 模式应读取真实相对路径，不能把空格重建成下划线。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    target_dir = report_dir / "project-a" / "daily" / "2026-06-09"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "Zhang Peng.md").write_text(
        "space author content", encoding="utf-8"
    )

    response = client.get(
        "/api/reports/content",
        params={"path": "project-a/daily/2026-06-09/Zhang Peng.md"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["content"] == "space author content"
    assert data["path"] == "project-a/daily/2026-06-09/Zhang Peng.md"


def test_admin_cannot_read_content_in_other_project(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """项目管理员不能查看其他项目中他人的报告内容"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    _write_report(report_dir, "project-b", "member", "member content")
    path = "project-b/daily/2026-06-09/member.md"

    response = client.get("/api/reports/content", params={"path": path})

    assert response.status_code == 403


def test_admin_can_download_in_managed_project(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """项目管理员可以下载自己管理项目中他人的报告"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    _write_report(report_dir, "project-a", "member", "member content")
    path = "project-a/daily/2026-06-09/member.md"

    response = client.get("/api/reports/download", params={"path": path})

    assert response.status_code == 200


def test_admin_cannot_download_in_other_project(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """项目管理员不能下载其他项目中他人的报告"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    _write_report(report_dir, "project-b", "member", "member content")
    path = "project-b/daily/2026-06-09/member.md"

    response = client.get("/api/reports/download", params={"path": path})

    assert response.status_code == 403


def test_admin_sees_mixed_reports_in_list(
    client, report_dir, monkeypatch, setup_projects_and_users, login_as_admin
):
    """项目管理员在列表中看到：管理项目的全部报告 + 其他项目的自己报告"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)

    # project-a: admin 管理的项目
    _write_report(report_dir, "project-a", "admin", "admin in a")
    _write_report(report_dir, "project-a", "member", "member in a")

    # project-b: admin 不管理的项目
    _write_report(report_dir, "project-b", "admin", "admin in b")
    _write_report(report_dir, "project-b", "member", "member in b")

    response = client.get("/api/reports")

    assert response.status_code == 200
    data = response.json()["data"]

    # 按项目分组检查
    reports_by_project = {}
    for item in data:
        proj = item["project"]
        if proj not in reports_by_project:
            reports_by_project[proj] = set()
        reports_by_project[proj].add(item["author"])

    # project-a: 应该看到两个用户的报告
    assert "admin" in reports_by_project.get("project-a", set())
    assert "member" in reports_by_project.get("project-a", set())

    # project-b: 只能看到自己的报告
    assert "admin" in reports_by_project.get("project-b", set())
    assert "member" not in reports_by_project.get("project-b", set())

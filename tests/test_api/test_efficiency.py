"""人员能效 API 测试"""
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.api import reports as reports_api
from app.database import Base, get_db
from app.api.efficiency import router as efficiency_router
from app.api.users import get_current_user_full
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.project import Project
from app.models.user import User, Role, project_admins, project_members
from app.security import security_service


# ──────────────── fixtures ────────────────

@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    eng = create_engine(f"sqlite:///{path}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def db_session(engine):
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def report_dir():
    path = Path(tempfile.mkdtemp(prefix="efficiency_reports_", dir="."))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def app(db_session):
    a = FastAPI()
    a.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    a.include_router(efficiency_router)

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    a.dependency_overrides[get_db] = _override_get_db
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def _ensure_role(db, name):
    role = db.query(Role).filter_by(name=name).first()
    if not role:
        role = Role(name=name, description=name, is_system_role=True)
        db.add(role)
        db.commit()
        db.refresh(role)
    return role


def _make_user(db, username, email, role_names):
    user = User(
        username=username,
        password_hash=security_service.hash_password("pass"),
        email=email,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    for rn in role_names:
        role = _ensure_role(db, rn)
        user.roles.append(role)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_user(db_session):
    return _make_user(db_session, "admin", "admin@x.com", ["system_admin"])


@pytest.fixture
def member_user(db_session):
    """无角色普通用户（project_member 角色已废弃）"""
    return _make_user(db_session, "member", "member@x.com", [])


@pytest.fixture
def login_as_admin(app, admin_user):
    """通过 dependency_overrides 模拟登录系统管理员"""
    app.dependency_overrides[get_current_user_full] = lambda: admin_user
    yield admin_user
    app.dependency_overrides.pop(get_current_user_full, None)


@pytest.fixture
def login_as_member(app, member_user):
    """通过 dependency_overrides 模拟登录项目成员"""
    app.dependency_overrides[get_current_user_full] = lambda: member_user
    yield member_user
    app.dependency_overrides.pop(get_current_user_full, None)


def _seed(db, email, name, d, *, score=80, commits=3, adds=100, dels=20,
          grade="良好", projects=None):
    db.add(EmployeeEfficiencyDaily(
        author_email=email, author_name=name, stat_date=d,
        commits_count=commits, additions=adds, deletions=dels,
        files_changed=5, new_files=0, deleted_files=0,
        projects_involved=json.dumps(projects or ["proj-a"]),
        review_score=score, review_grade=grade,
        review_summary="ok", work_summary=json.dumps(["A", "B"]),
        summary_top_n=5, llm_status="success",
    ))
    db.commit()


def _write_daily_report(base_dir, project, stat_date, filename, content="report"):
    report_dir = Path(base_dir) / project / "daily" / stat_date.isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / filename).write_text(content, encoding="utf-8")


# ──────────────── 用例 ────────────────

def test_list_requires_login(client):
    """未登录返回 401"""
    resp = client.get("/api/efficiency/list")
    assert resp.status_code == 401


def test_list_default_yesterday(client, db_session, login_as_admin):
    """默认查询昨天，返回 items + team_stats"""
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", yesterday, score=85)
    _seed(db_session, "c@d.com", "Carol", yesterday, score=70)
    resp = client.get("/api/efficiency/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert len(data["items"]) == 2
    assert data["team_stats"]["person_count"] == 2


def test_list_sort_by_score(client, db_session, login_as_admin):
    """支持按分数降序排序"""
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", yesterday, score=70)
    _seed(db_session, "b@b.com", "Bob", yesterday, score=95)
    resp = client.get("/api/efficiency/list?sort_by=score&order=desc")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert items[0]["author_email"] == "b@b.com"
    assert items[1]["author_email"] == "a@b.com"


def test_list_date_range(client, db_session, login_as_admin):
    """支持日期区间过滤（区间模式按人员聚合）"""
    d1 = date(2026, 5, 25)
    d2 = date(2026, 5, 26)
    d3 = date(2026, 5, 27)
    _seed(db_session, "a@b.com", "A", d1, commits=3, adds=100, dels=20)
    _seed(db_session, "a@b.com", "A", d2, commits=4, adds=150, dels=30)
    _seed(db_session, "a@b.com", "A", d3, commits=5, adds=200, dels=40)
    resp = client.get(
        "/api/efficiency/list?start_date=2026-05-25&end_date=2026-05-26"
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    # 区间模式：同一人聚合为一行
    assert len(items) == 1
    assert items[0]["commits_count"] == 7   # 3+4
    assert items[0]["additions"] == 250     # 100+150


def test_detail_returns_summary_trend_commits(client, db_session,
                                              login_as_admin):
    """detail 返回 summary + trend + commits 三块"""
    d = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", d, score=85)
    _seed(db_session, "a@b.com", "Alice", d - timedelta(days=1), score=82)
    resp = client.get(
        f"/api/efficiency/detail?email=a@b.com&date={d.isoformat()}"
        f"&trend_days=7"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"]["author_email"] == "a@b.com"
    assert len(data["trend"]) >= 1
    assert "commits" in data


def test_recompute_requires_admin(client, db_session, login_as_member):
    """非系统管理员调用 recompute 应返回 403"""
    today = date.today().isoformat()
    resp = client.post(
        "/api/efficiency/recompute",
        json={"start_date": today, "end_date": today},
    )
    assert resp.status_code == 403


def test_list_normal_user_sees_all(client, db_session, login_as_member,
                                   member_user):
    """无角色普通用户也能看到全员列表（列表全员可见）"""
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, member_user.email, "Self", yesterday)
    _seed(db_session, "other@x.com", "Other", yesterday)
    resp = client.get("/api/efficiency/list")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    emails = [i["author_email"] for i in items]
    assert member_user.email in emails
    assert "other@x.com" in emails  # 列表全员可见


def test_list_project_admin_sees_all_people_not_only_project(
    client, db_session, app
):
    """项目管理员能看到全员能效列表，详情再做权限限制"""
    role = _ensure_role(db_session, "project_admin")
    admin = _make_user(db_session, "pa", "pa@x.com", [])
    admin.roles.append(role)
    project = Project(name="proj-a", project_id=100, is_active=True)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    db_session.execute(
        project_admins.insert().values(project_id=project.id, user_id=admin.id)
    )
    db_session.commit()

    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, "in@x.com", "In", yesterday, projects=["proj-a"])
    _seed(db_session, "out@x.com", "Out", yesterday, projects=["proj-b"])

    app.dependency_overrides[get_current_user_full] = lambda: admin
    resp = client.get("/api/efficiency/list")
    app.dependency_overrides.pop(get_current_user_full, None)

    assert resp.status_code == 200
    emails = {i["author_email"] for i in resp.json()["data"]["items"]}
    assert emails == {"in@x.com", "out@x.com"}


def test_project_admin_detail_uses_project_member_relation(
    client, db_session, app
):
    """项目管理员可打开自己项目成员详情，即使能效记录项目名不匹配"""
    role = _ensure_role(db_session, "project_admin")
    admin = _make_user(db_session, "pa2", "pa2@x.com", [])
    admin.roles.append(role)
    target = _make_user(db_session, "member2", "member2@x.com", [])
    project = Project(name="proj-a", project_id=101, is_active=True)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    db_session.execute(
        project_admins.insert().values(project_id=project.id, user_id=admin.id)
    )
    db_session.execute(
        project_members.insert().values(project_id=project.id, user_id=target.id)
    )
    db_session.commit()
    yesterday = date.today() - timedelta(days=1)
    _seed(
        db_session,
        target.email,
        "Member2",
        yesterday,
        projects=["unrelated-project"],
    )

    app.dependency_overrides[get_current_user_full] = lambda: admin
    resp = client.get(
        f"/api/efficiency/detail?email={target.email}&date={yesterday.isoformat()}"
    )
    app.dependency_overrides.pop(get_current_user_full, None)

    assert resp.status_code == 200
    assert resp.json()["data"]["summary"]["author_email"] == target.email


def test_detail_returns_daily_reports_by_project_and_author_name(
    client, db_session, login_as_admin, report_dir, monkeypatch
):
    """系统管理员能看到按项目区分的日报入口，filename 使用相对路径语义。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)
    d = date(2026, 6, 10)
    db_session.add_all([
        Project(name="project-a", project_id=201, is_active=True),
        Project(name="project-b", project_id=202, is_active=True),
    ])
    db_session.commit()
    _seed(
        db_session,
        "alice@example.com",
        "Alice",
        d,
        projects=["project-a", "project-b"],
    )
    _write_daily_report(report_dir, "project-a", d, "Alice.md", "a report")
    _write_daily_report(report_dir, "project-b", d, "alice.md", "b report")

    resp = client.get(
        "/api/efficiency/detail",
        params={"email": "alice@example.com", "date": d.isoformat()},
    )

    assert resp.status_code == 200
    reports = resp.json()["data"]["daily_reports"]
    assert [item["project"] for item in reports] == ["project-a", "project-b"]
    assert reports[0]["author"] == "Alice"
    assert reports[0]["filename"] == "project-a/daily/2026-06-10/Alice.md"
    assert reports[1]["filename"] == "project-b/daily/2026-06-10/alice.md"


def test_detail_daily_reports_empty_when_summary_missing(
    client, login_as_admin, report_dir, monkeypatch
):
    """没有 summary 时仍返回 daily_reports 空数组，避免前端判断缺字段。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)
    d = date(2026, 6, 10)

    resp = client.get(
        "/api/efficiency/detail",
        params={"email": "missing@example.com", "date": d.isoformat()},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"] is None
    assert data["daily_reports"] == []


def test_detail_daily_reports_match_full_email_but_not_email_prefix(
    client, db_session, login_as_admin, report_dir, monkeypatch
):
    """邮箱完整值可匹配，邮箱前缀不能匹配，避免误关联。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)
    d = date(2026, 6, 10)
    db_session.add(Project(name="project-a", project_id=203, is_active=True))
    db_session.commit()
    _seed(
        db_session,
        "zhang@example.com",
        "Display Name",
        d,
        projects=["project-a"],
    )
    _write_daily_report(
        report_dir, "project-a", d, "zhang@example.com.md", "email report"
    )
    _write_daily_report(report_dir, "project-a", d, "zhang.md", "prefix report")

    resp = client.get(
        "/api/efficiency/detail",
        params={"email": "zhang@example.com", "date": d.isoformat()},
    )

    assert resp.status_code == 200
    reports = resp.json()["data"]["daily_reports"]
    assert len(reports) == 1
    assert reports[0]["author"] == "zhang@example.com"
    assert reports[0]["filename"] == (
        "project-a/daily/2026-06-10/zhang@example.com.md"
    )


def test_detail_daily_reports_hidden_when_content_would_forbid_self_identity(
    client, db_session, app, member_user, report_dir, monkeypatch
):
    """普通用户账号身份不匹配报告 stem 时，不展示点开会 403 的日报入口。"""
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)
    d = date(2026, 6, 10)
    project = Project(name="project-a", project_id=204, is_active=True)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    db_session.execute(
        project_members.insert().values(project_id=project.id, user_id=member_user.id)
    )
    db_session.commit()
    _seed(db_session, member_user.email, "Zhang Peng", d, projects=["project-a"])
    _write_daily_report(report_dir, "project-a", d, "Zhang Peng.md", "self mismatch")

    app.dependency_overrides[get_current_user_full] = lambda: member_user
    resp = client.get(
        "/api/efficiency/detail",
        params={"email": member_user.email, "date": d.isoformat()},
    )
    app.dependency_overrides.pop(get_current_user_full, None)

    assert resp.status_code == 200
    assert resp.json()["data"]["daily_reports"] == []


# ── 区间聚合测试 ──────────────────────────

def test_list_range_aggregation(client, db_session, login_as_admin):
    """区间查询时按人员聚合（同一人多天合并为一行）"""
    d1 = date.today() - timedelta(days=3)
    d2 = date.today() - timedelta(days=2)
    d3 = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", d1, score=80, commits=3, adds=100, dels=20)
    _seed(db_session, "a@b.com", "Alice", d2, score=90, commits=5, adds=200, dels=50)
    _seed(db_session, "a@b.com", "Alice", d3, score=70, commits=2, adds=50, dels=10)

    resp = client.get(
        f"/api/efficiency/list?start_date={d1.isoformat()}"
        f"&end_date={d3.isoformat()}"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # 同一人聚合为一行
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["author_email"] == "a@b.com"
    assert item["commits_count"] == 10       # 3+5+2
    assert item["additions"] == 350          # 100+200+50
    assert item["deletions"] == 80           # 20+50+10
    # review_score 取算术平均
    assert item["review_score"] == 80        # (80+90+70)/3 = 80
    # team_stats 也基于聚合后的数据
    assert data["team_stats"]["person_count"] == 1
    assert data["team_stats"]["total_commits"] == 10


def test_list_range_single_day_no_aggregation(client, db_session, login_as_admin):
    """单日查询不聚合（保持现有行为）"""
    d = date.today() - timedelta(days=1)
    _seed(db_session, "a@b.com", "Alice", d, score=80, commits=3, adds=100, dels=20)

    resp = client.get(f"/api/efficiency/list?date={d.isoformat()}")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["commits_count"] == 3

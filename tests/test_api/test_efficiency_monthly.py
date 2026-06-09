"""月度能效 API 测试"""
import json
import os
import tempfile
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.database import Base, get_db
from app.api.efficiency import router as efficiency_router
from app.api.users import get_current_user_full
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
from app.models.project import Project
from app.models.user import User, Role, project_admins
from app.security import security_service


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
def login_as_admin(app, admin_user):
    app.dependency_overrides[get_current_user_full] = lambda: admin_user
    yield admin_user
    app.dependency_overrides.pop(get_current_user_full, None)


def _seed_monthly(db, email, name, ym, *, score=80, commits=50,
                   adds=1000, dels=200, active_days=20, projects=None):
    db.add(EmployeeEfficiencyMonthly(
        author_email=email, author_name=name, year_month=ym,
        commits_count=commits, additions=adds, deletions=dels,
        files_changed=30, new_files=5, deleted_files=2,
        active_days=active_days,
        projects_involved=json.dumps(projects or ["proj-a"]),
        review_score=score, review_grade="良好",
        review_summary="月度总结", work_summary=json.dumps(["A", "B"]),
        summary_top_n=10, llm_status="success",
    ))
    db.commit()


def _seed_daily(db, email, name, d, *, score=80, commits=3, adds=100, dels=20):
    db.add(EmployeeEfficiencyDaily(
        author_email=email, author_name=name, stat_date=d,
        commits_count=commits, additions=adds, deletions=dels,
        files_changed=5, new_files=0, deleted_files=0,
        projects_involved=json.dumps(["proj-a"]),
        review_score=score, review_grade="良好",
        review_summary="ok", work_summary=json.dumps(["A"]),
        summary_top_n=5, llm_status="success",
    ))
    db.commit()


# ── /monthly/list 测试 ──────────────────────

def test_monthly_list_requires_login(client):
    resp = client.get("/api/efficiency/monthly/list")
    assert resp.status_code == 401


def test_monthly_list_returns_items(client, db_session, login_as_admin):
    _seed_monthly(db_session, "a@b.com", "Alice", "2026-05", score=85)
    _seed_monthly(db_session, "c@d.com", "Carol", "2026-05", score=70)

    resp = client.get("/api/efficiency/monthly/list?year_month=2026-05")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert len(data["items"]) == 2
    assert data["team_stats"]["person_count"] == 2


def test_monthly_list_sort_by_score(client, db_session, login_as_admin):
    _seed_monthly(db_session, "a@b.com", "Alice", "2026-05", score=70)
    _seed_monthly(db_session, "b@b.com", "Bob", "2026-05", score=95)

    resp = client.get(
        "/api/efficiency/monthly/list?year_month=2026-05"
        "&sort_by=score&order=desc"
    )
    items = resp.json()["data"]["items"]
    assert items[0]["author_email"] == "b@b.com"
    assert items[1]["author_email"] == "a@b.com"


def test_monthly_list_project_admin_sees_all_people_not_only_project(
    client, db_session, app
):
    """项目管理员月度列表也应全员可见"""
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

    _seed_monthly(
        db_session, "in@x.com", "In", "2026-05", score=85,
        projects=["proj-a"],
    )
    _seed_monthly(
        db_session, "out@x.com", "Out", "2026-05", score=70,
        projects=["proj-b"],
    )

    app.dependency_overrides[get_current_user_full] = lambda: admin
    resp = client.get("/api/efficiency/monthly/list?year_month=2026-05")
    app.dependency_overrides.pop(get_current_user_full, None)

    assert resp.status_code == 200
    emails = {item["author_email"] for item in resp.json()["data"]["items"]}
    assert emails == {"in@x.com", "out@x.com"}


# ── /monthly/detail 测试 ──────────────────────

def test_monthly_detail_returns_summary_and_trend(
    client, db_session, login_as_admin
):
    _seed_monthly(db_session, "a@b.com", "Alice", "2026-05", score=85)
    _seed_daily(db_session, "a@b.com", "Alice", date(2026, 5, 1), score=80)
    _seed_daily(db_session, "a@b.com", "Alice", date(2026, 5, 2), score=90)

    resp = client.get(
        "/api/efficiency/monthly/detail?email=a@b.com&year_month=2026-05"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"]["author_email"] == "a@b.com"
    assert data["summary"]["review_score"] == 85
    assert len(data["daily_trend"]) == 2


def test_monthly_detail_not_found(client, db_session, login_as_admin):
    resp = client.get(
        "/api/efficiency/monthly/detail?email=nobody@x.com&year_month=2026-05"
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["summary"] is None


def test_monthly_detail_permission_member_sees_only_self(
    client, db_session, app
):
    """project_member 只能查看自己的月度详情"""
    member = _make_user(db_session, "member1", "m1@x.com", ["project_member"])
    _seed_monthly(db_session, "other@x.com", "Other", "2026-05")
    _seed_monthly(db_session, "m1@x.com", "Member1", "2026-05")

    app.dependency_overrides[get_current_user_full] = lambda: member
    resp = client.get(
        "/api/efficiency/monthly/detail?email=other@x.com&year_month=2026-05"
    )
    assert resp.status_code == 403


def test_monthly_list_invalid_year_month(client, login_as_admin):
    """无效 year_month 格式返回 400"""
    resp = client.get("/api/efficiency/monthly/list?year_month=2026/05")
    assert resp.status_code == 400


def test_monthly_detail_invalid_year_month(client, login_as_admin):
    """无效 year_month 格式返回 400"""
    resp = client.get(
        "/api/efficiency/monthly/detail?email=a@b.com&year_month=abc"
    )
    assert resp.status_code == 400


# ── /monthly/recompute 测试 ──────────────────────

def test_monthly_recompute_requires_admin(client, db_session, app):
    """非管理员调用月度补算应返回 403"""
    from app.models.user import User, Role

    role = db_session.query(Role).filter_by(name="project_member").first()
    if not role:
        role = Role(name="project_member", description="m", is_system_role=True)
        db_session.add(role)
        db_session.commit()

    user = User(
        username="member", password_hash=security_service.hash_password("pass"),
        email="member@x.com", is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    user.roles.append(role)
    db_session.commit()

    app.dependency_overrides[get_current_user_full] = lambda: user
    resp = client.post(
        "/api/efficiency/monthly/recompute",
        json={"year_month": "2026-05"},
    )
    assert resp.status_code == 403
    app.dependency_overrides.pop(get_current_user_full, None)

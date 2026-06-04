"""人员能效 API 测试"""
import json
import os
import tempfile
from datetime import date, datetime, timedelta

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
from app.models.user import User, Role
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
    return _make_user(db_session, "member", "member@x.com", ["project_member"])


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


def test_list_member_sees_only_self(client, db_session, login_as_member,
                                    member_user):
    """项目成员只能看到与自己 email 一致的数据"""
    yesterday = date.today() - timedelta(days=1)
    _seed(db_session, member_user.email, "Self", yesterday)
    _seed(db_session, "other@x.com", "Other", yesterday)
    resp = client.get("/api/efficiency/list")
    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    emails = [i["author_email"] for i in items]
    assert member_user.email in emails
    assert "other@x.com" not in emails


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

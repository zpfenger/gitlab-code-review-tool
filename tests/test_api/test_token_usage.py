import os
import tempfile
from datetime import date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.api.deps import get_current_user_full
from app.api.token_usage import router as token_usage_router
from app.database import Base, get_db
from app.models.token_usage import TokenUsageLog
from app.models.user import Role, User
from app.security import security_service


@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(suffix=".db", dir=".")
    eng = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
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
    api.include_router(token_usage_router)

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


def _role(db, name):
    role = db.query(Role).filter_by(name=name).first()
    if not role:
        role = Role(name=name, description=name, is_system_role=True)
        db.add(role)
        db.commit()
        db.refresh(role)
    return role


def _user(db, username, roles):
    user = User(
        username=username,
        password_hash=security_service.hash_password("pass"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    for role_name in roles:
        user.roles.append(_role(db, role_name))
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin(app, db_session):
    user = _user(db_session, "admin", ["system_admin"])
    app.dependency_overrides[get_current_user_full] = lambda: user
    yield user
    app.dependency_overrides.pop(get_current_user_full, None)


@pytest.fixture
def normal_user(app, db_session):
    user = _user(db_session, "normal", [])
    app.dependency_overrides[get_current_user_full] = lambda: user
    yield user
    app.dependency_overrides.pop(get_current_user_full, None)


def _ts(year, month, day, hour=10):
    return int(datetime(year, month, day, hour, 0, 0).timestamp())


def _seed_usage(
    db,
    *,
    biz_type="report",
    biz_id=1,
    project_name="project-a",
    author="Alice",
    model="gpt-4",
    prompt=10,
    completion=5,
    total=15,
    created_at_ts=None,
):
    row = TokenUsageLog(
        biz_type=biz_type,
        biz_id=biz_id,
        project_name=project_name,
        author=author,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        created_at_ts=created_at_ts or _ts(2026, 6, 10),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_list_requires_system_admin(client, db_session, normal_user):
    _seed_usage(db_session)

    response = client.get("/api/token-usage")

    assert response.status_code == 403


def test_list_token_usage_filters_and_paginates(client, db_session, admin):
    match = _seed_usage(
        db_session,
        biz_type="report",
        project_name="project-a",
        model="gpt-4",
        created_at_ts=_ts(2026, 6, 10),
    )
    _seed_usage(
        db_session,
        biz_type="webhook_mr",
        project_name="project-a",
        model="gpt-4",
        created_at_ts=_ts(2026, 6, 10),
    )
    _seed_usage(
        db_session,
        biz_type="report",
        project_name="project-b",
        model="gpt-4",
        created_at_ts=_ts(2026, 6, 10),
    )

    response = client.get(
        "/api/token-usage",
        params={
            "biz_type": "report",
            "project_name": "project-a",
            "start_date": "2026-06-10",
            "end_date": "2026-06-10",
            "page": 1,
            "page_size": 10,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["id"] == match.id
    assert item["biz_type"] == "report"
    assert item["project_name"] == "project-a"
    assert item["prompt_tokens"] == 10
    assert item["completion_tokens"] == 5
    assert item["total_tokens"] == 15


def test_stats_returns_summary_today_month_and_groups(client, db_session, admin):
    today = date.today()
    yesterday = today - timedelta(days=1)
    today_ts = int(datetime.combine(today, datetime.min.time()).timestamp())
    yesterday_ts = int(datetime.combine(yesterday, datetime.min.time()).timestamp())

    _seed_usage(
        db_session,
        biz_type="report",
        model="gpt-4",
        prompt=100,
        completion=50,
        total=150,
        created_at_ts=today_ts,
    )
    _seed_usage(
        db_session,
        biz_type="webhook_mr",
        model="deepseek-chat",
        prompt=30,
        completion=20,
        total=50,
        created_at_ts=yesterday_ts,
    )

    response = client.get("/api/token-usage/stats")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"]["total_tokens"] == 200
    assert data["summary"]["prompt_tokens"] == 130
    assert data["summary"]["completion_tokens"] == 70
    assert data["today"]["total_tokens"] == 150
    assert data["month"]["total_tokens"] == 200
    assert {row["biz_type"] for row in data["by_biz_type"]} == {
        "report",
        "webhook_mr",
    }
    assert {row["model"] for row in data["by_model"]} == {
        "gpt-4",
        "deepseek-chat",
    }
    assert any(row["date"] == today.isoformat() for row in data["daily_trend"])

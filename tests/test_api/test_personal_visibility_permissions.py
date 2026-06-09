"""普通用户个人数据可见性权限测试"""
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
from app.api import webhook_reviews as webhook_reviews_api
from app.database import Base, get_db
from app.models.project import Project
from app.models.user import User, project_members
from app.models.webhook_review import MrReviewLog
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
    api.include_router(webhook_reviews_api.router)

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
def project_with_user(db_session):
    project = Project(name="proj-a", project_id=100, is_active=True)
    user = User(
        username="alice",
        nickname="Alice",
        email="alice@example.com",
        password_hash=security_service.hash_password("pass"),
        is_active=True,
    )
    db_session.add_all([project, user])
    db_session.commit()
    db_session.refresh(project)
    db_session.refresh(user)
    db_session.execute(
        project_members.insert().values(project_id=project.id, user_id=user.id)
    )
    db_session.commit()
    return project, user


@pytest.fixture
def login_as_plain_user(app, project_with_user):
    _, user = project_with_user
    app.dependency_overrides[reports_api.get_current_user_full] = lambda: user
    app.dependency_overrides[reports_api.get_current_user_obj] = (
        lambda request, db: user
    )
    app.dependency_overrides[webhook_reviews_api.get_current_user_full] = (
        lambda: user
    )
    yield user
    app.dependency_overrides.pop(reports_api.get_current_user_full, None)
    app.dependency_overrides.pop(reports_api.get_current_user_obj, None)
    app.dependency_overrides.pop(webhook_reviews_api.get_current_user_full, None)


def _write_report(base_dir, project, author, content):
    report_dir = base_dir / project / "daily" / "2026-06-09"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{author}.md").write_text(content, encoding="utf-8")


def test_plain_user_report_list_only_includes_self(
    client, report_dir, monkeypatch, project_with_user, login_as_plain_user
):
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)
    _write_report(report_dir, "proj-a", "alice@example.com", "self")
    _write_report(report_dir, "proj-a", "other@example.com", "other")

    response = client.get("/api/reports")

    assert response.status_code == 200
    authors = {item["author"] for item in response.json()["data"]}
    assert authors == {"alice@example.com"}


def test_plain_user_cannot_read_or_download_other_report(
    client, report_dir, monkeypatch, project_with_user, login_as_plain_user
):
    monkeypatch.setattr(reports_api, "ALLOWED_REPORT_DIR", report_dir)
    _write_report(report_dir, "proj-a", "other@example.com", "other")
    path = "proj-a/daily/2026-06-09/other@example.com.md"

    content_response = client.get("/api/reports/content", params={"path": path})
    download_response = client.get("/api/reports/download", params={"path": path})

    assert content_response.status_code == 403
    assert download_response.status_code == 403


def test_plain_user_webhook_list_stats_and_detail_only_include_self(
    client, db_session, project_with_user, login_as_plain_user
):
    project, _ = project_with_user
    self_log = MrReviewLog(
        project_name=project.name,
        author="alice@example.com",
        source_branch="feature-self",
        target_branch="main",
        updated_at=1717900000,
        score=90,
        review_result="self",
        additions=10,
        deletions=1,
        last_commit_id="self-sha",
    )
    other_log = MrReviewLog(
        project_name=project.name,
        author="other@example.com",
        source_branch="feature-other",
        target_branch="main",
        updated_at=1717900100,
        score=60,
        review_result="other",
        additions=20,
        deletions=2,
        last_commit_id="other-sha",
    )
    db_session.add_all([self_log, other_log])
    db_session.commit()
    db_session.refresh(other_log)

    list_response = client.get("/api/webhook-reviews?review_type=mr")
    stats_response = client.get("/api/webhook-reviews/stats?review_type=mr")
    detail_response = client.get(
        f"/api/webhook-reviews/{other_log.id}?review_type=mr"
    )

    assert list_response.status_code == 200
    items = list_response.json()["data"]["items"]
    assert [item["author"] for item in items] == ["alice@example.com"]

    assert stats_response.status_code == 200
    by_author = stats_response.json()["data"]["by_author"]
    assert [item["author"] for item in by_author] == ["alice@example.com"]

    assert detail_response.status_code == 403

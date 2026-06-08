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

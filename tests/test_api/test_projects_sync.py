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
from app.models.user import Role, User, project_admins, project_members
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
    project_admin_role = Role(name="project_admin", description="Project Administrator", is_system_role=True)
    db_session.add_all([admin_role, project_admin_role])
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
        gitlab_sync_default_password=security_service.encrypt("sync-default-pass"),
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
        assert "未配置" in data["message"]

    def test_sync_requires_gitlab_url(self, admin_session, db_session):
        add_settings(db_session, gitlab_url="")

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "未配置" in data["message"]

    def test_sync_requires_gitlab_token(self, admin_session, db_session):
        add_settings(db_session, token=None)

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "未配置" in data["message"]

    @patch("app.services.gitlab_sync_service.GitLabClient")
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
        mock_client_cls.return_value.get_project_members.return_value = [
            {"id": 1, "username": "dev1", "name": "Dev One", "email": "dev1@example.com", "access_level": 30, "source": "project"},
        ]

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["created"] == 1
        assert data["data"]["skipped"] == 1
        assert data["data"]["failed_projects"] == 0
        assert data["data"]["total"] == 2

        created = db_session.query(Project).filter(Project.project_id == 101).one()
        assert created.name == "New Project"
        assert created.is_active is True

        db_session.refresh(existing)
        assert existing.name == "Existing"
        assert existing.is_active is False

    @patch("app.services.gitlab_sync_service.GitLabClient")
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
        mock_client_cls.return_value.get_project_members.return_value = [
            {"id": 1, "username": "dev1", "name": "Dev One", "email": "dev1@example.com", "access_level": 30, "source": "project"},
        ]

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["created"] == 1

        created = db_session.query(Project).filter(Project.project_id == 202).one()
        assert created.name == "group service-api"
        assert created.is_active is True

    @patch("app.services.gitlab_sync_service.GitLabClient")
    def test_sync_promotes_maintainer_even_when_source_is_unknown(
        self, mock_client_cls, admin_session, db_session
    ):
        """Maintainer/Owner 无论来源（直接或继承）都应被赋予项目管理员权限。"""
        add_settings(db_session)
        project = Project(name="Service", project_id=300, is_active=True)
        db_session.add(project)
        db_session.commit()

        mock_client_cls.return_value.list_accessible_projects.return_value = [
            {
                "id": 300,
                "name": "Service",
                "path_with_namespace": "group/service",
                "description": "",
                "web_url": "https://gitlab.example.com/group/service",
                "default_branch": "main",
            }
        ]
        mock_client_cls.return_value.get_project_members.return_value = [
            {
                "id": 2,
                "username": "maint",
                "name": "Maintainer",
                "email": "maint@example.com",
                "access_level": 40,
            },
        ]

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        assert response.json()["success"] is True
        user = db_session.query(User).filter(User.email == "maint@example.com").one()
        admin_row = db_session.execute(
            project_admins.select().where(
                project_admins.c.project_id == project.id,
                project_admins.c.user_id == user.id,
            )
        ).fetchone()
        assert admin_row is not None
        assert "project_admin" in {role.name for role in user.roles}

    @patch("app.services.gitlab_sync_service.GitLabClient")
    def test_sync_does_not_duplicate_preserved_system_admin_relation(
        self, mock_client_cls, admin_session, db_session, admin_user
    ):
        add_settings(db_session)
        project = Project(name="Admin Project", project_id=301, is_active=True)
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)
        admin_user.email = "admin@example.com"
        db_session.execute(
            project_admins.insert().values(
                project_id=project.id,
                user_id=admin_user.id,
            )
        )
        db_session.commit()

        mock_client_cls.return_value.list_accessible_projects.return_value = [
            {
                "id": 301,
                "name": "Admin Project",
                "path_with_namespace": "group/admin-project",
                "description": "",
                "web_url": "https://gitlab.example.com/group/admin-project",
                "default_branch": "main",
            }
        ]
        mock_client_cls.return_value.get_project_members.return_value = [
            {
                "id": 1,
                "username": admin_user.username,
                "name": "Admin",
                "email": admin_user.email,
                "access_level": 40,
                "source": "project",
            },
        ]

        response = admin_session.post("/api/projects/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["failed_projects"] == 0
        rows = db_session.execute(
            project_admins.select().where(
                project_admins.c.project_id == project.id,
                project_admins.c.user_id == admin_user.id,
            )
        ).fetchall()
        assert len(rows) == 1


class TestSyncGitLabAccounts:
    def test_account_sync_requires_system_admin(self, client, normal_user, db_session):
        client.post("/api/auth/login", json={"username": "user", "password": "user123"})
        add_settings(db_session)

        response = client.post("/api/users/sync-gitlab")

        assert response.status_code == 403

    @patch("app.services.gitlab_sync_service.GitLabClient")
    def test_account_sync_creates_missing_gitlab_users(
        self, mock_client_cls, admin_session, db_session
    ):
        add_settings(db_session)
        mock_client_cls.return_value.list_accessible_projects.return_value = [
            {
                "id": 401,
                "name": "Account Sync Project",
                "path_with_namespace": "group/account-sync",
                "description": "",
                "web_url": "https://gitlab.example.com/group/account-sync",
                "default_branch": "main",
            }
        ]
        mock_client_cls.return_value.get_project_members.return_value = [
            {
                "id": 21,
                "username": "syncuser",
                "name": "Sync User",
                "email": "syncuser@example.com",
                "access_level": 30,
                "source": "project",
            },
        ]

        response = admin_session.post("/api/users/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["created_users"] == 1
        assert data["data"]["synced_members"] == 1

        user = db_session.query(User).filter(User.username == "syncuser").one()
        assert user.email == "syncuser@example.com"
        assert user.nickname == "Sync User"
        assert security_service.verify_password("sync-default-pass", user.password_hash)

        project = db_session.query(Project).filter(Project.project_id == 401).one()
        member_row = db_session.execute(
            project_members.select().where(
                project_members.c.project_id == project.id,
                project_members.c.user_id == user.id,
            )
        ).fetchone()
        assert member_row is not None

    @patch("app.services.gitlab_sync_service.GitLabClient")
    def test_account_sync_skips_disabled_and_bot_users(
        self, mock_client_cls, admin_session, db_session
    ):
        add_settings(db_session)
        mock_client_cls.return_value.list_accessible_projects.return_value = [
            {
                "id": 402,
                "name": "Filtered Account Sync Project",
                "path_with_namespace": "group/filtered-account-sync",
                "description": "",
                "web_url": "https://gitlab.example.com/group/filtered-account-sync",
                "default_branch": "main",
            }
        ]
        mock_client_cls.return_value.get_project_members.return_value = [
            {
                "id": 31,
                "username": "activeuser",
                "name": "Active User",
                "email": "activeuser@example.com",
                "access_level": 30,
                "source": "project",
                "state": "active",
                "bot": False,
                "user_type": "human",
            },
            {
                "id": 32,
                "username": "blockeduser",
                "name": "Blocked User",
                "email": "blockeduser@example.com",
                "access_level": 30,
                "source": "project",
                "state": "blocked",
                "bot": False,
                "user_type": "human",
            },
            {
                "id": 33,
                "username": "project_402_bot",
                "name": "Project Bot",
                "email": "project_402_bot@example.com",
                "access_level": 30,
                "source": "project",
                "state": "active",
                "bot": True,
                "user_type": "project_bot",
            },
        ]

        response = admin_session.post("/api/users/sync-gitlab")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["created_users"] == 1
        assert data["data"]["skipped_users"] == 2
        assert data["data"]["synced_members"] == 1

        assert db_session.query(User).filter(User.username == "activeuser").one()
        assert db_session.query(User).filter(User.username == "blockeduser").first() is None
        assert db_session.query(User).filter(User.username == "project_402_bot").first() is None

        project = db_session.query(Project).filter(Project.project_id == 402).one()
        member_rows = db_session.execute(
            project_members.select().where(project_members.c.project_id == project.id)
        ).fetchall()
        assert len(member_rows) == 1

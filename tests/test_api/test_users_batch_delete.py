from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models.project import Project
from app.models.user import Role, User, project_members
from app.security import security_service


def _make_user(db_session, username, password="user123", roles=None):
    user = User(
        username=username,
        password_hash=security_service.hash_password(password),
        is_active=True,
    )
    if roles:
        user.roles.extend(roles)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_role(db_session, name):
    role = Role(name=name, description=name, is_system_role=True)
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    return role


def test_batch_delete_requires_system_admin(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        normal_user = _make_user(db_session, "normal", password="normal123")
        target_user = _make_user(db_session, "target")

        with TestClient(app) as client:
            client.post("/api/auth/login", json={"username": normal_user.username, "password": "normal123"})
            response = client.post("/api/users/batch-delete", json={"user_ids": [target_user.id]})

        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_batch_delete_deletes_selected_users_and_skips_protected_accounts(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        admin_role = _make_role(db_session, Role.SYSTEM_ADMIN)
        admin_user = _make_user(db_session, "admin", password="admin123", roles=[admin_role])
        other_admin = _make_user(db_session, "other_admin", roles=[admin_role])
        synced_user_a = _make_user(db_session, "synced_a")
        synced_user_b = _make_user(db_session, "synced_b")

        project = Project(name="Batch Delete Project", project_id=9001, is_active=True)
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)
        db_session.execute(
            project_members.insert().values(project_id=project.id, user_id=synced_user_a.id)
        )
        db_session.commit()

        with TestClient(app) as client:
            client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
            response = client.post(
                "/api/users/batch-delete",
                json={
                    "user_ids": [
                        synced_user_a.id,
                        synced_user_b.id,
                        admin_user.id,
                        other_admin.id,
                        999999,
                    ]
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted_count"] == 2
        assert data["skipped_count"] == 3
        assert {item["id"] for item in data["deleted"]} == {synced_user_a.id, synced_user_b.id}
        skipped_reasons = {item["id"]: item["reason"] for item in data["skipped"]}
        assert skipped_reasons[admin_user.id] == "不能删除自己的账号"
        assert skipped_reasons[other_admin.id] == "不能删除系统管理员账号"
        assert skipped_reasons[999999] == "用户不存在"

        assert db_session.query(User).filter(User.username == "synced_a").first() is None
        assert db_session.query(User).filter(User.username == "synced_b").first() is None
        assert db_session.query(User).filter(User.username == "admin").one()
        assert db_session.query(User).filter(User.username == "other_admin").one()
        member_row = db_session.execute(
            project_members.select().where(project_members.c.user_id == synced_user_a.id)
        ).fetchone()
        assert member_row is None
    finally:
        app.dependency_overrides.clear()


def test_batch_delete_deletes_project_admin_role_user(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        admin_role = _make_role(db_session, Role.SYSTEM_ADMIN)
        project_admin_role = _make_role(db_session, Role.PROJECT_ADMIN)
        _make_user(db_session, "admin", password="admin123", roles=[admin_role])
        target = _make_user(db_session, "synced_maintainer", roles=[project_admin_role])

        with TestClient(app) as client:
            client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
            response = client.post(
                "/api/users/batch-delete",
                json={"user_ids": [target.id]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted_count"] == 1
        assert db_session.query(User).filter(User.username == "synced_maintainer").first() is None
    finally:
        app.dependency_overrides.clear()

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import tempfile
import os

from app.main import app
from app.database import Base, get_db
from app.models.settings import Settings
from app.models.user import User, Role
from app.security import security_service


@pytest.fixture
def db_engine():
    """创建临时数据库引擎"""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    engine = create_engine(f'sqlite:///{db_path}')
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def db_session(db_engine):
    """创建数据库会话"""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    """创建测试客户端"""
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
    """创建管理员用户（带 system_admin 角色）"""
    # 创建角色
    admin_role = Role(name='system_admin', description='System Administrator', is_system_role=True)
    db_session.add(admin_role)
    db_session.commit()

    # 创建用户
    user = User(
        username="admin",
        password_hash=security_service.hash_password("admin123"),
    )
    user.roles.append(admin_role)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def normal_user(db_session):
    """创建普通用户"""
    user = User(
        username="user",
        password_hash=security_service.hash_password("user123"),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def admin_session(client, admin_user):
    """登录管理员并返回客户端（已保持 session）"""
    response = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "admin123"
    })
    assert response.status_code == 200
    return client


@pytest.fixture
def settings_with_api_key(db_session):
    """创建带有 external_api_key 的设置"""
    settings = Settings(
        global_gitlab_url="https://gitlab.example.com",
        llm_api_url="https://api.example.com/v1",
        llm_model="gpt-4",
        report_output_dir="./data/reports",
        external_api_key=security_service.encrypt("sk-test-api-key-12345678")
    )
    db_session.add(settings)
    db_session.commit()
    db_session.refresh(settings)
    return settings


@pytest.fixture
def settings_without_api_key(db_session):
    """创建没有 external_api_key 的设置"""
    settings = Settings(
        global_gitlab_url="https://gitlab.example.com",
        llm_api_url="https://api.example.com/v1",
        llm_model="gpt-4",
        report_output_dir="./data/reports"
    )
    db_session.add(settings)
    db_session.commit()
    db_session.refresh(settings)
    return settings


class TestGetSettingsMasking:
    """测试 GET /api/settings 的 API Key 脱敏"""

    def test_get_settings_masks_api_key(self, admin_session, settings_with_api_key):
        """测试 GET 返回时 external_api_key 已脱敏"""
        response = admin_session.get("/api/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        # 应该只显示最后 4 位
        assert data["data"]["external_api_key"] == "****5678"

    def test_get_settings_no_api_key(self, admin_session, settings_without_api_key):
        """测试没有 API Key 时返回 null"""
        response = admin_session.get("/api/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["external_api_key"] is None

    def test_get_settings_short_api_key(self, admin_session, db_session):
        """测试短 API Key 的脱敏"""
        settings = Settings(
            global_gitlab_url="https://gitlab.example.com",
            llm_api_url="https://api.example.com/v1",
            llm_model="gpt-4",
            report_output_dir="./data/reports",
            external_api_key=security_service.encrypt("abc")
        )
        db_session.add(settings)
        db_session.commit()

        response = admin_session.get("/api/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["external_api_key"] == "****"


class TestRevealExternalApiKey:
    """测试读取外部接口 API Key 明文"""

    def test_reveal_external_api_key_success(self, admin_session, settings_with_api_key):
        """管理员可以读取已配置的 API Key 明文用于查看和复制"""
        response = admin_session.get("/api/settings/external-api-key")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["api_key"] == "sk-test-api-key-12345678"


class TestRegenerateApiKey:
    """测试 POST /api/settings/regenerate-api-key"""

    def test_regenerate_api_key_success(self, admin_session, settings_with_api_key):
        """测试成功重新生成 API Key"""
        response = admin_session.post("/api/settings/regenerate-api-key")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "api_key" in data["data"]
        assert data["data"]["api_key"].startswith("sk-")
        assert len(data["data"]["api_key"]) > 10

    def test_regenerate_api_key_stores_encrypted(self, admin_session, settings_with_api_key, db_session):
        """测试重新生成的 API Key 已加密存储"""
        response = admin_session.post("/api/settings/regenerate-api-key")
        new_key = response.json()["data"]["api_key"]

        # 从数据库读取并验证
        db_session.refresh(settings_with_api_key)
        stored_encrypted = settings_with_api_key.external_api_key
        decrypted = security_service.decrypt(stored_encrypted)
        assert decrypted == new_key

    def test_regenerate_api_key_different_each_time(self, admin_session, settings_with_api_key):
        """测试每次生成的 API Key 都不同"""
        response1 = admin_session.post("/api/settings/regenerate-api-key")
        key1 = response1.json()["data"]["api_key"]

        response2 = admin_session.post("/api/settings/regenerate-api-key")
        key2 = response2.json()["data"]["api_key"]

        assert key1 != key2

    def test_regenerate_api_key_no_settings(self, admin_session):
        """测试没有设置时重新生成 API Key 会报错"""
        response = admin_session.post("/api/settings/regenerate-api-key")
        assert response.status_code == 404

    def test_regenerate_api_key_requires_auth(self, client):
        """测试需要认证"""
        response = client.post("/api/settings/regenerate-api-key")
        assert response.status_code in [401, 403]

    def test_regenerate_api_key_requires_admin(self, client, normal_user):
        """测试需要管理员权限"""
        # 登录普通用户
        client.post("/api/auth/login", json={
            "username": "user",
            "password": "user123"
        })

        response = client.post("/api/settings/regenerate-api-key")
        assert response.status_code == 403


class TestUpdateSettingsApiKey:
    """测试 PUT /api/settings 更新 external_api_key"""

    def test_update_api_key(self, admin_session, settings_without_api_key):
        """测试通过 PUT 更新 API Key"""
        response = admin_session.put("/api/settings", json={
            "external_api_key": "new-api-key-12345"
        })
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_update_api_key_empty_preserves_old(self, admin_session, settings_with_api_key):
        """测试空值保留原有 API Key"""
        response = admin_session.put("/api/settings", json={
            "external_api_key": ""
        })
        assert response.status_code == 200
        # 验证原有值保留
        response = admin_session.get("/api/settings")
        assert response.json()["data"]["external_api_key"] == "****5678"

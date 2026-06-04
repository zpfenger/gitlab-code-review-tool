import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.external import router, verify_api_key
from app.database import Base, get_db
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.settings import Settings


@pytest.fixture
def db_engine():
    """创建临时数据库引擎"""
    import tempfile
    import os
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
def app(db_session):
    """创建 FastAPI 应用"""
    app = FastAPI()
    app.include_router(router)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
def client(app):
    """创建测试客户端"""
    return TestClient(app)


class TestVerifyApiKey:
    """测试 API Key 认证依赖项"""

    def test_no_settings_configured(self, client, db_session):
        """未配置 Settings 时返回 401"""
        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "some-key"}
        )
        assert response.status_code == 401
        assert "未配置" in response.json()["detail"]

    def test_no_external_api_key_configured(self, client, db_session):
        """Settings 中未配置 external_api_key 时返回 401"""
        settings = Settings()
        db_session.add(settings)
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "some-key"}
        )
        assert response.status_code == 401
        assert "未配置" in response.json()["detail"]

    @patch('app.api.external.security_service')
    def test_invalid_api_key(self, mock_security, client, db_session):
        """无效 API Key 返回 401"""
        mock_security.decrypt.return_value = "correct-key"

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "wrong-key"}
        )
        assert response.status_code == 401
        assert "无效" in response.json()["detail"]

    @patch('app.api.external.security_service')
    def test_valid_api_key(self, mock_security, client, db_session):
        """有效 API Key 通过认证"""
        mock_security.decrypt.return_value = "correct-key"

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "correct-key"}
        )
        assert response.status_code == 200

    def test_missing_api_key_header(self, client, db_session):
        """缺少 X-API-Key 请求头返回 422"""
        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)
        db_session.commit()

        response = client.get("/api/external/efficiency/list")
        assert response.status_code == 422

    @patch('app.api.external.security_service')
    def test_decrypt_failure(self, mock_security, client, db_session):
        """解密失败返回 401"""
        mock_security.decrypt.side_effect = ValueError("解密失败")

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "some-key"}
        )
        assert response.status_code == 401
        assert "解密失败" in response.json()["detail"]


class TestGetEfficiencyList:
    """测试能效列表端点"""

    @patch('app.api.external.security_service')
    def test_empty_result(self, mock_security, client, db_session):
        """无数据时返回空列表"""
        mock_security.decrypt.return_value = "correct-key"

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "correct-key"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["items"] == []
        assert data["data"]["total"] == 0

    @patch('app.api.external.security_service')
    def test_with_data(self, mock_security, client, db_session):
        """有数据时正确返回"""
        mock_security.decrypt.return_value = "correct-key"

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)

        # 创建测试数据
        today = date.today()
        row = EmployeeEfficiencyDaily(
            author_email="test@example.com",
            author_name="Test User",
            stat_date=today,
            commits_count=5,
            additions=100,
            deletions=50,
            files_changed=10,
            new_files=2,
            deleted_files=1,
            projects_involved='["project1"]',
            review_score=85.5,
            review_grade="A",
            review_summary="Good work",
            work_summary='["task1", "task2"]',
            llm_status="completed",
        )
        db_session.add(row)
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "correct-key"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["items"]) == 1
        assert data["data"]["total"] == 1
        assert data["data"]["items"][0]["author_email"] == "test@example.com"

    @patch('app.api.external.security_service')
    def test_filter_by_email(self, mock_security, client, db_session):
        """按邮箱筛选"""
        mock_security.decrypt.return_value = "correct-key"

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)

        today = date.today()
        row1 = EmployeeEfficiencyDaily(
            author_email="user1@example.com",
            author_name="User 1",
            stat_date=today,
            commits_count=5,
            additions=100,
            deletions=50,
            files_changed=10,
            new_files=2,
            deleted_files=1,
            projects_involved='["project1"]',
            review_score=85.5,
            review_grade="A",
            review_summary="Good work",
            work_summary='[]',
            llm_status="completed",
        )
        row2 = EmployeeEfficiencyDaily(
            author_email="user2@example.com",
            author_name="User 2",
            stat_date=today,
            commits_count=3,
            additions=60,
            deletions=30,
            files_changed=6,
            new_files=1,
            deleted_files=0,
            projects_involved='["project2"]',
            review_score=75.0,
            review_grade="B",
            review_summary="Average work",
            work_summary='[]',
            llm_status="completed",
        )
        db_session.add_all([row1, row2])
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "correct-key"},
            params={"author_email": "user1@example.com"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["items"]) == 1
        assert data["data"]["items"][0]["author_email"] == "user1@example.com"

    @patch('app.api.external.security_service')
    def test_filter_by_date_range(self, mock_security, client, db_session):
        """按日期范围筛选"""
        mock_security.decrypt.return_value = "correct-key"

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)

        today = date.today()
        yesterday = today - timedelta(days=1)
        week_ago = today - timedelta(days=7)

        for i, d in enumerate([today, yesterday, week_ago]):
            row = EmployeeEfficiencyDaily(
                author_email=f"user{i}@example.com",
                author_name=f"User {i}",
                stat_date=d,
                commits_count=5,
                additions=100,
                deletions=50,
                files_changed=10,
                new_files=2,
                deleted_files=1,
                projects_involved='["project1"]',
                review_score=85.5,
                review_grade="A",
                review_summary="Good work",
                work_summary='[]',
                llm_status="completed",
            )
            db_session.add(row)
        db_session.commit()

        # 筛选最近 3 天
        start = (today - timedelta(days=2)).isoformat()
        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "correct-key"},
            params={"start_date": start}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["total"] == 2

    @patch('app.api.external.security_service')
    def test_invalid_date_format(self, mock_security, client, db_session):
        """日期格式错误返回 400"""
        mock_security.decrypt.return_value = "correct-key"

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)

        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "correct-key"},
            params={"start_date": "invalid-date"}
        )
        assert response.status_code == 400

    @patch('app.api.external.security_service')
    def test_pagination(self, mock_security, client, db_session):
        """分页功能"""
        mock_security.decrypt.return_value = "correct-key"

        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)

        today = date.today()
        for i in range(25):
            row = EmployeeEfficiencyDaily(
                author_email=f"user{i}@example.com",
                author_name=f"User {i}",
                stat_date=today,
                commits_count=5,
                additions=100,
                deletions=50,
                files_changed=10,
                new_files=2,
                deleted_files=1,
                projects_involved='["project1"]',
                review_score=85.5,
                review_grade="A",
                review_summary="Good work",
                work_summary='[]',
                llm_status="completed",
            )
            db_session.add(row)
        db_session.commit()

        # 第一页
        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "correct-key"},
            params={"page": 1, "page_size": 10}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["items"]) == 10
        assert data["data"]["total"] == 25
        assert data["data"]["page"] == 1
        assert data["data"]["page_size"] == 10

        # 第三页
        response = client.get(
            "/api/external/efficiency/list",
            headers={"X-API-Key": "correct-key"},
            params={"page": 3, "page_size": 10}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]["items"]) == 5

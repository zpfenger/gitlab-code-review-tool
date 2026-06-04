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

API_HEADERS = {"X-API-Key": "correct-key"}


def _setup_api_key(db_session, mock_security):
    """辅助：配置 API Key 并返回 mock"""
    mock_security.decrypt.return_value = "correct-key"
    settings = Settings(external_api_key="encrypted-key")
    db_session.add(settings)
    db_session.commit()


def _make_row(**overrides):
    """辅助：构造 EmployeeEfficiencyDaily 行"""
    defaults = dict(
        author_email="test@example.com",
        author_name="Test User",
        stat_date=date.today(),
        commits_count=5,
        additions=100,
        deletions=50,
        files_changed=10,
        new_files=2,
        deleted_files=1,
        projects_involved='["project1"]',
        review_score=85,
        review_grade="良好",
        review_summary="Good work",
        work_summary='["task1"]',
        llm_status="success",
    )
    defaults.update(overrides)
    return EmployeeEfficiencyDaily(**defaults)


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


class TestGetEfficiencyDaily:
    """测试 GET /api/external/efficiency/daily 端点"""

    @patch('app.api.external.security_service')
    def test_success_status(self, mock_security, client, db_session):
        """llm_status 全部 success 时返回正确格式"""
        _setup_api_key(db_session, mock_security)
        yesterday = date.today() - timedelta(days=1)

        db_session.add(_make_row(
            author_email="a@example.com", author_name="Alice",
            stat_date=yesterday, llm_status="success",
        ))
        db_session.add(_make_row(
            author_email="b@example.com", author_name="Bob",
            stat_date=yesterday, llm_status="success",
        ))
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
            params={"date": yesterday.isoformat()},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["date"] == yesterday.isoformat()
        assert data["llm_status"] == "success"
        assert "message" not in data
        assert "generated_at" in data
        assert len(data["items"]) == 2
        assert data["items"][0]["author_email"] in ("a@example.com", "b@example.com")

    @patch('app.api.external.security_service')
    def test_no_data_returns_pending(self, mock_security, client, db_session):
        """指定日期无记录时返回 pending 状态和空列表"""
        _setup_api_key(db_session, mock_security)
        yesterday = date.today() - timedelta(days=1)

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
            params={"date": yesterday.isoformat()},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        assert data["llm_status"] == "pending"
        assert data["items"] == []
        assert "尚未生成" in data["message"]

    @patch('app.api.external.security_service')
    def test_pending_status(self, mock_security, client, db_session):
        """记录 llm_status 为 pending 时返回提示信息"""
        _setup_api_key(db_session, mock_security)
        yesterday = date.today() - timedelta(days=1)

        db_session.add(_make_row(
            stat_date=yesterday, llm_status="pending",
        ))
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
            params={"date": yesterday.isoformat()},
        )
        body = response.json()
        data = body["data"]
        assert data["llm_status"] == "pending"
        assert data["items"] == []
        assert "尚未生成" in data["message"]

    @patch('app.api.external.security_service')
    def test_failed_status(self, mock_security, client, db_session):
        """记录 llm_status 为 failed 时返回失败提示"""
        _setup_api_key(db_session, mock_security)
        yesterday = date.today() - timedelta(days=1)

        db_session.add(_make_row(
            stat_date=yesterday, llm_status="failed",
        ))
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
            params={"date": yesterday.isoformat()},
        )
        body = response.json()
        data = body["data"]
        assert data["llm_status"] == "failed"
        assert data["items"] == []
        assert "失败" in data["message"]

    @patch('app.api.external.security_service')
    def test_skipped_status(self, mock_security, client, db_session):
        """记录 llm_status 为 skipped 时返回跳过提示"""
        _setup_api_key(db_session, mock_security)
        yesterday = date.today() - timedelta(days=1)

        db_session.add(_make_row(
            stat_date=yesterday, llm_status="skipped",
        ))
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
            params={"date": yesterday.isoformat()},
        )
        body = response.json()
        data = body["data"]
        assert data["llm_status"] == "skipped"
        assert data["items"] == []
        assert "跳过" in data["message"]

    @patch('app.api.external.security_service')
    def test_partial_status(self, mock_security, client, db_session):
        """部分 success 部分 failed 时返回 partial 状态"""
        _setup_api_key(db_session, mock_security)
        yesterday = date.today() - timedelta(days=1)

        db_session.add(_make_row(
            author_email="ok@example.com", stat_date=yesterday,
            llm_status="success",
        ))
        db_session.add(_make_row(
            author_email="bad@example.com", stat_date=yesterday,
            llm_status="failed",
        ))
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
            params={"date": yesterday.isoformat()},
        )
        body = response.json()
        data = body["data"]
        assert data["llm_status"] == "partial"
        assert data["items"] == []
        assert "未完成" in data["message"]

    @patch('app.api.external.security_service')
    def test_default_date_is_yesterday(self, mock_security, client, db_session):
        """不传 date 参数时默认查询前一天"""
        _setup_api_key(db_session, mock_security)
        yesterday = date.today() - timedelta(days=1)

        db_session.add(_make_row(stat_date=yesterday, llm_status="success"))
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
        )
        body = response.json()
        data = body["data"]
        assert data["date"] == yesterday.isoformat()
        assert len(data["items"]) == 1

    @patch('app.api.external.security_service')
    def test_invalid_date_format(self, mock_security, client, db_session):
        """无效日期格式返回 400"""
        _setup_api_key(db_session, mock_security)

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
            params={"date": "not-a-date"},
        )
        assert response.status_code == 400
        assert "格式错误" in response.json()["detail"]

    @patch('app.api.external.security_service')
    def test_authentication_required(self, mock_security, client, db_session):
        """未认证请求返回 401"""
        mock_security.decrypt.return_value = "correct-key"
        settings = Settings(external_api_key="encrypted-key")
        db_session.add(settings)
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/daily",
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 401

    @patch('app.api.external.security_service')
    def test_response_contains_serialized_fields(self, mock_security, client, db_session):
        """返回的 items 包含 _serialize 输出的所有字段"""
        _setup_api_key(db_session, mock_security)
        yesterday = date.today() - timedelta(days=1)

        db_session.add(_make_row(
            stat_date=yesterday, llm_status="success",
            review_score=92, review_grade="优秀",
            review_summary="Excellent",
        ))
        db_session.commit()

        response = client.get(
            "/api/external/efficiency/daily",
            headers=API_HEADERS,
            params={"date": yesterday.isoformat()},
        )
        item = response.json()["data"]["items"][0]
        expected_keys = {
            "id", "author_email", "author_name", "stat_date",
            "commits_count", "additions", "deletions", "files_changed",
            "new_files", "deleted_files", "projects_involved",
            "review_score", "review_grade", "review_summary",
            "work_summary", "llm_status", "llm_error",
        }
        assert expected_keys == set(item.keys())

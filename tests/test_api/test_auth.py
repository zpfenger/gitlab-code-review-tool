import pytest
from unittest.mock import patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware
from app.api.auth import router
from app.config import AdminConfig


@pytest.fixture
def app():
    """Create FastAPI app with auth router and session middleware"""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key-for-testing")
    app.include_router(router)
    return app


@pytest.fixture
def client(app):
    """Create test client"""
    return TestClient(app)


class TestLoginEndpoint:
    """Test login endpoint"""

    @patch('app.api.auth.config_manager')
    def test_login_success(self, mock_config_manager, client):
        """Test successful login"""
        mock_config = AdminConfig(
            username="admin",
            password_hash="$2b$12$test_hash"
        )
        mock_config_manager.get_admin_config.return_value = mock_config

        with patch('app.api.auth.security_service.verify_password', return_value=True):
            response = client.post("/api/auth/login", json={
                "username": "admin",
                "password": "password123"
            })

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    @patch('app.api.auth.config_manager')
    def test_login_invalid_username(self, mock_config_manager, client):
        """Test login with invalid username"""
        mock_config = AdminConfig(
            username="admin",
            password_hash="$2b$12$test_hash"
        )
        mock_config_manager.get_admin_config.return_value = mock_config

        response = client.post("/api/auth/login", json={
            "username": "wronguser",
            "password": "password123"
        })

        assert response.status_code == 401

    @patch('app.api.auth.config_manager')
    def test_login_invalid_password(self, mock_config_manager, client):
        """Test login with invalid password"""
        mock_config = AdminConfig(
            username="admin",
            password_hash="$2b$12$test_hash"
        )
        mock_config_manager.get_admin_config.return_value = mock_config

        with patch('app.api.auth.security_service.verify_password', return_value=False):
            response = client.post("/api/auth/login", json={
                "username": "admin",
                "password": "wrongpassword"
            })

        assert response.status_code == 401


class TestLogoutEndpoint:
    """Test logout endpoint"""

    def test_logout_success(self, client):
        """Test successful logout"""
        response = client.get("/api/auth/logout", follow_redirects=False)
        assert response.status_code == 302


class TestMeEndpoint:
    """Test /me endpoint"""

    def test_me_not_authenticated(self, client):
        """Test /me without authentication"""
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    @patch('app.api.auth.config_manager')
    def test_me_authenticated(self, mock_config_manager, client):
        """Test /me with authentication"""
        mock_config = AdminConfig(
            username="admin",
            password_hash="$2b$12$test_hash"
        )
        mock_config_manager.get_admin_config.return_value = mock_config

        with patch('app.api.auth.security_service.verify_password', return_value=True):
            login_response = client.post("/api/auth/login", json={
                "username": "admin",
                "password": "password123"
            })

        assert login_response.status_code == 200

        # Now test /me endpoint
        response = client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["username"] == "admin"
        assert data["authenticated"] is True

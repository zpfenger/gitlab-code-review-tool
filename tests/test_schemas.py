# tests/test_schemas.py
import pytest
from pydantic import ValidationError
from datetime import datetime

from app.schemas.project import (
    ProjectBase,
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
)
from app.schemas.settings import (
    SettingsBase,
    SettingsCreate,
    SettingsUpdate,
    SettingsResponse,
)
from app.schemas.task import (
    TaskRunRequest,
    TaskProgress,
    TaskResponse,
)
from app.schemas.response import (
    ApiResponse,
    PaginatedResponse,
    ErrorDetail,
)


class TestProjectSchemas:
    """Test project schemas"""

    def test_project_base_valid(self):
        """Test valid ProjectBase"""
        data = {
            "name": "Test Project",
            "gitlab_url": "https://gitlab.example.com",
            "project_id": 123,
        }
        project = ProjectBase(**data)
        assert project.name == "Test Project"
        assert project.gitlab_url == "https://gitlab.example.com"
        assert project.project_id == 123
        assert project.is_active is True
        assert project.exclude_branches is None

    def test_project_base_name_validation(self):
        """Test ProjectBase name validation"""
        # Empty name
        with pytest.raises(ValidationError):
            ProjectBase(
                name="",
                gitlab_url="https://gitlab.example.com",
                project_id=123,
            )

        # Name too long
        with pytest.raises(ValidationError):
            ProjectBase(
                name="x" * 101,
                gitlab_url="https://gitlab.example.com",
                project_id=123,
            )

    def test_project_base_project_id_validation(self):
        """Test ProjectBase project_id validation"""
        # project_id must be > 0
        with pytest.raises(ValidationError):
            ProjectBase(
                name="Test",
                gitlab_url="https://gitlab.example.com",
                project_id=0,
            )

        with pytest.raises(ValidationError):
            ProjectBase(
                name="Test",
                gitlab_url="https://gitlab.example.com",
                project_id=-1,
            )

    def test_project_create(self):
        """Test ProjectCreate inherits from ProjectBase"""
        data = {
            "name": "Test Project",
            "gitlab_url": "https://gitlab.example.com",
            "project_id": 123,
        }
        project = ProjectCreate(**data)
        assert project.name == "Test Project"

    def test_project_update(self):
        """Test ProjectUpdate allows partial updates"""
        # Empty update is valid
        update = ProjectUpdate()
        assert update.name is None
        assert update.gitlab_url is None

        # Partial update
        update = ProjectUpdate(name="New Name")
        assert update.name == "New Name"

    def test_project_response(self):
        """Test ProjectResponse with timestamps"""
        data = {
            "id": 1,
            "name": "Test Project",
            "gitlab_url": "https://gitlab.example.com",
            "project_id": 123,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        response = ProjectResponse(**data)
        assert response.id == 1


class TestSettingsSchemas:
    """Test settings schemas"""

    def test_settings_base_valid(self):
        """Test valid SettingsBase"""
        data = {
            "global_gitlab_url": "https://gitlab.example.com",
            "llm_api_url": "https://api.openai.com",
            "llm_model": "gpt-4",
        }
        settings = SettingsBase(**data)
        assert settings.daily_schedule_times == '["09:00"]'
        assert settings.llm_timeout == 120
        assert settings.llm_max_retries == 3

    def test_settings_base_schedule_time_validation(self):
        """Test SettingsBase daily_schedule_times validation"""
        # Invalid format - not a valid time
        with pytest.raises(ValidationError):
            SettingsBase(
                global_gitlab_url="https://gitlab.example.com",
                llm_api_url="https://api.openai.com",
                llm_model="gpt-4",
                daily_schedule_times='["9:00"]',  # Missing leading zero
            )

    def test_settings_base_llm_timeout_validation(self):
        """Test SettingsBase llm_timeout validation"""
        # Too low
        with pytest.raises(ValidationError):
            SettingsBase(
                global_gitlab_url="https://gitlab.example.com",
                llm_api_url="https://api.openai.com",
                llm_model="gpt-4",
                llm_timeout=5,
            )

        # Too high
        with pytest.raises(ValidationError):
            SettingsBase(
                global_gitlab_url="https://gitlab.example.com",
                llm_api_url="https://api.openai.com",
                llm_model="gpt-4",
                llm_timeout=700,
            )

    def test_settings_update(self):
        """Test SettingsUpdate allows partial updates"""
        update = SettingsUpdate()
        assert update.global_gitlab_url is None
        assert update.llm_api_url is None

        update = SettingsUpdate(llm_model="gpt-4-turbo")
        assert update.llm_model == "gpt-4-turbo"


class TestTaskSchemas:
    """Test task schemas"""

    def test_task_run_request_default(self):
        """Test TaskRunRequest defaults"""
        request = TaskRunRequest()
        assert request.project_id is None
        assert request.task_type == "daily"

    def test_task_run_request_with_project(self):
        """Test TaskRunRequest with project_id"""
        request = TaskRunRequest(project_id=123, task_type="manual")
        assert request.project_id == 123
        assert request.task_type == "manual"

    def test_task_progress(self):
        """Test TaskProgress"""
        progress = TaskProgress(is_running=True)
        assert progress.is_running is True
        assert progress.branches_processed == 0
        assert progress.start_time is None

    def test_task_response(self):
        """Test TaskResponse"""
        response = TaskResponse(success=True, message="Task completed")
        assert response.success is True
        assert response.message == "Task completed"
        assert response.progress is None


class TestResponseSchemas:
    """Test response schemas"""

    def test_api_response_success(self):
        """Test ApiResponse with success"""
        response = ApiResponse[dict](success=True, data={"key": "value"})
        assert response.success is True
        assert response.data == {"key": "value"}

    def test_api_response_error(self):
        """Test ApiResponse with error"""
        response = ApiResponse[dict](
            success=False,
            error=ErrorDetail(code="ERROR", message="Something went wrong")
        )
        assert response.success is False
        assert response.error.message == "Something went wrong"
        assert response.error.code == "ERROR"

    def test_paginated_response(self):
        """Test PaginatedResponse"""
        response = PaginatedResponse[dict](
            data=[{"id": 1}, {"id": 2}],
            total=100,
            page=2,
            page_size=20
        )
        assert response.success is True
        assert len(response.data) == 2
        assert response.total == 100
        assert response.page == 2

# app/schemas/__init__.py
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
)

__all__ = [
    # Project schemas
    "ProjectBase",
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectResponse",
    # Settings schemas
    "SettingsBase",
    "SettingsCreate",
    "SettingsUpdate",
    "SettingsResponse",
    # Task schemas
    "TaskRunRequest",
    "TaskProgress",
    "TaskResponse",
    # Response schemas
    "ApiResponse",
    "PaginatedResponse",
]

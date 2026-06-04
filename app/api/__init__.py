# app/api/__init__.py
from app.api.auth import router as auth_router
from app.api.projects import router as projects_router
from app.api.settings import router as settings_router
from app.api.tasks import router as tasks_router
from app.api.logs import router as logs_router
from app.api.reports import router as reports_router
from app.api.users import router as users_router
from app.api.roles import router as roles_router
from app.api.external import router as external_router
from app.api.deps import get_current_user, get_optional_user

__all__ = [
    "auth_router",
    "projects_router",
    "settings_router",
    "tasks_router",
    "logs_router",
    "reports_router",
    "users_router",
    "roles_router",
    "external_router",
    "get_current_user",
    "get_optional_user",
]

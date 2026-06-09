from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.main import get_template_context


def test_template_context_prefers_database_nickname_for_current_user():
    """底部用户名优先显示当前登录用户昵称，而不是管理员配置昵称。"""
    request = SimpleNamespace(session={"user": "alice"})

    db_user = SimpleNamespace(nickname="Alice Nick")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = db_user

    with (
        patch("app.main.SessionLocal", return_value=db),
        patch(
            "app.main.config_manager.get_admin_config",
            return_value=SimpleNamespace(nickname="Admin Nick"),
        ),
    ):
        context = get_template_context(request)

    assert context["current_user"] == "alice"
    assert context["display_name"] == "Alice Nick"
    db.close.assert_called_once()


def test_template_context_falls_back_to_username_when_database_nickname_missing():
    """当前登录用户没有昵称时，底部用户名显示账号。"""
    request = SimpleNamespace(session={"user": "alice"})

    db_user = SimpleNamespace(nickname="")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = db_user

    with (
        patch("app.main.SessionLocal", return_value=db),
        patch(
            "app.main.config_manager.get_admin_config",
            return_value=SimpleNamespace(nickname="Admin Nick"),
        ),
    ):
        context = get_template_context(request)

    assert context["display_name"] == "alice"
    db.close.assert_called_once()

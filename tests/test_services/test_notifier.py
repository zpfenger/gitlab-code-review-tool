from unittest.mock import patch

from app.models.project import Project
from app.models.settings import Settings
from app.services.notifier import Notifier


class TestNotifier:
    def test_from_project_fallback_to_global_wecom_when_project_decrypt_fails(self):
        """项目级企业微信解密失败时，回退到全局企业微信配置"""
        project = Project(
            name="project-a",
            project_id=101,
            wecom_enabled=True,
            wecom_webhook_url="enc_project_url",
        )
        settings = Settings(
            global_gitlab_url="https://gitlab.example.com",
            llm_api_url="https://api.example.com/v1",
            llm_model="gpt-4",
            report_output_dir="./data/reports",
            wecom_enabled=True,
            wecom_webhook_url="enc_global_url",
        )

        def fake_decrypt(value: str) -> str:
            if value == "enc_project_url":
                raise ValueError("project decrypt failed")
            if value == "enc_global_url":
                return "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=global"
            raise AssertionError(f"unexpected encrypted value: {value}")

        with patch("app.security.security_service.decrypt", side_effect=fake_decrypt):
            notifier = Notifier.from_project(project, settings)

        assert notifier.wecom.enabled is True
        assert (
            notifier.wecom.webhook_url
            == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=global"
        )

    def test_from_project_global_wecom_disabled_when_global_decrypt_fails(self):
        """全局企业微信解密失败时，显式禁用企业微信通道"""
        project = Project(
            name="project-b",
            project_id=102,
            wecom_enabled=False,
            wecom_webhook_url="",
        )
        settings = Settings(
            global_gitlab_url="https://gitlab.example.com",
            llm_api_url="https://api.example.com/v1",
            llm_model="gpt-4",
            report_output_dir="./data/reports",
            wecom_enabled=True,
            wecom_webhook_url="enc_global_url_fail",
        )

        with patch(
            "app.security.security_service.decrypt",
            side_effect=ValueError("global decrypt failed"),
        ):
            notifier = Notifier.from_project(project, settings)

        assert notifier.wecom.enabled is False
        assert notifier.wecom.webhook_url == ""

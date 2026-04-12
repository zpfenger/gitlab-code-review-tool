import pytest
from app.models.settings import Settings


class TestSettings:
    def test_settings_creation(self, db_session):
        """测试创建设置"""
        settings = Settings(
            global_gitlab_url="https://gitlab.example.com",
            global_gitlab_token="encrypted_token",
            llm_api_url="https://api.example.com/v1",
            llm_api_key="encrypted_key",
            llm_model="gpt-4",
            review_prompt_template="请审查以下代码",
            stats_prompt="请统计以下代码",
            global_svn_url="https://svn.example.com",
            global_svn_username="svn_user",
            global_svn_password="encrypted_pass",
            report_output_dir="./data/reports",
            daily_schedule_times='["09:00"]'
        )
        db_session.add(settings)
        db_session.commit()

        assert settings.id is not None
        assert settings.daily_schedule_times == '["09:00"]'
        assert settings.llm_timeout == 120
        assert settings.llm_max_retries == 3

    def test_settings_defaults(self, db_session):
        """测试默认值"""
        settings = Settings(
            global_gitlab_url="https://gitlab.example.com",
            llm_api_url="https://api.example.com/v1",
            llm_model="gpt-4",
            report_output_dir="./reports"
        )
        db_session.add(settings)
        db_session.commit()

        assert settings.llm_timeout == 120
        assert settings.llm_max_retries == 3
        assert settings.llm_retry_delay == 5
        assert settings.max_commits_per_run == 100

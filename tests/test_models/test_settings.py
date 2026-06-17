import pytest
from app.models.settings import Settings
from app.security import security_service


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
        assert settings.llm_timeout == 240
        assert settings.llm_max_retries == 5

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

        assert settings.llm_timeout == 240
        assert settings.llm_max_retries == 5
        assert settings.llm_retry_delay == 60
        assert settings.max_commits_per_run == 100

    def test_external_api_key_field_nullable(self, db_session):
        """测试 external_api_key 字段可为 null"""
        settings = Settings(
            global_gitlab_url="https://gitlab.example.com",
            llm_api_url="https://api.example.com/v1",
            llm_model="gpt-4",
            report_output_dir="./reports"
        )
        db_session.add(settings)
        db_session.commit()

        assert settings.external_api_key is None

    def test_external_api_key_encrypted_storage(self, db_session):
        """测试 external_api_key 加密存储（含 100+ 字符明文，验证 Fernet 密文不超限）"""
        # 使用 100+ 字符的明文，Fernet 加密后约 233 字符，验证 String(500) 足够
        plaintext_key = "hr-system-api-key-" + "x" * 100 + "-end-12345"
        assert len(plaintext_key) > 100
        encrypted_key = security_service.encrypt(plaintext_key)

        settings = Settings(
            global_gitlab_url="https://gitlab.example.com",
            llm_api_url="https://api.example.com/v1",
            llm_model="gpt-4",
            report_output_dir="./reports",
            external_api_key=encrypted_key
        )
        db_session.add(settings)
        db_session.commit()
        db_session.refresh(settings)

        # 存储的值应该是加密的，不是明文
        assert settings.external_api_key != plaintext_key
        # 解密后应得到原始明文
        assert security_service.decrypt(settings.external_api_key) == plaintext_key

    def test_external_api_key_update(self, db_session):
        """测试 external_api_key 字段可正常更新"""
        settings = Settings(
            global_gitlab_url="https://gitlab.example.com",
            llm_api_url="https://api.example.com/v1",
            llm_model="gpt-4",
            report_output_dir="./reports",
            external_api_key=security_service.encrypt("old-key")
        )
        db_session.add(settings)
        db_session.commit()

        # 更新为新值
        new_key = "new-hr-api-key-67890"
        settings.external_api_key = security_service.encrypt(new_key)
        db_session.commit()
        db_session.refresh(settings)

        assert security_service.decrypt(settings.external_api_key) == new_key

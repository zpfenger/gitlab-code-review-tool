# tests/test_config.py
import pytest
import tempfile
import os
from app.config import AdminConfig, ConfigManager


class TestAdminConfig:
    def test_default_config(self):
        """测试默认配置"""
        config = AdminConfig(username="admin", password_hash="hash")
        assert config.username == "admin"
        assert config.session_timeout == 3600

    def test_config_from_yaml(self, tmp_path):
        """测试从 YAML 加载配置"""
        yaml_content = """
admin:
  username: testadmin
  password_hash: $2b$12$testhash
  session_timeout: 7200
"""
        config_file = tmp_path / "admin.yaml"
        config_file.write_text(yaml_content)

        manager = ConfigManager(str(config_file))
        config = manager.get_admin_config()

        assert config.username == "testadmin"
        assert config.session_timeout == 7200

    def test_config_not_found_creates_default(self, tmp_path):
        """测试配置不存在时创建默认"""
        config_file = tmp_path / "notexist.yaml"
        manager = ConfigManager(str(config_file))
        config = manager.get_admin_config()

        assert config.username == "admin"
        assert config_file.exists()

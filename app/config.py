# app/config.py
import os
import yaml
from dataclasses import dataclass
from typing import Optional
from loguru import logger


@dataclass
class AdminConfig:
    """管理员配置"""
    username: str
    password_hash: str
    session_timeout: int = 3600
    nickname: str = ""
    email: str = ""


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.environ.get(
            'CONFIG_PATH',
            './config/admin.yaml'
        )
        self._admin_config: Optional[AdminConfig] = None

    def get_admin_config(self) -> AdminConfig:
        """获取管理员配置"""
        if self._admin_config:
            return self._admin_config

        if os.path.exists(self.config_path):
            self._admin_config = self._load_admin_config()
        else:
            self._admin_config = self._create_default_admin_config()

        return self._admin_config

    def _load_admin_config(self) -> AdminConfig:
        """从 YAML 文件加载管理员配置"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        admin_data = data.get('admin', {})
        password_hash = admin_data.get('password_hash', '')

        # 如果密码哈希为空或无效，生成默认密码
        if not password_hash or not password_hash.startswith('$2b$'):
            return self._create_default_admin_config()

        return AdminConfig(
            username=admin_data.get('username', 'admin'),
            password_hash=password_hash,
            session_timeout=admin_data.get('session_timeout', 3600),
            nickname=admin_data.get('nickname', ''),
            email=admin_data.get('email', ''),
        )

    def _create_default_admin_config(self) -> AdminConfig:
        """创建默认管理员配置"""
        from app.security import security_service

        # 生成默认密码
        default_password = security_service.create_session_token()[:12]
        password_hash = security_service.hash_password(default_password)

        config = AdminConfig(
            username='admin',
            password_hash=password_hash
        )

        # 保存配置文件
        self._save_admin_config(config)

        # 打印默认密码（仅首次）
        logger.info(f"首次启动，已创建默认管理员账号")
        logger.info(f"用户名: admin")
        logger.info(f"密码: {default_password}")
        logger.info(f"请登录后立即修改密码！")

        return config

    def _save_admin_config(self, config: AdminConfig):
        """保存管理员配置"""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)

        data = {
            'admin': {
                'username': config.username,
                'password_hash': config.password_hash,
                'session_timeout': config.session_timeout,
                'nickname': config.nickname,
                'email': config.email,
            }
        }

        with open(self.config_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    def update_admin_password(self, new_password: str):
        """更新管理员密码"""
        from app.security import security_service

        config = self.get_admin_config()
        config.password_hash = security_service.hash_password(new_password)
        self._save_admin_config(config)
        # 强制刷新内存缓存，确保本进程内立即生效
        self._admin_config = None
        self._admin_config = self._load_admin_config()

    def update_admin_profile(self, nickname: Optional[str] = None, email: Optional[str] = None):
        """更新管理员个人信息"""
        config = self.get_admin_config()
        if nickname is not None:
            config.nickname = nickname
        if email is not None:
            config.email = email
        self._save_admin_config(config)
        self._admin_config = config


# 全局配置管理器
config_manager = ConfigManager()

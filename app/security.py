# app/security.py
import os
import secrets
import bcrypt
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


class SecurityService:
    """安全服务：加密、认证"""

    # 默认盐值文件路径
    _SALT_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'config', '.salt')

    def __init__(self, secret_key: str = None):
        """
        初始化安全服务

        Args:
            secret_key: 加密密钥，未提供时从环境变量或持久化文件读取
        """
        self.secret_key = secret_key or os.environ.get('SECRET_KEY')
        if not self.secret_key:
            # 尝试从持久化文件加载
            self.secret_key = self._load_or_create_secret()

        # 派生 Fernet 密钥
        salt = self._load_or_create_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self.secret_key.encode()))
        self.fernet = Fernet(key)

    def _load_or_create_secret(self) -> str:
        """加载或创建持久化的密钥"""
        secret_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'config', '.secret_key')
        if os.path.exists(secret_file):
            with open(secret_file, 'r') as f:
                return f.read().strip()
        # 首次生成，持久化保存
        secret = secrets.token_hex(32)
        os.makedirs(os.path.dirname(secret_file), exist_ok=True)
        with open(secret_file, 'w') as f:
            f.write(secret)
        return secret

    def _load_or_create_salt(self) -> bytes:
        """加载或创建持久化的盐值"""
        if os.path.exists(self._SALT_FILE):
            with open(self._SALT_FILE, 'rb') as f:
                return f.read()
        # 首次生成随机盐值，持久化保存
        salt = secrets.token_bytes(16)
        os.makedirs(os.path.dirname(self._SALT_FILE), exist_ok=True)
        with open(self._SALT_FILE, 'wb') as f:
            f.write(salt)
        return salt

    def encrypt(self, plaintext: str) -> str:
        """加密字符串"""
        if not plaintext:
            return ""
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """解密字符串"""
        if not ciphertext:
            return ""
        try:
            return self.fernet.decrypt(ciphertext.encode()).decode()
        except Exception as e:
            raise ValueError(
                f"解密失败（密钥可能已变更，请重新配置相关凭据）: {type(e).__name__}"
            ) from e

    def hash_password(self, password: str) -> str:
        """哈希密码"""
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def verify_password(self, password: str, hashed: str) -> bool:
        """验证密码"""
        return bcrypt.checkpw(password.encode(), hashed.encode())

    def create_session_token(self) -> str:
        """创建会话令牌"""
        return secrets.token_hex(16)


# 全局实例
security_service = SecurityService()

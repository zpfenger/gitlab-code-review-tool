# tests/test_security.py
import pytest
from app.security import SecurityService


class TestSecurityService:
    def test_encrypt_decrypt(self):
        """测试加密解密"""
        service = SecurityService()
        original = "my_secret_token"
        encrypted = service.encrypt(original)

        assert encrypted != original
        assert service.decrypt(encrypted) == original

    def test_encrypt_different_results(self):
        """测试每次加密结果不同（随机 IV）"""
        service = SecurityService()
        original = "same_token"
        encrypted1 = service.encrypt(original)
        encrypted2 = service.encrypt(original)

        assert encrypted1 != encrypted2
        assert service.decrypt(encrypted1) == original
        assert service.decrypt(encrypted2) == original

    def test_hash_password(self):
        """测试密码哈希"""
        service = SecurityService()
        password = "admin123"
        hashed = service.hash_password(password)

        assert hashed != password
        assert service.verify_password(password, hashed) is True
        assert service.verify_password("wrong_password", hashed) is False

    def test_create_session_token(self):
        """测试创建会话令牌"""
        service = SecurityService()
        token = service.create_session_token()

        assert len(token) == 32
        assert token != service.create_session_token()

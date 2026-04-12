import pytest
from datetime import datetime
from app.models.base import BaseModel, TimestampMixin


class TestBaseModel:
    def test_base_model_has_id(self):
        """测试基础模型有 id 字段"""
        assert hasattr(BaseModel, 'id')

    def test_timestamp_mixin_has_created_at(self):
        """测试时间戳混入有 created_at 字段"""
        assert hasattr(TimestampMixin, 'created_at')

    def test_timestamp_mixin_has_updated_at(self):
        """测试时间戳混入有 updated_at 字段"""
        assert hasattr(TimestampMixin, 'updated_at')

from datetime import datetime, timezone
from sqlalchemy import Column, Integer, DateTime
from app.database import Base


class TimestampMixin:
    """时间戳混入类"""
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)


class BaseModel(Base, TimestampMixin):
    """基础模型类"""
    __abstract__ = True
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)

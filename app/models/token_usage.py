from sqlalchemy import Column, String, Integer, Index

from app.models.base import BaseModel


class TokenUsageLog(BaseModel):
    """LLM token 消耗记录"""

    __tablename__ = "token_usage_log"

    biz_type = Column(String(50), nullable=False, comment="业务类型")
    biz_id = Column(Integer, nullable=True, comment="关联业务记录主键")
    project_name = Column(String(200), nullable=True, comment="项目名称")
    author = Column(String(200), nullable=True, comment="相关人员")
    model = Column(String(100), nullable=False, comment="LLM 模型名")
    prompt_tokens = Column(Integer, default=0, comment="输入 token")
    completion_tokens = Column(Integer, default=0, comment="输出 token")
    total_tokens = Column(Integer, default=0, comment="总 token")
    created_at_ts = Column(Integer, nullable=False, comment="调用时间戳")

    __table_args__ = (
        Index("idx_token_usage_biz", "biz_type", "biz_id"),
        Index("idx_token_usage_created_at_ts", "created_at_ts"),
    )

    def __repr__(self):
        return (
            f"<TokenUsageLog(id={self.id}, biz_type={self.biz_type}, "
            f"biz_id={self.biz_id}, total_tokens={self.total_tokens})>"
        )

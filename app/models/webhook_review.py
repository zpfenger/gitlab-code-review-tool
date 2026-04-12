from sqlalchemy import Column, String, Integer, Text, Index, UniqueConstraint
from app.models.base import BaseModel


class MrReviewLog(BaseModel):
    """Merge Request Webhook 审查日志"""
    __tablename__ = "mr_review_log"

    project_name = Column(String(200), nullable=False, comment="项目名称")
    author = Column(String(200), nullable=False, comment="提交者")
    source_branch = Column(String(200), nullable=True, comment="源分支")
    target_branch = Column(String(200), nullable=True, comment="目标分支")
    updated_at = Column(Integer, nullable=False, comment="时间戳")
    commit_messages = Column(Text, nullable=True, comment="提交信息汇总")
    score = Column(Integer, default=0, comment="AI 评分 (0-100)")
    url = Column(String(500), nullable=True, comment="MR 链接")
    review_result = Column(Text, nullable=True, comment="AI 审查结果")
    additions = Column(Integer, default=0, comment="新增行数")
    deletions = Column(Integer, default=0, comment="删除行数")
    last_commit_id = Column(String(100), nullable=True, default=None, comment="最后提交 ID（去重用）")

    __table_args__ = (
        Index("idx_mr_review_log_updated_at", "updated_at"),
        UniqueConstraint(
            "project_name",
            "source_branch",
            "target_branch",
            "last_commit_id",
            name="uq_mr_review_log_dedup",
        ),
    )

    def __repr__(self):
        return f"<MrReviewLog(id={self.id}, project={self.project_name}, score={self.score})>"


class PushReviewLog(BaseModel):
    """Push Webhook 审查日志"""
    __tablename__ = "push_review_log"

    project_name = Column(String(200), nullable=False, comment="项目名称")
    author = Column(String(200), nullable=False, comment="提交者")
    branch = Column(String(200), nullable=True, comment="分支")
    updated_at = Column(Integer, nullable=False, comment="时间戳")
    commit_messages = Column(Text, nullable=True, comment="提交信息汇总")
    score = Column(Integer, default=0, comment="AI 评分 (0-100)")
    review_result = Column(Text, nullable=True, comment="AI 审查结果")
    additions = Column(Integer, default=0, comment="新增行数")
    deletions = Column(Integer, default=0, comment="删除行数")
    last_commit_id = Column(String(100), nullable=True, default=None, comment="最后提交 ID（去重用）")

    __table_args__ = (
        Index("idx_push_review_log_updated_at", "updated_at"),
        UniqueConstraint(
            "project_name",
            "branch",
            "last_commit_id",
            name="uq_push_review_log_dedup",
        ),
    )

    def __repr__(self):
        return f"<PushReviewLog(id={self.id}, project={self.project_name}, score={self.score})>"

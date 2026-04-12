from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class CommitRecord(BaseModel):
    """提交记录（增量处理）"""
    __tablename__ = "commit_records"
    __table_args__ = (
        UniqueConstraint('project_id', 'commit_sha', name='uq_project_commit'),
    )

    project_id = Column(Integer, ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, comment="关联项目")
    commit_sha = Column(String(100), nullable=False, comment="提交 SHA")
    branch = Column(String(200), nullable=False, comment="分支名称")
    author_email = Column(String(200), nullable=False, comment="提交人邮箱")
    author_name = Column(String(100), nullable=False, comment="提交人姓名")
    commit_date = Column(DateTime, nullable=False, comment="提交时间")
    reviewed_at = Column(DateTime, nullable=True, comment="审查时间")
    review_status = Column(String(20), default="pending", comment="状态：pending/success/failed/skipped")
    report_path = Column(String(500), nullable=True, comment="生成的报告路径")

    # 关系：passive_deletes="all" 让数据库处理级联删除，ORM 不再尝试 SET NULL
    project = relationship("Project", back_populates="commit_records",
                           passive_deletes="all")

    def __repr__(self):
        return f"<CommitRecord(id={self.id}, sha='{self.commit_sha[:8]}')>"

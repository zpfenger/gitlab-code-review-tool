from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class TaskLog(BaseModel):
    """执行日志"""
    __tablename__ = "task_logs"

    project_id = Column(Integer, ForeignKey('projects.id', ondelete='CASCADE'), nullable=False, comment="关联项目")
    project_name = Column(String(100), nullable=True, comment="项目名称（冗余，避免 JOIN）")
    task_type = Column(String(20), nullable=False, comment="任务类型 (daily/weekly/monthly)")
    trigger_type = Column(String(20), nullable=False, comment="触发类型 (scheduled/manual)")
    trigger_user = Column(String(100), nullable=True, comment="手动触发时的用户")
    status = Column(String(20), nullable=False, comment="状态 (running/success/failed)")

    start_time = Column(DateTime, nullable=False, default=datetime.utcnow, comment="开始时间")
    end_time = Column(DateTime, nullable=True, comment="结束时间")

    branches_processed = Column(Integer, default=0, comment="处理的分支数")
    commits_processed = Column(Integer, default=0, comment="处理的提交数")
    reports_generated = Column(Integer, default=0, comment="生成的报告数")

    error_message = Column(Text, nullable=True, comment="错误信息")
    details = Column(Text, nullable=True, comment="详细日志 (JSON)")

    # 关系：passive_deletes="all" 让数据库处理级联删除，ORM 不再尝试 SET NULL
    project = relationship("Project", back_populates="task_logs",
                           passive_deletes="all")

    @property
    def duration(self) -> str:
        """计算并格式化执行耗时"""
        if not self.end_time or not self.start_time:
            return "-"
        delta = self.end_time - self.start_time
        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds}s"
        minutes, seconds = divmod(total_seconds, 60)
        if minutes < 60:
            return f"{minutes}m{seconds}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes}m{seconds}s"

    def __repr__(self):
        return f"<TaskLog(id={self.id}, project_id={self.project_id}, status='{self.status}')>"

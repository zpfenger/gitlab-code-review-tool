from sqlalchemy import Column, String, Integer, Boolean, Text
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


class Project(BaseModel):
    """GitLab 项目配置"""
    __tablename__ = "projects"

    name = Column(String(100), nullable=False, unique=True, comment="项目名称")
    gitlab_url = Column(String(500), nullable=True, comment="GitLab 地址 (为空则使用全局配置)")
    project_id = Column(Integer, nullable=False, unique=True, comment="GitLab 项目 ID")
    description = Column(Text, nullable=True, comment="项目描述")
    target_branches = Column(Text, nullable=True, comment="目标分支列表 (逗号分隔)")

    # GitLab 配置(可选,为空则使用全局配置)
    access_token = Column(String(500), nullable=True, comment="访问 Token (加密存储)")

    exclude_branches = Column(Text, nullable=True, default='', comment="排除分支列表 (逗号分隔)")

    # SVN 配置(可选,为空则使用全局配置)
    svn_url = Column(String(500), nullable=True, comment="项目专属 SVN 路径")
    svn_username = Column(String(100), nullable=True, comment="项目专属 SVN 用户名")
    svn_password = Column(String(500), nullable=True, comment="项目专属 SVN 密码 (加密存储)")

    # 企业微信通知配置(可选,为空则使用全局配置)
    wecom_enabled = Column(Boolean, default=False, comment="企业微信通知开关")
    wecom_webhook_url = Column(String(500), nullable=True, comment="企业微信 Webhook URL (加密存储)")

    is_active = Column(Boolean, default=True, nullable=False, comment="是否启用")

    # 与子表的 back_populates（子表使用 passive_deletes="all" 让数据库处理级联删除）
    task_logs = relationship("TaskLog", back_populates="project", passive_deletes="all")
    commit_records = relationship("CommitRecord", back_populates="project", passive_deletes="all")

    def __repr__(self):
        return f"<Project(id={self.id}, name='{self.name}')>"

"""
用户账号模型
"""
from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, Table
from sqlalchemy.orm import relationship
from app.models.base import BaseModel


# 用户-角色关联表
user_roles = Table(
    'user_roles',
    BaseModel.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('role_id', Integer, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
)

# 项目-管理员关联表（项目可以有多个管理员，一个管理员可管理多个项目）
project_admins = Table(
    'project_admins',
    BaseModel.metadata,
    Column('project_id', Integer, ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('assigned_by', Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    Column('assigned_at', Integer, nullable=True, comment="分配时间戳"),
)

# 项目-成员关联表（项目可以有多个成员）
project_members = Table(
    'project_members',
    BaseModel.metadata,
    Column('project_id', Integer, ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('assigned_by', Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    Column('assigned_at', Integer, nullable=True, comment="分配时间戳"),
)


class User(BaseModel):
    """用户账号模型"""
    __tablename__ = "users"

    username = Column(String(50), unique=True, nullable=False, comment="用户名")
    password_hash = Column(String(255), nullable=False, comment="密码哈希")
    nickname = Column(String(100), nullable=True, comment="昵称")
    email = Column(String(100), nullable=True, comment="邮箱")
    is_active = Column(Boolean, default=True, nullable=False, comment="是否启用")

    # 关系
    roles = relationship('Role', secondary=user_roles, back_populates='users')

    def has_role(self, role_name: str) -> bool:
        """检查用户是否拥有指定角色"""
        return any(role.name == role_name for role in self.roles)

    def is_system_admin(self) -> bool:
        """是否是系统管理员"""
        return self.has_role('system_admin')

    def is_project_admin(self) -> bool:
        """是否是项目管理员（任何项目）"""
        return self.has_role('project_admin')

    def is_project_member(self) -> bool:
        """是否是项目成员"""
        return self.has_role('project_member')

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"


class Role(BaseModel):
    """角色模型"""
    __tablename__ = "roles"

    name = Column(String(50), unique=True, nullable=False, comment="角色名称")
    description = Column(String(255), nullable=True, comment="角色描述")
    is_system_role = Column(Boolean, default=False, nullable=False, comment="是否系统内置角色")

    # 关系
    users = relationship('User', secondary=user_roles, back_populates='roles')

    # 角色常量
    SYSTEM_ADMIN = 'system_admin'
    PROJECT_ADMIN = 'project_admin'
    PROJECT_MEMBER = 'project_member'

    def __repr__(self):
        return f"<Role(id={self.id}, name='{self.name}')>"

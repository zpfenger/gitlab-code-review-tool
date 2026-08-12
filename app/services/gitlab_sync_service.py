# app/services/gitlab_sync_service.py
"""GitLab 项目及成员同步服务"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.settings import Settings
from app.models.user import Role, User, project_admins, project_members
from app.security import security_service
from app.services.gitlab_client import GitLabClient

MAINTAINER = 40
OWNER = 50


@dataclass
class GitLabSyncResult:
    """同步结果"""
    total: int = 0
    created: int = 0
    skipped: int = 0
    failed: int = 0
    created_users: int = 0
    skipped_users: int = 0
    synced_members: int = 0
    synced_admins: int = 0
    failed_projects: int = 0
    failed_reasons: List[Dict[str, Any]] = field(default_factory=list)
    removed_admins: List[Dict[str, Any]] = field(default_factory=list)
    created_projects: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class GitLabProjectMemberSyncService:
    """GitLab 项目及成员同步服务"""

    def __init__(self, db: Session):
        self.db = db

    def sync(self) -> GitLabSyncResult:
        """执行同步"""
        settings = self.db.query(Settings).first()
        if not settings:
            raise ValueError("系统设置未配置")
        if not settings.global_gitlab_url:
            raise ValueError("全局 GitLab URL 未配置")
        if not settings.global_gitlab_token:
            raise ValueError("全局 GitLab Token 未配置")

        # 解密凭据：失败时明确指出是哪个字段，引导用户重新配置
        try:
            token = security_service.decrypt(settings.global_gitlab_token)
        except ValueError:
            raise ValueError(
                "全局 GitLab Token 无法解密（数据库中存储的密文已失效），"
                "请在「系统设置」重新填写并保存该 Token"
            )
        try:
            default_password = (
                security_service.decrypt(settings.gitlab_sync_default_password)
                if settings.gitlab_sync_default_password
                else None
            )
        except ValueError:
            raise ValueError(
                "「新建账号初始密码」无法解密（数据库中存储的密文已失效），"
                "请在「系统设置」重新填写并保存，或将其清空"
            )

        client = GitLabClient(settings.global_gitlab_url, token)
        projects = client.list_accessible_projects()
        result = GitLabSyncResult(total=len(projects))

        for remote in projects:
            project = self._ensure_project(remote, result)
            try:
                members = client.get_project_members(project.project_id)
                if not members:
                    result.failed_projects += 1
                    result.failed_reasons.append({
                        "project_id": project.project_id,
                        "reason": "empty members",
                    })
                    continue
                self._replace_project_members(project, members, default_password, result)
            except Exception as exc:
                self.db.rollback()
                result.failed_projects += 1
                result.failed_reasons.append({
                    "project_id": project.project_id,
                    "reason": str(exc),
                })
                logger.warning(f"同步项目成员失败 project_id={project.project_id}: {exc}")

        return result

    def _ensure_project(self, remote: Dict[str, Any], result: GitLabSyncResult) -> Project:
        """确保本地项目存在，不存在则创建。"""
        from app.api.projects import _select_project_name

        gitlab_id = int(remote["id"])
        project = self.db.query(Project).filter(Project.project_id == gitlab_id).first()
        if project:
            result.skipped += 1
            return project

        existing_names = {p.name for p in self.db.query(Project.name).all()}
        name = _select_project_name(remote, existing_names)
        project = Project(
            name=name[:100],
            project_id=gitlab_id,
            description=remote.get("description") or None,
            target_branches=None,
            is_active=True,
        )
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        result.created += 1
        result.created_projects.append({
            "name": project.name,
            "project_id": project.project_id,
        })
        return project

    def _replace_project_members(
        self,
        project: Project,
        members: List[Dict[str, Any]],
        default_password: Optional[str],
        result: GitLabSyncResult,
    ) -> None:
        """覆盖项目成员关系（per-project 事务）。"""
        syncable_members = []
        for member in members:
            if self._should_sync_member(member):
                syncable_members.append(member)
            else:
                result.skipped_users += 1

        if not syncable_members:
            self._clear_project_relations(project, result)
            self.db.commit()
            return

        users = []
        for member in syncable_members:
            user = self._find_or_create_user(member, default_password, result)
            if user:
                users.append((user, member))

        if not users:
            result.failed_projects += 1
            result.failed_reasons.append({
                "project_id": project.project_id,
                "reason": "no matched users",
            })
            return

        system_admin_ids = self._clear_project_relations(project, result)

        # 重写成员关系
        now = int(datetime.now().timestamp())
        project_admin_role = self.db.query(Role).filter(
            Role.name == Role.PROJECT_ADMIN
        ).first()

        for user, member in users:
            self.db.execute(
                project_members.insert().values(
                    project_id=project.id, user_id=user.id, assigned_at=now
                )
            )
            result.synced_members += 1

            if self._is_maintainer_or_owner(member):
                existing_admin = self.db.execute(
                    project_admins.select().where(
                        project_admins.c.project_id == project.id,
                        project_admins.c.user_id == user.id,
                    )
                ).fetchone()
                if not existing_admin:
                    self.db.execute(
                        project_admins.insert().values(
                            project_id=project.id, user_id=user.id, assigned_at=now
                        )
                    )
                result.synced_admins += 1
                if (
                    not user.is_system_admin()
                    and project_admin_role
                    and project_admin_role not in user.roles
                ):
                    user.roles.append(project_admin_role)

        self.db.commit()

    def _clear_project_relations(self, project: Project, result: GitLabSyncResult) -> set[int]:
        """清空项目成员关系，保留系统管理员的项目管理员关系。"""
        old_admin_rows = self.db.execute(
            project_admins.select().where(project_admins.c.project_id == project.id)
        ).fetchall()
        system_admin_ids = {
            user.id
            for user in self.db.query(User)
            .join(User.roles)
            .filter(Role.name == Role.SYSTEM_ADMIN)
            .all()
        }
        for row in old_admin_rows:
            if row.user_id not in system_admin_ids:
                result.removed_admins.append({
                    "project_id": project.id,
                    "user_id": row.user_id,
                })

        # 清空该项目当前成员关系
        self.db.execute(
            project_members.delete().where(project_members.c.project_id == project.id)
        )
        self.db.execute(
            project_admins.delete().where(
                (project_admins.c.project_id == project.id)
                & (~project_admins.c.user_id.in_(system_admin_ids or {-1}))
            )
        )

        return system_admin_ids

    def _find_or_create_user(
        self,
        member: Dict[str, Any],
        default_password: Optional[str],
        result: GitLabSyncResult,
    ) -> Optional[User]:
        """查找或创建本地用户。"""
        email = (member.get("email") or "").strip()
        username = (member.get("username") or "").strip()

        # 按邮箱匹配
        user = self.db.query(User).filter(User.email == email).first() if email else None
        # 兜底按用户名匹配
        if not user and username:
            user = self.db.query(User).filter(User.username == username).first()

        if user:
            # 补齐空字段
            if not user.email and email:
                user.email = email
            if not user.nickname and member.get("name"):
                user.nickname = member.get("name")
            self.db.commit()
            return user

        # 新用户
        if not default_password:
            result.skipped_users += 1
            return None

        base_username = username or f"gitlab-{member.get('id')}"
        final_username = base_username
        if self.db.query(User).filter(User.username == final_username).first():
            final_username = f"{base_username}-{member.get('id')}"

        user = User(
            username=final_username,
            nickname=member.get("name") or final_username,
            email=email or None,
            password_hash=security_service.hash_password(default_password),
            is_active=True,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        result.created_users += 1
        return user

    @staticmethod
    def _should_sync_member(member: Dict[str, Any]) -> bool:
        """禁用用户和 Bot 账号不参与同步。"""
        state = str(member.get("state") or "").strip().lower()
        if state and state != "active":
            return False

        bot_value = member.get("bot")
        if isinstance(bot_value, str):
            is_bot = bot_value.strip().lower() in {"1", "true", "yes", "y"}
        else:
            is_bot = bool(bot_value)
        if is_bot:
            return False

        user_type = str(member.get("user_type") or "").strip().lower()
        if user_type in {"bot", "project_bot", "group_bot", "security_policy_bot"}:
            return False
        if user_type.endswith("_bot"):
            return False

        username = str(member.get("username") or "").strip().lower()
        if username.endswith("_bot") or username.endswith("-bot") or username.endswith("[bot]"):
            return False

        return True

    @staticmethod
    def _is_maintainer_or_owner(member: Dict[str, Any]) -> bool:
        """判断是否为 Maintainer 或 Owner（含直接成员和继承成员）。

        只要 access_level >= MAINTAINER (40)，无论来源是直接成员还是 Group 继承，
        都应赋予项目管理员权限。
        """
        access_level = member.get("access_level") or 0
        return access_level >= MAINTAINER


def run_gitlab_sync(db: Session) -> GitLabSyncResult:
    """便捷入口：执行 GitLab 项目及成员同步。"""
    service = GitLabProjectMemberSyncService(db)
    return service.sync()

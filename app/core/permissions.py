"""
权限计算纯函数模块

所有权限判断逻辑集中于此，供各 API 模块调用。
函数签名统一：输入 user + db（或 user + 参数），输出 project_ids / bool。
无 HTTP 依赖，不抛 HTTPException。
"""
from __future__ import annotations

from typing import Optional, Set

from sqlalchemy import false, func, or_
from sqlalchemy.orm import Session

from app.models.user import User, project_admins, project_members


# ──────────────── 项目范围计算 ────────────────


def get_readable_project_ids(user: User, db: Session) -> Optional[Set[int]]:
    """返回用户可读的项目 ID 集合。

    - system_admin → None（无限制）
    - project_admin → project_admins ∪ project_members
    - 无角色普通用户 → project_members
    """
    if user.is_system_admin():
        return None  # 语义：不限制

    ids: Set[int] = set()

    # 所有非 system_admin 用户都查 project_members
    member_ids = {
        r[0]
        for r in db.execute(
            project_members.select().where(project_members.c.user_id == user.id)
        ).fetchall()
    }
    ids |= member_ids

    # project_admin 额外包含 project_admins
    if user.is_project_admin():
        admin_ids = {
            r[0]
            for r in db.execute(
                project_admins.select().where(project_admins.c.user_id == user.id)
            ).fetchall()
        }
        ids |= admin_ids

    return ids


def get_writable_project_ids(user: User, db: Session) -> Optional[Set[int]]:
    """返回用户可写的项目 ID 集合。

    - system_admin → None（无限制）
    - project_admin → project_admins 仅
    - 无角色普通用户 → 空集合
    """
    if user.is_system_admin():
        return None  # 语义：不限制

    if user.is_project_admin():
        return {
            r[0]
            for r in db.execute(
                project_admins.select().where(project_admins.c.user_id == user.id)
            ).fetchall()
        }

    return set()


def can_read_project(user: User, project_id: int, db: Session) -> bool:
    """检查用户是否可读指定项目。"""
    readable = get_readable_project_ids(user, db)
    if readable is None:
        return True  # system_admin
    return project_id in readable


def can_write_project(user: User, project_id: int, db: Session) -> bool:
    """检查用户是否可写指定项目。"""
    writable = get_writable_project_ids(user, db)
    if writable is None:
        return True  # system_admin
    return project_id in writable


# ──────────────── 人员能效详情权限 ────────────────


def can_view_person_detail(
    user: User, target_email: str, db: Session
) -> bool:
    """检查用户是否可查看指定人员的能效详情。

    - system_admin → True
    - project_admin → 自己管理项目内成员 OR 自己
    - 普通用户 → 仅自己
    """
    if user.is_system_admin():
        return True

    # 自己看自己始终允许
    if is_self_identity(user, target_email):
        return True

    if user.is_project_admin():
        # project_admin 只能查看自己管理项目内的成员详情
        admin_ids = {
            r[0]
            for r in db.execute(
                project_admins.select().where(project_admins.c.user_id == user.id)
            ).fetchall()
        }
        if not admin_ids:
            return False

        target_user_ids = _find_user_ids_by_identity(target_email, db)
        if not target_user_ids:
            return False

        # 检查目标用户是否是自己管理项目的成员
        member_exists = db.execute(
            project_members.select().where(
                project_members.c.project_id.in_(admin_ids),
                project_members.c.user_id.in_(target_user_ids),
            )
        ).fetchone()
        if member_exists:
            return True

        # 检查目标用户是否是自己管理项目的管理员
        admin_exists = db.execute(
            project_admins.select().where(
                project_admins.c.project_id.in_(admin_ids),
                project_admins.c.user_id.in_(target_user_ids),
            )
        ).fetchone()
        return admin_exists is not None

    return False


def can_view_person_detail_for_project(
    user: User, target_email: str, project_id: int, db: Session
) -> bool:
    """检查用户是否可查看指定项目中指定人员的能效详情。

    - system_admin → True
    - project_admin → 如果是该项目管理员则可查看，否则只能查看自己
    - 普通用户 → 仅自己
    """
    if user.is_system_admin():
        return True

    # 自己看自己始终允许
    if is_self_identity(user, target_email):
        return True

    if user.is_project_admin():
        # 检查用户是否是该项目的管理员
        is_project_admin_of = db.execute(
            project_admins.select().where(
                project_admins.c.user_id == user.id,
                project_admins.c.project_id == project_id,
            )
        ).fetchone()
        return is_project_admin_of is not None

    return False


def _find_user_ids_by_identity(identity: str, db: Session) -> Set[int]:
    """按 email/username/nickname 精确查找本地用户。"""
    if not identity:
        return set()
    target = identity.strip().lower()
    if not target:
        return set()

    return {
        row[0]
        for row in db.query(User.id)
        .filter(
            or_(
                func.lower(User.email) == target,
                func.lower(User.username) == target,
                func.lower(User.nickname) == target,
            )
        )
        .all()
    }


# ──────────────── 身份判断 ────────────────


def is_self_identity(user: User, email_or_author: str) -> bool:
    """判断 email_or_author 是否指向当前用户自身。

    优先邮箱匹配；邮箱缺失时用 username/nickname 兜底。
    """
    if not email_or_author:
        return False
    target = email_or_author.strip().lower()
    if not target:
        return False
    identities = get_user_identity_values(user)
    if target in identities:
        return True
    if user.email and f"<{user.email.lower()}>" in target:
        return True
    return False


def get_user_identity_values(user: User) -> Set[str]:
    """返回可用于匹配提交者/报告作者的当前用户身份值。"""
    values = {
        value.strip().lower()
        for value in (user.email, user.username, user.nickname)
        if value and value.strip()
    }
    return values


def should_limit_to_self(user: User) -> bool:
    """普通无角色用户需要按作者限制到本人数据。"""
    return not user.is_system_admin() and not user.is_project_admin()


def should_limit_to_self_for_project(user: User, project_id: int, db: Session) -> bool:
    """按项目判断用户是否需要限制到本人数据。

    - system_admin → False（不限制）
    - project_admin → 如果是该项目的管理员则不限制，否则限制到自己
    - 普通用户 → 限制到自己
    """
    if user.is_system_admin():
        return False

    if user.is_project_admin():
        # 检查用户是否是该项目的管理员
        is_project_admin_of = db.execute(
            project_admins.select().where(
                project_admins.c.user_id == user.id,
                project_admins.c.project_id == project_id,
            )
        ).fetchone()
        return is_project_admin_of is None  # 不是该项目管理员则限制到自己

    return True  # 普通用户限制到自己


def author_matches_user_condition(column, user: User):
    """生成 SQLAlchemy 条件：作者字段指向当前用户本人。"""
    identities = get_user_identity_values(user)
    conds = [func.lower(column) == identity for identity in identities]
    if user.email:
        conds.append(func.lower(column).like(f"%<{user.email.lower()}>%"))
    if not conds:
        return false()
    return or_(*conds)

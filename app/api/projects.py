# app/api/projects.py
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Set
from app.database import get_db
from app.api.deps import get_current_user
from app.models import Project, User, Role
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse
from app.schemas.response import ApiResponse
from app.services.gitlab_client import GitLabClient, GitLabAuthError, GitLabConnectionError
from app.security import security_service
from app.api.deps import get_current_user_full, require_system_admin, require_project_admin
from app.core.permissions import get_readable_project_ids, can_read_project, can_write_project

router = APIRouter(prefix="/api/projects", tags=["projects"])

# 项目级需要加密存储的敏感字段
_PROJECT_SENSITIVE_FIELDS = ['svn_password', 'wecom_webhook_url']


def _encrypt_project_sensitive(data: dict) -> None:
    """就地加密项目敏感字段：
    - 空值 → 移除（保留数据库原值）
    - 已是加密串(gAAAAAB开头) → 不重复加密
    - 其他值 → 加密后存储（防止明文泄露）
    """
    for field in _PROJECT_SENSITIVE_FIELDS:
        if field in data:
            value = data[field]
            if not value:
                data.pop(field)
            elif not value.startswith("gAAAAAB"):
                # 未加密的值才需要加密（排除双重加密、排除已是明文URL的情况）
                data[field] = security_service.encrypt(value)
            # 已加密的不动


_PROJECT_NAME_UNSAFE_RE = re.compile(r"[^\w\-\u4e00-\u9fa5\s]+")
_PROJECT_NAME_SPACE_RE = re.compile(r"\s+")


def _sanitize_project_name(value: str, fallback: str) -> str:
    """转换为满足 Project.name 校验规则的安全名称。"""
    raw = (value or fallback or "project").replace("/", " ")
    sanitized = _PROJECT_NAME_UNSAFE_RE.sub(" ", raw)
    sanitized = _PROJECT_NAME_SPACE_RE.sub(" ", sanitized).strip()
    if not sanitized:
        sanitized = fallback or "project"
    return sanitized[:100].strip() or "project"


def _make_unique_project_name(base_name: str, existing_names: Set[str], gitlab_project_id: int) -> str:
    """生成不超过 100 字符且不与本地项目重名的项目名称。"""
    candidate = base_name[:100].strip() or f"project {gitlab_project_id}"
    if candidate not in existing_names:
        existing_names.add(candidate)
        return candidate

    counter = 1
    while True:
        suffix = f" {gitlab_project_id}" if counter == 1 else f" {gitlab_project_id}-{counter}"
        prefix_length = max(1, 100 - len(suffix))
        candidate = f"{base_name[:prefix_length].rstrip()}{suffix}"
        if candidate not in existing_names:
            existing_names.add(candidate)
            return candidate
        counter += 1


def _select_project_name(gitlab_project: Dict[str, Any], existing_names: Set[str]) -> str:
    """根据 GitLab 项目信息选择本地唯一项目名。"""
    gitlab_project_id = int(gitlab_project["id"])
    fallback = f"project {gitlab_project_id}"
    name = _sanitize_project_name(gitlab_project.get("name") or "", fallback)
    if name in existing_names:
        namespace_name = _sanitize_project_name(
            gitlab_project.get("path_with_namespace") or "",
            name,
        )
        name = namespace_name
    return _make_unique_project_name(name, existing_names, gitlab_project_id)


def _filter_projects_by_permission(projects: List[Project], user: User, db: Session) -> List[Project]:
    """根据用户权限过滤项目列表"""
    readable_ids = get_readable_project_ids(user, db)
    if readable_ids is None:
        # system_admin：看所有项目
        return projects
    if not readable_ids:
        return []
    return [p for p in projects if p.id in readable_ids]


def _check_project_permission(user: User, project_id: int, require_write: bool = False, db: Session = None) -> bool:
    """检查用户对项目的权限"""
    if require_write:
        return can_write_project(user, project_id, db) if db else user.is_system_admin()
    return can_read_project(user, project_id, db) if db else user.is_system_admin()


@router.get("", response_model=ApiResponse[List[ProjectResponse]])
async def list_projects(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """List projects - 根据权限过滤"""
    projects = db.query(Project).all()
    # 根据权限过滤
    filtered = _filter_projects_by_permission(projects, current_user, db)
    return ApiResponse(success=True, data=filtered)


@router.post("", response_model=ApiResponse[ProjectResponse])
async def create_project(
    data: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_admin),
):
    """Create a new project - 项目管理员或系统管理员"""
    # 项目管理员创建项目后自动成为该项目的管理员
    existing = db.query(Project).filter(Project.name == data.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="项目名称已存在")

    # 检查 GitLab 项目 ID 唯一性
    existing_gitlab_id = db.query(Project).filter(Project.project_id == data.project_id).first()
    if existing_gitlab_id:
        raise HTTPException(status_code=400, detail=f"GitLab 项目 ID {data.project_id} 已存在（项目：{existing_gitlab_id.name}），不能重复添加")

    create_data = data.model_dump()

    # 敏感字段加密存储
    _encrypt_project_sensitive(create_data)

    project = Project(**create_data)
    db.add(project)
    db.commit()
    db.refresh(project)
    
    # 如果不是系统管理员，自动将自己设为项目管理员
    if not current_user.is_system_admin():
        from app.models.user import project_admins
        from datetime import datetime
        stmt = project_admins.insert().values(
            project_id=project.id,
            user_id=current_user.id,
            assigned_by=current_user.id,
            assigned_at=int(datetime.now().timestamp())
        )
        db.execute(stmt)
        db.commit()
    
    return ApiResponse(success=True, data=project, message="项目创建成功")


@router.post("/sync-gitlab")
async def sync_gitlab_projects(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """同步 GitLab 项目及成员，仅系统管理员可操作。"""
    from app.services.gitlab_sync_service import run_gitlab_sync

    try:
        result = run_gitlab_sync(db)
    except ValueError as exc:
        return ApiResponse(success=False, message=str(exc))

    return ApiResponse(
        success=True,
        message="GitLab 项目及成员同步完成",
        data=result.to_dict(),
    )


@router.get("/{project_id}", response_model=ApiResponse[ProjectResponse])
async def get_project(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """Get a specific project by ID"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查查看权限
    if not _check_project_permission(current_user, project_id, require_write=False, db=db):
        raise HTTPException(status_code=403, detail="您没有权限查看此项目")
    
    return ApiResponse(success=True, data=project)


@router.put("/{project_id}", response_model=ApiResponse[ProjectResponse])
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_admin),
):
    """Update a project - 需要项目管理员权限"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 检查写入权限
    if not _check_project_permission(current_user, project_id, require_write=True, db=db):
        raise HTTPException(status_code=403, detail="您没有权限修改此项目")

    update_data = data.model_dump(exclude_unset=True)

    # 敏感字段加密处理
    _encrypt_project_sensitive(update_data)

    for key, value in update_data.items():
        setattr(project, key, value)

    db.commit()
    db.refresh(project)
    return ApiResponse(success=True, data=project, message="项目已更新")


@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """Delete a project - 仅系统管理员"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    db.delete(project)
    db.commit()
    return ApiResponse(success=True, message="项目已删除")


@router.patch("/{project_id}/toggle", response_model=ApiResponse[ProjectResponse])
async def toggle_project(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_admin),
):
    """Toggle project active status - 需要项目管理员权限"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 检查写入权限
    if not _check_project_permission(current_user, project_id, require_write=True, db=db):
        raise HTTPException(status_code=403, detail="您没有权限修改此项目")

    project.is_active = not project.is_active
    db.commit()
    db.refresh(project)

    status_text = "已启用" if project.is_active else "已禁用"
    return ApiResponse(
        success=True,
        data=project,
        message=f"项目{status_text}"
    )


@router.post("/{project_id}/test")
async def test_project_connection(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """Test GitLab connection for a specific project"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查查看权限
    if not _check_project_permission(current_user, project_id, require_write=False, db=db):
        raise HTTPException(status_code=403, detail="您没有权限查看此项目")

    from app.models.settings import Settings
    settings = db.query(Settings).first()

    # 使用全局 Token
    token = None
    if settings and settings.global_gitlab_token:
        try:
            token = security_service.decrypt(settings.global_gitlab_token)
        except ValueError:
            pass

    if not token:
        return ApiResponse(success=False, message="全局 GitLab Token 未配置，请在系统设置中配置")

    gitlab_url = project.gitlab_url or (settings.global_gitlab_url if settings else None)
    if not gitlab_url:
        return ApiResponse(success=False, message="未配置 GitLab URL")

    try:
        client = GitLabClient(gitlab_url=gitlab_url, access_token=token)
        connected = client.test_connection()
        if connected:
            # 进一步验证项目 ID 是否可访问
            project_info = client.get_project_info(project.project_id)
            if project_info:
                return ApiResponse(
                    success=True,
                    message=f"GitLab 连接成功，项目: {project_info.get('name', project.name)}"
                )
            return ApiResponse(success=False, message=f"GitLab 连接成功，但项目 ID {project.project_id} 不可访问")
        return ApiResponse(success=False, message="GitLab 连接失败")
    except GitLabAuthError as e:
        return ApiResponse(
            success=False,
            message=f"GitLab 认证失败: Token 无效或已过期，请在设置中更新 Token"
        )
    except GitLabConnectionError as e:
        return ApiResponse(
            success=False,
            message=f"无法连接到 GitLab: 请检查 GitLab URL 是否正确 ({gitlab_url})"
        )
    except Exception as e:
        return ApiResponse(success=False, message=f"GitLab 连接异常: {str(e)}")


# ==================== 项目成员管理 ====================

@router.get("/{project_id}/members")
async def get_project_members(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """获取项目成员列表"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查查看权限
    if not _check_project_permission(current_user, project_id, require_write=False, db=db):
        raise HTTPException(status_code=403, detail="您没有权限查看此项目")
    
    from app.models.user import project_admins, project_members
    
    # 查询管理员
    admin_stmt = project_admins.select().where(project_admins.c.project_id == project_id)
    admin_rows = db.execute(admin_stmt).fetchall()
    admin_users = []
    for row in admin_rows:
        u = db.query(User).filter(User.id == row.user_id).first()
        if u:
            admin_users.append({"id": u.id, "username": u.username, "nickname": u.nickname, "role": "admin"})
    
    # 查询成员
    member_stmt = project_members.select().where(project_members.c.project_id == project_id)
    member_rows = db.execute(member_stmt).fetchall()
    member_users = []
    for row in member_rows:
        u = db.query(User).filter(User.id == row.user_id).first()
        if u:
            member_users.append({"id": u.id, "username": u.username, "nickname": u.nickname, "role": "member"})
    
    return {
        "success": True,
        "data": {
            "admin_users": admin_users,
            "member_users": member_users,
        }
    }


@router.post("/{project_id}/members")
async def add_project_member(
    project_id: int,
    user_id: int,
    role: str = "member",  # "admin" or "member"
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_admin),
):
    """添加项目成员 - 项目管理员或系统管理员"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限
    if not current_user.is_system_admin() and not _check_project_permission(current_user, project_id, require_write=True, db=db):
        raise HTTPException(status_code=403, detail="您没有权限管理此项目的成员")
    
    from app.models.user import project_admins, project_members
    from app.models import Role
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    if role == "admin":
        # 检查是否已在关联中
        stmt = project_admins.select().where(
            (project_admins.c.project_id == project_id) & 
            (project_admins.c.user_id == user_id)
        )
        if not db.execute(stmt).fetchone():
            from datetime import datetime
            stmt = project_admins.insert().values(
                project_id=project_id,
                user_id=user_id,
                assigned_by=current_user.id,
                assigned_at=int(datetime.now().timestamp())
            )
            db.execute(stmt)
        
        # 确保有项目管理员角色
        pa_role = db.query(Role).filter(Role.name == Role.PROJECT_ADMIN).first()
        if pa_role and pa_role not in target_user.roles:
            target_user.roles.append(pa_role)
    else:
        # 项目成员由 GitLab 同步维护，不再支持手动添加
        raise HTTPException(
            status_code=400,
            detail="项目成员由 GitLab 同步维护，不支持手动添加。请通过同步 GitLab 项目及成员功能管理。"
        )
    
    db.commit()
    
    return {"success": True, "message": f"已添加 {target_user.username} 为项目{'管理员' if role == 'admin' else '成员'}"}


@router.delete("/{project_id}/members/{user_id}")
async def remove_project_member(
    project_id: int,
    user_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_admin),
):
    """移除项目成员 - 项目管理员或系统管理员"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 检查权限
    if not current_user.is_system_admin() and not _check_project_permission(current_user, project_id, require_write=True, db=db):
        raise HTTPException(status_code=403, detail="您没有权限管理此项目的成员")
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 项目成员由 GitLab 同步维护，不再支持手动移除
    raise HTTPException(
        status_code=400,
        detail="项目成员由 GitLab 同步维护，不支持手动移除。请通过同步 GitLab 项目及成员功能管理。"
    )

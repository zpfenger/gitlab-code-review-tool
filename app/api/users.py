"""
用户管理 API
"""
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Role, Project
from app.models.user import project_admins, project_members
from app.security import security_service
from app.api.deps import get_current_user

router = APIRouter(prefix="/api/users", tags=["users"])


# ==================== Schema 定义 ====================

class UserCreate(BaseModel):
    username: str
    password: str
    nickname: Optional[str] = None
    email: Optional[str] = None
    role_names: List[str] = []

    class Config:
        json_schema_extra = {
            "example": {
                "username": "john",
                "password": "secure123",
                "nickname": "John Doe",
                "email": "john@example.com",
                "role_names": ["project_admin"]
            }
        }


class UserUpdate(BaseModel):
    nickname: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordChangeAdmin(BaseModel):
    new_password: str


class UserResponse(BaseModel):
    id: int
    username: str
    nickname: Optional[str]
    email: Optional[str]
    is_active: bool
    roles: List[str]
    created_at: str

    class Config:
        from_attributes = True


class UserDetailResponse(BaseModel):
    id: int
    username: str
    nickname: Optional[str]
    email: Optional[str]
    is_active: bool
    roles: List[dict]
    admin_projects: List[dict]
    member_projects: List[dict]
    created_at: str

    @classmethod
    def from_user(cls, user: User, db: Session) -> "UserDetailResponse":
        """从 User 对象构建响应，包含数据库中的项目关联"""
        # 查询项目关联
        admin_stmt = project_admins.select().where(project_admins.c.user_id == user.id)
        admin_project_ids = [row[0] for row in db.execute(admin_stmt).fetchall()]
        admin_projects = db.query(Project).filter(Project.id.in_(admin_project_ids)).all() if admin_project_ids else []
        
        member_stmt = project_members.select().where(project_members.c.user_id == user.id)
        member_project_ids = [row[0] for row in db.execute(member_stmt).fetchall()]
        member_projects = db.query(Project).filter(Project.id.in_(member_project_ids)).all() if member_project_ids else []
        
        return cls(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            email=user.email,
            is_active=user.is_active,
            roles=[{"id": r.id, "name": r.name, "description": r.description} for r in user.roles],
            admin_projects=[{"id": p.id, "name": p.name} for p in admin_projects],
            member_projects=[{"id": p.id, "name": p.name} for p in member_projects],
            created_at=str(user.created_at) if user.created_at else "",
        )


# ==================== 依赖：获取当前用户 ====================

def get_current_user_full(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """获取当前登录用户的完整 User 对象"""
    username = request.session.get("user")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )
    return user


def require_system_admin(current_user: User = Depends(get_current_user_full)):
    """要求是系统管理员"""
    if not current_user.is_system_admin():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要系统管理员权限"
        )
    return current_user


def require_project_admin(current_user: User = Depends(get_current_user_full)):
    """要求是项目管理员或系统管理员"""
    if not (current_user.is_system_admin() or current_user.is_project_admin()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要项目管理员或系统管理员权限"
        )
    return current_user


# ==================== 用户 CRUD ====================

@router.get("", response_model=List[UserResponse])
async def list_users(
    request: Request,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """获取用户列表（仅系统管理员）"""
    query = db.query(User)
    
    if search:
        query = query.filter(
            (User.username.contains(search)) |
            (User.nickname.contains(search)) |
            (User.email.contains(search))
        )
    
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    
    users = query.order_by(User.created_at.desc()).all()
    
    return [
        UserResponse(
            id=u.id,
            username=u.username,
            nickname=u.nickname,
            email=u.email,
            is_active=u.is_active,
            roles=[r.name for r in u.roles],
            created_at=str(u.created_at) if u.created_at else "",
        )
        for u in users
    ]


@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """获取用户详情（仅系统管理员）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    return UserDetailResponse.from_user(user, db)


@router.post("", response_model=UserResponse)
async def create_user(
    data: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """创建新用户（仅系统管理员）"""
    # 检查用户名是否已存在
    existing = db.query(User).filter(User.username == data.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")
    
    # 验证密码强度
    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于6个字符")
    
    # 创建用户
    user = User(
        username=data.username,
        password_hash=security_service.hash_password(data.password),
        nickname=data.nickname,
        email=data.email,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    # 分配角色
    for role_name in data.role_names:
        role = db.query(Role).filter(Role.name == role_name).first()
        if role:
            user.roles.append(role)
    
    db.commit()
    db.refresh(user)
    
    return UserResponse(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        email=user.email,
        is_active=user.is_active,
        roles=[r.name for r in user.roles],
        created_at=str(user.created_at) if user.created_at else "",
    )


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """更新用户信息（仅系统管理员）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 系统管理员不能被禁用
    if user.is_system_admin() and data.is_active is False:
        raise HTTPException(status_code=400, detail="不能禁用系统管理员账号")
    
    if data.nickname is not None:
        user.nickname = data.nickname
    if data.email is not None:
        user.email = data.email
    if data.is_active is not None:
        user.is_active = data.is_active
    
    db.commit()
    db.refresh(user)
    
    return UserResponse(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        email=user.email,
        is_active=user.is_active,
        roles=[r.name for r in user.roles],
        created_at=str(user.created_at) if user.created_at else "",
    )


@router.delete("/{user_id}")
async def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """删除用户（仅系统管理员）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 系统管理员不能被删除
    if user.is_system_admin():
        raise HTTPException(status_code=400, detail="不能删除系统管理员账号")
    
    # 不能删除自己
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能删除自己的账号")
    
    db.delete(user)
    db.commit()
    
    return {"success": True, "message": "用户已删除"}


@router.put("/{user_id}/password")
async def reset_password(
    user_id: int,
    data: PasswordChangeAdmin,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """重置用户密码（仅系统管理员）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="密码长度不能少于6个字符")
    
    user.password_hash = security_service.hash_password(data.new_password)
    db.commit()
    
    return {"success": True, "message": "密码已重置"}


@router.put("/{user_id}/roles")
async def update_user_roles(
    user_id: int,
    role_names: List[str],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """更新用户角色（系统管理员或项目管理员可分配角色）"""
    # 非管理员无权分配角色
    if not current_user.is_system_admin() and not current_user.is_project_admin():
        raise HTTPException(status_code=403, detail="您没有权限分配角色")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 系统管理员角色只能由系统管理员分配
    if Role.SYSTEM_ADMIN in role_names and not current_user.is_system_admin():
        raise HTTPException(status_code=403, detail="系统管理员角色只能由系统管理员分配")

    # 系统管理员角色不能被移除
    if user.is_system_admin() and Role.SYSTEM_ADMIN not in role_names:
        raise HTTPException(status_code=400, detail="不能移除系统管理员的角色")

    # 不能修改自己的角色
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="不能修改自己的角色")

    # 先构建新角色列表，再一次性替换（事务安全）
    new_roles = []
    for role_name in role_names:
        role = db.query(Role).filter(Role.name == role_name).first()
        if role:
            new_roles.append(role)

    user.roles = new_roles

    db.commit()

    return {
        "success": True,
        "message": "角色已更新",
        "roles": [r.name for r in user.roles],
    }


# ==================== 项目权限分配 ====================

@router.post("/{user_id}/projects/admin")
async def assign_admin_projects(
    user_id: int,
    project_ids: List[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """分配用户管理的项目（仅系统管理员）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 必须有项目管理员角色
    project_admin_role = db.query(Role).filter(Role.name == Role.PROJECT_ADMIN).first()
    if project_admin_role not in user.roles:
        user.roles.append(project_admin_role)
    
    # 清除旧的项目管理员关联
    stmt = project_admins.delete().where(project_admins.c.user_id == user_id)
    db.execute(stmt)
    
    # 添加新的项目关联
    from datetime import datetime
    for pid in project_ids:
        stmt = project_admins.insert().values(
            project_id=pid,
            user_id=user_id,
            assigned_by=current_user.id,
            assigned_at=int(datetime.now().timestamp())
        )
        db.execute(stmt)
    
    db.commit()
    
    # 返回更新后的项目列表
    assigned_projects = db.query(Project).filter(Project.id.in_(project_ids)).all() if project_ids else []
    return {
        "success": True,
        "message": "项目管理员权限已更新",
        "admin_projects": [{"id": p.id, "name": p.name} for p in assigned_projects],
    }


@router.post("/{user_id}/projects/member")
async def assign_member_projects(
    user_id: int,
    project_ids: List[int],
    db: Session = Depends(get_db),
    current_user: User = Depends(require_project_admin),
):
    """分配用户查看的项目（项目管理员或系统管理员）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    # 系统管理员可以分配任何项目
    # 项目管理员只能分配自己有权限的项目
    if not current_user.is_system_admin():
        # 获取当前用户作为管理员的项目ID
        admin_stmt = project_admins.select().where(project_admins.c.user_id == current_user.id)
        admin_project_ids = [row[0] for row in db.execute(admin_stmt).fetchall()]
        for pid in project_ids:
            if pid not in admin_project_ids:
                raise HTTPException(
                    status_code=403,
                    detail=f"您没有项目 {pid} 的管理权限"
                )
    
    # 必须有项目成员角色
    member_role = db.query(Role).filter(Role.name == Role.PROJECT_MEMBER).first()
    if member_role not in user.roles:
        user.roles.append(member_role)
    
    # 清除旧的项目成员关联
    stmt = project_members.delete().where(project_members.c.user_id == user_id)
    db.execute(stmt)
    
    # 添加新的项目关联
    from datetime import datetime
    for pid in project_ids:
        stmt = project_members.insert().values(
            project_id=pid,
            user_id=user_id,
            assigned_by=current_user.id,
            assigned_at=int(datetime.now().timestamp())
        )
        db.execute(stmt)
    
    db.commit()
    
    # 返回更新后的项目列表
    assigned_projects = db.query(Project).filter(Project.id.in_(project_ids)).all() if project_ids else []
    return {
        "success": True,
        "message": "项目成员权限已更新",
        "member_projects": [{"id": p.id, "name": p.name} for p in assigned_projects],
    }


# ==================== 当前用户信息 ====================

@router.get("/me/profile")
async def get_my_profile(
    request: Request,
    db: Session = Depends(get_db),
):
    """获取当前用户信息"""
    username = request.session.get("user")
    if not username:
        raise HTTPException(status_code=401, detail="未登录")
    
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    
    return {
        "id": user.id,
        "username": user.username,
        "nickname": user.nickname,
        "email": user.email,
        "is_active": user.is_active,
        "roles": [r.name for r in user.roles],
        "is_system_admin": user.is_system_admin(),
        "is_project_admin": user.is_project_admin(),
        "is_project_member": user.is_project_member(),
    }


@router.put("/me/profile")
async def update_my_profile(
    nickname: Optional[str] = None,
    email: Optional[str] = None,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """更新当前用户个人信息"""
    username = request.session.get("user")
    if not username:
        raise HTTPException(status_code=401, detail="未登录")
    
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    
    if nickname is not None:
        user.nickname = nickname
    if email is not None:
        user.email = email
    
    db.commit()
    
    return {"success": True, "message": "个人信息已更新"}


@router.put("/me/password")
async def change_my_password(
    old_password: str,
    new_password: str,
    confirm_password: str,
    request: Request = None,
    db: Session = Depends(get_db),
):
    """修改当前用户密码"""
    username = request.session.get("user")
    if not username:
        raise HTTPException(status_code=401, detail="未登录")
    
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的新密码不一致")
    
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度不能少于6个字符")
    
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    
    if not security_service.verify_password(old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码错误")
    
    user.password_hash = security_service.hash_password(new_password)
    db.commit()
    
    return {"success": True, "message": "密码修改成功"}

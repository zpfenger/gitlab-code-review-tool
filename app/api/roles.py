"""
角色管理 API
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Role, Project
from app.api.users import require_system_admin, get_current_user_full

router = APIRouter(prefix="/api/roles", tags=["roles"])


class RoleResponse(BaseModel):
    id: int
    name: str
    description: str | None
    is_system_role: bool
    user_count: int = 0


class RoleDetailResponse(BaseModel):
    id: int
    name: str
    description: str | None
    is_system_role: bool
    users: List[dict]
    created_at: str


# ==================== 角色列表 ====================

@router.get("", response_model=List[RoleResponse])
async def list_roles(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """获取角色列表（仅系统管理员）"""
    roles = db.query(Role).order_by(Role.is_system_role.desc(), Role.name).all()
    
    result = []
    for role in roles:
        result.append(RoleResponse(
            id=role.id,
            name=role.name,
            description=role.description,
            is_system_role=role.is_system_role,
            user_count=len(role.users),
        ))
    
    return result


@router.get("/{role_id}", response_model=RoleDetailResponse)
async def get_role(
    role_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    """获取角色详情（仅系统管理员）"""
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    return RoleDetailResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        is_system_role=role.is_system_role,
        users=[
            {
                "id": u.id,
                "username": u.username,
                "nickname": u.nickname,
                "is_active": u.is_active,
            }
            for u in role.users
        ],
        created_at=str(role.created_at) if role.created_at else "",
    )


# ==================== 内置角色定义（前端展示用） ====================

@router.get("/definitions/builtin")
async def get_builtin_role_definitions():
    """获取内置角色定义说明（所有人可见）"""
    return [
        {
            "name": Role.SYSTEM_ADMIN,
            "display_name": "系统管理员",
            "description": "拥有系统全部权限",
            "permissions": [
                "查看和管理所有项目",
                "系统设置管理",
                "账号管理（创建、修改、删除用户）",
                "权限管理（分配角色、项目权限）",
                "查看所有操作日志",
            ],
        },
        {
            "name": Role.PROJECT_ADMIN,
            "display_name": "项目管理员",
            "description": "可管理被授权的项目",
            "permissions": [
                "查看授权项目的代码评审数据",
                "创建和修改授权项目",
                "配置项目通知",
                "分配项目成员查看权限",
                "不能访问其他项目",
            ],
        },
        {
            "name": Role.PROJECT_MEMBER,
            "display_name": "项目成员",
            "description": "只能查看被授权的项目",
            "permissions": [
                "查看授权项目的代码评审数据",
                "查看报告",
                "不能修改任何数据",
            ],
        },
    ]

# app/api/auth.py
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from typing import Optional
from app.config import config_manager
from app.security import security_service
from app.api.deps import get_current_user
from app.schemas.response import ApiResponse
from app.database import get_db
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    message: str = ""
    user: Optional[dict] = None


class ProfileUpdateRequest(BaseModel):
    nickname: Optional[str] = None
    email: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str
    confirm_password: str


@router.post("/login", response_model=LoginResponse)
async def login(request: Request, data: LoginRequest, db=Depends(get_db)):
    # 优先从数据库用户表登录
    user = db.query(User).filter(User.username == data.username).first()
    
    if user:
        # 数据库用户登录
        if not user.is_active:
            raise HTTPException(status_code=401, detail="账号已被禁用")
        
        if not security_service.verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        
        request.session["user"] = user.username
        request.session["user_id"] = user.id
        return LoginResponse(
            success=True,
            message="登录成功",
            user={
                "username": user.username,
                "nickname": user.nickname,
                "roles": [r.name for r in user.roles],
            }
        )
    
    # 兼容旧版 admin.yaml 登录（首次迁移后不再使用）
    config = config_manager.get_admin_config()
    if data.username != config.username:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not security_service.verify_password(data.password, config.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    request.session["user"] = data.username
    return LoginResponse(success=True, message="登录成功（兼容模式）")


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@router.get("/me")
async def get_me(request: Request, db=Depends(get_db)):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    # 尝试获取数据库用户信息
    db_user = db.query(User).filter(User.username == user).first()
    if db_user:
        return {
            "username": user,
            "nickname": db_user.nickname,
            "email": db_user.email,
            "roles": [r.name for r in db_user.roles],
            "authenticated": True,
        }
    
    return {"username": user, "authenticated": True}


@router.get("/profile")
async def get_profile(current_user: str = Depends(get_current_user), db=Depends(get_db)):
    # 优先从数据库获取
    user = db.query(User).filter(User.username == current_user).first()
    if user:
        return {
            "username": user.username,
            "nickname": user.nickname,
            "email": user.email,
            "roles": [r.name for r in user.roles],
        }
    
    # 兼容旧版
    config = config_manager.get_admin_config()
    return {
        "username": config.username,
        "nickname": config.nickname,
        "email": config.email,
    }


@router.put("/profile")
async def update_profile(
    data: ProfileUpdateRequest,
    current_user: str = Depends(get_current_user),
    db=Depends(get_db),
):
    # 优先更新数据库用户
    user = db.query(User).filter(User.username == current_user).first()
    if user:
        if data.nickname is not None:
            user.nickname = data.nickname
        if data.email is not None:
            user.email = data.email
        db.commit()
        return {"success": True, "message": "个人信息更新成功"}
    
    # 兼容旧版
    config_manager.update_admin_profile(
        nickname=data.nickname,
        email=data.email,
    )
    return {"success": True, "message": "个人信息更新成功"}


@router.put("/password")
async def change_password(
    data: PasswordChangeRequest,
    current_user: str = Depends(get_current_user),
    db=Depends(get_db),
):
    if data.new_password != data.confirm_password:
        return ApiResponse.fail(code="PASSWORD_MISMATCH", message="两次输入的新密码不一致")

    if len(data.new_password) < 6:
        return ApiResponse.fail(code="PASSWORD_TOO_SHORT", message="新密码长度不能少于6个字符")

    # 优先更新数据库用户
    user = db.query(User).filter(User.username == current_user).first()
    if user:
        if not security_service.verify_password(data.old_password, user.password_hash):
            return ApiResponse.fail(code="WRONG_PASSWORD", message="原密码错误")
        
        user.password_hash = security_service.hash_password(data.new_password)
        db.commit()
        return {"success": True, "message": "密码修改成功"}
    
    # 兼容旧版
    config = config_manager.get_admin_config()
    if not security_service.verify_password(data.old_password, config.password_hash):
        return ApiResponse.fail(code="WRONG_PASSWORD", message="原密码错误")

    config_manager.update_admin_password(data.new_password)
    return {"success": True, "message": "密码修改成功"}

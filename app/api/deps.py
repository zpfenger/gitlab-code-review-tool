# app/api/deps.py
from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.config import config_manager
from app.security import security_service
from app.models import User
from typing import Optional


def get_current_user(request: Request) -> str:
    """Get current authenticated user from session (username string)"""
    session_token = request.session.get("user")
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )
    return session_token


def get_current_user_obj(request: Request, db: Session) -> User:
    """Get current authenticated user as User object"""
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


def get_optional_user(request: Request) -> Optional[str]:
    """Get current user if authenticated, otherwise None"""
    return request.session.get("user")


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

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

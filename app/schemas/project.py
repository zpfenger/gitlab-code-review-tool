# app/schemas/project.py
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class ProjectBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, pattern=r'^[\w\-_\u4e00-\u9fa5\s]+$')
    gitlab_url: Optional[str] = Field(None, max_length=500)
    project_id: int = Field(..., gt=0)
    description: Optional[str] = None
    target_branches: Optional[str] = None
    access_token: Optional[str] = Field(None, max_length=500)
    exclude_branches: Optional[str] = None
    svn_url: Optional[str] = Field(None, max_length=500)
    svn_username: Optional[str] = Field(None, max_length=100)
    svn_password: Optional[str] = Field(None, max_length=500)
    wecom_enabled: bool = False
    wecom_webhook_url: Optional[str] = Field(None, max_length=500)
    is_active: bool = True


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100, pattern=r'^[\w\-_\u4e00-\u9fa5\s]+$')
    gitlab_url: Optional[str] = Field(None, max_length=500)
    project_id: Optional[int] = Field(None, gt=0)
    description: Optional[str] = None
    target_branches: Optional[str] = None
    access_token: Optional[str] = None
    exclude_branches: Optional[str] = None
    svn_url: Optional[str] = None
    svn_username: Optional[str] = None
    svn_password: Optional[str] = None
    wecom_enabled: Optional[bool] = None
    wecom_webhook_url: Optional[str] = None
    is_active: Optional[bool] = None


class ProjectResponse(ProjectBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

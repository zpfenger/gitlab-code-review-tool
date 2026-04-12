# app/schemas/task.py
from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class TaskRunRequest(BaseModel):
    project_id: Optional[int] = None
    task_type: str = "daily"


class TaskProgress(BaseModel):
    is_running: bool
    current_project: Optional[str] = None
    branches_processed: int = 0
    commits_processed: int = 0
    reports_generated: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    cancelled: bool = False
    error: Optional[str] = None


class TaskResponse(BaseModel):
    success: bool
    message: str
    progress: Optional[TaskProgress] = None

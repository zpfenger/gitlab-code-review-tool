# app/api/logs.py
from datetime import datetime, date
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.models import User
from app.models.task_log import TaskLog
from app.models.project import Project
from app.schemas.response import ApiResponse, PaginatedResponse
from app.api.users import get_current_user_full
from app.api.projects import _filter_projects_by_permission

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", response_model=PaginatedResponse[dict])
async def list_logs(
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """List task execution logs with optional filters (按权限过滤项目)"""
    # 获取用户有权限的项目 ID 列表
    all_projects = db.query(Project).all()
    allowed_projects = _filter_projects_by_permission(all_projects, current_user, db)
    allowed_project_ids = {p.id for p in allowed_projects}

    query = db.query(TaskLog).filter(TaskLog.project_id.in_(allowed_project_ids))

    # 将空字符串转为 None，并转换类型
    pid = int(project_id) if project_id and project_id.strip() else None
    status = status if status and status.strip() else None
    start_date = start_date if start_date and start_date.strip() else None
    end_date = end_date if end_date and end_date.strip() else None

    # 默认日期范围：当月
    if not start_date:
        today = date.today()
        start_date = today.replace(day=1).isoformat()
    if not end_date:
        end_date = date.today().isoformat()

    # Apply filters
    if pid:
        query = query.filter(TaskLog.project_id == pid)
    if status:
        query = query.filter(TaskLog.status == status)
    if start_date:
        query = query.filter(TaskLog.created_at >= datetime.fromisoformat(start_date))
    if end_date:
        query = query.filter(TaskLog.created_at < datetime.fromisoformat(end_date) + __import__('datetime').timedelta(days=1))

    # Get total count
    total = query.count()

    # Apply pagination and ordering
    logs = query.order_by(TaskLog.created_at.desc()) \
        .offset((page - 1) * page_size) \
        .limit(page_size) \
        .all()

    # Format response
    data = [
        {
            "id": log.id,
            "project_id": log.project_id,
            "project_name": log.project_name or (log.project.name if log.project else f"项目#{log.project_id}"),
            "task_type": log.task_type,
            "trigger_type": log.trigger_type,
            "trigger_user": log.trigger_user,
            "status": log.status,
            "start_time": log.start_time.isoformat() if log.start_time else None,
            "end_time": log.end_time.isoformat() if log.end_time else None,
            "duration": log.duration,
            "branches_processed": log.branches_processed,
            "commits_processed": log.commits_processed,
            "reports_generated": log.reports_generated,
            "error_message": log.error_message,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "updated_at": log.updated_at.isoformat() if log.updated_at else None,
        }
        for log in logs
    ]

    return PaginatedResponse(
        success=True,
        data=data,
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/{log_id}", response_model=ApiResponse[dict])
async def get_log_detail(
    log_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """Get detailed information about a specific log"""
    log = db.query(TaskLog).filter(TaskLog.id == log_id).first()
    if not log:
        return ApiResponse.fail(code="NOT_FOUND", message="Log not found")

    # 检查用户是否有权限查看该日志（项目级权限）
    all_projects = db.query(Project).all()
    allowed_projects = _filter_projects_by_permission(all_projects, current_user, db)
    allowed_project_ids = {p.id for p in allowed_projects}
    if log.project_id not in allowed_project_ids:
        return ApiResponse.fail(code="FORBIDDEN", message="您没有权限查看此日志")

    return ApiResponse(
        success=True,
        data={
            "id": log.id,
            "project_id": log.project_id,
            "project_name": log.project_name or (log.project.name if log.project else f"项目#{log.project_id}"),
            "task_type": log.task_type,
            "trigger_type": log.trigger_type,
            "trigger_user": log.trigger_user,
            "status": log.status,
            "start_time": log.start_time.isoformat() if log.start_time else None,
            "end_time": log.end_time.isoformat() if log.end_time else None,
            "duration": log.duration,
            "branches_processed": log.branches_processed,
            "commits_processed": log.commits_processed,
            "reports_generated": log.reports_generated,
            "error_message": log.error_message,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "updated_at": log.updated_at.isoformat() if log.updated_at else None,
        }
    )

# app/api/reports.py
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import Optional, List, Tuple
from datetime import date
from pathlib import Path
import re
from app.database import get_db
from app.models import User
from app.models.project import Project
from app.api.deps import get_current_user, get_current_user_obj
from app.api.users import get_current_user_full
from app.api.projects import _check_project_permission
from app.schemas.response import ApiResponse


def get_authenticated_user(request: Request, db: Session = Depends(get_db)) -> User:
    """获取已认证用户对象"""
    return get_current_user_obj(request, db)

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Allowed directory for reports
ALLOWED_REPORT_DIR = Path("./data/reports")


def _get_user_allowed_project_names(user: User, db: Session) -> set:
    """获取用户有权限查看的项目名称集合"""
    from app.api.projects import _filter_projects_by_permission
    all_projects = db.query(Project).all()
    allowed_projects = _filter_projects_by_permission(all_projects, user, db)
    return {p.name for p in allowed_projects}


def _validate_path(path: str) -> Path:
    """Validate path to prevent directory traversal"""
    full_path = (ALLOWED_REPORT_DIR / path).resolve()
    if not str(full_path).startswith(str(ALLOWED_REPORT_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid path")
    return full_path


def _sanitize_filename(name: str) -> str:
    """Sanitize filename to prevent path traversal and invalid characters"""
    # Only allow alphanumeric, underscore, hyphen, and dot
    return re.sub(r'[^\w\-.]', '_', name)


@router.get("")
async def list_reports(
    project_id: Optional[int] = None,
    report_date: Optional[date] = None,
    report_type: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    author: Optional[str] = None,
    current_user: User = Depends(get_current_user_full),
    db: Session = Depends(get_db),
):
    """List all available reports with optional filters (按权限过滤)"""
    # 获取用户有权限的项目名称
    allowed_project_names = _get_user_allowed_project_names(current_user, db)

    reports = []

    if not ALLOWED_REPORT_DIR.exists():
        return ApiResponse(success=True, data=reports)

    try:
        for project_dir in ALLOWED_REPORT_DIR.iterdir():
            if not project_dir.is_dir():
                continue

            # 按权限过滤项目
            if project_dir.name not in allowed_project_names:
                continue

            for type_dir in project_dir.iterdir():
                if not type_dir.is_dir():
                    continue

                # Filter by report type
                if report_type and type_dir.name != report_type:
                    continue

                for date_dir in type_dir.iterdir():
                    if not date_dir.is_dir():
                        continue

                    # Filter by date
                    if report_date:
                        try:
                            dir_date = date.fromisoformat(date_dir.name)
                            if dir_date != report_date:
                                continue
                        except ValueError:
                            continue

                    for report_file in date_dir.glob("*.md"):
                        # Filter by author
                        file_author = report_file.stem
                        if author and author.lower() not in file_author.lower():
                            continue

                        reports.append({
                            "project": project_dir.name,
                            "type": type_dir.name,
                            "date": date_dir.name,
                            "author": file_author,
                            "filename": str(report_file.relative_to(ALLOWED_REPORT_DIR)),
                            "size": report_file.stat().st_size,
                        })
    except Exception as e:
        return ApiResponse.fail(code="LIST_ERROR", message=f"Failed to list reports: {str(e)}")

    # Sort by date descending
    reports.sort(key=lambda x: x["date"], reverse=True)

    return ApiResponse(success=True, data=reports)


def _parse_report_path(path: str) -> Tuple[str, str, str, str]:
    """Parse a report path into (project, report_type, report_date, author) components.

    Expected format: {project}/{type}/{date}/{author}.md
    """
    parts = Path(path).parts
    if len(parts) != 4 or not parts[3].endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid report path format")

    project, report_type, report_date, filename = parts
    author = Path(filename).stem

    # Validate report_type
    if report_type not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="Invalid report type")

    # Validate date format (支持单日期和日期区间)
    if not re.match(r"^\d{4}-\d{2}-\d{2}(_to_\d{4}-\d{2}-\d{2})?$", report_date):
        raise HTTPException(status_code=400, detail="Invalid date format")

    return project, report_type, report_date, author


@router.get("/content")
async def get_report_content(
    path: Optional[str] = Query(None, min_length=1),
    project: Optional[str] = Query(None, min_length=1, max_length=100),
    report_type: Optional[str] = Query(None, pattern="^(daily|weekly|monthly)$"),
    report_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    author: Optional[str] = Query(None, min_length=1, max_length=100),
    current_user: User = Depends(get_current_user_full),
    db: Session = Depends(get_db),
):
    """Get content of a specific report"""
    # Support path parameter or individual parameters
    if path:
        project, report_type, report_date, author = _parse_report_path(path)

    if not all([project, report_type, report_date, author]):
        raise HTTPException(status_code=400, detail="Missing required parameters")

    # After validation, values are guaranteed non-None
    assert project is not None and author is not None

    # 检查项目级查看权限
    allowed_project_names = _get_user_allowed_project_names(current_user, db)
    if project not in allowed_project_names:
        raise HTTPException(status_code=403, detail="您没有权限查看此项目的报告")

    # Sanitize inputs to prevent path traversal
    safe_project = _sanitize_filename(project)
    safe_author = _sanitize_filename(author)

    # Build relative path
    relative_path = f"{safe_project}/{report_type}/{report_date}/{safe_author}.md"

    # Validate and get full path
    full_path = _validate_path(relative_path)

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    try:
        content = full_path.read_text(encoding="utf-8")
        return ApiResponse(
            success=True,
            data={
                "content": content,
                "path": relative_path,
                "size": full_path.stat().st_size,
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read report: {str(e)}")


@router.delete("")
async def delete_report(
    project: str = Query(..., min_length=1, max_length=100),
    report_type: str = Query(..., pattern="^(daily|weekly|monthly)$"),
    report_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    author: str = Query(..., min_length=1, max_length=100),
    current_user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
):
    """Delete a specific report - 仅该项目的管理员或系统管理员可删除"""

    # 检查项目级管理权限
    if not current_user.is_system_admin():
        # 非系统管理员，检查是否是该项目的管理员
        project_obj = db.query(Project).filter(Project.name == project).first()
        if not project_obj:
            raise HTTPException(status_code=403, detail="项目不存在")
        if not _check_project_permission(current_user, project_obj.id, require_write=True, db=db):
            raise HTTPException(status_code=403, detail="您没有权限删除此项目的报告")

    # Sanitize inputs
    safe_project = _sanitize_filename(project)
    safe_author = _sanitize_filename(author)

    # Build relative path
    relative_path = f"{safe_project}/{report_type}/{report_date}/{safe_author}.md"

    # Validate and get full path
    full_path = _validate_path(relative_path)

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    try:
        full_path.unlink()
        return ApiResponse(success=True, message="Report deleted")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete report: {str(e)}")


@router.get("/download")
async def download_report(
    path: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user_full),
    db: Session = Depends(get_db),
):
    """Download a specific report file"""
    from fastapi.responses import FileResponse

    # Parse and validate path
    project, report_type, report_date, author = _parse_report_path(path)

    # 检查项目级查看权限
    allowed_project_names = _get_user_allowed_project_names(current_user, db)
    if project not in allowed_project_names:
        raise HTTPException(status_code=403, detail="您没有权限下载此项目的报告")

    # Sanitize inputs
    safe_project = _sanitize_filename(project)
    safe_author = _sanitize_filename(author)

    # Build relative path
    relative_path = f"{safe_project}/{report_type}/{report_date}/{safe_author}.md"

    # Validate and get full path
    full_path = _validate_path(relative_path)

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    # Generate download filename: {project}_{date}_{author}.md
    download_name = f"{safe_project}_{report_date}_{safe_author}.md"

    return FileResponse(
        path=full_path,
        filename=download_name,
        media_type="text/markdown"
    )

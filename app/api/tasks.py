# app/api/tasks.py
import json
from typing import Optional
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, date, timedelta
from app.database import get_db, SessionLocal
from app.schemas.task import TaskRunRequest, TaskProgress, TaskResponse
from app.models.settings import Settings
from app.models.project import Project
from app.models.task_log import TaskLog
from app.models import User, Role
from app.security import security_service
from app.api.deps import get_current_user_full, require_project_admin
from app.api.projects import _check_project_permission
from app.core.permissions import get_readable_project_ids

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# In-memory task progress storage
_task_progress: dict = {}


@router.post("/run", response_model=TaskResponse)
async def run_task(
    request: TaskRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full)
):
    """Start a code review task - 项目管理员或系统管理员"""
    # 项目成员不能执行任务
    if not current_user.is_system_admin() and not current_user.is_project_admin():
        raise HTTPException(status_code=403, detail="您没有权限执行任务")

    # 检查项目权限（如果是指定项目）
    if request.project_id:
        if not _check_project_permission(current_user, request.project_id, require_write=True, db=db):
            raise HTTPException(status_code=403, detail="您没有权限执行此项目的任务")
    
    # 非系统管理员必须指定项目
    if not current_user.is_system_admin() and not request.project_id:
        raise HTTPException(status_code=400, detail="您需要指定要执行任务的项目")

    if _task_progress.get("is_running"):
        return TaskResponse(
            success=False,
            message="Task already running",
            progress=TaskProgress(**_task_progress)
        )

    # Reset progress
    _task_progress.clear()
    _task_progress.update({
        "is_running": True,
        "start_time": datetime.now(),
        "current_project": None,
        "branches_processed": 0,
        "commits_processed": 0,
        "reports_generated": 0,
        "cancelled": False
    })

    # Start background task
    background_tasks.add_task(
        _run_review_task,
        request.project_id,
        request.task_type,
        current_user.username,
        current_user.id  # 传入用户ID用于权限过滤
    )

    return TaskResponse(
        success=True,
        message="Task started",
        progress=TaskProgress(**_task_progress)
    )


@router.get("/status", response_model=TaskResponse)
async def get_task_status(
    current_user: User = Depends(get_current_user_full),
):
    """Get current task status"""
    if not _task_progress:
        return TaskResponse(
            success=True,
            message="No task has been run",
            progress=TaskProgress(is_running=False)
        )

    return TaskResponse(
        success=True,
        message="Task status",
        progress=TaskProgress(**_task_progress)
    )


@router.post("/cancel", response_model=TaskResponse)
async def cancel_task(
    current_user: User = Depends(get_current_user_full),
):
    """Request task cancellation"""
    if not _task_progress.get("is_running"):
        return TaskResponse(
            success=False,
            message="No task running",
            progress=TaskProgress(is_running=False)
        )

    _task_progress["cancelled"] = True
    return TaskResponse(
        success=True,
        message="Cancellation requested",
        progress=TaskProgress(**_task_progress)
    )


@router.post("/run-now/{job_id}")
async def run_now(
    job_id: str,
    current_user: User = Depends(get_current_user_full),
):
    """立即触发指定调度任务"""
    import app.main as main_mod

    sched = main_mod.scheduler
    if not sched or not sched.is_running:
        return {"success": False, "message": "调度器未运行"}

    success = sched.run_now(job_id)
    if success:
        return {"success": True, "message": f"任务 {job_id} 已触发"}
    return {"success": False, "message": f"未找到任务: {job_id}"}


@router.post("/run-all")
async def run_all(
    current_user: User = Depends(get_current_user_full),
):
    """立即触发所有调度任务"""
    import app.main as main_mod

    sched = main_mod.scheduler
    if not sched or not sched.is_running:
        return {"success": False, "message": "调度器未运行"}

    triggered = []
    for job in sched.get_jobs():
        job_id = job["id"]
        success = sched.run_now(job_id)
        if success:
            triggered.append(job_id)

    if triggered:
        return {"success": True, "message": f"已触发任务: {', '.join(triggered)}"}
    return {"success": False, "message": "无可触发的任务"}


async def _run_review_task(
    project_id: Optional[int],
    task_type: str,
    user: str,
    user_id: Optional[int] = None
):
    """Background task for running code review - 实际执行代码审查任务"""
    global _task_progress

    from loguru import logger
    from app.services.gitlab_client import GitLabClient, GitLabAuthError
    from app.services.code_reviewer import CodeReviewer
    from app.services.stats_generator import StatsGenerator
    from app.services.report_merger import ReportMerger
    from app.services.svn_uploader import SVNUploader
    from app.services.task_executor import TaskExecutor
    from pathlib import Path
    import asyncio

    db = SessionLocal()
    try:
        # Get settings
        settings = db.query(Settings).first()
        if not settings:
            _task_progress["error"] = "Settings not configured"
            return

        # 获取项目列表（根据用户权限过滤）
        if project_id:
            # 指定了项目
            projects_list = db.query(Project).filter(
                Project.id == project_id,
                Project.is_active == True
            ).all()
        else:
            # 未指定项目：根据权限获取可访问的项目
            projects_list = db.query(Project).filter(Project.is_active == True).all()
            
            # 根据用户权限过滤
            if user_id:
                user_obj = db.query(User).filter(User.id == user_id).first()
                if user_obj:
                    readable_ids = get_readable_project_ids(user_obj, db)
                    if readable_ids is None:
                        pass  # system_admin 看所有项目
                    else:
                        projects_list = [p for p in projects_list if p.id in readable_ids]

        if not projects_list:
            _task_progress["error"] = "No active projects found"
            return

        # 确定报告输出目录
        report_output_dir = Path(settings.report_output_dir or "./data/reports")

        # 确定审查天数
        if task_type == 'weekly':
            review_days = settings.weekly_review_days or 7
        else:
            review_days = settings.daily_review_days or 1

        for project in projects_list:
            # 检查取消标志
            if _task_progress.get("cancelled"):
                break

            _task_progress["current_project"] = project.name

            # ---- 创建 TaskLog 记录 ----
            task_log = TaskLog(
                project_id=project.id,
                project_name=project.name,
                task_type=task_type,
                trigger_type="manual",
                trigger_user=user,
                status="running",
                start_time=datetime.now(),
            )
            db.add(task_log)
            db.commit()
            db.refresh(task_log)

            try:
                # 使用全局 Token
                token = None
                if settings.global_gitlab_token:
                    try:
                        token = security_service.decrypt(settings.global_gitlab_token)
                    except ValueError:
                        logger.warning(f"全局 GitLab Token 解密失败")
                if not token:
                    logger.warning(f"全局 GitLab Token 未配置，跳过项目 {project.name}")
                    task_log.status = "failed"
                    task_log.error_message = "全局 GitLab Token 未配置"
                    task_log.end_time = datetime.now()
                    db.commit()
                    continue

                gitlab_url = project.gitlab_url or settings.global_gitlab_url
                if not gitlab_url:
                    logger.warning(f"项目 {project.name} GitLab URL 未配置，跳过")
                    task_log.status = "failed"
                    task_log.error_message = "GitLab URL 未配置"
                    task_log.end_time = datetime.now()
                    db.commit()
                    continue

                gitlab_client = GitLabClient(
                    gitlab_url=gitlab_url,
                    access_token=token
                )

                reviewer = CodeReviewer(
                    api_url=settings.llm_api_url,
                    api_key=security_service.decrypt(settings.llm_api_key) if settings.llm_api_key else "",
                    model=settings.llm_model,
                    timeout=settings.llm_timeout or 120,
                    max_retries=settings.llm_max_retries or 3,
                    retry_delay=settings.llm_retry_delay or 5,
                )

                # 配置 SVN 上传器（可选）
                svn_uploader = None
                svn_url = project.svn_url or settings.global_svn_url
                svn_username = project.svn_username or settings.global_svn_username
                svn_password_enc = project.svn_password or settings.global_svn_password
                if svn_url and svn_username and svn_password_enc:
                    try:
                        svn_password = security_service.decrypt(svn_password_enc)
                        svn_uploader = SVNUploader(
                            svn_url=svn_url,
                            username=svn_username,
                            password=svn_password
                        )
                    except Exception as e:
                        logger.warning(f"SVN 配置初始化失败: {e}")

                executor = TaskExecutor(
                    gitlab_client=gitlab_client,
                    code_reviewer=reviewer,
                    stats_generator=StatsGenerator(),
                    report_merger=ReportMerger(),
                    svn_uploader=svn_uploader,
                    report_output_dir=str(report_output_dir),
                    db=db,
                    task_log_id=task_log.id,
                )

                # 解析排除分支
                exclude_branches = []
                if project.exclude_branches:
                    try:
                        exclude_branches = json.loads(project.exclude_branches)
                    except (json.JSONDecodeError, TypeError):
                        exclude_branches = [
                            b.strip() for b in project.exclude_branches.split(',')
                            if b.strip()
                        ]

                # 按天数范围循环审查
                today = date.today()
                start_date = today - timedelta(days=review_days)
                end_date = today - timedelta(days=1)

                for days_back in range(review_days, 0, -1):
                    # 检查取消标志
                    if _task_progress.get("cancelled"):
                        break

                    target_date = today - timedelta(days=days_back)

                    # 周报模式下，如果日报文件已存在则跳过
                    if task_type == 'weekly':
                        daily_dir = report_output_dir / project.name / "daily" / target_date.isoformat()
                        if daily_dir.exists() and list(daily_dir.glob("*.md")):
                            logger.info(f"跳过 {project.name} - {target_date}（日报已存在）")
                            continue

                    logger.info(f"手动任务：审查 {project.name} - {target_date}")

                    result = await executor.run_daily_review(
                        project_id=project.project_id,
                        project_name=project.name,
                        exclude_branches=exclude_branches,
                        target_date=target_date,
                        prompt_template=settings.review_prompt_template
                    )

                    # 更新 TaskLog 进度
                    task_log.branches_processed = (task_log.branches_processed or 0) + (result.get("branches_processed") or 0)
                    task_log.commits_processed = (task_log.commits_processed or 0) + (result.get("commits_processed") or 0)
                    task_log.reports_generated = (task_log.reports_generated or 0) + (result.get("reports_generated") or 0)
                    db.commit()

                    # 更新进度
                    _task_progress["branches_processed"] += result.get("branches_processed", 0)
                    _task_progress["commits_processed"] += result.get("commits_processed", 0)
                    _task_progress["reports_generated"] += result.get("reports_generated", 0)

                # 周报生成
                if task_type == 'weekly' and not _task_progress.get("cancelled"):
                    logger.info(f"手动任务：生成 {project.name} 周报 {start_date} ~ {end_date}")
                    weekly_result = await executor.run_weekly_review(
                        project_id=project.project_id,
                        project_name=project.name,
                        start_date=start_date,
                        end_date=end_date,
                        weekly_prompt=settings.weekly_review_prompt
                    )
                    task_log.reports_generated = (task_log.reports_generated or 0) + (weekly_result.get("reports_generated") or 0)
                    db.commit()

                # 标记成功
                task_log.status = "success"
                task_log.end_time = datetime.now()
                db.commit()

            except GitLabAuthError as e:
                logger.error(f"处理项目 {project.name} GitLab 认证失败: {e}")
                task_log.status = "failed"
                task_log.error_message = f"GitLab 认证失败: {e}"
                task_log.end_time = datetime.now()
                db.commit()
                _task_progress["error"] = f"GitLab 认证失败: {e}"
            except Exception as e:
                logger.error(f"处理项目 {project.name} 失败: [{type(e).__name__}] {e}", exc_info=True)
                task_log.status = "failed"
                task_log.error_message = f"[{type(e).__name__}] {e}"
                task_log.end_time = datetime.now()
                db.commit()
                _task_progress["error"] = str(e)

    except Exception as e:
        _task_progress["error"] = str(e)
    finally:
        _task_progress["is_running"] = False
        _task_progress["end_time"] = datetime.now()
        _task_progress["current_project"] = None
        db.close()

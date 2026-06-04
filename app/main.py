"""
GitLab Code Review Tool - FastAPI Application Entry Point
"""
import os
import sys
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from loguru import logger

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import config_manager
from app.security import security_service
from app.database import SessionLocal, init_db
from app.models import User, Project, Settings, TaskLog
from sqlalchemy.orm import joinedload
from app.services.scheduler import ReviewScheduler, run_monthly_efficiency_aggregation
from app.services.task_executor import TaskExecutor

# Import API routers
from app.api import auth, projects, settings as settings_api, tasks, logs, reports
from app.api import webhook, webhook_reviews, users, roles, efficiency
from app.api.projects import _filter_projects_by_permission

# Configuration
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR.parent / 'data'
REPORT_DIR = DATA_DIR / 'reports'

# SECRET_KEY 从环境变量读取，security_service 内部已处理持久化
SECRET_KEY = os.environ.get('SECRET_KEY', security_service.secret_key)

# Global scheduler instance
scheduler: Optional[ReviewScheduler] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    global scheduler

    # Startup
    logger.info("Starting GitLab Code Review Tool...")

    # Initialize database
    init_db()
    logger.info("Database initialized")

    # Create necessary directories
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / 'config').mkdir(parents=True, exist_ok=True)
    logger.info(f"Data directory: {DATA_DIR}")

    # Initialize scheduler
    scheduler = ReviewScheduler()

    # Load settings and setup scheduler if schedule_times configured
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if settings and settings.scheduler_enabled:
            import json

            # 注册每日任务
            if settings.daily_enabled and settings.daily_schedule_times:
                try:
                    times = json.loads(settings.daily_schedule_times)
                except (json.JSONDecodeError, TypeError):
                    times = [settings.daily_schedule_times] if settings.daily_schedule_times else []

                for idx, t in enumerate(times):
                    job_id = f'daily_review_{idx}' if idx > 0 else 'daily_review'
                    scheduler.setup_daily_task(
                        time=t,
                        callback=lambda: run_scheduled_task('daily'),
                        job_id=job_id
                    )
                logger.info(f"Scheduled {len(times)} daily tasks: {times}")

            # 注册每周任务
            if settings.weekly_enabled and settings.weekly_schedule_time:
                weekday = settings.weekly_weekday if settings.weekly_weekday is not None else 0
                scheduler.setup_weekly_task(
                    weekday=weekday,
                    time=settings.weekly_schedule_time,
                    callback=lambda: run_scheduled_task('weekly'),
                    job_id='weekly_review'
                )
                logger.info(f"Scheduled weekly task: weekday={weekday}, time={settings.weekly_schedule_time}")

            # 注册每月任务
            scheduler.setup_monthly_task(
                day=1,
                time="02:00",
                callback=run_monthly_efficiency_aggregation,
                job_id="monthly_efficiency",
            )
            logger.info("Scheduled monthly task: monthly_efficiency")
    except Exception as e:
        logger.warning(f"Could not setup scheduler: {e}")
    finally:
        db.close()

    # 启动调度器
    scheduler.start()

    logger.info("Application started successfully")

    yield

    # Shutdown
    logger.info("Shutting down GitLab Code Review Tool...")
    if scheduler:
        scheduler.stop()
    logger.info("Application stopped")


# Create FastAPI app
app = FastAPI(
    title="GitLab Code Review Tool",
    description="Automated code review and statistics tool for GitLab repositories",
    version="1.0.0",
    lifespan=lifespan,
)


# 静默处理 Chrome DevTools 探测请求，避免 404 日志噪音
@app.get("/.well-known/appspecific/com.chrome.devtools.json")
async def _chrome_devtools_probe():
    from fastapi.responses import JSONResponse
    return JSONResponse(content={})


# Add middleware
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Mount static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Setup templates
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Include API routers
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(settings_api.router)
app.include_router(tasks.router)
app.include_router(logs.router)
app.include_router(reports.router)
app.include_router(webhook.router)
app.include_router(webhook_reviews.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(efficiency.router)


# Template context processor
def get_template_context(request: Request) -> dict:
    """Get common template context (不含 request，新版 Starlette 单独传)"""
    user = request.session.get("user")
    display_name = user
    if user:
        try:
            config = config_manager.get_admin_config()
            display_name = config.nickname or user
        except Exception:
            pass
    return {
        "current_user": user,
        "display_name": display_name,
    }


# Dependency for authentication
async def get_current_user_or_redirect(request: Request):
    """Get current user or redirect to login"""
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


# Web Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Dashboard page"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        # Get settings (Settings 已从 app.models 导入)
        settings = db.query(Settings).first()
        report_output_dir_str = settings.report_output_dir if settings else "./data/reports"
        
        # 获取当前用户并按权限过滤项目
        db_user = db.query(User).options(joinedload(User.roles)).filter(User.username == user).first()
        current_user_roles = [role.name for role in db_user.roles] if db_user else []
        allowed_projects = _filter_projects_by_permission(
            db.query(Project).filter(Project.is_active == True).all(), db_user, db
        ) if db_user else []
        allowed_project_ids = {p.id for p in allowed_projects}
        total_projects = len(allowed_projects)
        active_projects = total_projects

        # Get today's stats
        from datetime import date, datetime
        today = date.today()
        today_start = datetime.combine(today, datetime.min.time())
        today_end = datetime.combine(today, datetime.max.time())
        today_logs = db.query(TaskLog).filter(
            TaskLog.start_time >= today_start
        ).all()
        
        # 统计今天实际创建的文件数量（根据文件修改时间）
        def count_today_reports(report_dir: Path, today_start: datetime) -> int:
            """统计指定目录下今天修改的所有 .md 文件数量"""
            if not report_dir.exists():
                return 0
            count = 0
            for md_file in report_dir.rglob("*.md"):
                try:
                    mtime = datetime.fromtimestamp(md_file.stat().st_mtime)
                    if today_start <= mtime <= today_end:
                        count += 1
                except OSError:
                    continue
            return count
        
        report_output_dir = Path(report_output_dir_str)
        today_reports = count_today_reports(report_output_dir, today_start)

        # Webhook 审查统计
        from app.models.webhook_review import MrReviewLog, PushReviewLog
        today_ts = int(today_start.timestamp())
        today_mr_reviews = db.query(MrReviewLog).filter(MrReviewLog.updated_at >= today_ts).count()
        today_push_reviews = db.query(PushReviewLog).filter(PushReviewLog.updated_at >= today_ts).count()
        today_webhook_reviews = today_mr_reviews + today_push_reviews

        # Get recent tasks - 只显示有权限项目的任务记录
        recent_task_rows = db.query(TaskLog).filter(
            TaskLog.project_id.in_(allowed_project_ids)
        ).order_by(TaskLog.start_time.desc()).limit(5).all()
        recent_tasks = [
            {
                "id": log.id,
                "project_name": log.project_name or (log.project.name if log.project else f"项目#{log.project_id}"),
                "task_type": log.task_type,
                "status": log.status,
                "start_time": log.start_time.strftime("%Y-%m-%d %H:%M") if log.start_time else "-",
            }
            for log in recent_task_rows
        ]

        # Get scheduler jobs
        scheduler_jobs = []
        if scheduler and scheduler.is_running:
            for job in scheduler.get_jobs():
                scheduler_jobs.append({
                    'id': job['id'],
                    'name': job['name'] or job['id'],
                    'trigger': str(job['trigger']),
                    'next_run_time': job.get('next_run_time'),
                })

        stats = {
            'total_projects': total_projects,
            'active_projects': active_projects,
            'today_reports': today_reports,
            'today_webhook_reviews': today_webhook_reviews,
        }

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                **get_template_context(request),
                "stats": stats,
                "recent_tasks": recent_tasks,
                "scheduler_jobs": scheduler_jobs,
                "current_user_roles": current_user_roles,
            }
        )
    finally:
        db.close()


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    user = request.session.get("user")
    if user:
        return RedirectResponse(url="/", status_code=302)

    return templates.TemplateResponse(request, "login.html", get_template_context(request))


@app.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    """Projects management page"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        db_user = db.query(User).options(joinedload(User.roles)).filter(User.username == user).first()
        current_user_roles = [role.name for role in db_user.roles] if db_user else []
        all_projects = db.query(Project).order_by(Project.created_at.desc()).all()
        projects_list = _filter_projects_by_permission(all_projects, db_user, db) if db_user else []
        # 转为字典列表，避免 Jinja2 tojson 无法序列化 SQLAlchemy 对象
        projects_data = [
            {
                'id': p.id,
                'name': p.name,
                'gitlab_url': p.gitlab_url,
                'project_id': p.project_id,
                'description': p.description or '',
                'target_branches': p.target_branches or '',
                'exclude_branches': p.exclude_branches or '',
                'access_token': p.access_token or '',
                'svn_url': p.svn_url or '',
                'svn_username': p.svn_username or '',
                'svn_password': p.svn_password or '',
                'is_active': p.is_active,
                'wecom_enabled': p.wecom_enabled,
                'wecom_webhook_url': p.wecom_webhook_url or '',
                'created_at': str(p.created_at),
                'updated_at': str(p.updated_at),
            }
            for p in projects_list
        ]
        return templates.TemplateResponse(
            request,
            "projects.html",
            {
                **get_template_context(request),
                "projects": projects_data,
                "current_user_roles": current_user_roles,
            }
        )
    finally:
        db.close()


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page - 仅系统管理员可访问"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        db_user = db.query(User).options(joinedload(User.roles)).filter(User.username == user).first()
        if not db_user or not db_user.is_system_admin():
            return RedirectResponse(url="/", status_code=302)

        settings = db.query(Settings).first()
        if not settings:
            # Create default settings
            settings = Settings()
            db.add(settings)
            db.commit()
            db.refresh(settings)

        return templates.TemplateResponse(
            request,
            "settings.html",
            {**get_template_context(request), "settings": settings}
        )
    finally:
        db.close()


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
):
    """Task logs page"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # 空字符串转 None
    pid = int(project_id) if project_id and project_id.strip() else None
    status = status if status and status.strip() else None
    start_date = start_date if start_date and start_date.strip() else None
    end_date = end_date if end_date and end_date.strip() else None

    db = SessionLocal()
    try:
        # 获取当前用户并按权限过滤项目
        db_user = db.query(User).options(joinedload(User.roles)).filter(User.username == user).first()
        all_projects = db.query(Project).all()
        allowed_projects = _filter_projects_by_permission(all_projects, db_user, db) if db_user else []
        allowed_project_ids = {p.id for p in allowed_projects}

        # 如果筛选的项目不在权限列表中，清空筛选条件
        if pid and pid not in allowed_project_ids:
            pid = None

        # Build query - 只查询有权限的项目
        query = db.query(TaskLog).filter(TaskLog.project_id.in_(allowed_project_ids))

        if pid:
            query = query.filter(TaskLog.project_id == pid)
        if status:
            query = query.filter(TaskLog.status == status)
        if start_date:
            from datetime import datetime as dt
            query = query.filter(TaskLog.start_time >= dt.fromisoformat(start_date))
        if end_date:
            from datetime import datetime as dt
            query = query.filter(TaskLog.start_time <= dt.fromisoformat(end_date + "T23:59:59"))

        # Pagination
        per_page = 20
        total = query.count()
        logs_list = query.order_by(TaskLog.start_time.desc()).offset((page - 1) * per_page).limit(per_page).all()

        # 构建带 project_name 的日志列表供模板使用
        logs_data = []
        for log in logs_list:
            pname = log.project_name or (log.project.name if log.project else f"项目#{log.project_id}")
            logs_data.append({
                "id": log.id,
                "project_name": pname,
                "task_type": log.task_type,
                "status": log.status,
                "branches_processed": log.branches_processed or 0,
                "commits_processed": log.commits_processed or 0,
                "reports_generated": log.reports_generated or 0,
                "start_time": log.start_time.strftime("%Y-%m-%d %H:%M:%S") if log.start_time else "-",
                "end_time": log.end_time.strftime("%Y-%m-%d %H:%M:%S") if log.end_time else None,
                "duration": log.duration,
            })

        # Get projects for filter - 只显示有权限的项目
        projects_list = allowed_projects

        filters = {
            'project_id': pid,
            'status': status,
            'start_date': start_date,
            'end_date': end_date,
        }

        pagination = {
            'page': page,
            'pages': (total + per_page - 1) // per_page,
            'total': total,
        }

        return templates.TemplateResponse(
            request,
            "logs.html",
            {
                **get_template_context(request),
                "logs": logs_data,
                "projects": projects_list,
                "filters": filters,
                "pagination": pagination,
            }
        )
    finally:
        db.close()


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    project: str = None,
    report_type: str = None,
    date: str = None,
):
    """Reports page"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # 空字符串转 None
    project = project if project and project.strip() else None
    report_type = report_type if report_type and report_type.strip() else None
    date = date if date and date.strip() else None

    db = SessionLocal()
    try:
        # 获取当前用户并按权限过滤项目
        db_user = db.query(User).options(joinedload(User.roles)).filter(User.username == user).first()
        all_projects = db.query(Project).all()
        allowed_projects = _filter_projects_by_permission(all_projects, db_user, db) if db_user else []
        allowed_project_names = {p.name for p in allowed_projects}
        projects_list = allowed_projects

        # 如果筛选的项目不在权限列表中，清空筛选条件
        if project and project not in allowed_project_names:
            project = None

        # Build report tree（只显示有权限项目的报告）
        reports_tree = {}
        if REPORT_DIR.exists():
            for project_dir in sorted(REPORT_DIR.iterdir()):
                if not project_dir.is_dir():
                    continue
                project_name = project_dir.name

                # 项目筛选 - 跳过无权限的项目目录
                if project_name not in allowed_project_names:
                    continue
                if project and project_name != project:
                    continue

                for type_dir in sorted(project_dir.iterdir()):
                    if not type_dir.is_dir():
                        continue
                    dir_type = type_dir.name  # 'daily', 'weekly', 'monthly'

                    # 报告类型筛选
                    if report_type and dir_type != report_type:
                        continue

                    for date_dir in sorted(type_dir.iterdir(), reverse=True):
                        if not date_dir.is_dir():
                            continue
                        date_str = date_dir.name

                        # 日期筛选：
                        #   日报目录格式: YYYY-MM-DD，精确匹配
                        #   周报目录格式: YYYY-MM-DD_to_YYYY-MM-DD，匹配起止日期区间
                        if date:
                            if dir_type == 'daily':
                                if date_str != date:
                                    continue
                            else:
                                # 周报/月报：判断 date 是否落在区间内
                                parts = date_str.split('_to_')
                                if len(parts) == 2:
                                    if not (parts[0] <= date <= parts[1]):
                                        continue
                                else:
                                    # 格式不标准，按前缀匹配
                                    if not date_str.startswith(date):
                                        continue

                        report_files = list(date_dir.glob("*.md"))
                        if not report_files:
                            continue

                        if project_name not in reports_tree:
                            reports_tree[project_name] = {}

                        reports_tree[project_name][date_str] = {}
                        for report_file in sorted(report_files):
                            author = report_file.stem
                            reports_tree[project_name][date_str][author] = {
                                'filename': report_file.name,
                                'path': str(report_file.relative_to(REPORT_DIR)),
                                'type': dir_type,
                            }

        filters = {
            'project': project,
            'report_type': report_type,
            'date': date,
        }

        return templates.TemplateResponse(
            request,
            "reports.html",
            {
                **get_template_context(request),
                "projects": projects_list,
                "reports": reports_tree,
                "filters": filters,
            }
        )
    finally:
        db.close()


@app.get("/webhook-reviews", response_class=HTMLResponse)
async def webhook_reviews_page(request: Request):
    """Webhook 审查记录页面"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        db_user = db.query(User).options(joinedload(User.roles)).filter(User.username == user).first()
        current_user_roles = [role.name for role in db_user.roles] if db_user else []
    finally:
        db.close()

    context = get_template_context(request)
    context["current_user_roles"] = current_user_roles

    return templates.TemplateResponse(
        request,
        "webhook_reviews.html",
        context,
    )


@app.get("/efficiency", response_class=HTMLResponse)
async def efficiency_page(request: Request):
    """人员能效页面"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "efficiency.html",
        get_template_context(request),
    )


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request):
    """账号管理页面 - 仅系统管理员可访问"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        db_user = db.query(User).options(joinedload(User.roles)).filter(User.username == user).first()
        if not db_user or not db_user.is_system_admin():
            return RedirectResponse(url="/", status_code=302)
        current_user_roles = [role.name for role in db_user.roles] if db_user else []
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "users.html",
        {**get_template_context(request), "current_user_roles": current_user_roles},
    )


@app.get("/roles", response_class=HTMLResponse)
async def roles_page(request: Request):
    """权限管理页面 - 仅系统管理员可访问"""
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    db = SessionLocal()
    try:
        db_user = db.query(User).options(joinedload(User.roles)).filter(User.username == user).first()
        if not db_user or not db_user.is_system_admin():
            return RedirectResponse(url="/", status_code=302)
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "roles.html",
        get_template_context(request),
    )


def run_scheduled_task(task_type: str = 'daily'):
    """Run scheduled review task

    Args:
        task_type: 'daily' or 'weekly'
    """
    import asyncio
    import json

    logger.info(f"Starting scheduled {task_type} review task")

    db = SessionLocal()
    try:
        # Get active projects
        projects_list = db.query(Project).filter(Project.is_active == True).all()
        settings = db.query(Settings).first()

        if not projects_list:
            logger.info("No active projects to review")
            return

        if not settings:
            logger.warning("No settings found, skipping scheduled task")
            return

        # 根据任务类型确定审查天数
        if task_type == 'weekly':
            review_days = settings.weekly_review_days or 7
        else:
            review_days = settings.daily_review_days or 1

        from app.services.gitlab_client import GitLabClient, GitLabAuthError
        from app.services.code_reviewer import CodeReviewer
        from app.services.stats_generator import StatsGenerator
        from app.services.report_merger import ReportMerger

        for project in projects_list:
            # 创建 TaskLog 记录
            task_log = TaskLog(
                project_id=project.id,
                project_name=project.name,
                task_type=task_type,
                trigger_type="scheduled",
                trigger_user=None,
                status="running",
                start_time=datetime.now(),
            )
            db.add(task_log)
            db.commit()
            db.refresh(task_log)

            try:
                # 解密 token：优先使用项目级 token，解密失败则降级使用全局 token
                token = None
                if project.access_token:
                    try:
                        token = security_service.decrypt(project.access_token)
                    except ValueError:
                        logger.warning(f"项目 {project.name} Token 解密失败，尝试使用全局 Token")
                if not token and settings.global_gitlab_token:
                    try:
                        token = security_service.decrypt(settings.global_gitlab_token)
                    except ValueError:
                        logger.warning(f"项目 {project.name} 全局 Token 解密失败")
                if not token:
                    logger.warning(f"项目 {project.name} 无可用 Token，跳过")
                    task_log.status = "failed"
                    task_log.error_message = "无可用 Token"
                    task_log.end_time = datetime.now()
                    db.commit()
                    continue

                if not settings.global_gitlab_url:
                    logger.warning("GitLab URL 未配置，跳过")
                    task_log.status = "failed"
                    task_log.error_message = "GitLab URL 未配置"
                    task_log.end_time = datetime.now()
                    db.commit()
                    continue

                gitlab_client = GitLabClient(
                    gitlab_url=settings.global_gitlab_url,
                    access_token=token
                )

                reviewer = CodeReviewer(
                    api_url=settings.llm_api_url,
                    api_key=security_service.decrypt(settings.llm_api_key) if settings.llm_api_key else None,
                    model=settings.llm_model,
                    timeout=settings.llm_timeout,
                    max_retries=settings.llm_max_retries,
                    retry_delay=settings.llm_retry_delay,
                )

                stats_gen = StatsGenerator()
                merger = ReportMerger()

                # 配置 SVN 上传器（可选）
                from app.services.svn_uploader import SVNUploader
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
                    stats_generator=stats_gen,
                    report_merger=merger,
                    svn_uploader=svn_uploader,
                    report_output_dir=str(REPORT_DIR),
                )

                # 解析排除分支（JSON 格式）
                exclude_branches = []
                if project.exclude_branches:
                    try:
                        exclude_branches = json.loads(project.exclude_branches)
                    except (json.JSONDecodeError, TypeError):
                        exclude_branches = [
                            b.strip() for b in project.exclude_branches.split(',')
                            if b.strip()
                        ]

                # 按天数范围循环审查，每天生成独立报告
                from datetime import date, timedelta
                today = date.today()
                start_date = today - timedelta(days=review_days)
                end_date = today - timedelta(days=1)

                for days_back in range(review_days, 0, -1):
                    target_date = today - timedelta(days=days_back)
                    logger.info(f"Reviewing {project.name} for {target_date} (days_back={days_back})")
                    result = asyncio.run(executor.run_daily_review(
                        project_id=project.project_id,
                        project_name=project.name,
                        exclude_branches=exclude_branches,
                        target_date=target_date,
                        prompt_template=settings.review_prompt_template
                    ))
                    # 更新 TaskLog 进度
                    task_log.branches_processed = (task_log.branches_processed or 0) + (result.get("branches_processed") or 0)
                    task_log.commits_processed = (task_log.commits_processed or 0) + (result.get("commits_processed") or 0)
                    task_log.reports_generated = (task_log.reports_generated or 0) + (result.get("reports_generated") or 0)
                    db.commit()

                # 周报：在日报基础上生成汇总周报
                if task_type == 'weekly':
                    logger.info(f"Generating weekly report for {project.name}: {start_date} ~ {end_date}")
                    weekly_result = asyncio.run(executor.run_weekly_review(
                        project_id=project.project_id,
                        project_name=project.name,
                        start_date=start_date,
                        end_date=end_date,
                        weekly_prompt=settings.weekly_review_prompt
                    ))
                    task_log.reports_generated = (task_log.reports_generated or 0) + (weekly_result.get("reports_generated") or 0)
                    db.commit()

                # 标记成功
                task_log.status = "success"
                task_log.end_time = datetime.now()
                db.commit()

            except GitLabAuthError as e:
                logger.error(
                    f"项目 {project.name} GitLab 认证失败: {e}\n"
                    f"请检查: 1) Token 是否过期  2) Token 是否有 api 权限  3) 项目 Token 配置是否正确"
                )
                task_log.status = "failed"
                task_log.error_message = f"GitLab 认证失败: {e}"
                task_log.end_time = datetime.now()
                db.commit()
            except Exception as e:
                logger.error(
                    f"Failed to review project {project.name}: "
                    f"[{type(e).__name__}] {e}",
                    exc_info=True
                )
                task_log.status = "failed"
                task_log.error_message = f"[{type(e).__name__}] {e}"
                task_log.end_time = datetime.now()
                db.commit()

        # 日报跑完后顺便聚合人员能效（仅 daily 任务且开启时）
        if task_type == 'daily' and settings.efficiency_enabled:
            try:
                from datetime import date as _date, timedelta as _td
                from app.services.efficiency_aggregator import EfficiencyAggregator
                from app.services.gitlab_client import GitLabClient as _GLC

                target_efficiency_date = _date.today() - _td(days=1)

                def _client_factory(proj):
                    """根据项目解 token，构造 GitLabClient"""
                    tk = None
                    if proj.access_token:
                        try:
                            tk = security_service.decrypt(proj.access_token)
                        except ValueError:
                            tk = None
                    if not tk and settings.global_gitlab_token:
                        try:
                            tk = security_service.decrypt(settings.global_gitlab_token)
                        except ValueError:
                            tk = None
                    if not tk:
                        raise RuntimeError(f"项目 {proj.name} 无可用 Token")
                    return _GLC(gitlab_url=settings.global_gitlab_url,
                                 access_token=tk)

                llm_cfg = {
                    "api_url": settings.llm_api_url,
                    "api_key": (security_service.decrypt(settings.llm_api_key)
                                if settings.llm_api_key else ""),
                    "model": settings.llm_model,
                    "timeout": settings.llm_timeout,
                    "max_retries": settings.llm_max_retries,
                    "retry_delay": settings.llm_retry_delay,
                    "review_max_tokens": settings.review_max_tokens or 10000,
                }

                top_n = settings.efficiency_work_summary_top_n or 5

                aggregator = EfficiencyAggregator(
                    db=db,
                    gitlab_client_factory=_client_factory,
                    llm_config=llm_cfg,
                    top_n=top_n,
                    custom_prompt_template=settings.efficiency_prompt_template,
                )
                agg_result = aggregator.aggregate(target_efficiency_date)
                logger.info(f"人员能效聚合: {agg_result}")
            except Exception as e:
                # 聚合失败不影响日报本身
                logger.exception(f"人员能效聚合失败: {e}")

    finally:
        db.close()

    logger.info(f"Scheduled {task_type} review task completed")


if __name__ == "__main__":
    import uvicorn

    # Configure logging
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add(DATA_DIR / "logs" / "app.log", rotation="10 MB", retention="30 days", level="DEBUG")

    # Run server
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=5001,
        reload=False,
        log_level="info",
    )

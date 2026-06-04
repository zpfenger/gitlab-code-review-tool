# app/api/settings.py
import json
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.database import get_db
from app.models import User
from app.models.settings import Settings
from app.schemas.settings import SettingsUpdate, SettingsResponse, GitlabTestRequest
from app.schemas.response import ApiResponse
from app.services.gitlab_client import GitLabClient
from app.services.svn_uploader import SVNUploader
from app.security import security_service
from app.api.users import require_system_admin

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _mask_api_key(encrypted_key: str | None) -> str | None:
    """将加密的 API Key 脱敏显示，仅保留最后 4 位"""
    if not encrypted_key:
        return None
    try:
        plaintext = security_service.decrypt(encrypted_key)
        if len(plaintext) <= 4:
            return "****"
        return f"****{plaintext[-4:]}"
    except Exception:
        return "****(解密失败)"


@router.get("", response_model=ApiResponse[SettingsResponse])
async def get_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_system_admin),
):
    """Get current system settings (仅系统管理员)"""
    settings = db.query(Settings).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not configured")

    # 脱敏显示敏感字段
    response_data = SettingsResponse.model_validate(settings)
    response_data.external_api_key = _mask_api_key(settings.external_api_key)

    return ApiResponse(success=True, data=response_data)


class RegenerateApiKeyResponse(BaseModel):
    """重新生成 API Key 的响应"""
    api_key: str


@router.post("/regenerate-api-key", response_model=ApiResponse[RegenerateApiKeyResponse])
async def regenerate_api_key(
    db: Session = Depends(get_db),
    _: User = Depends(require_system_admin),
):
    """重新生成外部 API Key (仅系统管理员，返回明文仅此一次)"""
    settings = db.query(Settings).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not configured")

    # 生成新的随机 API Key
    new_api_key = f"sk-{secrets.token_urlsafe(32)}"

    # 加密并存储
    settings.external_api_key = security_service.encrypt(new_api_key)
    db.commit()

    return ApiResponse(
        success=True,
        data=RegenerateApiKeyResponse(api_key=new_api_key),
        message="API Key 已重新生成，请妥善保管，此为唯一一次显示明文"
    )


@router.put("", response_model=ApiResponse[SettingsResponse])
async def update_settings(
    data: SettingsUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_system_admin),
):
    """Update system settings (仅系统管理员)"""
    settings = db.query(Settings).first()

    # 敏感字段：前端传空字符串或 null 时保留原值，有值时加密存储
    sensitive_fields = ['global_gitlab_token', 'llm_api_key', 'global_svn_password',
                        'dingtalk_webhook_url', 'wecom_webhook_url', 'feishu_webhook_url',
                        'external_api_key']
    update_data = data.model_dump(exclude_unset=True)

    for field in sensitive_fields:
        value = update_data.get(field)
        if field in update_data:
            if not value:
                # 空值 → 保留原值，不更新
                update_data.pop(field)
            else:
                # 有值 → 加密后存储
                update_data[field] = security_service.encrypt(value)

    if not settings:
        settings = Settings(**update_data)
        db.add(settings)
    else:
        for key, value in update_data.items():
            setattr(settings, key, value)

    db.commit()
    db.refresh(settings)

    # 保存后刷新调度器
    _refresh_scheduler(settings)

    # 响应中脱敏显示 API Key
    response_data = SettingsResponse.model_validate(settings)
    response_data.external_api_key = _mask_api_key(settings.external_api_key)
    return ApiResponse(success=True, data=response_data, message="设置已更新")


def _refresh_scheduler(settings: Settings):
    """根据最新设置刷新调度器中的任务"""
    try:
        from app.main import scheduler, run_scheduled_task
        if not scheduler or not settings.scheduler_enabled:
            return

        # 移除所有旧的调度任务
        if scheduler.scheduler:
            for job in scheduler.scheduler.get_jobs():
                if job.id.startswith('daily_review') or job.id.startswith('weekly_review'):
                    scheduler.scheduler.remove_job(job.id)

        # 注册每日任务
        if settings.daily_enabled:
            times = json.loads(settings.daily_schedule_times) if settings.daily_schedule_times else ['09:00']
            for idx, t in enumerate(times):
                job_id = f'daily_review_{idx}' if idx > 0 else 'daily_review'
                scheduler.setup_daily_task(
                    time=t,
                    callback=lambda: run_scheduled_task('daily'),
                    job_id=job_id
                )

        # 注册每周任务
        if settings.weekly_enabled and settings.weekly_schedule_time:
            weekday = settings.weekly_weekday if settings.weekly_weekday is not None else 0
            scheduler.setup_weekly_task(
                weekday=weekday,
                time=settings.weekly_schedule_time,
                callback=lambda: run_scheduled_task('weekly'),
                job_id='weekly_review'
            )

        from loguru import logger
        logger.info(f"Scheduler refreshed - daily: {settings.daily_enabled}, weekly: {settings.weekly_enabled}")
    except Exception as e:
        from loguru import logger
        logger.warning(f"Failed to refresh scheduler: {e}")


@router.post("/test-gitlab")
async def test_gitlab_connection(
    request: GitlabTestRequest,
    _: User = Depends(require_system_admin),
):
    """Test GitLab connection using provided credentials"""
    client = GitLabClient(request.gitlab_url, request.token)
    success = client.test_connection()

    return ApiResponse(
        success=success,
        message="GitLab 连接成功" if success else "GitLab 连接失败"
    )


@router.post("/test-llm")
async def test_llm_connection(
    db: Session = Depends(get_db),
    _: User = Depends(require_system_admin),
):
    """Test LLM connection using current settings"""
    settings = db.query(Settings).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Settings not configured")

    # Basic validation - LLM test requires actual API call which is async
    if not settings.llm_api_url or not settings.llm_model:
        return ApiResponse.fail(code="VALIDATION_ERROR", message="LLM API 地址和模型名称不能为空")

    # Decrypt API key if exists
    if settings.llm_api_key:
        try:
            _ = security_service.decrypt(settings.llm_api_key)
        except Exception:
            return ApiResponse.fail(code="DECRYPT_ERROR", message="LLM API Key 解密失败")

    return ApiResponse(success=True, message="LLM 配置验证通过")


class SvnTestRequest(BaseModel):
    svn_url: str
    username: str
    password: str


@router.post("/test-svn")
async def test_svn_connection(
    request: SvnTestRequest,
    _: User = Depends(require_system_admin),
):
    """Test SVN connection using provided credentials"""
    try:
        uploader = SVNUploader(
            svn_url=request.svn_url,
            username=request.username,
            password=request.password
        )
        success = uploader.test_connection()
        return ApiResponse(
            success=success,
            message="SVN 连接成功" if success else "SVN 连接失败"
        )
    except Exception as e:
        return ApiResponse(success=False, message=f"SVN 连接失败: {str(e)}")


class NotifyTestRequest(BaseModel):
    webhook_url: str
    platform: str  # dingtalk / wecom / feishu


@router.post("/test-notify")
async def test_notification(
    request: NotifyTestRequest,
    _: User = Depends(require_system_admin),
):
    """测试 IM 通知连接"""
    test_msg = "这是一条测试消息 — GitLab 代码审查工具"
    try:
        if request.platform == "dingtalk":
            from app.services.im.dingtalk import DingTalkNotifier
            notifier = DingTalkNotifier(webhook_url=request.webhook_url, enabled=True)
            notifier.send_message(content=test_msg, msg_type="text", title="测试通知")
        elif request.platform == "wecom":
            from app.services.im.wecom import WeComNotifier
            notifier = WeComNotifier(webhook_url=request.webhook_url, enabled=True)
            notifier.send_message(content=test_msg, msg_type="text", title="测试通知")
        elif request.platform == "feishu":
            from app.services.im.feishu import FeishuNotifier
            notifier = FeishuNotifier(webhook_url=request.webhook_url, enabled=True)
            notifier.send_message(content=test_msg, msg_type="text", title="测试通知")
        else:
            return ApiResponse(success=False, message=f"不支持的平台: {request.platform}")

        return ApiResponse(success=True, message="测试消息已发送，请检查对应群聊")
    except Exception as e:
        return ApiResponse(success=False, message=f"发送失败: {str(e)}")

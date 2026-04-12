"""Webhook 接收端点 — 接收 GitLab Webhook 并异步处理"""
import threading
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.database import SessionLocal
from app.models.settings import Settings
from app.security import security_service
from app.services.webhook_worker import handle_merge_request_event, handle_push_event

router = APIRouter(tags=["webhook"])


@router.post("/review/webhook")
async def handle_webhook(request: Request):
    """接收 GitLab Webhook 请求，后台异步处理"""
    if not request.headers.get("content-type", "").startswith("application/json"):
        return JSONResponse({"error": "Invalid content type"}, status_code=400)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if not data:
        return JSONResponse({"error": "Empty payload"}, status_code=400)

    # 检查 Webhook 是否启用
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings or not settings.webhook_enabled:
            return JSONResponse(
                {"message": "Webhook 审查未启用"},
                status_code=200,
            )
    finally:
        db.close()

    # 提取 GitLab URL
    gitlab_url = request.headers.get("X-Gitlab-Instance")
    if not gitlab_url:
        repository = data.get("repository")
        if repository and repository.get("homepage"):
            try:
                parsed = urlparse(repository["homepage"])
                gitlab_url = f"{parsed.scheme}://{parsed.netloc}/"
            except Exception:
                pass
    if not gitlab_url:
        # 降级使用全局配置
        db = SessionLocal()
        try:
            settings = db.query(Settings).first()
            gitlab_url = settings.global_gitlab_url if settings else ""
        finally:
            db.close()

    if not gitlab_url:
        return JSONResponse({"error": "Missing GitLab URL"}, status_code=400)

    # 提取 Token：优先请求头 > 全局配置
    gitlab_token = request.headers.get("X-Gitlab-Token", "")
    if not gitlab_token:
        db = SessionLocal()
        try:
            settings = db.query(Settings).first()
            if settings and settings.global_gitlab_token:
                try:
                    gitlab_token = security_service.decrypt(settings.global_gitlab_token)
                except Exception:
                    logger.warning("全局 GitLab Token 解密失败")
        finally:
            db.close()

    if not gitlab_token:
        return JSONResponse({"error": "Missing GitLab access token"}, status_code=400)

    # 判断事件类型
    object_kind = data.get("object_kind")
    logger.info(f"收到 GitLab Webhook 事件: {object_kind}")

    if object_kind == "merge_request":
        thread = threading.Thread(
            target=handle_merge_request_event,
            args=(data, gitlab_token, gitlab_url),
            daemon=True,
        )
        thread.start()
        return JSONResponse(
            {"message": f"Webhook 已接收 (object_kind={object_kind})，正在异步处理"},
            status_code=200,
        )

    elif object_kind == "push":
        thread = threading.Thread(
            target=handle_push_event,
            args=(data, gitlab_token, gitlab_url),
            daemon=True,
        )
        thread.start()
        return JSONResponse(
            {"message": f"Webhook 已接收 (object_kind={object_kind})，正在异步处理"},
            status_code=200,
        )

    else:
        return JSONResponse(
            {"error": f"不支持的事件类型: {object_kind}，仅支持 merge_request 和 push"},
            status_code=400,
        )

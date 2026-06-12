"""Webhook 事件处理 Worker — 后台异步处理 GitLab Webhook 事件"""
import traceback
from datetime import datetime
from typing import Optional

from loguru import logger

from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models.settings import Settings
from app.models.project import Project
from app.models.webhook_review import MrReviewLog, PushReviewLog
from app.security import security_service
from app.services.webhook_handler import (
    MergeRequestHandler,
    PushHandler,
    filter_changes,
    match_branch,
)
from app.services.webhook_reviewer import WebhookReviewer
from app.services.notifier import Notifier
from app.services.llm_usage import LLMResult, record_token_usage


def _build_reviewer(settings: Settings) -> WebhookReviewer:
    """从 Settings 构建 WebhookReviewer 实例"""
    api_key = ""
    if settings.llm_api_key:
        try:
            api_key = security_service.decrypt(settings.llm_api_key)
        except Exception:
            logger.warning("LLM API Key 解密失败")

    return WebhookReviewer(
        api_url=settings.llm_api_url,
        api_key=api_key,
        model=settings.llm_model,
        max_tokens=settings.llm_max_tokens or 4096,
        temperature=float(settings.llm_temperature or 0.7),
        timeout=settings.llm_timeout or 120,
        max_retries=settings.llm_max_retries or 3,
        retry_delay=settings.llm_retry_delay or 5,
        review_style=settings.review_style or "professional",
        review_max_tokens=settings.review_max_tokens or 10000,
        custom_prompt=settings.webhook_review_prompt,
    )


def _build_notifier(settings: Settings, project: Optional[Project] = None) -> Notifier:
    """构建 Notifier 实例：优先项目级配置，回退全局配置"""
    return Notifier.from_project(project, settings)


def _resolve_project_for_webhook(db, webhook_data: dict) -> Optional[Project]:
    """根据 webhook 项目信息解析本地 Project，优先 project_id，其次 path_with_namespace，最后 name。"""
    if not webhook_data:
        return None

    project_info = webhook_data.get("project", {}) or {}
    raw_project_id = project_info.get("id")
    if raw_project_id is None:
        raw_project_id = webhook_data.get("project_id")

    webhook_project_id: Optional[int] = None
    if raw_project_id is not None:
        try:
            webhook_project_id = int(raw_project_id)
        except (TypeError, ValueError):
            webhook_project_id = None
    path_with_namespace = project_info.get("path_with_namespace")
    project_name = project_info.get("name")

    # 1) project_id 精确匹配（首选）
    if webhook_project_id is not None:
        project = db.query(Project).filter(Project.project_id == webhook_project_id).first()
        if project:
            return project

    # 2) path_with_namespace 匹配（兼容将 name 配置为 path_with_namespace 的场景）
    if path_with_namespace:
        project = db.query(Project).filter(Project.name == path_with_namespace).first()
        if project:
            return project

    # 3) name 回退
    if project_name:
        return db.query(Project).filter(Project.name == project_name).first()

    return None


def _llm_content(result) -> str:
    if isinstance(result, LLMResult):
        return result.content or ""
    return result or ""


def _llm_usage(result):
    return result.usage if isinstance(result, LLMResult) else None


def _record_webhook_token_usage(db, biz_type: str, review_log, usage) -> None:
    record_token_usage(
        db=db,
        biz_type=biz_type,
        biz_id=review_log.id,
        project_name=review_log.project_name,
        author=review_log.author,
        usage=usage,
        created_at_ts=review_log.updated_at,
    )


def handle_merge_request_event(
    webhook_data: dict, gitlab_token: str, gitlab_url: str
):
    """处理 GitLab Merge Request 事件（在后台线程中运行）"""
    db = SessionLocal()
    project: Optional[Project] = None
    try:
        settings = db.query(Settings).first()
        if not settings:
            logger.warning("系统设置未配置，跳过 Webhook 审查")
            return

        handler = MergeRequestHandler(webhook_data, gitlab_token, gitlab_url)
        project = _resolve_project_for_webhook(db, webhook_data)
        project_name = project.name if project else webhook_data["project"]["name"]
        logger.info("Merge Request Webhook 事件接收")

        obj_attrs = webhook_data.get("object_attributes", {})
        source_branch = obj_attrs.get("source_branch", "")
        target_branch = obj_attrs.get("target_branch", "")

        # 检查排除分支（目标分支或源分支在排除列表中则跳过）
        if project and project.exclude_branches:
            exclude_list = [
                b.strip()
                for b in project.exclude_branches.split(",")
                if b.strip()
            ]
            if exclude_list:
                if match_branch(target_branch, exclude_list):
                    logger.info(
                        f"MR 目标分支 '{target_branch}' 在排除列表 {exclude_list} 中，跳过审查"
                    )
                    return
                if match_branch(source_branch, exclude_list):
                    logger.info(
                        f"MR 源分支 '{source_branch}' 在排除列表 {exclude_list} 中，跳过审查"
                    )
                    return

        # 检查 draft MR
        is_draft = obj_attrs.get("draft") or obj_attrs.get("work_in_progress")
        if is_draft:
            msg = (
                f"[通知] MR 为草稿，未触发 AI 审查。\n"
                f"项目: {project_name}\n"
                f"作者: {webhook_data['user']['username']}\n"
                f"源分支: {obj_attrs.get('source_branch')}\n"
                f"目标分支: {obj_attrs.get('target_branch')}"
            )
            _build_notifier(settings, project).send_notification(content=msg)
            logger.info("MR 为 draft，仅发送通知")
            return

        # 仅处理 open/update
        if handler.action not in ("open", "update", "approved"):
            # GitLab 部分事件用 action，部分用 state；两者都取
            mr_state = obj_attrs.get("state")
            if mr_state in ("opened", "reopened"):
                # 处于 opened/reopened 状态，但 action 未知，尝试处理
                pass
            elif mr_state in ("merged", "closed"):
                logger.info(f"MR state={mr_state}，忽略")
                return
            else:
                logger.info(f"MR action={handler.action}, state={mr_state}，忽略")
                return

        # 去重检查
        last_commit_id = obj_attrs.get("last_commit", {}).get("id")
        source_branch = obj_attrs.get("source_branch", "")
        target_branch = obj_attrs.get("target_branch", "")

        if last_commit_id:
            exists = (
                db.query(MrReviewLog)
                .filter(
                    MrReviewLog.project_name == project_name,
                    MrReviewLog.source_branch == source_branch,
                    MrReviewLog.target_branch == target_branch,
                    MrReviewLog.last_commit_id == last_commit_id,
                )
                .first()
            )
            if exists:
                logger.info(f"MR last_commit_id {last_commit_id} 已存在，跳过")
                return

        # 获取 changes
        changes = handler.get_merge_request_changes()
        supported_ext = settings.supported_extensions or ".java,.py,.js"
        changes = filter_changes(changes, supported_ext)
        if not changes:
            logger.info("未检测到支持扩展名的代码修改")
            return

        additions = sum(c.get("additions", 0) for c in changes)
        deletions = sum(c.get("deletions", 0) for c in changes)

        # 获取 commits
        commits = handler.get_merge_request_commits()
        if not commits:
            logger.error("获取 MR commits 失败")
            return

        # 提取实际提交者
        commit_authors = list(dict.fromkeys(c.get("author_name", "Unknown") for c in commits))
        author = ", ".join(commit_authors) if commit_authors else webhook_data["user"]["username"]

        # 调用 LLM 审查
        reviewer = _build_reviewer(settings)
        commits_text = ";".join(c.get("title", "") for c in commits)
        llm_result = reviewer.review_and_strip_code(str(changes), commits_text)
        review_result = _llm_content(llm_result)
        score = WebhookReviewer.parse_review_score(review_result)

        # 回写 GitLab notes
        handler.add_merge_request_notes(f"Auto Review Result: \n{review_result}")

        # 发送 IM 通知
        im_msg = (
            f"### 🔀 {project_name}: Merge Request\n\n"
            f"#### 合并请求信息:\n"
            f"- **提交者:** {author}\n"
            f"- **源分支**: {source_branch}\n"
            f"- **目标分支**: {target_branch}\n"
            f"- **提交信息:** {commits_text}\n"
            f"- [查看合并详情]({obj_attrs.get('url', '')})\n\n"
            f"- **AI Review 结果:**\n\n{review_result}"
        )
        _build_notifier(settings, project).send_notification(
            content=im_msg, msg_type="markdown", title="Merge Request Review"
        )

        # 保存到数据库
        log = MrReviewLog(
            project_name=project_name,
            author=author,
            source_branch=source_branch,
            target_branch=target_branch,
            updated_at=int(datetime.now().timestamp()),
            commit_messages=commits_text,
            score=score,
            url=obj_attrs.get("url", ""),
            review_result=review_result,
            additions=additions,
            deletions=deletions,
            last_commit_id=last_commit_id,
        )
        db.add(log)
        try:
            db.commit()
            db.refresh(log)
        except IntegrityError as e:
            db.rollback()
            msg = str(getattr(e, "orig", e)).lower()
            if "uq_mr_review_log_dedup" in msg or "unique constraint failed" in msg:
                logger.info(f"MR last_commit_id {last_commit_id} 并发重复，跳过")
                return
            raise
        _record_webhook_token_usage(
            db=db,
            biz_type="webhook_mr",
            review_log=log,
            usage=_llm_usage(llm_result),
        )
        logger.info(f"MR 审查完成: {project_name}, 评分: {score}")

    except Exception as e:
        logger.error(f"MR Webhook 处理异常: {e}\n{traceback.format_exc()}")
        try:
            settings = db.query(Settings).first()
            if settings:
                _build_notifier(settings, project).send_notification(
                    content=f"Webhook 处理异常: {e}"
                )
        except Exception:
            pass
    finally:
        db.close()


def handle_push_event(webhook_data: dict, gitlab_token: str, gitlab_url: str):
    """处理 GitLab Push 事件（在后台线程中运行）"""
    db = SessionLocal()
    project: Optional[Project] = None
    try:
        settings = db.query(Settings).first()
        if not settings:
            logger.warning("系统设置未配置，跳过 Webhook 审查")
            return

        if not settings.push_review_enabled:
            logger.info("Push 事件审查未启用，跳过")
            return

        handler = PushHandler(webhook_data, gitlab_token, gitlab_url)
        project = _resolve_project_for_webhook(db, webhook_data)
        logger.info("Push Webhook 事件接收")

        branch = webhook_data.get("ref", "").replace("refs/heads/", "")

        # 检查排除分支
        if project and project.exclude_branches:
            exclude_list = [
                b.strip()
                for b in project.exclude_branches.split(",")
                if b.strip()
            ]
            if exclude_list and match_branch(branch, exclude_list):
                logger.info(
                    f"Push 分支 '{branch}' 在排除列表 {exclude_list} 中，跳过审查"
                )
                return

        commits = handler.get_push_commits()
        if not commits:
            logger.error("获取 Push commits 失败")
            return

        project_name = project.name if project else webhook_data.get("project", {}).get("name", "Unknown")
        last_commit_id = webhook_data.get("after") or commits[-1].get("id")

        # 去重检查（project + branch + last_commit_id）
        if last_commit_id:
            exists = (
                db.query(PushReviewLog)
                .filter(
                    PushReviewLog.project_name == project_name,
                    PushReviewLog.branch == branch,
                    PushReviewLog.last_commit_id == last_commit_id,
                )
                .first()
            )
            if exists:
                logger.info(f"Push last_commit_id {last_commit_id} 已存在，跳过")
                return

        # 获取 changes
        changes = handler.get_push_changes()
        supported_ext = settings.supported_extensions or ".java,.py,.js"
        changes = filter_changes(changes, supported_ext)

        review_result = "关注的文件没有修改"
        score = 0
        additions = 0
        deletions = 0
        llm_result = None

        if changes:
            commits_text = ";".join(c.get("message", "").strip() for c in commits)
            reviewer = _build_reviewer(settings)
            llm_result = reviewer.review_and_strip_code(str(changes), commits_text)
            review_result = _llm_content(llm_result)
            score = WebhookReviewer.parse_review_score(review_result)
            additions = sum(c.get("additions", 0) for c in changes)
            deletions = sum(c.get("deletions", 0) for c in changes)

            # 回写 GitLab comments
            handler.add_push_notes(f"Auto Review Result: \n{review_result}")

        # 发送 IM 通知
        im_msg = f"### 🚀 {project_name}: Push\n\n#### 提交记录:\n"
        for commit in commits:
            im_msg += (
                f"- **提交信息**: {commit.get('message', '').strip()}\n"
                f"- **提交者**: {commit.get('author', 'Unknown')}\n"
                f"- **时间**: {commit.get('timestamp', '')}\n\n"
            )
        if review_result:
            im_msg += f"#### AI Review 结果:\n{review_result}\n"

        _build_notifier(settings, project).send_notification(
            content=im_msg, msg_type="markdown", title=f"{project_name} Push Event"
        )

        # 保存到数据库
        log = PushReviewLog(
            project_name=project_name,
            author=webhook_data.get("user_username", "Unknown"),
            branch=branch,
            updated_at=int(datetime.now().timestamp()),
            commit_messages=";".join(c.get("message", "").strip() for c in commits),
            score=score,
            review_result=review_result,
            additions=additions,
            deletions=deletions,
            last_commit_id=last_commit_id,
        )
        db.add(log)
        try:
            db.commit()
            db.refresh(log)
        except IntegrityError as e:
            db.rollback()
            msg = str(getattr(e, "orig", e)).lower()
            if "uq_push_review_log_dedup" in msg or "unique constraint failed" in msg:
                logger.info(f"Push last_commit_id {last_commit_id} 并发重复，跳过")
                return
            raise
        _record_webhook_token_usage(
            db=db,
            biz_type="webhook_push",
            review_log=log,
            usage=_llm_usage(llm_result),
        )
        logger.info(f"Push 审查完成: {project_name}, 评分: {score}")

    except Exception as e:
        logger.error(f"Push Webhook 处理异常: {e}\n{traceback.format_exc()}")
    finally:
        db.close()

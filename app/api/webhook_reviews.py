"""Webhook 审查记录 API — 列表、统计、详情"""
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.api.deps import get_current_user
from app.models.webhook_review import MrReviewLog, PushReviewLog
from app.models.user import User, project_admins
from app.core.permissions import (
    author_matches_user_condition,
    get_readable_project_ids,
    is_self_identity,
    should_limit_to_self,
    should_limit_to_self_for_project,
)
from app.models.project import Project
from app.schemas.response import ApiResponse
from app.api.deps import get_current_user_full
from app.services.llm_usage import aggregate_token_usage_by_biz, empty_token_totals

router = APIRouter(prefix="/api/webhook-reviews", tags=["webhook-reviews"])


def _get_user_allowed_project_names(user: User, db: Session) -> Optional[List[str]]:
    """获取用户有权限的项目名称列表"""
    readable_ids = get_readable_project_ids(user, db)
    if readable_ids is None:
        # system_admin：看所有项目
        return None
    if not readable_ids:
        return []
    projects = db.query(Project.name).filter(Project.id.in_(readable_ids)).all()
    return [p[0] for p in projects]


def _get_user_admin_project_names(user: User, db: Session) -> List[str]:
    """获取用户管理的项目名称列表"""
    if not user.is_project_admin():
        return []
    admin_ids = {
        r[0]
        for r in db.execute(
            project_admins.select().where(project_admins.c.user_id == user.id)
        ).fetchall()
    }
    if not admin_ids:
        return []
    return [p[0] for p in db.query(Project.name).filter(Project.id.in_(admin_ids)).all()]


@router.get("")
async def list_webhook_reviews(
    review_type: str = Query("mr", description="mr 或 push"),
    project_name: Optional[str] = None,
    author: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """列出 Webhook 审查记录（分页）"""
    from sqlalchemy import or_

    model = MrReviewLog if review_type == "mr" else PushReviewLog
    query = db.query(model)

    # 按用户有权限的项目过滤数据
    allowed_project_names = _get_user_allowed_project_names(current_user, db)
    if allowed_project_names == []:
        # 用户没有任何项目权限，返回空
        return ApiResponse(
            success=True,
            data={
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "pages": 0,
            },
        )
    elif allowed_project_names is not None:
        # 非系统管理员，按项目名过滤
        query = query.filter(model.project_name.in_(allowed_project_names))

    # 按项目判断是否需要限制到自己
    if current_user.is_project_admin():
        # 项目管理员：自己管理项目的全部数据 + 其他项目的自己数据
        admin_project_names = _get_user_admin_project_names(current_user, db)
        if admin_project_names:
            query = query.filter(
                or_(
                    model.project_name.in_(admin_project_names),
                    author_matches_user_condition(model.author, current_user),
                )
            )
        else:
            # 没有管理任何项目，只能看自己的
            query = query.filter(author_matches_user_condition(model.author, current_user))
    elif should_limit_to_self(current_user):
        query = query.filter(author_matches_user_condition(model.author, current_user))

    if project_name:
        query = query.filter(model.project_name == project_name)
    if author:
        query = query.filter(model.author.contains(author))
    if start_date:
        ts = int(datetime.fromisoformat(start_date).timestamp())
        query = query.filter(model.updated_at >= ts)
    if end_date:
        ts = int(datetime.fromisoformat(end_date + "T23:59:59").timestamp())
        query = query.filter(model.updated_at <= ts)

    total = query.count()
    items = (
        query.order_by(desc(model.updated_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    token_biz_type = "webhook_mr" if review_type == "mr" else "webhook_push"
    token_totals = aggregate_token_usage_by_biz(
        db, token_biz_type, [item.id for item in items]
    )

    # 转为字典
    records = []
    for item in items:
        record = {
            "id": item.id,
            "project_name": item.project_name,
            "author": item.author,
            "updated_at": item.updated_at,
            "commit_messages": item.commit_messages,
            "score": item.score,
            "review_result": item.review_result,
            "additions": item.additions,
            "deletions": item.deletions,
            "token_usage": token_totals.get(item.id, empty_token_totals()),
        }
        if review_type == "mr":
            record["source_branch"] = item.source_branch
            record["target_branch"] = item.target_branch
            record["url"] = item.url
            record["last_commit_id"] = item.last_commit_id
        else:
            record["branch"] = item.branch
        records.append(record)

    return ApiResponse(
        success=True,
        data={
            "items": records,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    )


@router.get("/stats")
async def get_webhook_stats(
    review_type: str = Query("mr", description="mr 或 push"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """获取 Webhook 审查统计数据（供图表使用）"""
    from sqlalchemy import or_

    model = MrReviewLog if review_type == "mr" else PushReviewLog
    query = db.query(model)

    # 按用户有权限的项目过滤数据
    allowed_project_names = _get_user_allowed_project_names(current_user, db)
    if allowed_project_names == []:
        # 用户没有任何项目权限，返回空统计
        return ApiResponse(
            success=True,
            data={
                "total_count": 0,
                "avg_score": 0,
                "by_project": [],
                "by_author": [],
            },
        )
    elif allowed_project_names is not None:
        # 非系统管理员，按项目名过滤
        query = query.filter(model.project_name.in_(allowed_project_names))

    # 按项目判断是否需要限制到自己
    if current_user.is_project_admin():
        # 项目管理员：自己管理项目的全部数据 + 其他项目的自己数据
        admin_project_names = _get_user_admin_project_names(current_user, db)
        if admin_project_names:
            query = query.filter(
                or_(
                    model.project_name.in_(admin_project_names),
                    author_matches_user_condition(model.author, current_user),
                )
            )
        else:
            # 没有管理任何项目，只能看自己的
            query = query.filter(author_matches_user_condition(model.author, current_user))
    elif should_limit_to_self(current_user):
        query = query.filter(author_matches_user_condition(model.author, current_user))

    if start_date:
        ts = int(datetime.fromisoformat(start_date).timestamp())
        query = query.filter(model.updated_at >= ts)
    if end_date:
        ts = int(datetime.fromisoformat(end_date + "T23:59:59").timestamp())
        query = query.filter(model.updated_at <= ts)

    # 按项目统计
    project_stats = (
        query.with_entities(
            model.project_name,
            func.count(model.id).label("count"),
            func.avg(model.score).label("avg_score"),
        )
        .group_by(model.project_name)
        .all()
    )

    # 按作者统计
    author_stats = (
        query.with_entities(
            model.author,
            func.count(model.id).label("count"),
            func.avg(model.score).label("avg_score"),
            func.sum(model.additions).label("total_additions"),
            func.sum(model.deletions).label("total_deletions"),
        )
        .group_by(model.author)
        .all()
    )

    # 总计
    total_count = query.count()
    avg_score_row = query.with_entities(func.avg(model.score)).scalar()
    avg_score = round(float(avg_score_row), 1) if avg_score_row else 0

    return ApiResponse(
        success=True,
        data={
            "total_count": total_count,
            "avg_score": avg_score,
            "by_project": [
                {
                    "project_name": s.project_name,
                    "count": s.count,
                    "avg_score": round(float(s.avg_score), 1) if s.avg_score else 0,
                }
                for s in project_stats
            ],
            "by_author": [
                {
                    "author": s.author,
                    "count": s.count,
                    "avg_score": round(float(s.avg_score), 1) if s.avg_score else 0,
                    "total_additions": s.total_additions or 0,
                    "total_deletions": s.total_deletions or 0,
                }
                for s in author_stats
            ],
        },
    )


@router.get("/{review_id}")
async def get_webhook_review_detail(
    review_id: int,
    review_type: str = Query("mr", description="mr 或 push"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """获取 Webhook 审查记录详情"""
    model = MrReviewLog if review_type == "mr" else PushReviewLog
    item = db.query(model).filter(model.id == review_id).first()
    if not item:
        return ApiResponse(success=False, message="记录不存在")

    # 检查用户是否有权限查看该记录
    allowed_project_names = _get_user_allowed_project_names(current_user, db)
    if allowed_project_names == [] or (allowed_project_names is not None and item.project_name not in allowed_project_names):
        raise HTTPException(status_code=403, detail="您没有权限查看此记录")

    # 按项目判断是否需要限制到自己
    if current_user.is_project_admin():
        # 项目管理员：自己管理项目的全部数据 + 其他项目的自己数据
        admin_project_names = _get_user_admin_project_names(current_user, db)
        if item.project_name not in admin_project_names and not is_self_identity(
            current_user, item.author
        ):
            raise HTTPException(status_code=403, detail="您没有权限查看此记录")
    elif should_limit_to_self(current_user) and not is_self_identity(
        current_user, item.author
    ):
        raise HTTPException(status_code=403, detail="您没有权限查看此记录")

    record = {
        "id": item.id,
        "project_name": item.project_name,
        "author": item.author,
        "updated_at": item.updated_at,
        "commit_messages": item.commit_messages,
        "score": item.score,
        "review_result": item.review_result,
        "additions": item.additions,
        "deletions": item.deletions,
        "token_usage": aggregate_token_usage_by_biz(
            db,
            "webhook_mr" if review_type == "mr" else "webhook_push",
            [item.id],
        ).get(item.id, empty_token_totals()),
    }
    if review_type == "mr":
        record["source_branch"] = item.source_branch
        record["target_branch"] = item.target_branch
        record["url"] = item.url
    else:
        record["branch"] = item.branch

    return ApiResponse(success=True, data=record)


@router.delete("/{review_id}")
async def delete_webhook_review(
    review_id: int,
    review_type: str = Query("mr", description="mr 或 push"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """删除 Webhook 审查记录 - 仅项目管理员或系统管理员可删除"""
    # 只有项目管理员或系统管理员可以删除
    if not current_user.is_system_admin() and not current_user.is_project_admin():
        raise HTTPException(status_code=403, detail="您没有权限删除审查记录")

    model = MrReviewLog if review_type == "mr" else PushReviewLog
    item = db.query(model).filter(model.id == review_id).first()
    if not item:
        return ApiResponse(success=False, message="记录不存在")
    
    # 检查用户是否有权限删除该记录（必须是有权管理的项目）
    if not current_user.is_system_admin():
        # 非系统管理员，通过项目名找到项目ID，检查是否有该项目的管理权限
        project = db.query(Project).filter(Project.name == item.project_name).first()
        if not project:
            raise HTTPException(status_code=403, detail="您没有权限删除此审查记录")
        admin_stmt = project_admins.select().where(
            project_admins.c.user_id == current_user.id,
            project_admins.c.project_id == project.id
        )
        if not db.execute(admin_stmt).fetchone():
            raise HTTPException(status_code=403, detail="您没有权限删除此审查记录")

    db.delete(item)
    db.commit()
    return ApiResponse(success=True, message="删除成功")

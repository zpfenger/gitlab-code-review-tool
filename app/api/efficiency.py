"""人员能效 API

提供三个端点：
- GET  /api/efficiency/list       列表 + 团队概览
- GET  /api/efficiency/detail     单人详情（summary + trend + commits）
- POST /api/efficiency/recompute  系统管理员手动补算指定日期
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session

from app.api.users import get_current_user_full
from app.database import get_db
from app.models.commit_record import CommitRecord
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.project import Project
from app.models.user import User, project_admins, project_members
from app.schemas.response import ApiResponse

router = APIRouter(prefix="/api/efficiency", tags=["efficiency"])


# 排序字段白名单：避免任意列注入
SORT_FIELDS = {
    "score": EmployeeEfficiencyDaily.review_score,
    "additions": EmployeeEfficiencyDaily.additions,
    "deletions": EmployeeEfficiencyDaily.deletions,
    "commits": EmployeeEfficiencyDaily.commits_count,
    "files_changed": EmployeeEfficiencyDaily.files_changed,
}


# ──────────────── 权限工具 ────────────────

def _allowed_project_names(user: User, db: Session) -> Optional[List[str]]:
    """系统管理员返回 None（无需限制）；
    项目角色返回其有权访问的项目名列表；普通用户返回 []"""
    if user.is_system_admin():
        return None
    if user.is_project_admin() or user.is_project_member():
        admin_ids = {
            r[0] for r in db.execute(
                project_admins.select().where(
                    project_admins.c.user_id == user.id
                )
            ).fetchall()
        }
        member_ids = {
            r[0] for r in db.execute(
                project_members.select().where(
                    project_members.c.user_id == user.id
                )
            ).fetchall()
        }
        ids = admin_ids | member_ids
        if not ids:
            return []
        return [p[0] for p in db.query(Project.name).filter(
            Project.id.in_(ids)
        ).all()]
    return []


def _restrict_query_by_user(query, current_user: User, db: Session):
    """按用户角色裁剪 EmployeeEfficiencyDaily 查询：
    - system_admin 不限制
    - project_admin 看其管理项目里出现过的人（projects_involved JSON LIKE）
    - project_member 仅看与自己 email 相同的行
    - 其他角色 / 无角色：空结果
    """
    if current_user.is_system_admin():
        return query
    if current_user.is_project_admin():
        names = _allowed_project_names(current_user, db) or []
        if not names:
            return query.filter(False)
        conds = [
            EmployeeEfficiencyDaily.projects_involved.like(f'%"{n}"%')
            for n in names
        ]
        return query.filter(or_(*conds))
    if current_user.is_project_member():
        if not current_user.email:
            return query.filter(False)
        return query.filter(
            EmployeeEfficiencyDaily.author_email == current_user.email
        )
    return query.filter(False)


# ──────────────── 序列化 ────────────────

def _serialize(row: EmployeeEfficiencyDaily) -> dict:
    return {
        "id": row.id,
        "author_email": row.author_email,
        "author_name": row.author_name,
        "stat_date": row.stat_date.isoformat(),
        "commits_count": row.commits_count,
        "additions": row.additions,
        "deletions": row.deletions,
        "files_changed": row.files_changed,
        "new_files": row.new_files,
        "deleted_files": row.deleted_files,
        "projects_involved": json.loads(row.projects_involved or "[]"),
        "review_score": row.review_score,
        "review_grade": row.review_grade,
        "review_summary": row.review_summary,
        "work_summary": (
            json.loads(row.work_summary) if row.work_summary else []
        ),
        "llm_status": row.llm_status,
        "llm_error": row.llm_error,
    }


def _resolve_date_filter(
    date_str: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
):
    """统一解析日期过滤参数。
    返回 (mode, target, start, end)：mode 为 "range" 或 "single"。
    """
    if start_date or end_date:
        s = date.fromisoformat(start_date) if start_date else None
        e = date.fromisoformat(end_date) if end_date else None
        return "range", None, s, e
    target = (
        date.fromisoformat(date_str)
        if date_str
        else date.today() - timedelta(days=1)
    )
    return "single", target, None, None


def _apply_date_filter(query, mode, target, start, end):
    if mode == "range":
        if start:
            query = query.filter(
                EmployeeEfficiencyDaily.stat_date >= start
            )
        if end:
            query = query.filter(
                EmployeeEfficiencyDaily.stat_date <= end
            )
    else:
        query = query.filter(
            EmployeeEfficiencyDaily.stat_date == target
        )
    return query


# ──────────────── /list ────────────────

@router.get("/list")
async def list_efficiency(
    date_str: Optional[str] = Query(None, alias="date"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """列表 + 团队概览"""
    mode, target, start, end = _resolve_date_filter(
        date_str, start_date, end_date
    )

    q = db.query(EmployeeEfficiencyDaily)
    q = _restrict_query_by_user(q, current_user, db)
    q = _apply_date_filter(q, mode, target, start, end)

    sort_col = SORT_FIELDS.get(sort_by, EmployeeEfficiencyDaily.review_score)
    direction = desc if order == "desc" else asc
    q = q.order_by(direction(sort_col))

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    stats_q = db.query(EmployeeEfficiencyDaily)
    stats_q = _restrict_query_by_user(stats_q, current_user, db)
    stats_q = _apply_date_filter(stats_q, mode, target, start, end)

    agg = stats_q.with_entities(
        func.count(EmployeeEfficiencyDaily.id).label("n"),
        func.coalesce(
            func.sum(EmployeeEfficiencyDaily.commits_count), 0
        ).label("total_commits"),
        func.coalesce(
            func.sum(EmployeeEfficiencyDaily.additions), 0
        ).label("total_additions"),
        func.coalesce(
            func.sum(EmployeeEfficiencyDaily.deletions), 0
        ).label("total_deletions"),
        func.coalesce(
            func.avg(EmployeeEfficiencyDaily.review_score), 0
        ).label("avg_score"),
    ).one()

    team_stats = {
        "person_count": int(agg.n or 0),
        "total_commits": int(agg.total_commits or 0),
        "total_additions": int(agg.total_additions or 0),
        "total_deletions": int(agg.total_deletions or 0),
        "avg_score": round(float(agg.avg_score or 0), 1),
    }

    return ApiResponse(
        success=True,
        data={
            "items": [_serialize(r) for r in items],
            "total": total,
            "team_stats": team_stats,
        },
    )


# ──────────────── /detail ────────────────

@router.get("/detail")
async def get_detail(
    email: str = Query(...),
    date_str: Optional[str] = Query(None, alias="date"),
    trend_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """个人详情 = 当日 summary + 近 N 天 trend + 当日 commits 列表"""
    target = (
        date.fromisoformat(date_str)
        if date_str
        else date.today() - timedelta(days=1)
    )

    # 普通成员只能看自己的详情
    if not current_user.is_system_admin():
        if (
            current_user.is_project_member()
            and not current_user.is_project_admin()
        ):
            if (current_user.email or "").lower() != email.lower():
                raise HTTPException(403, "无权查看他人能效详情")

    summary = (
        db.query(EmployeeEfficiencyDaily)
        .filter_by(author_email=email, stat_date=target)
        .first()
    )

    trend_rows = (
        db.query(EmployeeEfficiencyDaily)
        .filter(
            EmployeeEfficiencyDaily.author_email == email,
            EmployeeEfficiencyDaily.stat_date
            >= target - timedelta(days=trend_days - 1),
            EmployeeEfficiencyDaily.stat_date <= target,
        )
        .order_by(EmployeeEfficiencyDaily.stat_date.asc())
        .all()
    )

    trend_data = [
        {
            "stat_date": r.stat_date.isoformat(),
            "commits_count": r.commits_count,
            "additions": r.additions,
            "deletions": r.deletions,
            "review_score": r.review_score,
        }
        for r in trend_rows
    ]

    day_start = datetime.combine(target, datetime.min.time())
    day_end = datetime.combine(target, datetime.max.time())
    commit_rows = (
        db.query(CommitRecord)
        .filter(
            CommitRecord.author_email == email,
            CommitRecord.commit_date >= day_start,
            CommitRecord.commit_date <= day_end,
        )
        .all()
    )
    commits_data = [
        {
            "commit_sha": c.commit_sha,
            "branch": c.branch,
            "author_name": c.author_name,
            "commit_date": (
                c.commit_date.isoformat() if c.commit_date else None
            ),
            "review_status": c.review_status,
        }
        for c in commit_rows
    ]

    return ApiResponse(
        success=True,
        data={
            "summary": _serialize(summary) if summary else None,
            "trend": trend_data,
            "commits": commits_data,
        },
    )


# ──────────────── /recompute ────────────────

class RecomputeRequest(BaseModel):
    date: str
    force: bool = False


@router.post("/recompute")
async def recompute(
    body: RecomputeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """管理员手动补算指定日期的人员能效数据"""
    if not current_user.is_system_admin():
        raise HTTPException(403, "仅系统管理员可补算")

    target = date.fromisoformat(body.date)

    # 非 force 模式下，若已有记录则跳过
    if not body.force:
        existing = (
            db.query(EmployeeEfficiencyDaily)
            .filter_by(stat_date=target)
            .count()
        )
        if existing > 0:
            return ApiResponse(
                success=True,
                message=(
                    f"已存在 {existing} 条记录，如需重算请勾选 force"
                ),
                data={"skipped": True, "existing": existing},
            )

    from app.models import Settings
    from app.security import security_service
    from app.services.efficiency_aggregator import EfficiencyAggregator
    from app.services.gitlab_client import GitLabClient

    settings = db.query(Settings).first()
    if not settings or not settings.global_gitlab_url:
        raise HTTPException(400, "GitLab 全局配置缺失")

    def _factory(proj):
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
            raise RuntimeError(f"项目 {proj.name} 无 Token")
        return GitLabClient(
            gitlab_url=settings.global_gitlab_url,
            access_token=tk,
        )

    llm_cfg = {
        "api_url": settings.llm_api_url,
        "api_key": (
            security_service.decrypt(settings.llm_api_key)
            if settings.llm_api_key
            else ""
        ),
        "model": settings.llm_model,
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
        "retry_delay": settings.llm_retry_delay,
    }
    top_n = getattr(settings, "efficiency_work_summary_top_n", 5) or 5

    try:
        aggregator = EfficiencyAggregator(
            db=db,
            gitlab_client_factory=_factory,
            llm_config=llm_cfg,
            top_n=top_n,
        )
        result = aggregator.aggregate(target)
        return ApiResponse(success=True, data=result)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"补算 {target} 失败")
        raise HTTPException(500, f"补算失败: {e}")

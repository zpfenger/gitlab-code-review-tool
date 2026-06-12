"""Token usage detail and statistics API."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.api.deps import require_system_admin
from app.database import get_db
from app.models.token_usage import TokenUsageLog
from app.models.user import User
from app.schemas.response import ApiResponse

router = APIRouter(prefix="/api/token-usage", tags=["token-usage"])


def _parse_date_bounds(
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[Optional[int], Optional[int]]:
    try:
        start_ts = (
            int(datetime.combine(date.fromisoformat(start_date), time.min).timestamp())
            if start_date else None
        )
        end_ts = (
            int(datetime.combine(date.fromisoformat(end_date), time.max).timestamp())
            if end_date else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date format") from exc
    return start_ts, end_ts


def _apply_filters(
    query,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    biz_type: Optional[str] = None,
    model: Optional[str] = None,
    project_name: Optional[str] = None,
):
    start_ts, end_ts = _parse_date_bounds(start_date, end_date)
    if start_ts is not None:
        query = query.filter(TokenUsageLog.created_at_ts >= start_ts)
    if end_ts is not None:
        query = query.filter(TokenUsageLog.created_at_ts <= end_ts)
    if biz_type:
        query = query.filter(TokenUsageLog.biz_type == biz_type)
    if model:
        query = query.filter(TokenUsageLog.model == model)
    if project_name:
        query = query.filter(TokenUsageLog.project_name == project_name)
    return query


def _apply_non_date_filters(
    query,
    *,
    biz_type: Optional[str] = None,
    model: Optional[str] = None,
    project_name: Optional[str] = None,
):
    return _apply_filters(
        query,
        biz_type=biz_type,
        model=model,
        project_name=project_name,
    )


def _sum_query(query) -> dict:
    row = query.with_entities(
        func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0).label("prompt"),
        func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0).label("completion"),
        func.coalesce(func.sum(TokenUsageLog.total_tokens), 0).label("total"),
    ).one()
    return {
        "prompt_tokens": int(row.prompt or 0),
        "completion_tokens": int(row.completion or 0),
        "total_tokens": int(row.total or 0),
    }


def _serialize(row: TokenUsageLog) -> dict:
    return {
        "id": row.id,
        "biz_type": row.biz_type,
        "biz_id": row.biz_id,
        "project_name": row.project_name,
        "author": row.author,
        "model": row.model,
        "prompt_tokens": row.prompt_tokens or 0,
        "completion_tokens": row.completion_tokens or 0,
        "total_tokens": row.total_tokens or 0,
        "created_at_ts": row.created_at_ts,
        "created_at": datetime.fromtimestamp(row.created_at_ts).isoformat(),
    }


@router.get("/stats")
async def get_token_usage_stats(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    biz_type: Optional[str] = None,
    model: Optional[str] = None,
    project_name: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    base = _apply_filters(
        db.query(TokenUsageLog),
        start_date=start_date,
        end_date=end_date,
        biz_type=biz_type,
        model=model,
        project_name=project_name,
    )

    today = date.today()
    month_start = today.replace(day=1)
    today_query = _apply_filters(
        _apply_non_date_filters(
            db.query(TokenUsageLog),
            biz_type=biz_type,
            model=model,
            project_name=project_name,
        ),
        start_date=today.isoformat(),
        end_date=today.isoformat(),
    )
    month_query = _apply_filters(
        _apply_non_date_filters(
            db.query(TokenUsageLog),
            biz_type=biz_type,
            model=model,
            project_name=project_name,
        ),
        start_date=month_start.isoformat(),
        end_date=today.isoformat(),
    )

    by_biz_type = (
        base.with_entities(
            TokenUsageLog.biz_type,
            func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0).label("prompt"),
            func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0).label("completion"),
            func.coalesce(func.sum(TokenUsageLog.total_tokens), 0).label("total"),
            func.count(TokenUsageLog.id).label("count"),
        )
        .group_by(TokenUsageLog.biz_type)
        .order_by(desc("total"))
        .all()
    )

    trend_rows = (
        base.with_entities(
            TokenUsageLog.created_at_ts,
            TokenUsageLog.prompt_tokens,
            TokenUsageLog.completion_tokens,
            TokenUsageLog.total_tokens,
        )
        .all()
    )
    daily_totals = {}
    for row in trend_rows:
        day = datetime.fromtimestamp(row.created_at_ts).date().isoformat()
        bucket = daily_totals.setdefault(
            day,
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        bucket["prompt_tokens"] += int(row.prompt_tokens or 0)
        bucket["completion_tokens"] += int(row.completion_tokens or 0)
        bucket["total_tokens"] += int(row.total_tokens or 0)

    by_model = (
        base.with_entities(
            TokenUsageLog.model,
            func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0).label("prompt"),
            func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0).label("completion"),
            func.coalesce(func.sum(TokenUsageLog.total_tokens), 0).label("total"),
            func.count(TokenUsageLog.id).label("count"),
        )
        .group_by(TokenUsageLog.model)
        .order_by(desc("total"))
        .all()
    )

    return ApiResponse(
        success=True,
        data={
            "summary": _sum_query(base),
            "today": _sum_query(today_query),
            "month": _sum_query(month_query),
            "by_biz_type": [
                {
                    "biz_type": row.biz_type,
                    "count": int(row.count or 0),
                    "prompt_tokens": int(row.prompt or 0),
                    "completion_tokens": int(row.completion or 0),
                    "total_tokens": int(row.total or 0),
                }
                for row in by_biz_type
            ],
            "daily_trend": [
                {"date": day, **daily_totals[day]}
                for day in sorted(daily_totals)
            ],
            "by_model": [
                {
                    "model": row.model,
                    "count": int(row.count or 0),
                    "prompt_tokens": int(row.prompt or 0),
                    "completion_tokens": int(row.completion or 0),
                    "total_tokens": int(row.total or 0),
                }
                for row in by_model
            ],
        },
    )


@router.get("")
async def list_token_usage(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    biz_type: Optional[str] = None,
    model: Optional[str] = None,
    project_name: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_system_admin),
):
    query = _apply_filters(
        db.query(TokenUsageLog),
        start_date=start_date,
        end_date=end_date,
        biz_type=biz_type,
        model=model,
        project_name=project_name,
    )
    total = query.count()
    rows = (
        query.order_by(desc(TokenUsageLog.created_at_ts), desc(TokenUsageLog.id))
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ApiResponse(
        success=True,
        data={
            "items": [_serialize(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
        },
    )

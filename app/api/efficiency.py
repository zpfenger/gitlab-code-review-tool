"""人员能效 API

提供三个端点：
- GET  /api/efficiency/list       列表 + 团队概览
- GET  /api/efficiency/detail     单人详情（summary + trend + commits）
- POST /api/efficiency/recompute  系统管理员手动补算指定日期
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, or_
from sqlalchemy.orm import Session

from app.api.users import get_current_user_full
from app.database import get_db, SessionLocal
from app.models.commit_record import CommitRecord
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
from app.models.project import Project
from app.models.user import User, project_admins, project_members
from app.schemas.response import ApiResponse

router = APIRouter(prefix="/api/efficiency", tags=["efficiency"])


# ──────────────── 补算任务状态管理 ────────────────

_recompute_lock = threading.Lock()

_recompute_task = {
    "is_running": False,
    "task_type": None,        # "daily" | "monthly"
    "start_date": None,
    "end_date": None,
    "year_month": None,
    "total_days": 0,
    "processed_days": 0,
    "skipped_days": 0,
    "failed_days": 0,
    "current_date": None,     # 当前正在处理的日期
    "processed": [],
    "skipped": [],
    "failed": [],
    "cancelled": False,
    "error": None,
}


def _reset_recompute_state():
    """重置补算状态（调用方需持有锁）"""
    _recompute_task.update({
        "is_running": False,
        "task_type": None,
        "start_date": None,
        "end_date": None,
        "year_month": None,
        "total_days": 0,
        "processed_days": 0,
        "skipped_days": 0,
        "failed_days": 0,
        "current_date": None,
        "processed": [],
        "skipped": [],
        "failed": [],
        "cancelled": False,
        "error": None,
    })


def _build_llm_config(settings):
    """从 Settings 构建 LLM 配置（复用）"""
    from app.security import security_service
    return {
        "api_url": settings.llm_api_url,
        "api_key": (
            security_service.decrypt(settings.llm_api_key)
            if settings.llm_api_key else ""
        ),
        "model": settings.llm_model,
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
        "retry_delay": settings.llm_retry_delay,
        "review_max_tokens": settings.review_max_tokens or 10000,
    }


def _run_daily_recompute(start: date, end: date, force: bool):
    """后台线程：按天补算人员能效数据"""
    from app.models import Settings
    from app.security import security_service
    from app.services.efficiency_aggregator import EfficiencyAggregator
    from app.services.gitlab_client import GitLabClient

    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings or not settings.global_gitlab_url:
            with _recompute_lock:
                _recompute_task["is_running"] = False
                _recompute_task["error"] = "GitLab 全局配置缺失"
            return

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

        llm_cfg = _build_llm_config(settings)
        top_n = getattr(settings, "efficiency_work_summary_top_n", 5) or 5

        aggregator = EfficiencyAggregator(
            db=db,
            gitlab_client_factory=_factory,
            llm_config=llm_cfg,
            top_n=top_n,
        )

        current = start
        while current <= end:
            # 检查取消标志
            with _recompute_lock:
                if _recompute_task["cancelled"]:
                    logger.info("补算任务被用户取消")
                    _recompute_task["is_running"] = False
                    return
                _recompute_task["current_date"] = current.isoformat()

            # 非 force 模式下，若已有记录则跳过
            if not force:
                existing = (
                    db.query(EmployeeEfficiencyDaily)
                    .filter_by(stat_date=current)
                    .count()
                )
                if existing > 0:
                    with _recompute_lock:
                        _recompute_task["skipped"].append(current.isoformat())
                        _recompute_task["skipped_days"] += 1
                        _recompute_task["processed_days"] += 1
                    current += timedelta(days=1)
                    continue

            try:
                aggregator.aggregate(current)
                with _recompute_lock:
                    _recompute_task["processed"].append(current.isoformat())
                    _recompute_task["processed_days"] += 1
            except Exception as ex:
                logger.exception(f"补算 {current} 失败")
                with _recompute_lock:
                    _recompute_task["failed"].append(
                        {"date": current.isoformat(), "error": str(ex)}
                    )
                    _recompute_task["failed_days"] += 1
                    _recompute_task["processed_days"] += 1

            current += timedelta(days=1)

        with _recompute_lock:
            _recompute_task["is_running"] = False
            _recompute_task["current_date"] = None
        logger.info(
            f"补算完成：处理 {len(_recompute_task['processed'])} 天，"
            f"跳过 {len(_recompute_task['skipped'])} 天，"
            f"失败 {len(_recompute_task['failed'])} 天"
        )

    except Exception as ex:
        logger.exception(f"补算任务异常: {ex}")
        with _recompute_lock:
            _recompute_task["is_running"] = False
            _recompute_task["error"] = str(ex)
    finally:
        db.close()


def _run_monthly_recompute(year_month: str, force: bool):
    """后台线程：补算月度能效数据"""
    from app.models import Settings
    from app.security import security_service
    from app.services.efficiency_monthly_aggregator import EfficiencyMonthlyAggregator

    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings:
            with _recompute_lock:
                _recompute_task["is_running"] = False
                _recompute_task["error"] = "系统配置缺失"
            return

        llm_cfg = _build_llm_config(settings)
        top_n = getattr(settings, "efficiency_work_summary_top_n", 10) or 10

        aggregator = EfficiencyMonthlyAggregator(
            db=db,
            llm_config=llm_cfg,
            top_n=top_n,
        )
        result = aggregator.aggregate(year_month)

        with _recompute_lock:
            _recompute_task["is_running"] = False
            _recompute_task["processed"] = [result]
        logger.info(f"月度补算完成: {result}")

    except Exception as ex:
        logger.exception(f"月度补算 {year_month} 失败")
        with _recompute_lock:
            _recompute_task["is_running"] = False
            _recompute_task["error"] = str(ex)
    finally:
        db.close()


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


def _restrict_query_by_user(query, current_user: User, db: Session,
                            model_class=None):
    """按用户角色裁剪查询（通用，支持 daily 和 monthly 模型）：
    - system_admin 不限制
    - project_admin 看其管理项目里出现过的人（projects_involved JSON LIKE）
    - project_member 仅看与自己 email 相同的行
    - 其他角色 / 无角色：空结果
    """
    if model_class is None:
        model_class = EmployeeEfficiencyDaily
    if current_user.is_system_admin():
        return query
    if current_user.is_project_admin():
        names = _allowed_project_names(current_user, db) or []
        if not names:
            return query.filter(False)
        conds = [
            model_class.projects_involved.like(f'%"{n}"%')
            for n in names
        ]
        return query.filter(or_(*conds))
    if current_user.is_project_member():
        if not current_user.email:
            return query.filter(False)
        return query.filter(
            model_class.author_email == current_user.email
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


# ──────────────── 区间聚合辅助 ────────────────

def _list_range_aggregated(
    base_query, db, current_user, sort_by, order, limit, offset,
    start, end,
):
    """区间模式：按 author_email 聚合统计"""
    rows = base_query.all()

    # 按 author_email 分组聚合
    grouped = {}
    for r in rows:
        email = r.author_email
        if email not in grouped:
            grouped[email] = {
                "author_email": email,
                "author_name": r.author_name,
                "commits_count": 0,
                "additions": 0,
                "deletions": 0,
                "files_changed": 0,
                "scores": [],
                "projects": set(),
            }
        g = grouped[email]
        g["commits_count"] += r.commits_count or 0
        g["additions"] += r.additions or 0
        g["deletions"] += r.deletions or 0
        g["files_changed"] += r.files_changed or 0
        if r.review_score is not None and r.review_score > 0:
            g["scores"].append(r.review_score)
        try:
            g["projects"].update(json.loads(r.projects_involved or "[]"))
        except (ValueError, TypeError):
            pass

    # 构造结果列表
    items = []
    for g in grouped.values():
        scores = g["scores"]
        avg_score = round(sum(scores) / len(scores)) if scores else None
        items.append({
            "author_email": g["author_email"],
            "author_name": g["author_name"],
            "commits_count": g["commits_count"],
            "additions": g["additions"],
            "deletions": g["deletions"],
            "files_changed": g["files_changed"],
            "review_score": avg_score,
            "review_grade": _map_score_to_grade(avg_score),
            "projects_involved": sorted(g["projects"]),
        })

    # 排序
    sort_key_map = {
        "score": lambda x: x.get("review_score") or 0,
        "additions": lambda x: x["additions"],
        "deletions": lambda x: x["deletions"],
        "commits": lambda x: x["commits_count"],
        "files_changed": lambda x: x["files_changed"],
    }
    sort_fn = sort_key_map.get(sort_by, sort_key_map["score"])
    items.sort(key=sort_fn, reverse=(order == "desc"))

    total = len(items)
    items = items[offset:offset + limit]

    # 团队概览（基于聚合后数据）
    team_stats = {
        "person_count": total,
        "total_commits": sum(g["commits_count"] for g in grouped.values()),
        "total_additions": sum(g["additions"] for g in grouped.values()),
        "total_deletions": sum(g["deletions"] for g in grouped.values()),
        "avg_score": round(
            sum(i["review_score"] or 0 for i in items) /
            max(len([i for i in items if i["review_score"]]), 1), 1
        ) if items else 0,
    }

    return ApiResponse(
        success=True,
        data={"items": items, "total": total, "team_stats": team_stats},
    )


def _map_score_to_grade(score):
    """分数映射到等级（API 层复用）"""
    if score is None:
        return None
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "一般"
    return "待改进"


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
    """列表 + 团队概览

    单日模式：返回每人当日记录（现有行为）
    区间模式：返回每人区间汇总（聚合后）
    """
    mode, target, start, end = _resolve_date_filter(
        date_str, start_date, end_date
    )

    q = db.query(EmployeeEfficiencyDaily)
    q = _restrict_query_by_user(q, current_user, db)
    q = _apply_date_filter(q, mode, target, start, end)

    if mode == "range":
        # 区间模式：按 author_email 聚合
        return _list_range_aggregated(
            q, db, current_user, sort_by, order, limit, offset,
            start, end,
        )

    # 单日模式：保持现有行为
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
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    trend_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """个人详情 = 当日 summary + 近 N 天 trend + 当日 commits 列表

    支持区间模式：传 start_date + end_date 时返回区间内每日明细列表。
    """
    # 普通成员只能看自己的详情
    if not current_user.is_system_admin():
        if (
            current_user.is_project_member()
            and not current_user.is_project_admin()
        ):
            if (current_user.email or "").lower() != email.lower():
                raise HTTPException(403, "无权查看他人能效详情")

    # 区间模式：返回区间内每日明细
    if start_date and end_date:
        s = date.fromisoformat(start_date)
        e = date.fromisoformat(end_date)
        daily_rows = (
            db.query(EmployeeEfficiencyDaily)
            .filter(
                EmployeeEfficiencyDaily.author_email == email,
                EmployeeEfficiencyDaily.stat_date >= s,
                EmployeeEfficiencyDaily.stat_date <= e,
            )
            .order_by(EmployeeEfficiencyDaily.stat_date.asc())
            .all()
        )
        daily_items = [_serialize(r) for r in daily_rows]
        return ApiResponse(
            success=True,
            data={"daily_items": daily_items},
        )

    # 单日模式
    target = (
        date.fromisoformat(date_str)
        if date_str
        else date.today() - timedelta(days=1)
    )

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
    start_date: str
    end_date: str
    force: bool = False


@router.post("/recompute")
async def recompute(
    body: RecomputeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """管理员手动补算指定日期范围的人员能效数据（异步执行）"""
    logger.info(f"收到补算请求: start_date={body.start_date}, end_date={body.end_date}, force={body.force}")

    if not current_user.is_system_admin():
        raise HTTPException(403, "仅系统管理员可补算")

    try:
        s = date.fromisoformat(body.start_date)
        e = date.fromisoformat(body.end_date)
    except ValueError as ex:
        logger.error(f"日期格式错误: {ex}")
        raise HTTPException(400, f"日期格式错误: {ex}")

    if s > e:
        logger.error(f"开始日期 {s} 晚于结束日期 {e}")
        raise HTTPException(400, "开始日期不能晚于结束日期")

    with _recompute_lock:
        if _recompute_task["is_running"]:
            raise HTTPException(409, "补算任务正在执行中，请稍后再试")
        _recompute_task.update({
            "is_running": True,
            "task_type": "daily",
            "start_date": body.start_date,
            "end_date": body.end_date,
            "year_month": None,
            "total_days": (e - s).days + 1,
            "processed_days": 0,
            "skipped_days": 0,
            "failed_days": 0,
            "current_date": None,
            "processed": [],
            "skipped": [],
            "failed": [],
            "cancelled": False,
            "error": None,
        })

    t = threading.Thread(
        target=_run_daily_recompute,
        args=(s, e, body.force),
        daemon=True,
    )
    t.start()

    return ApiResponse(
        success=True,
        message="补算任务已启动，请在页面查看进度",
        data={"task_type": "daily", "total_days": (e - s).days + 1},
    )


# ──────────────── /monthly/list ────────────────

MONTHLY_SORT_FIELDS = {
    "score": EmployeeEfficiencyMonthly.review_score,
    "additions": EmployeeEfficiencyMonthly.additions,
    "deletions": EmployeeEfficiencyMonthly.deletions,
    "commits": EmployeeEfficiencyMonthly.commits_count,
    "files_changed": EmployeeEfficiencyMonthly.files_changed,
    "active_days": EmployeeEfficiencyMonthly.active_days,
}


def _serialize_monthly(row: EmployeeEfficiencyMonthly) -> dict:
    return {
        "id": row.id,
        "author_email": row.author_email,
        "author_name": row.author_name,
        "year_month": row.year_month,
        "commits_count": row.commits_count,
        "additions": row.additions,
        "deletions": row.deletions,
        "files_changed": row.files_changed,
        "new_files": row.new_files,
        "deleted_files": row.deleted_files,
        "active_days": row.active_days,
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


def _restrict_monthly_query(query, current_user: User, db: Session):
    """复用 daily 表的权限逻辑裁剪 monthly 查询"""
    return _restrict_query_by_user(query, current_user, db,
                                   EmployeeEfficiencyMonthly)


@router.get("/monthly/list")
async def monthly_list(
    year_month: str = Query(...),
    sort_by: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """月度列表 + 团队概览"""
    # 参数校验
    if not re.match(r'^\d{4}-\d{2}$', year_month):
        raise HTTPException(400, "year_month 格式错误，应为 YYYY-MM")

    q = db.query(EmployeeEfficiencyMonthly)
    q = _restrict_monthly_query(q, current_user, db)
    q = q.filter(EmployeeEfficiencyMonthly.year_month == year_month)

    sort_col = MONTHLY_SORT_FIELDS.get(sort_by,
                                        EmployeeEfficiencyMonthly.review_score)
    direction = desc if order == "desc" else asc
    q = q.order_by(direction(sort_col))

    total = q.count()
    items = q.offset(offset).limit(limit).all()

    # 团队概览
    stats_q = db.query(EmployeeEfficiencyMonthly)
    stats_q = _restrict_monthly_query(stats_q, current_user, db)
    stats_q = stats_q.filter(
        EmployeeEfficiencyMonthly.year_month == year_month
    )

    agg = stats_q.with_entities(
        func.count(EmployeeEfficiencyMonthly.id).label("n"),
        func.coalesce(
            func.sum(EmployeeEfficiencyMonthly.commits_count), 0
        ).label("total_commits"),
        func.coalesce(
            func.sum(EmployeeEfficiencyMonthly.additions), 0
        ).label("total_additions"),
        func.coalesce(
            func.sum(EmployeeEfficiencyMonthly.deletions), 0
        ).label("total_deletions"),
        func.coalesce(
            func.avg(EmployeeEfficiencyMonthly.review_score), 0
        ).label("avg_score"),
        func.coalesce(
            func.sum(EmployeeEfficiencyMonthly.active_days), 0
        ).label("total_active_days"),
    ).one()

    team_stats = {
        "person_count": int(agg.n or 0),
        "total_commits": int(agg.total_commits or 0),
        "total_additions": int(agg.total_additions or 0),
        "total_deletions": int(agg.total_deletions or 0),
        "avg_score": round(float(agg.avg_score or 0), 1),
        "total_active_days": int(agg.total_active_days or 0),
    }

    return ApiResponse(
        success=True,
        data={
            "items": [_serialize_monthly(r) for r in items],
            "total": total,
            "team_stats": team_stats,
        },
    )


# ──────────────── /monthly/detail ────────────────

def _next_month_str(year_month: str) -> str:
    """返回下月第一天的 ISO 字符串"""
    year, month = int(year_month[:4]), int(year_month[5:7])
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"


@router.get("/monthly/detail")
async def monthly_detail(
    email: str = Query(...),
    year_month: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """月度详情 = 月度 summary + 每日 trend"""
    # 参数校验
    if not re.match(r'^\d{4}-\d{2}$', year_month):
        raise HTTPException(400, "year_month 格式错误，应为 YYYY-MM")

    # 权限检查
    if not current_user.is_system_admin():
        if (current_user.is_project_member()
                and not current_user.is_project_admin()):
            if (current_user.email or "").lower() != email.lower():
                raise HTTPException(403, "无权查看他人月度能效详情")

    summary = (
        db.query(EmployeeEfficiencyMonthly)
        .filter_by(author_email=email, year_month=year_month)
        .first()
    )

    # 查询该月每日数据用于趋势图
    ym_start = date.fromisoformat(f"{year_month}-01")
    ym_end = date.fromisoformat(_next_month_str(year_month))

    daily_rows = (
        db.query(EmployeeEfficiencyDaily)
        .filter(
            EmployeeEfficiencyDaily.author_email == email,
            EmployeeEfficiencyDaily.stat_date >= ym_start,
            EmployeeEfficiencyDaily.stat_date < ym_end,
        )
        .order_by(EmployeeEfficiencyDaily.stat_date.asc())
        .all()
    )

    daily_trend = [
        {
            "stat_date": r.stat_date.isoformat(),
            "commits_count": r.commits_count,
            "additions": r.additions,
            "deletions": r.deletions,
            "review_score": r.review_score,
        }
        for r in daily_rows
    ]

    return ApiResponse(
        success=True,
        data={
            "summary": _serialize_monthly(summary) if summary else None,
            "daily_trend": daily_trend,
        },
    )


# ──────────────── /monthly/recompute ────────────────

class MonthlyRecomputeRequest(BaseModel):
    year_month: str
    force: bool = False


@router.post("/monthly/recompute")
async def monthly_recompute(
    body: MonthlyRecomputeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_full),
):
    """管理员手动补算指定月份的月度能效数据（异步执行）"""
    logger.info(f"收到月度补算请求: year_month={body.year_month}, force={body.force}")

    if not current_user.is_system_admin():
        raise HTTPException(403, "仅系统管理员可补算月度数据")

    if not re.match(r'^\d{4}-\d{2}$', body.year_month):
        logger.error(f"year_month 格式错误: {body.year_month}")
        raise HTTPException(400, "year_month 格式错误，应为 YYYY-MM")

    # 非 force 模式下，检查是否已有记录
    if not body.force:
        existing = (
            db.query(EmployeeEfficiencyMonthly)
            .filter_by(year_month=body.year_month)
            .count()
        )
        if existing > 0:
            return ApiResponse(
                success=True,
                message=f"已存在 {existing} 条月度记录，如需重算请勾选 force",
                data={"skipped": True, "existing": existing},
            )

    with _recompute_lock:
        if _recompute_task["is_running"]:
            raise HTTPException(409, "补算任务正在执行中，请稍后再试")
        _recompute_task.update({
            "is_running": True,
            "task_type": "monthly",
            "start_date": None,
            "end_date": None,
            "year_month": body.year_month,
            "total_days": 1,
            "processed_days": 0,
            "skipped_days": 0,
            "failed_days": 0,
            "current_date": body.year_month,
            "processed": [],
            "skipped": [],
            "failed": [],
            "cancelled": False,
            "error": None,
        })

    t = threading.Thread(
        target=_run_monthly_recompute,
        args=(body.year_month, body.force),
        daemon=True,
    )
    t.start()

    return ApiResponse(
        success=True,
        message="月度补算任务已启动",
        data={"task_type": "monthly", "year_month": body.year_month},
    )


# ──────────────── /recompute/status ────────────────

@router.get("/recompute/status")
async def recompute_status(
    current_user: User = Depends(get_current_user_full),
):
    """查询补算任务进度"""
    if not current_user.is_system_admin():
        raise HTTPException(403, "仅系统管理员可查询补算状态")

    with _recompute_lock:
        status = dict(_recompute_task)

    return ApiResponse(success=True, data=status)


# ──────────────── /recompute/cancel ────────────────

@router.post("/recompute/cancel")
async def recompute_cancel(
    current_user: User = Depends(get_current_user_full),
):
    """取消正在执行的补算任务"""
    if not current_user.is_system_admin():
        raise HTTPException(403, "仅系统管理员可取消补算")

    with _recompute_lock:
        if not _recompute_task["is_running"]:
            return ApiResponse(
                success=True,
                message="当前没有正在执行的补算任务",
            )
        _recompute_task["cancelled"] = True

    return ApiResponse(
        success=True,
        message="补算取消请求已发送，将在当前批次完成后停止",
    )

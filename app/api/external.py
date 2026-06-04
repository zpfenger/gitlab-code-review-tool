"""外部 API 路由模块

为 HR 系统等外部服务提供员工能效数据查询接口。

端点：
- GET /api/external/efficiency/list    查询员工能效数据（需 API Key 认证）
- GET /api/external/efficiency/daily   查询指定日期的能效数据
"""
from __future__ import annotations

import hmac
import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.efficiency import _serialize
from app.database import get_db
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.settings import Settings
from app.schemas.response import ApiResponse
from app.security import security_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/external", tags=["external"])


# ──────────────── 认证依赖项 ────────────────


def verify_api_key(
    x_api_key: str = Header(..., description="外部 API Key"),
    db: Session = Depends(get_db),
) -> str:
    """验证 X-API-Key 请求头

    1. 从 Settings 表读取加密存储的 external_api_key
    2. 使用 security_service 解密
    3. 与请求头中的 key 比对
    4. 无效则返回 401

    Returns:
        验证通过的 API Key（可用于审计日志）
    """
    settings = db.query(Settings).first()
    if not settings or not settings.external_api_key:
        logger.warning("外部 API 认证失败：未配置 External API Key")
        raise HTTPException(
            status_code=401,
            detail="外部 API 未配置，请联系管理员设置 External API Key",
        )

    try:
        stored_key = security_service.decrypt(settings.external_api_key)
    except ValueError:
        logger.warning("外部 API 认证失败：API Key 解密失败")
        raise HTTPException(
            status_code=401,
            detail="API Key 解密失败，请联系管理员重新配置",
        )

    if not hmac.compare_digest(x_api_key.encode(), stored_key.encode()):
        logger.warning("外部 API 认证失败：无效的 API Key")
        raise HTTPException(
            status_code=401,
            detail="无效的 API Key",
        )

    return x_api_key


# ──────────────── 路由端点 ────────────────


@router.get("/efficiency/list")
def get_efficiency_list(
    *,
    db: Session = Depends(get_db),
    _api_key: str = Depends(verify_api_key),
    author_email: Optional[str] = Query(None, description="按邮箱精确筛选"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
) -> ApiResponse:
    """查询员工能效数据

    支持按邮箱、日期范围筛选，分页返回。
    """
    query = db.query(EmployeeEfficiencyDaily)

    # 按邮箱筛选
    if author_email:
        query = query.filter(EmployeeEfficiencyDaily.author_email == author_email)

    # 日期范围筛选
    if start_date:
        try:
            sd = date.fromisoformat(start_date)
            query = query.filter(EmployeeEfficiencyDaily.stat_date >= sd)
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date 格式错误，需 YYYY-MM-DD")

    if end_date:
        try:
            ed = date.fromisoformat(end_date)
            query = query.filter(EmployeeEfficiencyDaily.stat_date <= ed)
        except ValueError:
            raise HTTPException(status_code=400, detail="end_date 格式错误，需 YYYY-MM-DD")

    # 默认返回最近 30 天
    if not start_date and not end_date:
        default_start = date.today() - timedelta(days=30)
        query = query.filter(EmployeeEfficiencyDaily.stat_date >= default_start)

    # 统计总数
    total = query.count()

    # 分页
    items = (
        query.order_by(EmployeeEfficiencyDaily.stat_date.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ApiResponse.ok(
        data={
            "items": [_serialize(r) for r in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/efficiency/daily")
def get_efficiency_daily(
    *,
    db: Session = Depends(get_db),
    _api_key: str = Depends(verify_api_key),
    date_str: Optional[str] = Query(
        None, alias="date", description="查询日期 YYYY-MM-DD，默认为前一天"
    ),
) -> ApiResponse:
    """查询指定日期的能效数据

    - 默认查询前一天的数据
    - 返回每个员工的能效记录及 llm_status
    - 当 llm_status 为 pending/failed/skipped 时，返回提示信息
    """
    # 解析日期参数，默认前一天
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(
                status_code=400, detail="date 格式错误，需 YYYY-MM-DD"
            )
    else:
        target_date = date.today() - timedelta(days=1)

    # 查询该日期的所有记录
    rows = (
        db.query(EmployeeEfficiencyDaily)
        .filter(EmployeeEfficiencyDaily.stat_date == target_date)
        .all()
    )

    # 无记录视为 pending
    if not rows:
        return ApiResponse.ok(
            data={
                "date": target_date.isoformat(),
                "llm_status": "pending",
                "message": "能效数据尚未生成，请稍后重试",
                "items": [],
            }
        )

    # 根据记录的 llm_status 汇总
    statuses = {r.llm_status for r in rows}
    if statuses == {"success"}:
        overall_status = "success"
    elif "success" in statuses:
        overall_status = "partial"
    elif "failed" in statuses:
        overall_status = "failed"
    else:
        # 全部 pending 或 skipped
        overall_status = next(iter(statuses))

    # 构造响应
    result = {
        "date": target_date.isoformat(),
        "generated_at": max(r.updated_at for r in rows).isoformat() if rows else None,
        "llm_status": overall_status,
        "items": [_serialize(r) for r in rows] if overall_status == "success" else [],
    }

    # 非 success 时附加提示
    if overall_status != "success":
        messages = {
            "pending": "能效数据尚未生成，请稍后重试",
            "skipped": "该日期数据已跳过",
            "failed": "能效数据生成失败",
            "partial": "部分员工能效数据未完成",
        }
        result["message"] = messages.get(overall_status, "")

    return ApiResponse.ok(data=result)

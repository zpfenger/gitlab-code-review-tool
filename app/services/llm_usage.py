"""LLM usage parsing and persistence helpers."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Optional

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.token_usage import TokenUsageLog


@dataclass(frozen=True)
class TokenUsage:
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class LLMResult:
    content: Optional[str]
    usage: Optional[TokenUsage]

    def __bool__(self) -> bool:
        return bool(self.content)

    def __str__(self) -> str:
        return self.content or ""

    def __contains__(self, item: str) -> bool:
        return item in (self.content or "")

    def __eq__(self, other):
        if isinstance(other, str):
            return (self.content or "") == other
        return super().__eq__(other)

    def strip(self) -> str:
        return (self.content or "").strip()


def _is_valid_token_count(value) -> bool:
    return isinstance(value, int) and value >= 0


def parse_usage(data: dict, model: str) -> Optional[TokenUsage]:
    """Extract OpenAI-compatible usage from a response payload."""
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        logger.warning("LLM 响应缺失 usage 字段，跳过 token 记录")
        return None

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    total_tokens = usage.get("total_tokens")
    if not all(
        _is_valid_token_count(v)
        for v in (prompt_tokens, completion_tokens, total_tokens)
    ):
        logger.warning(f"LLM usage 字段畸形，跳过 token 记录: {usage}")
        return None

    response_model = data.get("model") if isinstance(data, dict) else None
    return TokenUsage(
        model=response_model or model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def empty_token_totals() -> dict:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def record_token_usage(
    *,
    db: Session,
    biz_type: str,
    biz_id: Optional[int],
    project_name: Optional[str],
    author: Optional[str],
    usage: Optional[TokenUsage],
    created_at_ts: Optional[int] = None,
    commit: bool = True,
) -> Optional[TokenUsageLog]:
    """Persist token usage without interrupting the caller's business flow."""
    if usage is None:
        return None

    try:
        row = TokenUsageLog(
            biz_type=biz_type,
            biz_id=biz_id,
            project_name=project_name,
            author=author,
            model=usage.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            created_at_ts=created_at_ts or int(time.time()),
        )
        db.add(row)
        if commit:
            db.commit()
        return row
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(f"记录 token usage 失败，已忽略: {exc}")
        return None


def aggregate_token_usage_by_biz(
    db: Session,
    biz_type: str,
    biz_ids: Iterable[int],
) -> dict[int, dict]:
    """Return summed token usage keyed by business id."""
    unique_ids = [i for i in dict.fromkeys(biz_ids) if i is not None]
    if not unique_ids:
        return {}

    totals = {biz_id: empty_token_totals() for biz_id in unique_ids}
    rows = (
        db.query(
            TokenUsageLog.biz_id,
            func.coalesce(func.sum(TokenUsageLog.prompt_tokens), 0).label("prompt"),
            func.coalesce(func.sum(TokenUsageLog.completion_tokens), 0).label("completion"),
            func.coalesce(func.sum(TokenUsageLog.total_tokens), 0).label("total"),
        )
        .filter(
            TokenUsageLog.biz_type == biz_type,
            TokenUsageLog.biz_id.in_(unique_ids),
        )
        .group_by(TokenUsageLog.biz_id)
        .all()
    )

    for row in rows:
        totals[row.biz_id] = {
            "prompt_tokens": int(row.prompt or 0),
            "completion_tokens": int(row.completion or 0),
            "total_tokens": int(row.total or 0),
        }
    return totals

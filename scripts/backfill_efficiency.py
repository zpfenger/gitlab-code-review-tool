"""
回填脚本：按指定日期范围补算 employee_efficiency_daily

用法:
    python scripts/backfill_efficiency.py --start 2026-05-01 --end 2026-05-27
    python scripts/backfill_efficiency.py --days 7   # 最近 7 天
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from app.database import SessionLocal, init_db
from app.models import Settings
from app.security import security_service
from app.services.efficiency_aggregator import EfficiencyAggregator
from app.services.gitlab_client import GitLabClient


def main():
    parser = argparse.ArgumentParser(
        description="回填人员能效历史数据"
    )
    parser.add_argument("--start", help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--days", type=int,
                        help="最近 N 天（与 start/end 互斥）")
    args = parser.parse_args()

    if args.days:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=args.days - 1)
    else:
        if not args.start or not args.end:
            parser.error("必须指定 --start 和 --end，或使用 --days")
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)

    logger.info(f"回填范围: {start} ~ {end}")

    init_db()
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if not settings:
            logger.error("Settings 未配置，请先完成系统设置")
            return

        if not settings.global_gitlab_url:
            logger.error("GitLab URL 未配置")
            return

        def _factory(proj):
            """使用全局 Token 构造 GitLabClient"""
            tk = None
            if settings.global_gitlab_token:
                try:
                    tk = security_service.decrypt(settings.global_gitlab_token)
                except ValueError:
                    pass
            if not tk:
                raise RuntimeError("全局 GitLab Token 未配置")
            return GitLabClient(gitlab_url=settings.global_gitlab_url,
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
        top_n = getattr(settings, "efficiency_work_summary_top_n", 5) or 5

        aggregator = EfficiencyAggregator(
            db=db, gitlab_client_factory=_factory,
            llm_config=llm_cfg, top_n=top_n,
        )

        current = start
        success_count = 0
        fail_count = 0
        while current <= end:
            logger.info(f"=== 回填 {current} ===")
            try:
                result = aggregator.aggregate(current)
                logger.info(f"完成: {result}")
                success_count += 1
            except Exception as e:
                logger.exception(f"回填 {current} 失败: {e}")
                fail_count += 1
            current += timedelta(days=1)

        logger.info(f"回填结束: 成功 {success_count} 天, 失败 {fail_count} 天")
    finally:
        db.close()


if __name__ == "__main__":
    main()

"""人员能效月度聚合服务

职责：
1. 读取指定月份的所有 daily 数据
2. 按 author_email 分组聚合（求和 + 去重）
3. 调用 LLM 生成月度总结（串行，2 秒间隔）
4. UPSERT 写入 employee_efficiency_monthly
"""
from __future__ import annotations
import json
import time
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy.orm import Session

from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
from app.services.efficiency_llm import call_and_parse_monthly


def _safe_json_loads(text: str, default=None):
    """防御性 JSON 解析，失败返回 default"""
    if not text:
        return default if default is not None else []
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"JSON 解析失败，原始值: {text[:100]}")
        return default if default is not None else []


class EfficiencyMonthlyAggregator:
    """月度能效聚合器：从 daily 表读取 → 聚合 → LLM → 写入 monthly 表"""

    def __init__(
        self,
        db: Session,
        llm_config: Dict[str, Any],
        top_n: int = 10,
        llm_interval: int = 2,
        custom_prompt_template: Optional[str] = None,
        excluded_emails: Optional[list] = None,
    ):
        """
        Args:
            db: SQLAlchemy session
            llm_config: LLM 配置
            top_n: 工作总结条目上限
            llm_interval: LLM 调用间隔（秒）
            custom_prompt_template: 自定义月度提示词模板（可选）
            excluded_emails: 排除的邮箱列表（可选）
        """
        self.db = db
        self.llm_config = llm_config
        self.top_n = top_n
        self.llm_interval = llm_interval
        self.custom_prompt_template = custom_prompt_template
        self.excluded_emails = set(e.lower() for e in (excluded_emails or []))

    def aggregate(self, year_month: str) -> Dict[str, Any]:
        """对指定月份做一次聚合（幂等，重复调用会 UPSERT）

        Args:
            year_month: 格式 "YYYY-MM"

        Returns:
            {"year_month", "authors_total", "authors_success", "authors_failed"}
        """
        logger.info(f"开始月度能效聚合: {year_month}")

        # 1. 查询该月所有 daily 数据
        query = (
            self.db.query(EmployeeEfficiencyDaily)
            .filter(
                EmployeeEfficiencyDaily.stat_date >= f"{year_month}-01",
                EmployeeEfficiencyDaily.stat_date < _next_month(year_month),
            )
        )

        # 过滤排除的邮箱
        if self.excluded_emails:
            query = query.filter(
                EmployeeEfficiencyDaily.author_email.notin_(list(self.excluded_emails))
            )

        daily_rows = query.all()

        if not daily_rows:
            logger.warning(f"月份 {year_month} 无 daily 数据")
            return {
                "year_month": year_month,
                "authors_total": 0,
                "authors_success": 0,
                "authors_failed": 0,
            }

        # 2. 按 author_email 分组
        grouped: Dict[str, List[EmployeeEfficiencyDaily]] = {}
        for row in daily_rows:
            grouped.setdefault(row.author_email, []).append(row)

        logger.info(f"月度聚合: {year_month}, 共 {len(grouped)} 位作者")

        # 3. 逐个作者聚合（串行，避免 LLM 限流）
        success = 0
        failed = 0
        for i, (email, records) in enumerate(grouped.items(), 1):
            try:
                self._aggregate_author(email, records, year_month)
                success += 1
                logger.info(f"月度聚合进度: [{i}/{len(grouped)}] {email} 完成")
            except Exception as e:
                logger.exception(f"月度聚合失败 [{email}]: {e}")
                failed += 1

            # LLM 限流间隔
            if i < len(grouped):
                time.sleep(self.llm_interval)

        result = {
            "year_month": year_month,
            "authors_total": len(grouped),
            "authors_success": success,
            "authors_failed": failed,
        }
        logger.info(f"月度能效聚合完成: {result}")
        return result

    def _aggregate_author(
        self,
        email: str,
        daily_records: List[EmployeeEfficiencyDaily],
        year_month: str,
    ) -> None:
        """聚合单个作者的月度数据并 UPSERT"""
        first = daily_records[0]

        # 统计字段求和
        commits_count = sum(r.commits_count or 0 for r in daily_records)
        additions = sum(r.additions or 0 for r in daily_records)
        deletions = sum(r.deletions or 0 for r in daily_records)
        files_changed = sum(r.files_changed or 0 for r in daily_records)
        new_files = sum(r.new_files or 0 for r in daily_records)
        deleted_files = sum(r.deleted_files or 0 for r in daily_records)
        active_days = len(daily_records)

        # 项目合并去重
        all_projects = set()
        for r in daily_records:
            all_projects.update(_safe_json_loads(r.projects_involved, []))

        # review_score 算术平均
        scores = [r.review_score for r in daily_records
                  if r.review_score is not None and r.review_score > 0]
        avg_score = round(sum(scores) / len(scores)) if scores else None

        # 构造 LLM 所需的每日评分摘要
        daily_summary_parts = []
        for r in sorted(daily_records, key=lambda x: x.stat_date):
            score_str = f"{r.review_score}分" if r.review_score else "未评分"
            daily_summary_parts.append(
                f"- {r.stat_date}: {score_str}, "
                f"+{r.additions}/-{r.deletions}, "
                f"{r.commits_count}次提交"
            )
        daily_scores_summary = "\n".join(daily_summary_parts)

        # 调用 LLM
        llm_result = call_and_parse_monthly(
            api_url=self.llm_config["api_url"],
            api_key=self.llm_config["api_key"],
            model=self.llm_config["model"],
            author_name=first.author_name,
            year_month=year_month,
            active_days=active_days,
            commits_count=commits_count,
            additions=additions,
            deletions=deletions,
            projects="、".join(sorted(all_projects)),
            daily_scores_summary=daily_scores_summary,
            top_n=self.top_n,
            max_tokens=self.llm_config.get("max_tokens", 4096),
            # 评分任务固定低温，保证同模型重复打分结果稳定
            temperature=self.llm_config.get("temperature", 0.0),
            timeout=self.llm_config.get("timeout", 240),
            max_retries=self.llm_config.get("max_retries", 3),
            retry_delay=self.llm_config.get("retry_delay", 10),
            custom_prompt_template=self.custom_prompt_template,
        )

        # UPSERT
        existing = (
            self.db.query(EmployeeEfficiencyMonthly)
            .filter_by(author_email=email, year_month=year_month)
            .first()
        )

        values = dict(
            author_email=email,
            author_name=first.author_name,
            year_month=year_month,
            commits_count=commits_count,
            additions=additions,
            deletions=deletions,
            files_changed=files_changed,
            new_files=new_files,
            deleted_files=deleted_files,
            active_days=active_days,
            projects_involved=json.dumps(sorted(all_projects),
                                          ensure_ascii=False),
            summary_top_n=self.top_n,
        )

        if llm_result["success"]:
            # 月度评分使用算术平均，LLM 返回的作为参考
            values.update(
                review_score=avg_score,
                review_grade=_map_score_to_grade(avg_score),
                review_summary=llm_result["review_summary"],
                work_summary=json.dumps(llm_result["work_summary"],
                                         ensure_ascii=False),
                llm_status="success",
                llm_error=None,
            )
        else:
            values.update(
                review_score=None,
                review_grade=None,
                review_summary=None,
                work_summary=None,
                llm_status="failed",
                llm_error="LLM call failed or returned empty",
            )

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            self.db.add(EmployeeEfficiencyMonthly(**values))
        self.db.commit()


def _next_month(year_month: str) -> str:
    """计算下一个月的第一天（用于日期范围过滤）"""
    year, month = int(year_month[:4]), int(year_month[5:7])
    if month == 12:
        return f"{year + 1}-01-01"
    return f"{year}-{month + 1:02d}-01"


def _map_score_to_grade(score: Optional[int]) -> Optional[str]:
    """分数映射到等级"""
    if score is None:
        return None
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "一般"
    return "待改进"

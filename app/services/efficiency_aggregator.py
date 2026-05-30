"""人员能效聚合服务

职责：
1. 从 GitLab 拉取指定日期的所有项目所有分支的 commits
2. 跨项目跨分支按 commit sha 去重
3. 按 author_email 分组（email 为空的过滤）
4. 累加代码量统计
5. 调 1 次 LLM 同时拿评分 + 工作总结
6. UPSERT 写入 employee_efficiency_daily
"""
from __future__ import annotations
import json
from datetime import date, datetime
from typing import Callable, Dict, Set, Any

from loguru import logger
from sqlalchemy.orm import Session

from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.project import Project
from app.services.diff_utils import count_diff_lines
from app.services.efficiency_llm import call_and_parse


class EfficiencyAggregator:
    """日报跑完后的人员能效聚合器"""

    def __init__(
        self,
        db: Session,
        gitlab_client_factory: Callable[[Project], Any],
        llm_config: Dict[str, Any],
        top_n: int = 5,
    ):
        """
        Args:
            db: SQLAlchemy session
            gitlab_client_factory: 给 Project 返回 GitLabClient 的工厂
                                   （便于注入 mock，且每个项目可能有独立 token）
            llm_config: {"api_url", "api_key", "model", 可选 timeout/temperature 等}
            top_n: 工作总结条目上限
        """
        self.db = db
        self.gitlab_client_factory = gitlab_client_factory
        self.llm_config = llm_config
        self.top_n = top_n

    # ── 主入口 ────────────────────────────────────────
    def aggregate(self, target_date: date) -> Dict[str, Any]:
        """对指定日期做一次聚合（幂等，重复调用会 UPSERT）"""
        logger.info(f"开始聚合人员能效: {target_date}")

        per_author: Dict[str, Dict[str, Any]] = {}

        projects = self.db.query(Project).filter(Project.is_active == True).all()
        global_seen_sha: Set[str] = set()

        for project in projects:
            try:
                client = self.gitlab_client_factory(project)
                self._collect_project(project, client, target_date,
                                       per_author, global_seen_sha)
            except Exception as e:
                logger.error(f"项目 {project.name} 聚合失败: {e}")

        success = 0
        failed = 0
        for email, data in per_author.items():
            try:
                self._upsert_author(email, data, target_date)
                success += 1
            except Exception as e:
                logger.exception(f"写入 {email} 能效记录失败: {e}")
                failed += 1

        result = {
            "target_date": target_date.isoformat(),
            "authors_total": len(per_author),
            "authors_success": success,
            "authors_failed": failed,
        }
        logger.info(f"人员能效聚合完成: {result}")
        return result

    def _collect_project(
        self,
        project: Project,
        client: Any,
        target_date: date,
        per_author: Dict[str, Dict[str, Any]],
        global_seen_sha: Set[str],
    ) -> None:
        """拉取该项目当日所有分支的 commits，按 author 累加"""
        exclude_branches = []
        if project.exclude_branches:
            exclude_branches = [
                b.strip() for b in project.exclude_branches.split(",")
                if b.strip()
            ]

        all_branches = client.get_branches(project.project_id) or []
        branches = [b for b in all_branches
                    if b.get("name") not in exclude_branches]

        since_iso = datetime.combine(target_date,
                                      datetime.min.time()).isoformat() + "Z"
        until_iso = datetime.combine(target_date,
                                      datetime.max.time()).isoformat() + "Z"

        for branch_info in branches:
            branch_name = branch_info.get("name", "")
            commits = client.get_commits(
                project.project_id,
                since=since_iso,
                until=until_iso,
                ref_name=branch_name,
                exclude_merge_commits=True,
            ) or []

            for commit in commits:
                sha = commit.get("id")
                if not sha or sha in global_seen_sha:
                    continue
                email = (commit.get("author_email") or "").strip()
                if not email or email.endswith("@noreply"):
                    continue
                global_seen_sha.add(sha)

                bucket = per_author.setdefault(email, {
                    "author_name": commit.get("author_name") or email,
                    "commits": [],
                    "additions": 0,
                    "deletions": 0,
                    "files": set(),
                    "new_files": 0,
                    "deleted_files": 0,
                    "projects": set(),
                    "messages": [],
                    "diffs_text": [],
                })
                bucket["commits"].append(sha)
                bucket["projects"].add(project.name)
                bucket["messages"].append(
                    f"[{project.name}/{branch_name}] {commit.get('message', '').strip()}"
                )

                try:
                    diffs = client.get_commit_diff(project.project_id, sha) or []
                except Exception as e:
                    logger.warning(f"获取 {sha[:8]} diff 失败: {e}")
                    diffs = []
                for d in diffs:
                    diff_text = d.get("diff", "")
                    adds, dels = count_diff_lines(diff_text)
                    bucket["additions"] += adds
                    bucket["deletions"] += dels
                    path = d.get("new_path") or d.get("old_path") or "unknown"
                    bucket["files"].add(path)
                    if d.get("new_file"):
                        bucket["new_files"] += 1
                    if d.get("deleted_file"):
                        bucket["deleted_files"] += 1
                    if diff_text:
                        bucket["diffs_text"].append(
                            f"--- {path} ---\n{diff_text}"
                        )

    def _upsert_author(
        self,
        email: str,
        data: Dict[str, Any],
        target_date: date,
    ) -> None:
        """对单个作者调 LLM 并 UPSERT"""
        commits_text = "\n".join(data["messages"])
        diffs_text = "\n\n".join(data["diffs_text"])

        llm_result = call_and_parse(
            api_url=self.llm_config["api_url"],
            api_key=self.llm_config["api_key"],
            model=self.llm_config["model"],
            author_name=data["author_name"],
            commits_text=commits_text,
            diffs_text=diffs_text,
            top_n=self.top_n,
            max_tokens=self.llm_config.get("max_tokens", 4096),
            temperature=self.llm_config.get("temperature", 0.7),
            timeout=self.llm_config.get("timeout", 240),
            max_retries=self.llm_config.get("max_retries", 3),
            retry_delay=self.llm_config.get("retry_delay", 10),
        )

        existing = (self.db.query(EmployeeEfficiencyDaily)
                       .filter_by(author_email=email, stat_date=target_date)
                       .first())

        values = dict(
            author_email=email,
            author_name=data["author_name"],
            stat_date=target_date,
            commits_count=len(data["commits"]),
            additions=data["additions"],
            deletions=data["deletions"],
            files_changed=len(data["files"]),
            new_files=data["new_files"],
            deleted_files=data["deleted_files"],
            projects_involved=json.dumps(sorted(data["projects"]),
                                          ensure_ascii=False),
            summary_top_n=self.top_n,
        )

        if llm_result["success"]:
            values.update(
                review_score=llm_result["score"],
                review_grade=llm_result["grade"],
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
            self.db.add(EmployeeEfficiencyDaily(**values))
        self.db.commit()

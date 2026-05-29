# app/services/task_executor.py
"""任务执行器 - 协调所有服务完成代码审查任务"""
import asyncio
from datetime import date, datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path
from loguru import logger

from app.services.gitlab_client import GitLabClient, GitLabAuthError
from app.services.code_reviewer import CodeReviewer
from app.services.stats_generator import StatsGenerator
from app.services.report_merger import ReportMerger
from app.services.svn_uploader import SVNUploader


class TaskExecutor:
    """任务执行器"""

    def __init__(
        self,
        gitlab_client: GitLabClient,
        code_reviewer: CodeReviewer,
        stats_generator: StatsGenerator,
        report_merger: ReportMerger,
        svn_uploader: Optional[SVNUploader] = None,
        report_output_dir: str = "./data/reports"
    ):
        """
        初始化任务执行器

        Args:
            gitlab_client: GitLab 客户端
            code_reviewer: 代码审查服务
            stats_generator: 统计生成器
            report_merger: 报告合并器
            svn_uploader: SVN 上传器（可选）
            report_output_dir: 报告输出目录
        """
        self.gitlab_client = gitlab_client
        self.code_reviewer = code_reviewer
        self.stats_generator = stats_generator
        self.report_merger = report_merger
        self.svn_uploader = svn_uploader
        self.report_output_dir = Path(report_output_dir)
        self.is_running = False
        self._progress: Dict[str, Any] = {}

    async def run_daily_review(
        self,
        project_id: int,
        project_name: str,
        exclude_branches: Optional[List[str]] = None,
        target_date: Optional[date] = None,
        prompt_template: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        执行每日代码审查

        Args:
            project_id: GitLab 项目 ID
            project_name: 项目名称
            exclude_branches: 要排除的分支列表
            target_date: 目标日期（默认昨天）

        Returns:
            执行结果
        """
        if self.is_running:
            logger.warning("任务正在执行中，跳过")
            return {"status": "skipped", "reason": "already_running"}

        self.is_running = True
        self._progress = {
            "project_id": project_id,
            "project_name": project_name,
            "start_time": datetime.now().isoformat(),
            "branches_processed": 0,
            "commits_processed": 0,
            "reports_generated": 0
        }

        try:
            if target_date is None:
                target_date = date.today() - timedelta(days=1)

            exclude_branches = exclude_branches or []

            # 获取分支列表（获取全部后过滤排除分支）
            all_branches = self.gitlab_client.get_branches(project_id)
            branches = [
                b for b in all_branches
                if b.get('name', '') not in exclude_branches
            ]
            logger.info(f"获取到 {len(branches)} 个分支（排除 {len(all_branches) - len(branches)} 个）")

            all_reports = []
            processed_commit_ids: set = set()  # 全局去重：已处理的提交 ID

            for branch_info in branches:
                branch_name = branch_info.get('name', '')
                # 获取提交列表
                since_iso = datetime.combine(target_date, datetime.min.time()).isoformat() + 'Z'
                until_iso = datetime.combine(target_date, datetime.max.time()).isoformat() + 'Z'
                commits = self.gitlab_client.get_commits(
                    project_id,
                    since=since_iso,
                    until=until_iso,
                    ref_name=branch_name,
                    exclude_merge_commits=True   # 审查按提交人算，不包含合并提交
                )

                if not commits:
                    continue

                # 过滤掉已处理过的提交（同一提交可能在多个分支上存在）
                new_commits = [c for c in commits if c.get('id') not in processed_commit_ids]
                skipped = len(commits) - len(new_commits)
                if skipped > 0:
                    logger.info(f"分支 {branch_name}: 跳过 {skipped} 个已处理的提交")
                commits = new_commits

                if not commits:
                    continue

                self._progress["branches_processed"] += 1

                # 标记这些提交为已处理
                for c in commits:
                    processed_commit_ids.add(c.get('id'))

                # 按作者分组
                by_author: Dict[str, List[Dict]] = {}
                for commit in commits:
                    author = commit.get('author_name', 'Unknown')
                    if author not in by_author:
                        by_author[author] = []
                    by_author[author].append(commit)

                # 处理每个作者
                for author, author_commits in by_author.items():
                    try:
                        report = await self._process_author_commits(
                            project_id=project_id,
                            project_name=project_name,
                            branch_name=branch_name,
                            author=author,
                            commits=author_commits,
                            target_date=target_date,
                            prompt_template=prompt_template
                        )
                        if report:
                            all_reports.append(report)
                            self._progress["reports_generated"] += 1

                        self._progress["commits_processed"] += len(author_commits)

                    except Exception as e:
                        logger.error(f"处理作者 {author} 的提交失败: {e}")

            # 上传报告到 SVN
            if self.svn_uploader and all_reports:
                await self._upload_reports(all_reports, project_name, target_date)

            self._progress["status"] = "success"
            self._progress["end_time"] = datetime.now().isoformat()

            return self._progress

        except GitLabAuthError:
            # 认证错误向上抛出，让调用方处理
            self._progress["status"] = "failed"
            self._progress["error"] = "GitLab 认证失败"
            raise
        except Exception as e:
            logger.exception(f"执行每日审查失败: {e}")
            self._progress["status"] = "failed"
            self._progress["error"] = str(e)
            return self._progress

        finally:
            self.is_running = False

    async def _process_author_commits(
        self,
        project_id: int,
        project_name: str,
        branch_name: str,
        author: str,
        commits: List[Dict],
        target_date: date,
        prompt_template: Optional[str] = None
    ) -> Optional[str]:
        """处理单个作者的提交"""
        # 收集所有差异
        all_diffs = []
        total_commits = len(commits)
        success_count = 0
        fail_count = 0
        for commit in commits:
            try:
                diffs = self.gitlab_client.get_commit_diff(project_id, commit['id'])
                if diffs is None:
                    logger.warning(
                        f"提交 {commit['id'][:8]} get_commit_diff 返回 None, "
                        f"项目: {project_name}, 分支: {branch_name}, 作者: {author}"
                    )
                    fail_count += 1
                    continue

                if not diffs:
                    logger.debug(
                        f"提交 {commit['id'][:8]} 差异文件数为 0, "
                        f"项目: {project_name}, 分支: {branch_name}"
                    )
                    continue
                success_count += 1
                all_diffs.extend(diffs)
                logger.debug(
                    f"提交 {commit['id'][:8]} 成功获取 {len(diffs)} 个文件差异"
                )
            except Exception as e:
                fail_count += 1
                logger.warning(
                    f"获取提交 {commit.get('id', 'N/A')[:8]} 差异失败: {e}"
                )

        if not all_diffs:
            logger.warning(
                f"作者 {author} 在分支 {branch_name} 的所有提交均无 diff, "
                f"共 {total_commits} 个提交 (成功: {success_count}, 失败: {fail_count})"
            )
            return None

        logger.info(
            f"作者 {author} 在分支 {branch_name} "
            f"提交: {len(commits)} 个 (成功: {success_count}, 失败: {fail_count}), "
            f"差异文件: {len(all_diffs)} 个"
        )

        # 生成代码审查报告（review_commit 返回 Optional[str]）
        # 记录 diff 信息用于调试
        logger.info(
            f"准备审查: 作者={author}, 分支={branch_name}, "
            f"提交数={len(commits)}, 差异文件数={len(all_diffs)}, "
            f"差异总字节={sum(len(d.get('diff', '')) for d in all_diffs)}"
        )
        review_content = await self.code_reviewer.review_commit(commits[0], all_diffs, prompt_template=prompt_template)
        if not review_content:
            review_content = "# 审查报告\n\n无需审查的内容"

        # 生成统计报告
        stats_content = self.stats_generator.generate_summary_report(all_diffs)

        # 合并报告
        commit_info = {**commits[0], 'branch': branch_name}
        full_report = self.report_merger.merge_reports(
            review_report=review_content,
            stats_report=stats_content,
            commit_info=commit_info,
            project_info={"name": project_name}
        )

        # 保存报告
        report_path = self._save_report(full_report, project_name, target_date, author)

        return report_path

    def _save_report(
        self,
        content: str,
        project_name: str,
        target_date: date,
        author: str,
        report_type: str = 'daily',
        date_end: Optional[date] = None
    ) -> str:
        """保存报告到文件"""
        # 根据报告类型构建日期目录
        if report_type == 'weekly' and date_end:
            date_dir = f"{target_date.isoformat()}_to_{date_end.isoformat()}"
        else:
            date_dir = target_date.isoformat()
        report_dir = self.report_output_dir / project_name / report_type / date_dir
        report_dir.mkdir(parents=True, exist_ok=True)

        # 生成文件名（替换特殊字符）
        safe_author = author.replace("/", "_").replace("\\", "_")
        filename = f"{safe_author}.md"
        report_path = report_dir / filename

        # 写入文件
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(content)

        logger.info(f"已保存报告: {report_path}")
        return str(report_path)

    async def _upload_reports(
        self,
        reports: List[str],
        project_name: str,
        target_date: date,
        report_type: str = 'daily',
        date_end: Optional[date] = None
    ) -> None:
        """上传报告到 SVN"""
        if not self.svn_uploader:
            return

        try:
            for report_path in reports:
                if report_type == 'weekly' and date_end:
                    date_dir = f"{target_date.isoformat()}_to_{date_end.isoformat()}"
                else:
                    date_dir = target_date.isoformat()
                remote_path = f"{project_name}/{report_type}/{date_dir}/{Path(report_path).name}"
                self.svn_uploader.upload_file(report_path, remote_path)
        except Exception as e:
            logger.error(f"上传报告到 SVN 失败: {e}")

    async def run_weekly_review(
        self,
        project_id: int,
        project_name: str,
        start_date: date,
        end_date: date,
        weekly_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """生成周度汇总报告

        收集日期范围内的日报，按作者汇总后用 LLM 生成周报。

        Args:
            project_id: GitLab 项目 ID
            project_name: 项目名称
            start_date: 周开始日期
            end_date: 周结束日期
            weekly_prompt: 周报汇总提示词

        Returns:
            执行结果
        """
        if self.is_running:
            logger.warning("任务正在执行中，跳过")
            return {"status": "skipped", "reason": "already_running"}

        self.is_running = True
        self._progress = {
            "project_id": project_id,
            "project_name": project_name,
            "start_time": datetime.now().isoformat(),
            "reports_generated": 0
        }

        try:
            # 收集日期范围内的日报
            daily_dir = self.report_output_dir / project_name / "daily"
            author_reports: Dict[str, List[str]] = {}

            current_date = start_date
            while current_date <= end_date:
                date_path = daily_dir / current_date.isoformat()
                if date_path.exists():
                    for report_file in date_path.glob("*.md"):
                        author = report_file.stem
                        content = report_file.read_text(encoding="utf-8")
                        author_reports.setdefault(author, []).append(content)
                current_date += timedelta(days=1)

            if not author_reports:
                logger.info(f"项目 {project_name} 在 {start_date} ~ {end_date} 内无日报，跳过周报生成")
                self._progress["status"] = "skipped"
                self._progress["reason"] = "no_daily_reports"
                return self._progress

            all_weekly_reports = []
            default_weekly_prompt = """请作为资深代码审查专家，汇总以下一周内的代码审查日报：

{diff}

## 汇总要求
1. 本周代码质量总体评价（优点和不足）
2. 主要发现的问题类型和频率
3. 代码量与质量的关系分析
4. 下周重点关注建议

请用中文回复，格式如下：
### 本周总体评价
[评价内容]

### 主要问题汇总
- [问题1]
- [问题2]

### 改进建议
- [建议1]
- [建议2]

### 下周关注重点
[关注内容]
"""
            prompt = weekly_prompt or default_weekly_prompt

            # 从日报中提取统计数据
            import re
            author_stats: Dict[str, Dict[str, Any]] = {}
            for author, reports in author_reports.items():
                stats = {"commit_count": len(reports), "additions": 0, "deletions": 0, "files": 0}
                for report in reports:
                    # 尝试从日报中提取代码统计
                    add_match = re.findall(r'\+(\d+)\s*(?:行|lines?)', report)
                    del_match = re.findall(r'-(\d+)\s*(?:行|lines?)', report)
                    file_match = re.findall(r'(\d+)\s*(?:个文件|files?)', report)
                    stats["additions"] += sum(int(x) for x in add_match) if add_match else 0
                    stats["deletions"] += sum(int(x) for x in del_match) if del_match else 0
                    stats["files"] += sum(int(x) for x in file_match) if file_match else 0
                author_stats[author] = stats

            for author, reports in author_reports.items():
                try:
                    # 合并该作者的所有日报，用于 LLM 分析
                    combined = "\n\n---\n\n".join(reports)

                    # 用 LLM 生成汇总
                    system_prompt = (
                        f"你是代码审查周报汇总专家。请对 {author} 本周的代码审查日报进行综合分析。"
                    )
                    weekly_summary = await self.code_reviewer.review(
                        diff=combined,
                        prompt_template=prompt,
                        system_prompt=system_prompt
                    )

                    if not weekly_summary:
                        weekly_summary = "# 周报汇总\n\n本周无需汇总的内容"

                    # 使用 ReportMerger 生成纯汇总周报（不含日报原文）
                    astats = author_stats.get(author, {})
                    final_report = self.report_merger.generate_weekly_summary(
                        project_name=project_name,
                        week_start=start_date.isoformat(),
                        week_end=end_date.isoformat(),
                        author=author,
                        commit_count=astats.get("commit_count", len(reports)),
                        additions=astats.get("additions", 0),
                        deletions=astats.get("deletions", 0),
                        files=astats.get("files", 0),
                        ai_summary=weekly_summary,
                    )

                    # 保存周报
                    report_path = self._save_report(
                        final_report, project_name, start_date, author,
                        report_type='weekly', date_end=end_date
                    )
                    all_weekly_reports.append(report_path)
                    self._progress["reports_generated"] += 1

                except Exception as e:
                    logger.error(f"生成作者 {author} 的周报失败: {e}")

            # 上传到 SVN
            if self.svn_uploader and all_weekly_reports:
                await self._upload_reports(
                    all_weekly_reports, project_name, start_date,
                    report_type='weekly', date_end=end_date
                )

            self._progress["status"] = "success"
            self._progress["end_time"] = datetime.now().isoformat()
            return self._progress

        except Exception as e:
            logger.exception(f"生成周报失败: {e}")
            self._progress["status"] = "failed"
            self._progress["error"] = str(e)
            return self._progress

        finally:
            self.is_running = False

    def get_progress(self) -> Dict[str, Any]:
        """获取任务进度"""
        return self._progress.copy()

    def stop(self) -> None:
        """停止当前任务"""
        self.is_running = False
        logger.info("任务执行器已停止")

    async def close(self) -> None:
        """关闭资源"""
        if hasattr(self.code_reviewer, 'close'):
            await self.code_reviewer.close()
        logger.info("任务执行器资源已释放")

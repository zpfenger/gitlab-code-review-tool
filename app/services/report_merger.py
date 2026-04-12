# app/services/report_merger.py
"""报告合并服务"""
from typing import Dict, Any, List, Optional
from datetime import datetime
import html


class ReportMerger:
    """报告合并服务"""

    def merge_reports(
        self,
        review_report: str,
        stats_report: str,
        commit_info: Dict[str, Any],
        project_info: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        合并审查报告和统计报告

        Args:
            review_report: 代码审查报告
            stats_report: 统计报告
            commit_info: 提交信息
            project_info: 项目信息（可选）

        Returns:
            str: 合并后的报告
        """
        sections = []

        # 添加报告头
        header = self.add_header(
            project_name=project_info.get("name", "Unknown Project") if project_info else "Unknown Project",
            commit_sha=commit_info.get("id", "N/A"),
            author=commit_info.get("author_name", "Unknown"),
            branch=commit_info.get("branch", "main")
        )
        sections.append(header)

        # 添加提交信息部分
        commit_section = self.format_commit_section(commit_info)
        sections.append(commit_section)

        # 添加代码审查报告
        if review_report:
            sections.append("## 代码审查结果\n")
            sections.append(review_report)

        # 添加统计报告
        if stats_report:
            sections.append("\n---\n")
            sections.append(stats_report)

        # 添加报告尾
        footer = self.add_footer(
            generated_by="GitLab Code Review Tool",
            version="1.0.0"
        )
        sections.append(footer)

        return "\n".join(sections)

    def add_header(
        self,
        project_name: str,
        commit_sha: str,
        author: str,
        branch: str = "main"
    ) -> str:
        """
        添加报告头

        Args:
            project_name: 项目名称
            commit_sha: 提交 SHA
            author: 作者
            branch: 分支名

        Returns:
            str: 报告头
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        header = f"""# 代码审查报告

**项目**: {project_name}
**分支**: {branch}
**提交**: {commit_sha[:8] if len(commit_sha) > 8 else commit_sha}
**作者**: {author}
**生成时间**: {timestamp}

---"""
        return header

    def add_footer(
        self,
        generated_by: str = "GitLab Code Review Tool",
        version: str = "1.0.0"
    ) -> str:
        """
        添加报告尾

        Args:
            generated_by: 生成工具名称
            version: 版本号

        Returns:
            str: 报告尾
        """
        footer = f"""
---

*本报告由 {generated_by} v{version} 自动生成*
"""
        return footer

    def format_commit_section(self, commit_info: Dict[str, Any]) -> str:
        """
        格式化提交信息部分

        Args:
            commit_info: 提交信息

        Returns:
            str: 格式化后的提交部分
        """
        commit_id = commit_info.get("id", "N/A")
        title = commit_info.get("title", "N/A")
        author = commit_info.get("author_name", "Unknown")
        email = commit_info.get("author_email", "")
        created_at = commit_info.get("created_at", "")
        message = commit_info.get("message", "")

        section = f"""
## 提交信息

| 属性 | 值 |
|------|-----|
| **提交 ID** | `{commit_id}` |
| **标题** | {title} |
| **作者** | {author} <{email}> |
| **时间** | {self.format_timestamp(created_at) if created_at else 'N/A'} |
"""

        if message and message != title:
            section += f"""
**完整提交消息**:
```
{message}
```
"""
        return section

    def merge_multiple_commits(
        self,
        commits: List[Dict[str, Any]],
        reports: Dict[str, str],
        project_info: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        合并多个提交的报告

        Args:
            commits: 提交列表
            reports: 提交 ID 到报告的映射
            project_info: 项目信息（可选）

        Returns:
            str: 合并后的报告
        """
        sections = []

        # 添加总体报告头
        project_name = project_info.get("name", "Unknown Project") if project_info else "Unknown Project"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sections.append(f"""# 代码审查报告（批量）

**项目**: {project_name}
**提交数量**: {len(commits)}
**生成时间**: {timestamp}

---""")

        # 为每个提交添加报告
        for commit in commits:
            commit_id = commit.get("id", "")
            report = reports.get(commit_id, "")

            sections.append(f"\n## 提交: {commit.get('title', 'N/A')}\n")
            sections.append(f"**ID**: `{commit_id}`")
            sections.append(f"**作者**: {commit.get('author_name', 'Unknown')}")
            sections.append(f"**时间**: {self.format_timestamp(commit.get('created_at', ''))}\n")

            if report:
                sections.append("### 审查结果\n")
                sections.append(report)
            else:
                sections.append("*无审查结果*\n")

            sections.append("\n---")

        # 添加报告尾
        footer = self.add_footer()
        sections.append(footer)

        return "\n".join(sections)

    def generate_weekly(
        self,
        project_name: str,
        week_start: str,
        week_end: str,
        daily_reports: List[str],
        author_stats: Optional[Dict[str, Any]] = None,
        author: Optional[str] = None
    ) -> str:
        """
        生成周报

        Args:
            project_name: 项目名称
            week_start: 周开始日期
            week_end: 周结束日期
            daily_reports: 日报内容列表
            author_stats: 作者统计映射
            author: 周报归属作者名称

        Returns:
            str: 周报 Markdown
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sections = [
            "# 代码审查周报",
            "",
            f"**项目**: {project_name}",
        ]
        if author:
            sections.append(f"**作者**: {author}")
        sections.extend([
            f"**周期**: {week_start} ~ {week_end}",
            f"**日报数量**: {len(daily_reports)}",
            f"**生成时间**: {timestamp}",
            "",
            "---",
            "",
            "## 汇总",
        ])

        if author_stats:
            sections.append("")
            sections.append("### 提交人排行榜")
            sections.append("")
            sections.append("| 作者 | 提交数 | 新增行 | 删除行 | 净变化 |")
            sections.append("|------|--------|--------|--------|--------|")
            for author, stats in sorted(
                author_stats.items(),
                key=lambda x: x[1].get("diff_stats", {}).get("total_additions", 0),
                reverse=True
            ):
                ds = stats.get("diff_stats", {})
                sections.append(
                    f"| {author} | {stats.get('commit_count', 0)} | "
                    f"+{ds.get('total_additions', 0)} | "
                    f"-{ds.get('total_deletions', 0)} | "
                    f"{'+' if ds.get('net_change', 0) >= 0 else ''}{ds.get('net_change', 0)} |"
                )

        sections.append("")
        sections.append("## 每日详情")
        sections.append("")
        for report in daily_reports:
            sections.append(report)
            sections.append("\n---\n")

        footer = self.add_footer(generated_by="GitLab Code Review Tool", version="1.0.0")
        sections.append(footer)

        return "\n".join(sections)

    def generate_monthly(
        self,
        project_name: str,
        year: int,
        month: int,
        weekly_reports: List[str],
        monthly_stats: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        生成月报

        Args:
            project_name: 项目名称
            year: 年份
            month: 月份
            weekly_reports: 周报内容列表
            monthly_stats: 月度统计

        Returns:
            str: 月报 Markdown
        """
        sections = [
            "# 代码审查月报",
            "",
            f"**项目**: {project_name}",
            f"**月份**: {year}年{month}月",
            "",
            "---",
            "",
            "## 月度汇总",
        ]

        if monthly_stats:
            total_add = monthly_stats.get("total_additions", 0)
            total_del = monthly_stats.get("total_deletions", 0)
            total_commits = monthly_stats.get("total_commits", 0)
            total_authors = monthly_stats.get("total_authors", 0)

            sections.append("")
            sections.append(f"- **总提交数**: {total_commits}")
            sections.append(f"- **参与人数**: {total_authors}")
            sections.append(f"- **新增行数**: +{total_add}")
            sections.append(f"- **删除行数**: -{total_del}")
            sections.append(f"- **净变化**: {'+' if total_add - total_del >= 0 else ''}{total_add - total_del}")

            author_ranking = monthly_stats.get("author_ranking", [])
            if author_ranking:
                sections.append("")
                sections.append("### 团队贡献排行")
                sections.append("")
                sections.append("| 排名 | 作者 | 提交数 | 新增行 |")
                sections.append("|------|------|--------|--------|")
                for i, ad in enumerate(author_ranking, 1):
                    sections.append(
                        f"| {i} | {ad.get('author', 'N/A')} | "
                        f"{ad.get('commits', 0)} | "
                        f"+{ad.get('additions', 0)} |"
                    )

        sections.append("")
        sections.append("## 周报详情")
        sections.append("")
        for report in weekly_reports:
            sections.append(report)
            sections.append("\n---\n")

        footer = self.add_footer(generated_by="GitLab Code Review Tool", version="1.0.0")
        sections.append(footer)

        return "\n".join(sections)

    def generate_weekly_summary(
        self,
        project_name: str,
        week_start: str,
        week_end: str,
        author: str,
        commit_count: int,
        additions: int = 0,
        deletions: int = 0,
        files: int = 0,
        ai_summary: str = "",
    ) -> str:
        """
        生成纯汇总周报（不含每日详情）

        Args:
            project_name: 项目名称
            week_start: 周开始日期
            week_end: 周结束日期
            author: 作者名称
            commit_count: 提交次数
            additions: 新增行数
            deletions: 删除行数
            files: 变更文件数
            ai_summary: AI 汇总分析内容

        Returns:
            str: 周报 Markdown
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        net_change = additions - deletions

        sections = [
            "# 代码审查周报",
            "",
            f"**项目**: {project_name}",
            f"**作者**: {author}",
            f"**周期**: {week_start} ~ {week_end}",
            f"**生成时间**: {timestamp}",
            "",
            "---",
            "",
            "## 本周统计",
            "",
            "| 指标 | 数值 |",
            "|------|------|",
            f"| 提交次数 | {commit_count} |",
            f"| 变更文件数 | {files} |",
            f"| 新增行数 | +{additions} |",
            f"| 删除行数 | -{deletions} |",
            f"| 净变化 | {'+' if net_change >= 0 else ''}{net_change} |",
            "",
            "---",
            "",
        ]

        if ai_summary:
            sections.append(ai_summary)
        else:
            sections.append("## 汇总\n\n本周无需汇总的内容")

        footer = self.add_footer(generated_by="GitLab Code Review Tool", version="1.0.0")
        sections.append(footer)

        return "\n".join(sections)

    def format_timestamp(self, timestamp: str) -> str:
        """
        格式化时间戳

        Args:
            timestamp: ISO 格式时间戳

        Returns:
            str: 格式化后的时间字符串
        """
        if not timestamp:
            return "N/A"

        try:
            # 处理 ISO 格式时间戳
            if "T" in timestamp:
                if "Z" in timestamp:
                    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromisoformat(timestamp)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            return timestamp
        except (ValueError, TypeError):
            return timestamp

    def html_escape(self, text: str) -> str:
        """
        HTML 转义

        Args:
            text: 原始文本

        Returns:
            str: 转义后的文本
        """
        return html.escape(text)

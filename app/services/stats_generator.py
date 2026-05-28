# app/services/stats_generator.py
"""代码统计服务"""
from typing import List, Dict, Any
from collections import defaultdict
from loguru import logger

from app.services.diff_utils import ADDITION_PATTERN, DELETION_PATTERN


class StatsGenerator:
    """代码统计生成器"""

    def __init__(self):
        """初始化统计生成器"""
        # 复用 diff_utils 中的共享正则，避免重复定义
        self.addition_pattern = ADDITION_PATTERN
        self.deletion_pattern = DELETION_PATTERN

    def calculate_diff_stats(
        self,
        diffs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        计算差异统计

        Args:
            diffs: 差异列表

        Returns:
            Dict: 统计结果
        """
        total_additions = 0
        total_deletions = 0
        files_changed = 0
        new_files = 0
        deleted_files = 0

        for diff in diffs:
            diff_content = diff.get("diff", "")
            additions, deletions = self._parse_diff_lines(diff_content)

            total_additions += additions
            total_deletions += deletions
            files_changed += 1

            if diff.get("new_file", False):
                new_files += 1
            if diff.get("deleted_file", False):
                deleted_files += 1

        return {
            "total_additions": total_additions,
            "total_deletions": total_deletions,
            "files_changed": files_changed,
            "new_files": new_files,
            "deleted_files": deleted_files,
            "net_change": total_additions - total_deletions
        }

    def calculate_file_stats(
        self,
        diffs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        计算每个文件的统计

        Args:
            diffs: 差异列表

        Returns:
            List[Dict]: 文件统计列表
        """
        file_stats = []

        for diff in diffs:
            path = diff.get("new_path", "unknown")
            if path == "/dev/null":
                path = diff.get("old_path", "unknown")

            diff_content = diff.get("diff", "")
            additions, deletions = self._parse_diff_lines(diff_content)

            file_stats.append({
                "path": path,
                "additions": additions,
                "deletions": deletions,
                "extension": self._get_file_extension(path),
                "is_new": diff.get("new_file", False),
                "is_deleted": diff.get("deleted_file", False)
            })

        return file_stats

    def generate_extension_stats(
        self,
        diffs: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, int]]:
        """
        生成按扩展名分组的统计

        Args:
            diffs: 差异列表

        Returns:
            Dict[str, Dict]: 扩展名统计
        """
        ext_stats = defaultdict(lambda: {
            "files": 0,
            "additions": 0,
            "deletions": 0
        })

        for diff in diffs:
            path = diff.get("new_path", "")
            if path == "/dev/null":
                path = diff.get("old_path", "")

            ext = self._get_file_extension(path)
            diff_content = diff.get("diff", "")
            additions, deletions = self._parse_diff_lines(diff_content)

            ext_stats[ext]["files"] += 1
            ext_stats[ext]["additions"] += additions
            ext_stats[ext]["deletions"] += deletions

        return dict(ext_stats)

    def generate_summary_report(
        self,
        diffs: List[Dict[str, Any]]
    ) -> str:
        """
        生成摘要报告

        Args:
            diffs: 差异列表

        Returns:
            str: 摘要报告
        """
        stats = self.calculate_diff_stats(diffs)
        file_stats = self.calculate_file_stats(diffs)
        ext_stats = self.generate_extension_stats(diffs)

        report_lines = [
            "# 代码变更统计",
            "",
            "## 总体统计",
            f"- **变更文件数**: {stats['files_changed']}",
            f"- **新增文件**: {stats['new_files']}",
            f"- **删除文件**: {stats['deleted_files']}",
            f"- **新增行数**: +{self._format_number(stats['total_additions'])}",
            f"- **删除行数**: -{self._format_number(stats['total_deletions'])}",
            f"- **净变化**: {'+' if stats['net_change'] >= 0 else ''}{self._format_number(stats['net_change'])}",
            "",
            "## 文件统计",
            ""
        ]

        for file in file_stats:
            status = ""
            if file["is_new"]:
                status = " [NEW]"
            elif file["is_deleted"]:
                status = " [DELETED]"

            report_lines.append(
                f"- `{file['path']}`{status}: "
                f"+{file['additions']}/-{file['deletions']}"
            )

        report_lines.extend([
            "",
            "## 扩展名统计",
            ""
        ])

        for ext, ext_stat in sorted(ext_stats.items()):
            ext_name = ext if ext else "(无扩展名)"
            report_lines.append(
                f"- **{ext_name}**: {ext_stat['files']} 文件, "
                f"+{ext_stat['additions']}/-{ext_stat['deletions']}"
            )

        return "\n".join(report_lines)

    def generate_detailed_report(
        self,
        diffs: List[Dict[str, Any]],
        commit_info: Dict[str, Any]
    ) -> str:
        """
        生成详细报告

        Args:
            diffs: 差异列表
            commit_info: 提交信息

        Returns:
            str: 详细报告
        """
        stats = self.calculate_diff_stats(diffs)

        report_lines = [
            "# 代码变更详细报告",
            "",
            "## 提交信息",
            f"- **提交 ID**: {commit_info.get('id', 'N/A')}",
            f"- **标题**: {commit_info.get('title', 'N/A')}",
            f"- **作者**: {commit_info.get('author_name', 'N/A')}",
            f"- **邮箱**: {commit_info.get('author_email', 'N/A')}",
            f"- **时间**: {commit_info.get('created_at', 'N/A')}",
            "",
            "## 统计摘要",
            f"- **变更文件**: {stats['files_changed']} 个",
            f"- **新增行数**: +{self._format_number(stats['total_additions'])} 行",
            f"- **删除行数**: -{self._format_number(stats['total_deletions'])} 行",
            "",
            "## 文件详情",
            ""
        ]

        for diff in diffs:
            path = diff.get("new_path", "unknown")
            if path == "/dev/null":
                path = diff.get("old_path", "unknown") + " (已删除)"

            diff_content = diff.get("diff", "")
            additions, deletions = self._parse_diff_lines(diff_content)

            status = ""
            if diff.get("new_file", False):
                status = " (新文件)"
            elif diff.get("deleted_file", False):
                status = " (已删除)"

            report_lines.append(f"### {path}{status}")
            report_lines.append(f"- 新增: {additions} 行")
            report_lines.append(f"- 删除: {deletions} 行")
            report_lines.append("")

        return "\n".join(report_lines)

    def _parse_diff_lines(self, diff: str) -> tuple:
        """
        解析差异中的行数变化

        Args:
            diff: 差异内容

        Returns:
            tuple: (新增行数, 删除行数)
        """
        if not diff:
            return 0, 0

        additions = len(self.addition_pattern.findall(diff))
        deletions = len(self.deletion_pattern.findall(diff))

        return additions, deletions

    def _get_file_extension(self, path: str) -> str:
        """
        获取文件扩展名

        Args:
            path: 文件路径

        Returns:
            str: 扩展名（包含点）
        """
        if not path or path == "/dev/null":
            return ""

        if "." in path:
            return "." + path.rsplit(".", 1)[-1].lower()

        return ""

    def stats_by_author(
        self,
        author: str,
        commits: List[Dict[str, Any]],
        diffs: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        按作者统计代码变更

        Args:
            author: 作者名称
            commits: 提交列表
            diffs: 差异列表

        Returns:
            Dict: 作者统计结果
        """
        diff_stats = self.calculate_diff_stats(diffs)
        file_stats = self.calculate_file_stats(diffs)
        ext_stats = self.generate_extension_stats(diffs)

        return {
            "author": author,
            "commit_count": len(commits),
            "diff_stats": diff_stats,
            "file_stats": file_stats,
            "extension_stats": ext_stats,
        }

    def _format_number(self, num: int) -> str:
        """
        格式化数字（添加千位分隔符）

        Args:
            num: 数字

        Returns:
            str: 格式化后的字符串
        """
        if num >= 1000:
            return f"{num:,}"
        return str(num)

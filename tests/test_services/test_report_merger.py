# tests/test_services/test_report_merger.py
"""报告合并服务测试"""
import pytest
from app.services.report_merger import ReportMerger


class TestReportMerger:
    """报告合并服务测试类"""

    @pytest.fixture
    def merger(self):
        """创建报告合并器实例"""
        return ReportMerger()

    @pytest.fixture
    def sample_review_report(self):
        """示例代码审查报告"""
        return """## 代码审查结果

### 文件: test.py
- 问题1: 变量命名不规范
- 问题2: 缺少错误处理

### 改进建议
1. 使用更清晰的命名
2. 添加 try-except 块
"""

    @pytest.fixture
    def sample_stats_report(self):
        """示例统计报告"""
        return """# 代码统计

- 新增行数: 100
- 删除行数: 50
- 文件数: 5
"""

    @pytest.fixture
    def sample_commit_info(self):
        """示例提交信息"""
        return {
            "id": "abc123",
            "title": "Test commit",
            "author_name": "Test User",
            "author_email": "test@example.com",
            "created_at": "2024-01-01T10:00:00Z"
        }

    def test_init(self, merger):
        """测试初始化"""
        assert merger is not None

    def test_merge_reports(self, merger, sample_review_report, sample_stats_report, sample_commit_info):
        """测试合并报告"""
        merged = merger.merge_reports(
            review_report=sample_review_report,
            stats_report=sample_stats_report,
            commit_info=sample_commit_info
        )

        assert "代码审查结果" in merged
        assert "代码统计" in merged
        assert "abc123" in merged
        assert "Test commit" in merged
        assert "Test User" in merged

    def test_add_header(self, merger):
        """测试添加报告头"""
        header = merger.add_header(
            project_name="Test Project",
            commit_sha="abc123",
            author="Test User",
            branch="main"
        )

        assert "Test Project" in header
        assert "abc123" in header
        assert "Test User" in header
        assert "main" in header

    def test_add_footer(self, merger):
        """测试添加报告尾"""
        footer = merger.add_footer(
            generated_by="GitLab Code Review Tool",
            version="1.0.0"
        )

        assert "GitLab Code Review Tool" in footer
        assert "1.0.0" in footer

    def test_format_commit_section(self, merger, sample_commit_info):
        """测试格式化提交部分"""
        section = merger.format_commit_section(sample_commit_info)

        assert "abc123" in section
        assert "Test commit" in section
        assert "Test User" in section
        assert "test@example.com" in section

    def test_merge_multiple_commits(self, merger):
        """测试合并多个提交"""
        commits = [
            {
                "id": "abc123",
                "title": "First commit",
                "author_name": "User1",
                "created_at": "2024-01-01T10:00:00Z"
            },
            {
                "id": "def456",
                "title": "Second commit",
                "author_name": "User2",
                "created_at": "2024-01-01T11:00:00Z"
            }
        ]

        reports = {
            "abc123": "Review for first commit",
            "def456": "Review for second commit"
        }

        merged = merger.merge_multiple_commits(
            commits=commits,
            reports=reports
        )

        assert "abc123" in merged
        assert "def456" in merged
        assert "First commit" in merged
        assert "Second commit" in merged

    def test_format_timestamp(self, merger):
        """测试格式化时间戳"""
        timestamp = "2024-01-01T10:00:00Z"
        formatted = merger.format_timestamp(timestamp)

        assert "2024" in formatted

    def test_format_timestamp_with_timezone(self, merger):
        """测试格式化带时区的时间戳"""
        timestamp = "2024-01-01T10:00:00+08:00"
        formatted = merger.format_timestamp(timestamp)

        assert "2024" in formatted

    def test_format_timestamp_empty(self, merger):
        """测试空时间戳"""
        formatted = merger.format_timestamp("")
        assert formatted == "N/A"

    def test_empty_reports(self, merger, sample_commit_info):
        """测试空报告"""
        merged = merger.merge_reports(
            review_report="",
            stats_report="",
            commit_info=sample_commit_info
        )

        # 应该至少包含提交信息
        assert "abc123" in merged

    def test_merge_with_project_info(self, merger, sample_review_report, sample_stats_report):
        """测试包含项目信息的合并"""
        project_info = {
            "name": "Test Project",
            "web_url": "https://gitlab.example.com/test/project"
        }

        merged = merger.merge_reports(
            review_report=sample_review_report,
            stats_report=sample_stats_report,
            commit_info={"id": "abc123", "title": "Test", "author_name": "User"},
            project_info=project_info
        )

        assert "Test Project" in merged

    def test_html_escape(self, merger):
        """测试 HTML 转义"""
        unsafe_content = "<script>alert('xss')</script>"
        escaped = merger.html_escape(unsafe_content)

        assert "<script>" not in escaped
        assert "&lt;" in escaped

    def test_html_escape_safe_content(self, merger):
        """测试安全内容的 HTML 转义"""
        safe_content = "This is safe content"
        escaped = merger.html_escape(safe_content)

        assert escaped == safe_content

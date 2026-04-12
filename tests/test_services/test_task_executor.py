# tests/test_services/test_task_executor.py
"""任务执行器测试"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import date, datetime
from pathlib import Path

from app.services.task_executor import TaskExecutor
from app.services.gitlab_client import GitLabClient
from app.services.code_reviewer import CodeReviewer
from app.services.stats_generator import StatsGenerator
from app.services.report_merger import ReportMerger


class TestTaskExecutor:
    """任务执行器测试类"""

    @pytest.fixture
    def mock_gitlab_client(self):
        """模拟 GitLab 客户端"""
        client = MagicMock(spec=GitLabClient)
        client.get_branches.return_value = ['main', 'develop']
        client.get_commits.return_value = [
            {
                'sha': 'abc123def456',
                'author_name': 'Test User',
                'author_email': 'test@example.com',
                'message': 'Test commit',
                'created_at': '2026-03-27T10:00:00Z'
            }
        ]
        client.get_commit_diff.return_value = [
            {'old_path': 'test.py', 'new_path': 'test.py', 'diff': '+print("hello")'}
        ]
        return client

    @pytest.fixture
    def mock_code_reviewer(self):
        """模拟代码审查器"""
        reviewer = MagicMock(spec=CodeReviewer)
        result = MagicMock()
        result.content = "# 审查报告\n\n代码质量良好"
        reviewer.review_commit = AsyncMock(return_value=result)
        reviewer.close = AsyncMock()
        return reviewer

    @pytest.fixture
    def mock_stats_generator(self):
        """模拟统计生成器"""
        generator = MagicMock(spec=StatsGenerator)
        generator.calculate_diff_stats.return_value = {
            'additions': 10,
            'deletions': 5,
            'files_changed': 2
        }
        generator.generate_summary_report.return_value = "# 统计报告\n\n新增: 10 行"
        return generator

    @pytest.fixture
    def mock_report_merger(self):
        """模拟报告合并器"""
        merger = MagicMock(spec=ReportMerger)
        merger.merge_reports.return_value = "# 完整报告\n\n审查和统计内容"
        return merger

    @pytest.fixture
    def task_executor(self, mock_gitlab_client, mock_code_reviewer, mock_stats_generator, mock_report_merger, tmp_path):
        """创建任务执行器实例"""
        return TaskExecutor(
            gitlab_client=mock_gitlab_client,
            code_reviewer=mock_code_reviewer,
            stats_generator=mock_stats_generator,
            report_merger=mock_report_merger,
            report_output_dir=str(tmp_path)
        )

    def test_init(self, task_executor):
        """测试初始化"""
        assert task_executor.gitlab_client is not None
        assert task_executor.code_reviewer is not None
        assert task_executor.is_running is False

    def test_get_progress(self, task_executor):
        """测试获取进度"""
        progress = task_executor.get_progress()
        assert isinstance(progress, dict)

    def test_stop(self, task_executor):
        """测试停止"""
        task_executor.is_running = True
        task_executor.stop()
        assert task_executor.is_running is False

    @pytest.mark.asyncio
    async def test_close(self, task_executor):
        """测试关闭资源"""
        await task_executor.close()

    @pytest.mark.asyncio
    async def test_run_daily_review(self, task_executor):
        """测试执行每日审查"""
        result = await task_executor.run_daily_review(
            project_id=123,
            project_name="test-project",
            target_date=date(2026, 3, 27)
        )

        assert result is not None
        assert 'status' in result

    @pytest.mark.asyncio
    async def test_run_daily_review_already_running(self, task_executor):
        """测试重复执行"""
        task_executor.is_running = True

        result = await task_executor.run_daily_review(
            project_id=123,
            project_name="test-project"
        )

        assert result['status'] == 'skipped'
        assert result['reason'] == 'already_running'

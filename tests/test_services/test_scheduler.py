# tests/test_services/test_scheduler.py
"""定时任务调度器测试"""
import pytest
from unittest.mock import MagicMock, patch
from app.services.scheduler import ReviewScheduler


class TestReviewScheduler:
    """调度器测试类"""

    def test_init(self):
        """测试初始化"""
        scheduler = ReviewScheduler()
        assert scheduler.scheduler is None
        assert scheduler.is_running is False

    def test_setup_daily_task(self):
        """测试设置每日任务"""
        scheduler = ReviewScheduler()
        callback = MagicMock()

        scheduler.setup_daily_task("09:00", callback)

        # 验证任务已添加
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0]['id'] == 'daily_review'

    def test_setup_weekly_task(self):
        """测试设置每周任务"""
        scheduler = ReviewScheduler()
        callback = MagicMock()

        scheduler.setup_weekly_task(0, "10:00", callback)

        jobs = scheduler.get_jobs()
        assert len(jobs) == 1

    def test_start_stop(self):
        """测试启动和停止"""
        scheduler = ReviewScheduler()

        scheduler.start()
        assert scheduler.is_running is True

        scheduler.stop()
        assert scheduler.is_running is False

    def test_get_jobs_empty(self):
        """测试获取空任务列表"""
        scheduler = ReviewScheduler()
        jobs = scheduler.get_jobs()
        assert jobs == []

    def test_add_job(self):
        """测试添加自定义任务"""
        scheduler = ReviewScheduler()

        def dummy_func():
            pass

        job_id = scheduler.add_job(
            func=dummy_func,
            trigger_type='interval',
            job_id='test_job',
            seconds=60
        )

        assert job_id == 'test_job'
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1

    def test_remove_job(self):
        """测试移除任务"""
        scheduler = ReviewScheduler()

        def dummy_func():
            pass

        scheduler.add_job(
            func=dummy_func,
            trigger_type='interval',
            job_id='job_to_remove',
            seconds=60
        )

        result = scheduler.remove_job('job_to_remove')
        assert result is True

        jobs = scheduler.get_jobs()
        assert len(jobs) == 0

    def test_get_next_run_time(self):
        """测试获取下次执行时间"""
        scheduler = ReviewScheduler()
        callback = MagicMock()

        scheduler.setup_daily_task("09:00", callback)
        # 启动调度器才能获取 next_run_time
        scheduler.start()
        next_run = scheduler.get_next_run_time('daily_review')
        scheduler.stop()

        # 应该返回一个时间字符串
        assert next_run is not None

    def test_run_now(self):
        """测试立即执行"""
        scheduler = ReviewScheduler()
        callback = MagicMock()

        scheduler.setup_daily_task("09:00", callback)
        result = scheduler.run_now('daily_review')

        assert result is True


# Mock functions for testing
def mock_job():
    pass


def mock_job1():
    pass


def mock_job2():
    pass


def mock_job3():
    pass


def mock_custom_job():
    pass


def mock_immediate_job():
    pass

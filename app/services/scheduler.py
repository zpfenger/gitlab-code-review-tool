# app/services/scheduler.py
"""定时任务调度器"""
from typing import Callable, Dict, List, Optional, Any
from datetime import datetime
from loguru import logger
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


class ReviewScheduler:
    """定时任务调度器"""

    def __init__(self):
        """初始化调度器"""
        self.scheduler: Optional[BackgroundScheduler] = None
        self.is_running = False
        self._current_task_id: Optional[str] = None

    def setup_daily_task(self, time: str, callback: Callable, job_id: str = 'daily_review') -> None:
        """
        设置每日任务

        Args:
            time: 执行时间 (HH:MM 格式)
            callback: 回调函数
            job_id: 任务 ID
        """
        hour, minute = time.split(':')
        trigger = CronTrigger(hour=int(hour), minute=int(minute))

        # 移除旧任务
        if self.scheduler and self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        if self.scheduler is None:
            self.scheduler = BackgroundScheduler()

        self.scheduler.add_job(
            callback,
            trigger=trigger,
            id=job_id,
            name='每日代码审查',
            replace_existing=True
        )
        logger.info(f"已设置每日任务: {time}")

    def setup_weekly_task(self, weekday: int, time: str, callback: Callable, job_id: str = 'weekly_report') -> None:
        """
        设置每周任务

        Args:
            weekday: 星期几 (0=周一, 6=周日)
            time: 执行时间
            callback: 回调函数
            job_id: 任务 ID
        """
        hour, minute = time.split(':')
        trigger = CronTrigger(day_of_week=weekday, hour=int(hour), minute=int(minute))

        if self.scheduler is None:
            self.scheduler = BackgroundScheduler()

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        self.scheduler.add_job(
            callback,
            trigger=trigger,
            id=job_id,
            name='周报生成',
            replace_existing=True
        )
        logger.info(f"已设置每周任务: 星期{weekday + 1} {time}")

    def setup_monthly_task(self, day: int, time: str, callback: Callable, job_id: str = 'monthly_report') -> None:
        """
        设置每月任务

        Args:
            day: 每月的第几天 (1-31)
            time: 执行时间 (HH:MM 格式)
            callback: 回调函数
            job_id: 任务 ID
        """
        hour, minute = time.split(':')
        trigger = CronTrigger(day=day, hour=int(hour), minute=int(minute))

        if self.scheduler is None:
            self.scheduler = BackgroundScheduler()

        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

        self.scheduler.add_job(
            callback,
            trigger=trigger,
            id=job_id,
            name='月报生成',
            replace_existing=True
        )
        logger.info(f"已设置每月任务: 每月{day}日 {time}")

    def start(self) -> None:
        """启动调度器"""
        if not self.is_running:
            if self.scheduler is None:
                self.scheduler = BackgroundScheduler()
            self.scheduler.start()
            self.is_running = True
            logger.info("任务调度器已启动")

    def stop(self) -> None:
        """停止调度器"""
        if self.is_running and self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.is_running = False
            logger.info("任务调度器已停止")

    def pause(self) -> None:
        """暂停调度器"""
        if self.scheduler:
            self.scheduler.pause()
            logger.info("任务调度器已暂停")

    def resume(self) -> None:
        """恢复调度器"""
        if self.scheduler:
            self.scheduler.resume()
            logger.info("任务调度器已恢复")

    def run_now(self, job_id: str = 'daily_review') -> bool:
        """
        立即执行指定任务

        Args:
            job_id: 任务 ID

        Returns:
            是否成功触发
        """
        if self.scheduler:
            job = self.scheduler.get_job(job_id)
            if job:
                job.modify(next_run_time=datetime.now())
                logger.info(f"已触发任务: {job_id}")
                return True
        return False

    def get_next_run_time(self, job_id: str = 'daily_review') -> Optional[str]:
        """获取下次执行时间"""
        if self.scheduler:
            job = self.scheduler.get_job(job_id)
            if job and hasattr(job, 'next_run_time') and job.next_run_time:
                return job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')
        return None

    def get_jobs(self) -> List[Dict[str, Any]]:
        """获取所有任务列表"""
        if not self.scheduler:
            return []

        jobs = self.scheduler.get_jobs()
        result = []
        for job in jobs:
            next_run = None
            if hasattr(job, 'next_run_time') and job.next_run_time:
                next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')
            result.append({
                'id': job.id,
                'name': job.name,
                'next_run_time': next_run,
                'trigger': str(job.trigger)
            })
        return result

    def add_job(
        self,
        func: Callable,
        trigger_type: str = 'cron',
        job_id: Optional[str] = None,
        **trigger_kwargs
    ) -> Optional[str]:
        """
        添加自定义任务

        Args:
            func: 任务函数
            trigger_type: 触发器类型 ('cron' 或 'interval')
            job_id: 任务 ID
            **trigger_kwargs: 触发器参数

        Returns:
            任务 ID
        """
        if self.scheduler is None:
            self.scheduler = BackgroundScheduler()

        if trigger_type == 'cron':
            trigger = CronTrigger(**trigger_kwargs)
        elif trigger_type == 'interval':
            trigger = IntervalTrigger(**trigger_kwargs)
        else:
            logger.error(f"不支持的触发器类型: {trigger_type}")
            return None

        job = self.scheduler.add_job(
            func=func,
            trigger=trigger,
            id=job_id,
            replace_existing=True
        )
        logger.info(f"任务 {job.id} 已添加")
        return job.id

    def remove_job(self, job_id: str) -> bool:
        """
        移除任务

        Args:
            job_id: 任务 ID

        Returns:
            是否成功移除
        """
        if self.scheduler:
            try:
                self.scheduler.remove_job(job_id)
                logger.info(f"任务 {job_id} 已移除")
                return True
            except Exception as e:
                logger.error(f"移除任务失败: {e}")
                return False
        return False

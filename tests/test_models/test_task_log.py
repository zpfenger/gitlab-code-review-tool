import pytest
from datetime import datetime
from app.models.task_log import TaskLog
from app.models.project import Project


class TestTaskLog:
    def test_task_log_creation(self, db_session):
        """测试创建任务日志"""
        # 先创建项目
        project = Project(
            name="测试项目",
            gitlab_url="https://gitlab.example.com",
            project_id=123
        )
        db_session.add(project)
        db_session.commit()

        # 创建日志
        log = TaskLog(
            project_id=project.id,
            task_type="daily",
            trigger_type="scheduled",
            status="running",
            start_time=datetime.utcnow()
        )
        db_session.add(log)
        db_session.commit()

        assert log.id is not None
        assert log.task_type == "daily"
        assert log.trigger_type == "scheduled"

    def test_task_log_completion(self, db_session):
        """测试任务完成"""
        project = Project(
            name="完成测试",
            gitlab_url="https://gitlab.example.com",
            project_id=456
        )
        db_session.add(project)
        db_session.commit()

        log = TaskLog(
            project_id=project.id,
            task_type="daily",
            trigger_type="manual",
            trigger_user="admin",
            status="success",
            start_time=datetime.utcnow(),
            end_time=datetime.utcnow(),
            branches_processed=5,
            commits_processed=20,
            reports_generated=5
        )
        db_session.add(log)
        db_session.commit()

        assert log.status == "success"
        assert log.branches_processed == 5

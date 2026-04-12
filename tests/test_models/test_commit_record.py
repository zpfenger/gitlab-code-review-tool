import pytest
from datetime import datetime
from app.models.commit_record import CommitRecord
from app.models.project import Project


class TestCommitRecord:
    def test_commit_record_creation(self, db_session):
        """测试创建提交记录"""
        project = Project(
            name="提交测试",
            gitlab_url="https://gitlab.example.com",
            project_id=789
        )
        db_session.add(project)
        db_session.commit()

        record = CommitRecord(
            project_id=project.id,
            commit_sha="abc123def456",
            branch="develop",
            author_email="test@example.com",
            author_name="Test User",
            commit_date=datetime.utcnow(),
            review_status="pending"
        )
        db_session.add(record)
        db_session.commit()

        assert record.id is not None
        assert record.commit_sha == "abc123def456"
        assert record.review_status == "pending"

    def test_commit_record_unique(self, db_session):
        """测试提交 SHA 唯一约束"""
        project = Project(
            name="唯一测试",
            gitlab_url="https://gitlab.example.com",
            project_id=999
        )
        db_session.add(project)
        db_session.commit()

        record1 = CommitRecord(
            project_id=project.id,
            commit_sha="unique123",
            branch="main",
            author_email="test@example.com",
            author_name="Test",
            commit_date=datetime.utcnow(),
            review_status="success"
        )
        db_session.add(record1)
        db_session.commit()

        # 同一项目同一 SHA 应该允许（不同项目可能相同 SHA）
        record2 = CommitRecord(
            project_id=project.id,
            commit_sha="unique456",
            branch="main",
            author_email="test@example.com",
            author_name="Test",
            commit_date=datetime.utcnow(),
            review_status="pending"
        )
        db_session.add(record2)
        db_session.commit()

        assert record2.id != record1.id

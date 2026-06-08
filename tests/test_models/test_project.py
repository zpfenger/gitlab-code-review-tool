import pytest
from app.models.project import Project


class TestProject:
    def test_project_creation(self, db_session):
        """测试创建项目"""
        project = Project(
            name="测试项目",
            gitlab_url="https://gitlab.example.com",
            project_id=123,
            exclude_branches='["master", "main"]',
            is_active=True
        )
        db_session.add(project)
        db_session.commit()

        assert project.id is not None
        assert project.name == "测试项目"
        assert project.is_active is True

    def test_project_default_active(self, db_session):
        """测试项目默认激活"""
        project = Project(
            name="默认项目",
            gitlab_url="https://gitlab.example.com",
            project_id=456
        )
        db_session.add(project)
        db_session.commit()

        assert project.is_active is True

    def test_project_optional_fields(self, db_session):
        """测试可选字段"""
        project = Project(
            name="可选字段测试",
            gitlab_url="https://gitlab.example.com",
            project_id=789,
            svn_url="https://svn.example.com/repo",
            svn_username="user",
            svn_password="encrypted_pass"
        )
        db_session.add(project)
        db_session.commit()

        assert project.svn_url == "https://svn.example.com/repo"

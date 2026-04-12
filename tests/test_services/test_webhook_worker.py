from app.models.project import Project
from app.services.webhook_worker import _resolve_project_for_webhook


def _create_project(db_session, *, name: str, project_id: int) -> Project:
    project = Project(
        name=name,
        project_id=project_id,
        gitlab_url="https://gitlab.example.com",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


class TestResolveProjectForWebhook:
    def test_resolve_fallback_to_name_when_id_and_path_not_found(self, db_session):
        """当 id/path 都不命中时，回退到 name"""
        project_by_name = _create_project(
            db_session, name="repo-by-name-last-fallback", project_id=3003
        )

        webhook_data = {
            "project": {
                "id": 888888,
                "path_with_namespace": "group/not-exists",
                "name": "repo-by-name-last-fallback",
            }
        }

        resolved = _resolve_project_for_webhook(db_session, webhook_data)

        assert resolved is project_by_name

    def test_resolve_prioritize_project_id_over_path_and_name(self, db_session):
        """当 id/path/name 同时可命中时，优先使用 project.id"""
        project_by_id = _create_project(db_session, name="repo-by-id", project_id=1001)
        _create_project(db_session, name="group/repo-by-path", project_id=1002)
        _create_project(db_session, name="repo-by-name", project_id=1003)

        webhook_data = {
            "project": {
                "id": 1001,
                "path_with_namespace": "group/repo-by-path",
                "name": "repo-by-name",
            }
        }

        resolved = _resolve_project_for_webhook(db_session, webhook_data)

        assert resolved is project_by_id

    def test_resolve_fallback_to_path_when_id_not_found(self, db_session):
        """当 id 不命中但 path/name 都可命中时，优先 path_with_namespace"""
        project_by_path = _create_project(
            db_session, name="group/repo-by-path-fallback", project_id=2002
        )
        _create_project(db_session, name="repo-by-name-fallback", project_id=2003)

        webhook_data = {
            "project": {
                "id": 999999,
                "path_with_namespace": "group/repo-by-path-fallback",
                "name": "repo-by-name-fallback",
            }
        }

        resolved = _resolve_project_for_webhook(db_session, webhook_data)

        assert resolved is project_by_path

    def test_resolve_prioritize_nested_project_id_over_top_level_project_id(
        self, db_session
    ):
        """当嵌套与顶层 id 同时存在时，优先使用 project.id"""
        project_nested_id = _create_project(db_session, name="repo-nested-id", project_id=4004)
        _create_project(db_session, name="repo-top-id", project_id=5005)

        webhook_data = {
            "project_id": 5005,
            "project": {
                "id": 4004,
                "path_with_namespace": "group/not-used",
                "name": "repo-not-used",
            },
        }

        resolved = _resolve_project_for_webhook(db_session, webhook_data)

        assert resolved is project_nested_id

    def test_resolve_use_top_level_project_id_when_nested_missing(self, db_session):
        """当 project.id 缺失时，使用顶层 project_id 匹配"""
        project_top_level_id = _create_project(
            db_session, name="repo-top-level-id", project_id=6006
        )

        webhook_data = {
            "project_id": "6006",
            "project": {
                "path_with_namespace": "group/not-used",
                "name": "repo-not-used",
            },
        }

        resolved = _resolve_project_for_webhook(db_session, webhook_data)

        assert resolved is project_top_level_id

    def test_resolve_ignore_invalid_project_id_and_fallback_to_path(self, db_session):
        """当 id 非法时，忽略并继续 path/name 回退链路"""
        project_by_path = _create_project(
            db_session, name="group/repo-invalid-id-path", project_id=7007
        )

        webhook_data = {
            "project_id": "not-a-number",
            "project": {
                "id": "also-invalid",
                "path_with_namespace": "group/repo-invalid-id-path",
                "name": "repo-not-used",
            },
        }

        resolved = _resolve_project_for_webhook(db_session, webhook_data)

        assert resolved is project_by_path

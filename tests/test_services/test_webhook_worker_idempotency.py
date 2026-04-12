from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError

from app.services import webhook_worker


def _mock_integrity_error(message: str) -> IntegrityError:
    class _OrigExc(Exception):
        pass

    orig = _OrigExc(message)
    return IntegrityError("insert", {}, orig)


def _build_mr_payload() -> dict:
    return {
        "project": {"name": "demo-project"},
        "user": {"username": "alice"},
        "object_attributes": {
            "action": "open",
            "source_branch": "feat/a",
            "target_branch": "main",
            "url": "https://gitlab.example.com/mr/1",
            "last_commit": {"id": "abc123"},
        },
    }


def _build_push_payload() -> dict:
    return {
        "project": {"name": "demo-project"},
        "ref": "refs/heads/main",
        "after": "def456",
        "user_username": "bob",
    }


def _build_settings(push_enabled: bool = True):
    settings = MagicMock()
    settings.llm_api_key = ""
    settings.llm_api_url = "https://api.example.com/v1"
    settings.llm_model = "gpt-4"
    settings.llm_max_tokens = 1024
    settings.llm_temperature = 0.1
    settings.llm_timeout = 30
    settings.llm_max_retries = 1
    settings.llm_retry_delay = 1
    settings.review_style = "professional"
    settings.review_max_tokens = 2048
    settings.webhook_review_prompt = "review"
    settings.supported_extensions = ".py"
    settings.push_review_enabled = push_enabled
    return settings


def _build_db_session(settings):
    db = MagicMock()

    settings_query = MagicMock()
    settings_query.filter.return_value = settings_query
    settings_query.first.return_value = settings

    empty_query = MagicMock()
    empty_query.filter.return_value = empty_query
    empty_query.first.return_value = None

    def _query_side_effect(model):
        if model is webhook_worker.Settings:
            return settings_query
        return empty_query

    db.query.side_effect = _query_side_effect
    return db


def test_handle_merge_request_event_ignore_dedup_integrity_error():
    webhook_data = _build_mr_payload()
    settings = _build_settings()
    db = _build_db_session(settings)
    db.commit.side_effect = _mock_integrity_error("UNIQUE constraint failed: uq_mr_review_log_dedup")

    handler = MagicMock()
    handler.action = "open"
    handler.get_merge_request_changes.return_value = [{"additions": 1, "deletions": 0, "new_path": "a.py"}]
    handler.get_merge_request_commits.return_value = [{"title": "feat", "author_name": "alice"}]

    reviewer = MagicMock()
    reviewer.review_and_strip_code.return_value = "score: 90"

    notifier = MagicMock()

    with patch.object(webhook_worker, "SessionLocal", return_value=db), \
         patch.object(webhook_worker, "MergeRequestHandler", return_value=handler), \
         patch.object(webhook_worker, "_resolve_project_for_webhook", return_value=None), \
         patch.object(webhook_worker, "_build_reviewer", return_value=reviewer), \
         patch.object(webhook_worker, "_build_notifier", return_value=notifier), \
         patch.object(webhook_worker.WebhookReviewer, "parse_review_score", return_value=90):
        webhook_worker.handle_merge_request_event(webhook_data, "token", "https://gitlab.example.com")

    db.commit.assert_called_once()
    db.rollback.assert_called_once()
    reviewer.review_and_strip_code.assert_called_once()
    notifier.send_notification.assert_called_once()


def test_handle_push_event_ignore_dedup_integrity_error():
    webhook_data = _build_push_payload()
    settings = _build_settings(push_enabled=True)
    db = _build_db_session(settings)
    db.commit.side_effect = _mock_integrity_error("UNIQUE constraint failed: uq_push_review_log_dedup")

    handler = MagicMock()
    handler.get_push_commits.return_value = [{"message": "fix", "author": "bob", "timestamp": "now", "id": "def456"}]
    handler.get_push_changes.return_value = [{"additions": 1, "deletions": 0, "new_path": "a.py"}]

    reviewer = MagicMock()
    reviewer.review_and_strip_code.return_value = "score: 88"

    notifier = MagicMock()

    with patch.object(webhook_worker, "SessionLocal", return_value=db), \
         patch.object(webhook_worker, "PushHandler", return_value=handler), \
         patch.object(webhook_worker, "_resolve_project_for_webhook", return_value=None), \
         patch.object(webhook_worker, "_build_reviewer", return_value=reviewer), \
         patch.object(webhook_worker, "_build_notifier", return_value=notifier), \
         patch.object(webhook_worker.WebhookReviewer, "parse_review_score", return_value=88):
        webhook_worker.handle_push_event(webhook_data, "token", "https://gitlab.example.com")

    db.commit.assert_called_once()
    db.rollback.assert_called_once()
    reviewer.review_and_strip_code.assert_called_once()
    notifier.send_notification.assert_called_once()


def test_handle_merge_request_event_non_dedup_integrity_error_goes_to_exception_flow():
    webhook_data = _build_mr_payload()
    settings = _build_settings()
    db = _build_db_session(settings)
    db.commit.side_effect = _mock_integrity_error("FOREIGN KEY constraint failed")

    handler = MagicMock()
    handler.action = "open"
    handler.get_merge_request_changes.return_value = [{"additions": 1, "deletions": 0, "new_path": "a.py"}]
    handler.get_merge_request_commits.return_value = [{"title": "feat", "author_name": "alice"}]

    reviewer = MagicMock()
    reviewer.review_and_strip_code.return_value = "score: 90"

    notifier = MagicMock()

    with patch.object(webhook_worker, "SessionLocal", return_value=db), \
         patch.object(webhook_worker, "MergeRequestHandler", return_value=handler), \
         patch.object(webhook_worker, "_resolve_project_for_webhook", return_value=None), \
         patch.object(webhook_worker, "_build_reviewer", return_value=reviewer), \
         patch.object(webhook_worker, "_build_notifier", return_value=notifier), \
         patch.object(webhook_worker.WebhookReviewer, "parse_review_score", return_value=90):
        webhook_worker.handle_merge_request_event(webhook_data, "token", "https://gitlab.example.com")

    db.commit.assert_called_once()
    db.rollback.assert_called_once()
    assert notifier.send_notification.call_count == 2


def test_handle_push_event_non_dedup_integrity_error_logs_error():
    webhook_data = _build_push_payload()
    settings = _build_settings(push_enabled=True)
    db = _build_db_session(settings)
    db.commit.side_effect = _mock_integrity_error("FOREIGN KEY constraint failed")

    handler = MagicMock()
    handler.get_push_commits.return_value = [{"message": "fix", "author": "bob", "timestamp": "now", "id": "def456"}]
    handler.get_push_changes.return_value = [{"additions": 1, "deletions": 0, "new_path": "a.py"}]

    reviewer = MagicMock()
    reviewer.review_and_strip_code.return_value = "score: 88"

    notifier = MagicMock()

    with patch.object(webhook_worker, "SessionLocal", return_value=db), \
         patch.object(webhook_worker, "PushHandler", return_value=handler), \
         patch.object(webhook_worker, "_resolve_project_for_webhook", return_value=None), \
         patch.object(webhook_worker, "_build_reviewer", return_value=reviewer), \
         patch.object(webhook_worker, "_build_notifier", return_value=notifier), \
         patch.object(webhook_worker.WebhookReviewer, "parse_review_score", return_value=88), \
         patch.object(webhook_worker.logger, "error") as mock_log_error:
        webhook_worker.handle_push_event(webhook_data, "token", "https://gitlab.example.com")

    db.commit.assert_called_once()
    db.rollback.assert_called_once()
    notifier.send_notification.assert_called_once()
    mock_log_error.assert_called()


def test_handle_merge_request_event_skip_when_dedup_exists():
    webhook_data = _build_mr_payload()
    settings = _build_settings()

    db = MagicMock()
    settings_query = MagicMock()
    settings_query.filter.return_value = settings_query
    settings_query.first.return_value = settings

    exists_query = MagicMock()
    exists_query.filter.return_value = exists_query
    exists_query.first.return_value = object()

    empty_query = MagicMock()
    empty_query.filter.return_value = empty_query
    empty_query.first.return_value = None

    def _query_side_effect(model):
        if model is webhook_worker.Settings:
            return settings_query
        if model is webhook_worker.MrReviewLog:
            return exists_query
        return empty_query

    db.query.side_effect = _query_side_effect

    handler = MagicMock()
    handler.action = "open"

    reviewer = MagicMock()
    notifier = MagicMock()

    with patch.object(webhook_worker, "SessionLocal", return_value=db), \
         patch.object(webhook_worker, "MergeRequestHandler", return_value=handler), \
         patch.object(webhook_worker, "_resolve_project_for_webhook", return_value=None), \
         patch.object(webhook_worker, "_build_reviewer", return_value=reviewer), \
         patch.object(webhook_worker, "_build_notifier", return_value=notifier):
        webhook_worker.handle_merge_request_event(webhook_data, "token", "https://gitlab.example.com")

    exists_filter_args = exists_query.filter.call_args.args
    assert len(exists_filter_args) == 4
    db.commit.assert_not_called()
    reviewer.review_and_strip_code.assert_not_called()
    notifier.send_notification.assert_not_called()


def test_handle_push_event_skip_when_dedup_exists():
    webhook_data = _build_push_payload()
    settings = _build_settings(push_enabled=True)

    db = MagicMock()
    settings_query = MagicMock()
    settings_query.filter.return_value = settings_query
    settings_query.first.return_value = settings

    exists_query = MagicMock()
    exists_query.filter.return_value = exists_query
    exists_query.first.return_value = object()

    empty_query = MagicMock()
    empty_query.filter.return_value = empty_query
    empty_query.first.return_value = None

    def _query_side_effect(model):
        if model is webhook_worker.Settings:
            return settings_query
        if model is webhook_worker.PushReviewLog:
            return exists_query
        return empty_query

    db.query.side_effect = _query_side_effect

    handler = MagicMock()
    handler.get_push_commits.return_value = [{"message": "fix", "author": "bob", "timestamp": "now", "id": "def456"}]

    reviewer = MagicMock()
    notifier = MagicMock()

    with patch.object(webhook_worker, "SessionLocal", return_value=db), \
         patch.object(webhook_worker, "PushHandler", return_value=handler), \
         patch.object(webhook_worker, "_resolve_project_for_webhook", return_value=None), \
         patch.object(webhook_worker, "_build_reviewer", return_value=reviewer), \
         patch.object(webhook_worker, "_build_notifier", return_value=notifier):
        webhook_worker.handle_push_event(webhook_data, "token", "https://gitlab.example.com")

    exists_filter_args = exists_query.filter.call_args.args
    assert len(exists_filter_args) == 3
    db.commit.assert_not_called()
    reviewer.review_and_strip_code.assert_not_called()
    notifier.send_notification.assert_not_called()

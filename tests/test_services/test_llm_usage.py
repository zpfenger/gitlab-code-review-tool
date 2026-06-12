from app.models.token_usage import TokenUsageLog
from app.services.llm_usage import (
    TokenUsage,
    aggregate_token_usage_by_biz,
    empty_token_totals,
    parse_usage,
    record_token_usage,
)


def test_parse_usage_returns_token_usage_for_valid_response():
    usage = parse_usage(
        {
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            }
        },
        model="gpt-4o-mini",
    )

    assert usage == TokenUsage(
        model="gpt-4o-mini",
        prompt_tokens=12,
        completion_tokens=8,
        total_tokens=20,
    )


def test_parse_usage_returns_none_when_usage_missing():
    assert parse_usage({"choices": []}, model="gpt-4o-mini") is None


def test_parse_usage_returns_none_when_usage_is_malformed():
    assert (
        parse_usage(
            {
                "usage": {
                    "prompt_tokens": "12",
                    "completion_tokens": 8,
                    "total_tokens": 20,
                }
            },
            model="gpt-4o-mini",
        )
        is None
    )


def test_record_token_usage_persists_usage(db_session):
    usage = TokenUsage(
        model="deepseek-chat",
        prompt_tokens=100,
        completion_tokens=25,
        total_tokens=125,
    )

    record_token_usage(
        db=db_session,
        biz_type="report",
        biz_id=7,
        project_name="project-a",
        author="Alice",
        usage=usage,
        created_at_ts=1_780_000_000,
    )

    row = db_session.query(TokenUsageLog).one()
    assert row.biz_type == "report"
    assert row.biz_id == 7
    assert row.project_name == "project-a"
    assert row.author == "Alice"
    assert row.model == "deepseek-chat"
    assert row.prompt_tokens == 100
    assert row.completion_tokens == 25
    assert row.total_tokens == 125
    assert row.created_at_ts == 1_780_000_000


def test_record_token_usage_skips_none_usage(db_session):
    record_token_usage(
        db=db_session,
        biz_type="report",
        biz_id=7,
        project_name="project-a",
        author="Alice",
        usage=None,
    )

    assert db_session.query(TokenUsageLog).count() == 0


def test_record_token_usage_does_not_raise_when_db_fails():
    class FailingSession:
        def __init__(self):
            self.rollback_called = False

        def add(self, _obj):
            raise RuntimeError("db is unavailable")

        def commit(self):
            raise AssertionError("commit should not be reached")

        def rollback(self):
            self.rollback_called = True

    db = FailingSession()

    record_token_usage(
        db=db,
        biz_type="report",
        biz_id=1,
        project_name="project-a",
        author="Alice",
        usage=TokenUsage(
            model="gpt-4",
            prompt_tokens=1,
            completion_tokens=2,
            total_tokens=3,
        ),
    )

    assert db.rollback_called is True


def test_aggregate_token_usage_by_biz_sums_matching_rows(db_session):
    rows = [
        TokenUsageLog(
            biz_type="webhook_mr",
            biz_id=1,
            project_name="project-a",
            model="gpt-4",
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
            created_at_ts=100,
        ),
        TokenUsageLog(
            biz_type="webhook_mr",
            biz_id=1,
            project_name="project-a",
            model="gpt-4",
            prompt_tokens=7,
            completion_tokens=3,
            total_tokens=10,
            created_at_ts=101,
        ),
        TokenUsageLog(
            biz_type="webhook_push",
            biz_id=1,
            project_name="project-a",
            model="gpt-4",
            prompt_tokens=99,
            completion_tokens=99,
            total_tokens=198,
            created_at_ts=102,
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()

    result = aggregate_token_usage_by_biz(db_session, "webhook_mr", [1, 2])

    assert result[1] == {
        "prompt_tokens": 17,
        "completion_tokens": 5,
        "total_tokens": 22,
    }
    assert result[2] == empty_token_totals()

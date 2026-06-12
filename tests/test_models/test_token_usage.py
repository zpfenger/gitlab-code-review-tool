from app.models.token_usage import TokenUsageLog


def test_token_usage_log_table_and_columns():
    assert TokenUsageLog.__tablename__ == "token_usage_log"

    columns = TokenUsageLog.__table__.columns
    expected = {
        "id",
        "biz_type",
        "biz_id",
        "project_name",
        "author",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "created_at_ts",
        "created_at",
        "updated_at",
    }

    assert expected.issubset(set(columns.keys()))
    assert columns["biz_type"].nullable is False
    assert columns["model"].nullable is False
    assert columns["created_at_ts"].nullable is False


def test_token_usage_log_indexes():
    index_names = {idx.name for idx in TokenUsageLog.__table__.indexes}

    assert "idx_token_usage_biz" in index_names
    assert "idx_token_usage_created_at_ts" in index_names

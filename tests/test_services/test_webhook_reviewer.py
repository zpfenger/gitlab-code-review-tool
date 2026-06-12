from unittest.mock import Mock, patch

from app.services.llm_usage import LLMResult
from app.services.webhook_reviewer import WebhookReviewer


def test_call_llm_success_includes_token_usage():
    reviewer = WebhookReviewer(
        api_url="https://api.example.com/v1/chat/completions",
        api_key="sk-test",
        model="gpt-4",
    )
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": "审查完成"}}],
        "usage": {
            "prompt_tokens": 11,
            "completion_tokens": 4,
            "total_tokens": 15,
        },
    }

    with patch("app.services.webhook_reviewer.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = response

        result = reviewer._call_llm([{"role": "user", "content": "diff"}])

    assert isinstance(result, LLMResult)
    assert result.content == "审查完成"
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens == 4
    assert result.usage.total_tokens == 15


def test_review_and_strip_code_preserves_usage():
    reviewer = WebhookReviewer(
        api_url="https://api.example.com/v1/chat/completions",
        api_key="sk-test",
        model="gpt-4",
    )
    response = Mock()
    response.raise_for_status = Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": "```markdown\n审查完成\n```"}}],
        "usage": {
            "prompt_tokens": 9,
            "completion_tokens": 3,
            "total_tokens": 12,
        },
    }

    with patch("app.services.webhook_reviewer.httpx.Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.return_value = response

        result = reviewer.review_and_strip_code("+print('hi')", "feat")

    assert isinstance(result, LLMResult)
    assert result.content == "审查完成"
    assert result.usage.total_tokens == 12

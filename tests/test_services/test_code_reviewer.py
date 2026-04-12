# tests/test_services/test_code_reviewer.py
"""LLM 代码审查服务测试"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
import httpx
from app.services.code_reviewer import CodeReviewer


class TestCodeReviewer:
    """LLM 代码审查服务测试类"""

    @pytest.fixture
    def reviewer(self):
        """创建代码审查器实例"""
        return CodeReviewer(
            api_url="https://api.example.com/v1/chat/completions",
            api_key="test-api-key",
            model="gpt-4",
            timeout=120,
            max_retries=3,
            retry_delay=1
        )

    @pytest.fixture
    def sample_diff(self):
        """示例代码差异"""
        return """diff --git a/test.py b/test.py
index 1234567..abcdefg 100644
--- a/test.py
+++ b/test.py
@@ -1,5 +1,5 @@
 def hello():
-    print("hello")
+    print("hello world")
     return True
"""

    @pytest.fixture
    def sample_prompt(self):
        """示例提示词"""
        return "请审查以下代码变更，指出潜在问题并提供改进建议：\n{diff}"

    @pytest.mark.asyncio
    async def test_init(self, reviewer):
        """测试初始化"""
        assert reviewer.api_url == "https://api.example.com/v1/chat/completions"
        assert reviewer.api_key == "test-api-key"
        assert reviewer.model == "gpt-4"
        assert reviewer.timeout == 120
        assert reviewer.max_retries == 3
        assert reviewer.retry_delay == 1

    @pytest.mark.asyncio
    async def test_review_success(self, reviewer, sample_diff, sample_prompt):
        """测试成功的代码审查"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "代码审查结果：\n1. 改进建议：..."
                    }
                }
            ]
        }
        mock_response.raise_for_status = Mock()

        with patch.object(reviewer.client, 'post', new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await reviewer.review(
                diff=sample_diff,
                prompt_template=sample_prompt
            )

            assert result is not None
            assert "代码审查结果" in result
            mock_post.assert_called_once()

    @pytest.mark.asyncio
    async def test_review_with_custom_system_prompt(self, reviewer, sample_diff):
        """测试使用自定义系统提示的代码审查"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "审查完成"
                    }
                }
            ]
        }
        mock_response.raise_for_status = Mock()

        with patch.object(reviewer.client, 'post', new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await reviewer.review(
                diff=sample_diff,
                prompt_template="审查代码：{diff}",
                system_prompt="你是一位资深代码审查专家"
            )

            assert result is not None
            call_args = mock_post.call_args
            request_body = call_args[1]["json"]
            assert request_body["messages"][0]["role"] == "system"
            assert request_body["messages"][0]["content"] == "你是一位资深代码审查专家"

    @pytest.mark.asyncio
    async def test_review_timeout_retry(self, reviewer, sample_diff, sample_prompt):
        """测试超时重试"""
        reviewer.max_retries = 2
        reviewer.retry_delay = 0.1

        with patch.object(reviewer.client, 'post', new_callable=AsyncMock) as mock_post:
            # 第一次超时，第二次成功
            mock_post.side_effect = [
                httpx.TimeoutException("Request timeout"),
                Mock(
                    status_code=200,
                    json=lambda: {"choices": [{"message": {"content": "成功"}}]},
                    raise_for_status=Mock()
                )
            ]

            result = await reviewer.review(
                diff=sample_diff,
                prompt_template=sample_prompt
            )

            assert result == "成功"
            assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_review_max_retries_exceeded(self, reviewer, sample_diff, sample_prompt):
        """测试超过最大重试次数"""
        reviewer.max_retries = 2
        reviewer.retry_delay = 0.1

        with patch.object(reviewer.client, 'post', new_callable=AsyncMock) as mock_post:
            # 所有请求都超时
            mock_post.side_effect = httpx.TimeoutException("Request timeout")

            result = await reviewer.review(
                diff=sample_diff,
                prompt_template=sample_prompt
            )

            assert result is None
            # max_retries=2 表示最多尝试2次
            assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_review_http_error(self, reviewer, sample_diff, sample_prompt):
        """测试 HTTP 错误"""
        with patch.object(reviewer.client, 'post', new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = httpx.HTTPStatusError(
                "500 Server Error",
                request=Mock(),
                response=Mock(status_code=500)
            )

            result = await reviewer.review(
                diff=sample_diff,
                prompt_template=sample_prompt
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_review_empty_diff(self, reviewer, sample_prompt):
        """测试空差异"""
        result = await reviewer.review(
            diff="",
            prompt_template=sample_prompt
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_review_commit(self, reviewer):
        """测试审查提交"""
        commit_info = {
            "id": "abc123",
            "title": "Test commit",
            "author_name": "Test User",
            "message": "Test commit message"
        }
        diffs = [
            {
                "diff": "@@ -1 +1 @@\n-old\n+new\n",
                "new_path": "test.py",
                "old_path": "test.py"
            }
        ]

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "审查结果"
                    }
                }
            ]
        }
        mock_response.raise_for_status = Mock()

        with patch.object(reviewer.client, 'post', new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await reviewer.review_commit(
                commit_info=commit_info,
                diffs=diffs
            )

            assert result is not None
            assert "审查结果" in result

    @pytest.mark.asyncio
    async def test_format_diff(self, reviewer):
        """测试格式化差异"""
        diffs = [
            {
                "diff": "@@ -1 +1 @@\n-old\n+new\n",
                "new_path": "test.py",
                "old_path": "test.py",
                "new_file": False,
                "deleted_file": False
            },
            {
                "diff": "@@ -0,0 +1,5 @@\n+new file\n",
                "new_path": "new.py",
                "old_path": "/dev/null",
                "new_file": True,
                "deleted_file": False
            }
        ]

        formatted = reviewer._format_diff(diffs)

        assert "test.py" in formatted
        assert "new.py" in formatted
        assert "[NEW FILE]" in formatted

    def test_get_default_prompt(self, reviewer):
        """测试获取默认提示词"""
        prompt = reviewer.get_default_prompt()
        assert "代码审查" in prompt
        assert "{diff}" in prompt

    @pytest.mark.asyncio
    async def test_close(self, reviewer):
        """测试关闭客户端"""
        with patch.object(reviewer.client, 'aclose', new_callable=AsyncMock) as mock_close:
            await reviewer.close()
            mock_close.assert_called_once()

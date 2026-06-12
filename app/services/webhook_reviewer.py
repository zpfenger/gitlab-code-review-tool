"""Webhook 代码审查器 — 基于当前项目 LLM 配置调用大模型审查代码"""
import re
from typing import List, Dict, Optional

import httpx
from loguru import logger

from app.services.llm_usage import LLMResult, parse_usage
from app.services.truncate_utils import truncate_text


# ── 默认 Prompt ────────────────────────────────────────────────
DEFAULT_SYSTEM_PROMPT = """你是一位资深的软件开发工程师，专注于代码的规范性、功能性、安全性和稳定性。本次任务是对员工的代码进行审查，具体要求如下：

### 代码审查目标：
1. 注释（5分）： 注释要"有用"不冗余：只注释"为什么这么做"，而非"做了什么"，避免无意义、与代码脱节的注释。
2. 业务逻辑校验（30分）：是否符合需求文档的核心规则，有无业务场景遗漏？异常处理是否合理，错误码/提示信息是否清晰且便于排查？数据库交互是否存在 N+1 查询、未加索引、事务未正确控制的问题。
3. 性能优化点（40分）：是否存在循环嵌套、重复计算、大对象频繁创建等性能瓶颈？缓存策略是否合理？IO 操作是否同步阻塞，有无优化空间。
4. 安全风险排查（10分）：是否存在 SQL 注入、XSS、CSRF 等常见安全漏洞？敏感数据是否脱敏，权限校验是否覆盖所有入口？
5. 代码架构与扩展性（10分）：是否遵循 SOLID 原则，有无过度耦合？配置项是否硬编码？
6. 编码规范（5分）：命名/注释/代码格式是否统一？测试覆盖率是否足够？

### 输出格式:
请以Markdown格式输出代码审查报告，并包含以下内容：
1. 报告头部信息：包含审查日期（使用当前系统时间，格式：YYYY-MM-DD）、审查范围（本次提交的变更文件数）、审查数量（文件数量）。
2. 问题描述和优化建议(如果有)：列出代码中存在的问题，简要说明其影响，并给出优化建议。
3. 评分明细：为每个评分标准提供具体分数。
4. 总分：格式为"总分:XX分"（例如：总分:80分），确保可通过正则表达式解析出总分。

注意：审查日期必须使用当前实际日期，不要使用示例日期或固定日期。"""

STYLE_INSTRUCTIONS = {
    "professional": "评论时请使用标准的工程术语，保持专业严谨。",
    "sarcastic": "评论时请大胆使用讽刺性语言，但要确保技术指正准确。",
    "gentle": '评论时请多用"建议"、"可以考虑"等温和措辞。',
    "humorous": "评论时请在技术点评中加入适当幽默元素，合理使用Emoji。",
}

DEFAULT_USER_PROMPT = """以下是某位员工向代码库提交的代码，请审查以下代码。

代码变更内容：
{diffs_text}

提交历史(commits)：
{commits_text}"""


def _build_system_prompt(style: str, custom_prompt: Optional[str] = None) -> str:
    """构建带风格的系统提示词"""
    base = custom_prompt or DEFAULT_SYSTEM_PROMPT
    style_hint = STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS["professional"])
    return f"{base}\n\n### 特别说明：\n整个评论要保持{style}风格。{style_hint}"


def _truncate_text(text: str, max_tokens: int) -> str:
    """简易 token 截断 — 委托给共享截断工具"""
    return truncate_text(text, max_tokens)


class WebhookReviewer:
    """Webhook 触发的实时代码审查器"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout: int = 240,
        max_retries: int = 5,
        retry_delay: int = 60,
        review_style: str = "professional",
        review_max_tokens: int = 10000,
        custom_prompt: Optional[str] = None,
    ):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.review_style = review_style
        self.review_max_tokens = review_max_tokens
        self.system_prompt = _build_system_prompt(review_style, custom_prompt)

    def review_and_strip_code(self, changes_text: str, commits_text: str = "") -> LLMResult:
        """审查代码并返回结果（同步）"""
        if not changes_text:
            logger.info("代码变更为空，跳过审查")
            return LLMResult(content="代码为空", usage=None)

        changes_text = _truncate_text(changes_text, self.review_max_tokens)

        user_content = DEFAULT_USER_PROMPT.format(
            diffs_text=changes_text,
            commits_text=commits_text,
        )

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_content},
        ]

        result = self._call_llm(messages)
        if not result:
            return LLMResult(content="LLM 调用失败，未能完成审查", usage=None)

        content = result.strip()
        # 去除 markdown 代码块包裹
        if content.startswith("```markdown") and content.endswith("```"):
            content = content[11:-3].strip()
        return LLMResult(content=content, usage=result.usage)

    def _call_llm(self, messages: List[Dict[str, str]]) -> Optional[LLMResult]:
        """同步调用 LLM API（带重试）"""
        import time

        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        self.api_url,
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model,
                            "messages": messages,
                            "temperature": self.temperature,
                            "max_tokens": self.max_tokens,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    content = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content")
                    )
                    usage = parse_usage(data, self.model)
                    if content:
                        logger.info("Webhook 代码审查完成")
                        return LLMResult(content=content, usage=usage)
                    logger.warning("LLM 返回空内容")
                    return None

            except httpx.TimeoutException:
                logger.warning(
                    f"LLM 请求超时 (尝试 {attempt + 1}/{self.max_retries})"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
            except httpx.HTTPStatusError as e:
                logger.error(f"LLM 请求失败: {e.response.status_code}")
                return None
            except Exception as e:
                logger.error(f"LLM 请求异常: {e}")
                return None

        logger.error("达到最大重试次数，放弃请求")
        return None

    @staticmethod
    def parse_review_score(review_text: str) -> int:
        """从审查结果中解析评分"""
        if not review_text:
            return 0
        match = re.search(r"总分[:：]\s*(\d+)分?", review_text)
        return int(match.group(1)) if match else 0

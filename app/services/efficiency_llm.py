"""人员能效 LLM 调用与解析模块

职责：
1. 构造 prompt（要求 LLM 同时输出评分 + 工作总结 + 简述）
2. 调 LLM（复用 settings 配置）
3. 解析输出（score / grade / work_summary / review_summary）

与 webhook_reviewer 解耦，独立单元便于测试。
"""
from __future__ import annotations
import re
from typing import List, Optional, Dict

import httpx
from loguru import logger


# ── Prompt 模板 ────────────────────────────────────────
EFFICIENCY_SYSTEM_PROMPT = """你是一位资深的软件开发工程师，专注于代码的规范性、功能性、安全性和稳定性。本次任务是对单个员工"某一天"提交的代码进行综合评审，并提炼当日主要工作内容。

### 评分目标（与日报审查一致）：
1. 注释（5分）：注释要"有用"不冗余，只注释"为什么这么做"，避免无意义、与代码脱节的注释。
2. 业务逻辑校验（30分）：是否符合需求文档的核心规则、异常处理是否合理、数据库交互是否存在 N+1 查询等。
3. 性能优化点（40分）：是否存在循环嵌套、重复计算、大对象频繁创建等性能瓶颈、缓存策略、IO 同步阻塞。
4. 安全风险排查（10分）：是否存在 SQL 注入、XSS、CSRF；敏感数据脱敏；权限校验覆盖。
5. 代码架构与扩展性（10分）：是否遵循 SOLID、有无过度耦合、配置项是否硬编码。
6. 编码规范（5分）：命名/注释/格式统一性，测试覆盖率。

### 输出格式（严格按照）：
请按以下 Markdown 结构输出，确保所有标记都存在，便于程序解析：

## 评分简述
（1-2 句话点明当日代码的整体质量与突出问题）

## 评分明细
- 注释（5分）：x 分，说明
- 业务逻辑校验（30分）：x 分，说明
- 性能优化点（40分）：x 分，说明
- 安全风险排查（10分）：x 分，说明
- 代码架构与扩展性（10分）：x 分，说明
- 编码规范（5分）：x 分，说明

## 主要工作（不超过 {top_n} 条）
1. xxx
2. xxx
3. xxx
（按对业务的影响和工作量排序，简单的修复、typo、格式调整请合并或忽略）

## 总分：XX 分
"""


EFFICIENCY_USER_PROMPT = """以下是员工 {author_name} 当日的代码提交内容。

### 提交信息（commits）：
{commits_text}

### 代码变更（diffs）：
{diffs_text}

请按系统提示的格式输出评分简述、评分明细、主要工作（不超过 {top_n} 条）和总分。"""


# ── 等级映射 ──────────────────────────────────────────
def map_score_to_grade(score: Optional[int]) -> Optional[str]:
    """根据分数映射到等级"""
    if score is None:
        return None
    if score >= 90:
        return "优秀"
    if score >= 75:
        return "良好"
    if score >= 60:
        return "一般"
    return "待改进"


# ── 解析函数 ──────────────────────────────────────────
_SCORE_PATTERN = re.compile(r"总分[:：]\s*(\d+)\s*分?")
_WORK_HEADER_PATTERN = re.compile(r"##\s*主要工作.*?\n(.+?)(?=\n##|\Z)", re.DOTALL)
_WORK_ITEM_PATTERN = re.compile(r"^\s*(?:\d+[.、)]|\-|\*)\s*(.+?)\s*$", re.MULTILINE)
_REVIEW_SUMMARY_PATTERN = re.compile(r"##\s*评分简述.*?\n(.+?)(?=\n##|\Z)", re.DOTALL)


def parse_score(text: str) -> int:
    """从 LLM 输出中解析总分（0 表示未识别）"""
    if not text:
        return 0
    match = _SCORE_PATTERN.search(text)
    return int(match.group(1)) if match else 0


def parse_work_summary(text: str, top_n: int = 5) -> List[str]:
    """从 LLM 输出中提取工作总结条目列表"""
    if not text:
        return []
    block_match = _WORK_HEADER_PATTERN.search(text)
    if not block_match:
        return []
    block = block_match.group(1)
    items = [m.group(1).strip() for m in _WORK_ITEM_PATTERN.finditer(block)]
    items = [it for it in items if it]
    return items[:top_n]


def parse_review_summary(text: str) -> str:
    """从 LLM 输出中提取评分简述段落（fallback：截断前 200 字）"""
    if not text:
        return ""
    match = _REVIEW_SUMMARY_PATTERN.search(text)
    if match:
        summary = match.group(1).strip()
        # 取第一段（双换行分隔）
        return summary.split("\n\n")[0].strip()[:200]
    return text[:200]


# ── Prompt 构造 ───────────────────────────────────────
def build_system_prompt(top_n: int = 5) -> str:
    return EFFICIENCY_SYSTEM_PROMPT.format(top_n=top_n)


def build_user_prompt(author_name: str, commits_text: str,
                       diffs_text: str, top_n: int = 5) -> str:
    return EFFICIENCY_USER_PROMPT.format(
        author_name=author_name,
        commits_text=commits_text or "(无)",
        diffs_text=diffs_text or "(无)",
        top_n=top_n,
    )


# ── LLM 调用 ──────────────────────────────────────────
def _truncate(text: str, max_tokens: int) -> str:
    """按字符近似截断（1 token ≈ 4 字符）"""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    logger.warning(f"文本长度 {len(text)} 超限 {max_chars}，已截断")
    return text[:max_chars] + "\n\n... (内容已截断)"


def call_llm(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    commits_text: str,
    diffs_text: str,
    top_n: int = 5,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    timeout: int = 240,
    max_retries: int = 3,
    retry_delay: int = 10,
    review_max_tokens: int = 10000,
) -> Optional[str]:
    """同步调用 LLM，返回原始 markdown 文本；失败返回 None"""
    import time

    diffs_text = _truncate(diffs_text, review_max_tokens)
    commits_text = _truncate(commits_text, review_max_tokens // 5)

    messages = [
        {"role": "system", "content": build_system_prompt(top_n=top_n)},
        {"role": "user", "content": build_user_prompt(
            author_name=author_name,
            commits_text=commits_text,
            diffs_text=diffs_text,
            top_n=top_n,
        )},
    ]

    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                content = (data.get("choices", [{}])[0]
                              .get("message", {})
                              .get("content"))
                if content:
                    return content
                logger.warning("LLM 返回空内容")
                return None
        except httpx.TimeoutException:
            logger.warning(f"LLM 请求超时 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
        except Exception as e:
            logger.error(f"LLM 请求异常: {type(e).__name__}: {e}")
            return None

    logger.error("达到最大重试次数")
    return None


def call_and_parse(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    commits_text: str,
    diffs_text: str,
    top_n: int = 5,
    **llm_kwargs,
) -> Dict[str, object]:
    """便捷封装：调用 LLM 并解析所有字段

    返回字典:
        {
            "raw": str | None,        # 原始输出
            "score": int,             # 0-100
            "grade": str | None,
            "work_summary": list[str],
            "review_summary": str,
            "success": bool,
        }
    """
    raw = call_llm(
        api_url=api_url, api_key=api_key, model=model,
        author_name=author_name, commits_text=commits_text,
        diffs_text=diffs_text, top_n=top_n, **llm_kwargs,
    )
    if raw is None:
        return {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }
    score = parse_score(raw)
    return {
        "raw": raw,
        "score": score,
        "grade": map_score_to_grade(score) if score > 0 else None,
        "work_summary": parse_work_summary(raw, top_n=top_n),
        "review_summary": parse_review_summary(raw),
        "success": True,
    }

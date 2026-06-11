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

from app.services.truncate_utils import truncate_text, truncate_diffs_by_files
from app.services.efficiency_prompt_template import (
    EFFICIENCY_STANDARD_TEMPLATE,
    EFFICIENCY_MONTHLY_STANDARD_TEMPLATE,
    get_efficiency_template,
    get_monthly_template,
)


# ── 用户提示词模板 ────────────────────────────────────
EFFICIENCY_USER_PROMPT = """以下是员工 {author_name} 当日的代码提交内容。

### 提交信息（commits）：
{commits_text}

### 代码变更（diffs）：
{diffs_text}

请按系统提示的格式输出评分简述、评分明细、主要工作（不超过 {top_n} 条）和总分。"""


EFFICIENCY_MONTHLY_USER_PROMPT = """以下是员工 {author_name} 在 {year_month} 的代码提交数据概览。

### 本月数据：
- 活跃天数：{active_days} 天
- 提交次数：{commits_count} 次
- 代码变更：+{additions} / -{deletions}
- 涉及项目：{projects}

### 每日评分详情：
{daily_scores_summary}

请按系统提示的格式输出月度评分简述、月度评分明细、月度主要工作（不超过 {top_n} 条）和月度总分。"""


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
# 支持多种格式：总分：85分、总分: 85 分、总分85分、总得分：85分
# 标题行优先（## 总分 / ## 月度总分），避免误抓正文中复述的"总分 100 分"等字样
_SCORE_HEADING_PATTERN = re.compile(r"##\s*(?:月度)?总[得]?分[:：]?\s*(\d+)\s*分?")
_SCORE_PATTERN = re.compile(r"总[得]?分[:：]?\s*(\d+)\s*分?")
# 评分明细行：- 注释（5分）：4 分，xxx（兼容半角括号与 */- 列表符）
_DIMENSION_LINE_PATTERN = re.compile(
    r"^\s*[-*]\s*(.+?)[（(](\d+)\s*分[）)][:：]\s*(\d+(?:\.\d+)?)\s*分",
    re.MULTILINE,
)
_WORK_HEADER_PATTERN = re.compile(r"##\s*[^\n]*?主要工作.*?\n(.+?)(?=\n##|\Z)", re.DOTALL)
_WORK_ITEM_PATTERN = re.compile(r"^\s*(?:\d+[.、)]|\-|\*)\s*(.+?)\s*$", re.MULTILINE)
_REVIEW_SUMMARY_PATTERN = re.compile(r"##\s*[^\n]*?评分简述.*?\n(.+?)(?=\n##|\Z)", re.DOTALL)


def parse_score(text: str) -> int:
    """从 LLM 输出中解析总分（0 表示未识别或超出 0-100 范围）

    优先匹配 "## 总分" 标题行；无标题行时取最后一个 "总分：XX"
    （总分位于输出末尾，取最后可避免误抓正文中的复述）。
    """
    if not text:
        return 0
    match = _SCORE_HEADING_PATTERN.search(text)
    if match:
        score = int(match.group(1))
        return score if 0 <= score <= 100 else 0
    matches = _SCORE_PATTERN.findall(text)
    if not matches:
        # 记录无法解析的情况，便于调试
        logger.warning(f"无法从 LLM 输出中解析总分，原始内容末 200 字: ...{text[-200:]}")
        return 0
    score = int(matches[-1])
    return score if 0 <= score <= 100 else 0


def parse_dimension_scores(text: str) -> List[tuple]:
    """解析评分明细各维度分，返回 [(维度名, 得分, 满分)]

    得分超出该维度满分时按满分截断，避免模型给出越界分数。
    """
    if not text:
        return []
    results = []
    for m in _DIMENSION_LINE_PATTERN.finditer(text):
        name = m.group(1).strip()
        max_score = int(m.group(2))
        score = min(float(m.group(3)), float(max_score))
        results.append((name, score, max_score))
    return results


def validate_score(text: str, parsed_total: int) -> int:
    """用评分明细之和校验总分，明细解析充分时以重算值为准

    LLM 的加法经常出错，且不同模型出错方式不同；以明细重算可消除
    算术误差和总分误抓，是跨模型一致性的最后一道防线。
    """
    dimensions = parse_dimension_scores(text)
    # 明细维度过少视为解析不充分，直接信任原总分
    if len(dimensions) < 4:
        return parsed_total
    recomputed = max(0, min(100, round(sum(s for _, s, _ in dimensions))))
    if recomputed != parsed_total:
        logger.warning(
            f"总分校验不一致: LLM 输出 {parsed_total} 分，"
            f"明细重算 {recomputed} 分，以重算为准"
        )
    return recomputed


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
def build_system_prompt(
    author_name: str,
    top_n: int = 5,
    custom_template: Optional[str] = None,
) -> str:
    """构造日度能效评分系统提示词

    Args:
        author_name: 员工姓名
        top_n: 工作总结条目上限
        custom_template: 自定义提示词模板（可选）

    Returns:
        格式化后的系统提示词
    """
    return get_efficiency_template(
        author_name=author_name,
        top_n=top_n,
        custom_template=custom_template,
    )


def build_user_prompt(author_name: str, commits_text: str,
                       diffs_text: str, top_n: int = 5) -> str:
    return EFFICIENCY_USER_PROMPT.format(
        author_name=author_name,
        commits_text=commits_text or "(无)",
        diffs_text=diffs_text or "(无)",
        top_n=top_n,
    )


# ── 月度 Prompt 构造 ───────────────────────────────────
def build_monthly_system_prompt(author_name: str, year_month: str,
                                 top_n: int = 10,
                                 custom_template: Optional[str] = None) -> str:
    """构造月度能效汇总系统提示词

    Args:
        author_name: 员工姓名
        year_month: 年月（如 2026-01）
        top_n: 工作总结条目上限
        custom_template: 自定义提示词模板（可选）

    Returns:
        格式化后的系统提示词
    """
    return get_monthly_template(
        author_name=author_name,
        year_month=year_month,
        top_n=top_n,
        custom_template=custom_template,
    )


def build_monthly_user_prompt(author_name: str, year_month: str,
                               active_days: int, commits_count: int,
                               additions: int, deletions: int,
                               projects: str, daily_scores_summary: str,
                               top_n: int = 10) -> str:
    return EFFICIENCY_MONTHLY_USER_PROMPT.format(
        author_name=author_name, year_month=year_month,
        active_days=active_days, commits_count=commits_count,
        additions=additions, deletions=deletions,
        projects=projects, daily_scores_summary=daily_scores_summary,
        top_n=top_n,
    )


# ── LLM 调用 ──────────────────────────────────────────
def call_llm(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    commits_text: str,
    diffs: List[str],
    top_n: int = 5,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    timeout: int = 240,
    max_retries: int = 3,
    retry_delay: int = 10,
    review_max_tokens: int = 10000,
    custom_prompt_template: Optional[str] = None,
) -> Optional[str]:
    """同步调用 LLM，返回原始 markdown 文本；失败返回 None

    Args:
        diffs: 按文件分隔的 diff 列表，每项格式 "--- {path} ---\n{diff}"
        custom_prompt_template: 自定义提示词模板（可选）
    """
    import time

    diffs_text = truncate_diffs_by_files(diffs, review_max_tokens)
    commits_text = truncate_text(commits_text, review_max_tokens // 5)

    messages = [
        {"role": "system", "content": build_system_prompt(
            author_name=author_name,
            top_n=top_n,
            custom_template=custom_prompt_template,
        )},
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
        except httpx.HTTPStatusError as e:
            logger.error(f"LLM 请求失败: {e.response.status_code}")
            return None
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
    diffs: List[str],
    top_n: int = 5,
    **llm_kwargs,
) -> Dict[str, object]:
    """便捷封装：调用 LLM 并解析所有字段

    Args:
        diffs: 按文件分隔的 diff 列表，每项格式 "--- {path} ---\n{diff}"

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
        diffs=diffs, top_n=top_n, **llm_kwargs,
    )
    if raw is None:
        return {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }

    # 记录 LLM 原始输出，便于调试解析问题
    logger.debug(f"LLM 原始输出 [{author_name}]: {raw[:500]}...")

    score = parse_score(raw)
    score = validate_score(raw, score)
    if score == 0:
        logger.warning(f"评分解析失败 [{author_name}]，LLM 输出可能格式不符")

    return {
        "raw": raw,
        "score": score,
        "grade": map_score_to_grade(score) if score > 0 else None,
        "work_summary": parse_work_summary(raw, top_n=top_n),
        "review_summary": parse_review_summary(raw),
        "success": True,
    }


# ── 月度 LLM 调用 ──────────────────────────────────────
def call_monthly_llm(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    year_month: str,
    active_days: int,
    commits_count: int,
    additions: int,
    deletions: int,
    projects: str,
    daily_scores_summary: str,
    top_n: int = 10,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    timeout: int = 240,
    max_retries: int = 3,
    retry_delay: int = 10,
    custom_prompt_template: Optional[str] = None,
) -> Optional[str]:
    """同步调用 LLM 生成月度总结，返回原始 markdown 文本

    Args:
        custom_prompt_template: 自定义月度提示词模板（可选）
    """
    import time

    messages = [
        {"role": "system", "content": build_monthly_system_prompt(
            author_name=author_name, year_month=year_month, top_n=top_n,
            custom_template=custom_prompt_template,
        )},
        {"role": "user", "content": build_monthly_user_prompt(
            author_name=author_name, year_month=year_month,
            active_days=active_days, commits_count=commits_count,
            additions=additions, deletions=deletions,
            projects=projects, daily_scores_summary=daily_scores_summary,
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
                logger.warning("月度 LLM 返回空内容")
                return None
        except httpx.TimeoutException:
            logger.warning(f"月度 LLM 请求超时 (尝试 {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
        except httpx.HTTPStatusError as e:
            logger.error(f"月度 LLM 请求失败: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"月度 LLM 请求异常: {type(e).__name__}: {e}")
            return None

    logger.error("月度 LLM 达到最大重试次数")
    return None


def call_and_parse_monthly(
    *,
    api_url: str,
    api_key: str,
    model: str,
    author_name: str,
    year_month: str,
    active_days: int,
    commits_count: int,
    additions: int,
    deletions: int,
    projects: str,
    daily_scores_summary: str,
    top_n: int = 10,
    **llm_kwargs,
) -> Dict[str, object]:
    """调用月度 LLM 并解析结果"""
    raw = call_monthly_llm(
        api_url=api_url, api_key=api_key, model=model,
        author_name=author_name, year_month=year_month,
        active_days=active_days, commits_count=commits_count,
        additions=additions, deletions=deletions,
        projects=projects, daily_scores_summary=daily_scores_summary,
        top_n=top_n, **llm_kwargs,
    )
    if raw is None:
        return {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }

    logger.debug(f"月度 LLM 原始输出 [{author_name}/{year_month}]: {raw[:500]}...")

    score = parse_score(raw)
    score = validate_score(raw, score)
    if score == 0:
        logger.warning(f"月度评分解析失败 [{author_name}/{year_month}]")

    return {
        "raw": raw,
        "score": score,
        "grade": map_score_to_grade(score) if score > 0 else None,
        "work_summary": parse_work_summary(raw, top_n=top_n),
        "review_summary": parse_review_summary(raw),
        "success": True,
    }

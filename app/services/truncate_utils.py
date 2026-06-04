"""文本截断工具模块

统一封装截断逻辑，供 efficiency_llm / webhook_reviewer 共用，消除重复代码。

截断策略：
- 普通文本：按字符近似截断（1 token ≈ 4 字符）
- diffs 列表：按文件粒度截断，保留完整文件 diff 上下文
"""
from __future__ import annotations

from typing import List

from loguru import logger


def truncate_text(text: str, max_tokens: int) -> str:
    """按字符近似截断（1 token ≈ 4 字符）

    Args:
        text: 待截断文本
        max_tokens: 最大 token 数
    Returns:
        截断后的文本，超限时追加截断提示
    """
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    logger.warning(f"文本长度 {len(text)} 超限 {max_chars}，已截断")
    return text[:max_chars] + "\n\n... (内容已截断)"


def truncate_diffs_by_files(diffs: List[str], max_tokens: int) -> str:
    """按文件粒度截断 diffs，保留完整文件 diff 上下文

    从后往前保留（最新的文件优先），确保每个保留的文件 diff 完整。

    Args:
        diffs: 每个元素格式为 "--- {path} ---\n{diff_text}"
        max_tokens: 最大 token 数（1 token ≈ 4 字符）
    Returns:
        拼接后的 diffs 文本，超限时附带截断提示
    """
    if not diffs:
        return ""

    max_chars = max_tokens * 4

    # 计算完整拼接后的总长度
    full_text = "\n\n".join(diffs)
    if len(full_text) <= max_chars:
        return full_text

    # 从后往前按文件粒度保留
    kept: List[str] = []
    total = 0
    for diff in reversed(diffs):
        cost = len(diff) + 2  # +2 for "\n\n" separator
        if total + cost > max_chars and kept:
            break
        kept.append(diff)
        total += cost

    kept.reverse()

    skipped = len(diffs) - len(kept)
    result = "\n\n".join(kept)
    if skipped > 0:
        logger.warning(
            f"diffs 共 {len(diffs)} 个文件，超限 {max_chars} 字符，"
            f"跳过前 {skipped} 个，保留最近 {len(kept)} 个"
        )
        result = (
            f"... (已跳过前 {skipped} 个文件的 diff，保留最近 {len(kept)} 个)\n\n"
            + result
        )
    return result

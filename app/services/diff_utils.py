"""Diff 工具：解析 diff 文本的新增/删除行数

供 stats_generator 和 efficiency_aggregator 等模块共享。
"""
from __future__ import annotations
import re
from typing import Tuple

# 匹配 diff 中的行变化：开头是 + 或 - 但排除 ++/-- 这种 diff header
ADDITION_PATTERN = re.compile(r"^\+(?!\+\+|\-\-)", re.MULTILINE)
DELETION_PATTERN = re.compile(r"^\-(?!\+\+|\-\-)", re.MULTILINE)


def count_diff_lines(diff_text: str) -> Tuple[int, int]:
    """统计 diff 文本中的新增/删除行数

    Args:
        diff_text: unified diff 格式的文本（不含 ++/-- 文件头）

    Returns:
        (additions, deletions)
    """
    if not diff_text:
        return (0, 0)
    return (
        len(ADDITION_PATTERN.findall(diff_text)),
        len(DELETION_PATTERN.findall(diff_text)),
    )

"""truncate_utils 测试 — 截断工具函数"""
from app.services.truncate_utils import truncate_text, truncate_diffs_by_files


# ── truncate_text ────────────────────────────────────
def test_truncate_text_within_limit():
    text = "short text"
    assert truncate_text(text, max_tokens=100) == text


def test_truncate_text_at_limit():
    # 10 tokens = 40 chars
    text = "a" * 40
    assert truncate_text(text, max_tokens=10) == text


def test_truncate_text_over_limit():
    # 10 tokens = 40 chars, text is 50 chars
    text = "a" * 50
    result = truncate_text(text, max_tokens=10)
    assert len(result) == 40 + len("\n\n... (内容已截断)")
    assert result.endswith("... (内容已截断)")


def test_truncate_text_empty():
    assert truncate_text("", max_tokens=100) == ""


# ── truncate_diffs_by_files ──────────────────────────
def test_truncate_diffs_by_files_within_limit():
    diffs = [
        "--- a.py ---\n+line1\n+line2",
        "--- b.py ---\n+line3",
    ]
    result = truncate_diffs_by_files(diffs, max_tokens=1000)
    assert "--- a.py ---" in result
    assert "--- b.py ---" in result
    assert "跳过" not in result


def test_truncate_diffs_by_files_empty():
    assert truncate_diffs_by_files([], max_tokens=1000) == ""


def test_truncate_diffs_by_files_preserves_file_boundary():
    """截断应保留完整文件 diff，不切断中间"""
    # 构造两个大文件 diff
    big_diff_1 = "--- big1.py ---\n" + "+line\n" * 500  # ~3000 chars
    big_diff_2 = "--- big2.py ---\n" + "+line\n" * 500  # ~3000 chars
    big_diff_3 = "--- big3.py ---\n" + "+line\n" * 500  # ~3000 chars

    diffs = [big_diff_1, big_diff_2, big_diff_3]
    # 设置较小的 token 限制，只能容纳约 1 个文件（100 tokens = 400 chars）
    result = truncate_diffs_by_files(diffs, max_tokens=800)

    # 应该保留后面的文件（最新的优先）
    assert "--- big3.py ---" in result
    # 应该有截断提示
    assert "跳过" in result


def test_truncate_diffs_by_files_keeps_later_files():
    """从后往前保留，最新的文件优先"""
    # 构造足够大的文件，使限制只能容纳后面 2 个
    old_content = "x" * 200
    small_old = f"--- old.py ---\n+{old_content}"
    small_mid = "--- mid.py ---\n+mid_line"
    small_new = "--- new.py ---\n+new_line"

    diffs = [small_old, small_mid, small_new]
    # 限制只能容纳 mid + new（约 60 chars），old（~210 chars）应被跳过
    result = truncate_diffs_by_files(diffs, max_tokens=20)

    # 新的文件应该保留
    assert "--- new.py ---" in result
    assert "--- mid.py ---" in result
    # old 应该被跳过
    assert "跳过" in result


def test_truncate_diffs_by_files_single_large_file():
    """单个超大文件也应该保留（不会被切成一半）"""
    big_diff = "--- huge.py ---\n" + "+line\n" * 2000
    result = truncate_diffs_by_files([big_diff], max_tokens=100)
    # 即使超限，单个文件也要保留
    assert "--- huge.py ---" in result

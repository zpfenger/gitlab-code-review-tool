"""diff_utils 测试"""
from app.services.diff_utils import count_diff_lines


def test_count_basic():
    diff = "+a\n+b\n-c"
    assert count_diff_lines(diff) == (2, 1)


def test_count_ignores_diff_headers():
    """++ 和 -- 是 diff 文件头，不应被计数"""
    diff = "+++ b/file.py\n--- a/file.py\n+real_add\n-real_del"
    assert count_diff_lines(diff) == (1, 1)


def test_count_empty():
    assert count_diff_lines("") == (0, 0)
    assert count_diff_lines(None) == (0, 0)


def test_count_no_changes():
    diff = " unchanged line\n context"
    assert count_diff_lines(diff) == (0, 0)
